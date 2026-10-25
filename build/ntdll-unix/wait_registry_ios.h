/* In-flight Wine wait registry, shared by ntdll (server_ios.c, the writer) and
 * the in-process wineserver (queue_ios.c, which resolves handles and records
 * who woke a wait). Both live in the same Mach task, so the table is plain
 * memory: no IPC, no allocation, no lock on the wait path.
 *
 * Slot = Wine thread id / 4, modulo the table. Wine thread ids are small
 * multiples of 4 reused lowest-first, so live threads rarely collide; the
 * server can index a thread's slot from thread->id alone. A slot names its
 * owner (wine_tid); a colliding thread takes it over only while the owner is
 * not in a wait, otherwise the newer wait is counted as untracked.
 *
 * Publication is a seqlock: gen is odd while the owner rewrites the fields,
 * readers retry or skip on a torn read. depth > 0 means "in a wait". A wait
 * nested inside a kernel APC saves the outer entry on its own stack and puts
 * it back when it leaves, so the outer wait stays visible afterwards.
 *
 * wake_*: written by the server thread just BEFORE it writes the wake-up
 * reply, so the woken client reads them after its pipe read returns.
 * wake_cookie is stored last (release) and matched against the client's own
 * cookie and entry time, so a record from an earlier wait is never taken for
 * this one. wake_tid 0 = the server itself (timeout, timer, process exit). */
#ifndef MADEIRA_WAIT_REGISTRY_IOS_H
#define MADEIRA_WAIT_REGISTRY_IOS_H

#include <stdint.h>
#include <string.h>

#define IOS_WAITREG_SLOTS 1024
#define IOS_WAITREG_HANDLES 8

enum ios_wait_phase { IOS_WAIT_PHASE_REQUEST = 1, IOS_WAIT_PHASE_PIPE = 2, IOS_WAIT_PHASE_APC = 3 };

struct ios_wait_entry
{
    volatile unsigned int  gen;          /* seqlock: odd while being rewritten */
    volatile unsigned int  depth;        /* 0 idle, 1 in a wait, 2+ nested    */
    volatile unsigned int  wine_tid;     /* slot owner                         */
    unsigned int           wine_pid;
    uint64_t               native_tid;   /* pthread_threadid_np                */
    void                  *teb;
    uint64_t               t0_ns;        /* CLOCK_MONOTONIC at wait entry      */
    unsigned int           flags;        /* SELECT_* (alertable etc.)          */
    int                    op;           /* select_op opcode, -1 = none        */
    long long              timeout;      /* abs_timeout as passed down         */
    int                    count;
    unsigned int           handles[IOS_WAITREG_HANDLES];
    uint64_t               key;          /* keyed-event key, or signal handle  */
    uint64_t               cookie;       /* client address of the wait cookie  */
    uint64_t               frame;        /* syscall frame: the caller context  */
    void                  *ret_pc;       /* native caller of server_select     */
    /* updated in place by the owner during the wait (relaxed atomics) */
    volatile unsigned int  phase, requests, apcs;
    volatile uint64_t      watch_until_ns;   /* a stall snapshot selected it */
    /* written by the server thread */
    volatile uint64_t      wake_cookie;
    volatile uint64_t      wake_ns;
    volatile unsigned int  wake_tid, wake_pid;
    volatile int           wake_status;
};

/* What one wait publishes; copied into the slot at entry. */
struct ios_wait_info
{
    unsigned int wine_tid, wine_pid;
    uint64_t native_tid;
    void *teb;
    uint64_t t0_ns;
    unsigned int flags;
    int op;
    long long timeout;
    int count;
    unsigned int handles[IOS_WAITREG_HANDLES];
    uint64_t key, cookie, frame;
    void *ret_pc;
};

/* The owner's state for one wait, on its stack, between enter and leave. */
struct ios_wait_token
{
    struct ios_wait_entry *slot;      /* NULL: untracked (collision)  */
    int nested;                       /* an outer entry was saved     */
    struct ios_wait_info outer;
};

static inline unsigned int ios_wait_slot_index( unsigned int wine_tid )
{
    return (wine_tid >> 2) % IOS_WAITREG_SLOTS;
}

static inline void ios_wait_copy_in( struct ios_wait_entry *e, const struct ios_wait_info *in )
{
    e->wine_pid   = in->wine_pid;
    e->native_tid = in->native_tid;
    e->teb        = in->teb;
    e->t0_ns      = in->t0_ns;
    e->flags      = in->flags;
    e->op         = in->op;
    e->timeout    = in->timeout;
    e->count      = in->count < 0 ? 0 : in->count > IOS_WAITREG_HANDLES ? IOS_WAITREG_HANDLES : in->count;
    memcpy( (void *)e->handles, in->handles, sizeof(e->handles) );
    e->key        = in->key;
    e->cookie     = in->cookie;
    e->frame      = in->frame;
    e->ret_pc     = in->ret_pc;
}

static inline void ios_wait_copy_out( const struct ios_wait_entry *e, struct ios_wait_info *out )
{
    out->wine_tid   = e->wine_tid;
    out->wine_pid   = e->wine_pid;
    out->native_tid = e->native_tid;
    out->teb        = e->teb;
    out->t0_ns      = e->t0_ns;
    out->flags      = e->flags;
    out->op         = e->op;
    out->timeout    = e->timeout;
    out->count      = e->count;
    memcpy( out->handles, (const void *)e->handles, sizeof(out->handles) );
    out->key        = e->key;
    out->cookie     = e->cookie;
    out->frame      = e->frame;
    out->ret_pc     = e->ret_pc;
}

/* Owner side. Returns the slot, or NULL when another live thread holds it. */
static inline struct ios_wait_entry *ios_wait_publish( struct ios_wait_entry *table,
                                                       const struct ios_wait_info *in,
                                                       struct ios_wait_token *tok )
{
    struct ios_wait_entry *e = &table[ios_wait_slot_index( in->wine_tid )];
    unsigned int owner = __atomic_load_n( &e->wine_tid, __ATOMIC_ACQUIRE );

    tok->slot = NULL;
    tok->nested = 0;
    if (owner != in->wine_tid)
    {
        /* a colliding thread: take the slot only while its owner is idle */
        if (owner && __atomic_load_n( &e->depth, __ATOMIC_ACQUIRE )) return NULL;
        if (!__atomic_compare_exchange_n( &e->wine_tid, &owner, in->wine_tid, 0,
                                          __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE )) return NULL;
        __atomic_store_n( &e->depth, 0, __ATOMIC_RELEASE );
    }
    /* A thread killed inside a wait never unpublished it; a new thread that
     * got the same Wine id must not treat that leftover as its outer wait. */
    else if (__atomic_load_n( &e->depth, __ATOMIC_ACQUIRE ) && e->native_tid != in->native_tid)
        __atomic_store_n( &e->depth, 0, __ATOMIC_RELEASE );
    if (__atomic_load_n( &e->depth, __ATOMIC_ACQUIRE ))
    {
        ios_wait_copy_out( e, &tok->outer );
        tok->nested = 1;
    }
    __atomic_add_fetch( &e->gen, 1, __ATOMIC_ACQ_REL );          /* -> odd */
    ios_wait_copy_in( e, in );
    __atomic_store_n( &e->phase, IOS_WAIT_PHASE_REQUEST, __ATOMIC_RELAXED );
    __atomic_store_n( &e->requests, 0, __ATOMIC_RELAXED );
    __atomic_store_n( &e->apcs, 0, __ATOMIC_RELAXED );
    __atomic_store_n( &e->wake_cookie, 0, __ATOMIC_RELAXED );
    __atomic_store_n( &e->depth, e->depth + 1, __ATOMIC_RELAXED );
    __atomic_add_fetch( &e->gen, 1, __ATOMIC_RELEASE );          /* -> even */
    tok->slot = e;
    return e;
}

/* Owner side: undo exactly one publish, restoring an outer wait if any. */
static inline void ios_wait_unpublish( struct ios_wait_token *tok )
{
    struct ios_wait_entry *e = tok->slot;
    if (!e) return;
    __atomic_add_fetch( &e->gen, 1, __ATOMIC_ACQ_REL );
    if (tok->nested)
    {
        ios_wait_copy_in( e, &tok->outer );
        __atomic_store_n( &e->phase, IOS_WAIT_PHASE_APC, __ATOMIC_RELAXED );
    }
    __atomic_store_n( &e->depth, e->depth ? e->depth - 1 : 0, __ATOMIC_RELAXED );
    __atomic_add_fetch( &e->gen, 1, __ATOMIC_RELEASE );
    tok->slot = NULL;
}

/* Any thread: a consistent copy of an active entry, or 0. */
static inline int ios_wait_read( const struct ios_wait_entry *e, struct ios_wait_info *out,
                                 unsigned int *depth )
{
    int tries;
    for (tries = 0; tries < 4; tries++)
    {
        unsigned int g0 = __atomic_load_n( &e->gen, __ATOMIC_ACQUIRE ), g1, d;
        if (g0 & 1) continue;
        d = __atomic_load_n( &e->depth, __ATOMIC_RELAXED );
        ios_wait_copy_out( e, out );
        __atomic_thread_fence( __ATOMIC_ACQUIRE );
        g1 = __atomic_load_n( &e->gen, __ATOMIC_RELAXED );
        if (g0 != g1) continue;
        if (!d) return 0;
        if (depth) *depth = d;
        return 1;
    }
    return 0;
}

/* Server side, before the wake-up reply is written. */
static inline void ios_wait_note_wake( struct ios_wait_entry *table, unsigned int tid,
                                       uint64_t cookie, int status, unsigned int waker,
                                       unsigned int waker_pid, uint64_t now_ns )
{
    struct ios_wait_entry *e = &table[ios_wait_slot_index( tid )];
    if (!cookie || __atomic_load_n( &e->wine_tid, __ATOMIC_ACQUIRE ) != tid) return;
    __atomic_store_n( &e->wake_tid, waker, __ATOMIC_RELAXED );
    __atomic_store_n( &e->wake_pid, waker_pid, __ATOMIC_RELAXED );
    __atomic_store_n( &e->wake_status, status, __ATOMIC_RELAXED );
    __atomic_store_n( &e->wake_ns, now_ns, __ATOMIC_RELAXED );
    __atomic_store_n( &e->wake_cookie, cookie, __ATOMIC_RELEASE );
}

/* Owner side, after the wait: who woke THIS wait, if the server recorded it. */
static inline int ios_wait_take_wake( const struct ios_wait_entry *e, uint64_t cookie, uint64_t t0_ns,
                                      unsigned int *waker, unsigned int *waker_pid, int *status,
                                      uint64_t *wake_ns )
{
    if (!e || !cookie || __atomic_load_n( &e->wake_cookie, __ATOMIC_ACQUIRE ) != cookie) return 0;
    *wake_ns = __atomic_load_n( &e->wake_ns, __ATOMIC_RELAXED );
    if (*wake_ns < t0_ns) return 0;
    *waker  = __atomic_load_n( &e->wake_tid, __ATOMIC_RELAXED );
    *waker_pid = __atomic_load_n( &e->wake_pid, __ATOMIC_RELAXED );
    *status = __atomic_load_n( &e->wake_status, __ATOMIC_RELAXED );
    return 1;
}

/* Stall snapshot -> server: which Wine threads to resolve. gen is bumped
 * after the ids are written; the server copies them and re-checks gen. */
#define IOS_WAIT_PROBE_MAX 16
struct ios_wait_probe_req
{
    volatile unsigned int gen;
    volatile unsigned int count;
    volatile unsigned int tids[IOS_WAIT_PROBE_MAX];
    volatile uint64_t     posted_ns;
};

#endif /* MADEIRA_WAIT_REGISTRY_IOS_H */
