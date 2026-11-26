/*
 * Client-side registry read cache: the core (madeira, env.MADEIRA_REG_CACHE).
 *
 * WHY. Red Dead Redemption 2's main thread opens a registry key, reads values
 * and closes it again thousands of times a second while a menu is up: ~5,700
 * open_key, ~3,600 get_key_value and ~4,300 close_handle requests a second for
 * 30-60 s at a time (logs 2026-11-26 20:17 and 2026-11-27 00:19, [srv-req]
 * ml1055 req87/req92/req21 all from the main thread). Windows answers these
 * in the kernel in about a microsecond; here each one is a round trip to the
 * wineserver thread, with two thread switches, on a phone whose CPU power is
 * capped after a few minutes. This file answers repeated opens and value
 * reads of the same key from the client, without the server, and stays
 * exactly as fresh as the server.
 *
 * FRESHNESS. All pseudo-processes share one copy of this code (one iOS
 * process), and every registry write of every process goes through ntdll's
 * NtCreateKey, NtSetValueKey, NtDeleteValueKey, NtDeleteKey, NtRenameKey,
 * NtLoadKeyEx, NtUnloadKey or NtSetSecurityObject (nothing else sends
 * create_key, set_key_value, delete_key_value, delete_key, rename_key,
 * load_registry, unload_registry or set_security_object). Each write
 * brackets its request with rc_write_begin / rc_write_end: a count of writes
 * in flight, and a generation that moves when one has finished. A cached
 * answer is used only while no write is in flight and the generation is the
 * one it was read at; an answer is stored only if no write was in flight
 * when its request was sent and none started or finished before the reply
 * was in. So a read that starts after a write has returned never sees what
 * the write replaced, from any process, and during a write the cache is not
 * used at all. Any write anywhere invalidates everything: coarse, but the
 * hot reads repeat thousands of times between writes.
 *
 * HANDLES. An open the cache answers returns a handle that only this code
 * knows (RC_VIRT_FIRST and up, below the 0x80000000 predefined keys, far
 * above any handle the server hands out). Value reads and closes of it
 * never reach the server. Anything else done with it (enumeration, query
 * key, notification, security, duplication, a write, or use as the root of
 * an open the cache does not take) first opens the key for real the way
 * the program did (same root, name, access and attributes) and keeps that
 * handle with it from then on ("materialize"); the call then proceeds on
 * the real handle. Opens are cached per (root handle, name, access,
 * attributes); a root handle's cached opens die when that handle is closed,
 * after the outstanding cached handles below it have been materialized
 * while the root still exists. The one difference from a real handle: a
 * cached handle that was never materialized and whose key is deleted and
 * re-created under the same name before its next uncached use names the new
 * key instead of reporting STATUS_KEY_DELETED.
 *
 * This header has no Wine dependencies so tests/host/check-regcache.py can
 * run it against a model registry. The includer defines HANDLE, NTSTATUS,
 * ACCESS_MASK, ULONG, USHORT, WCHAR, BYTE, the STATUS_ codes used below,
 * OBJ_CASE_INSENSITIVE, and:
 *
 *   static NTSTATUS rc_srv_open( HANDLE *key, HANDLE root, const WCHAR *name, USHORT len,
 *                                ACCESS_MASK access, ULONG attributes );
 *   static NTSTATUS rc_srv_query( HANDLE key, const WCHAR *name, USHORT len, ULONG *type,
 *                                 ULONG *total, void *data, ULONG max );
 *   static NTSTATUS rc_srv_close( HANDLE key );
 */

#include <pthread.h>
#include <stdint.h>
#include <string.h>

#define RC_ENTRIES      256     /* cached opens per process */
#define RC_EBUCKETS     512
#define RC_VALUES       512     /* cached values per process */
#define RC_VBUCKETS     1024
#define RC_SLOTS        512     /* cached handles open at once per process */
#define RC_NAME_MAX     128     /* WCHARs of an open's name */
#define RC_VNAME_MAX    64      /* WCHARs of a value's name */
#define RC_DATA_MAX     256     /* bytes of a value's data */
#define RC_BASES        16      /* distinct root handles per process */
#define RC_VIRT_FIRST   0x7ff00000u
/* KEY_READ (READ_CONTROL | KEY_QUERY_VALUE | KEY_ENUMERATE_SUB_KEYS | KEY_NOTIFY) and the
 * WOW64 view bits: the only access a cached open may ask for */
#define RC_READ_ACCESS  (0x00020019u | 0x0100u | 0x0200u)
#define RC_HANDLE(i)    ((HANDLE)(uintptr_t)(RC_VIRT_FIRST + ((unsigned int)(i) << 2)))

/* ---- freshness: shared by every process ---- */

static long long rc_gen;        /* registry writes finished, any process */
static int rc_inflight;         /* registry writes in flight, any process */

static void rc_write_begin( void )
{
    __atomic_fetch_add( &rc_inflight, 1, __ATOMIC_SEQ_CST );
}

static void rc_write_end( int changed )
{
    if (changed) __atomic_fetch_add( &rc_gen, 1, __ATOMIC_SEQ_CST );
    __atomic_fetch_sub( &rc_inflight, 1, __ATOMIC_SEQ_CST );
}

/* before a request whose answer may be stored: 0 while a write is in flight */
static int rc_snap( long long *gen )
{
    if (__atomic_load_n( &rc_inflight, __ATOMIC_SEQ_CST )) return 0;
    *gen = __atomic_load_n( &rc_gen, __ATOMIC_SEQ_CST );
    return 1;
}

/* an answer read at `gen` is still the server's answer */
static int rc_still( long long gen )
{
    if (__atomic_load_n( &rc_inflight, __ATOMIC_SEQ_CST )) return 0;
    return __atomic_load_n( &rc_gen, __ATOMIC_SEQ_CST ) == gen;
}

/* ---- per-process state ---- */

struct rc_entry
{
    unsigned char used, live, valid;
    short parent;                   /* entry this was opened under, -1: under `root` */
    short next;                     /* hash chain */
    unsigned short name_len;        /* bytes */
    unsigned int hash, epoch, opens;
    int refs;                       /* cached handles and child entries using it */
    HANDLE root;                    /* the program's real root handle (parent < 0) */
    HANDLE base;                    /* the real root handle at the top of the chain, 0: none */
    ACCESS_MASK access;
    ULONG attributes;
    long long gen;                  /* the cached open result is valid at this generation */
    NTSTATUS status;
    WCHAR name[RC_NAME_MAX];
};

struct rc_value
{
    unsigned char used, big;        /* big: data longer than RC_DATA_MAX, ask the server */
    short entry, next;
    unsigned short name_len, data_len;
    unsigned int epoch, hash;
    long long gen;
    NTSTATUS status;
    ULONG type, total;
    WCHAR name[RC_VNAME_MAX];
    BYTE data[RC_DATA_MAX];
};

struct rc_slot
{
    int used;
    int entry;
    HANDLE real;                    /* 0 until materialized */
};

struct rc_stats
{
    unsigned long long open_hit, open_neg_hit, open_miss, open_pass;
    unsigned long long val_hit, val_miss, val_big, val_pass;
    unsigned long long closes, materialized, root_closes, full;
};

struct rc_proc
{
    int cache;                      /* answer from the cache (else census only) */
    unsigned int hot;               /* opens of one (root, name, access) before it is cached */
    pthread_mutex_t lock;           /* recursive */
    struct rc_entry entries[RC_ENTRIES];
    short ebuckets[RC_EBUCKETS];
    unsigned int eclock;
    struct rc_value values[RC_VALUES];
    short vbuckets[RC_VBUCKETS];
    unsigned int vclock;
    struct rc_slot slots[RC_SLOTS];
    unsigned int shint, slots_used;
    HANDLE bases[RC_BASES];
    int base_refs[RC_BASES];
    int nbases;
    struct rc_stats st;
};

static void rc_proc_init( struct rc_proc *p, int cache, unsigned int hot )
{
    pthread_mutexattr_t attr;
    int i;

    memset( p, 0, sizeof(*p) );
    p->cache = cache;
    p->hot = hot;
    pthread_mutexattr_init( &attr );
    pthread_mutexattr_settype( &attr, PTHREAD_MUTEX_RECURSIVE );
    pthread_mutex_init( &p->lock, &attr );
    pthread_mutexattr_destroy( &attr );
    for (i = 0; i < RC_EBUCKETS; i++) p->ebuckets[i] = -1;
    for (i = 0; i < RC_VBUCKETS; i++) p->vbuckets[i] = -1;
}

static inline uintptr_t rc_hval( HANDLE h )
{
    return (uintptr_t)h & ~(uintptr_t)3;   /* the low two bits are tag bits, as on Windows */
}

static inline int rc_is_virtual( HANDLE h )
{
    uintptr_t v = rc_hval( h );
    return v >= RC_VIRT_FIRST && v < RC_VIRT_FIRST + RC_SLOTS * 4u;
}

/* slot index of a live cached handle of this process, -1 otherwise */
static int rc_slot_of( struct rc_proc *p, HANDLE h )
{
    unsigned int i;
    if (!rc_is_virtual( h )) return -1;
    i = (unsigned int)((rc_hval( h ) - RC_VIRT_FIRST) >> 2);
    return p->slots[i].used ? (int)i : -1;
}

static unsigned int rc_hash( unsigned int h, const void *data, unsigned int len )
{
    const unsigned char *b = data;
    while (len--) h = (h ^ *b++) * 16777619u;   /* FNV-1a */
    return h;
}

static unsigned int rc_entry_hash( int parent, HANDLE root, ACCESS_MASK access, ULONG attributes,
                                   const WCHAR *name, USHORT len )
{
    uintptr_t r = parent >= 0 ? (uintptr_t)parent | ((uintptr_t)1 << 31) : rc_hval( root );
    unsigned int h = 2166136261u;
    h = rc_hash( h, &r, sizeof(r) );
    h = rc_hash( h, &access, sizeof(access) );
    h = rc_hash( h, &attributes, sizeof(attributes) );
    return rc_hash( h, name, len );
}

/* ---- root handles ---- */

static int rc_base_index( struct rc_proc *p, HANDLE base )
{
    int i;
    for (i = 0; i < p->nbases; i++) if (rc_hval( p->bases[i] ) == rc_hval( base )) return i;
    return -1;
}

static int rc_base_add( struct rc_proc *p, HANDLE base )
{
    int i;
    if (!base) return 1;
    if ((i = rc_base_index( p, base )) >= 0)
    {
        p->base_refs[i]++;
        return 1;
    }
    if (p->nbases == RC_BASES) return 0;
    __atomic_store_n( &p->bases[p->nbases], base, __ATOMIC_RELAXED );
    p->base_refs[p->nbases] = 1;
    __atomic_store_n( &p->nbases, p->nbases + 1, __ATOMIC_RELEASE );
    return 1;
}

static void rc_base_remove_at( struct rc_proc *p, int i )
{
    int last = p->nbases - 1;
    __atomic_store_n( &p->bases[i], p->bases[last], __ATOMIC_RELAXED );
    p->base_refs[i] = p->base_refs[last];
    __atomic_store_n( &p->bases[last], (HANDLE)0, __ATOMIC_RELAXED );
    __atomic_store_n( &p->nbases, last, __ATOMIC_RELEASE );
}

static void rc_base_del( struct rc_proc *p, HANDLE base )
{
    int i;
    if (!base || (i = rc_base_index( p, base )) < 0) return;
    if (!--p->base_refs[i]) rc_base_remove_at( p, i );
}

/* lock-free hint for NtClose: may `h` be the root of cached opens? */
static int rc_maybe_base( struct rc_proc *p, HANDLE h )
{
    int i, n = __atomic_load_n( &p->nbases, __ATOMIC_ACQUIRE );
    for (i = 0; i < n && i < RC_BASES; i++)
        if (rc_hval( __atomic_load_n( &p->bases[i], __ATOMIC_RELAXED ) ) == rc_hval( h )) return 1;
    return 0;
}

/* ---- entries ---- */

static void rc_unlink_entry( struct rc_proc *p, int ei )
{
    struct rc_entry *e = &p->entries[ei];
    short *link = &p->ebuckets[e->hash % RC_EBUCKETS];
    while (*link >= 0 && *link != ei) link = &p->entries[*link].next;
    if (*link == ei) *link = e->next;
    e->next = -1;
}

/* a live entry stops being found; it stays allocated while something uses it */
static void rc_kill_entry( struct rc_proc *p, int ei )
{
    struct rc_entry *e = &p->entries[ei];
    if (!e->live) return;
    rc_unlink_entry( p, ei );
    rc_base_del( p, e->base );
    e->base = 0;
    e->live = 0;
    e->valid = 0;
    e->epoch++;     /* its values die with it */
}

static void rc_free_entry( struct rc_proc *p, int ei )
{
    struct rc_entry *e = &p->entries[ei];
    int parent = e->parent;
    rc_kill_entry( p, ei );
    e->used = 0;
    e->parent = -1;
    if (parent >= 0 && !--p->entries[parent].refs && !p->entries[parent].live) rc_free_entry( p, parent );
}

static int rc_find_entry( struct rc_proc *p, int parent, HANDLE root, ACCESS_MASK access, ULONG attributes,
                          const WCHAR *name, USHORT len, unsigned int hash )
{
    int ei;
    for (ei = p->ebuckets[hash % RC_EBUCKETS]; ei >= 0; ei = p->entries[ei].next)
    {
        struct rc_entry *e = &p->entries[ei];
        if (e->hash != hash || e->parent != parent || e->access != access || e->attributes != attributes ||
            e->name_len != len || (len && memcmp( e->name, name, len ))) continue;
        if (parent < 0 && rc_hval( e->root ) != rc_hval( root )) continue;
        return ei;
    }
    return -1;
}

static int rc_new_entry( struct rc_proc *p, int parent, HANDLE root, HANDLE base, ACCESS_MASK access,
                         ULONG attributes, const WCHAR *name, USHORT len, unsigned int hash )
{
    struct rc_entry *e;
    unsigned int n, ei = 0;

    for (n = 0; n < RC_ENTRIES; n++)
    {
        ei = p->eclock++ % RC_ENTRIES;
        e = &p->entries[ei];
        if (!e->used) break;
        if (!e->refs)
        {
            rc_free_entry( p, ei );
            if (!e->used) break;
        }
    }
    if (n == RC_ENTRIES)
    {
        p->st.full++;
        return -1;
    }
    if (!rc_base_add( p, base ))
    {
        p->st.full++;
        return -1;
    }
    e = &p->entries[ei];
    e->used = e->live = 1;
    e->valid = 0;
    e->parent = parent;
    e->root = parent < 0 ? root : 0;
    e->base = base;
    e->access = access;
    e->attributes = attributes;
    e->name_len = len;
    if (len) memcpy( e->name, name, len );
    e->hash = hash;
    e->opens = 0;
    e->refs = 0;
    e->epoch++;
    if (parent >= 0) p->entries[parent].refs++;
    e->next = p->ebuckets[hash % RC_EBUCKETS];
    p->ebuckets[hash % RC_EBUCKETS] = (short)ei;
    return (int)ei;
}

/* ---- cached handles ---- */

static int rc_alloc_slot( struct rc_proc *p, int ei, HANDLE real )
{
    unsigned int n, i;
    if (p->slots_used >= RC_SLOTS)
    {
        p->st.full++;
        return -1;
    }
    for (n = 0; n < RC_SLOTS; n++)
    {
        i = (p->shint + n) % RC_SLOTS;
        if (p->slots[i].used) continue;
        p->shint = i + 1;
        p->slots[i].used = 1;
        p->slots[i].entry = ei;
        p->slots[i].real = real;
        p->entries[ei].refs++;
        p->slots_used++;
        return (int)i;
    }
    p->st.full++;
    return -1;
}

static void rc_free_slot( struct rc_proc *p, int si )
{
    struct rc_slot *s = &p->slots[si];
    int ei = s->entry;
    s->used = 0;
    s->real = 0;
    s->entry = -1;
    p->slots_used--;
    if (!--p->entries[ei].refs && !p->entries[ei].live) rc_free_entry( p, ei );
}

/* open entry `ei` for real, through its chain of parents */
static NTSTATUS rc_open_chain( struct rc_proc *p, int ei, HANDLE *out )
{
    struct rc_entry *e = &p->entries[ei];
    HANDLE parent = e->root, tmp = 0;
    NTSTATUS status;

    *out = 0;
    if (e->parent >= 0)
    {
        if ((status = rc_open_chain( p, e->parent, &tmp ))) return status;
        parent = tmp;
    }
    status = rc_srv_open( out, parent, e->name, e->name_len, e->access, e->attributes );
    if (status && *out)
    {
        rc_srv_close( *out );
        *out = 0;
    }
    if (tmp) rc_srv_close( tmp );
    return status;
}

/* give cached handle `si` a real handle (lock held) */
static NTSTATUS rc_materialize( struct rc_proc *p, int si )
{
    struct rc_slot *s = &p->slots[si];
    NTSTATUS status;
    HANDLE real;

    if (s->real) return STATUS_SUCCESS;
    if (!p->entries[s->entry].live) return STATUS_KEY_DELETED;
    status = rc_open_chain( p, s->entry, &real );
    if (status == STATUS_OBJECT_NAME_NOT_FOUND || status == STATUS_OBJECT_PATH_NOT_FOUND)
        status = STATUS_KEY_DELETED;
    if (!status) s->real = real;
    p->st.materialized++;
    return status;
}

static int rc_cacheable_open_status( NTSTATUS status )
{
    return status == STATUS_SUCCESS || status == STATUS_OBJECT_NAME_NOT_FOUND ||
           status == STATUS_OBJECT_PATH_NOT_FOUND;
}

/* NtOpenKeyEx. `attributes` as sent to the server. 0: `root` is a handle in the
 * cached range that this process does not have; the caller sends the request
 * unchanged and gets the server's answer for it. */
static int rc_open( struct rc_proc *p, HANDLE *key, HANDLE root, const WCHAR *name, USHORT len,
                    ACCESS_MASK access, ULONG attributes, ULONG options, NTSTATUS *status )
{
    int rslot = -1, parent = -1, ei = -1, si, cacheable, snapped = 0, created = 0;
    HANDLE real_root = root, base = root, h = 0;
    unsigned int hash = 0;
    long long gen = 0;

    pthread_mutex_lock( &p->lock );
    if (rc_is_virtual( root ) && (rslot = rc_slot_of( p, root )) < 0)
    {
        pthread_mutex_unlock( &p->lock );
        return 0;
    }
    /* read-only opens only: a key opened for writing is likely written through,
     * and when in doubt the cache stays out of the way */
    cacheable = p->cache && !options && !(attributes & ~(ULONG)OBJ_CASE_INSENSITIVE) &&
                !(access & ~(ACCESS_MASK)RC_READ_ACCESS) &&
                !(len & 1) && len <= sizeof(((struct rc_entry *)0)->name);
    if (rslot >= 0)
    {
        parent = p->slots[rslot].entry;
        if (!p->entries[parent].live) cacheable = 0;
        else base = p->entries[parent].base;
        root = 0;
    }
    if (cacheable)
    {
        hash = rc_entry_hash( parent, root, access, attributes, name, len );
        ei = rc_find_entry( p, parent, root, access, attributes, name, len, hash );
        if (ei >= 0)
        {
            struct rc_entry *e = &p->entries[ei];
            e->opens++;
            if (e->valid && rc_still( e->gen ))
            {
                if (e->status)
                {
                    *key = 0;
                    *status = e->status;
                    p->st.open_neg_hit++;
                    pthread_mutex_unlock( &p->lock );
                    return 1;
                }
                if (e->opens > p->hot && (si = rc_alloc_slot( p, ei, 0 )) >= 0)
                {
                    *key = RC_HANDLE( si );
                    *status = STATUS_SUCCESS;
                    p->st.open_hit++;
                    pthread_mutex_unlock( &p->lock );
                    return 1;
                }
            }
        }
    }

    if (rslot >= 0)
    {
        if ((*status = rc_materialize( p, rslot )))
        {
            *key = 0;
            pthread_mutex_unlock( &p->lock );
            return 1;
        }
        real_root = p->slots[rslot].real;
    }
    if (cacheable) snapped = rc_snap( &gen );
    *status = rc_srv_open( &h, real_root, name, len, access, attributes );
    if (cacheable && ei < 0)
    {
        ei = rc_new_entry( p, parent, root, base, access, attributes, name, len, hash );
        created = ei >= 0;
    }
    if (cacheable && ei >= 0)
    {
        struct rc_entry *e = &p->entries[ei];
        if (created) e->opens = 1;
        if (snapped && rc_cacheable_open_status( *status ) && rc_still( gen ))
        {
            e->valid = 1;
            e->gen = gen;
            e->status = *status;
        }
        else e->valid = 0;
        if (!*status && e->opens > p->hot && (si = rc_alloc_slot( p, ei, h )) >= 0) h = RC_HANDLE( si );
        p->st.open_miss++;
    }
    else p->st.open_pass++;
    *key = h;
    pthread_mutex_unlock( &p->lock );
    return 1;
}

/* ---- values ---- */

static unsigned int rc_value_hash( int ei, unsigned int epoch, const WCHAR *name, USHORT len )
{
    unsigned int h = 2166136261u;
    h = rc_hash( h, &ei, sizeof(ei) );
    h = rc_hash( h, &epoch, sizeof(epoch) );
    return rc_hash( h, name, len );
}

static int rc_find_value( struct rc_proc *p, int ei, unsigned int epoch, const WCHAR *name, USHORT len,
                          unsigned int hash )
{
    int vi;
    for (vi = p->vbuckets[hash % RC_VBUCKETS]; vi >= 0; vi = p->values[vi].next)
    {
        struct rc_value *v = &p->values[vi];
        if (v->hash == hash && v->entry == ei && v->epoch == epoch && v->name_len == len &&
            (!len || !memcmp( v->name, name, len ))) return vi;
    }
    return -1;
}

static void rc_unlink_value( struct rc_proc *p, int vi )
{
    struct rc_value *v = &p->values[vi];
    short *link = &p->vbuckets[v->hash % RC_VBUCKETS];
    while (*link >= 0 && *link != vi) link = &p->values[*link].next;
    if (*link == vi) *link = v->next;
    v->next = -1;
    v->used = 0;
}

static void rc_store_value( struct rc_proc *p, int vi, int ei, unsigned int epoch, const WCHAR *name,
                            USHORT len, unsigned int hash, long long gen, NTSTATUS status, ULONG type,
                            ULONG total, const void *data )
{
    struct rc_value *v;

    if (vi < 0)
    {
        vi = (int)(p->vclock++ % RC_VALUES);
        if (p->values[vi].used) rc_unlink_value( p, vi );
        v = &p->values[vi];
        v->used = 1;
        v->entry = (short)ei;
        v->epoch = epoch;
        v->hash = hash;
        v->name_len = len;
        if (len) memcpy( v->name, name, len );
        v->next = p->vbuckets[hash % RC_VBUCKETS];
        p->vbuckets[hash % RC_VBUCKETS] = (short)vi;
    }
    v = &p->values[vi];
    v->gen = gen;
    v->big = total > RC_DATA_MAX && !status;
    v->status = status;
    v->type = status ? 0 : type;
    v->total = status ? 0 : total;
    v->data_len = (status || v->big) ? 0 : (unsigned short)total;
    if (v->data_len) memcpy( v->data, data, v->data_len );
}

/* NtQueryValueKey. 1: answered here (*status, and on success *type, *total and
 * min(total, max) bytes of data). 0: not a cached handle, or not cacheable;
 * *handle is now the real handle to send the request with. */
static int rc_query( struct rc_proc *p, HANDLE *handle, const WCHAR *name, USHORT len, ULONG *type,
                     ULONG *total, void *data, ULONG max, NTSTATUS *status )
{
    struct rc_slot *s;
    struct rc_entry *e;
    BYTE buf[RC_DATA_MAX];
    ULONG t = 0, tot = 0;
    unsigned int hash;
    long long gen = 0;
    int si, vi, snapped;

    if (!rc_is_virtual( *handle )) return 0;
    pthread_mutex_lock( &p->lock );
    if ((si = rc_slot_of( p, *handle )) < 0)
    {
        pthread_mutex_unlock( &p->lock );
        return 0;
    }
    s = &p->slots[si];
    e = &p->entries[s->entry];
    if (!p->cache || !e->live || (len & 1) || len > sizeof(((struct rc_value *)0)->name))
    {
        p->st.val_pass++;
        if (!(*status = rc_materialize( p, si ))) *handle = s->real;
        pthread_mutex_unlock( &p->lock );
        return *status != STATUS_SUCCESS;
    }
    hash = rc_value_hash( s->entry, e->epoch, name, len );
    vi = rc_find_value( p, s->entry, e->epoch, name, len, hash );
    if (vi >= 0 && rc_still( p->values[vi].gen ))
    {
        struct rc_value *v = &p->values[vi];
        if (v->big)
        {
            p->st.val_big++;
            if (!(*status = rc_materialize( p, si ))) *handle = s->real;
            pthread_mutex_unlock( &p->lock );
            return *status != STATUS_SUCCESS;
        }
        if (!(*status = v->status))
        {
            *type = v->type;
            *total = v->total;
            if (max && v->data_len) memcpy( data, v->data, max < v->data_len ? max : v->data_len );
        }
        p->st.val_hit++;
        pthread_mutex_unlock( &p->lock );
        return 1;
    }

    if ((*status = rc_materialize( p, si )))
    {
        pthread_mutex_unlock( &p->lock );
        return 1;
    }
    snapped = rc_snap( &gen );
    *status = rc_srv_query( s->real, name, len, &t, &tot, buf, sizeof(buf) );
    if (!*status && tot > sizeof(buf))
    {
        /* too big to keep: the caller asks the server with its own buffer, and
         * so will the next read of it while nothing has been written */
        if (snapped && rc_still( gen ))
            rc_store_value( p, vi, s->entry, e->epoch, name, len, hash, gen, STATUS_SUCCESS, t, tot, NULL );
        p->st.val_big++;
        *handle = s->real;
        pthread_mutex_unlock( &p->lock );
        return 0;
    }
    if ((*status == STATUS_SUCCESS || *status == STATUS_OBJECT_NAME_NOT_FOUND) && snapped && rc_still( gen ))
        rc_store_value( p, vi, s->entry, e->epoch, name, len, hash, gen, *status, t, tot, buf );
    if (!*status)
    {
        *type = t;
        *total = tot;
        if (max && tot) memcpy( data, buf, max < tot ? max : tot );
    }
    p->st.val_miss++;
    pthread_mutex_unlock( &p->lock );
    return 1;
}

/* ---- everything else ---- */

/* a cached handle becomes its real handle (materialized); other handles pass */
static NTSTATUS rc_real( struct rc_proc *p, HANDLE *handle )
{
    NTSTATUS status = STATUS_SUCCESS;
    int si;

    if (!rc_is_virtual( *handle )) return STATUS_SUCCESS;
    pthread_mutex_lock( &p->lock );
    if ((si = rc_slot_of( p, *handle )) >= 0 && !(status = rc_materialize( p, si )))
        *handle = p->slots[si].real;
    pthread_mutex_unlock( &p->lock );
    return status;
}

/* NtClose. 1: a cached handle, closed here (*ret). 2: the root of cached opens;
 * they have been dropped and the lock is held until rc_close_done, after the
 * caller has closed the handle. 0: nothing to do with the cache. */
static int rc_close( struct rc_proc *p, HANDLE h, NTSTATUS *ret )
{
    int si, i, b;

    if (rc_is_virtual( h ))
    {
        pthread_mutex_lock( &p->lock );
        if ((si = rc_slot_of( p, h )) < 0)
        {
            pthread_mutex_unlock( &p->lock );
            return 0;
        }
        *ret = STATUS_SUCCESS;
        if (p->slots[si].real)
        {
            *ret = rc_srv_close( p->slots[si].real );
            if (*ret == STATUS_HANDLE_NOT_CLOSABLE)
            {
                pthread_mutex_unlock( &p->lock );
                return 1;
            }
            *ret = STATUS_SUCCESS;
        }
        rc_free_slot( p, si );
        p->st.closes++;
        pthread_mutex_unlock( &p->lock );
        return 1;
    }
    if (!h || !rc_maybe_base( p, h )) return 0;
    pthread_mutex_lock( &p->lock );
    if ((b = rc_base_index( p, h )) < 0)
    {
        pthread_mutex_unlock( &p->lock );
        return 0;
    }
    /* the cached handles below it get real handles while it still exists */
    for (si = 0; si < RC_SLOTS; si++)
    {
        struct rc_slot *s = &p->slots[si];
        if (s->used && !s->real && p->entries[s->entry].live &&
            rc_hval( p->entries[s->entry].base ) == rc_hval( h ))
            rc_materialize( p, si );
    }
    for (i = 0; i < RC_ENTRIES; i++)
    {
        struct rc_entry *e = &p->entries[i];
        if (e->used && e->live && rc_hval( e->base ) == rc_hval( h ))
        {
            rc_kill_entry( p, i );
            if (!e->refs) rc_free_entry( p, i );
        }
    }
    if ((b = rc_base_index( p, h )) >= 0) rc_base_remove_at( p, b );
    p->st.root_closes++;
    return 2;
}

static void rc_close_done( struct rc_proc *p )
{
    pthread_mutex_unlock( &p->lock );
}

/* NtDuplicateObject with DUPLICATE_CLOSE_SOURCE took the real handle of cached handle
 * `h` (rc_real gave it); forget it without closing it again */
static void rc_forget( struct rc_proc *p, HANDLE h )
{
    int si;
    pthread_mutex_lock( &p->lock );
    if ((si = rc_slot_of( p, h )) >= 0)
    {
        rc_free_slot( p, si );
        p->st.closes++;
    }
    pthread_mutex_unlock( &p->lock );
}
