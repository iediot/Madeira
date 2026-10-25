#!/usr/bin/env python3
"""Production typed-view index versus the original scan, with allocation faults."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root/'madeira-d3d12/src/pe/madeira_d3d12.c').read_text()
start = source.index('#define MAD_TVH_MIN')
functions = source[start:source.index('static int mad_typed_buffer_view(', start)]
harness = r'''
#include <assert.h>
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
typedef uint32_t UINT,UINT32;
typedef uint64_t UINT64;
typedef uint8_t UINT8;
struct mad_tview { UINT fmt; UINT64 off,num; UINT8 uav; };
struct mad_resource { struct mad_tview *tview; unsigned ntview; UINT32 *tvh; unsigned tvh_cap; };
static long long mad_cfg_int_pe(const char *key,long long fallback) { return fallback; }
static int fail_alloc;
static void *test_calloc(size_t n,size_t bytes) { return fail_alloc ? NULL : calloc(n,bytes); }
#define calloc test_calloc
''' + functions + r'''
static unsigned scan(const struct mad_resource *r,UINT fmt,UINT64 off,UINT64 num,UINT uav) {
    for(unsigned i=0;i<r->ntview;i++)
        if(r->tview[i].fmt==fmt && r->tview[i].off==off && r->tview[i].num==num && r->tview[i].uav==uav) return i;
    return r->ntview;
}
int main(void) {
    struct mad_resource r={0};r.tview=calloc(20000,sizeof(*r.tview));assert(r.tview);
    for(unsigned i=0;i<16384;i++) {
        r.tview[i]=(struct mad_tview){1+i%5,(UINT64)i*64,1+i%71,i%2};
        r.ntview=i+1;mad_tvh_add(&r,i);
        if(i==30) assert(!r.tvh);
        if(i==31) assert(r.tvh && r.tvh_cap==128);
        if(i<65) assert(mad_tvh_find(&r,r.tview[i].fmt,r.tview[i].off,r.tview[i].num,r.tview[i].uav)==i);
    }
    assert(r.tvh_cap==32768);
    unsigned seed=42;
    for(unsigned n=0;n<2000;n++) {
        seed=seed*1664525+1013904223;unsigned i=seed%r.ntview;
        struct mad_tview v=r.tview[i];
        assert(mad_tvh_find(&r,v.fmt,v.off,v.num,v.uav)==scan(&r,v.fmt,v.off,v.num,v.uav));
        assert(mad_tvh_find(&r,v.fmt+100,v.off,v.num,v.uav)==r.ntview);
        assert(mad_tvh_find(&r,v.fmt,v.off+1,v.num,v.uav)==r.ntview);
        assert(mad_tvh_find(&r,v.fmt,v.off,v.num+100,v.uav)==r.ntview);
        assert(mad_tvh_find(&r,v.fmt,v.off,v.num,!v.uav)==r.ntview);
    }
    struct mad_tview old=r.tview[7];r.tview[7].fmt=0; /* invalidated shadow view */
    assert(mad_tvh_find(&r,old.fmt,old.off,old.num,old.uav)==r.ntview);
    r.tview[r.ntview]=old;r.ntview++;fail_alloc=1;
    mad_tvh_add(&r,r.ntview-1); /* growth OOM: must drop index and use scan */
    assert(!r.tvh && !r.tvh_cap);
    assert(mad_tvh_find(&r,old.fmt,old.off,old.num,old.uav)==r.ntview-1);
    fail_alloc=0;mad_tvh_add(&r,r.ntview-1);
    assert(r.tvh && mad_tvh_find(&r,old.fmt,old.off,old.num,old.uav)==r.ntview-1);
    unsigned actual=r.ntview;r.ntview=MAD_TVH_MAX+1;
    mad_tvh_add(&r,0);assert(!r.tvh);r.ntview=actual;
    assert(mad_tvh_find(&r,old.fmt,old.off,old.num,old.uav)==actual-1);
    free(r.tview);free(r.tvh);
    struct mad_tview one={42,64,9,1};r=(struct mad_resource){.tview=&one,.ntview=1};
    g_tvh_on=0;mad_tvh_add(&r,0);assert(!r.tvh);
    assert(mad_tvh_find(&r,42,64,9,1)==0);
    puts("PASS: typed-view index matches scan, complete-key collisions, growth, invalidation, OOM/cap fallback and opt-out");
}
'''
with tempfile.TemporaryDirectory(prefix='madeira-view-index-') as directory:
    exe = Path(directory)/'test'
    subprocess.run(['xcrun','clang','-O1','-x','c','-fsanitize=address,undefined','-o',str(exe),'-'],
                   input=harness,text=True,check=True)
    subprocess.run([str(exe)],check=True,timeout=20)
start=source.index('static int mad_typed_buffer_view(')
body=source[start:source.index('\n}\n',start)+3]
assert 'k = mad_tvh_find(' in body and 'mad_tvh_add(r, k);' in body
assert body.index('EnterCriticalSection(&d->view_lock)') < body.index('k = mad_tvh_find(')
assert 'free(r->tvh)' in source[source.index('static ULONG STDMETHODCALLTYPE res_Release('):]
