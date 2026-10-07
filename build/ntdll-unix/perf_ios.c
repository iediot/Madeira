/*
 * MADEIRA: the [perf] profile -- where a frame's time goes.
 *
 * Off unless env.MADEIRA_PERF = 1 (madeira.cfg). When on, a reporter thread
 * prints two lines every MADEIRA_PERF_SECS seconds (default 5):
 *
 *   [perf] frames: rate, frame-time avg/p50/p95/max on the presenting
 *          thread, GPU time per frame, drawable wait, limiter sleep, render
 *          passes, command-buffer queue depth, process CPU and footprint.
 *   [perf-thr] the busiest threads by CPU over the window, by name.
 *
 * Read together they say which side is the bottleneck: a presenting thread at
 * ~100% with GPU time far below frame time is CPU (emulation) bound; GPU time
 * close to frame time is GPU bound; a large drawable wait is display pacing.
 *
 * The producers are DXMT's winemetal_unix.c hooks (ios_frame_*), which were
 * stubs in virtual_ios.c; they run only while ios_frame_stats_on is set, so
 * with the profile off nothing here runs. Thread CPU comes from
 * THREAD_EXTENDED_INFO. Threads consuming over one CPU second per report
 * also get a bounded stack snapshot, with symbolication only after resume.
 */

#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>
#include <time.h>
#include <unistd.h>
#include <mach/mach.h>
#include <mach/thread_info.h>
#include <dlfcn.h>

int ios_frame_stats_on = 0;

extern unsigned long long ios_last_footprint_mb;

static uint64_t perf_now_ns( void )
{
    return clock_gettime_nsec_np( CLOCK_UPTIME_RAW );
}

/* ---- accumulators (all relaxed atomics; the reporter swaps them to 0) ---- */

#define PERF_RING 4096
static _Atomic uint32_t perf_frame_ms_x100[PERF_RING]; /* game frame intervals, 0.01 ms */
static _Atomic uint32_t perf_frames;
static _Atomic uint64_t perf_last_tick_ns;

static _Atomic uint64_t perf_presents, perf_presents_skipped;
static _Atomic uint64_t perf_drawable_wait_ns, perf_drawable_wait_max_ns;
static _Atomic uint64_t perf_gpu_ns, perf_gpu_max_ns, perf_cmdbufs, perf_qdepth_max;
static _Atomic uint64_t perf_limiter_ns;
static _Atomic uint64_t perf_pass[3], perf_loads, perf_stores, perf_clears;
static _Atomic int perf_panel_hz, perf_intent_hz, perf_vsync_mode;

static void perf_max( _Atomic uint64_t *slot, uint64_t v )
{
    uint64_t cur = atomic_load_explicit( slot, memory_order_relaxed );
    while (v > cur && !atomic_compare_exchange_weak_explicit( slot, &cur, v, memory_order_relaxed,
                                                              memory_order_relaxed ))
        ;
}

/* ---- the hooks DXMT calls ---- */

void ios_frame_game_tick( void )
{
    uint64_t now = perf_now_ns();
    uint64_t last = atomic_exchange_explicit( &perf_last_tick_ns, now, memory_order_relaxed );
    uint32_t n;
    uint64_t d;

    if (!last) return;
    d = (now - last) / 10000;   /* 0.01 ms */
    if (d > 0xffffffffu) d = 0xffffffffu;
    n = atomic_fetch_add_explicit( &perf_frames, 1, memory_order_relaxed );
    atomic_store_explicit( &perf_frame_ms_x100[n % PERF_RING], (uint32_t)d, memory_order_relaxed );
}

void ios_frame_encode_present( int skipped )
{
    atomic_fetch_add_explicit( skipped ? &perf_presents_skipped : &perf_presents, 1, memory_order_relaxed );
}

void ios_frame_drawable_wait( unsigned long long ns )
{
    atomic_fetch_add_explicit( &perf_drawable_wait_ns, ns, memory_order_relaxed );
    perf_max( &perf_drawable_wait_max_ns, ns );
}

void ios_frame_gpu( unsigned long long gpu_ns, unsigned long long inflight )
{
    atomic_fetch_add_explicit( &perf_gpu_ns, gpu_ns, memory_order_relaxed );
    atomic_fetch_add_explicit( &perf_cmdbufs, 1, memory_order_relaxed );
    perf_max( &perf_gpu_max_ns, gpu_ns );
    perf_max( &perf_qdepth_max, inflight );
}

void ios_frame_note_display( int panel_hz, int intent_hz, int mode )
{
    atomic_store_explicit( &perf_panel_hz, panel_hz, memory_order_relaxed );
    atomic_store_explicit( &perf_intent_hz, intent_hz, memory_order_relaxed );
    atomic_store_explicit( &perf_vsync_mode, mode, memory_order_relaxed );
}

void ios_frame_limiter( unsigned long long ns )
{
    atomic_fetch_add_explicit( &perf_limiter_ns, ns, memory_order_relaxed );
}

void ios_frame_pass( unsigned kind, unsigned loads, unsigned stores, unsigned clears )
{
    if (kind < 3) atomic_fetch_add_explicit( &perf_pass[kind], 1, memory_order_relaxed );
    atomic_fetch_add_explicit( &perf_loads, loads, memory_order_relaxed );
    atomic_fetch_add_explicit( &perf_stores, stores, memory_order_relaxed );
    atomic_fetch_add_explicit( &perf_clears, clears, memory_order_relaxed );
}

/* ---- per-thread CPU ---- */

#define PERF_THREADS 512
struct perf_thread
{
    uint64_t id;        /* THREAD_IDENTIFIER_INFO thread_id, stable for the thread's life */
    uint64_t cpu_ns;    /* user + system at the last report */
    uint32_t seen;      /* report generation that last saw it */
};
static struct perf_thread perf_threads[PERF_THREADS];

struct perf_busy
{
    uint64_t delta_ns;
    char name[MAXTHREADNAMESIZE];
    uint64_t id;
};

/* Sample hot threads once per report. Never symbolize, allocate or print while
 * stopped: the sampled thread may own dyld, malloc or stdio locks. */
static int perf_read(uint64_t address, void *out, size_t size)
{
    vm_size_t got = 0;
    return address > 0x10000 && !vm_read_overwrite(mach_task_self(), address, size,
                                                  (vm_address_t)out, &got) && got == size;
}

static void perf_hot_stack(thread_t port, uint64_t id, const char *name)
{
    extern int ios_thread_registry_count(void);
    extern uintptr_t ios_thread_registry_teb(int);
    extern thread_t ios_thread_registry_mach(int);
    extern uint64_t ios_native_rip_from_hostpc(uint64_t, uint64_t, const char **);
    arm_thread_state64_t st;
    mach_msg_type_number_t count = ARM_THREAD_STATE64_COUNT;
    uint64_t pcs[14], fp, pair[2], bb = 0, teb = 0, rip;
    unsigned int tid = 0, n = 0, i;
    const char *why = "no-block";
    kern_return_t status;
    for (i = 0; i < (unsigned)ios_thread_registry_count(); i++)
        if (ios_thread_registry_mach(i) == port) { teb = ios_thread_registry_teb(i); break; }
    if (thread_suspend(port)) return;
    status = thread_get_state(port, ARM_THREAD_STATE64, (thread_state_t)&st, &count);
    if (!status)
    {
        pcs[n++] = arm_thread_state64_get_pc(st);
        pcs[n++] = arm_thread_state64_get_lr(st);
        fp = arm_thread_state64_get_fp(st);
        perf_read(st.__x[28], &bb, sizeof(bb));
        if (teb) perf_read(teb + 0x48, &tid, sizeof(tid));
        while (n < 14 && !(fp & 15) && perf_read(fp, pair, sizeof(pair)))
        {
            pcs[n++] = pair[1];
            if (pair[0] <= fp || pair[0] - fp > 1024 * 1024) break;
            fp = pair[0];
        }
    }
    thread_resume(port);
    if (status) return;
    rip = bb ? ios_native_rip_from_hostpc(bb, pcs[0], &why) : 0;
    dprintf(2, "[perf-stack] id=%llx name=%s wine_tid=%04x teb=%llx host_pc=%llx "
            "guest_rip=%llx resolve=%s\n", (unsigned long long)id, name, tid,
            (unsigned long long)teb, (unsigned long long)pcs[0], (unsigned long long)rip, why);
    for (i = 0; i < n; i++)
    {
        Dl_info di = {0};
        /* Strip arm64e return-address authentication before dladdr. */
        uint64_t pc = pcs[i] & 0x0000007fffffffffull;
        dladdr((void *)(uintptr_t)pc, &di);
        dprintf(2, "[perf-stack] id=%llx #%u pc=%llx %s %s+%llx\n",
                (unsigned long long)id, i, (unsigned long long)pc,
                di.dli_fname ? di.dli_fname : "?", di.dli_sname ? di.dli_sname : "?",
                (unsigned long long)(pc - (uintptr_t)(di.dli_saddr ? di.dli_saddr : di.dli_fbase)));
    }
}

static int perf_busy_cmp( const void *a, const void *b )
{
    const struct perf_busy *x = a, *y = b;
    return x->delta_ns < y->delta_ns ? 1 : x->delta_ns > y->delta_ns ? -1 : 0;
}

static int perf_u32_cmp( const void *a, const void *b )
{
    uint32_t x = *(const uint32_t *)a, y = *(const uint32_t *)b;
    return x < y ? -1 : x > y;
}

/* Fills busy[] with this window's per-thread CPU deltas; returns the count and
 * the whole-process delta in *total_ns. */
static unsigned perf_sample_threads( struct perf_busy *busy, unsigned max, uint32_t gen, uint64_t *total_ns )
{
    thread_act_array_t list;
    mach_msg_type_number_t count, i;
    unsigned out = 0;

    *total_ns = 0;
    if (task_threads( mach_task_self(), &list, &count ) != KERN_SUCCESS) return 0;
    for (i = 0; i < count; i++)
    {
        thread_extended_info_data_t ext;
        thread_identifier_info_data_t ident;
        mach_msg_type_number_t n = THREAD_EXTENDED_INFO_COUNT, m = THREAD_IDENTIFIER_INFO_COUNT;
        uint64_t cpu, prev = 0;
        unsigned h, probe;

        if (thread_info( list[i], THREAD_EXTENDED_INFO, (thread_info_t)&ext, &n ) == KERN_SUCCESS &&
            thread_info( list[i], THREAD_IDENTIFIER_INFO, (thread_info_t)&ident, &m ) == KERN_SUCCESS)
        {
            cpu = ext.pth_user_time + ext.pth_system_time;
            h = (unsigned)(ident.thread_id * 2654435761u) % PERF_THREADS;
            for (probe = 0; probe < PERF_THREADS; probe++, h = (h + 1) % PERF_THREADS)
            {
                struct perf_thread *t = &perf_threads[h];
                /* a slot not seen for two reports belongs to a thread that exited */
                if (t->id == ident.thread_id || !t->id || gen - t->seen > 2)
                {
                    if (t->id == ident.thread_id) prev = t->cpu_ns;
                    else prev = cpu;   /* first sight: no delta yet */
                    t->id = ident.thread_id;
                    t->cpu_ns = cpu;
                    t->seen = gen;
                    break;
                }
            }
            if (cpu > prev)
            {
                *total_ns += cpu - prev;
                /* MADEIRA_PERF_STACK_MS: CPU per report above which a thread's stack is
                 * sampled (default 1000). Lowered, it also catches threads that poll a
                 * wait, which a hang's waiter usually is. */
                static long long stack_ns = -1;
                if (stack_ns < 0)
                {
                    const char *ms = getenv( "MADEIRA_PERF_STACK_MS" );
                    stack_ns = (ms && atoi( ms ) > 0 ? atoi( ms ) : 1000) * 1000000ll;
                }
                if (cpu - prev > (unsigned long long)stack_ns && list[i] != pthread_mach_thread_np(pthread_self()))
                    perf_hot_stack(list[i], ident.thread_id, ext.pth_name);
                if (out < max)
                {
                    busy[out].delta_ns = cpu - prev;
                    busy[out].id = ident.thread_id;
                    memcpy( busy[out].name, ext.pth_name, sizeof(busy[out].name) );
                    busy[out].name[sizeof(busy[out].name) - 1] = 0;
                    out++;
                }
            }
        }
        mach_port_deallocate( mach_task_self(), list[i] );
    }
    vm_deallocate( mach_task_self(), (vm_address_t)list, count * sizeof(*list) );
    return out;
}

/* ---- reporter ---- */

static unsigned perf_secs = 5;

static void *perf_reporter( void *arg )
{
    static uint32_t ms[PERF_RING];
    static struct perf_busy busy[PERF_THREADS];
    uint64_t t_start = perf_now_ns(), t_prev = t_start, total_ns;
    uint32_t gen = 0, frames_seen = 0;
    unsigned nbusy, i;

    (void)arg;
    pthread_setname_np( "madeira-perf" );
    perf_sample_threads( busy, PERF_THREADS, ++gen, &total_ns );   /* baseline */
    for (;;)
    {
        uint64_t now, wall_ns, frames_total, presents, skipped, cmdbufs;
        uint32_t frames, nring;
        double wall_s, fps, avg = 0, p50 = 0, p95 = 0, mx = 0;
        char thr[1024];
        int len = 0;

        sleep( perf_secs );
        now = perf_now_ns();
        wall_ns = now - t_prev;
        t_prev = now;
        wall_s = wall_ns / 1e9;

        frames_total = atomic_load_explicit( &perf_frames, memory_order_relaxed );
        frames = (uint32_t)frames_total - frames_seen;
        nring = frames < PERF_RING ? frames : PERF_RING;
        for (i = 0; i < nring; i++)
            ms[i] = atomic_load_explicit( &perf_frame_ms_x100[(frames_seen + frames - nring + i) % PERF_RING],
                                          memory_order_relaxed );
        frames_seen = (uint32_t)frames_total;
        if (nring)
        {
            uint64_t sum = 0;
            qsort( ms, nring, sizeof(*ms), perf_u32_cmp );
            for (i = 0; i < nring; i++) sum += ms[i];
            avg = sum / (double)nring / 100.0;
            p50 = ms[nring / 2] / 100.0;
            p95 = ms[(nring * 95) / 100 < nring ? (nring * 95) / 100 : nring - 1] / 100.0;
            mx = ms[nring - 1] / 100.0;
        }
        fps = frames / wall_s;

        presents = atomic_exchange_explicit( &perf_presents, 0, memory_order_relaxed );
        skipped = atomic_exchange_explicit( &perf_presents_skipped, 0, memory_order_relaxed );
        cmdbufs = atomic_exchange_explicit( &perf_cmdbufs, 0, memory_order_relaxed );
        {
            double per = frames ? (double)frames : 1.0;
            uint64_t gpu = atomic_exchange_explicit( &perf_gpu_ns, 0, memory_order_relaxed );
            uint64_t gpu_max = atomic_exchange_explicit( &perf_gpu_max_ns, 0, memory_order_relaxed );
            uint64_t dw = atomic_exchange_explicit( &perf_drawable_wait_ns, 0, memory_order_relaxed );
            uint64_t dw_max = atomic_exchange_explicit( &perf_drawable_wait_max_ns, 0, memory_order_relaxed );
            uint64_t lim = atomic_exchange_explicit( &perf_limiter_ns, 0, memory_order_relaxed );
            uint64_t qd = atomic_exchange_explicit( &perf_qdepth_max, 0, memory_order_relaxed );
            uint64_t pr = atomic_exchange_explicit( &perf_pass[0], 0, memory_order_relaxed );
            uint64_t pc = atomic_exchange_explicit( &perf_pass[1], 0, memory_order_relaxed );
            uint64_t pb = atomic_exchange_explicit( &perf_pass[2], 0, memory_order_relaxed );
            uint64_t ld = atomic_exchange_explicit( &perf_loads, 0, memory_order_relaxed );
            uint64_t st = atomic_exchange_explicit( &perf_stores, 0, memory_order_relaxed );
            uint64_t cl = atomic_exchange_explicit( &perf_clears, 0, memory_order_relaxed );

            nbusy = perf_sample_threads( busy, PERF_THREADS, ++gen, &total_ns );

            dprintf( 2, "[perf] t=%.0fs fps=%.1f frame avg=%.1f p50=%.1f p95=%.1f max=%.1f ms | "
                        "gpu=%.1f ms/frame (cb max %.1f, %llu cb, qdepth<=%llu) | drawable-wait=%.1f ms/frame (max %.1f) | "
                        "limiter=%.1f ms/frame | passes/frame render=%.1f compute=%.1f blit=%.1f loads=%.1f stores=%.1f clears=%.1f | "
                        "presents=%llu skipped=%llu | panel=%dHz intent=%dHz vsync=%d | cpu=%.0f%% | footprint=%lluMB\n",
                     (now - t_start) / 1e9, fps, avg, p50, p95, mx,
                     gpu / 1e6 / per, gpu_max / 1e6, (unsigned long long)cmdbufs, (unsigned long long)qd,
                     dw / 1e6 / per, dw_max / 1e6, lim / 1e6 / per,
                     pr / per, pc / per, pb / per, ld / per, st / per, cl / per,
                     (unsigned long long)presents, (unsigned long long)skipped,
                     atomic_load_explicit( &perf_panel_hz, memory_order_relaxed ),
                     atomic_load_explicit( &perf_intent_hz, memory_order_relaxed ),
                     atomic_load_explicit( &perf_vsync_mode, memory_order_relaxed ),
                     total_ns * 100.0 / wall_ns, ios_last_footprint_mb );
        }

        qsort( busy, nbusy, sizeof(*busy), perf_busy_cmp );
        for (i = 0; i < nbusy && i < 12 && len < (int)sizeof(thr) - 64; i++)
        {
            double pct = busy[i].delta_ns * 100.0 / wall_ns;
            if (pct < 2.0) break;
            len += snprintf( thr + len, sizeof(thr) - len, "%s%s#%llx %.0f%%", i ? " | " : "",
                             busy[i].name[0] ? busy[i].name : "?", (unsigned long long)(busy[i].id & 0xffff), pct );
        }
        thr[len] = 0;
        dprintf( 2, "[perf-thr] %s\n", len ? thr : "(idle)" );
    }
    return NULL;
}

/* Called once the Wine session's JIT pool is up (virtual_ios.c), after the app
 * has applied madeira.cfg to the environment. */
void ios_perf_init( void )
{
    static int done;
    const char *e = getenv( "MADEIRA_PERF" ), *s;
    pthread_t t;

    if (done++ || !e || *e != '1') return;
    if ((s = getenv( "MADEIRA_PERF_SECS" )) && atoi( s ) > 0) perf_secs = (unsigned)atoi( s );
    /* winemetal's hooks default to 32-bit callers only; the profile wants every frame. */
    setenv( "MADEIRA_FRAME_STATS", "1", 0 );
    ios_frame_stats_on = 1;
    if (!pthread_create( &t, NULL, perf_reporter, NULL )) pthread_detach( t );
    dprintf( 2, "[perf] profile on: a report every %us\n", perf_secs );
}
