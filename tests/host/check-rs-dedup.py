#!/usr/bin/env python3
"""madeira-d3d12 rs-dedup, run from production source: identical root
signature blobs for one device share the live object (as D3D12 on Windows
does), different blobs or devices do not, the last Release takes the entry
out of the table in the same step (a later create makes a new object, never
revives a freed one), rs-dedup = 0 restores one object per call, and racing
creates/releases stay consistent (ASan/TSan-style stress under ASan)."""
from pathlib import Path
import subprocess
import tempfile
import sys

root = Path(__file__).resolve().parents[2]
source = (root / 'madeira-d3d12/src/pe/madeira_d3d12.c').read_text()

def cut(begin, end):
    b = source.index(begin)
    return source[b:source.index(end, b)]

block = cut('struct mad_rsdup { struct mad_rsdup *next;', 'static HRESULT STDMETHODCALLTYPE heap_QI')
wrapper = cut('static HRESULT STDMETHODCALLTYPE device_CreateRootSignature(ID3D12Device *This, UINT node,\n        const void *blob, SIZE_T blob_len, REFIID riid, void **out) {   /* madeira-bcd: timed */',
              'static HRESULT device_CreateRootSignature_impl(ID3D12Device *This, UINT node,\n        const void *blob, SIZE_T blob_len, REFIID riid, void **out) {')
assert 'struct mad_rsdup *rsd;' in source

harness = r'''
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <assert.h>
#include <pthread.h>
typedef uint64_t UINT64; typedef uint32_t UINT; typedef int32_t LONG; typedef int64_t LONG64; typedef uint32_t ULONG;
typedef size_t SIZE_T; typedef int HRESULT; typedef struct { int x; } IID; typedef const IID *REFIID;
typedef struct ID3D12Device ID3D12Device; typedef struct ID3D12RootSignature ID3D12RootSignature;
#define STDMETHODCALLTYPE
#define SUCCEEDED(h) ((h) >= 0)
#define S_OK 0
#define E_INVALIDARG (-3)
typedef pthread_mutex_t SRWLOCK;
#define SRWLOCK_INIT PTHREAD_MUTEX_INITIALIZER
#define AcquireSRWLockExclusive pthread_mutex_lock
#define ReleaseSRWLockExclusive pthread_mutex_unlock
static LONG InterlockedIncrement(volatile LONG *p) { return __atomic_add_fetch(p, 1, __ATOMIC_SEQ_CST); }
static LONG InterlockedDecrement(volatile LONG *p) { return __atomic_sub_fetch(p, 1, __ATOMIC_SEQ_CST); }
static LONG64 InterlockedExchangeAdd64(volatile LONG64 *p, LONG64 v) { return __atomic_fetch_add(p, v, __ATOMIC_SEQ_CST); }
static int cfg_on = 1;
static long long mad_cfg_int_pe(const char *k, long long d) { (void)k; (void)d; return cfg_on; }
static LONG64 mad_qpc(void) { return 0; }
static volatile LONG64 g_pt_rs; static volatile LONG g_pn_rs, g_rsd_made, g_rsd_shared, live, freed;
static void d3d12_log(const char *f, ...) { (void)f; }
static void mad_pd_purge(const void *o) { (void)o; }
struct mad_rsdup;
struct mad_rootsig { void *vtbl; volatile LONG refs; const IID *iid; const char *name; struct mad_rsdup *rsd; int content; };
struct mad_obj { void *vtbl; volatile LONG refs; const IID *iid; const char *name; };
static ULONG mad_release(struct mad_obj *o) { LONG r = InterlockedDecrement(&o->refs); if (!r) { InterlockedDecrement(&live); InterlockedIncrement(&freed); free(o); } return r; }
static HRESULT rootsig_QI(ID3D12RootSignature *T, REFIID r, void **o) { (void)r; InterlockedIncrement(&((struct mad_rootsig *)T)->refs); *o = T; return S_OK; }
static HRESULT device_CreateRootSignature_impl(ID3D12Device *This, UINT node, const void *blob, SIZE_T len, REFIID riid, void **out);
''' + block.replace('d3d12_log("[madeira-d3d12] destroyed %s\\n", r->name);', 'InterlockedDecrement(&live); InterlockedIncrement(&freed);') + wrapper + r'''
static HRESULT device_CreateRootSignature_impl(ID3D12Device *This, UINT node, const void *blob, SIZE_T len, REFIID riid, void **out) {
    struct mad_rootsig *r; HRESULT hr; (void)This; (void)node;
    if (!len || ((const unsigned char *)blob)[0] == 0xee) return E_INVALIDARG;   /* a refused blob */
    r = calloc(1, sizeof *r); r->refs = 1; r->name = "RootSignature"; r->content = ((const unsigned char *)blob)[0];
    InterlockedIncrement(&live);
    hr = rootsig_QI((ID3D12RootSignature *)r, riid, out);
    rootsig_Release((ID3D12RootSignature *)r);
    return hr;
}
static ID3D12Device *devA = (ID3D12Device *)0x1000, *devB = (ID3D12Device *)0x2000;
static IID iid;
static void *mk(ID3D12Device *d, unsigned char tag, unsigned n) {
    unsigned char b[64]; void *o = NULL; memset(b, tag, sizeof b); b[63] = (unsigned char)n;
    assert(device_CreateRootSignature(d, 0, b, sizeof b, &iid, &o) == S_OK && o); return o;
}
static void *worker(void *arg) {
    unsigned i, seed = (unsigned)(uintptr_t)arg;
    for (i = 0; i < 20000; i++) {
        void *o; seed = seed * 1103515245 + 12345;
        o = mk(devA, (unsigned char)(1 + (seed >> 16) % 3), 0);
        assert(((struct mad_rootsig *)o)->refs > 0);
        rootsig_Release(o);
    }
    return NULL;
}
int main(void) {
    void *a = mk(devA, 1, 0), *b = mk(devA, 1, 0), *c = mk(devA, 2, 0), *d = mk(devB, 1, 0), *e = mk(devA, 1, 1), *f;
    unsigned char bad[4] = { 0xee, 0, 0, 0 }; void *o = NULL;
    assert(a == b && a != c && a != d && a != e && ((struct mad_rootsig *)a)->refs == 2);
    assert(g_rsd_shared == 1 && g_rsd_made == 4 && live == 4);
    assert(device_CreateRootSignature(devA, 0, bad, sizeof bad, &iid, &o) == E_INVALIDARG && !o);
    rootsig_Release(a); assert(live == 4);
    rootsig_Release(b); assert(live == 3);                 /* last reference: gone, entry with it */
    f = mk(devA, 1, 0); assert(live == 4 && g_rsd_made == 5);   /* a new object, not the freed one revived */
    rootsig_Release(f); rootsig_Release(c); rootsig_Release(d); rootsig_Release(e);
    assert(live == 0);
    for (unsigned i = 0; i < 1024; i++) assert(!g_rsd[i]);
    cfg_on = 0; g_rsd_on = -1;
    a = mk(devA, 1, 0); b = mk(devA, 1, 0); assert(a != b); rootsig_Release(a); rootsig_Release(b); assert(live == 0);
    cfg_on = 1; g_rsd_on = -1;
    { pthread_t t[6]; unsigned i;
      for (i = 0; i < 6; i++) pthread_create(&t[i], NULL, worker, (void *)(uintptr_t)(i + 1));
      for (i = 0; i < 6; i++) pthread_join(t[i], NULL);
      assert(live == 0);
      for (i = 0; i < 1024; i++) assert(!g_rsd[i]); }
    puts("PASS: rs-dedup shares identical live blobs per device, last release removes the entry, switch off, concurrent create/release");
    return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='madeira-rs-dedup-') as directory:
    exe = Path(directory) / 'test'
    subprocess.run(['xcrun', 'clang', '-std=gnu11', '-x', 'c', '-O1', '-pthread', '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                    '-Wno-unused-function', '-o', str(exe), '-'], input=harness, text=True, check=True)
    sys.exit(subprocess.run([str(exe)], timeout=120).returncode)
