/* MADEIRA ml2120: host model of fastsync's multi-object WaitAny fast path
 * (MADEIRA_FASTSYNC_MULTI), run by check-fastsync-multi.py.
 *
 * The code under test is NOT copied here: check-fastsync-multi.py cuts
 * madeira_multi_pass/take/earlier/attempt/core and madeira_cell_alive out of
 * wine/dlls/ntdll/unix/sync.c into multi_core.inc, and the cell layout, the
 * park/wake primitives and madeira_cell_kick_multi() come from the shipping
 * header wine/include/wine/madeira_fastsync.h.  The setters below follow the
 * shipping order (state RMW, then the futex wake, then the kick) of
 * madeira_fast_event_op / madeira_fast_sem_release and the server hooks.
 *
 * Checks (exit code = first failing check):
 *   10 ORDER.  A thread that sets e0 and then e1 is answered e0 first by a
 *      waiter on {e0, e1}, with and without the waiter parked.
 *   20 CONSERVATION.  Semaphores and auto-reset events under many producers
 *      and multi waiters: released == taken + left, per cell, exactly.
 *   30 NO LOST WAKEUP.  Waiters park with a budget far above the bound; once
 *      the producers stop, a manual-reset quit event at index 0 must release
 *      every waiter within the bound.  31 is the control: the same run with
 *      the kick suppressed must stall, or check 30 proves nothing.
 *   40 GENERATION.  A cell freed under a parked waiter makes it return BAD
 *      promptly, and a token in the recycled cell is never taken.
 *   50 MANUAL RESET.  A manual-reset event releases every waiter and stays set.
 *   60 MIXED.  One auto-reset token, a single-object fast waiter parked on the
 *      cell futex and a multi waiter parked on the epoch: exactly one wins.
 *   70 BOUNDS.  The herd limit and the one-look poll never park.
 */
#define _GNU_SOURCE
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include "wine/madeira_fastsync.h"

unsigned int madeira_multi_epoch;
struct madeira_sync_cell madeira_sync_cells[MADEIRA_SYNC_CELLS];

static unsigned long long madeira_now_ns(void)
{
    struct timespec ts;
    clock_gettime( CLOCK_MONOTONIC, &ts );
    return (unsigned long long)ts.tv_sec * 1000000000ull + ts.tv_nsec;
}

/* the control for check 30: suppress the kick of every setter below */
static int no_kick;

#include "multi_core.inc"

/* ---------------------------------------------------------------- the cells */

static unsigned int next_gen = 1;

static int cell_new( int idx, unsigned int kind, int manual, unsigned int smax, int state )
{
    struct madeira_sync_cell *c = &madeira_sync_cells[idx];
    c->manual = !!manual; c->kind = kind; c->smax = smax;
    c->srv_waiters = 0; c->waiters = 0; c->mwaiters = 0;
    __atomic_store_n( &c->sg, MADEIRA_SG( next_gen, state ), __ATOMIC_SEQ_CST );
    next_gen++;
    return idx;
}

static unsigned int cell_gen( int idx )
{
    return MADEIRA_SG_GEN( __atomic_load_n( &madeira_sync_cells[idx].sg, __ATOMIC_SEQ_CST ) );
}

static void after_write( struct madeira_sync_cell *c, int all )
{
    if (__atomic_load_n( &c->waiters, __ATOMIC_SEQ_CST )) madeira_fast_wake( madeira_cell_futex( c ), all );
    if (!no_kick) madeira_cell_kick_multi( c );
}

/* madeira_fast_event_op( set ) */
static void ev_set( int idx )
{
    struct madeira_sync_cell *c = &madeira_sync_cells[idx];
    for (;;)
    {
        uint64_t sg = __atomic_load_n( &c->sg, __ATOMIC_SEQ_CST );
        int st = MADEIRA_SG_STATE( sg );
        if (st == MADEIRA_CELL_SET) break;
        if (st != MADEIRA_CELL_RESET) return;
        if (__atomic_compare_exchange_n( &c->sg, &sg, MADEIRA_SG( MADEIRA_SG_GEN( sg ), MADEIRA_CELL_SET ), 0,
                                         __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST )) break;
    }
    after_write( c, c->manual );
}

/* madeira_fast_sem_release; returns 0 on overflow */
static int sem_release( int idx, int n )
{
    struct madeira_sync_cell *c = &madeira_sync_cells[idx];
    for (;;)
    {
        uint64_t sg = __atomic_load_n( &c->sg, __ATOMIC_SEQ_CST );
        int st = MADEIRA_SG_STATE( sg );
        if (st < 0 || (unsigned int)st + n > c->smax) return 0;
        if (__atomic_compare_exchange_n( &c->sg, &sg, MADEIRA_SG( MADEIRA_SG_GEN( sg ), st + n ), 0,
                                         __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST )) break;
    }
    after_write( c, n > 1 );
    return 1;
}

/* server free: one store retires the generation, then the wakes */
static void cell_free( int idx )
{
    struct madeira_sync_cell *c = &madeira_sync_cells[idx];
    __atomic_store_n( &c->sg, MADEIRA_SG( next_gen, MADEIRA_CELL_DISABLED ), __ATOMIC_SEQ_CST );
    next_gen++;
    if (__atomic_load_n( &c->waiters, __ATOMIC_SEQ_CST )) madeira_fast_wake( madeira_cell_futex( c ), 1 );
    madeira_cell_kick_multi( c );
}

struct waitset
{
    int n;
    struct madeira_sync_cell *cells[8];
    unsigned int gens[8], manuals[8], kinds[8];
};

static void ws_init( struct waitset *w, int n, const int *idx )
{
    int i;
    w->n = n;
    for (i = 0; i < n; i++)
    {
        struct madeira_sync_cell *c = &madeira_sync_cells[idx[i]];
        w->cells[i] = c; w->gens[i] = cell_gen( idx[i] );
        w->manuals[i] = c->manual; w->kinds[i] = c->kind;
    }
}

/* BUSY (too many re-looks) sends a real caller to the server; here it looks again */
static long busy_count;
static int ws_wait( struct waitset *w, unsigned long long budget_ns, int *phase, int *inv )
{
    int k;
    *inv = 0;
    while ((k = madeira_multi_core( w->cells, w->gens, w->manuals, w->kinds, w->n, 1, budget_ns, phase ))
           == MADEIRA_MULTI_BUSY)
        __atomic_add_fetch( &busy_count, 1, __ATOMIC_RELAXED );
    return k;
}

static int fail( int code, const char *what )
{
    printf( "FAIL %d: %s\n", code, what );
    exit( code );
}

/* ---------------------------------------------------------------- 10 ORDER */

static atomic_int ord_ready, ord_round, ord_done;
static int ord_e0, ord_e1;
static long ord_viol, ord_parked;

static void *ord_waiter( void *arg )
{
    int rounds = (int)(intptr_t)arg, r;
    int idx[2] = { ord_e0, ord_e1 };
    struct waitset w;

    ws_init( &w, 2, idx );
    for (r = 0; r < rounds; r++)
    {
        int phase, inv, k, k2;

        atomic_store( &ord_ready, r + 1 );
        do k = ws_wait( &w, 50000000ull, &phase, &inv ); while (k == MADEIRA_MULTI_NONE);
        if (k < 0) fail( 11, "order: BAD on live cells" );
        if (phase == 2) ord_parked++;
        if (k != 0) ord_viol++;
        do k2 = ws_wait( &w, 50000000ull, &phase, &inv ); while (k2 == MADEIRA_MULTI_NONE);
        if (k2 != 1 - k) fail( 12, "order: the second wait did not return the other event" );
        while (atomic_load( &ord_done ) != r + 1) ;
    }
    return NULL;
}

static void check_order( int rounds )
{
    pthread_t t;
    int r;

    ord_e0 = cell_new( 1, MADEIRA_CELL_KIND_EVENT, 0, 0, MADEIRA_CELL_RESET );
    ord_e1 = cell_new( 2, MADEIRA_CELL_KIND_EVENT, 0, 0, MADEIRA_CELL_RESET );
    pthread_create( &t, NULL, ord_waiter, (void *)(intptr_t)rounds );
    for (r = 0; r < rounds; r++)
    {
        while (atomic_load( &ord_ready ) != r + 1) ;
        /* vary where the sets land: before the pass, in it, after a park */
        if (r % 3 == 1) { volatile int s; for (s = 0; s < (r % 200); s++) ; }
        else if (r % 3 == 2) usleep( 50 );
        ev_set( ord_e0 );
        ev_set( ord_e1 );
        /* wait for the waiter to have taken both before the next round */
        while (MADEIRA_SG_STATE( madeira_sync_cells[ord_e0].sg ) != MADEIRA_CELL_RESET ||
               MADEIRA_SG_STATE( madeira_sync_cells[ord_e1].sg ) != MADEIRA_CELL_RESET) ;
        atomic_store( &ord_done, r + 1 );
    }
    pthread_join( t, NULL );
    printf( "order: %d rounds, %ld parked, %ld answered e1 first\n", rounds, ord_parked, ord_viol );
    if (ord_viol) fail( 10, "order: e1 answered before e0" );
    if (!ord_parked) fail( 13, "order: the park path was never exercised" );
}

/* ---------------------------------------------------------------- 20/30 CONSERVATION, LIVENESS */

#define CS_SEMS 4
#define CS_EVS  3
#define CS_WAITERS 6
#define CS_PRODUCERS 3

static int cs_quit, cs_sem[CS_SEMS], cs_ev[CS_EVS];
static atomic_long cs_released[16], cs_taken[16];
static atomic_int cs_stop, cs_exited;


struct cs_arg { unsigned int seed; int id; };

static void *cs_waiter( void *p )
{
    struct cs_arg *a = p;
    unsigned int seed = a->seed;
    int idx[5], n, i;
    struct waitset w;

    idx[0] = cs_quit;
    n = 1 + 2 + rand_r( &seed ) % 3;
    for (i = 1; i < n; i++)
        idx[i] = (rand_r( &seed ) & 1) ? cs_sem[rand_r( &seed ) % CS_SEMS] : cs_ev[rand_r( &seed ) % CS_EVS];
    ws_init( &w, n, idx );
    for (;;)
    {
        int phase, inv, k = ws_wait( &w, 20000000000ull, &phase, &inv );   /* 20 s: far past the bound */
        if (k == MADEIRA_MULTI_NONE) continue;
        if (k < 0) fail( 21, "conservation: BAD on live cells" );
        if (k == 0) break;
        atomic_fetch_add( &cs_taken[idx[k]], 1 );
    }
    atomic_fetch_add( &cs_exited, 1 );
    return NULL;
}

static void *cs_producer( void *p )
{
    struct cs_arg *a = p;
    unsigned int seed = a->seed;

    while (!atomic_load( &cs_stop ))
    {
        if (rand_r( &seed ) & 1)
        {
            int s = cs_sem[rand_r( &seed ) % CS_SEMS], n = 1 + rand_r( &seed ) % 3;
            if (sem_release( s, n )) atomic_fetch_add( &cs_released[s], n );
        }
        else
        {
            int e = cs_ev[rand_r( &seed ) % CS_EVS];
            /* an auto-reset event carries at most one token: count a set only
             * when it made the transition, as the ledger must */
            struct madeira_sync_cell *c = &madeira_sync_cells[e];
            uint64_t sg = __atomic_load_n( &c->sg, __ATOMIC_SEQ_CST );
            if (MADEIRA_SG_STATE( sg ) == MADEIRA_CELL_RESET &&
                __atomic_compare_exchange_n( &c->sg, &sg, MADEIRA_SG( MADEIRA_SG_GEN( sg ), MADEIRA_CELL_SET ), 0,
                                             __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST ))
            {
                atomic_fetch_add( &cs_released[e], 1 );
                after_write( c, 0 );
            }
        }
        if (!(rand_r( &seed ) % 64)) usleep( rand_r( &seed ) % 300 );
    }
    return NULL;
}

/* returns 1 when every waiter left within bound_ms of the quit */
static int cs_run( int secs, int kick, unsigned int bound_ms )
{
    pthread_t w[CS_WAITERS], p[CS_PRODUCERS];
    struct cs_arg wa[CS_WAITERS], pa[CS_PRODUCERS];
    unsigned long long t0;
    int i, ok;

    no_kick = !kick;
    for (i = 0; i < 16; i++) { atomic_store( &cs_released[i], 0 ); atomic_store( &cs_taken[i], 0 ); }
    atomic_store( &cs_stop, 0 ); atomic_store( &cs_exited, 0 );
    madeira_multi_parked = 0;
    cs_quit = cell_new( 3, MADEIRA_CELL_KIND_EVENT, 1, 0, MADEIRA_CELL_RESET );
    for (i = 0; i < CS_SEMS; i++) cs_sem[i] = cell_new( 4 + i, MADEIRA_CELL_KIND_SEM, 0, 1000, 0 );
    for (i = 0; i < CS_EVS; i++) cs_ev[i] = cell_new( 8 + i, MADEIRA_CELL_KIND_EVENT, 0, 0, MADEIRA_CELL_RESET );
    for (i = 0; i < CS_WAITERS; i++) { wa[i].seed = 1234 + i * 77 + kick; wa[i].id = i; pthread_create( &w[i], NULL, cs_waiter, &wa[i] ); }
    for (i = 0; i < CS_PRODUCERS; i++) { pa[i].seed = 999 + i * 13; pa[i].id = i; pthread_create( &p[i], NULL, cs_producer, &pa[i] ); }
    sleep( secs );
    atomic_store( &cs_stop, 1 );
    for (i = 0; i < CS_PRODUCERS; i++) pthread_join( p[i], NULL );
    usleep( 200000 );   /* let the waiters drain and park */
    ev_set( cs_quit );
    t0 = madeira_now_ns();
    while (atomic_load( &cs_exited ) < CS_WAITERS && madeira_now_ns() - t0 < bound_ms * 1000000ull) usleep( 1000 );
    ok = atomic_load( &cs_exited ) == CS_WAITERS;
    if (!ok)
    {
        /* release the stragglers so the threads can be joined */
        no_kick = 0;
        madeira_cell_kick_multi( &madeira_sync_cells[cs_quit] );
        while (atomic_load( &cs_exited ) < CS_WAITERS) { __atomic_add_fetch( &madeira_multi_epoch, 1, __ATOMIC_SEQ_CST );
                                                          madeira_fast_wake( (const int *)&madeira_multi_epoch, 1 ); usleep( 1000 ); }
    }
    for (i = 0; i < CS_WAITERS; i++) pthread_join( w[i], NULL );
    no_kick = 0;
    return ok;
}

static void check_conservation_and_liveness( int secs )
{
    int i, ok;
    long rel = 0, tak = 0;

    ok = cs_run( secs, 1, 1000 );
    for (i = 0; i < CS_SEMS; i++)
    {
        int s = cs_sem[i], left = MADEIRA_SG_STATE( madeira_sync_cells[s].sg );
        rel += cs_released[s]; tak += cs_taken[s];
        if (cs_released[s] != cs_taken[s] + left) fail( 20, "conservation: a semaphore's tokens do not add up" );
    }
    for (i = 0; i < CS_EVS; i++)
    {
        int e = cs_ev[i], left = MADEIRA_SG_STATE( madeira_sync_cells[e].sg ) == MADEIRA_CELL_SET;
        rel += cs_released[e]; tak += cs_taken[e];
        if (cs_released[e] != cs_taken[e] + left) fail( 22, "conservation: an auto-reset event's tokens do not add up" );
    }
    for (i = 0; i < 16; i++)
        if (madeira_sync_cells[i].mwaiters) fail( 23, "conservation: mwaiters left registered" );
    if (madeira_multi_parked) fail( 24, "conservation: the parked count did not return to 0" );
    printf( "conservation: %ld tokens released, %ld taken (%ld calls re-looked past the try limit); "
            "liveness: all waiters left %s\n", rel, tak, busy_count, ok ? "within 1 s" : "LATE" );
    if (!ok) fail( 30, "liveness: a parked waiter missed the quit event" );
    if (!tak) fail( 25, "conservation: nothing was taken" );

    /* the control: without the kick, parked waiters must sleep through the quit */
    ok = cs_run( 1, 0, 1000 );
    printf( "control without the kick: %s\n", ok ? "every waiter still left (BAD control)" : "stalled, as it must" );
    if (ok) fail( 31, "control: suppressing the kick did not stall, so check 30 proves nothing" );
}

/* ---------------------------------------------------------------- 40 GENERATION */

static atomic_int gen_res = 99;
static struct waitset gen_ws;

static void *gen_waiter( void *p )
{
    int phase, inv;
    atomic_store( &gen_res, ws_wait( &gen_ws, 10000000000ull, &phase, &inv ) );
    return NULL;
}

static void check_generation(void)
{
    int idx[2], phase, inv, k;
    pthread_t t;
    unsigned long long t0;

    idx[0] = cell_new( 20, MADEIRA_CELL_KIND_EVENT, 0, 0, MADEIRA_CELL_RESET );
    idx[1] = cell_new( 21, MADEIRA_CELL_KIND_SEM, 0, 10, 0 );
    ws_init( &gen_ws, 2, idx );
    pthread_create( &t, NULL, gen_waiter, NULL );
    usleep( 20000 );
    t0 = madeira_now_ns();
    cell_free( 21 );
    pthread_join( t, NULL );
    if (atomic_load( &gen_res ) != MADEIRA_MULTI_BAD) fail( 40, "generation: a freed cell did not end the wait with BAD" );
    if (madeira_now_ns() - t0 > 500000000ull) fail( 41, "generation: the free did not wake the parked waiter" );
    /* recycle it with tokens: the old generation must never take one */
    cell_new( 21, MADEIRA_CELL_KIND_SEM, 0, 10, 5 );
    k = ws_wait( &gen_ws, 1000000ull, &phase, &inv );
    if (k != MADEIRA_MULTI_BAD || MADEIRA_SG_STATE( madeira_sync_cells[21].sg ) != 5)
        fail( 42, "generation: a token was taken out of a recycled cell" );
    printf( "generation: freed cell ends the park with BAD, recycled tokens untouched\n" );
}

/* ---------------------------------------------------------------- 50 MANUAL RESET */

static atomic_int man_hits;
static struct waitset man_ws;

static void *man_waiter( void *p )
{
    int phase, inv, k;
    do k = ws_wait( &man_ws, 5000000000ull, &phase, &inv ); while (k == MADEIRA_MULTI_NONE);
    if (k == 1) atomic_fetch_add( &man_hits, 1 );
    return NULL;
}

static void check_manual(void)
{
    pthread_t t[5];
    int idx[2], i;

    idx[0] = cell_new( 30, MADEIRA_CELL_KIND_EVENT, 0, 0, MADEIRA_CELL_RESET );
    idx[1] = cell_new( 31, MADEIRA_CELL_KIND_EVENT, 1, 0, MADEIRA_CELL_RESET );
    ws_init( &man_ws, 2, idx );
    for (i = 0; i < 5; i++) pthread_create( &t[i], NULL, man_waiter, NULL );
    usleep( 20000 );
    ev_set( 31 );
    for (i = 0; i < 5; i++) pthread_join( t[i], NULL );
    if (atomic_load( &man_hits ) != 5 || MADEIRA_SG_STATE( madeira_sync_cells[31].sg ) != MADEIRA_CELL_SET)
        fail( 50, "manual reset: not every waiter was released, or the event was consumed" );
    printf( "manual reset: 5 waiters released, event still set\n" );
}

/* ---------------------------------------------------------------- 60 MIXED */

static atomic_int mix_wins;
static struct waitset mix_ws;
static int mix_cell;

static void *mix_multi( void *p )
{
    int phase, inv, k = ws_wait( &mix_ws, 30000000ull, &phase, &inv );
    if (k == 0) atomic_fetch_add( &mix_wins, 1 );
    return NULL;
}

/* a single-object fast waiter: madeira_fast_wait's park loop in miniature */
static void *mix_single( void *p )
{
    struct madeira_sync_cell *c = &madeira_sync_cells[mix_cell];
    unsigned long long t0 = madeira_now_ns();
    while (madeira_now_ns() - t0 < 30000000ull)
    {
        uint64_t sg = __atomic_load_n( &c->sg, __ATOMIC_SEQ_CST );
        if (MADEIRA_SG_STATE( sg ) == MADEIRA_CELL_SET &&
            __atomic_compare_exchange_n( &c->sg, &sg, MADEIRA_SG( MADEIRA_SG_GEN( sg ), MADEIRA_CELL_RESET ), 0,
                                         __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST ))
        { atomic_fetch_add( &mix_wins, 1 ); return NULL; }
        __atomic_add_fetch( &c->waiters, 1, __ATOMIC_SEQ_CST );
        if (MADEIRA_SG_STATE( __atomic_load_n( &c->sg, __ATOMIC_SEQ_CST ) ) == MADEIRA_CELL_RESET)
            madeira_fast_park( madeira_cell_futex( c ), MADEIRA_CELL_RESET, 5000000ull );
        __atomic_sub_fetch( &c->waiters, 1, __ATOMIC_SEQ_CST );
    }
    return NULL;
}

static void check_mixed( int rounds )
{
    int r, idx[2];
    for (r = 0; r < rounds; r++)
    {
        pthread_t a, b;
        atomic_store( &mix_wins, 0 );
        mix_cell = cell_new( 40, MADEIRA_CELL_KIND_EVENT, 0, 0, MADEIRA_CELL_RESET );
        idx[0] = mix_cell;
        idx[1] = cell_new( 41, MADEIRA_CELL_KIND_EVENT, 0, 0, MADEIRA_CELL_RESET );
        ws_init( &mix_ws, 2, idx );
        pthread_create( &a, NULL, mix_multi, NULL );
        pthread_create( &b, NULL, mix_single, NULL );
        usleep( r & 1 ? 2000 : 50 );
        ev_set( mix_cell );
        pthread_join( a, NULL );
        pthread_join( b, NULL );
        if (atomic_load( &mix_wins ) != 1) fail( 60, "mixed: one auto-reset token released != 1 waiter" );
    }
    printf( "mixed: %d rounds, one token -> exactly one of a single and a multi waiter\n", rounds );
}

/* ---------------------------------------------------------------- 70 BOUNDS */

static void check_bounds(void)
{
    struct waitset w;
    int idx[2], phase, inv, k;
    unsigned long long t0;

    idx[0] = cell_new( 50, MADEIRA_CELL_KIND_EVENT, 0, 0, MADEIRA_CELL_RESET );
    idx[1] = cell_new( 51, MADEIRA_CELL_KIND_SEM, 0, 3, 0 );
    ws_init( &w, 2, idx );
    /* one look: nothing there, no park */
    t0 = madeira_now_ns();
    k = madeira_multi_core( w.cells, w.gens, w.manuals, w.kinds, w.n, 0, 0, &phase );
    if (k != MADEIRA_MULTI_NONE || phase != 0 || madeira_now_ns() - t0 > 50000000ull)
        fail( 70, "bounds: a poll did more than one look" );
    sem_release( 51, 2 );
    k = madeira_multi_core( w.cells, w.gens, w.manuals, w.kinds, w.n, 0, 0, &phase );
    if (k != 1 || MADEIRA_SG_STATE( madeira_sync_cells[51].sg ) != 1) fail( 71, "bounds: a poll missed a token" );
    k = madeira_multi_core( w.cells, w.gens, w.manuals, w.kinds, w.n, 0, 0, &phase );   /* the last token */
    if (k != 1 || MADEIRA_SG_STATE( madeira_sync_cells[51].sg ) != 0) fail( 71, "bounds: a poll missed a token" );
    /* the herd limit: with the limit reached a waiter goes to the server at once */
    madeira_multi_parked = MADEIRA_MULTI_MAX_PARKED;
    t0 = madeira_now_ns();
    k = madeira_multi_core( w.cells, w.gens, w.manuals, w.kinds, w.n, 1, 1000000000ull, &phase );
    if (k != MADEIRA_MULTI_NONE || madeira_now_ns() - t0 > 100000000ull || phase == 2)
        fail( 72, "bounds: a waiter parked past the herd limit" );
    madeira_multi_parked = 0;
    /* the budget ends a park */
    t0 = madeira_now_ns();
    k = madeira_multi_core( w.cells, w.gens, w.manuals, w.kinds, w.n, 1, 3000000ull, &phase );
    if (k != MADEIRA_MULTI_NONE || phase != 2 || madeira_now_ns() - t0 > 200000000ull)
        fail( 73, "bounds: the budget did not end the park" );
    if (madeira_sync_cells[50].mwaiters || madeira_sync_cells[51].mwaiters)
        fail( 74, "bounds: a timed-out park left mwaiters registered" );
    printf( "bounds: one-look poll, herd limit and park budget hold\n" );
}

int main( int argc, char **argv )
{
    int secs = argc > 1 ? atoi( argv[1] ) : 3;
    check_order( 20000 );
    check_conservation_and_liveness( secs );
    check_generation();
    check_manual();
    check_mixed( 100 );
    check_bounds();
    printf( "fastsync-multirace: PASS\n" );
    return 0;
}
