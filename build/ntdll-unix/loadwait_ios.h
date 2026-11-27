/* madeira-bcd load-wait (2026-11-27): what a game's threads wait on during its
 * loading screen. Diagnostic only: no wait, timeout, signal or schedule changes.
 *
 * Why: RDR2 on the iPhone 18 Pro shows its loading screen for 75-78 s. In every
 * RDR2 log kept (2026-11-26 13:30 .. 2026-11-27 01:05) the main thread then sits
 * at 10-30 ms of CPU per 2 s for 10-70 s ("dead zone") before a CPU-bound main
 * thread phase and the parallel load. The existing v3 wait probe cannot see it:
 * its snapshot fires only on windows with no completed Present, and the loading
 * screen presents at ~55 fps. This census does not depend on Presents stopping.
 *
 * MADEIRA_LOAD_WAIT = "<exe>[;<exe>...][:<seconds>]" (RDR2 profile: RDR2.exe:180).
 * The first listed process that waits (its first thread's Wine pid) is followed.
 * Recording starts when the [xp] sampler sees madeira_d3d12's Present counter
 * above zero (the first loading-screen frame) and stops <seconds> later
 * (10..900, default 180). Every ~2 s one report: for each (thread, kind, object,
 * timeout) the number of waits, the summed and longest wait, how many timed out
 * and who ended the last one (the wineserver's waker record: Wine tid/pid, 0 =
 * the server itself: timeout, timer, exit). Kinds: select = any wineserver wait
 * (handles, IOCP, async file/socket/pipe I/O, alertable sleeps), sleep =
 * non-alertable NtDelayExecution, alert = NtWaitForAlertByThreadId (critical
 * sections, SRW locks, condition variables, WaitOnAddress; object = address).
 * In-process fastsync parks (<= 2 ms each, then a select) are not listed.
 *
 * Cost: unset = one load of ios_lw_state per wait. Configured but not armed = a
 * thread-local flag test per wait. Armed = a lock-protected insert into a 256
 * entry table per wait of the followed process only. After the window: one load.
 *
 * Self-contained so tests/host/check-load-wait.py compiles it with mocks; the
 * includer defines ios_lw_state and supplies the clock and the image path. */
#ifndef MADEIRA_LOADWAIT_IOS_H
#define MADEIRA_LOADWAIT_IOS_H

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <os/lock.h>

enum { IOS_LW_OFF = 0, IOS_LW_WAITING = 1, IOS_LW_ARMED = 2, IOS_LW_DONE = 3 };
enum { IOS_LW_SELECT = 1, IOS_LW_SLEEP = 2, IOS_LW_ALERT = 3 };

#define IOS_LW_SLOTS     256
#define IOS_LW_TMO_INF   (-1)
#define IOS_LW_TMO_ABS   (-2)
#define IOS_LW_TMO_NONE  (-3)
#define IOS_LW_REPORT_NS 2000000000ull

extern volatile int ios_lw_state;   /* defined by the includer; read by sync.c's wrappers */

struct ios_lw_key
{
    uint32_t tid;
    uint8_t  kind;
    int8_t   op;        /* select_op opcode, -1 = none (sleep, alert, no-handle select) */
    uint16_t count;     /* handles in the select */
    uint64_t obj;       /* first handle, or the alert address */
    uint32_t h1;        /* second handle */
    int32_t  tmo_ms;    /* requested timeout bucket, IOS_LW_TMO_* */
};

struct ios_lw_ent
{
    struct ios_lw_key k;
    uint32_t used;
    uint32_t n, timeouts, immediate, woken;
    uint64_t sum_ns, max_ns;
    uint32_t waker, waker_pid, status;
};

static struct
{
    volatile uint32_t pid;
    int secs;
    char exes[192];
    uint64_t t0_ns, until_ns, last_report_ns, window_t0_ns;
    os_unfair_lock lock;
    uint32_t used, dropped, total_waits;
    struct ios_lw_ent tab[IOS_LW_SLOTS];
} ios_lw;   /* zero = unlocked (OS_UNFAIR_LOCK_INIT is all zero) */

/* "<exe>[;<exe>...][:<seconds>]" -> list (';'-separated, as given) and seconds.
 * Returns 1 when on; 0 for unset, empty, "0" or a value without an exe. */
static int ios_lw_parse( const char *spec, char *exes, size_t cap, int *secs )
{
    const char *colon;
    size_t n;

    *secs = 180;
    if (cap) exes[0] = 0;
    if (!spec || !spec[0] || !strcmp( spec, "0" ) || !cap) return 0;
    colon = strrchr( spec, ':' );
    n = colon ? (size_t)(colon - spec) : strlen( spec );
    if (colon)
    {
        char *end;
        long v = strtol( colon + 1, &end, 10 );
        if (end == colon + 1 || *end) return 0;
        if (v < 10) v = 10;
        if (v > 900) v = 900;
        *secs = (int)v;
    }
    if (!n || n >= cap) return 0;
    memcpy( exes, spec, n );
    exes[n] = 0;
    {   /* digits only is not an exe name */
        size_t i;
        for (i = 0; i < n && exes[i] >= '0' && exes[i] <= '9'; i++) ;
        if (i == n) { exes[0] = 0; return 0; }
    }
    return 1;
}

/* Does the image path's file name match one entry of list (';' or ',' between,
 * ASCII case-insensitive)? path is UTF-16, len in code units. */
static int ios_lw_image_listed( const uint16_t *path, int len, const char *list )
{
    int start = len, i;
    const char *p = list;

    if (!path || len <= 0 || !list) return 0;
    while (start > 0 && path[start - 1] != '\\' && path[start - 1] != '/') start--;
    while (*p)
    {
        const char *e = p;
        int n, ok;
        while (*e && *e != ';' && *e != ',') e++;
        n = (int)(e - p);
        ok = n > 0 && n == len - start;
        for (i = 0; ok && i < n; i++)
        {
            uint16_t c = path[start + i];
            char a = p[i];
            if (c >= 'A' && c <= 'Z') c += 'a' - 'A';
            if (a >= 'A' && a <= 'Z') a += 'a' - 'A';
            ok = c == (uint16_t)(unsigned char)a;
        }
        if (ok) return 1;
        p = *e ? e + 1 : e;
    }
    return 0;
}

/* Requested timeout of a server_select, in ms buckets. timeout is server_select's
 * abs_timeout: INFINITE, 0 (poll), > 0 absolute system time, < 0 = -(monotonic
 * deadline in 100 ns). The request is rebuilt as deadline - now + elapsed. */
static int32_t ios_lw_tmo_bucket( long long timeout, uint64_t dur_ns, long long mono_now_100ns )
{
    long long ms;
    if (timeout == 0x7fffffffffffffffll) return IOS_LW_TMO_INF;
    if (!timeout) return 0;
    if (timeout > 0) return IOS_LW_TMO_ABS;
    ms = ((-timeout) - mono_now_100ns) / 10000 + (long long)(dur_ns / 1000000ull);
    if (ms < 0) ms = 0;
    if (ms > 10000) ms = (ms + 500) / 1000 * 1000;
    else if (ms > 1000) ms = (ms + 5) / 10 * 10;
    if (ms > 0x7fffffffll) ms = 0x7fffffffll;
    return (int32_t)ms;
}

/* A relative or absolute NT timeout as given to NtDelayExecution and
 * NtWaitForAlertByThreadId (< 0 relative 100 ns, > 0 absolute, NULL infinite). */
static int32_t ios_lw_nt_tmo_bucket( const long long *timeout )
{
    long long ms;
    if (!timeout || *timeout == 0x7fffffffffffffffll) return IOS_LW_TMO_INF;
    if (!*timeout) return 0;
    if (*timeout > 0) return IOS_LW_TMO_ABS;
    ms = (-*timeout) / 10000;
    if (ms > 10000) ms = (ms + 500) / 1000 * 1000;
    else if (ms > 1000) ms = (ms + 5) / 10 * 10;
    if (ms > 0x7fffffffll) ms = 0x7fffffffll;
    return (int32_t)ms;
}

static unsigned int ios_lw_hash( const struct ios_lw_key *k )
{
    uint64_t h = 1469598103934665603ull;
    h = (h ^ k->tid) * 1099511628211ull;
    h = (h ^ k->kind) * 1099511628211ull;
    h = (h ^ (uint8_t)k->op) * 1099511628211ull;
    h = (h ^ k->count) * 1099511628211ull;
    h = (h ^ k->obj) * 1099511628211ull;
    h = (h ^ k->h1) * 1099511628211ull;
    h = (h ^ (uint32_t)k->tmo_ms) * 1099511628211ull;
    return (unsigned int)(h ^ (h >> 29));
}

static int ios_lw_key_eq( const struct ios_lw_key *a, const struct ios_lw_key *b )
{
    return a->tid == b->tid && a->kind == b->kind && a->op == b->op && a->count == b->count &&
           a->obj == b->obj && a->h1 == b->h1 && a->tmo_ms == b->tmo_ms;
}

/* One finished wait of the followed process. woke: the server recorded who ended it.
 * A wait finishing inside a signal handler that interrupted this thread's own
 * insert (Wine's suspend handler waits in the server) is not listed: the lock is
 * not recursive. */
static __thread int ios_lw_busy;
static void ios_lw_record( const struct ios_lw_key *k, uint64_t dur_ns, int woke,
                           uint32_t waker, uint32_t waker_pid, uint32_t status, int timed_out )
{
    unsigned int h = ios_lw_hash( k ) % IOS_LW_SLOTS, i;
    struct ios_lw_ent *e = NULL;

    if (ios_lw_busy) return;
    ios_lw_busy = 1;
    __atomic_signal_fence( __ATOMIC_SEQ_CST );
    os_unfair_lock_lock( &ios_lw.lock );
    ios_lw.total_waits++;
    for (i = 0; i < IOS_LW_SLOTS; i++, h = (h + 1) % IOS_LW_SLOTS)
    {
        struct ios_lw_ent *c = &ios_lw.tab[h];
        if (!c->used)
        {
            if (ios_lw.used >= IOS_LW_SLOTS - IOS_LW_SLOTS / 8) break;   /* keep probes short */
            memset( c, 0, sizeof(*c) );
            c->k = *k;
            c->used = 1;
            ios_lw.used++;
            e = c;
            break;
        }
        if (ios_lw_key_eq( &c->k, k )) { e = c; break; }
    }
    if (!e) ios_lw.dropped++;
    else
    {
        e->n++;
        e->sum_ns += dur_ns;
        if (dur_ns > e->max_ns) e->max_ns = dur_ns;
        if (timed_out) e->timeouts++;
        if (dur_ns < 50000) e->immediate++;
        if (woke) { e->woken++; e->waker = waker; e->waker_pid = waker_pid; }
        e->status = status;
    }
    os_unfair_lock_unlock( &ios_lw.lock );
    __atomic_signal_fence( __ATOMIC_SEQ_CST );
    ios_lw_busy = 0;
}

/* Sampler side: copy the window out, sorted by summed wait (largest first), and
 * start the next window. Returns the entries copied; dropped and waits get the
 * window's overflow and wait counts. */
static unsigned int ios_lw_take( struct ios_lw_ent *out, unsigned int cap, uint32_t *dropped, uint32_t *waits )
{
    unsigned int n = 0, i, j;

    os_unfair_lock_lock( &ios_lw.lock );
    for (i = 0; i < IOS_LW_SLOTS && n < cap; i++)
        if (ios_lw.tab[i].used) out[n++] = ios_lw.tab[i];
    memset( ios_lw.tab, 0, sizeof(ios_lw.tab) );
    *dropped = ios_lw.dropped;
    *waits = ios_lw.total_waits;
    ios_lw.used = ios_lw.dropped = ios_lw.total_waits = 0;
    os_unfair_lock_unlock( &ios_lw.lock );
    for (i = 1; i < n; i++)   /* insertion sort: n <= 256, once per 2 s */
    {
        struct ios_lw_ent t = out[i];
        for (j = i; j > 0 && out[j - 1].sum_ns < t.sum_ns; j--) out[j] = out[j - 1];
        out[j] = t;
    }
    return n;
}

/* Sampler side, every pass. Returns 1 when a report is due now; *final is set
 * on the last one. pe_valid/pres: madeira_d3d12's counter block and its Present
 * enqueue count. */
static int ios_lw_tick( uint64_t now_ns, int pe_valid, long long pres, int *final )
{
    int st = __atomic_load_n( &ios_lw_state, __ATOMIC_RELAXED );

    *final = 0;
    if (st == IOS_LW_WAITING)
    {
        if (!__atomic_load_n( &ios_lw.pid, __ATOMIC_ACQUIRE ) || !pe_valid || pres <= 0) return 0;
        os_unfair_lock_lock( &ios_lw.lock );
        memset( ios_lw.tab, 0, sizeof(ios_lw.tab) );
        ios_lw.used = ios_lw.dropped = ios_lw.total_waits = 0;
        os_unfair_lock_unlock( &ios_lw.lock );
        ios_lw.t0_ns = ios_lw.last_report_ns = ios_lw.window_t0_ns = now_ns;
        ios_lw.until_ns = now_ns + (uint64_t)ios_lw.secs * 1000000000ull;
        __atomic_store_n( &ios_lw_state, IOS_LW_ARMED, __ATOMIC_RELEASE );
        return 0;
    }
    if (st != IOS_LW_ARMED) return 0;
    if (now_ns >= ios_lw.until_ns)
    {
        __atomic_store_n( &ios_lw_state, IOS_LW_DONE, __ATOMIC_RELEASE );
        *final = 1;
        return 1;
    }
    if (now_ns - ios_lw.last_report_ns < IOS_LW_REPORT_NS) return 0;
    return 1;
}

static const char *ios_lw_kind_name( int kind )
{
    return kind == IOS_LW_SELECT ? "select" : kind == IOS_LW_SLEEP ? "sleep" : kind == IOS_LW_ALERT ? "alert" : "?";
}

static void ios_lw_tmo_text( char *out, size_t cap, int32_t tmo )
{
    if (tmo == IOS_LW_TMO_INF) snprintf( out, cap, "inf" );
    else if (tmo == IOS_LW_TMO_ABS) snprintf( out, cap, "abs" );
    else if (tmo == IOS_LW_TMO_NONE) snprintf( out, cap, "-" );
    else snprintf( out, cap, "%dms", (int)tmo );
}

/* One report line for an entry (no newline). main_tid marks the main thread '*'. */
static int ios_lw_format( char *out, size_t cap, const struct ios_lw_ent *e, uint32_t main_tid )
{
    char tmo[24], obj[48];
    ios_lw_tmo_text( tmo, sizeof(tmo), e->k.tmo_ms );
    if (e->k.kind == IOS_LW_ALERT) snprintf( obj, sizeof(obj), "addr=%llx", (unsigned long long)e->k.obj );
    else if (e->k.kind == IOS_LW_SLEEP) snprintf( obj, sizeof(obj), "-" );
    else if (e->k.count > 1) snprintf( obj, sizeof(obj), "op=%d n=%u h=%llx,%x", e->k.op, e->k.count,
                                       (unsigned long long)e->k.obj, e->k.h1 );
    else snprintf( obj, sizeof(obj), "op=%d n=%u h=%llx", e->k.op, e->k.count, (unsigned long long)e->k.obj );
    return snprintf( out, cap, "%04x%s %s %s tmo=%s x%u sum=%.1fms max=%.1fms timeouts=%u immediate=%u "
                     "waker=%s%04x/%04x (%u of %u recorded) st=%08x",
                     e->k.tid, e->k.tid == main_tid ? "*" : "", ios_lw_kind_name( e->k.kind ), obj, tmo,
                     e->n, e->sum_ns / 1e6, e->max_ns / 1e6, e->timeouts, e->immediate,
                     e->woken ? (e->waker ? "" : "server/") : "none/", e->waker, e->waker_pid,
                     e->woken, e->n, e->status );
}

#endif /* MADEIRA_LOADWAIT_IOS_H */
