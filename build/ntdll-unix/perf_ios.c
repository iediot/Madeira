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
#include <mach-o/dyld.h>
#include <mach-o/loader.h>
#include "perf_ios.h"

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

/* ---- 200 Hz, unsuspended main-thread time classifier ----
 * All storage is bounded. The Wine registry avoids task_threads' VM allocation
 * on every selection pass. Mach send rights are retained across each window.
 * PC attribution and scheduler state are independent: trap PCs are a useful
 * wait estimate, while THREAD_BASIC_INFO reports whether the thread is waiting.
 */
#define CLASS_DYLIBS 2048
#define CLASS_BUCKETS 1024
#define CLASS_ROWS 8
#define CLASS_TRAPS 16

enum perf_class { C_GAME, C_DXMT, C_PE, C_UNIX, C_FEX, C_METAL, C_SYSTEM, C_BLOCKED, C_OTHER, C_COUNT };
static const char *class_names[C_COUNT] =
    { "game", "dxmt", "winePE", "wineUnix", "fex", "mvk/metal", "system", "blocked", "other" };
struct class_image { uintptr_t lo, hi, base; enum perf_class kind; char name[96]; };
struct class_row
{
    uint64_t id;
    unsigned tid, samples, counts[C_COUNT], waiting, running, state_other, state_failed, failed;
    char name[MAXTHREADNAMESIZE];
};
struct class_bucket
{
    uint64_t id;
    uintptr_t offset;
    unsigned tid, hits;
    enum perf_class kind;
    char name[96];
};
static struct ios_perf_image class_pe[IOS_PERF_IMAGES];
static struct class_image class_dylibs[CLASS_DYLIBS];
static struct { uintptr_t lo, hi; } class_traps[CLASS_TRAPS];
static struct class_bucket class_buckets[CLASS_BUCKETS];
static struct class_row class_rows[CLASS_ROWS];
static struct perf_thread class_threads[PERF_THREADS];
static enum perf_class class_pe_classes[IOS_PERF_IMAGES];
static unsigned class_npe, class_ndylibs, class_ntraps, class_dropped;
static uint32_t class_dyld_count = UINT32_MAX;
extern void *ios_jit_rx_base_global;
extern size_t ios_jit_pool_size_global;
extern uintptr_t ios_fex_arena_base_unix, ios_fex_arena_end_unix;
extern int ios_thread_registry_count(void);
extern uintptr_t ios_thread_registry_teb(int);
extern thread_t ios_thread_registry_mach(int);

static const char *class_basename(const char *s)
{
    const char *p = strrchr(s, '/');
    return p ? p + 1 : s;
}

static enum perf_class class_pe_kind(const char *name)
{
    static const char *dxmt[] = { "d3d11", "d3d10core", "dxgi", "d3d9", "d3d9-emulated", "winemetal" };
    char stem[64];
    unsigned i;
    snprintf(stem, sizeof(stem), "%s", class_basename(name));
    char *ext = strrchr(stem, '.');
    if (ext && !strcasecmp(ext, ".dll")) *ext = 0;
    for (i = 0; i < sizeof(dxmt)/sizeof(dxmt[0]); ++i)
        if (!strcasecmp(stem, dxmt[i])) return C_DXMT;
    if (!strcasecmp(stem, "libarm64ecfex") || !strcasecmp(stem, "libwow64fex") ||
        !strncasecmp(stem, "xtajit", 6)) return C_FEX;
    return C_PE;
}

static void class_refresh_dyld(void)
{
    uint32_t count = _dyld_image_count(), i;
    if (count == class_dyld_count) return;
    class_ndylibs = 0;
    for (i = 0; i < count && class_ndylibs < CLASS_DYLIBS; ++i)
    {
        uintptr_t base = (uintptr_t)_dyld_get_image_header(i), cursor;
        intptr_t slide = _dyld_get_image_vmaddr_slide(i);
        struct mach_header_64 h;
        char path[512] = "?";
        const char *name = _dyld_get_image_name(i);
        unsigned j;
        /* Safe reads also tolerate a concurrent unload. Never keep dyld strings. */
        if (!perf_read(base, &h, sizeof(h)) || h.magic != MH_MAGIC_64) continue;
        if (name) for (j = 0; j < sizeof(path)-1; ++j)
            if (!perf_read((uintptr_t)name+j, path+j, 1) || !path[j]) break;
        path[sizeof(path)-1] = 0;
        cursor = base + sizeof(h);
        for (j = 0; j < h.ncmds && j < 4096; ++j)
        {
            struct load_command lc;
            struct segment_command_64 seg;
            if (cursor - base - sizeof(h) + sizeof(lc) > h.sizeofcmds ||
                !perf_read(cursor, &lc, sizeof(lc)) || lc.cmdsize < sizeof(lc) ||
                lc.cmdsize > h.sizeofcmds - (cursor - base - sizeof(h))) break;
            if (lc.cmd == LC_SEGMENT_64 && lc.cmdsize >= sizeof(seg) &&
                perf_read(cursor, &seg, sizeof(seg)) && !strncmp(seg.segname, "__TEXT", 16))
            {
                struct class_image *im = &class_dylibs[class_ndylibs++];
                im->lo = seg.vmaddr + slide;
                im->hi = im->lo + seg.vmsize;
                im->base = base;
                snprintf(im->name, sizeof(im->name), "%s", class_basename(path));
                im->kind = h.filetype == MH_EXECUTE ? C_UNIX :
                    (strcasestr(path, "MoltenVK") || strcasestr(path, "Metal") ||
                     strcasestr(path, "IOGPU") || strcasestr(path, "AGX")) ? C_METAL : C_SYSTEM;
                break;
            }
            cursor += lc.cmdsize;
        }
    }
    class_dyld_count = count;
}

static void class_init_traps(void)
{
    static const char *names[] = { "mach_msg2_trap", "mach_msg_trap", "__ulock_wait", "__ulock_wait2",
        "__psynch_cvwait", "__psynch_mutexwait", "__semwait_signal", "__select", "read",
        "__read_nocancel", "__workq_kernreturn", "semaphore_wait_trap", "semaphore_timedwait_trap" };
    void *kernel = dlopen("/usr/lib/system/libsystem_kernel.dylib", RTLD_LAZY | RTLD_LOCAL);
    unsigned i;
    if (!kernel) return;
    for (i = 0; i < sizeof(names)/sizeof(names[0]); ++i)
    {
        uintptr_t lo = (uintptr_t)dlsym(kernel, names[i]), hi;
        Dl_info first = {0}, next;
        if (!lo || !dladdr((void *)lo, &first) || !first.dli_saddr) continue;
        /* Bound by the next symbol, with a conservative 256-byte cap. Resolve
         * once at setup, never call dladdr for sampled PCs. Includes PC after svc. */
        for (hi = lo + 4; hi < lo + 256; hi += 4)
            if (!dladdr((void *)hi, &next) || next.dli_saddr != first.dli_saddr) break;
        class_traps[class_ntraps].lo = lo;
        class_traps[class_ntraps++].hi = hi;
    }
    dlclose(kernel);
}

static enum perf_class class_pc(uintptr_t pc, uintptr_t dispatcher, const char **name, uintptr_t *offset)
{
    unsigned i;
    *name = "?"; *offset = pc;
    for (i = 0; i < class_npe; ++i)
        if (pc >= class_pe[i].base && pc - class_pe[i].base < class_pe[i].size)
        {
            *name = class_pe[i].name; *offset = pc - class_pe[i].base;
            return class_pe_classes[i];
        }
    if (pc >= (uintptr_t)ios_jit_rx_base_global &&
        pc - (uintptr_t)ios_jit_rx_base_global < ios_jit_pool_size_global)
    {
        /* FEX Dispatcher.cpp allocates four 4K FEX pages. The EC CPU area's
         * EnterEC pointer lies in that 16K-aligned allocation. Blocks use
         * separate buffers; do not mistake all FEX-produced code for FEX overhead. */
        if (dispatcher && pc >= dispatcher && pc - dispatcher < 0x4000) return C_FEX;
        return C_GAME;
    }
    if (ios_fex_arena_base_unix && pc >= ios_fex_arena_base_unix && pc < ios_fex_arena_end_unix)
        return C_FEX;
    for (i = 0; i < class_ndylibs; ++i)
        if (pc >= class_dylibs[i].lo && pc < class_dylibs[i].hi)
        {
            struct class_image *im = &class_dylibs[i];
            unsigned j;
            *name = im->name; *offset = pc - im->base;
            if (im->kind == C_SYSTEM)
                for (j = 0; j < class_ntraps; ++j)
                    if (pc >= class_traps[j].lo && pc < class_traps[j].hi) return C_BLOCKED;
            return im->kind;
        }
    return C_OTHER;
}

static thread_t class_pick(uint32_t gen, uint64_t *id, unsigned *tid, uintptr_t *teb, char *name)
{
    thread_t best = MACH_PORT_NULL;
    uint64_t highest = 0, previous_id = *id;
    int i, count = ios_thread_registry_count();
    for (i = 0; i < count && i < PERF_THREADS; ++i)
    {
        thread_t port = ios_thread_registry_mach(i);
        thread_extended_info_data_t ext;
        thread_identifier_info_data_t ident;
        mach_msg_type_number_t n = THREAD_EXTENDED_INFO_COUNT, m = THREAD_IDENTIFIER_INFO_COUNT;
        struct perf_thread *prev = &class_threads[i];
        uint64_t cpu, delta;
        int measured;
        if (!port || mach_port_mod_refs(mach_task_self(), port, MACH_PORT_RIGHT_SEND, 1)) continue;
        if (thread_info(port, THREAD_EXTENDED_INFO, (thread_info_t)&ext, &n) ||
            thread_info(port, THREAD_IDENTIFIER_INFO, (thread_info_t)&ident, &m))
        { mach_port_deallocate(mach_task_self(), port); continue; }
        cpu = ext.pth_user_time + ext.pth_system_time;
        measured = prev->id == ident.thread_id && prev->seen == gen - 1 && cpu >= prev->cpu_ns;
        delta = measured ? cpu - prev->cpu_ns : 0;
        prev->id = ident.thread_id; prev->cpu_ns = cpu; prev->seen = gen;
        /* MADEIRA_PERF_CLASSIFY_THREAD=<part of a thread name>: sample the busiest
         * matching thread instead of the busiest overall. Slime Rancher's main thread
         * was blocked 31-61% of the time waiting on another (UnityGfxDeviceWorker),
         * which is where its Direct3D work runs. */
        {
            static char want[32];
            static int want_read;
            if (!want_read)
            {
                const char *w = getenv( "MADEIRA_PERF_CLASSIFY_THREAD" );
                if (w) snprintf( want, sizeof(want), "%s", w );
                want_read = 1;
            }
            if (want[0] && !strstr( ext.pth_name, want ))
            { mach_port_deallocate(mach_task_self(), port); continue; }
        }
        if (measured && (!best || delta > highest || (delta == highest && ident.thread_id == previous_id)))
        {
            if (best) mach_port_deallocate(mach_task_self(), best);
            best = port; highest = delta; *id = ident.thread_id;
            *teb = ios_thread_registry_teb(i);
            *tid = 0;
            perf_read(*teb + 0x48, tid, sizeof(*tid));
            memcpy(name, ext.pth_name, MAXTHREADNAMESIZE);
            name[MAXTHREADNAMESIZE - 1] = 0;
        }
        else mach_port_deallocate(mach_task_self(), port);
    }
    return best;
}

static struct class_row *class_row(uint64_t id, unsigned tid, const char *name)
{
    unsigned i;
    for (i = 0; i < CLASS_ROWS; ++i)
        if (!class_rows[i].id || class_rows[i].id == id)
        {
            class_rows[i].id = id; class_rows[i].tid = tid;
            snprintf(class_rows[i].name, sizeof(class_rows[i].name), "%s", name);
            return &class_rows[i];
        }
    return NULL;
}

static void class_bucket_add(struct class_row *row, enum perf_class kind, const char *name, uintptr_t offset)
{
    unsigned i, h;
    if (kind != C_DXMT && kind != C_PE && kind != C_UNIX) return;
    offset &= ~(uintptr_t)0xfff;
    h = (unsigned)((offset >> 12) ^ row->id) % CLASS_BUCKETS;
    for (i = 0; i < CLASS_BUCKETS; ++i, h = (h + 1) % CLASS_BUCKETS)
    {
        struct class_bucket *b = &class_buckets[h];
        if (!b->hits || (b->id == row->id && b->kind == kind && b->offset == offset && !strcmp(b->name, name)))
        {
            b->id = row->id; b->tid = row->tid; b->kind = kind; b->offset = offset; ++b->hits;
            snprintf(b->name, sizeof(b->name), "%s", name);
            return;
        }
    }
    ++class_dropped;
}

static void class_report(void)
{
    unsigned i, j, rows = 0;
    for (i = 0; i < CLASS_ROWS; ++i)
    {
        struct class_row *r = &class_rows[i];
        char line[1024];
        int len;
        if (!r->id) continue;
        ++rows;
        len = snprintf(line, sizeof(line), "[perf-classify] tid=%04x name=%s samples=%u", r->tid, r->name, r->samples);
        for (j = 0; j < C_COUNT; ++j)
            len += snprintf(line + len, sizeof(line) - len, " | %s %.1f%%", class_names[j],
                            r->samples ? 100.0 * r->counts[j] / r->samples : 0.0);
        dprintf(2, "%s\n", line);
        dprintf(2, "[perf-classify-state] tid=%04x running=%u blocked=%u other=%u unknown=%u missed=%u\n",
                r->tid, r->running, r->waiting, r->state_other, r->state_failed, r->failed);
    }
    if (!rows) dprintf(2, "[perf-classify] tid=0000 name=(no-active-Wine-thread) samples=0\n");
    memset(class_rows, 0, sizeof(class_rows));
}

static void class_report_top(void)
{
    unsigned top[8], n = 0, i, j;
    for (i = 0; i < CLASS_BUCKETS; ++i)
    {
        if (!class_buckets[i].hits) continue;
        for (j = 0; j < n && class_buckets[top[j]].hits >= class_buckets[i].hits; ++j) ;
        if (j == 8) continue;
        if (n < 8) ++n;
        memmove(top + j + 1, top + j, (n - j - 1) * sizeof(*top));
        top[j] = i;
    }
    for (i = 0; i < n; ++i)
    {
        struct class_bucket *b = &class_buckets[top[i]];
        dprintf(2, "[perf-classify-top] tid=%04x %s %s+0x%llx samples=%u\n",
                b->tid, class_names[b->kind], b->name, (unsigned long long)b->offset, b->hits);
    }
    if (class_dropped) dprintf(2, "[perf-classify-top] bucket-table-full dropped=%u\n", class_dropped);
    memset(class_buckets, 0, sizeof(class_buckets)); class_dropped = 0;
}

static void *perf_classifier(void *arg)
{
    thread_t selected = MACH_PORT_NULL;
    uint64_t id = 0, now, refresh = 0, report = perf_now_ns() + 5000000000ull, top = report + 25000000000ull;
    uintptr_t teb = 0, dispatcher = 0;
    unsigned tid = 0;
    uint32_t gen = 0;
    char name[MAXTHREADNAMESIZE] = "";
    (void)arg;
    pthread_setname_np("madeira-classify");
    class_init_traps();
    for (;;)
    {
        now = perf_now_ns();
        if (now >= report) { class_report(); report = now + 5000000000ull; }
        if (now >= top) { class_report_top(); top = now + 30000000000ull; }
        if (now >= refresh)
        {
            uintptr_t area = 0, entry = 0;
            unsigned i;
            if (selected) mach_port_deallocate(mach_task_self(), selected);
            selected = class_pick(++gen, &id, &tid, &teb, name);
            class_npe = ios_perf_image_snapshot(class_pe, IOS_PERF_IMAGES);
            for (i = 0; i < class_npe; ++i) class_pe_classes[i] = class_pe_kind(class_pe[i].name);
            class_refresh_dyld();
            dispatcher = 0;
            if (selected && perf_read(teb + 0x1788, &area, sizeof(area)) &&
                perf_read(area + 0x40, &entry, sizeof(entry)) &&
                entry >= (uintptr_t)ios_jit_rx_base_global &&
                entry - (uintptr_t)ios_jit_rx_base_global < ios_jit_pool_size_global)
                dispatcher = entry & ~(uintptr_t)0x3fff;
            refresh = now + 1000000000ull;
        }
        if (selected)
        {
            struct class_row *r = class_row(id, tid, name);
            arm_thread_state64_t st;
            mach_msg_type_number_t n = ARM_THREAD_STATE64_COUNT;
            kern_return_t kr = thread_get_state(selected, ARM_THREAD_STATE64, (thread_state_t)&st, &n);
            if (r && kr == KERN_SUCCESS)
            {
                const char *module;
                uintptr_t offset, pc = arm_thread_state64_get_pc(st);
                uintptr_t lr = arm_thread_state64_get_lr(st);
                thread_basic_info_data_t basic;
                enum perf_class kind = class_pc(pc, dispatcher, &module, &offset);
                (void)lr; /* Deliberately PC-only: LR attribution would double-count leaf calls. */
                ++r->samples; ++r->counts[kind];
                class_bucket_add(r, kind, module, offset);
                n = THREAD_BASIC_INFO_COUNT;
                if (thread_info(selected, THREAD_BASIC_INFO, (thread_info_t)&basic, &n)) ++r->state_failed;
                else if (basic.run_state == TH_STATE_WAITING || basic.run_state == TH_STATE_UNINTERRUPTIBLE) ++r->waiting;
                else if (basic.run_state == TH_STATE_RUNNING) ++r->running;
                else ++r->state_other;
            }
            else if (r) ++r->failed;
            /* Includes KERN_INVALID_ARGUMENT on exit; never keep sampling a dead right. */
            if (kr != KERN_SUCCESS)
            { mach_port_deallocate(mach_task_self(), selected); selected = MACH_PORT_NULL; }
        }
        usleep(5000);
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

    if (done++) return;
    /* MADEIRA_PERF_CLASSIFY: sample the busiest Wine thread every 5 ms; print
     * PC classes every 5 seconds and top module+4K buckets every 30 seconds.
     * Default off. Works standalone; MADEIRA_PERF=1 is not required. */
    s = getenv( "MADEIRA_PERF_CLASSIFY" );
    if (s && *s == '1')
    {
        int err = pthread_create( &t, NULL, perf_classifier, NULL );
        if (!err) pthread_detach( t );
        dprintf(2, "[perf-classify] %s (5ms samples, 1s CPU selection, 5s/30s reports)\n",
                err ? "failed to start" : "on");
    }
    if (!e || *e != '1') return;
    if ((s = getenv( "MADEIRA_PERF_SECS" )) && atoi( s ) > 0) perf_secs = (unsigned)atoi( s );
    /* winemetal's hooks default to 32-bit callers only; the profile wants every frame. */
    setenv( "MADEIRA_FRAME_STATS", "1", 0 );
    ios_frame_stats_on = 1;
    if (!pthread_create( &t, NULL, perf_reporter, NULL )) pthread_detach( t );
    dprintf( 2, "[perf] profile on: a report every %us\n", perf_secs );
}
