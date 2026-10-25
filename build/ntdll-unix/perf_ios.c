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
static _Atomic uint64_t perf_read_calls, perf_read_bytes, perf_read_ns, perf_read_max_ns;
static _Atomic uint64_t perf_read_errors, perf_read_short;
#define PERF_IO_ACTIVE 512
struct perf_io_active {
    _Atomic uint64_t seq, started, tid, size, offset;
    _Atomic int fd, positioned;
};
static struct perf_io_active perf_io_active[PERF_IO_ACTIVE];
static _Atomic unsigned perf_io_slots, perf_io_untracked;

/* One bounded slot per native thread; no allocation or registry walk per read.
 * An odd sequence is active. Readers validate the sequence around snapshots. */
int ios_perf_read_enter(int fd, size_t size, int64_t offset, int positioned, uint64_t now)
{
    static _Thread_local int slot = -2;
    if (slot == -2) {
        unsigned n = atomic_fetch_add_explicit(&perf_io_slots, 1, memory_order_relaxed);
        slot = n < PERF_IO_ACTIVE ? (int)n : -1;
        if (slot >= 0) {
            uint64_t tid = 0;
            pthread_threadid_np(NULL, &tid);
            atomic_store_explicit(&perf_io_active[slot].tid, tid, memory_order_relaxed);
        }
        else atomic_fetch_add_explicit(&perf_io_untracked, 1, memory_order_relaxed);
    }
    if (slot >= 0) {
        struct perf_io_active *s = &perf_io_active[slot];
        atomic_store_explicit(&s->fd, fd, memory_order_relaxed);
        atomic_store_explicit(&s->size, size, memory_order_relaxed);
        atomic_store_explicit(&s->offset, offset, memory_order_relaxed);
        atomic_store_explicit(&s->positioned, positioned, memory_order_relaxed);
        atomic_store_explicit(&s->started, now, memory_order_relaxed);
        atomic_fetch_add_explicit(&s->seq, 1, memory_order_release);
    }
    return slot;
}

void ios_perf_read_leave(int slot)
{
    if (slot >= 0) atomic_fetch_add_explicit(&perf_io_active[slot].seq, 1, memory_order_release);
}

static void perf_active_reads(void)
{
    unsigned count = atomic_load_explicit(&perf_io_slots, memory_order_relaxed), shown = 0;
    uint64_t now = perf_now_ns();
    if (count > PERF_IO_ACTIVE) count = PERF_IO_ACTIVE;
    /* Positioned disk reads first, so idle launcher pipe reads do not hide them. */
    for (int pass = 1; pass >= 0; pass--)
    for (unsigned i = 0; i < count && shown < 32; i++) {
        struct perf_io_active *s = &perf_io_active[i];
        uint64_t seq = atomic_load_explicit(&s->seq, memory_order_acquire);
        if (!(seq & 1)) continue;
        uint64_t start = atomic_load_explicit(&s->started, memory_order_relaxed);
        uint64_t tid = atomic_load_explicit(&s->tid, memory_order_relaxed);
        uint64_t size = atomic_load_explicit(&s->size, memory_order_relaxed);
        uint64_t offset = atomic_load_explicit(&s->offset, memory_order_relaxed);
        int fd = atomic_load_explicit(&s->fd, memory_order_relaxed);
        int positioned = atomic_load_explicit(&s->positioned, memory_order_relaxed);
        atomic_thread_fence(memory_order_acquire);
        if (positioned != pass || start > now || now-start < 20000000ull ||
            seq != atomic_load_explicit(&s->seq, memory_order_acquire)) continue;
        dprintf(2, "[stall-io] native_tid=%llu op=%s fd=%d requested=%llu off=%llx active_ms=%.1f (in-flight; no result yet)\n",
                (unsigned long long)tid, positioned ? "pread" : "read", fd,
                (unsigned long long)size, (unsigned long long)offset, (now-start)/1e6);
        shown++;
    }
}

static void perf_max( _Atomic uint64_t *slot, uint64_t v )
{
    uint64_t cur = atomic_load_explicit( slot, memory_order_relaxed );
    while (v > cur && !atomic_compare_exchange_weak_explicit( slot, &cur, v, memory_order_relaxed,
                                                              memory_order_relaxed ))
        ;
}

/* ---- the hooks DXMT calls ---- */

void ios_perf_read_done(size_t requested, ssize_t result, uint64_t elapsed)
{
    atomic_fetch_add_explicit(&perf_read_calls, 1, memory_order_relaxed);
    if (result > 0) atomic_fetch_add_explicit(&perf_read_bytes, result, memory_order_relaxed);
    if (result < 0) atomic_fetch_add_explicit(&perf_read_errors, 1, memory_order_relaxed);
    else if ((size_t)result < requested) atomic_fetch_add_explicit(&perf_read_short, 1, memory_order_relaxed);
    atomic_fetch_add_explicit(&perf_read_ns, elapsed, memory_order_relaxed);
    perf_max(&perf_read_max_ns, elapsed);
}

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

/* Slow-path diagnostics only. No TLS/TEB guess on native Cocoa threads: use
 * the existing registry and fault-safe reads. Zero means no Wine owner found.
 * ClientId in the 64-bit TEB is {process, thread} at 0x40/0x48. */
uint64_t ios_perf_caller_ids(void)
{
    extern int ios_thread_registry_count(void);
    extern uintptr_t ios_thread_registry_teb(int);
    extern thread_t ios_thread_registry_mach(int);
    thread_t self = pthread_mach_thread_np(pthread_self());
    unsigned i, count = ios_thread_registry_count();
    uint64_t ids[2] = {0};
    for (i = 0; i < count; i++)
        if (ios_thread_registry_mach(i) == self)
        {
            uintptr_t teb = ios_thread_registry_teb(i);
            if (teb && perf_read(teb + 0x40, ids, sizeof(ids)))
                return ((uint64_t)(uint32_t)ids[0] << 32) | (uint32_t)ids[1];
            break;
        }
    return 0;
}

/* ---- stall probe v3: wait targets, caller context, VM attribution ----
 *
 * v2 showed RDR2's render thread in wait_select_reply during ~0.7 s gaps and
 * other game threads Mach-UNINTERRUPTIBLE with game PCs. v3 asks: what is the
 * render thread waiting on, who ends that wait and when (server_ios.c
 * [wait-done], queue_ios.c [stall-wait-srv]); and is an uninterruptible
 * thread blocked on a page (decoded load/store address + kernel page state).
 * Everything here runs on the XP probe thread at most once a second; reads go
 * through perf_read (fault-safe) and no thread is suspended. */
#include "wait_registry_ios.h"
#include "arm64_mem_ea.h"
#include <mach/mach_vm.h>
#include <mach/vm_region.h>

/* Guest module map per PEB (pseudo-processes share one address space, so a
 * single global map would name another process's modules). Rebuilt at most
 * every 20 s; at most 2 PEBs x 256 modules. */
#define PERF_MOD_MAX 256
static struct perf_modmap {
    uint64_t peb, built_ns;
    unsigned n;
    struct { uint64_t base, size; char name[40]; } m[PERF_MOD_MAX];
} perf_modmaps[2];

static struct perf_modmap *perf_modmap_for(uint64_t teb)
{
    uint64_t peb = 0, ldr = 0, head, cur, now = perf_now_ns();
    struct perf_modmap *map = NULL;
    unsigned guard = 0, k;
    if (!teb || !perf_read(teb + 0x60, &peb, 8) || !peb) return NULL;
    for (k = 0; k < 2; k++) if (perf_modmaps[k].peb == peb) map = &perf_modmaps[k];
    if (map && now - map->built_ns < 20000000000ull) return map;
    if (!map) map = perf_modmaps[0].built_ns <= perf_modmaps[1].built_ns ? &perf_modmaps[0] : &perf_modmaps[1];
    map->peb = peb; map->built_ns = now; map->n = 0;
    if (!perf_read(peb + 0x18, &ldr, 8) || !ldr) return map;
    head = ldr + 0x10;                        /* InLoadOrderModuleList */
    if (!perf_read(head, &cur, 8)) return map;
    while (cur && cur != head && guard++ < PERF_MOD_MAX && map->n < PERF_MOD_MAX)
    {
        /* LDR_DATA_TABLE_ENTRY (x64): DllBase +0x30, SizeOfImage +0x40, BaseDllName +0x58 */
        uint64_t base = 0, next = 0, str[2] = {0};
        uint32_t size = 0;
        uint16_t wname[39];
        if (!perf_read(cur + 0x30, &base, 8) || !perf_read(cur + 0x40, &size, 4)) break;
        if (base && size && size < (1u << 30))
        {
            unsigned n = 0, j;
            if (perf_read(cur + 0x58, str, 16))
            {
                n = (unsigned)(str[0] & 0xffff) / 2;
                if (n > 39) n = 39;
                if (!str[1] || !perf_read(str[1], wname, n * 2)) n = 0;
            }
            map->m[map->n].base = base; map->m[map->n].size = size;
            for (j = 0; j < n; j++) map->m[map->n].name[j] = wname[j] < 0x80 ? (char)wname[j] : '?';
            map->m[map->n].name[n] = 0;
            map->n++;
        }
        if (!perf_read(cur, &next, 8)) break;
        cur = next;
    }
    return map;
}

static const char *perf_module_name(const struct perf_modmap *map, uint64_t addr, uint64_t *rva)
{
    unsigned i;
    if (map)
        for (i = 0; i < map->n; i++)
            if (addr >= map->m[i].base && addr - map->m[i].base < map->m[i].size)
            { *rva = addr - map->m[i].base; return map->m[i].name; }
    *rva = addr;
    return NULL;
}

/* "module+rva", or the native symbol, or the raw address. */
static void perf_addr_name(char *out, size_t size, const struct perf_modmap *map, uint64_t addr)
{
    uint64_t rva;
    const char *mod = perf_module_name(map, addr, &rva);
    Dl_info info = {0};
    if (mod) snprintf(out, size, "%s+%llx", mod[0] ? mod : "module", (unsigned long long)rva);
    else if (addr && dladdr((void *)addr, &info) && (info.dli_sname || info.dli_fname))
        snprintf(out, size, "%s+%llx", info.dli_sname ? info.dli_sname : info.dli_fname,
                 (unsigned long long)(addr - (uint64_t)(uintptr_t)(info.dli_saddr ? info.dli_saddr : info.dli_fbase)));
    else snprintf(out, size, "%llx", (unsigned long long)addr);
}

/* Where a waiting thread came from: its syscall frame's pc/lr, the frame-
 * pointer chain inside the thread's own stack bounds, then stack words that
 * fall inside guest modules (x64 return addresses are not in the fp chain;
 * these are CANDIDATES, not a proven call chain). */
static void perf_wait_caller(const struct ios_wait_info *w, uint64_t native_tid)
{
    extern const unsigned int ios_syscall_frame_size;
    extern void ios_syscall_frame_regs(const void *, uint64_t *, uint64_t *, uint64_t *, uint64_t *);
    static unsigned char frame[0x400];
    uint64_t fp, lr, sp, pc, limit = 0, base = 0, words[256];
    struct perf_modmap *map = perf_modmap_for((uint64_t)(uintptr_t)w->teb);
    char line[1400], name[96];
    int len, i, shown = 0;

    if (!w->frame || ios_syscall_frame_size > sizeof(frame) ||
        !perf_read(w->frame, frame, ios_syscall_frame_size)) return;
    ios_syscall_frame_regs(frame, &fp, &lr, &sp, &pc);
    perf_read((uint64_t)(uintptr_t)w->teb + 0x08, &base, 8);    /* NT_TIB StackBase  */
    perf_read((uint64_t)(uintptr_t)w->teb + 0x10, &limit, 8);   /* NT_TIB StackLimit */
    perf_addr_name(name, sizeof(name), map, pc);
    len = snprintf(line, sizeof(line), "[stall-wait-stack] native_tid=%llu wine_tid=%04x pc=%s", (unsigned long long)native_tid, w->wine_tid, name);
    perf_addr_name(name, sizeof(name), map, lr);
    len += snprintf(line + len, sizeof(line) - len, " lr=%s fp-chain:", name);
    for (i = 0; i < 12 && fp && !(fp & 7) && fp >= limit && fp + 16 <= base && len < (int)sizeof(line) - 120; i++)
    {
        uint64_t pair[2];
        if (!perf_read(fp, pair, 16) || !pair[1]) break;
        perf_addr_name(name, sizeof(name), map, pair[1]);
        len += snprintf(line + len, sizeof(line) - len, " %s", name);
        if (pair[0] <= fp) break;
        fp = pair[0];
    }
    len += snprintf(line + len, sizeof(line) - len, " | guest-stack-candidates:");
    if (sp >= limit && sp < base)
    {
        size_t bytes = base - sp < sizeof(words) ? base - sp : sizeof(words);
        if (perf_read(sp, words, bytes & ~7ul))
            for (i = 0; i < (int)(bytes / 8) && shown < 8 && len < (int)sizeof(line) - 100; i++)
            {
                uint64_t rva;
                const char *mod = perf_module_name(map, words[i], &rva);
                if (!mod) continue;
                len += snprintf(line + len, sizeof(line) - len, " sp+%x:%s+%llx", i * 8, mod[0] ? mod : "module", (unsigned long long)rva);
                shown++;
            }
    }
    dprintf(2, "%s\n", line);
}

static const char *perf_wait_phase_name(unsigned p)
{
    return p == IOS_WAIT_PHASE_REQUEST ? "request" : p == IOS_WAIT_PHASE_PIPE ? "wait-pipe" :
           p == IOS_WAIT_PHASE_APC ? "kernel-apc" : "?";
}

/* Print the selected threads' in-flight Wine waits, the other recent waits of
 * the same processes, mark the selected ones for [wait-done], then ask the
 * server thread to resolve the waited objects ([stall-wait-srv]). */
void ios_perf_wait_report(const uint64_t *ids, unsigned nids)
{
    extern struct ios_wait_entry ios_wait_reg[IOS_WAITREG_SLOTS];
    extern struct ios_wait_probe_req ios_wait_probe;
    extern void ios_wineserver_wake(void);
    struct timespec ts;
    uint64_t now;
    unsigned i, j, probe[IOS_WAIT_PROBE_MAX], nprobe = 0, pids[16], npids = 0, others = 0, idle = 0;
    struct ios_wait_info w;
    unsigned depth;

    clock_gettime(CLOCK_MONOTONIC, &ts);
    now = (uint64_t)ts.tv_sec * 1000000000ull + ts.tv_nsec;
    for (i = 0; i < IOS_WAITREG_SLOTS; i++)
    {
        if (!ios_wait_read(&ios_wait_reg[i], &w, &depth)) continue;
        for (j = 0; j < nids && ids[j] != w.native_tid; j++) ;
        if (j == nids) continue;
        dprintf(2, "[stall-wait] native_tid=%llu wine_pid=%04x wine_tid=%04x age_ms=%.1f phase=%s depth=%u op=%d flags=%x "
                   "n=%d h=%x,%x,%x,%x key=%llx timeout=%lld requests=%u apcs=%u\n",
                (unsigned long long)w.native_tid, w.wine_pid, w.wine_tid, now > w.t0_ns ? (now - w.t0_ns) / 1e6 : 0.0,
                perf_wait_phase_name(ios_wait_reg[i].phase), depth, w.op, w.flags, w.count,
                w.handles[0], w.handles[1], w.handles[2], w.handles[3], (unsigned long long)w.key,
                (long long)w.timeout, ios_wait_reg[i].requests, ios_wait_reg[i].apcs);
        perf_wait_caller(&w, w.native_tid);
        ios_wait_reg[i].watch_until_ns = now + 5000000000ull;
        if (nprobe < IOS_WAIT_PROBE_MAX) probe[nprobe++] = w.wine_tid;
        for (j = 0; j < npids && pids[j] != w.wine_pid; j++) ;
        if (j == npids && npids < 16) pids[npids++] = w.wine_pid;
    }
    /* Selected threads that are not in a server wait are running, faulting or
     * waiting elsewhere (fast-sync park, Mach semaphore, mutex). */
    for (j = 0; j < nids; j++)
    {
        int found = 0;
        for (i = 0; i < IOS_WAITREG_SLOTS && !found; i++)
            found = ios_wait_read(&ios_wait_reg[i], &w, NULL) && w.native_tid == ids[j];
        if (!found) idle++;
    }
    /* Other waits of the same processes that began in the last 2 s: the
     * producers and consumers around the stalled thread. */
    for (i = 0; i < IOS_WAITREG_SLOTS && others < 24; i++)
    {
        uint64_t age;
        if (!ios_wait_read(&ios_wait_reg[i], &w, &depth) || now < w.t0_ns) continue;
        age = now - w.t0_ns;
        if (age < 20000000ull || age > 2000000000ull) continue;
        for (j = 0; j < nids && ids[j] != w.native_tid; j++) ;
        if (j < nids) continue;
        for (j = 0; j < npids && pids[j] != w.wine_pid; j++) ;
        if (j == npids) continue;
        dprintf(2, "[stall-waits] wine_pid=%04x wine_tid=%04x native_tid=%llu age_ms=%.1f phase=%s op=%d n=%d h=%x,%x key=%llx timeout=%lld\n",
                w.wine_pid, w.wine_tid, (unsigned long long)w.native_tid, age / 1e6,
                perf_wait_phase_name(ios_wait_reg[i].phase), w.op, w.count, w.handles[0], w.handles[1],
                (unsigned long long)w.key, (long long)w.timeout);
        if (nprobe < IOS_WAIT_PROBE_MAX) probe[nprobe++] = w.wine_tid;
        others++;
    }
    dprintf(2, "[stall-wait-summary] selected=%u in_server_wait=%u not_in_server_wait=%u recent_same_process_waits=%u resolve_requested=%u\n",
            nids, nids - idle, idle, others, nprobe);
    if (!nprobe) return;
    for (i = 0; i < nprobe; i++) ios_wait_probe.tids[i] = probe[i];
    ios_wait_probe.count = nprobe;
    ios_wait_probe.posted_ns = now;
    __atomic_add_fetch(&ios_wait_probe.gen, 1, __ATOMIC_RELEASE);
    ios_wineserver_wake();
}

/* Task and host VM counters over the last XP window (~250 ms). Uninterruptible
 * threads plus rising pageins/decompressions point at memory, not scheduling. */
void ios_perf_vm_tick(int report)
{
    static int have;
    static task_events_info_data_t last_ev;
    static uint64_t last_decomp, last_ns;
    static vm_statistics64_data_t last_host;
    task_events_info_data_t ev;
    task_vm_info_data_t vm;
    vm_statistics64_data_t host;
    mach_msg_type_number_t n = TASK_EVENTS_INFO_COUNT;
    uint64_t now = perf_now_ns(), decomp = 0;
    int ok_ev, ok_host;

    ok_ev = task_info(mach_task_self(), TASK_EVENTS_INFO, (task_info_t)&ev, &n) == KERN_SUCCESS;
    n = TASK_VM_INFO_COUNT;
    if (task_info(mach_task_self(), TASK_VM_INFO, (task_info_t)&vm, &n) == KERN_SUCCESS && n >= TASK_VM_INFO_REV5_COUNT)
        decomp = vm.decompressions;
    n = HOST_VM_INFO64_COUNT;
    ok_host = host_statistics64(mach_host_self(), HOST_VM_INFO64, (host_info64_t)&host, &n) == KERN_SUCCESS;
    if (report && have && ok_ev && ok_host)
        dprintf(2, "[stall-vm] window_ms=%.0f task: faults=%d pageins=%d cow=%d decompressions=%llu | host: pageins=%llu "
                   "decompressions=%llu compressions=%llu swapins=%llu swapouts=%llu throttled_pages=%u free_pages=%u\n",
                (now - last_ns) / 1e6, ev.faults - last_ev.faults, ev.pageins - last_ev.pageins,
                ev.cow_faults - last_ev.cow_faults, (unsigned long long)(decomp - last_decomp),
                (unsigned long long)(host.pageins - last_host.pageins),
                (unsigned long long)(host.decompressions - last_host.decompressions),
                (unsigned long long)(host.compressions - last_host.compressions),
                (unsigned long long)(host.swapins - last_host.swapins),
                (unsigned long long)(host.swapouts - last_host.swapouts),
                host.throttled_count, host.free_count);
    if (ok_ev && ok_host)
    {
        last_ev = ev; last_host = host; last_decomp = decomp; last_ns = now; have = 1;
    }
}

/* For a thread not running with a PC in non-library code: decode the memory
 * operand of the instruction at PC and ask the kernel about that page. */
static void perf_fault_candidate(uint64_t native_tid, const arm_thread_state64_t *st, int run_state)
{
    extern int ios_swap_diag_classify(uint64_t addr, uint64_t *off, int *resv, int *img);
    uint64_t regs[31], pc = arm_thread_state64_get_pc(*st), sp = arm_thread_state64_get_sp(*st), ea, off = 0;
    uint32_t insn;
    unsigned bytes, k;
    integer_t disp = 0, refs = 0;
    mach_vm_address_t raddr;
    mach_vm_size_t rsize = 0;
    vm_region_extended_info_data_t ext;
    mach_msg_type_number_t n = VM_REGION_EXTENDED_INFO_COUNT;
    mach_port_t obj = MACH_PORT_NULL;
    int resv = 0, img = 0, swap, have_region;
    Dl_info info;

    if (dladdr((void *)pc, &info)) return;            /* inside a library: a syscall/kernel wait, not a load */
    if (!perf_read(pc, &insn, 4)) return;
    for (k = 0; k < 29; k++) regs[k] = st->__x[k];
    regs[29] = arm_thread_state64_get_fp(*st);
    regs[30] = arm_thread_state64_get_lr(*st);
    if (!ios_arm64_mem_ea(insn, pc, regs, sp, &ea, &bytes))
    {
        dprintf(2, "[stall-fault] native_tid=%llu state=%d pc=%llx insn=%08x not a load/store (busy in code, or the sample moved)\n",
                (unsigned long long)native_tid, run_state, (unsigned long long)pc, insn);
        return;
    }
    mach_vm_page_query(mach_task_self(), ea & ~0xfffull, &disp, &refs);
    raddr = ea;
    have_region = mach_vm_region(mach_task_self(), &raddr, &rsize, VM_REGION_EXTENDED_INFO,
                                 (vm_region_info_t)&ext, &n, &obj) == KERN_SUCCESS && raddr <= ea;
    if (obj != MACH_PORT_NULL) mach_port_deallocate(mach_task_self(), obj);
    swap = ios_swap_diag_classify(ea, &off, &resv, &img);
    dprintf(2, "[stall-fault] native_tid=%llu state=%d pc=%llx insn=%08x ea=%llx bytes=%u page=%s%s%s%s%s "
               "region=%s tag=%d share=%d resident=%u swapped_out=%u external_pager=%u swap_file=%s off=%llx resv=%d img=%d\n",
            (unsigned long long)native_tid, run_state, (unsigned long long)pc, insn, (unsigned long long)ea, bytes,
            disp & VM_PAGE_QUERY_PAGE_PRESENT ? "present" : "absent",
            disp & VM_PAGE_QUERY_PAGE_PAGED_OUT ? ",compressed/paged-out" : "",
            disp & VM_PAGE_QUERY_PAGE_EXTERNAL ? ",file-backed" : "",
            disp & VM_PAGE_QUERY_PAGE_COPIED ? ",copied" : "",
            disp & VM_PAGE_QUERY_PAGE_DIRTY ? ",dirty" : "",
            have_region ? "ok" : "none", have_region ? (int)ext.user_tag : -1, have_region ? (int)ext.share_mode : -1,
            have_region ? ext.pages_resident : 0, have_region ? ext.pages_swapped_out : 0,
            have_region ? ext.external_pager : 0, swap ? "yes" : "no", (unsigned long long)off, resv, img);
}

/* Capture live PCs during a zero-present interval, including idle role threads
 * that the normal CPU-threshold stack sampler misses. No suspension, stack
 * walk, target lock or guest memory dereference: these are PC/LR samples, not
 * coherent call stacks. The caller caps cadence and selects at most 16 IDs. */
void ios_perf_stall_snapshot(const uint64_t *ids, unsigned nids)
{
    extern int ios_thread_registry_count(void);
    extern uintptr_t ios_thread_registry_teb(int);
    extern thread_t ios_thread_registry_mach(int);
    extern uint64_t ios_native_rip_from_hostpc(uint64_t, uint64_t, const char **);
    extern void ios_swap_diag_report(void);
    thread_act_array_t threads;
    mach_msg_type_number_t count, i;
    uint64_t start = perf_now_ns();
    struct { thread_t port; uint64_t tid, pc; int state; } second[16];
    unsigned nsecond = 0, s2;
    perf_active_reads();
    ios_swap_diag_report();
    if (nids > 16) nids = 16;
    if (task_threads(mach_task_self(), &threads, &count)) return;
    for (i = 0; i < count; i++)
    {
        int keep = 0;
        thread_identifier_info_data_t ident;
        mach_msg_type_number_t n = THREAD_IDENTIFIER_INFO_COUNT;
        unsigned j;
        if (!thread_info(threads[i], THREAD_IDENTIFIER_INFO, (thread_info_t)&ident, &n))
        for (j = 0; j < nids; j++) if (ident.thread_id == ids[j])
        {
            arm_thread_state64_t st;
            thread_basic_info_data_t basic = {0};
            uintptr_t teb = 0;
            uint64_t owner[2] = {0}, rip = 0;
            unsigned k, nr = ios_thread_registry_count();
            const char *why = "not-resolved";
            Dl_info pcinfo = {0}, lrinfo = {0};
            n = ARM_THREAD_STATE64_COUNT;
            if (thread_get_state(threads[i], ARM_THREAD_STATE64, (thread_state_t)&st, &n)) break;
            n = THREAD_BASIC_INFO_COUNT;
            thread_info(threads[i], THREAD_BASIC_INFO, (thread_info_t)&basic, &n);
            for (k = 0; k < nr; k++) if (ios_thread_registry_mach(k) == threads[i])
            { teb = ios_thread_registry_teb(k); break; }
            if (teb) perf_read(teb + 0x40, owner, sizeof(owner));
            uintptr_t pc = arm_thread_state64_get_pc(st), lr = arm_thread_state64_get_lr(st);
            /* Only resolve an FEX block when x28 is readable; the resolver also
             * validates its block metadata. Other PCs legitimately resolve 0. */
            uint64_t block = 0;
            if (teb && perf_read(st.__x[28], &block, sizeof(block)))
                rip = ios_native_rip_from_hostpc(block, pc, &why);
            dladdr((void *)pc, &pcinfo); dladdr((void *)lr, &lrinfo);
            {
                /* Mach run states: 1 running, 2 stopped, 3 waiting, 4 uninterruptible, 5 halted.
                 * Cumulative user/system ms let consecutive snapshots show whether a
                 * "waiting" thread is really burning kernel time (faults, decompression). */
                thread_extended_info_data_t xi = {0};
                mach_msg_type_number_t xn = THREAD_EXTENDED_INFO_COUNT;
                thread_info(threads[i], THREAD_EXTENDED_INFO, (thread_info_t)&xi, &xn);
                dprintf(2, "[stall-thread] native_tid=%llu name=%s state=%s user_ms=%.1f sys_ms=%.1f pri=%d\n",
                        (unsigned long long)ident.thread_id, xi.pth_name[0] ? xi.pth_name : "-",
                        basic.run_state == 1 ? "running" : basic.run_state == 3 ? "waiting" :
                        basic.run_state == 4 ? "uninterruptible" : basic.run_state == 2 ? "stopped" : "other",
                        xi.pth_user_time / 1e6, xi.pth_system_time / 1e6, xi.pth_curpri);
                if (basic.run_state != 1) perf_fault_candidate(ident.thread_id, &st, basic.run_state);
                if (nsecond < 16) { second[nsecond].port = threads[i]; second[nsecond].tid = ident.thread_id;
                                    second[nsecond].pc = pc; second[nsecond].state = basic.run_state; nsecond++; keep = 1; }
            }
            dprintf(2, "[stall-pc] native_tid=%llu wine_pid=%04x wine_tid=%04x state=%d suspend=%d pc=%llx %s+%llx lr=%llx %s+%llx guest_rip=%llx resolve=%s\n",
                    (unsigned long long)ident.thread_id, (unsigned)owner[0], (unsigned)owner[1],
                    basic.run_state, basic.suspend_count, (unsigned long long)pc,
                    pcinfo.dli_sname ? pcinfo.dli_sname : pcinfo.dli_fname ? pcinfo.dli_fname : "?",
                    (unsigned long long)(pc - (uintptr_t)(pcinfo.dli_saddr ? pcinfo.dli_saddr : pcinfo.dli_fbase)),
                    (unsigned long long)lr,
                    lrinfo.dli_sname ? lrinfo.dli_sname : lrinfo.dli_fname ? lrinfo.dli_fname : "?",
                    (unsigned long long)(lr - (uintptr_t)(lrinfo.dli_saddr ? lrinfo.dli_saddr : lrinfo.dli_fbase)),
                    (unsigned long long)rip, why ? why : "?");
            break;
        }
        if (!keep) mach_port_deallocate(mach_task_self(), threads[i]);
    }
    vm_deallocate(mach_task_self(), (vm_address_t)threads, count * sizeof(*threads));
    /* A second look 15 ms later: a state 4 that persists with the same PC is a
     * long kernel wait (page-in, file I/O); a changed PC means it was moving. */
    if (nsecond)
    {
        char line[1200];
        int len = snprintf(line, sizeof(line), "[stall-pc2] after_ms=15");
        usleep(15000);
        for (s2 = 0; s2 < nsecond; s2++)
        {
            arm_thread_state64_t st;
            thread_basic_info_data_t basic = {0};
            mach_msg_type_number_t n = ARM_THREAD_STATE64_COUNT;
            uint64_t pc = 0;
            if (!thread_get_state(second[s2].port, ARM_THREAD_STATE64, (thread_state_t)&st, &n))
                pc = arm_thread_state64_get_pc(st);
            n = THREAD_BASIC_INFO_COUNT;
            thread_info(second[s2].port, THREAD_BASIC_INFO, (thread_info_t)&basic, &n);
            if (len < (int)sizeof(line) - 80)
                len += snprintf(line + len, sizeof(line) - len, " %llu:%d->%d:%s",
                                (unsigned long long)second[s2].tid, second[s2].state, basic.run_state,
                                pc == second[s2].pc ? "same-pc" : "moved");
            mach_port_deallocate(mach_task_self(), second[s2].port);
        }
        dprintf(2, "%s\n", line);
    }
    ios_perf_wait_report(ids, nids);
    dprintf(2, "[stall-pc-cost] ms=%.2f selected=%u (live PC samples; loading/background can also trigger; includes 15 ms second look)\n",
            (perf_now_ns() - start) / 1e6, nids);
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

        dprintf(2, "[perf-io] t=%.0fs scope=all-processes read_pread_calls=%llu MB=%.2f sum_ms=%.1f max_ms=%.1f errors=%llu short_or_eof=%llu untracked_threads=%u\n",
                (now - t_start) / 1e9,
                (unsigned long long)atomic_exchange_explicit(&perf_read_calls, 0, memory_order_relaxed),
                atomic_exchange_explicit(&perf_read_bytes, 0, memory_order_relaxed) / 1048576.0,
                atomic_exchange_explicit(&perf_read_ns, 0, memory_order_relaxed) / 1e6,
                atomic_exchange_explicit(&perf_read_max_ns, 0, memory_order_relaxed) / 1e6,
                (unsigned long long)atomic_exchange_explicit(&perf_read_errors, 0, memory_order_relaxed),
                (unsigned long long)atomic_exchange_explicit(&perf_read_short, 0, memory_order_relaxed),
                atomic_load_explicit(&perf_io_untracked, memory_order_relaxed));

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
                        "presents=%llu skipped=%llu | panel=%dHz intent=%dHz vsync=%d | cpu=%.0f%% | footprint=%lluMB scope=all-processes\n",
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
#define CLASS_PCS 4096
#define CLASS_SYSTEM_PCS 2048
#define CLASS_GAME_PCS 2048
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
struct class_pc_bucket
{
    uint64_t id;
    uintptr_t pc, offset;
    unsigned tid, hits, window_hits;
    enum perf_class kind;
    char name[64];
};
struct class_game_bucket
{
    uint64_t id, block, teb;
    uintptr_t pc;
    unsigned tid, hits;
};
static struct ios_perf_image class_pe[IOS_PERF_IMAGES];
static struct ios_perf_fex_range class_fex_ranges[IOS_PERF_FEX_RANGES];
static struct class_image class_dylibs[CLASS_DYLIBS];
static struct { uintptr_t lo, hi; } class_traps[CLASS_TRAPS];
static struct class_bucket class_buckets[CLASS_BUCKETS];
static struct class_pc_bucket class_pcs[CLASS_PCS];
static struct class_pc_bucket class_system_pcs[CLASS_SYSTEM_PCS];
static struct class_game_bucket class_game_pcs[CLASS_GAME_PCS];
static struct class_row class_rows[CLASS_ROWS];
static struct perf_thread class_threads[PERF_THREADS];
static enum perf_class class_pe_classes[IOS_PERF_IMAGES];
static unsigned class_npe, class_nfex_ranges, class_ndylibs, class_ntraps, class_dropped;
static unsigned class_pc_dropped, class_system_dropped, class_game_dropped;
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
        if (dispatcher && pc >= dispatcher && pc - dispatcher < 0x4000)
        {
            *name = "jit-dispatcher"; *offset = pc - dispatcher;
            return C_FEX;
        }
        *name = "jit-pool"; *offset = pc - (uintptr_t)ios_jit_rx_base_global;
        return C_GAME;
    }
    for (i = 0; i < class_nfex_ranges; ++i)
        if (pc >= class_fex_ranges[i].base &&
            pc - class_fex_ranges[i].base < class_fex_ranges[i].size)
        {
            *name = "fex-code-buffer"; *offset = pc - class_fex_ranges[i].base;
            return C_GAME;
        }
    if (ios_fex_arena_base_unix && pc >= ios_fex_arena_base_unix && pc < ios_fex_arena_end_unix)
    {
        *name = "fex-arena"; *offset = pc - ios_fex_arena_base_unix;
        return C_FEX;
    }
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

/* Keep raw PCs cheap to record. dladdr may take dyld locks, so it is used only
 * by the reporter, after the sampled thread is running again. C_UNIX is kept
 * because FEXCore is statically linked into Madeira's main executable. */
static void class_pc_add(struct class_row *row, enum perf_class kind, uintptr_t pc,
                         const char *name, uintptr_t offset)
{
    unsigned i, h;
    if (kind != C_FEX && kind != C_UNIX) return;
    h = (unsigned)(((pc >> 2) ^ row->id) * 2654435761u) % CLASS_PCS;
    for (i = 0; i < CLASS_PCS; ++i, h = (h + 1) % CLASS_PCS)
    {
        struct class_pc_bucket *b = &class_pcs[h];
        if (!b->hits || (b->id == row->id && b->pc == pc))
        {
            if (!b->hits)
            {
                b->id = row->id; b->tid = row->tid; b->pc = pc; b->kind = kind; b->offset = offset;
                snprintf(b->name, sizeof(b->name), "%s", name);
            }
            ++b->hits; ++b->window_hits;
            return;
        }
    }
    ++class_pc_dropped;
}

static void class_system_add(struct class_row *row, uintptr_t pc,
                             const char *name, uintptr_t offset)
{
    unsigned i, h = (unsigned)(((pc >> 2) ^ row->id) * 2654435761u) % CLASS_SYSTEM_PCS;
    for (i = 0; i < CLASS_SYSTEM_PCS; ++i, h = (h + 1) % CLASS_SYSTEM_PCS)
    {
        struct class_pc_bucket *b = &class_system_pcs[h];
        if (!b->hits || (b->id == row->id && b->pc == pc))
        {
            if (!b->hits)
            {
                b->id = row->id; b->tid = row->tid; b->pc = pc; b->kind = C_SYSTEM; b->offset = offset;
                snprintf(b->name, sizeof(b->name), "%s", name);
            }
            ++b->hits;
            return;
        }
    }
    ++class_system_dropped;
}

static void class_game_add(struct class_row *row, uintptr_t pc, uint64_t x28, uintptr_t teb)
{
    uint64_t block = 0;
    unsigned i, h;
    if (!perf_read(x28, &block, sizeof(block)) || !block) return;
    h = (unsigned)(((pc >> 2) ^ block ^ row->id) * 2654435761u) % CLASS_GAME_PCS;
    for (i = 0; i < CLASS_GAME_PCS; ++i, h = (h + 1) % CLASS_GAME_PCS)
    {
        struct class_game_bucket *b = &class_game_pcs[h];
        if (!b->hits || (b->id == row->id && b->pc == pc && b->block == block))
        {
            if (!b->hits)
            { b->id = row->id; b->tid = row->tid; b->pc = pc; b->block = block; b->teb = teb; }
            ++b->hits;
            return;
        }
    }
    ++class_game_dropped;
}

enum class_fex_sub { FEX_DISPATCH, FEX_COMPILE, FEX_SYSCALL, FEX_SIGNAL, FEX_OTHER, FEX_SUB_COUNT };

static int class_is_fex_symbol(const char *s)
{
    return s && (strstr(s, "FEXCore") || strstr(s, "fextl") || strstr(s, "Arm64JIT") ||
                 strstr(s, "iOSSyscallHandler") || strstr(s, "iOSSignalDelegator"));
}

static enum class_fex_sub class_fex_sub(const char *s)
{
    if (!s) return FEX_OTHER;
    if (strstr(s, "Compile") || strstr(s, "Frontend") || strstr(s, "Decoder") ||
        strstr(s, "IR::") || strstr(s, "Backend") || strstr(s, "Emitter") || strstr(s, "JIT::"))
        return FEX_COMPILE;
    if (strstr(s, "Syscall") || strstr(s, "Thunk") || strstr(s, "HLE")) return FEX_SYSCALL;
    if (strstr(s, "Signal") || strstr(s, "Fault") || strstr(s, "Exception")) return FEX_SIGNAL;
    if (strstr(s, "Dispatch") || strstr(s, "Lookup") || strstr(s, "FindBlock") ||
        strstr(s, "GuestToHost") || strstr(s, "jit-dispatcher")) return FEX_DISPATCH;
    return FEX_OTHER;
}

static int class_pc_symbol(struct class_pc_bucket *b, char *out, size_t cap,
                           enum class_fex_sub *sub)
{
    Dl_info di = {0};
    const char *symbol = NULL, *image;
    int is_fex = b->kind == C_FEX;
    if (dladdr((void *)b->pc, &di) && di.dli_fname)
    {
        symbol = di.dli_sname;
        if (class_is_fex_symbol(symbol)) is_fex = 1;
        if (symbol) snprintf(out, cap, "%s", symbol);
        else
        {
            image = class_basename(di.dli_fname);
            snprintf(out, cap, "%s+0x%llx", image,
                     (unsigned long long)((b->pc - (uintptr_t)di.dli_fbase) & ~(uintptr_t)0xfff));
        }
    }
    else snprintf(out, cap, "%s+0x%llx", b->name,
                  (unsigned long long)(b->offset & ~(uintptr_t)0xfff));
    *sub = class_fex_sub(symbol ? symbol : b->name);
    return is_fex;
}

static void class_adjust_fex(struct class_row *row, unsigned sub[FEX_SUB_COUNT])
{
    unsigned i;
    memset(sub, 0, FEX_SUB_COUNT * sizeof(*sub));
    for (i = 0; i < CLASS_PCS; ++i)
    {
        struct class_pc_bucket *b = &class_pcs[i];
        enum class_fex_sub which;
        char desc[192];
        unsigned hits;
        if (b->id != row->id || !(hits = b->window_hits)) continue;
        b->window_hits = 0;
        if (!class_pc_symbol(b, desc, sizeof(desc), &which)) continue;
        sub[which] += hits;
        if (b->kind == C_UNIX && row->counts[C_UNIX] >= hits)
        { row->counts[C_UNIX] -= hits; row->counts[C_FEX] += hits; }
    }
}

static void class_report(void)
{
    unsigned i, j, rows = 0;
    for (i = 0; i < CLASS_ROWS; ++i)
    {
        struct class_row *r = &class_rows[i];
        unsigned fex_sub[FEX_SUB_COUNT];
        char line[1024];
        int len;
        if (!r->id) continue;
        ++rows;
        class_adjust_fex(r, fex_sub);
        len = snprintf(line, sizeof(line), "[perf-classify] tid=%04x name=%s samples=%u", r->tid, r->name, r->samples);
        for (j = 0; j < C_COUNT; ++j)
            len += snprintf(line + len, sizeof(line) - len, " | %s %.1f%%", class_names[j],
                            r->samples ? 100.0 * r->counts[j] / r->samples : 0.0);
        len += snprintf(line + len, sizeof(line) - len,
                        " | fex(dispatch %.1f%% compile %.1f%% syscall %.1f%% signal %.1f%% other %.1f%%)",
                        r->samples ? 100.0 * fex_sub[FEX_DISPATCH] / r->samples : 0.0,
                        r->samples ? 100.0 * fex_sub[FEX_COMPILE] / r->samples : 0.0,
                        r->samples ? 100.0 * fex_sub[FEX_SYSCALL] / r->samples : 0.0,
                        r->samples ? 100.0 * fex_sub[FEX_SIGNAL] / r->samples : 0.0,
                        r->samples ? 100.0 * fex_sub[FEX_OTHER] / r->samples : 0.0);
        dprintf(2, "%s\n", line);
        dprintf(2, "[perf-classify-state] tid=%04x running=%u blocked=%u other=%u unknown=%u missed=%u\n",
                r->tid, r->running, r->waiting, r->state_other, r->state_failed, r->failed);
    }
    if (!rows) dprintf(2, "[perf-classify] tid=0000 name=(no-active-Wine-thread) samples=0\n");
    for (i = 0; i < CLASS_PCS; ++i) class_pcs[i].window_hits = 0;
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

struct class_named_bucket
{
    uint64_t id;
    unsigned tid, hits;
    char name[192];
};

#include "perf_guest_pc.h"

static void class_named_add(struct class_named_bucket *out, unsigned cap, unsigned *count,
                            uint64_t id, unsigned tid, const char *name, unsigned hits)
{
    unsigned i;
    for (i = 0; i < *count; ++i)
        if (out[i].id == id && !strcmp(out[i].name, name)) { out[i].hits += hits; return; }
    if (*count >= cap) return;
    out[*count].id = id; out[*count].tid = tid; out[*count].hits = hits;
    snprintf(out[*count].name, sizeof(out[*count].name), "%s", name);
    ++*count;
}

static void class_report_fex(void)
{
    static struct class_named_bucket named[CLASS_PCS];
    unsigned totals[CLASS_ROWS * 2] = {0};
    uint64_t total_id[CLASS_ROWS * 2] = {0};
    unsigned count = 0, ntotal = 0, i, rank;
    memset(named, 0, sizeof(named));
    for (i = 0; i < CLASS_PCS; ++i)
    {
        struct class_pc_bucket *b = &class_pcs[i];
        enum class_fex_sub sub;
        char desc[192];
        unsigned j;
        if (!b->hits || !class_pc_symbol(b, desc, sizeof(desc), &sub)) continue;
        class_named_add(named, CLASS_PCS, &count, b->id, b->tid, desc, b->hits);
        for (j = 0; j < ntotal && total_id[j] != b->id; ++j) ;
        if (j == ntotal && ntotal < sizeof(totals)/sizeof(totals[0]))
        { total_id[ntotal] = b->id; ++ntotal; }
        if (j < ntotal) totals[j] += b->hits;
    }
    for (rank = 0; rank < 25; ++rank)
    {
        unsigned best = ~0u, j;
        for (i = 0; i < count; ++i)
            if (named[i].hits && (best == ~0u || named[i].hits > named[best].hits)) best = i;
        if (best == ~0u) break;
        for (j = 0; j < ntotal && total_id[j] != named[best].id; ++j) ;
        dprintf(2, "[perf-classify-fex] tid=%04x %s samples=%u pct=%.1f\n",
                named[best].tid, named[best].name, named[best].hits,
                j < ntotal && totals[j] ? 100.0 * named[best].hits / totals[j] : 0.0);
        named[best].hits = 0;
    }
    if (class_pc_dropped)
        dprintf(2, "[perf-classify-fex] pc-table-full dropped=%u\n", class_pc_dropped);
    memset(class_pcs, 0, sizeof(class_pcs)); class_pc_dropped = 0;
}

static void class_report_system(void)
{
    static struct class_named_bucket named[CLASS_SYSTEM_PCS];
    unsigned totals[CLASS_ROWS * 2] = {0};
    uint64_t total_id[CLASS_ROWS * 2] = {0};
    unsigned count = 0, ntotal = 0, i, rank;
    memset(named, 0, sizeof(named));
    for (i = 0; i < CLASS_SYSTEM_PCS; ++i)
    {
        struct class_pc_bucket *b = &class_system_pcs[i];
        Dl_info di = {0};
        char desc[192];
        unsigned j;
        if (!b->hits) continue;
        if (dladdr((void *)b->pc, &di) && di.dli_sname)
            snprintf(desc, sizeof(desc), "%s", di.dli_sname);
        else if (dladdr((void *)b->pc, &di) && di.dli_fname)
            snprintf(desc, sizeof(desc), "%s+0x%llx", class_basename(di.dli_fname),
                     (unsigned long long)((b->pc - (uintptr_t)di.dli_fbase) & ~(uintptr_t)0xfff));
        else snprintf(desc, sizeof(desc), "%s+0x%llx", b->name,
                      (unsigned long long)(b->offset & ~(uintptr_t)0xfff));
        class_named_add(named, CLASS_SYSTEM_PCS, &count, b->id, b->tid, desc, b->hits);
        for (j = 0; j < ntotal && total_id[j] != b->id; ++j) ;
        if (j == ntotal && ntotal < sizeof(totals)/sizeof(totals[0]))
        { total_id[ntotal] = b->id; ++ntotal; }
        if (j < ntotal) totals[j] += b->hits;
    }
    for (rank = 0; rank < 10; ++rank)
    {
        unsigned best = ~0u, j;
        for (i = 0; i < count; ++i)
            if (named[i].hits && (best == ~0u || named[i].hits > named[best].hits)) best = i;
        if (best == ~0u) break;
        for (j = 0; j < ntotal && total_id[j] != named[best].id; ++j) ;
        dprintf(2, "[perf-classify-system] tid=%04x %s samples=%u pct=%.1f\n",
                named[best].tid, named[best].name, named[best].hits,
                j < ntotal && totals[j] ? 100.0 * named[best].hits / totals[j] : 0.0);
        named[best].hits = 0;
    }
    if (class_system_dropped)
        dprintf(2, "[perf-classify-system] pc-table-full dropped=%u\n", class_system_dropped);
    memset(class_system_pcs, 0, sizeof(class_system_pcs)); class_system_dropped = 0;
}

static void class_report_game(void)
{
    extern uint64_t ios_native_rip_from_hostpc(uint64_t, uint64_t, const char **);
    extern const char *ios_guest_module_from_rip(uint64_t, uint64_t, uint64_t *);
    static struct class_named_bucket named[CLASS_GAME_PCS];
    static struct class_named_bucket detail[CLASS_GAME_PCS];
    const char *fine_env = getenv("MADEIRA_PERF_CLASSIFY_FINE");
    int fine = !fine_env || *fine_env != '0';
    unsigned totals[CLASS_ROWS * 2] = {0};
    uint64_t total_id[CLASS_ROWS * 2] = {0};
    unsigned count = 0, detail_count = 0, ntotal = 0, i, rank;
    memset(named, 0, sizeof(named));
    if (fine) memset(detail, 0, sizeof(detail));
    for (i = 0; i < CLASS_GAME_PCS; ++i)
    {
        struct class_game_bucket *b = &class_game_pcs[i];
        uint64_t rip, rva;
        const char *module;
        char desc[192];
        unsigned j;
        if (!b->hits || !(rip = ios_native_rip_from_hostpc(b->block, b->pc, NULL))) continue;
        module = ios_guest_module_from_rip(b->teb, rip, &rva);
        ios_perf_guest_pc_name(desc, sizeof(desc), module, rva, rip, 0);
        class_named_add(named, CLASS_GAME_PCS, &count, b->id, b->tid, desc, b->hits);
        if (fine)
        {
            ios_perf_guest_pc_name(desc, sizeof(desc), module, rva, rip, 1);
            class_named_add(detail, CLASS_GAME_PCS, &detail_count, b->id, b->tid, desc, b->hits);
        }
        for (j = 0; j < ntotal && total_id[j] != b->id; ++j) ;
        if (j == ntotal && ntotal < sizeof(totals)/sizeof(totals[0]))
        { total_id[ntotal] = b->id; ++ntotal; }
        if (j < ntotal) totals[j] += b->hits;
    }
    for (rank = 0; rank < 10; ++rank)
    {
        unsigned best = ~0u, j;
        for (i = 0; i < count; ++i)
            if (named[i].hits && (best == ~0u || named[i].hits > named[best].hits)) best = i;
        if (best == ~0u) break;
        for (j = 0; j < ntotal && total_id[j] != named[best].id; ++j) ;
        dprintf(2, "[perf-classify-game] tid=%04x %s samples=%u pct=%.1f\n",
                named[best].tid, named[best].name, named[best].hits,
                j < ntotal && totals[j] ? 100.0 * named[best].hits / totals[j] : 0.0);
        named[best].hits = 0;
    }
    /* Same samples and per-thread denominator, no additional suspensions or
     * guest walks. 16-byte buckets distinguish loops within the hot 4 KiB page.
     * These are addresses, not inferred function names or whole-process CPU %. */
    if (fine) for (rank = 0; rank < 10; ++rank)
    {
        unsigned best = ~0u, j;
        for (i = 0; i < detail_count; ++i)
            if (detail[i].hits && (best == ~0u || detail[i].hits > detail[best].hits)) best = i;
        if (best == ~0u) break;
        for (j = 0; j < ntotal && total_id[j] != detail[best].id; ++j) ;
        dprintf(2, "[perf-classify-game-detail] tid=%04x %s samples=%u pct=%.1f bucket=16B\n",
                detail[best].tid, detail[best].name, detail[best].hits,
                j < ntotal && totals[j] ? 100.0 * detail[best].hits / totals[j] : 0.0);
        detail[best].hits = 0;
    }
    if (class_game_dropped)
        dprintf(2, "[perf-classify-game] pc-table-full dropped=%u\n", class_game_dropped);
    memset(class_game_pcs, 0, sizeof(class_game_pcs)); class_game_dropped = 0;
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
        if (now >= top)
        {
            uint64_t t0 = perf_now_ns(), t1, t2, t3, t4;
            class_report_top(); t1 = perf_now_ns();
            class_report_fex(); t2 = perf_now_ns();
            class_report_system(); t3 = perf_now_ns();
            class_report_game(); t4 = perf_now_ns();
            /* Distinguish periodic diagnostic work from an actual game/PSO
             * stall. Times include lock waits; no target thread is suspended. */
            if (t4 - t0 >= 50000000ull)
                dprintf(2, "[perf-classify-cost] report_ms=%.1f top_ms=%.1f fex_symbols_ms=%.1f system_symbols_ms=%.1f guest_ms=%.1f\n",
                        (t4-t0)/1e6, (t1-t0)/1e6, (t2-t1)/1e6, (t3-t2)/1e6, (t4-t3)/1e6);
            top = t4 + 30000000000ull;
        }
        if (now >= refresh)
        {
            uintptr_t area = 0, entry = 0;
            unsigned i;
            if (selected) mach_port_deallocate(mach_task_self(), selected);
            selected = class_pick(++gen, &id, &tid, &teb, name);
            class_npe = ios_perf_image_snapshot(class_pe, IOS_PERF_IMAGES);
            class_nfex_ranges = ios_perf_fex_range_snapshot(class_fex_ranges, IOS_PERF_FEX_RANGES);
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
                class_pc_add(r, kind, pc, module, offset);
                if (kind == C_SYSTEM) class_system_add(r, pc, module, offset);
                if (kind == C_GAME) class_game_add(r, pc, st.__x[28], teb);
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
     * PC classes every 5 seconds and native/guest hot spots every 30 seconds.
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
    dprintf(2, "[perf-hitch] diagnostics v3: v2 + in-flight Wine wait targets/wakers ([stall-wait*], [wait-done]), uninterruptible fault pages ([stall-fault], [stall-vm], [stall-swap]); no extra thread suspensions\n");
    if (!pthread_create( &t, NULL, perf_reporter, NULL )) pthread_detach( t );
    dprintf( 2, "[perf] profile on: a report every %us\n", perf_secs );
}
