/*
 * Client-side registry read cache and registry census (madeira).
 *
 *   env.MADEIRA_REG_CACHE  = RDR2.exe;Other.exe | 1 (every process) | 0
 *       answer repeated opens and value reads from the client (regcache_ios.h
 *       says how it stays exactly as fresh as the server).
 *   env.MADEIRA_REG_CACHE_HOT = n (default 2): opens of one key before the
 *       cache answers them.
 *   env.MADEIRA_REG_CENSUS = RDR2.exe | 1 | 0
 *       every 30 s, per process: the keys opened and the values read most,
 *       with counts and failures ([reg-census]), and the cache's counters
 *       ([reg-cache]).
 *
 * The hooks are in ntdll/unix/registry.c (every key function), server_ios.c
 * (NtClose, NtDuplicateObject, NtCompareObjects), file.c (NtQueryObject,
 * NtSetInformationObject) and security.c (NtQuerySecurityObject,
 * NtSetSecurityObject). The registry writes there bracket their requests with
 * ios_regcache_write_begin / _end whatever the switches say.
 */

#if 0
#pragma makedep unix
#endif

#include "config.h"

#include <pthread.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <time.h>

#include "ntstatus.h"
#define WIN32_NO_STATUS
#include "windef.h"
#include "winternl.h"
#include "unix_private.h"

/* registry.c: the requests themselves, without the cache */
extern NTSTATUS ios_reg_srv_open( HANDLE *key, HANDLE root, const WCHAR *name, USHORT len,
                                  ACCESS_MASK access, ULONG attributes );
extern NTSTATUS ios_reg_srv_query( HANDLE key, const WCHAR *name, USHORT len, ULONG *type,
                                   ULONG *total, void *data, ULONG max );
extern NTSTATUS ios_reg_srv_keyname( HANDLE key, WCHAR *name, ULONG size, ULONG *len );

static NTSTATUS rc_srv_open( HANDLE *key, HANDLE root, const WCHAR *name, USHORT len,
                             ACCESS_MASK access, ULONG attributes )
{
    return ios_reg_srv_open( key, root, name, len, access, attributes );
}

static NTSTATUS rc_srv_query( HANDLE key, const WCHAR *name, USHORT len, ULONG *type,
                              ULONG *total, void *data, ULONG max )
{
    return ios_reg_srv_query( key, name, len, type, total, data, max );
}

static NTSTATUS rc_srv_close( HANDLE key )
{
    return NtClose( key );
}

/* the cache and census locks are held across server requests: hold them the way
 * Wine holds fd_cache_mutex (signals blocked; a killed thread leaves first) */
static void rc_enter_section( pthread_mutex_t *mutex, sigset_t *sigset )
{
    server_enter_uninterrupted_section( mutex, sigset );
}

static void rc_leave_section( pthread_mutex_t *mutex, sigset_t *sigset )
{
    server_leave_uninterrupted_section( mutex, sigset );
}

#include "regcache_ios.h"

#ifndef ARRAY_SIZE
#define ARRAY_SIZE(x) (sizeof(x) / sizeof((x)[0]))
#endif

/* ---- switches and processes ---- */

int ios_regcache_on = -1;       /* -1 not read yet, 0 off, 1 some process may use it */
static const char *rc_cache_list, *rc_census_list;
static unsigned int rc_hot = 2;

#define RC_PATH 200
#define RC_CEN 256
#define RC_HPATH 64

struct rc_cen_item
{
    unsigned int hash, count, fails;
    unsigned char kind, used;
    char path[RC_PATH];
    char value[64];
};

struct rc_census
{
    pthread_mutex_t lock;       /* recursive; taken before a process's cache lock, never after;
                                 * through rc_enter_section / rc_leave_section */
    struct rc_cen_item items[RC_CEN];
    unsigned int used;
    unsigned long long dropped, n[4], fails[4];
    struct { HANDLE h; char path[RC_PATH]; } hpath[RC_HPATH];
    unsigned int hclock;
    char labels[RC_ENTRIES][RC_PATH];
    unsigned int label_epoch[RC_ENTRIES];
};

enum { RC_K_OPEN, RC_K_VALUE, RC_K_CREATE, RC_K_OTHER };
static const char * const rc_kind_names[] = { "open ", "value", "create", "other" };

struct rc_rec
{
    void *peb, *params;
    ULONG_PTR pid;
    int used;
    struct rc_proc *proc;       /* cache on */
    struct rc_census *cen;      /* census on */
    char exe[48];
    unsigned int ops;
    unsigned long long last_report;
    long long gen0;
    struct rc_stats st0;
};

static struct rc_rec rc_recs[64];
static unsigned int rc_rec_clock;
static pthread_mutex_t rc_recs_lock = PTHREAD_MUTEX_INITIALIZER;
static __thread struct rc_rec *rc_tls_rec;
static __thread struct rc_proc *rc_tls_close_proc;
static __thread sigset_t rc_tls_close_sigset;   /* rc_close's root case, until ios_regcache_close_done */

static unsigned long long rc_now_ms( void )
{
    struct timespec ts;
    clock_gettime( CLOCK_MONOTONIC, &ts );
    return (unsigned long long)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

static const char *rc_env_list( const char *name )
{
    const char *v = getenv( name );
    if (!v || !*v || (v[0] == '0' && !v[1])) return NULL;
    return v;
}

int ios_regcache_init( void )
{
    const char *hot = getenv( "MADEIRA_REG_CACHE_HOT" );
    rc_cache_list = rc_env_list( "MADEIRA_REG_CACHE" );
    rc_census_list = rc_env_list( "MADEIRA_REG_CENSUS" );
    if (hot && *hot) rc_hot = (unsigned int)strtoul( hot, NULL, 10 );
    __atomic_store_n( &ios_regcache_on, (rc_cache_list || rc_census_list) ? 1 : 0, __ATOMIC_RELEASE );
    if (rc_cache_list || rc_census_list)
        fprintf( stderr, "[reg-cache] MADEIRA_REG_CACHE=%s MADEIRA_REG_CENSUS=%s hot=%u (client-side registry "
                 "read cache, invalidated by every registry write of every process)\n",
                 rc_cache_list ? rc_cache_list : "0", rc_census_list ? rc_census_list : "0", rc_hot );
    return ios_regcache_on;
}

static void rc_exe_name( PEB *peb, char *out, size_t size )
{
    RTL_USER_PROCESS_PARAMETERS *pp = peb->ProcessParameters;
    size_t len, base = 0, k, n = 0;
    const WCHAR *path;

    out[0] = 0;
    if (!pp || !pp->ImagePathName.Buffer) return;
    path = pp->ImagePathName.Buffer;
    len = pp->ImagePathName.Length / sizeof(WCHAR);
    for (k = 0; k < len; k++) if (path[k] == '\\' || path[k] == '/') base = k + 1;
    for (k = base; k < len && n + 1 < size; k++) out[n++] = path[k] < 0x80 ? (char)path[k] : '?';
    out[n] = 0;
}

static int rc_in_list( const char *list, const char *exe )
{
    if (!list) return 0;
    if ((list[0] == '1' && !list[1]) || !strcasecmp( list, "all" )) return 1;
    while (*list)
    {
        const char *end = list;
        while (*end && *end != ';' && *end != ',') end++;
        if ((size_t)(end - list) == strlen( exe ) && exe[0] && !strncasecmp( list, exe, end - list )) return 1;
        list = *end ? end + 1 : end;
    }
    return 0;
}

static struct rc_rec *rc_current( void )
{
    TEB *teb = NtCurrentTeb();
    struct rc_rec *r = rc_tls_rec;
    PEB *peb;
    ULONG_PTR pid;
    unsigned int i;

    if (!teb || !(peb = teb->Peb)) return NULL;
    pid = (ULONG_PTR)teb->ClientId.UniqueProcess;
    if (r && r->peb == peb && r->pid == pid && r->params == peb->ProcessParameters) return r;
    if (!peb->ProcessParameters) return NULL;   /* too early to know the exe */

    pthread_mutex_lock( &rc_recs_lock );
    for (i = 0; i < ARRAY_SIZE(rc_recs); i++)
    {
        r = &rc_recs[i];
        if (r->used && r->peb == peb && r->pid == pid && r->params == peb->ProcessParameters) goto done;
    }
    /* a new process: take a free record, or one with nothing allocated */
    for (i = 0; i < ARRAY_SIZE(rc_recs); i++)
    {
        r = &rc_recs[rc_rec_clock++ % ARRAY_SIZE(rc_recs)];
        if (!r->used || (!r->proc && !r->cen)) break;
    }
    if (i == ARRAY_SIZE(rc_recs))
    {
        r = NULL;   /* every record holds a cache: leave this process uncached */
        goto done;
    }
    memset( r, 0, sizeof(*r) );
    r->used = 1;
    r->peb = peb;
    r->pid = pid;
    r->params = peb->ProcessParameters;
    rc_exe_name( peb, r->exe, sizeof(r->exe) );
    if (rc_in_list( rc_cache_list, r->exe ) && (r->proc = malloc( sizeof(*r->proc) )))
        rc_proc_init( r->proc, 1, rc_hot );
    if (rc_in_list( rc_census_list, r->exe ) && (r->cen = calloc( 1, sizeof(*r->cen) )))
    {
        pthread_mutexattr_t attr;
        pthread_mutexattr_init( &attr );
        pthread_mutexattr_settype( &attr, PTHREAD_MUTEX_RECURSIVE );
        pthread_mutex_init( &r->cen->lock, &attr );
        pthread_mutexattr_destroy( &attr );
    }
    r->last_report = rc_now_ms();
    r->gen0 = __atomic_load_n( &rc_gen, __ATOMIC_RELAXED );
    if (r->proc || r->cen)
        fprintf( stderr, "[reg-cache] pid=%04lx %s: cache %s, census %s\n", (unsigned long)pid, r->exe,
                 r->proc ? "on" : "off", r->cen ? "on" : "off" );
done:
    pthread_mutex_unlock( &rc_recs_lock );
    rc_tls_rec = r;
    return r;
}

/* ---- census ---- */

static void rc_wcs_to_path( char *out, size_t size, const WCHAR *s, size_t chars )
{
    size_t n = strlen( out ), k;
    for (k = 0; k < chars && n + 1 < size; k++) out[n++] = s[k] && s[k] < 0x80 ? (char)s[k] : '?';
    out[n] = 0;
}

static void rc_join( char *out, const char *prefix, const WCHAR *name, USHORT len )
{
    out[0] = 0;
    if (prefix && *prefix)
    {
        snprintf( out, RC_PATH, "%s", prefix );
        if (len && strlen( out ) + 1 < RC_PATH) strcat( out, "\\" );
    }
    rc_wcs_to_path( out, RC_PATH, name, len / sizeof(WCHAR) );
}

static void rc_hpath_set( struct rc_census *c, HANDLE h, const char *path )
{
    unsigned int i;
    for (i = 0; i < RC_HPATH; i++) if (c->hpath[i].h == h) break;
    if (i == RC_HPATH)
    {
        for (i = 0; i < RC_HPATH; i++) if (!c->hpath[i].h) break;
        if (i == RC_HPATH) i = c->hclock++ % RC_HPATH;
    }
    c->hpath[i].h = h;
    snprintf( c->hpath[i].path, RC_PATH, "%s", path );
}

static const char *rc_entry_label( struct rc_rec *r, int ei );

/* a key handle's name, for the census only */
static const char *rc_handle_label( struct rc_rec *r, HANDLE h, char *buf )
{
    struct rc_census *c = r->cen;
    WCHAR name[RC_PATH];
    ULONG len = 0;
    unsigned int i;
    int si;

    if (!h) return "";
    if (r->proc && rc_is_virtual( h ))
    {
        const char *label = NULL;
        sigset_t sigset;
        rc_lock( r->proc, &sigset );
        if ((si = rc_slot_of( r->proc, h )) >= 0) label = rc_entry_label( r, r->proc->slots[si].entry );
        rc_unlock( r->proc, &sigset );
        return label ? label : "<cached handle>";
    }
    for (i = 0; i < RC_HPATH; i++) if (c->hpath[i].h == h) return c->hpath[i].path;
    buf[0] = 0;
    if (!ios_reg_srv_keyname( h, name, sizeof(name), &len ))
        rc_wcs_to_path( buf, RC_PATH, name, len / sizeof(WCHAR) );
    else snprintf( buf, RC_PATH, "<handle %p>", h );
    rc_hpath_set( c, h, buf );
    return buf;
}

/* (proc lock held) */
static const char *rc_entry_label( struct rc_rec *r, int ei )
{
    struct rc_census *c = r->cen;
    struct rc_entry *e = &r->proc->entries[ei];
    char buf[RC_PATH];
    const char *prefix;

    if (c->label_epoch[ei] == e->epoch && c->labels[ei][0]) return c->labels[ei];
    prefix = e->parent >= 0 ? rc_entry_label( r, e->parent ) : rc_handle_label( r, e->root, buf );
    rc_join( buf, prefix, e->name, e->name_len );
    memcpy( c->labels[ei], buf, RC_PATH );
    c->label_epoch[ei] = e->epoch;
    return c->labels[ei];
}

static void rc_census_add( struct rc_census *c, int kind, const char *path, const char *value, int failed )
{
    unsigned int h = 2166136261u, k;

    c->n[kind]++;
    if (failed) c->fails[kind]++;
    h = rc_hash( h, &kind, sizeof(kind) );
    h = rc_hash( h, path, (unsigned int)strlen( path ) );
    if (value) h = rc_hash( h, value, (unsigned int)strlen( value ) );
    for (k = 0; k < RC_CEN; k++)
    {
        struct rc_cen_item *it = &c->items[(h + k) % RC_CEN];
        if (!it->used)
        {
            if (c->used >= RC_CEN * 3 / 4) break;
            it->used = 1;
            it->hash = h;
            it->kind = (unsigned char)kind;
            snprintf( it->path, sizeof(it->path), "%s", path );
            snprintf( it->value, sizeof(it->value), "%s", value ? value : "" );
            c->used++;
        }
        else if (it->hash != h || it->kind != kind || strcmp( it->path, path ) ||
                 strcmp( it->value, value ? value : "" )) continue;
        it->count++;
        if (failed) it->fails++;
        return;
    }
    c->dropped++;
}

static void rc_report( struct rc_rec *r, unsigned long long now )
{
    double secs = (now - r->last_report) / 1000.0;
    long long gen = __atomic_load_n( &rc_gen, __ATOMIC_RELAXED );

    if (r->proc)
    {
        struct rc_stats s = r->proc->st, *o = &r->st0;
        fprintf( stderr, "[reg-cache] pid=%04lx %s %.1f s: opens cached %llu, not-found cached %llu, to the server %llu "
                 "(+%llu not cacheable); value reads cached %llu, to the server %llu, too big %llu (+%llu not cacheable); "
                 "cached closes %llu, materialized %llu, root closes %llu, table full %llu; registry writes %lld "
                 "(all processes)\n", (unsigned long)r->pid, r->exe, secs,
                 s.open_hit - o->open_hit, s.open_neg_hit - o->open_neg_hit, s.open_miss - o->open_miss,
                 s.open_pass - o->open_pass, s.val_hit - o->val_hit, s.val_miss - o->val_miss,
                 s.val_big - o->val_big, s.val_pass - o->val_pass, s.closes - o->closes,
                 s.materialized - o->materialized, s.root_closes - o->root_closes, s.full - o->full,
                 gen - r->gen0 );
        r->st0 = s;
    }
    if (r->cen)
    {
        struct rc_census *c = r->cen;
        int order[16], n = 0, i, j;
        unsigned int k;
        sigset_t csig;

        rc_enter_section( &c->lock, &csig );
        for (k = 0; k < RC_CEN; k++)
        {
            if (!c->items[k].used) continue;
            for (i = n; i > 0 && c->items[order[i - 1]].count < c->items[k].count; i--)
                if (i < 16) order[i] = order[i - 1];
            if (i < 16)
            {
                order[i] = (int)k;
                if (n < 16) n++;
            }
        }
        fprintf( stderr, "[reg-census] pid=%04lx %s %.1f s: opens %llu (%llu failed), value reads %llu (%llu not found), "
                 "creates %llu, other key calls %llu; %u distinct, %llu uncounted; top %d:\n",
                 (unsigned long)r->pid, r->exe, secs, c->n[RC_K_OPEN], c->fails[RC_K_OPEN], c->n[RC_K_VALUE],
                 c->fails[RC_K_VALUE], c->n[RC_K_CREATE], c->n[RC_K_OTHER], c->used, c->dropped, n );
        for (j = 0; j < n; j++)
        {
            struct rc_cen_item *it = &c->items[order[j]];
            fprintf( stderr, "[reg-census]   %7u %s %s%s%s%s (%u failed)\n", it->count, rc_kind_names[it->kind],
                     it->path, it->value[0] ? " : \"" : "", it->value, it->value[0] ? "\"" : "", it->fails );
        }
        memset( c->items, 0, sizeof(c->items) );
        memset( c->n, 0, sizeof(c->n) );
        memset( c->fails, 0, sizeof(c->fails) );
        c->used = 0;
        c->dropped = 0;
        rc_leave_section( &c->lock, &csig );
    }
    r->last_report = now;
    r->gen0 = gen;
}

static void rc_tick( struct rc_rec *r )
{
    unsigned long long now;
    if (++r->ops & 255) return;
    now = rc_now_ms();
    if (now - r->last_report < 30000) return;
    pthread_mutex_lock( &rc_recs_lock );
    if (now - r->last_report >= 30000) rc_report( r, now );
    pthread_mutex_unlock( &rc_recs_lock );
}

/* ---- hooks ---- */

void ios_regcache_write_begin( void )
{
    rc_write_begin();
}

void ios_regcache_write_end( int changed )
{
    rc_write_end( changed );
}

/* NtOpenKeyEx: 1 when answered here (*status, *key) */
int ios_regcache_open( HANDLE *key, HANDLE root, const UNICODE_STRING *name, ACCESS_MASK access,
                       ULONG attributes, ULONG options, NTSTATUS *status )
{
    struct rc_rec *r = rc_current();
    char rootbuf[RC_PATH], path[RC_PATH];
    int handled = 0;
    sigset_t csig;

    if (!r || (!r->proc && !r->cen)) return 0;
    if (r->cen)
    {
        rc_enter_section( &r->cen->lock, &csig );
        rc_join( path, rc_handle_label( r, root, rootbuf ), name->Buffer, name->Length );
        rc_leave_section( &r->cen->lock, &csig );
    }
    if (r->proc)
        handled = rc_open( r->proc, key, root, name->Buffer, name->Length, access, attributes, options, status );
    if (!handled)
    {
        HANDLE real = root;
        if (r->proc && (*status = rc_real( r->proc, &real ))) *key = 0;
        else *status = ios_reg_srv_open( key, real, name->Buffer, name->Length, access, attributes );
    }
    if (r->cen)
    {
        rc_enter_section( &r->cen->lock, &csig );
        rc_census_add( r->cen, RC_K_OPEN, path, NULL, *status != STATUS_SUCCESS );
        if (!*status && *key && !rc_is_virtual( *key )) rc_hpath_set( r->cen, *key, path );
        rc_leave_section( &r->cen->lock, &csig );
    }
    rc_tick( r );
    return 1;
}

/* NtCreateKey, for the census */
void ios_regcache_note_create( HANDLE root, const UNICODE_STRING *name, NTSTATUS status, HANDLE key )
{
    struct rc_rec *r;
    char rootbuf[RC_PATH], path[RC_PATH];
    sigset_t csig;

    if (ios_regcache_on <= 0 || !(r = rc_current()) || !r->cen) return;
    rc_enter_section( &r->cen->lock, &csig );
    rc_join( path, rc_handle_label( r, root, rootbuf ), name ? name->Buffer : NULL, name ? name->Length : 0 );
    rc_census_add( r->cen, RC_K_CREATE, path, NULL, status != STATUS_SUCCESS );
    if (!status && key) rc_hpath_set( r->cen, key, path );
    rc_leave_section( &r->cen->lock, &csig );
    rc_tick( r );
}

/* NtQueryValueKey: 1 when answered here; else *handle is the handle to ask the server with */
int ios_regcache_query( HANDLE *handle, const UNICODE_STRING *name, ULONG *type, ULONG *total,
                        void *data, ULONG max, NTSTATUS *status )
{
    struct rc_rec *r = rc_current();
    HANDLE orig = *handle;
    char buf[RC_PATH], path[RC_PATH], value[64];
    int served = 0;
    sigset_t csig;

    if (!r || (!r->proc && !r->cen)) return 0;
    if (r->proc) served = rc_query( r->proc, handle, name->Buffer, name->Length, type, total, data, max, status );
    if (r->cen)
    {
        rc_enter_section( &r->cen->lock, &csig );
        snprintf( path, sizeof(path), "%s", rc_handle_label( r, orig, buf ) );
        value[0] = 0;
        rc_wcs_to_path( value, sizeof(value), name->Buffer, name->Length / sizeof(WCHAR) );
        /* a read the server answers is counted by ios_regcache_query_done */
        if (served) rc_census_add( r->cen, RC_K_VALUE, path, value, *status != STATUS_SUCCESS );
        rc_leave_section( &r->cen->lock, &csig );
    }
    rc_tick( r );
    return served;
}

/* NtQueryValueKey's own request finished (census) */
void ios_regcache_query_done( HANDLE handle, const UNICODE_STRING *name, NTSTATUS status )
{
    struct rc_rec *r;
    char buf[RC_PATH], path[RC_PATH], value[64];
    sigset_t csig;

    if (ios_regcache_on <= 0 || !(r = rc_current()) || !r->cen) return;
    rc_enter_section( &r->cen->lock, &csig );
    snprintf( path, sizeof(path), "%s", rc_handle_label( r, handle, buf ) );
    value[0] = 0;
    rc_wcs_to_path( value, sizeof(value), name->Buffer, name->Length / sizeof(WCHAR) );
    rc_census_add( r->cen, RC_K_VALUE, path, value,
                   status != STATUS_SUCCESS && status != STATUS_BUFFER_OVERFLOW && status != STATUS_BUFFER_TOO_SMALL );
    rc_leave_section( &r->cen->lock, &csig );
}

/* any other use of a handle: a cached one becomes its real handle. `op` names a
 * key function for the census; NULL for the generic object functions. */
NTSTATUS ios_regcache_real( HANDLE *handle, const char *op )
{
    struct rc_rec *r;
    char buf[RC_PATH];
    sigset_t csig;

    if (!op && !rc_is_virtual( *handle )) return STATUS_SUCCESS;   /* the generic object calls */
    if (!(r = rc_current()) || (!r->proc && !r->cen)) return STATUS_SUCCESS;
    if (op && r->cen)
    {
        rc_enter_section( &r->cen->lock, &csig );
        rc_census_add( r->cen, RC_K_OTHER, rc_handle_label( r, *handle, buf ), op, 0 );
        rc_leave_section( &r->cen->lock, &csig );
    }
    if (!r->proc || !rc_is_virtual( *handle )) return STATUS_SUCCESS;
    return rc_real( r->proc, handle );
}

/* NtClose: 1 when `handle` was a cached handle (*ret); 2 when it was the root of
 * cached opens: call ios_regcache_close_done after closing it */
int ios_regcache_close( HANDLE handle, NTSTATUS *ret )
{
    struct rc_rec *r = rc_current();
    int res;

    if (!r) return 0;
    if (r->cen)
    {
        /* without the census lock: this runs inside the cache's own closes, which hold
         * the cache lock, and the census takes its lock before that one. A race with
         * rc_hpath_set can only mislabel a census line. */
        unsigned int i;
        for (i = 0; i < RC_HPATH; i++)
            if (r->cen->hpath[i].h == handle) __atomic_store_n( &r->cen->hpath[i].h, (HANDLE)0, __ATOMIC_RELAXED );
    }
    if (!r->proc) return 0;
    res = rc_close( r->proc, handle, ret, &rc_tls_close_sigset );
    if (res == 2) rc_tls_close_proc = r->proc;
    return res;
}

void ios_regcache_close_done( void )
{
    struct rc_proc *p = rc_tls_close_proc;
    rc_tls_close_proc = NULL;
    if (p) rc_close_done( p, &rc_tls_close_sigset );
}

/* NtDuplicateObject with DUPLICATE_CLOSE_SOURCE on cached handle `handle`,
 * after ios_regcache_real gave its real handle to the server */
void ios_regcache_forget( HANDLE handle )
{
    struct rc_rec *r = rc_current();
    if (r && r->proc) rc_forget( r->proc, handle );
}
