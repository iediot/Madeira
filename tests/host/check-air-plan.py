#!/usr/bin/env python3
"""madeira-d3d12 replay fast paths, run from production source:

- air-plan: mad_air_route_find/apply (and the per-pipeline plans built from
  them) answer exactly as the previous single-pass mad_air_resolve (taken from
  git HEAD when it still has it, else from this file's embedded copy) for
  random root signatures, declarations, visibilities, root arguments and heaps,
  including every failure message and the visibility-ALL fallback;
- plans are keyed by root-signature serial, capped per pipeline, freed;
- view-info cache: answers equal the uncached lookups, cached only when the
  view map knew them, invalidated by the device's vmap generation;
- use-dedup hash: equal to the linear scan under random declarations,
  commits and uncommitted rollbacks, including duplicate handles."""
from pathlib import Path
import subprocess
import tempfile
import sys

root = Path(__file__).resolve().parents[2]
src_path = root / 'madeira-d3d12/src/pe/madeira_d3d12.c'
source = src_path.read_text()

def cut(text, begin, end, start=0):
    b = text.index(begin, start)
    return text[b:text.index(end, b)]

types = cut(source, 'static UINT mad_air_range_type(', '/* Resolve one declaration range')
routes = cut(source, 'enum { MAD_AR_NONE', "/* ml1011: the DXBC backend's vertex-buffer table")
plans = cut(source, '#define MAD_AIR_PLANS_MAX', 'static int mad_air_build_tables_ex_body(')
rmask = cut(source, 'static UINT mad_air_root_mask(const struct mad_rootsig *rs, const struct madeira_ir_air_range *air, UINT n) {', 'static int mad_air_roots_equal(')
vic = cut(source, 'static int g_vic_on = -1;', '\nstatic UINT mad_air_range_type(')
use = cut(source, 'static void mad_use_begin(', '/* madeira-bcd: skip-ps')
assert 'mad_air_route_apply(e, rs, root, consts, rg, &plan->r[nair + k]' in source
assert 'c->root_mask = have_mask ? root_mask : plan ? plan->root_mask' in source
assert 'mad_air_plans_free(p);' in source and 'r->serial = (UINT64)InterlockedIncrement64(&serial)' in source
assert source.count('InterlockedIncrement64(&d->vmap_gen)') == 2

# The single-pass resolver this replaces, for the equivalence check.
OLD_BEGIN = 'static int mad_air_resolve(struct mad_exec *e, const struct mad_rootsig *rs, const UINT64 *root,'
old = None
try:
    head = subprocess.run(['git', '-C', str(root), 'show', 'HEAD:madeira-d3d12/src/pe/madeira_d3d12.c'],
                          capture_output=True, text=True, check=True).stdout
    if 'mad_air_route_find' not in head:
        b = head.index(OLD_BEGIN, head.index('/* Resolve one declaration range'))
        old = head[b:head.index("/* ml1011: the DXBC backend's vertex-buffer table", b)]
except (subprocess.CalledProcessError, ValueError):
    pass
if old is None:
    old = (Path(__file__).with_suffix('.oldresolve.c')).read_text()
else:
    # keep a copy beside the test so it still runs once HEAD carries the split
    (Path(__file__).with_suffix('.oldresolve.c')).write_text(old)
old = old.replace('static int mad_air_resolve(', 'static int old_resolve(', 1)

harness = r'''
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <assert.h>
#include "madeira_ir_abi.h"
#define MAD_ROOT_PARAM_MAX 32
typedef uint64_t UINT64, obj_handle_t;
typedef uint32_t UINT, UINT32;
typedef uint16_t UINT16;
typedef uint8_t UINT8;
typedef int64_t LONG64;
typedef int32_t LONG;
static LONG64 InterlockedIncrement64(volatile LONG64 *p) { return __atomic_add_fetch(p, 1, __ATOMIC_SEQ_CST); }
static LONG InterlockedIncrement(volatile LONG *p) { return __atomic_add_fetch(p, 1, __ATOMIC_SEQ_CST); }
static int cfg_plan = 1, cfg_vic = 1, cfg_dedup = 1;
static long long mad_cfg_int_pe(const char *k, long long d) {
    if (!strcmp(k, "air-plan")) return cfg_plan;
    if (!strcmp(k, "view-info-cache")) return cfg_vic;
    if (!strcmp(k, "use-dedup")) return cfg_dedup;
    return d;
}
struct mad_descriptor { UINT64 gpu_va, texture_view_id, metadata; };
struct mad_heap { struct mad_descriptor *cpu; UINT64 gpu_address; UINT count; };
struct mad_rootsig { struct madeira_ir_root_param params[MAD_ROOT_PARAM_MAX]; struct madeira_ir_root_range *ranges;
    UINT nparams, nranges; struct madeira_ir_static_sampler samplers[32]; UINT nsamplers;
    const struct mad_descriptor *stab_cpu; UINT64 serial; };
struct mad_air_plan;
struct mad_pso { struct mad_air_plan *volatile air_plans; volatile LONG air_nplans; };
#define MAD_VIC_N 256u
struct mad_vic { UINT64 id, gen, v; UINT32 a, mode; };
struct mad_queue { struct mad_vic vic[MAD_VIC_N]; };
struct mad_device { volatile LONG64 vmap_gen; };
struct mad_exec { struct mad_heap *srv, *smp; struct mad_queue *q;
    obj_handle_t ud_enc; unsigned ud_n, ud_committed;
    struct { obj_handle_t h; UINT32 usage, stages; } ud[96];
    UINT8 ud_hash[256]; };
static unsigned char ring[256];
static unsigned ring_takes; static int ring_fail;
static int exec_ring_take_z(struct mad_exec *e, unsigned n, size_t z, obj_handle_t *buf, UINT64 *off, void **cpu, UINT64 *gpu) {
    (void)e; (void)n;
    if (ring_fail) return 0;
    ring_takes++; memset(ring, 0, z < sizeof ring ? z : sizeof ring);
    *buf = 77; *off = 0x40; *cpu = ring; *gpu = 0x900000; return 1;
}
static volatile LONG64 g_vic_hits, g_vic_misses;
static volatile LONG g_air_plans_built, g_es_use_skipped;
static int g_use_dedup = -1;
#define MAD_VREC_TOMB (~(UINT64)0)
/* view map stand-ins: counters and a "known" switch */
static unsigned n_flat, n_alen, n_tb; static int map_knows = 1;
static UINT64 flat_of(UINT64 id) { return id ^ 0x1000; }
static UINT alen_of(UINT64 id) { return (UINT)(id % 7) + 1; }
static UINT64 mad_air_flat_view(struct mad_device *d, UINT64 id, int *known) { (void)d; n_flat++; if (known) *known = map_knows; return flat_of(id); }
static UINT mad_air_array_len(struct mad_device *d, UINT64 v, int *known) { (void)d; n_alen++; if (known) *known = map_knows; return alen_of(v); }
static int mad_air_texbuf(struct mad_device *d, UINT64 id, UINT32 *count, UINT32 *first) {
    (void)d; n_tb++; if (!map_knows) return 0; *count = (UINT32)(id & 0xffff); *first = (UINT32)(id >> 16); return 1; }
''' + types + old + routes + rmask + plans + vic + use + r'''
static uint64_t rng = 0x9e3779b97f4a7c15ull;
static unsigned rnd(unsigned n) { rng ^= rng << 13; rng ^= rng >> 7; rng ^= rng << 17; return n ? (unsigned)(rng % n) : 0; }

static void random_rs(struct mad_rootsig *rs, struct madeira_ir_root_range *ranges, unsigned cap) {
    unsigned i, j, nr = 0;
    memset(rs, 0, sizeof *rs);
    rs->ranges = ranges;
    rs->nparams = 1 + rnd(MAD_ROOT_PARAM_MAX + 2);   /* > MAD_ROOT_PARAM_MAX too: the loops stop at 32 */
    for (i = 0; i < rs->nparams && i < MAD_ROOT_PARAM_MAX; i++) {
        struct madeira_ir_root_param *p = &rs->params[i];
        p->type = rnd(5);
        p->visibility = rnd(4) == 0 ? 40 : rnd(6);   /* now and then out of range: seen by everyone */
        p->shader_register = rnd(8); p->register_space = rnd(2);
        p->num_constants = rnd(80);
        if (p->type == MADEIRA_IR_PARAM_TABLE) {
            p->first_range = nr; p->num_ranges = rnd(6);
            if (rnd(10) == 0) p->num_ranges += 4;   /* past nranges: the walk stops there */
            for (j = 0; j < p->num_ranges && nr < cap; j++, nr++) {
                struct madeira_ir_root_range *r = &ranges[nr];
                r->range_type = rnd(4); r->base_register = rnd(8); r->register_space = rnd(2);
                r->num_descriptors = rnd(5) == 0 ? ~0u : 1 + rnd(4);
                r->table_offset = rnd(3) == 0 ? 0xffffffffu : rnd(12);
            }
        }
    }
    rs->nranges = nr;
    rs->nsamplers = rnd(5);
    for (i = 0; i < rs->nsamplers; i++) {
        rs->samplers[i].shader_register = rnd(8); rs->samplers[i].register_space = rnd(2);
        rs->samplers[i].visibility = rnd(4) == 0 ? 33 : rnd(6);
    }
}

int main(void) {
    static struct madeira_ir_root_range ranges[256];
    static struct mad_descriptor hcpu[64], scpu[16], stab[32];
    struct mad_heap srv = { hcpu, 0x10000, 64 }, smp = { scpu, 0x20000, 16 };
    struct mad_exec e; struct mad_rootsig rs; UINT64 rootv[MAD_ROOT_PARAM_MAX]; UINT32 consts[MAD_ROOT_PARAM_MAX][64];
    unsigned it, k, i, fails = 0, oks = 0, fallbacks = 0, kinds[8] = {0};
    for (i = 0; i < 64; i++) { hcpu[i].gpu_va = 0x5000 + i; hcpu[i].texture_view_id = 100 + i; hcpu[i].metadata = i * 3; }
    for (i = 0; i < 16; i++) { scpu[i].gpu_va = 0x7000 + i; scpu[i].metadata = i; }
    for (i = 0; i < 32; i++) stab[i].gpu_va = (i % 5) ? 0x8000 + i : 0;
    for (i = 0; i < MAD_ROOT_PARAM_MAX; i++) for (k = 0; k < 64; k++) consts[i][k] = i * 1000 + k;
    memset(&e, 0, sizeof e);
    for (it = 0; it < 40000; it++) {
        static const unsigned vis[4] = { ~0u, (1u << MADEIRA_IR_VIS_ALL) | (1u << MADEIRA_IR_VIS_PIXEL),
            (1u << MADEIRA_IR_VIS_ALL) | (1u << MADEIRA_IR_VIS_VERTEX) | (1u << MADEIRA_IR_VIS_GEOMETRY),
            (1u << MADEIRA_IR_VIS_ALL) | (1u << MADEIRA_IR_VIS_HULL) };
        struct madeira_ir_air_range air[6]; unsigned nair = 1 + rnd(6), vm = vis[rnd(4)];
        struct mad_pso pso = { 0 }; const struct mad_air_plan *pl;
        random_rs(&rs, ranges, 256);
        rs.stab_cpu = rnd(4) ? stab : NULL; rs.serial = 1 + it;
        for (i = 0; i < MAD_ROOT_PARAM_MAX; i++) {
            unsigned r = rnd(10);
            /* table bases in and around the heaps, misaligned, zero, or a root address */
            rootv[i] = r == 0 ? 0 : r == 1 ? srv.gpu_address - 24 : r == 2 ? srv.gpu_address + 8 * 24 + 4 :
                       r < 6 ? srv.gpu_address + rnd(70) * 24 : r < 8 ? smp.gpu_address + rnd(20) * 24 : 0xabc000 + r;
        }
        e.srv = rnd(8) ? &srv : NULL; e.smp = rnd(8) ? &smp : NULL;
        if (rnd(10) == 0 && e.srv) { static struct mad_heap nocpu; nocpu = srv; nocpu.cpu = NULL; e.srv = &nocpu; }
        for (k = 0; k < nair; k++) {
            memset(&air[k], 0, sizeof air[k]);
            air[k].type = rnd(4); air[k].space = rnd(2); air[k].lower_bound = rnd(10);
        }
        pl = mad_air_plan_get(&pso, rnd(20) ? &rs : NULL, air, nair, vm);
        for (k = 0; k < nair; k++) {
            unsigned pass;
            for (pass = 0; pass < 2; pass++) {
                unsigned v = pass ? ~0u : vm;
                struct mad_descriptor d0, d1, d2; UINT64 x0 = 5, x1 = 6, x2 = 7; const char *w0 = "?", *w1 = "?", *w2 = "?";
                unsigned char r0[256], r1[256];
                int a, b, c = -1;
                ring_fail = rnd(30) == 0;
                memset(&d0, 0xcc, sizeof d0); memset(&d1, 0xcc, sizeof d1); memset(&d2, 0xcc, sizeof d2);
                a = old_resolve(&e, &rs, rootv, (const UINT32 (*)[64])consts, &air[k], v, &d0, &x0, &w0); memcpy(r0, ring, sizeof ring);
                b = mad_air_resolve(&e, &rs, rootv, (const UINT32 (*)[64])consts, &air[k], v, &d1, &x1, &w1); memcpy(r1, ring, sizeof ring);
                assert(a == b && x0 == x1 && !strcmp(w0, w1));
                if (a) assert(!memcmp(&d0, &d1, sizeof d0) || x0);
                if (a && x0) assert(!memcmp(r0, r1, sizeof r0));
                if (pl && (pass == 0 || vm != ~0u)) {
                    const struct mad_air_route *ro = &((const struct mad_air_plan *)pl)->r[pass ? nair + k : k];
                    c = mad_air_route_apply(&e, &rs, rootv, (const UINT32 (*)[64])consts, &air[k], ro, &d2, &x2, &w2);
                    assert(c == a && x2 == x0 && !strcmp(w2, w0));
                    if (a && !x0) assert(!memcmp(&d2, &d0, sizeof d0));
                    kinds[ro->kind]++;
                }
                if (a) oks++; else fails++;
                if (pass && !c) fallbacks++;
            }
        }
        if (pl) {
            assert(((const struct mad_air_plan *)pl)->root_mask == mad_air_root_mask(&rs, air, nair));
            assert(mad_air_plan_get(&pso, &rs, air, nair, vm) == pl);   /* found again */
            rs.serial++;                                                 /* a new signature at the same address */
            assert(mad_air_plan_get(&pso, &rs, air, nair, vm) != pl);
            rs.serial = 0; assert(!mad_air_plan_get(&pso, &rs, air, nair, vm));
            rs.serial = 1ull << 40;   /* clear of the serials above */
            while (pso.air_nplans < MAD_AIR_PLANS_MAX) { rs.serial++; assert(mad_air_plan_get(&pso, &rs, air, nair, vm)); }
            rs.serial++; assert(!mad_air_plan_get(&pso, &rs, air, nair, vm));   /* capped */
        }
        mad_air_plans_free(&pso);
        assert(!pso.air_plans && !pso.air_nplans);
    }
    for (k = MAD_AR_TABLE; k <= MAD_AR_STATIC; k++) assert(kinds[k]);
    assert(kinds[MAD_AR_NONE]);
    cfg_plan = 0; g_air_plan_on = -1;
    { struct mad_pso pso = { 0 }; struct madeira_ir_air_range a1 = { 0 }; rs.serial = 9;
      assert(!mad_air_plan_get(&pso, &rs, &a1, 1, ~0u)); }
    cfg_plan = 1; g_air_plan_on = -1;
    printf("ok   air routes: %u resolves agree (%u failures, %u fallback-pass failures); plans keyed by serial, capped, freed\n", oks + fails, fails, fallbacks);

    /* ---- view-info cache ---- */
    {
        static struct mad_queue q; struct mad_device d = { 0 }; struct mad_exec ve; UINT64 v; UINT32 al, cnt, fst;
        memset(&ve, 0, sizeof ve); ve.q = &q;
        mad_air_texinfo_c(&ve, &d, 0x1234, 1, &v, &al);
        assert(v == flat_of(0x1234) && al == alen_of(flat_of(0x1234)) && n_flat == 1 && n_alen == 1);
        mad_air_texinfo_c(&ve, &d, 0x1234, 1, &v, &al);
        assert(v == flat_of(0x1234) && n_flat == 1 && n_alen == 1 && g_vic_hits == 1);   /* cached */
        mad_air_texinfo_c(&ve, &d, 0x1234, 0, &v, &al);
        assert(v == 0x1234 && al == alen_of(0x1234) && n_flat == 1 && n_alen == 2);       /* other kind: its own entry */
        d.vmap_gen++;
        mad_air_texinfo_c(&ve, &d, 0x1234, 1, &v, &al);
        assert(n_flat == 2 && n_alen == 3);                                                /* generation moved */
        map_knows = 0;
        mad_air_texinfo_c(&ve, &d, 0x5555, 1, &v, &al); mad_air_texinfo_c(&ve, &d, 0x5555, 1, &v, &al);
        assert(n_flat == 4);                                                               /* not cached when the map did not know */
        assert(!mad_air_texbuf_c(&ve, (struct mad_device *)&d, 0x77, &cnt, &fst) && n_tb == 1);
        assert(!mad_air_texbuf_c(&ve, (struct mad_device *)&d, 0x77, &cnt, &fst) && n_tb == 2);
        map_knows = 1;
        assert(mad_air_texbuf_c(&ve, &d, 0x30077, &cnt, &fst) && cnt == 0x77 && fst == 3 && n_tb == 3);
        assert(mad_air_texbuf_c(&ve, &d, 0x30077, &cnt, &fst) && cnt == 0x77 && fst == 3 && n_tb == 3);
        mad_air_texinfo_c(&ve, &d, 0, 1, &v, &al); mad_air_texinfo_c(&ve, &d, MAD_VREC_TOMB, 1, &v, &al);
        assert(n_flat == 6);                                                               /* id 0 and the tombstone never cached */
        cfg_vic = 0; g_vic_on = -1;
        mad_air_texinfo_c(&ve, &d, 0x1234, 1, &v, &al); assert(n_flat == 7 && v == flat_of(0x1234));
        cfg_vic = 1; g_vic_on = -1;
        ve.q = NULL; mad_air_texinfo_c(&ve, &d, 0x1234, 1, &v, &al); assert(n_flat == 8);
        /* collisions: many ids through the 256 slots, every answer right */
        ve.q = &q;
        for (it = 0; it < 200000; it++) {
            UINT64 id = 1 + rnd(3000); int fl = rnd(2);
            if (rnd(500) == 0) d.vmap_gen++;
            mad_air_texinfo_c(&ve, &d, id, fl, &v, &al);
            assert(v == (fl ? flat_of(id) : id) && al == alen_of(v));
            if (rnd(3) == 0) { assert(mad_air_texbuf_c(&ve, &d, id << 4, &cnt, &fst) && cnt == (UINT32)((id << 4) & 0xffff) && fst == (UINT32)((id << 4) >> 16)); }
        }
        printf("ok   view-info cache: equal answers, map-known only, generation and switch respected (%lld hits, %lld misses)\n",
               (long long)g_vic_hits, (long long)g_vic_misses);
    }

    /* ---- use-dedup hash against the linear scan ---- */
    {
        struct ref { obj_handle_t h; UINT32 u, s; } ref[96]; unsigned rn = 0, rc = 0; obj_handle_t renc = 0;
        struct mad_exec ue; memset(&ue, 0, sizeof ue);
        for (it = 0; it < 400000; it++) {
            unsigned op = rnd(100);
            if (op < 2) {   /* another encoder */
                obj_handle_t enc = 0x1000 + rnd(3);
                mad_use_begin(&ue, enc);
                if (renc != enc) { renc = enc; rn = rc = 0; } else rn = rc;
            } else if (op < 12) {   /* a draw begins: uncommitted entries go */
                mad_use_begin(&ue, renc ? renc : 0x1000);
                if (!renc) { renc = 0x1000; rn = rc = 0; } else rn = rc;
            } else if (op < 20) { mad_use_commit(&ue); rc = rn; }
            else {
                obj_handle_t h = 0x40 + rnd(140) * 0x10; UINT32 u = 1 + rnd(3), s = 1 + rnd(3);
                int want = 0, got; unsigned j;
                if (!renc) continue;
                for (j = 0; j < rn; j++) if (ref[j].h == h && (ref[j].u & u) == u && (ref[j].s & s) == s) { want = 1; break; }
                got = mad_use_seen(&ue, renc, h, u, s);
                if (!want && got) { fprintf(stderr, "use-dedup: skipped a declaration the scan would make\n"); return 1; }
                if (want && !got) { fprintf(stderr, "use-dedup: declared again what the scan would skip\n"); return 1; }
                if (!want && rn < 96) { ref[rn].h = h; ref[rn].u = u; ref[rn].s = s; rn++; }
                assert(ue.ud_n == rn);
            }
        }
        cfg_dedup = 0; g_use_dedup = -1;
        mad_use_begin(&ue, 0x2000); assert(!mad_use_seen(&ue, 0x2000, 0x50, 1, 1)); assert(!mad_use_seen(&ue, 0x2000, 0x50, 1, 1));
        printf("ok   use-dedup hash: same answers as the linear scan across commits, rollbacks and encoder changes\n");
    }
    puts("PASS");
    return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='madeira-air-plan-') as directory:
    exe = Path(directory) / 'test'
    subprocess.run(['xcrun', 'clang', '-std=gnu11', '-x', 'c', '-O1', '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                    '-Wno-unused-function', '-I' + str(root / 'madeira-d3d12/src'), '-o', str(exe), '-'],
                   input=harness, text=True, check=True)
    r = subprocess.run([str(exe)], timeout=120)
    sys.exit(r.returncode)
