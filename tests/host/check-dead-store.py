#!/usr/bin/env python3
"""madeira-d3d12 dead-store (madeira.cfg dead-store, default 1), run from production source.

A render pass leaves an attachment's store out only when D3D12 proves nobody
can read it: the next thing to happen to that attachment, in the rest of its
list or the later lists of the same ExecuteCommandLists call, is a whole clear
of the same view and aspect, a whole DiscardResource (dead-store-discard,
default 1), or an aliasing barrier deactivating a texture that cannot inherit
data (dead-store-alias, opt-in). This test cuts mad_ds_fate, its helpers and
the command layout out of madeira_d3d12.c and compiles them on the host:

- against an independent single-attachment oracle on ~200k random command
  streams across several lists (draws while bound, target changes, barriers
  naming it, naming everything, UAV-all barriers, aliasing barriers, copies,
  resolves, UAV clears, whole and partial clears through matching and other
  views, depth-only and stencil-only clears, whole and partial discards,
  skipped lists, the 8192-command limit), with each switch on and off;
- every slot decided in one multi-slot scan equals its own scan, and slots
  already decided stay as they were;
- the kill_bloom filter the recorder builds never hides a proof (a dead
  verdict always has its resource's bit in the remaining lists' filters);
- the census table of stores tracked past their call (add, hit, age, full,
  concurrent hits credited once).

In the source: the recorder sets kill_bloom exactly at whole clears, whole
discards and aliasing barriers' pResourceBefore, Reset clears it; passes take
Unknown store actions only with op 24, never in capture frames, and only when
a proof may lie ahead; the late store decision runs right before endEncoding;
defaults are dead-store 1, dead-store-discard 1, dead-store-alias 0; the
counters are printed in the [perf] dead-store and ml1137 lines."""
from pathlib import Path
import os, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
src = (root / 'madeira-d3d12/src/pe/madeira_d3d12.c').read_text()
ok = True


def check(what, cond):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what)
    ok &= bool(cond)


def cut(begin, end, start=0):
    b = src.index(begin, start)
    return src[b:src.index(end, b)]


def body(sig):
    b = src.index(sig)
    return src[b:src.index('\n}\n', b) + 3]


cmds = cut('enum mad_ck {', '#define MAD_ARG_RING_BYTES')
bc = cut('enum { BC_RAR, BC_RAW_ATT', '\n', 0)
names = body('static int mad_cmd_names(const struct mad_cmd *c, const struct mad_resource *r) {')
rtvp = body('static int mad_rtvp_eq(const struct mad_rtvp *a, const struct mad_rtvp *b) {')
listat = body('static const struct mad_list *mad_rp_list_at(const struct mad_queue *q, UINT i) {')
bit = body('static inline UINT64 mad_ds_bit(const struct mad_resource *r) {')
ds = cut('enum { DSP_UNDECIDED = -1', '/* ml1137 census (see f7_store_census)')
fate = cut('#define MAD_FATE_N 128', 'struct mad_ds_slot;\n')
fx = cut('enum { FX_CLEAR', '\n', src.index('enum { ACL_CLEAR'))

# ---- source checks ---------------------------------------------------------
clear_rt = body('static void STDMETHODCALLTYPE list_ClearRenderTargetView(')
clear_ds = body('static void STDMETHODCALLTYPE list_ClearDepthStencilView(')
disc = body('static void STDMETHODCALLTYPE list_DiscardResource(')
barrier = body('static void STDMETHODCALLTYPE list_ResourceBarrier(')
reset = body('static HRESULT STDMETHODCALLTYPE list_Reset(')
check('recorder: whole-view clears set kill_bloom, clear-rects do not',
      'else l->kill_bloom |= mad_ds_bit(rt);' in clear_rt and 'if (nr > 0) { c->u.clear.nrect = (UINT)nr; c->u.clear.rect_at = at; }\n    else l->kill_bloom' in clear_rt
      and 'else l->kill_bloom |= mad_ds_bit(d);' in clear_ds)
check('recorder: whole discards set kill_bloom',
      'if (c->u.discard.whole) ((struct mad_list *)This)->kill_bloom |= mad_ds_bit(r);' in disc)
check('recorder: aliasing barriers note pResourceBefore (still a barrier naming everything) and set kill_bloom',
      'b[i].Type == D3D12_RESOURCE_BARRIER_TYPE_ALIASING && b[i].Aliasing.pResourceBefore' in barrier
      and 'if (ab->texture) l->kill_bloom |= mad_ds_bit(ab);' in barrier
      and barrier.index('ALIASING') < barrier.index('if (!r) {\n            c->u.barrier.all = 1;'))
check('Reset clears kill_bloom', 'l->kill_bloom = 0;' in reset)
check('the barrier command still fits the command union',
      'UINT8 nalias; struct mad_resource *alias[4]; } barrier;' in cmds)
begin = body('static int exec_begin_render(struct mad_exec *e) {')
check('passes take Unknown store actions only with op 24, never in capture frames, only when a proof may lie ahead',
      'if (!g_capture_on && ((mad_rp_discard_on() && exec_rp_late_want(e)) || (mad_ds_on() && exec_ds_want(e))) && mad_rp_late_ok()) {' in begin)
end = body('static void exec_end(struct mad_exec *e) {')
check('the store decision runs right before endEncoding',
      'if (e->rp_late) exec_rp_late_store(e);   /* madeira-bcd rp-discard: the store actions the pass left open */\n        MTLCommandEncoder_endEncoding(e->renc);' in end)
late = body('static void exec_rp_late_store(struct mad_exec *e) {')
check('late store: no proof ahead keeps Store without a scan; a scan uses the switches; DontCare only for a proof',
      "s[i].res = (ahead & mad_ds_bit(s[i].r)) ? DSP_UNDECIDED : DSP_LIVE;" in late
      and 'mad_ds_fate(e->q, e->l, e->q->rp_idx, e->rp_from, s, n, disc_ok, alias_ok, 1)' in late
      and '*act[i] = s[i].res > DSP_LIVE ? WMTStoreActionDontCare : WMTStoreActionStore;' in late
      and 'int disc_ok = g_rp_discard > 0 || (g_ds_on > 0 && g_ds_discard > 0), alias_ok = g_ds_on > 0 && g_ds_alias > 0;' in late)
check('defaults: dead-store 1, dead-store-discard 1, dead-store-alias 0',
      'mad_cfg_int_pe("dead-store", 1)' in ds and 'mad_cfg_int_pe("dead-store-discard", 1)' in ds and 'mad_cfg_int_pe("dead-store-alias", 0)' in ds)
check('counters printed', '[perf] dead-store per frame' in src and 'mad_ds_report(np);' in src
      and '[perf] ml1137 stores with no use in their list, per census frame' in src)
check('the census tracks stores past their call and ages them at present',
      'if (g_fate_n) exec_fate_cmd(c);' in src and 'mad_fate_age();   /* madeira-bcd dead-store census */' in src)
cat = (root / 'app/Madeira/ConfigCatalog.generated.swift').read_text()
for key, dflt in (('dead-store', '1'), ('dead-store-discard', '1'), ('dead-store-alias', '0')):
    check('catalog lists %s (default %s)' % (key, dflt),
          re.search(r'key: "%s".*defaultValue: "%s"' % (re.escape(key), dflt), cat) is not None)

harness = r'''
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <pthread.h>
typedef uint64_t UINT64, obj_handle_t; typedef uint32_t UINT, UINT32, DWORD; typedef uint16_t UINT16; typedef uint8_t UINT8;
typedef int64_t LONG64; typedef int32_t LONG, INT; typedef uintptr_t ULONG_PTR; typedef float FLOAT;
typedef struct { float TopLeftX, TopLeftY, Width, Height, MinDepth, MaxDepth; } D3D12_VIEWPORT;
typedef struct { LONG left, top, right, bottom; } D3D12_RECT;
typedef int D3D_PRIMITIVE_TOPOLOGY, D3D12_PRIMITIVE_TOPOLOGY; enum WMTIndexType { WMTIndexTypeUInt16, WMTIndexTypeUInt32 };
typedef void ID3D12CommandList;
#define D3D12_CLEAR_FLAG_DEPTH 1
#define D3D12_CLEAR_FLAG_STENCIL 2
#define D3D12_TEXTURE_LAYOUT_UNKNOWN 0
#define D3D12_RESOURCE_FLAG_ALLOW_SIMULTANEOUS_ACCESS 0x20
struct mad_rtvp { UINT16 level, slice, layers, plane; };
struct mad_resource { int texture; struct { int Layout; unsigned Flags; } desc; };
struct mad_pso; struct mad_rootsig; struct mad_heap; struct mad_queryheap;
struct mad_alloc { LONG generation; };
''' + cmds + bc + r'''
struct mad_list { struct mad_cmd *cmds; unsigned ncmds; int closed; struct mad_alloc *alloc; LONG recorded_generation; UINT64 kill_bloom; };
struct mad_queue { ID3D12CommandList *const *rp_lists; UINT rp_nlists, rp_idx; };
static LONG InterlockedIncrement(volatile LONG *p) { return __atomic_add_fetch(p, 1, __ATOMIC_SEQ_CST); }
static LONG InterlockedDecrement(volatile LONG *p) { return __atomic_sub_fetch(p, 1, __ATOMIC_SEQ_CST); }
static LONG64 InterlockedExchangeAdd64(volatile LONG64 *p, LONG64 v) { return __atomic_fetch_add(p, v, __ATOMIC_SEQ_CST); }
static long long cfg_ds = 1, cfg_disc = 1, cfg_alias = 0;
static long long mad_cfg_int_pe(const char *k, long long d) {
    if (!strcmp(k, "dead-store")) return cfg_ds;
    if (!strcmp(k, "dead-store-discard")) return cfg_disc;
    if (!strcmp(k, "dead-store-alias")) return cfg_alias;
    return d;
}
static void d3d12_log(const char *f, ...) { (void)f; }
typedef pthread_mutex_t SRWLOCK;
#define SRWLOCK_INIT PTHREAD_MUTEX_INITIALIZER
static void AcquireSRWLockExclusive(SRWLOCK *l) { pthread_mutex_lock(l); }
static void ReleaseSRWLockExclusive(SRWLOCK *l) { pthread_mutex_unlock(l); }
''' + rtvp + names + listat + bit + fx + 'static LONG64 g_fx_b[FX_N];\n' + ds + fate + r'''
static int bad;
#define EXPECT(c, ...) do { if (!(c)) { printf("FAIL "); printf(__VA_ARGS__); printf("\n"); bad = 1; } } while (0)
static uint64_t rs = 88172645463325252ull;
static unsigned rnd(unsigned n) { rs ^= rs << 13; rs ^= rs >> 7; rs ^= rs << 17; return (unsigned)(rs % n); }

#define NRES 6
static struct mad_resource res[NRES];
static const struct mad_rtvp views[3] = { {0, 0, 1, 0}, {1, 0, 1, 0}, {0, 2, 1, 0} };

/* An independent oracle: can anything after the pass observe what the store of
 * (r, view v, aspect a) would write? 1 = no (the store is dead), with why. */
static int oracle(const struct mad_queue *q, unsigned li, unsigned from, const struct mad_resource *r, const struct mad_rtvp *v,
                  int a, int disc_ok, int alias_ok, int *why) {
    unsigned n = 0, j, k;
    int bound = 1;
    *why = 0;
    for (; li < q->rp_nlists; li++, from = 0, bound = 0) {
        const struct mad_list *l = (const struct mad_list *)q->rp_lists[li];
        if (!l || !l->closed || (l->alloc && l->recorded_generation != l->alloc->generation)) { if (li == q->rp_idx) return 0; continue; }
        for (j = from; j < l->ncmds; j++) {
            const struct mad_cmd *c = &l->cmds[j];
            if (++n > 8192) return 0;
            switch (c->kind) {
            case MC_RTS: {
                int b = c->u.rts.depth == r;
                for (k = 0; k < c->u.rts.n; k++) b |= c->u.rts.rt[k] == r;
                bound = b; break;
            }
            case MC_DRAW: case MC_DRAW_INDEXED: case MC_DRAW_INDIRECT: case MC_DRAW_INDEXED_INDIRECT:
                if (bound) return 0;
                break;
            case MC_DISPATCH: case MC_PSO: case MC_ROOT: case MC_VP: break;
            case MC_BARRIER: {
                int named = 0, deact = 0;
                for (k = 0; k < c->u.barrier.n; k++) named |= c->u.barrier.res[k] == r;
                if (named) return 0;
                for (k = 0; k < 4 && k < c->u.barrier.nalias; k++) deact |= c->u.barrier.alias[k] == r;
                if (deact) {
                    if (alias_ok && r->desc.Layout == 0 && !(r->desc.Flags & 0x20)) { *why = 3; return 1; }
                    return 0;
                }
                if (c->u.barrier.all && c->u.barrier.cls_noref != BC_RAW_UAV) return 0;
                break;
            }
            case MC_COPY_T2T: case MC_RESOLVE: if (c->u.tt.src == r || c->u.tt.dst == r) return 0; break;
            case MC_FILL_TEX: if (c->u.filltex.res == r) return 0; break;
            case MC_DISCARD:
                if (c->u.discard.res != r) break;
                if (c->u.discard.whole && disc_ok) { *why = 2; return 1; }
                return 0;
            case MC_CLEAR_RT: case MC_CLEAR_DS: {
                int same;
                if (c->u.clear.res != r) break;
                same = !c->u.clear.nrect && c->u.clear.v.level == v->level && c->u.clear.v.slice == v->slice &&
                       c->u.clear.v.layers == v->layers && c->u.clear.v.plane == v->plane;
                if (!same) return 0;
                if (a == 0) { if (c->kind == MC_CLEAR_RT) { *why = 1; return 1; } return 0; }
                if (c->kind == MC_CLEAR_DS && (c->u.clear.flags & (a == 1 ? 1 : 2))) { *why = 1; return 1; }
                return 0;
            }
            default: break;
            }
        }
    }
    return 0;
}

static struct mad_cmd *pool;
static void gen_cmd(struct mad_cmd *c, int big) {
    unsigned t = rnd(100), k;
    memset(c, 0, sizeof *c);
    if (big) { c->kind = rnd(2) ? MC_PSO : MC_DISPATCH; return; }
    if (t < 14) { c->kind = MC_RTS; c->u.rts.n = rnd(3); for (k = 0; k < c->u.rts.n; k++) { c->u.rts.rt[k] = &res[rnd(NRES)]; c->u.rts.v[k] = views[rnd(3)]; }
                  if (rnd(2)) { c->u.rts.depth = &res[rnd(NRES)]; c->u.rts.dv = views[rnd(3)]; } }
    else if (t < 30) c->kind = rnd(4) ? MC_DRAW : MC_DRAW_INDEXED_INDIRECT;
    else if (t < 38) c->kind = MC_DISPATCH;
    else if (t < 50) { c->kind = MC_BARRIER; c->u.barrier.n = rnd(3); for (k = 0; k < c->u.barrier.n; k++) c->u.barrier.res[k] = &res[rnd(NRES)];
                       if (!rnd(4)) { c->u.barrier.all = 1; c->u.barrier.cls_noref = rnd(2) ? BC_RAW_UAV : BC_ALL; }
                       if (!rnd(3)) { c->u.barrier.nalias = 1 + rnd(5); for (k = 0; k < 4 && k < c->u.barrier.nalias; k++) c->u.barrier.alias[k] = &res[rnd(NRES)];
                                      c->u.barrier.all = 1; c->u.barrier.cls_noref = BC_ALL; } }
    else if (t < 53) { c->kind = rnd(2) ? MC_COPY_T2T : MC_RESOLVE; c->u.tt.src = &res[rnd(NRES)]; c->u.tt.dst = &res[rnd(NRES)]; }
    else if (t < 55) { c->kind = MC_FILL_TEX; c->u.filltex.res = &res[rnd(NRES)]; }
    else if (t < 67) { c->kind = rnd(2) ? MC_CLEAR_RT : MC_CLEAR_DS; c->u.clear.res = &res[rnd(NRES)]; c->u.clear.v = views[rnd(3)];
                       c->u.clear.nrect = rnd(5) ? 0 : 1 + rnd(3); c->u.clear.flags = 1 + rnd(3); }
    else if (t < 77) { c->kind = MC_DISCARD; c->u.discard.res = &res[rnd(NRES)]; c->u.discard.whole = rnd(4) != 0; }
    else c->kind = rnd(2) ? MC_PSO : MC_VP;
}
/* What the recorder puts in kill_bloom for one list. */
static UINT64 bloom_of(const struct mad_list *l) {
    UINT64 b = 0; unsigned j, k;
    for (j = 0; j < l->ncmds; j++) {
        const struct mad_cmd *c = &l->cmds[j];
        if ((c->kind == MC_CLEAR_RT || c->kind == MC_CLEAR_DS) && !c->u.clear.nrect) b |= mad_ds_bit(c->u.clear.res);
        if (c->kind == MC_DISCARD && c->u.discard.whole) b |= mad_ds_bit(c->u.discard.res);
        if (c->kind == MC_BARRIER) for (k = 0; k < 4 && k < c->u.barrier.nalias; k++) if (c->u.barrier.alias[k]->texture) b |= mad_ds_bit(c->u.barrier.alias[k]);
    }
    return b;
}

int main(void) {
    struct mad_list lists[5]; ID3D12CommandList *ptrs[5]; struct mad_alloc al = { 3 };
    struct mad_queue q;
    unsigned it, i, total = 0, dead[4] = { 0 }, nslots = 0;
    for (i = 0; i < NRES; i++) { res[i].texture = 1; res[i].desc.Layout = i == 4 ? 1 : 0; res[i].desc.Flags = i == 5 ? 0x20 : 0; }
    pool = calloc(5 * 9000, sizeof *pool);
    for (it = 0; it < 40000; it++) {
        unsigned nl = 1 + rnd(4), li = rnd(nl), from, big = it % 997 == 0;
        int disc_ok = rnd(2), alias_ok = rnd(2);
        struct mad_ds_slot s[6], one;
        unsigned n = 1 + rnd(5);
        for (i = 0; i < nl; i++) {
            unsigned j;
            lists[i].cmds = pool + i * 9000; lists[i].ncmds = big ? 3000 + rnd(1000) : rnd(40);
            lists[i].closed = rnd(12) != 0 || i == li; lists[i].alloc = &al; lists[i].recorded_generation = (rnd(15) || i == li) ? 3 : 2;
            for (j = 0; j < lists[i].ncmds; j++) gen_cmd(&lists[i].cmds[j], big && j + 3 < lists[i].ncmds);
            if (big) { struct mad_cmd *c = &lists[i].cmds[lists[i].ncmds - 1]; memset(c, 0, sizeof *c); c->kind = MC_CLEAR_RT; c->u.clear.res = &res[0]; c->u.clear.v = views[0]; }
            lists[i].kill_bloom = bloom_of(&lists[i]);
            ptrs[i] = &lists[i];
        }
        q.rp_lists = ptrs; q.rp_nlists = nl; q.rp_idx = li;
        from = lists[li].ncmds ? rnd(lists[li].ncmds + 1) : 0;
        for (i = 0; i < n; i++) {
            s[i].r = &res[rnd(NRES)]; s[i].v = &views[rnd(3)]; s[i].aspect = rnd(3);
            s[i].res = rnd(8) ? DSP_UNDECIDED : DSP_LIVE;
        }
        {
            struct mad_ds_slot pre[6]; memcpy(pre, s, sizeof s);
            mad_ds_fate(&q, &lists[li], li, from, s, n, disc_ok, alias_ok, 1);
            for (i = 0; i < n; i++) {
                int why, want;
                if (pre[i].res == DSP_LIVE) { EXPECT(s[i].res == DSP_LIVE, "a decided slot was changed"); continue; }
                want = oracle(&q, li, from, s[i].r, s[i].v, s[i].aspect, disc_ok, alias_ok, &why);
                EXPECT((s[i].res > DSP_LIVE) == want && (!want || s[i].res == why), "it %u slot %u: fate %d, oracle %d/%d (list %u of %u, from %u, aspect %d)",
                       it, i, s[i].res, want, why, li, nl, from, s[i].aspect);
                one = pre[i];
                mad_ds_fate(&q, &lists[li], li, from, &one, 1, disc_ok, alias_ok, 1);
                EXPECT(one.res == s[i].res, "it %u slot %u: alone %d, in a group %d", it, i, one.res, s[i].res);
                if (s[i].res > DSP_LIVE) {
                    UINT64 b = 0; unsigned k;
                    for (k = li; k < nl; k++) if (lists[k].closed && lists[k].recorded_generation == 3) b |= lists[k].kill_bloom;
                    EXPECT(b & mad_ds_bit(s[i].r), "it %u: a proof the kill_bloom filter does not show", it);
                    dead[s[i].res]++;
                }
                nslots++;
            }
        }
        total++;
    }
    printf("note: %u streams, %u slots; dead by clear %u, discard %u, aliasing %u\n", total, nslots, dead[1], dead[2], dead[3]);
    EXPECT(dead[1] > 500 && dead[2] > 1000 && dead[3] > 500, "every proof kind exercised");
    {   /* the 8192-command limit: a clear 9000 commands on is not looked for */
        struct mad_ds_slot t; unsigned j;
        lists[0].cmds = pool; lists[0].ncmds = 9000; lists[0].closed = 1; lists[0].alloc = NULL;
        for (j = 0; j < 9000; j++) { memset(&pool[j], 0, sizeof pool[j]); pool[j].kind = MC_PSO; }
        pool[8999].kind = MC_CLEAR_RT; pool[8999].u.clear.res = &res[0]; pool[8999].u.clear.v = views[0];
        ptrs[0] = &lists[0]; q.rp_lists = ptrs; q.rp_nlists = 1; q.rp_idx = 0;
        t.r = &res[0]; t.v = &views[0]; t.aspect = 0; t.res = DSP_UNDECIDED;
        mad_ds_fate(&q, &lists[0], 0, 0, &t, 1, 1, 1, 1);
        EXPECT(t.res == DSP_LIVE, "the limit keeps the store");
        pool[100] = pool[8999]; t.res = DSP_UNDECIDED;
        mad_ds_fate(&q, &lists[0], 0, 0, &t, 1, 1, 1, 1);
        EXPECT(t.res == DSP_CLEAR, "a clear within the limit is proof");
    }
    {   /* census-only all_live = 0 passes a barrier naming everything */
        struct mad_ds_slot t; struct mad_cmd c2[2];
        memset(c2, 0, sizeof c2); c2[0].kind = MC_BARRIER; c2[0].u.barrier.all = 1; c2[0].u.barrier.cls_noref = BC_ALL;
        c2[1].kind = MC_DISCARD; c2[1].u.discard.res = &res[1]; c2[1].u.discard.whole = 1;
        lists[0].cmds = c2; lists[0].ncmds = 2; ptrs[0] = &lists[0]; q.rp_nlists = 1;
        t.r = &res[1]; t.v = &views[0]; t.aspect = 0; t.res = DSP_UNDECIDED;
        mad_ds_fate(&q, &lists[0], 0, 0, &t, 1, 1, 0, 1);
        EXPECT(t.res == DSP_LIVE, "a barrier naming everything keeps the store");
        t.res = DSP_UNDECIDED; mad_ds_fate(&q, &lists[0], 0, 0, &t, 1, 1, 0, 0);
        EXPECT(t.res == DSP_DISCARD, "the census looks past it");
    }
    EXPECT(mad_ds_on() == 1 && g_ds_discard == 1 && g_ds_alias == 0, "defaults");
    {   /* census table */
        struct mad_resource extra[200]; int k;
        mad_fate_add(&res[0], 100); mad_fate_add(&res[0], 50); mad_fate_add(&res[1], 7);
        EXPECT(g_fate_n == 2, "one entry per texture");
        mad_fate_hit(&res[0], FX_LOADED); mad_fate_hit(&res[0], FX_CLEAR);
        EXPECT(g_fx_b[FX_LOADED] == 150 && g_fx_b[FX_CLEAR] == 0 && g_fate_n == 1, "a hit credits once and removes it");
        mad_fate_hit(&res[3], FX_OTHER);
        EXPECT(g_fx_b[FX_OTHER] == 0, "untracked textures are ignored");
        mad_fate_age(); mad_fate_age();
        EXPECT(g_fate_n == 1, "two presents keep it");
        mad_fate_age();
        EXPECT(g_fate_n == 0 && g_fx_b[FX_UNSEEN] == 7, "the third present ages it out");
        for (k = 0; k < 200; k++) mad_fate_add(&extra[k], 1);
        EXPECT(g_fate_n == MAD_FATE_N && g_fx_b[FX_FULL] == 200 - MAD_FATE_N, "a full table counts the rest as untracked");
        for (k = 0; k < MAD_FATE_N; k++) mad_fate_hit(&extra[k], FX_DISCARD);
        EXPECT(g_fate_n == 0 && g_fx_b[FX_DISCARD] == MAD_FATE_N, "every entry hit once");
    }
    if (!bad) printf("ok\n");
    return bad;
}
'''
cc = shutil.which('clang') or shutil.which('cc')
if not cc:
    print('note: no host compiler; runtime checks skipped')
else:
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        (t / 'h.c').write_text(harness)
        exe = t / 'h'
        r = subprocess.run([cc, '-std=gnu11', '-O2', '-Wall', '-Wno-unused-function', '-Wno-unused-variable', '-o', str(exe), str(t / 'h.c'), '-lpthread'],
                           capture_output=True, text=True)
        if r.returncode:
            print(r.stderr[-4000:])
        out = subprocess.run([str(exe)], capture_output=True, text=True).stdout if exe.exists() else ''
        print('\n'.join(out.strip().splitlines()[-25:]))
        check('dead-store runtime (oracle agreement, groups, filter, limit, census table)', r.returncode == 0 and out.strip().endswith('ok'))

print('PASS' if ok else 'FAILED')
sys.exit(0 if ok else 1)
