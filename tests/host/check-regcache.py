#!/usr/bin/env python3
"""Registry read cache (build/ntdll-unix/regcache_ios.h, env.MADEIRA_REG_CACHE).

Runs the production cache core against a model wineserver registry with per-process
handle tables (lowest free handle reused first, as the wineserver does), under
ASan/UBSan and then TSan:

  * repeated opens and value reads stop reaching the server once a key is hot;
  * a write by another process is seen by the next read (value set, value deleted,
    key created after a cached "not found", key deleted under a cached handle);
  * a writer and readers in two processes race for thousands of rounds and no read
    returns a value older than the last write that had returned before it began;
  * closing a root handle materializes the cached handles below it, and the same
    handle value reused for another key never answers from the old key's cache;
  * cached handles as roots, tag bits, big values, ProtectFromClose and
    DUPLICATE_CLOSE_SOURCE behave as real handles do;
  * NtClose of a root while another thread drops a different root (the review's R1
    interleaving, forced through RC_SCAN_HOOK): the unlocked root scan reads the slot
    the other root is about to leave, the drop happens, the scan goes on; the closed
    root is still found, its cached opens die, and the next key with its handle value
    gets the server's answer;
  * every server request the cache makes is inside rc_enter_section, with signals
    (SIGUSR1, as server_block_set) blocked.

Also checks that every ntdll function that hands a key handle to the server converts
cached handles first and that every registry write is bracketed, so the cache cannot
leak a handle the server does not know or miss a write.
"""
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
header = root / "build/ntdll-unix/regcache_ios.h"
glue = (root / "build/ntdll-unix/regcache_ios.c").read_text()
registry = (root / "wine/dlls/ntdll/unix/registry.c").read_text()
security = (root / "wine/dlls/ntdll/unix/security.c").read_text()
filec = (root / "wine/dlls/ntdll/unix/file.c").read_text()
server = (root / "build/ntdll-unix/server_ios.c").read_text()
build = (root / "build/ntdll-unix/build.sh").read_text()
library = (root / "app/Madeira/Library.swift").read_text()

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


# ---- source contracts ----

def functions(src):
    """name -> body for top-level C functions"""
    out = {}
    for m in re.finditer(r"^(?:NTSTATUS WINAPI|NTSTATUS|static NTSTATUS)\s+(\w+)\s*\([^;{]*?\)\s*\{", src, re.M | re.S):
        start = m.end()
        depth, i = 1, start
        while depth:
            c = src[i]
            depth += c == "{"
            depth -= c == "}"
            i += 1
        out[m.group(1)] = src[start:i]
    return out


reg_funcs = functions(registry)
writes = {"create_key", "delete_key", "rename_key", "set_key_value", "delete_key_value",
          "load_registry", "unload_registry"}
for name, body in reg_funcs.items():
    reqs = re.findall(r"SERVER_START_REQ\(\s*(\w+)\s*\)", body)
    if not reqs or name.startswith("ios_reg_srv_") or name == "enumerate_key":
        continue
    sends_key = "req->hkey" in body or "req->parent" in body or "alloc_object_attributes" in body
    if sends_key:
        check("IOS_RC_REAL" in body or "ios_regcache_open" in body or "ios_regcache_query" in body
              or "ios_regcache_real" in body,
              f"registry.c {name}: sends a key handle without converting cached handles")
    for r in reqs:
        if r in writes:
            check("ios_regcache_write_begin" in body and "ios_regcache_write_end" in body,
                  f"registry.c {name}: write request {r} not bracketed by ios_regcache_write_begin/_end")
for name in ("NtEnumerateKey", "NtQueryKey"):
    check("IOS_RC_REAL" in reg_funcs.get(name, ""), f"registry.c {name}: no IOS_RC_REAL")
check("ios_regcache_write_end( ret == STATUS_SUCCESS )" in reg_funcs["NtCreateKey"],
      "NtCreateKey must count only a created key as a change")
for name in ("NtQuerySecurityObject", "NtSetSecurityObject"):
    check("ios_regcache_real" in functions(security).get(name, ""), f"security.c {name}: no ios_regcache_real")
check("ios_regcache_write_begin" in functions(security).get("NtSetSecurityObject", ""),
      "security.c NtSetSecurityObject: not bracketed as a registry write")
for name in ("NtQueryObject", "NtSetInformationObject"):
    check("ios_regcache_real" in functions(filec).get(name, ""), f"file.c {name}: no ios_regcache_real")
srv_funcs = functions(server)
# R2: the cache and census locks are held like fd_cache_mutex (signals blocked, killed threads leave first)
hdr = header.read_text()
check("pthread_mutex_lock" not in hdr and "pthread_mutex_unlock" not in hdr,
      "regcache_ios.h takes the cache lock without rc_lock/rc_unlock (rc_enter_section)")
check("server_enter_uninterrupted_section( mutex, sigset );" in glue and
      "server_leave_uninterrupted_section( mutex, sigset );" in glue,
      "regcache_ios.c: rc_enter_section/rc_leave_section must be Wine's uninterrupted section")
check(not re.search(r"pthread_mutex_(un)?lock\( &(r->cen|c|r->proc|p)->lock", glue),
      "regcache_ios.c takes a cache or census lock outside rc_enter_section")
# R1: roots never move between slots while NtClose may be scanning them
remove_at = hdr[hdr.index("static void rc_base_remove_at("):]
remove_at = remove_at[:remove_at.index("\n}\n")]
check("p->bases[last]" not in remove_at and "(HANDLE)0" in remove_at,
      "rc_base_remove_at must free the slot, never move another root into it")
check("ios_regcache_close(" in srv_funcs.get("NtClose", "") and "ios_regcache_close_done" in srv_funcs.get("NtClose", ""),
      "server_ios.c NtClose: cache hook missing")
check("ios_regcache_real" in srv_funcs.get("NtDuplicateObject", "") and "ios_regcache_forget" in srv_funcs.get("NtDuplicateObject", ""),
      "server_ios.c NtDuplicateObject: cache hook missing")
check("ios_regcache_real" in srv_funcs.get("NtCompareObjects", ""), "server_ios.c NtCompareObjects: cache hook missing")
for path in (root / "wine/dlls", root / "build"):
    for f in path.rglob("*.c"):
        if "/obj/" in str(f) or f.name in ("registry.c", "security.c") and "ntdll/unix" in str(f):
            continue
        try:
            text = f.read_text(errors="ignore")
        except OSError:
            continue
        m = re.search(r"SERVER_START_REQ\(\s*(set_key_value|delete_key_value|create_key|delete_key|rename_key|"
                      r"load_registry|unload_registry|set_security_object)\s*\)", text)
        check(not m, f"{f.relative_to(root)} sends {m.group(1) if m else ''} outside ntdll's bracketed writers")
# off means off: "0" or unset leaves ios_regcache_on at 0, and every hook is gated on it
check("if (!v || !*v || (v[0] == '0' && !v[1])) return NULL;" in glue, "MADEIRA_REG_CACHE/CENSUS = 0 must read as off")
check("(rc_cache_list || rc_census_list) ? 1 : 0" in glue, "ios_regcache_on must stay 0 with both switches off")
for name, body in list(reg_funcs.items()) + list(srv_funcs.items()):
    for call in ("ios_regcache_open(", "ios_regcache_query(", "ios_regcache_close(", "ios_regcache_note_create("):
        if call in body:
            check("ios_rc_on()" in body or "ios_regcache_on > 0" in body, f"{name}: {call} not gated on the switches")
check('compile_one "$BUILD_DIR/regcache_ios.c" "regcache_ios"' in build and '"$OBJ_DIR/regcache_ios.o"' in build,
      "build.sh does not compile and archive regcache_ios")
rdr2 = library[library.index('case "rdr2.exe", "playrdr2.exe":'):]
rdr2 = rdr2[:rdr2.index("        case ", 10)]
check("env.MADEIRA_REG_CACHE = RDR2.exe" in rdr2, "RDR2 profile does not enable MADEIRA_REG_CACHE")
check("env.MADEIRA_REG_CENSUS = RDR2.exe" in rdr2, "RDR2 profile does not enable MADEIRA_REG_CENSUS")

# ---- the core against a model server ----

code = r'''
#include <assert.h>
#include <pthread.h>
#include <sched.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

typedef void *HANDLE;
typedef int32_t NTSTATUS;
typedef uint32_t ACCESS_MASK, ULONG;
typedef uint16_t USHORT, WCHAR;
typedef uint8_t BYTE;
#define STATUS_SUCCESS               ((NTSTATUS)0)
#define STATUS_INVALID_HANDLE        ((NTSTATUS)0xC0000008)
#define STATUS_OBJECT_NAME_NOT_FOUND ((NTSTATUS)0xC0000034)
#define STATUS_OBJECT_PATH_NOT_FOUND ((NTSTATUS)0xC000003A)
#define STATUS_KEY_DELETED           ((NTSTATUS)0xC000017C)
#define STATUS_HANDLE_NOT_CLOSABLE   ((NTSTATUS)0xC0000235)
#define OBJ_CASE_INSENSITIVE 0x40
#define KEY_READ 0x20019
#define KEY_ALL_ACCESS 0xF003F

/* ---- model wineserver ---- */
struct mval { char name[32]; ULONG type, len; BYTE data[600]; };
struct node { char name[32]; struct node *parent; struct node *kids[16]; int nkids; struct mval vals[8]; int nvals; int deleted; };
static struct node *top;
static pthread_mutex_t srv = PTHREAD_MUTEX_INITIALIZER;
static __thread int cur;                  /* current pseudo-process */
static struct { struct node *n; int used, protect; } htab[2][4096];
static unsigned long srv_opens, srv_queries, srv_closes;
static unsigned int seed = 1;

static void jitter(void) { if ((__atomic_add_fetch( &seed, 2654435761u, __ATOMIC_RELAXED ) >> 13) % 5 == 0) sched_yield(); }

/* the cache's lock, as ntdll holds it: SIGUSR1 (in server_block_set) blocked while held */
static __thread int in_section;
static sigset_t block_set;
static unsigned long unsectioned;
static void rc_enter_section( pthread_mutex_t *mutex, sigset_t *sigset )
{
    pthread_sigmask( SIG_BLOCK, &block_set, sigset );
    pthread_mutex_lock( mutex );
    in_section++;
}
static void rc_leave_section( pthread_mutex_t *mutex, sigset_t *sigset )
{
    in_section--;
    pthread_mutex_unlock( mutex );
    pthread_sigmask( SIG_SETMASK, sigset, NULL );
}
/* a request from the cache (not from t_open's own pass-through) must be inside the section */
static __thread int direct;
static void check_section( void )
{
    sigset_t cur_set;
    if (direct) return;
    pthread_sigmask( SIG_BLOCK, NULL, &cur_set );
    if (!in_section || !sigismember( &cur_set, SIGUSR1 )) __atomic_add_fetch( &unsectioned, 1, __ATOMIC_RELAXED );
}


static struct node *mknode( struct node *parent, const char *name )
{
    struct node *n = calloc( 1, sizeof(*n) );
    snprintf( n->name, sizeof(n->name), "%s", name );
    n->parent = parent;
    if (parent) parent->kids[parent->nkids++] = n;
    return n;
}
static struct node *kid( struct node *n, const char *name, size_t len )
{
    for (int i = 0; i < n->nkids; i++)
        if (strlen( n->kids[i]->name ) == len && !strncasecmp( n->kids[i]->name, name, len )) return n->kids[i];
    return NULL;
}
static HANDLE halloc( struct node *n )
{
    for (int i = 0; i < 4096; i++) if (!htab[cur][i].used) { htab[cur][i].used = 1; htab[cur][i].n = n; htab[cur][i].protect = 0; return (HANDLE)(uintptr_t)((i + 1) * 4); }
    abort();
}
static struct node *hget( HANDLE h, NTSTATUS *st )
{
    uintptr_t v = ((uintptr_t)h & ~3) / 4;
    if (!h) return top;
    if (v < 1 || v > 4096 || !htab[cur][v - 1].used) { *st = STATUS_INVALID_HANDLE; return NULL; }
    if (htab[cur][v - 1].n->deleted) { *st = STATUS_KEY_DELETED; return NULL; }
    return htab[cur][v - 1].n;
}
static void w2a( char *out, const WCHAR *w, USHORT len ) { int n = len / 2; for (int i = 0; i < n; i++) out[i] = (char)w[i]; out[n] = 0; }
static struct node *walk( struct node *n, const char *path )
{
    while (n && *path)
    {
        const char *e;
        while (*path == '\\') path++;
        if (!*path) break;
        e = strchr( path, '\\' ); if (!e) e = path + strlen( path );
        n = kid( n, path, e - path );
        path = e;
    }
    return n;
}
static NTSTATUS rc_srv_open( HANDLE *key, HANDLE root, const WCHAR *name, USHORT len, ACCESS_MASK access, ULONG attributes )
{
    char path[400]; NTSTATUS st = 0; struct node *n;
    check_section();
    jitter();
    pthread_mutex_lock( &srv );
    srv_opens++;
    *key = 0;
    w2a( path, name, len );
    if ((n = hget( root, &st ))) { if (!(n = walk( n, path ))) st = STATUS_OBJECT_NAME_NOT_FOUND; else *key = halloc( n ); }
    pthread_mutex_unlock( &srv );
    jitter();
    return st;
}
static NTSTATUS rc_srv_query( HANDLE key, const WCHAR *name, USHORT len, ULONG *type, ULONG *total, void *data, ULONG max )
{
    char vn[200]; NTSTATUS st = 0; struct node *n;
    check_section();
    jitter();
    pthread_mutex_lock( &srv );
    srv_queries++;
    w2a( vn, name, len );
    if ((n = hget( key, &st )))
    {
        st = STATUS_OBJECT_NAME_NOT_FOUND;
        for (int i = 0; i < n->nvals; i++)
            if (!strcasecmp( n->vals[i].name, vn ))
            {
                *type = n->vals[i].type; *total = n->vals[i].len;
                memcpy( data, n->vals[i].data, max < n->vals[i].len ? max : n->vals[i].len );
                st = 0;
            }
    }
    pthread_mutex_unlock( &srv );
    jitter();
    return st;
}
static NTSTATUS srv_close_raw( HANDLE key )
{
    uintptr_t v = ((uintptr_t)key & ~3) / 4; NTSTATUS st = 0;
    pthread_mutex_lock( &srv );
    srv_closes++;
    if (v < 1 || v > 4096 || !htab[cur][v - 1].used) st = STATUS_INVALID_HANDLE;
    else if (htab[cur][v - 1].protect) st = STATUS_HANDLE_NOT_CLOSABLE;
    else htab[cur][v - 1].used = 0;
    pthread_mutex_unlock( &srv );
    return st;
}
static NTSTATUS rc_srv_close( HANDLE key );

/* R1: a forced schedule inside NtClose's unlocked root scan */
static void (*scan_hook)( int i );
static __thread int scan_hook_armed;
#define RC_SCAN_HOOK(p, i) do { if (scan_hook_armed && scan_hook) scan_hook( i ); } while (0)

#include "REGCACHE_H"

/* ---- the ntdll side, as registry.c / server_ios.c call the core ---- */
static struct rc_proc procs[2];
static void S( const char *a, WCHAR *w, USHORT *len ) { int n = (int)strlen( a ); for (int i = 0; i < n; i++) w[i] = a[i]; *len = (USHORT)(n * 2); }

static NTSTATUS t_close( HANDLE h )
{
    NTSTATUS ret;
    sigset_t root_sigset;
    switch (rc_close( &procs[cur], h, &ret, &root_sigset ))
    {
    case 1: return ret;
    case 2: ret = srv_close_raw( h ); rc_close_done( &procs[cur], &root_sigset ); return ret;
    default: return srv_close_raw( h );
    }
}
static NTSTATUS rc_srv_close( HANDLE key ) { return t_close( key ); }   /* NtClose, hook included */

static NTSTATUS t_open( HANDLE *key, HANDLE root, const char *name, ACCESS_MASK access )
{
    WCHAR w[200]; USHORT len; NTSTATUS st;
    S( name, w, &len );
    if (rc_open( &procs[cur], key, root, w, len, access, OBJ_CASE_INSENSITIVE, 0, &st )) return st;
    direct = 1;
    st = rc_srv_open( key, root, w, len, access, OBJ_CASE_INSENSITIVE );
    direct = 0;
    return st;
}
static NTSTATUS t_query( HANDLE h, const char *name, ULONG *val )
{
    WCHAR w[100]; USHORT len; NTSTATUS st; ULONG type = 0, total = 0; BYTE data[600];
    S( name, w, &len );
    if (!rc_query( &procs[cur], &h, w, len, &type, &total, data, sizeof(data), &st ))
    {
        direct = 1;
        st = rc_srv_query( h, w, len, &type, &total, data, sizeof(data) );
        direct = 0;
    }
    if (!st && val) memcpy( val, data, 4 );
    return st;
}
static NTSTATUS t_query_big( HANDLE h, const char *name, BYTE *data, ULONG *total )
{
    WCHAR w[100]; USHORT len; NTSTATUS st; ULONG type = 0;
    S( name, w, &len );
    if (!rc_query( &procs[cur], &h, w, len, &type, total, data, 600, &st ))
    {
        direct = 1;
        st = rc_srv_query( h, w, len, &type, total, data, 600 );
        direct = 0;
    }
    return st;
}
static NTSTATUS t_set( HANDLE h, const char *name, const void *data, ULONG len )
{
    NTSTATUS st = rc_real( &procs[cur], &h ); struct node *n; char vn[32];
    if (st) return st;
    snprintf( vn, sizeof(vn), "%s", name );
    rc_write_begin();
    jitter();
    pthread_mutex_lock( &srv );
    if ((n = hget( h, &st )))
    {
        int i;
        for (i = 0; i < n->nvals; i++) if (!strcasecmp( n->vals[i].name, vn )) break;
        if (i == n->nvals) { n->nvals++; snprintf( n->vals[i].name, 32, "%s", vn ); }
        n->vals[i].type = 4; n->vals[i].len = len; memcpy( n->vals[i].data, data, len );
    }
    pthread_mutex_unlock( &srv );
    jitter();
    rc_write_end( 1 );
    return st;
}
static NTSTATUS t_delval( HANDLE h, const char *name )
{
    NTSTATUS st = rc_real( &procs[cur], &h ); struct node *n;
    if (st) return st;
    rc_write_begin();
    pthread_mutex_lock( &srv );
    if ((n = hget( h, &st )))
        for (int i = 0; i < n->nvals; i++) if (!strcasecmp( n->vals[i].name, name )) { n->vals[i] = n->vals[--n->nvals]; break; }
    pthread_mutex_unlock( &srv );
    rc_write_end( 1 );
    return st;
}
static NTSTATUS t_create( HANDLE *key, HANDLE root, const char *name )
{
    NTSTATUS st = rc_real( &procs[cur], &root ); struct node *n, *k; int created = 0;
    if (st) return st;
    rc_write_begin();
    pthread_mutex_lock( &srv );
    if ((n = hget( root, &st )))
    {
        if (!(k = kid( n, name, strlen( name ) ))) { k = mknode( n, name ); created = 1; }
        *key = halloc( k );
    }
    pthread_mutex_unlock( &srv );
    rc_write_end( created );
    return st;
}
static NTSTATUS t_delkey( HANDLE h )
{
    NTSTATUS st = rc_real( &procs[cur], &h ); struct node *n;
    if (st) return st;
    rc_write_begin();
    pthread_mutex_lock( &srv );
    if ((n = hget( h, &st )))
    {
        struct node *p = n->parent;
        n->deleted = 1;
        for (int i = 0; i < p->nkids; i++) if (p->kids[i] == n) { p->kids[i] = p->kids[--p->nkids]; break; }
    }
    pthread_mutex_unlock( &srv );
    rc_write_end( 1 );
    return st;
}
static ULONG u32( ULONG v ) { return v; }

/* ---- freshness race ---- */
static volatile ULONG committed;
static volatile int stop;
static HANDLE wkey;
static unsigned long stale, reads;

static void *writer( void *arg )
{
    cur = 1;
    for (ULONG i = 1; i <= ROUNDS; i++)
    {
        assert( !t_set( wkey, "Counter", &i, 4 ) );
        __atomic_store_n( &committed, i, __ATOMIC_SEQ_CST );
        for (int k = 0; k < 40; k++) sched_yield();     /* let the readers hit the cache between writes */
        if (i % 7 == 0) { HANDLE k; assert( !t_open( &k, 0, "\\Machine\\Software\\Game", KEY_READ ) ); t_close( k ); }
    }
    __atomic_store_n( &stop, 1, __ATOMIC_SEQ_CST );
    return NULL;
}
static void *reader( void *arg )
{
    HANDLE hold = 0;
    cur = 0;
    assert( !t_open( &hold, 0, "\\Machine\\Software\\Game", KEY_READ ) );
    while (!__atomic_load_n( &stop, __ATOMIC_SEQ_CST ))
    {
        ULONG before = __atomic_load_n( &committed, __ATOMIC_SEQ_CST ), v = 0;
        HANDLE k;
        if (!t_open( &k, 0, "\\Machine\\Software\\Game", KEY_READ ))
        {
            if (!t_query( k, "Counter", &v ) && v < before) __atomic_add_fetch( &stale, 1, __ATOMIC_RELAXED );
            t_close( k );
        }
        before = __atomic_load_n( &committed, __ATOMIC_SEQ_CST );
        if (!t_query( hold, "Counter", &v ) && v < before) __atomic_add_fetch( &stale, 1, __ATOMIC_RELAXED );
        __atomic_add_fetch( &reads, 2, __ATOMIC_RELAXED );
    }
    t_close( hold );
    return NULL;
}

/* ---- R1: NtClose(B) scans the roots while another thread drops root X ---- */
static HANDLE r1_x;
static volatile int r1_go, r1_done, r1_fired;
static int r1_x_slot;
static void r1_hook( int i )
{
    if (i != r1_x_slot || r1_fired) return;
    r1_fired = 1;          /* the scan has read X's slot (not B) and not yet the slots after it */
    __atomic_store_n( &r1_go, 1, __ATOMIC_SEQ_CST );
    while (!__atomic_load_n( &r1_done, __ATOMIC_SEQ_CST )) sched_yield();
}
static void *r1_close_x( void *arg )
{
    cur = 0;
    while (!__atomic_load_n( &r1_go, __ATOMIC_SEQ_CST )) sched_yield();
    assert( !t_close( r1_x ) );     /* drops root X under the lock */
    __atomic_store_n( &r1_done, 1, __ATOMIC_SEQ_CST );
    return NULL;
}

int main( void )
{
    HANDLE h, h2, hw, v, sub, rootk;
    ULONG val, one = 1, two = 2, three = 3;
    unsigned long o0, q0;
    struct node *mach, *soft, *game, *other;

    sigemptyset( &block_set );
    sigaddset( &block_set, SIGUSR1 );
    top = mknode( NULL, "" );
    mach = mknode( top, "Machine" );
    soft = mknode( mach, "Software" );
    game = mknode( soft, "Game" );
    other = mknode( soft, "Other" );
    mknode( game, "Sub" );
    rc_proc_init( &procs[0], 1, 2 );
    rc_proc_init( &procs[1], 1, 2 );

    /* writer process: a real (cold) handle to write with */
    cur = 1;
    assert( !t_open( &hw, 0, "\\Machine\\Software\\Game", KEY_ALL_ACCESS ) );
    assert( !rc_is_virtual( hw ) );
    assert( !t_set( hw, "Counter", &one, 4 ) );
    wkey = hw;

    /* 1: hot opens and reads leave the server alone */
    cur = 0;
    for (int i = 0; i < 3; i++) { assert( !t_open( &h, 0, "\\Machine\\Software\\Game", KEY_READ ) ); assert( !t_query( h, "Counter", &val ) && val == 1 ); t_close( h ); }
    o0 = srv_opens; q0 = srv_queries;
    for (int i = 0; i < 1000; i++)
    {
        assert( !t_open( &h, 0, "\\Machine\\Software\\Game", KEY_READ ) );
        assert( rc_is_virtual( h ) );
        assert( !t_query( h, "Counter", &val ) && val == 1 );
        assert( t_query( h, "Missing", &val ) == STATUS_OBJECT_NAME_NOT_FOUND );
        assert( !t_close( h ) );
    }
    printf( "hot loop: %lu server opens, %lu server queries for 1000 rounds\n", srv_opens - o0, srv_queries - q0 );
    assert( srv_opens - o0 <= 2 && srv_queries - q0 <= 2 );
    assert( procs[0].slots_used == 0 );

    /* 2: another process writes; the next read sees it */
    cur = 1; assert( !t_set( hw, "Counter", &two, 4 ) );
    cur = 0; assert( !t_open( &h, 0, "\\Machine\\Software\\Game", KEY_READ ) ); assert( !t_query( h, "Counter", &val ) && val == 2 );
    cur = 1; assert( !t_set( hw, "Missing", &three, 4 ) );
    cur = 0; assert( !t_query( h, "Missing", &val ) && val == 3 );
    cur = 1; assert( !t_delval( hw, "Missing" ) );
    cur = 0; assert( t_query( h, "Missing", &val ) == STATUS_OBJECT_NAME_NOT_FOUND );
    t_close( h );

    /* 3: cached "not found", then the key is created by another process */
    cur = 0;
    for (int i = 0; i < 5; i++) assert( t_open( &h, 0, "\\Machine\\Software\\New", KEY_READ ) == STATUS_OBJECT_NAME_NOT_FOUND );
    o0 = srv_opens;
    assert( t_open( &h, 0, "\\Machine\\Software\\New", KEY_READ ) == STATUS_OBJECT_NAME_NOT_FOUND && srv_opens == o0 );
    cur = 1; { HANDLE soft1, nk; assert( !t_open( &soft1, 0, "\\Machine\\Software", KEY_ALL_ACCESS ) ); assert( !t_create( &nk, soft1, "New" ) ); t_close( nk ); t_close( soft1 ); }
    cur = 0; assert( !t_open( &h, 0, "\\Machine\\Software\\New", KEY_READ ) ); t_close( h );

    /* 4: a cached handle never materialized, its key deleted by another process */
    cur = 0;
    for (int i = 0; i < 3; i++) { assert( !t_open( &h, 0, "\\Machine\\Software\\New", KEY_READ ) ); t_close( h ); }
    assert( !t_open( &v, 0, "\\Machine\\Software\\New", KEY_READ ) && rc_is_virtual( v ) && !procs[0].slots[rc_slot_of( &procs[0], v )].real );
    cur = 1; { HANDLE nk; assert( !t_open( &nk, 0, "\\Machine\\Software\\New", KEY_ALL_ACCESS ) ); assert( !t_delkey( nk ) ); t_close( nk ); }
    cur = 0; assert( t_query( v, "Counter", &val ) == STATUS_KEY_DELETED ); assert( !t_close( v ) );
    assert( t_open( &h, 0, "\\Machine\\Software\\New", KEY_READ ) == STATUS_OBJECT_NAME_NOT_FOUND );

    /* 5: a root handle's cached opens; the root closed; its handle value reused */
    cur = 0;
    assert( !t_open( &rootk, 0, "\\Machine\\Software", KEY_READ ) && !rc_is_virtual( rootk ) );
    for (int i = 0; i < 3; i++) { assert( !t_open( &h, rootk, "Game", KEY_READ ) ); t_close( h ); }
    assert( !t_open( &v, rootk, "Game", KEY_READ ) && rc_is_virtual( v ) );
    assert( !t_open( &sub, v, "Sub", KEY_READ ) );     /* opened under a cached handle */
    assert( !t_query( v, "Counter", &val ) && val == 2 );
    assert( !t_close( rootk ) );                        /* v gets a real handle first */
    assert( procs[0].slots[rc_slot_of( &procs[0], v )].real );
    assert( !t_query( v, "Counter", &val ) && val == 2 );
    { HANDLE r2; assert( !t_open( &r2, 0, "\\Machine\\Software\\Other", KEY_READ ) ); assert( r2 == rootk );   /* same value, other key */
      assert( t_open( &h, r2, "Game", KEY_READ ) == STATUS_OBJECT_NAME_NOT_FOUND ); t_close( r2 ); }
    assert( !t_close( v ) ); assert( !t_close( sub ) );

    /* 6: tag bits, ProtectFromClose, DUPLICATE_CLOSE_SOURCE */
    cur = 0;
    assert( !t_open( &v, 0, "\\Machine\\Software\\Game", KEY_READ ) && rc_is_virtual( v ) );
    assert( !t_query( (HANDLE)((uintptr_t)v | 1), "Counter", &val ) && val == 2 );
    h = v; assert( !rc_real( &procs[0], &h ) && !rc_is_virtual( h ) );     /* materialized */
    htab[0][((uintptr_t)h / 4) - 1].protect = 1;
    assert( t_close( v ) == STATUS_HANDLE_NOT_CLOSABLE );
    assert( !t_query( v, "Counter", &val ) && val == 2 );
    htab[0][((uintptr_t)h / 4) - 1].protect = 0;
    assert( !t_close( (HANDLE)((uintptr_t)v | 2) ) );
    assert( t_close( v ) == STATUS_INVALID_HANDLE );                       /* now gone: the server says so */
    assert( !t_open( &v, 0, "\\Machine\\Software\\Game", KEY_READ ) && rc_is_virtual( v ) );
    h = v; assert( !rc_real( &procs[0], &h ) );
    assert( !srv_close_raw( h ) );                                         /* the dup closed the source */
    rc_forget( &procs[0], v );
    assert( procs[0].slots_used == 0 );

    /* 7: a value too big to keep */
    {
        BYTE big[500], out[600]; ULONG total;
        for (int i = 0; i < 500; i++) big[i] = (BYTE)i;
        cur = 1; assert( !t_set( hw, "Big", big, 500 ) );
        cur = 0;
        for (int i = 0; i < 5; i++)
        {
            assert( !t_open( &v, 0, "\\Machine\\Software\\Game", KEY_READ ) );
            assert( !t_query_big( v, "Big", out, &total ) && total == 500 && !memcmp( out, big, 500 ) );
            t_close( v );
        }
    }

    /* 7b: the default (unnamed) value, an empty name */
    {
        WCHAR w[1]; NTSTATUS st; ULONG type, total; BYTE data[16]; HANDLE dv;
        cur = 1; assert( !t_set( hw, "", &three, 4 ) );
        cur = 0;
        for (int i = 0; i < 4; i++)
        {
            assert( !t_open( &dv, 0, "\\Machine\\Software\\Game", KEY_READ ) );
            if (!rc_query( &procs[0], &dv, NULL, 0, &type, &total, data, sizeof(data), &st ))
            {
                direct = 1;
                st = rc_srv_query( dv, w, 0, &type, &total, data, sizeof(data) );
                direct = 0;
            }
            assert( !st && total == 4 && !memcmp( data, &three, 4 ) );
            t_close( dv );
        }
    }

    /* 8: opens that may write are never cached; read opens of the same key still are */
    cur = 0;
    for (int i = 0; i < 6; i++) { assert( !t_open( &h, 0, "\\Machine\\Software\\Game", KEY_ALL_ACCESS ) ); assert( !rc_is_virtual( h ) ); t_close( h ); }
    for (int i = 0; i < 6; i++) { assert( !t_open( &h, 0, "\\Machine\\Software\\Game", 0x20000 | 0x2 ) ); assert( !rc_is_virtual( h ) ); t_close( h ); }  /* READ_CONTROL | KEY_SET_VALUE */
    assert( !t_open( &h2, 0, "\\Machine\\Software\\Game", KEY_READ ) ); assert( rc_is_virtual( h2 ) ); t_close( h2 );

    /* 9: the race */
    {
        pthread_t w, r1, r2;
        pthread_create( &r1, NULL, reader, NULL );
        pthread_create( &r2, NULL, reader, NULL );
        pthread_create( &w, NULL, writer, NULL );
        pthread_join( w, NULL ); pthread_join( r1, NULL ); pthread_join( r2, NULL );
        printf( "race: %lu reads against %d writes, %lu stale; cache hits %llu value / %llu open\n", reads, ROUNDS, stale,
                procs[0].st.val_hit, procs[0].st.open_hit );
        assert( !stale );
        assert( procs[0].slots_used == 0 );
    }

    /* 10: R1. Roots X then B in the list, B in the last used slot; cached opens below
     * both. Thread A closes X while this thread closes B, with A's close forced into
     * the moment B's unlocked scan has read X's slot and not the ones after it. */
    {
        HANDLE x, b, r1, r2, k;
        pthread_t a;
        int bslot;
        cur = 0;
        /* real root handles (opens for writing are never cached) */
        assert( !t_open( &x, 0, "\\Machine\\Software", KEY_ALL_ACCESS ) && !rc_is_virtual( x ) );
        assert( !t_open( &b, 0, "\\Machine\\Software", KEY_ALL_ACCESS ) && !rc_is_virtual( b ) );
        for (int i = 0; i < 3; i++)
        {
            assert( !t_open( &k, x, "Game", KEY_READ ) ); t_close( k );
            assert( !t_open( &k, b, "Game", KEY_READ ) ); t_close( k );
        }
        r1_x_slot = rc_base_index( &procs[0], x );
        bslot = rc_base_index( &procs[0], b );
        assert( r1_x_slot >= 0 && bslot > r1_x_slot && bslot == procs[0].nbases - 1 );
        assert( procs[0].slots_used == 0 );
        r1_x = x;
        scan_hook = r1_hook;
        pthread_create( &a, NULL, r1_close_x, NULL );
        scan_hook_armed = 1;
        assert( !t_close( b ) );
        scan_hook_armed = 0;
        pthread_join( a, NULL );
        assert( r1_fired );
        /* the freed values come back: X's first, then B's, now naming a key with no "Game" */
        assert( !t_open( &r1, 0, "\\Machine\\Software\\Other", KEY_ALL_ACCESS ) );
        assert( !t_open( &r2, 0, "\\Machine\\Software\\Other", KEY_ALL_ACCESS ) );
        assert( (r1 == x && r2 == b) || (r1 == b && r2 == x) );
        k = 0;
        assert( t_open( &k, b, "Game", KEY_READ ) == STATUS_OBJECT_NAME_NOT_FOUND && !k );
        assert( t_open( &k, x, "Game", KEY_READ ) == STATUS_OBJECT_NAME_NOT_FOUND && !k );
        t_close( r1 ); t_close( r2 );
        assert( rc_base_index( &procs[0], b ) < 0 && rc_base_index( &procs[0], x ) < 0 );
        scan_hook = NULL;
        printf( "R1: NtClose(B) during the drop of root X found B; B's reused value opens fresh\n" );
    }

    printf( "server requests from the cache outside its section: %lu\n", unsectioned );
    assert( !unsectioned );
    (void)other; (void)u32;
    printf( "ok\n" );
    return 0;
}
'''

cc = shutil.which("clang") or shutil.which("cc")
if not cc:
    print("SKIP: no C compiler")
    sys.exit(0 if not failures else 1)

with tempfile.TemporaryDirectory() as tmp:
    src = Path(tmp) / "t.c"
    src.write_text(code.replace('"REGCACHE_H"', '"' + str(header) + '"'))
    for san, rounds in (("address,undefined", 5000), ("thread", 1500)):
        exe = Path(tmp) / f"t-{san.split(',')[0]}"
        r = subprocess.run([cc, "-O1", "-g", f"-fsanitize={san}", "-fno-sanitize-recover=all", "-pthread",
                            f"-DROUNDS={rounds}", "-Wall", "-Wno-unused-function", str(src), "-o", str(exe)],
                           capture_output=True, text=True)
        if r.returncode:
            failures.append(f"compile ({san}) failed:\n{r.stderr[-3000:]}")
            break
        r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=600)
        sys.stdout.write(f"[{san}] " + r.stdout)
        if r.returncode or "ok" not in r.stdout:
            failures.append(f"model run ({san}) failed (rc {r.returncode}):\n{r.stdout[-2000:]}\n{r.stderr[-4000:]}")

if failures:
    print("FAIL")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("PASS check-regcache")
