#!/usr/bin/env python3
"""madeira-d3d12 air-table-inc, run from production source.

A table miss after a root change copies the cached stage's tables and rewrites
only the entries that can differ (mad_air_build_tables_inc). Checked here with
random root signatures, declarations, heaps, root arguments, root constants,
descriptor writes, heap and signature switches, view-map generations and UAV
counters, over long replays of several pipeline stages:

- equivalence: every table a replay binds with air-table-inc = 1 carries the
  same words as a whole build of that draw (air-table-cache = 0), constant
  buffers made from root constants compared by content; refusals agree, with
  the same reason. While view-map answers and UAV counters stay fixed this
  holds for every draw; while they move, for every draw that missed the cache
  (a hit binds a cached table, as before this change);
- copy on write: no table bound earlier in the list changes afterwards;
- off switch: air-table-inc = 0 gives the same ring allocations, offsets and
  bytes as the builder before this change (taken from git HEAD while it does
  not have air-table-inc, else from this file's .oldex.c copy);
- no air-plan: whole builds only;
- the counters behind the [perf] air-inc line add up."""
from pathlib import Path
import subprocess
import tempfile
import sys

root = Path(__file__).resolve().parents[2]
source = (root / 'madeira-d3d12/src/pe/madeira_d3d12.c').read_text()

def cut(text, begin, end, start=0):
    b = text.index(begin, start)
    return text[b:text.index(end, b)]

versions = cut(source, 'static SRWLOCK g_descriptor_heaps_lock', '/* An RTV or DSV handle')
cache = cut(source, '    struct {\n        const struct mad_rootsig *rs;', '    obj_handle_t cenc;', source.index('UINT64 root_version[2]'))
types = cut(source, 'static UINT mad_air_range_type(', '/* Resolve one declaration range')
routes = cut(source, 'enum { MAD_AR_NONE', "/* ml1011: the DXBC backend's vertex-buffer table")
plans = cut(source, '#define MAD_AIR_PLANS_MAX', 'static unsigned g_air_said_fail;')
tables = cut(source, 'static unsigned g_air_said_fail;', 'static int mad_air_build_tables(struct mad_exec')
typed = cut(source, '#define MAD_DESC_TYPEDBUF', '\n')
assert 'mad_air_build_tables_inc(' in tables and 'g_air_inc_on' in tables

# The builder before air-table-inc, for the off-switch check.
OLD_BEGIN = 'static int mad_air_build_tables_ex_body('
old = None
try:
    head = subprocess.run(['git', '-C', str(root), 'show', 'HEAD:madeira-d3d12/src/pe/madeira_d3d12.c'],
                          capture_output=True, text=True, check=True).stdout
    if 'air-table-inc' not in head:
        old = cut(head, OLD_BEGIN, 'static int mad_air_build_tables(struct mad_exec')
except (subprocess.CalledProcessError, ValueError):
    pass
fixture = Path(__file__).with_suffix('.oldex.c')
if old is None:
    old = fixture.read_text()
else:
    fixture.write_text(old)
for name in ('mad_air_build_tables_ex_body', 'mad_air_build_tables_ex', 'mad_air_root_mask', 'mad_air_roots_equal'):
    old = old.replace(name + '(', 'old_' + name + '(')

harness = r'''
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <stdarg.h>
#include <assert.h>
#include <pthread.h>
#include "madeira_ir_abi.h"
#define MAD_ROOT_PARAM_MAX 32
#define MAD_ARG_RING_BYTES  (64u * 1024u)
#define MAD_ARG_SLOT_BYTES  1088u
typedef uint64_t UINT64, obj_handle_t;
typedef uint32_t UINT, UINT32;
typedef uint16_t UINT16;
typedef uint8_t UINT8;
typedef int64_t LONG64;
typedef int32_t LONG;
typedef int D3D12_DESCRIPTOR_HEAP_TYPE;
enum { D3D12_DESCRIPTOR_HEAP_TYPE_CBV_SRV_UAV = 0, D3D12_DESCRIPTOR_HEAP_TYPE_SAMPLER = 1 };
typedef pthread_mutex_t SRWLOCK;
#define SRWLOCK_INIT PTHREAD_MUTEX_INITIALIZER
#define AcquireSRWLockExclusive pthread_mutex_lock
#define ReleaseSRWLockExclusive pthread_mutex_unlock
#define AcquireSRWLockShared pthread_mutex_lock
#define ReleaseSRWLockShared pthread_mutex_unlock
static LONG64 InterlockedIncrement64(volatile LONG64 *p) { return __atomic_add_fetch(p, 1, __ATOMIC_SEQ_CST); }
static LONG InterlockedIncrement(volatile LONG *p) { return __atomic_add_fetch(p, 1, __ATOMIC_SEQ_CST); }
static LONG64 InterlockedExchangeAdd64(volatile LONG64 *p, LONG64 v) { return __atomic_fetch_add(p, v, __ATOMIC_SEQ_CST); }
static LONG64 InterlockedCompareExchange64(volatile LONG64 *p, LONG64 x, LONG64 c) {
    __atomic_compare_exchange_n(p, &c, x, 0, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST); return c; }
''' + typed + r'''
struct mad_descriptor { UINT64 gpu_va, texture_view_id, metadata; };
struct mad_heap { int shader_visible; struct mad_descriptor *cpu; UINT count; int type;
    volatile LONG64 table_version; struct mad_heap *table_version_next; UINT64 gpu_address; };
''' + versions + r'''
struct mad_rootsig { struct madeira_ir_root_param params[MAD_ROOT_PARAM_MAX]; struct madeira_ir_root_range *ranges;
    UINT nparams, nranges; struct madeira_ir_static_sampler samplers[32]; UINT nsamplers;
    const struct mad_descriptor *stab_cpu; UINT64 serial; };
struct mad_air_plan;
struct mad_pso { struct mad_air_plan *volatile air_plans; volatile LONG air_nplans; const char *vs_name; };
struct mad_device { volatile LONG64 vmap_gen; UINT64 null_gpu; };
struct mad_queue { struct mad_device *device; };
struct skin { int tab_on; };
struct mad_exec { struct mad_queue *q; struct mad_heap *srv, *smp; struct skin *sk;
    UINT64 root[MAD_ROOT_PARAM_MAX], croot[MAD_ROOT_PARAM_MAX];
    UINT64 root_version[2], root_param_version[2][MAD_ROOT_PARAM_MAX];
''' + cache + r'''
    int ring_id;
};
/* ---- the argument ring: per exec, 64 KB chunks of 1088-byte slots, as exec_ring_take_z ---- */
#define NCHUNK 192
#define RING_GPU 0x7000000000ull
static unsigned char *ring_mem[6]; static unsigned ring_used[6]; static int ring_fail;
static int exec_ring_take_z(struct mad_exec *e, unsigned n, size_t z, obj_handle_t *buf, UINT64 *off, void **cpu, UINT64 *gpu) {
    const unsigned per = MAD_ARG_RING_BYTES / MAD_ARG_SLOT_BYTES;
    unsigned chunk, slot, *u = &ring_used[e->ring_id];
    size_t i;
    if (!n || n > per || ring_fail) return 0;
    if ((*u % per) + n > per) *u += per - (*u % per);
    chunk = *u / per; slot = *u % per;
    if (chunk >= NCHUNK) { fprintf(stderr, "test ring exhausted\n"); exit(2); }
    *cpu = ring_mem[e->ring_id] + (size_t)chunk * MAD_ARG_RING_BYTES + (size_t)slot * MAD_ARG_SLOT_BYTES;
    for (i = 0; i < (size_t)n * MAD_ARG_SLOT_BYTES; i++)   /* what the slot held before: garbage, the same in every exec */
        ((unsigned char *)*cpu)[i] = (unsigned char)((chunk * 131u + slot * 31u + i * 7u) ^ 0x5a);
    if (z > (size_t)n * MAD_ARG_SLOT_BYTES) z = (size_t)n * MAD_ARG_SLOT_BYTES;
    memset(*cpu, 0, z);
    *buf = 1 + chunk; *off = (UINT64)slot * MAD_ARG_SLOT_BYTES; *gpu = RING_GPU + (UINT64)chunk * MAD_ARG_RING_BYTES;
    *u += n;
    return 1;
}
static void ring_reset(void) { memset(ring_used, 0, sizeof ring_used); }
static unsigned char *ring_ptr(int id, obj_handle_t buf, UINT64 off) {
    assert(buf >= 1 && buf <= NCHUNK && off < MAD_ARG_RING_BYTES);
    return ring_mem[id] + (size_t)(buf - 1) * MAD_ARG_RING_BYTES + off;
}
/* ---- what the builder reads besides heaps and roots ---- */
static int moving;            /* phase B: view-map answers follow vmap_gen, UAV counters follow ctr_epoch */
static UINT64 ctr_epoch;
static struct mad_device dev = { 1, 0x6600000 };
static UINT64 mix(UINT64 x) { x ^= x >> 31; x *= 0x9E3779B97F4A7C15ull; x ^= x >> 29; return x; }
static int mad_air_texbuf_c(struct mad_exec *e, struct mad_device *d, UINT64 id, UINT32 *count, UINT32 *first) {
    UINT64 h = mix(id ^ (moving ? (UINT64)d->vmap_gen << 40 : 0)); (void)e;
    if (!id || (h % 97) == 0) return 0;   /* "typed buffer view not found" */
    *count = (UINT32)h; *first = (UINT32)(h >> 32); return 1;
}
static void mad_air_texinfo_c(struct mad_exec *e, struct mad_device *d, UINT64 id, int flat, UINT64 *vid, UINT32 *alen) {
    UINT64 h = mix(id * 3 + (UINT64)flat + (moving ? (UINT64)d->vmap_gen << 40 : 0)); (void)e;
    *vid = flat ? h | 1 : id; *alen = 1 + (UINT32)(h % 6);
}
static UINT64 mad_uavctr_get(UINT64 va) { return va & 3 ? 0 : mix(va + (moving ? ctr_epoch : 0)) | 1; }
static int g_vis_fallback, g_null_cbv_bound, g_dump_tables, g_replay_split, g_flat_views = -1;
static void mad_skin_tok(struct mad_exec *e, const struct madeira_ir_air_range *rg, const struct mad_descriptor *de, UINT64 d) { (void)e; (void)rg; (void)de; (void)d; }
static const char *last_why; static unsigned nwhy;
static void mad_skip_why(const char *w) { last_why = w; nwhy++; }
static unsigned nlog;
static void d3d12_log(const char *f, ...) { (void)f; nlog++; }
static int cfg_plan = 1;
static long long mad_cfg_int_pe(const char *k, long long d) { if (!strcmp(k, "air-plan")) return cfg_plan; return d; }
static UINT64 mad_tick(void) { return 0; }
enum { RS_TABLES };
static void mad_rs_step(int a, UINT64 *b) { (void)a; (void)b; }
static volatile LONG64 g_air_cache_hits, g_air_cache_misses, g_air_cache_root_reuse, g_air_miss_new, g_air_miss_heap, g_air_miss_root;
static volatile LONG64 g_ai_full, g_ai_full_ent, g_ai_full_bytes, g_ai_inc, g_ai_inc_ent, g_ai_inc_kept, g_ai_inc_bytes, g_ai_inc_tabkept, g_ai_inc_none;
static volatile LONG g_air_plans_built;
static int g_air_cache_on = -1;
''' + types + routes + plans + tables + old + r'''
static struct { const char *w; unsigned long n; } whys[32];
static uint64_t rng = 0x243F6A8885A308D3ull;
static unsigned rnd(unsigned n) { rng ^= rng << 13; rng ^= rng >> 7; rng ^= rng << 17; return n ? (unsigned)(rng % n) : 0; }

#define NSTAGE 4
struct stage { struct mad_pso *pso; struct madeira_ir_air_range air[8]; UINT nair, cb_bind, arg_bind, arg_qwords, vis; int compute; };
enum { X_REF, X_INC, X_OFF, X_HEAD, NX };
static struct mad_exec ex[NX];
static struct skin no_skin;

/* master replay state */
static struct mad_rootsig rsv[2][2];            /* [graphics/compute][which] */
static int rs_cur[2];
static struct madeira_ir_root_range ranges[2][2][96];
static UINT64 mroot[2][MAD_ROOT_PARAM_MAX], mver[2], mpver[2][MAD_ROOT_PARAM_MAX];
static UINT32 mconst[2][MAD_ROOT_PARAM_MAX][64];
static struct mad_descriptor hsrv[2][64], hsmp[16], stab[32];
static struct mad_heap srv[2], smp;
static int srv_cur;

static void random_rs(struct mad_rootsig *rs, struct madeira_ir_root_range *rg, unsigned cap, UINT64 serial) {
    unsigned i, j, nr = 0;
    memset(rs, 0, sizeof *rs);
    rs->ranges = rg; rs->serial = serial; rs->stab_cpu = stab;
    rs->nparams = 2 + rnd(12);
    for (i = 0; i < rs->nparams; i++) {
        struct madeira_ir_root_param *p = &rs->params[i];
        unsigned t = rnd(10);
        p->type = t < 5 ? MADEIRA_IR_PARAM_TABLE : t < 7 ? MADEIRA_IR_PARAM_CONSTANTS : t < 9 || rnd(4) ? MADEIRA_IR_PARAM_CBV : (rnd(2) ? MADEIRA_IR_PARAM_SRV : MADEIRA_IR_PARAM_UAV);
        p->visibility = rnd(5) == 0 ? 40 : rnd(6);
        p->shader_register = rnd(6); p->register_space = rnd(2);
        p->num_constants = 1 + rnd(70);
        if (p->type == MADEIRA_IR_PARAM_TABLE) {
            int smp_table = rnd(4) == 0;   /* a table holds samplers or views, as D3D12 requires (now and then mixed) */
            int mixed = rnd(30) == 0;
            p->first_range = nr; p->num_ranges = 1 + rnd(4);
            for (j = 0; j < p->num_ranges && nr < cap; j++, nr++) {
                struct madeira_ir_root_range *r = &rg[nr];
                r->range_type = mixed ? rnd(4) : smp_table ? MADEIRA_IR_RANGE_SAMPLER : (MADEIRA_IR_RANGE_SAMPLER + 1 + rnd(3)) % 4; r->base_register = rnd(6); r->register_space = rnd(2);
                r->num_descriptors = rnd(6) == 0 ? ~0u : 1 + rnd(4);
                r->table_offset = rnd(3) == 0 ? 0xffffffffu : rnd(10);
            }
        }
    }
    rs->nranges = nr;
    rs->nsamplers = rnd(4);
    for (i = 0; i < rs->nsamplers; i++) {
        rs->samplers[i].shader_register = rnd(6); rs->samplers[i].register_space = rnd(2);
        rs->samplers[i].visibility = rnd(4) == 0 ? 33 : rnd(6);
    }
}
static UINT air_of_range(UINT range_type) {
    UINT a;
    for (a = 0; a < 4; a++) if (mad_air_range_type(a) == range_type) return a;
    return MADEIRA_IR_AIR_SRV;
}
static unsigned width(const struct madeira_ir_air_range *a) {
    if (a->type == MADEIRA_IR_AIR_CBV) return 1;
    if (a->type == MADEIRA_IR_AIR_SAMPLER) return 3;
    return (a->flags & MADEIRA_IR_AIR_F_UAV_COUNTER) ? 3 : 2;
}
static void random_stage(struct stage *st, const struct mad_rootsig *rs, struct mad_pso *pso, int compute, unsigned vis) {
    unsigned k, cbo = 0, ago = rnd(8) == 0 ? 130 + rnd(20) : 0;   /* now and then an argument table past one slot */
    memset(st->air, 0, sizeof st->air);
    st->pso = pso; st->compute = compute; st->vis = vis;
    st->nair = 1 + rnd(rnd(3) ? 5 : 8);
    st->cb_bind = rnd(6) ? 1 : ~0u; st->arg_bind = 2;
    for (k = 0; k < st->nair; k++) {
        struct madeira_ir_air_range *a = &st->air[k];
        unsigned pi = rnd(rs->nparams);
        const struct madeira_ir_root_param *p = &rs->params[pi];
        if (rnd(50) < 49 && p->type == MADEIRA_IR_PARAM_TABLE && rs->nranges > p->first_range) {
            const struct madeira_ir_root_range *r = &rs->ranges[p->first_range + rnd(p->num_ranges) % (rs->nranges - p->first_range)];
            a->type = air_of_range(r->range_type); a->space = r->register_space;
            a->lower_bound = r->base_register + rnd(r->num_descriptors == ~0u ? 3 : r->num_descriptors);
        } else if (rnd(50) < 49 && p->type != MADEIRA_IR_PARAM_TABLE) {
            a->type = p->type == MADEIRA_IR_PARAM_SRV ? MADEIRA_IR_AIR_SRV : p->type == MADEIRA_IR_PARAM_UAV ? MADEIRA_IR_AIR_UAV : MADEIRA_IR_AIR_CBV;
            a->space = p->register_space; a->lower_bound = p->shader_register;
        } else { a->type = rnd(4); a->space = rnd(2); a->lower_bound = rnd(8); }
        if (a->type == MADEIRA_IR_AIR_SRV || a->type == MADEIRA_IR_AIR_UAV) {
            unsigned f = rnd(20);
            a->flags = f < 6 ? MADEIRA_IR_AIR_F_BUFFER : f < 12 ? MADEIRA_IR_AIR_F_TEXTURE : f < 16 ? (MADEIRA_IR_AIR_F_TEXTURE | MADEIRA_IR_AIR_F_TEXTURE_ARRAY)
                     : f < 19 ? (MADEIRA_IR_AIR_F_BUFFER | MADEIRA_IR_AIR_F_UAV_COUNTER) : rnd(5) ? MADEIRA_IR_AIR_F_TEXTURE : 0;
            if (f >= 16 && f < 19) a->type = MADEIRA_IR_AIR_UAV;
        }
        a->cb_table = a->type == MADEIRA_IR_AIR_CBV && st->cb_bind != ~0u && rnd(3) != 0;
        if (rnd(40) == 0) a->cb_table = 1, st->cb_bind = rnd(2) ? st->cb_bind : ~0u;   /* the table it belongs to may be missing */
        if (a->cb_table) { a->ptr_offset = cbo; cbo += width(a) + rnd(2); }
        else { a->ptr_offset = ago; ago += width(a) + rnd(2); }
        a->range_id = k;
    }
    if (rnd(30) == 0 && st->nair > 1) {   /* entries sharing a word: the builder must not build such a stage incrementally */
        k = 1 + rnd(st->nair - 1); st->air[k].cb_table = st->air[0].cb_table; st->air[k].ptr_offset = st->air[0].ptr_offset + rnd(width(&st->air[0]));
        if (!st->air[0].cb_table && st->air[k].ptr_offset + width(&st->air[k]) > ago) ago = st->air[k].ptr_offset + width(&st->air[k]);
    }
    st->arg_qwords = ago ? ago - ((ago - 1) % 136 ? rnd(2) : 0) : 1;   /* sometimes one word short of the last entry (inside its slot) */
    if (!st->arg_qwords) st->arg_qwords = 1;
}
static void bump(int bp, unsigned i) { mver[bp]++; mpver[bp][i]++; }
static void set_root(int bp, unsigned i, UINT64 v) { if (mroot[bp][i] != v) { mroot[bp][i] = v; bump(bp, i); } }
static UINT64 random_root(int bp, unsigned pi) {   /* mostly what the parameter wants, now and then not */
    const struct mad_rootsig *rs = &rsv[bp][rs_cur[bp]];
    unsigned r = rnd(40);
    if (r == 0) return 0;
    if (r == 1) return srv[0].gpu_address - 24;
    if (r == 2) return srv[srv_cur].gpu_address + 5 * 24 + 4;
    if (r == 3) return srv[rnd(2)].gpu_address + rnd(70) * 24;
    if (r == 4) return smp.gpu_address + rnd(18) * 24;
    if (pi < rs->nparams && rs->params[pi].type == MADEIRA_IR_PARAM_TABLE) {
        if (rs->params[pi].first_range < rs->nranges && rs->ranges[rs->params[pi].first_range].range_type == MADEIRA_IR_RANGE_SAMPLER)
            return smp.gpu_address + rnd(3) * 24;
        return srv[rnd(8) ? srv_cur : srv_cur ^ 1].gpu_address + rnd(40) * 24;
    }
    return 0xabc000 + 256 * rnd(64);
}
static void random_desc(struct mad_descriptor *d, int sampler) {
    unsigned r = rnd(12);
    d->gpu_va = r == 0 ? 0 : 0x500000 + rnd(4000) * 4;
    d->texture_view_id = rnd(6) ? 1 + rnd(5000) : 0;
    d->metadata = (UINT64)rnd(1u << 30) | (!sampler && rnd(5) == 0 ? MAD_DESC_TYPEDBUF : 0);
}
static void sync_exec(struct mad_exec *e) {
    memcpy(e->root, mroot[0], sizeof e->root); memcpy(e->croot, mroot[1], sizeof e->croot);
    memcpy(e->root_version, mver, sizeof mver); memcpy(e->root_param_version, mpver, sizeof mpver);
    e->srv = &srv[srv_cur]; e->smp = &smp;
}
struct res { int ok; obj_handle_t cb, arg; UINT64 cbo, argo; const char *why; unsigned nwhy; };
static struct res build(int x, const struct stage *st) {
    struct mad_exec *e = &ex[x]; struct res r = { 0 };
    int bp = st->compute;
    const struct mad_rootsig *rs = &rsv[bp][rs_cur[bp]];
    sync_exec(e);
    last_why = NULL; nwhy = 0;
    if (x == X_REF) g_air_cache_on = 0; else g_air_cache_on = 1;
    g_air_inc_on = x == X_INC ? 1 : 0;
    if (x == X_HEAD)
        r.ok = old_mad_air_build_tables_ex(e, rs, bp ? e->croot : e->root, (const UINT32 (*)[64])mconst[bp], st->pso, st->cb_bind, st->arg_bind,
                                           st->arg_qwords, st->nair, st->air, st->vis, &r.cb, &r.cbo, &r.arg, &r.argo);
    else
        r.ok = mad_air_build_tables_ex(e, rs, bp ? e->croot : e->root, (const UINT32 (*)[64])mconst[bp], st->pso, st->cb_bind, st->arg_bind,
                                       st->arg_qwords, st->nair, st->air, st->vis, &r.cb, &r.cbo, &r.arg, &r.argo);
    r.why = last_why; r.nwhy = nwhy;
    return r;
}
/* the words a draw's table carries: every word below the cleared size, every word an entry writes;
 * a word pointing into the ring (a root-constant buffer) compares by the 256 bytes it points at */
static int words_of(const struct stage *st, int cbt, unsigned w) {
    unsigned k;
    if (cbt) {
        size_t cbz = 8;
        for (k = 0; k < st->nair; k++) if (st->air[k].cb_table && ((size_t)st->air[k].ptr_offset + 3) * 8 > cbz) cbz = ((size_t)st->air[k].ptr_offset + 3) * 8;
        if (cbz > MAD_ARG_SLOT_BYTES) cbz = MAD_ARG_SLOT_BYTES;
        if ((size_t)w * 8 < cbz) return 1;
    } else if (w < st->arg_qwords) return 1;
    for (k = 0; k < st->nair; k++)
        if (!st->air[k].cb_table == !cbt && w >= st->air[k].ptr_offset && w < st->air[k].ptr_offset + width(&st->air[k])) return 1;
    return 0;
}
static unsigned table_words(const struct stage *st, int cbt) {
    unsigned k, hi = cbt ? 1 : st->arg_qwords;
    for (k = 0; k < st->nair; k++) if (!st->air[k].cb_table == !cbt && st->air[k].ptr_offset + width(&st->air[k]) > hi) hi = st->air[k].ptr_offset + width(&st->air[k]);
    if (cbt) { size_t cbz = 8; for (k = 0; k < st->nair; k++) if (st->air[k].cb_table && ((size_t)st->air[k].ptr_offset + 3) * 8 > cbz) cbz = ((size_t)st->air[k].ptr_offset + 3) * 8;
               if (cbz / 8 > hi) hi = (unsigned)(cbz / 8); }
    return hi;
}
static int same_table(const struct stage *st, int cbt, int xa, obj_handle_t ba, UINT64 oa, int xb, obj_handle_t bb, UINT64 ob) {
    const UINT64 *a, *b; unsigned w, n = table_words(st, cbt);
    if (!ba || !bb) return !ba && !bb;
    a = (const UINT64 *)ring_ptr(xa, ba, oa); b = (const UINT64 *)ring_ptr(xb, bb, ob);
    for (w = 0; w < n; w++) {
        if (!words_of(st, cbt, w)) continue;
        if (a[w] >= RING_GPU && a[w] < RING_GPU + (UINT64)NCHUNK * MAD_ARG_RING_BYTES &&
            b[w] >= RING_GPU && b[w] < RING_GPU + (UINT64)NCHUNK * MAD_ARG_RING_BYTES) {
            UINT64 ra = a[w] - RING_GPU, rb = b[w] - RING_GPU;
            if (memcmp(ring_mem[xa] + ra, ring_mem[xb] + rb, 256)) return 0;
            continue;
        }
        if (a[w] != b[w]) return 0;
    }
    return 1;
}
/* copy on write: tables bound earlier never change */
struct seen { const struct stage *st; int cbt; const UINT64 *p; UINT64 h; };
static struct seen seen[40000]; static unsigned nseen;
static UINT64 hash(const struct stage *st, int cbt, const UINT64 *p) {   /* the words the draw's table carries */
    UINT64 h = 1469598103934665603ull; unsigned w, n = table_words(st, cbt);
    for (w = 0; w < n; w++) if (words_of(st, cbt, w)) h = (h ^ p[w]) * 1099511628211ull;
    return h;
}
static void remember(int x, const struct stage *st, const struct res *r) {
    if (!r->ok || nseen + 2 > sizeof seen / sizeof seen[0]) return;
    if (r->cb) { struct seen *s = &seen[nseen++]; s->st = st; s->cbt = 1; s->p = (const UINT64 *)ring_ptr(x, r->cb, r->cbo); s->h = hash(st, 1, s->p); }
    if (r->arg) { struct seen *s = &seen[nseen++]; s->st = st; s->cbt = 0; s->p = (const UINT64 *)ring_ptr(x, r->arg, r->argo); s->h = hash(st, 0, s->p); }
}
static void check_seen(void) { unsigned i; for (i = 0; i < nseen; i++) assert(hash(seen[i].st, seen[i].cbt, seen[i].p) == seen[i].h); nseen = 0; }

int main(void) {
    static struct mad_pso psos[3];
    static struct stage st[NSTAGE];
    unsigned scen, step, i, k, x;
    unsigned long long compared = 0, compared_miss = 0, refusals = 0, head_same = 0;
    struct mad_queue q = { &dev };
    LONG64 h0, m0;
    for (x = 0; x < NX; x++) { ring_mem[x] = malloc((size_t)NCHUNK * MAD_ARG_RING_BYTES); assert(ring_mem[x]); }
    for (i = 0; i < 32; i++) stab[i].gpu_va = (i % 5) ? 0x8000 + i : 0, stab[i].metadata = i;
    for (i = 0; i < 2; i++) { srv[i].shader_visible = 1; srv[i].cpu = hsrv[i]; srv[i].count = 64; srv[i].type = 0; srv[i].gpu_address = 0x100000 + i * 24 * 4096; mad_descriptor_register(&srv[i]); }
    smp.shader_visible = 1; smp.cpu = hsmp; smp.count = 16; smp.type = 1; smp.gpu_address = 0x200000; mad_descriptor_register(&smp);
    for (scen = 0; scen < 4000; scen++) {
        int phase_b = scen % 3 == 2;
        unsigned nsteps = 150 + rnd(250);
        moving = phase_b;
        cfg_plan = scen % 25 == 24 ? 0 : 1; g_air_plan_on = -1;
        for (i = 0; i < 2; i++) {   /* the second signature: mostly the same layout (another object), else another one */
            random_rs(&rsv[i][0], ranges[i][0], 96, 1000 * scen + 10 * i + 1);
            if (rnd(8)) { memcpy(ranges[i][1], ranges[i][0], sizeof ranges[i][0]); rsv[i][1] = rsv[i][0]; rsv[i][1].ranges = ranges[i][1];
                          rsv[i][1].serial++; if (rnd(4) == 0) rsv[i][1].params[rnd(rsv[i][1].nparams)].shader_register ^= 1; }
            else random_rs(&rsv[i][1], ranges[i][1], 96, 1000 * scen + 10 * i + 2);
        }
        rs_cur[0] = rs_cur[1] = 0;
        for (i = 0; i < 64; i++) { random_desc(&hsrv[0][i], 0); random_desc(&hsrv[1][i], 0); }
        for (i = 0; i < 16; i++) random_desc(&hsmp[i], 1);
        srv_cur = 0;
        memset(mroot, 0, sizeof mroot); memset(mpver, 0, sizeof mpver); mver[0] = mver[1] = 1;
        for (i = 0; i < 2; i++) for (k = 0; k < MAD_ROOT_PARAM_MAX; k++) { set_root(i, k, random_root(i, k)); unsigned j; for (j = 0; j < 64; j++) mconst[i][k][j] = rnd(1000); }
        for (i = 0; i < 3; i++) { mad_air_plans_free(&psos[i]); psos[i].vs_name = "test"; }
        random_stage(&st[0], &rsv[0][0], &psos[0], 0, (1u << MADEIRA_IR_VIS_ALL) | (1u << MADEIRA_IR_VIS_VERTEX) | (1u << MADEIRA_IR_VIS_GEOMETRY));
        random_stage(&st[1], &rsv[0][0], &psos[0], 0, (1u << MADEIRA_IR_VIS_ALL) | (1u << MADEIRA_IR_VIS_PIXEL));
        random_stage(&st[2], &rsv[0][0], &psos[1], 0, (1u << MADEIRA_IR_VIS_ALL) | (1u << MADEIRA_IR_VIS_PIXEL));
        random_stage(&st[3], &rsv[1][0], &psos[2], 1, ~0u);
        /* a new list: empty caches and rings */
        ring_reset();
        for (x = 0; x < NX; x++) { memset(&ex[x], 0, sizeof ex[x]); ex[x].q = &q; ex[x].ring_id = (int)x; ex[x].sk = rnd(2) ? NULL : &no_skin; }
        for (step = 0; step < nsteps; step++) {
            unsigned m = rnd(100), bp = rnd(3) == 0, s;
            const struct mad_rootsig *rs = &rsv[bp][rs_cur[bp]];
            if (m < 40) {   /* a root argument (a table offset, a CBV address) */
                unsigned pi = rnd(rs->nparams + 1);
                if (rnd(3)) { UINT64 v = mroot[bp][pi % MAD_ROOT_PARAM_MAX];   /* mostly a near move, as a draw loop makes */
                    set_root(bp, pi % MAD_ROOT_PARAM_MAX, v ? (rnd(2) ? v + (UINT64)24 * (1 + rnd(3)) : v - (UINT64)24 * rnd(3)) : random_root(bp, pi % MAD_ROOT_PARAM_MAX)); }
                else set_root(bp, pi % MAD_ROOT_PARAM_MAX, random_root(bp, pi % MAD_ROOT_PARAM_MAX));
            } else if (m < 50) {   /* root constants */
                unsigned pi = rnd(rs->nparams) % MAD_ROOT_PARAM_MAX, j = rnd(64);
                mconst[bp][pi][j] ^= 1 + rnd(7); bump(bp, pi);
            } else if (m < 56) {   /* a descriptor write */
                if (rnd(3)) { unsigned h = rnd(2), j = rnd(64); random_desc(&hsrv[h][j], 0); mad_descriptor_write(&hsrv[h][j], 1, 0); }
                else { unsigned j = rnd(16); random_desc(&hsmp[j], 1); mad_descriptor_write(&hsmp[j], 1, 1); }
            } else if (m < 58) {   /* SetDescriptorHeaps */
                srv_cur ^= 1; mver[0]++; mver[1]++;
            } else if (m < 60) {   /* another root signature, and back */
                rs_cur[bp] ^= 1; mver[bp]++; for (k = 0; k < MAD_ROOT_PARAM_MAX; k++) mpver[bp][k]++;
            } else if (m < 63) {
                dev.vmap_gen++;   /* phase A: the answers stay; phase B: they move */
            } else if (m < 65 && phase_b) {
                ctr_epoch++;
            }
            /* every stage draws now and then; a draw binds one or two stages */
            s = rnd(NSTAGE);
            if ((st[s].compute ? rs_cur[1] : rs_cur[0]) == 1 && rnd(2)) continue;   /* draws under the other signature too */
            for (i = 0; i < (s == 0 ? 2u : 1u); i++) {
                const struct stage *sg = &st[s + i];
                struct res r[NX];
                LONG64 hits_before;
                r[X_REF] = build(X_REF, sg);
                hits_before = g_air_cache_hits;
                r[X_INC] = build(X_INC, sg);
                {
                    int inc_hit = g_air_cache_hits != hits_before;
                    if (!phase_b || !inc_hit) {
                        if (r[X_INC].ok != r[X_REF].ok || r[X_INC].nwhy != r[X_REF].nwhy ||
                            (r[X_REF].why && strcmp(r[X_INC].why, r[X_REF].why))) {
                            fprintf(stderr, "scenario %u step %u stage %u: inc ok %d (%s) vs whole build ok %d (%s)\n", scen, step, s + i,
                                    r[X_INC].ok, r[X_INC].why ? r[X_INC].why : "-", r[X_REF].ok, r[X_REF].why ? r[X_REF].why : "-");
                            return 1;
                        }
                        if (r[X_REF].ok && (!same_table(sg, 1, X_INC, r[X_INC].cb, r[X_INC].cbo, X_REF, r[X_REF].cb, r[X_REF].cbo) ||
                                            !same_table(sg, 0, X_INC, r[X_INC].arg, r[X_INC].argo, X_REF, r[X_REF].arg, r[X_REF].argo))) {
                            fprintf(stderr, "scenario %u step %u stage %u: an incremental table differs from the whole build\n", scen, step, s + i);
                            return 1;
                        }
                        compared++; if (!inc_hit) compared_miss++;
                        if (!r[X_REF].ok) { unsigned j; refusals++;
                            for (j = 0; j < 31 && whys[j].w && whys[j].w != r[X_REF].why; j++) ;
                            whys[j].w = r[X_REF].why; whys[j].n++; }
                    }
                }
                r[X_OFF] = build(X_OFF, sg);
                r[X_HEAD] = build(X_HEAD, sg);
                /* the switch off: the builder as it was, allocation for allocation */
                if (r[X_OFF].ok != r[X_HEAD].ok || r[X_OFF].cb != r[X_HEAD].cb || r[X_OFF].cbo != r[X_HEAD].cbo ||
                    r[X_OFF].arg != r[X_HEAD].arg || r[X_OFF].argo != r[X_HEAD].argo || r[X_OFF].nwhy != r[X_HEAD].nwhy ||
                    ring_used[X_OFF] != ring_used[X_HEAD]) {
                    fprintf(stderr, "scenario %u step %u: air-table-inc = 0 differs from the builder before it\n", scen, step);
                    return 1;
                }
                head_same++;
                remember(X_INC, sg, &r[X_INC]);
                remember(X_OFF, sg, &r[X_OFF]);
            }
        }
        check_seen();
        assert(!memcmp(ring_mem[X_OFF], ring_mem[X_HEAD], (size_t)NCHUNK * MAD_ARG_RING_BYTES));   /* byte for byte */
    }
    printf("ok   equivalence: %llu draws' tables equal a whole build (%llu of them cache misses, %llu refusals with the same reason)\n",
           compared, compared_miss, refusals);
    for (i = 0; i < 32 && whys[i].w; i++) printf("     refused %lu: %s\n", whys[i].n, whys[i].w);
    printf("ok   copy on write: no bound table changed later in its list\n");
    printf("ok   air-table-inc = 0: %llu builds allocate and write exactly as the builder before it\n", head_same);
    printf("     incremental %lld (%lld entries rewritten, %lld kept, %lld tables bound unchanged), %lld misses with no entry changed; whole builds %lld\n",
           (long long)g_ai_inc, (long long)g_ai_inc_ent, (long long)g_ai_inc_kept, (long long)g_ai_inc_tabkept, (long long)g_ai_inc_none, (long long)g_ai_full);
    assert(g_ai_inc > 1000 && g_ai_inc_none > 100 && g_ai_inc_tabkept > 100 && g_ai_inc_kept > 1000 && g_ai_full > 1000);
    assert(compared_miss > 10000 && refusals > 100);

    /* directed: no ring slot for the copy -> a whole build, which reports as before */
    {
        struct stage d; struct res a, b;
        memset(&ex[X_INC], 0, sizeof ex[X_INC]); ex[X_INC].q = &q; ex[X_INC].ring_id = X_INC; ring_reset();
        moving = 0; cfg_plan = 1; g_air_plan_on = -1;
        for (;;) {   /* a stage whose single CBV comes from a root CBV */
            random_rs(&rsv[0][0], ranges[0][0], 96, 777777 + rnd(1000)); rs_cur[0] = 0;
            random_stage(&d, &rsv[0][0], &psos[0], 0, ~0u);
            d.nair = 2; d.air[0].type = MADEIRA_IR_AIR_CBV; d.air[0].cb_table = 0; d.air[0].ptr_offset = 0; d.air[0].flags = 0;
            d.air[1] = d.air[0]; d.air[1].ptr_offset = 1; d.air[1].lower_bound = 99; d.air[1].space = 7;   /* nothing covers it ... */
            d.arg_qwords = 2;
            rsv[0][0].params[0].type = MADEIRA_IR_PARAM_CBV; rsv[0][0].params[0].visibility = 40;
            rsv[0][0].params[0].shader_register = d.air[0].lower_bound; rsv[0][0].params[0].register_space = d.air[0].space;
            rsv[0][0].params[1].type = MADEIRA_IR_PARAM_CBV; rsv[0][0].params[1].visibility = 40;
            rsv[0][0].params[1].shader_register = 99; rsv[0][0].params[1].register_space = 7;   /* ... but this root CBV */
            break;
        }
        mad_air_plans_free(&psos[0]);
        set_root(0, 0, 0xabc000); set_root(0, 1, 0xabd000);
        a = build(X_INC, &d); assert(a.ok);
        set_root(0, 0, 0xabc100);
        ring_fail = 1; b = build(X_INC, &d); ring_fail = 0;
        assert(!b.ok && b.nwhy == 0);                  /* the whole build found no slot either: refused quietly, as before */
        b = build(X_INC, &d); assert(b.ok && (b.arg != a.arg || b.argo != a.argo));
        assert(((UINT64 *)ring_ptr(X_INC, b.arg, b.argo))[0] == 0xabc100 && ((UINT64 *)ring_ptr(X_INC, b.arg, b.argo))[1] == 0xabd000);
        assert(((UINT64 *)ring_ptr(X_INC, a.arg, a.argo))[0] == 0xabc000);   /* the old table is untouched */
        h0 = g_ai_inc_none; set_root(0, 5, 0x123);     /* a parameter no declaration reads */
        rsv[0][0].params[5].type = MADEIRA_IR_PARAM_CBV; rsv[0][0].params[5].shader_register = d.air[0].lower_bound;
        rsv[0][0].params[5].register_space = d.air[0].space; rsv[0][0].params[5].visibility = MADEIRA_IR_VIS_PIXEL;
        mad_air_plans_free(&psos[0]); memset(ex[X_INC].air_cache, 0, sizeof ex[X_INC].air_cache);
        a = build(X_INC, &d); set_root(0, 5, 0x456);
        m0 = g_air_cache_misses; b = build(X_INC, &d);
        assert(b.ok && b.arg == a.arg && b.argo == a.argo);   /* bound again: a hit or a miss with no entry changed */
        (void)m0; (void)h0;
    }
    printf("ok   no ring slot for a copy: a whole build, refused as before; an unread parameter binds the same tables\n");
    puts("PASS");
    return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='madeira-air-inc-') as directory:
    exe = Path(directory) / 'test'
    subprocess.run(['xcrun', 'clang', '-std=gnu11', '-x', 'c', '-O1', '-g', '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                    '-Wno-unused-function', '-I' + str(root / 'madeira-d3d12/src'), '-o', str(exe), '-'],
                   input=harness, text=True, check=True)
    r = subprocess.run([str(exe)], timeout=600)
    sys.exit(r.returncode)
