#!/usr/bin/env python3
"""Run production descriptor versioning/cache lookup with stub table allocation."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / 'madeira-d3d12/src/pe/madeira_d3d12.c').read_text()
versions = source[source.index('static SRWLOCK g_descriptor_heaps_lock'):source.index('/* An RTV or DSV handle')]
begin = source.index('    struct {\n        const struct mad_rootsig *rs;', source.index('UINT64 root_version[2]'))
cache = source[begin:source.index('    obj_handle_t cenc;', begin)]
begin = source.index('static int mad_air_build_tables_ex(struct mad_exec')
lookup = source[begin:source.index('static int mad_air_build_tables(struct mad_exec', begin)]
harness = r'''
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <assert.h>
#include <pthread.h>
typedef uint64_t UINT64, obj_handle_t;
typedef uint32_t UINT, UINT32;
typedef int64_t LONG64;
typedef int D3D12_DESCRIPTOR_HEAP_TYPE;
enum { D3D12_DESCRIPTOR_HEAP_TYPE_CBV_SRV_UAV=0, D3D12_DESCRIPTOR_HEAP_TYPE_SAMPLER=1, RTV=2 };
typedef pthread_mutex_t SRWLOCK;
#define SRWLOCK_INIT PTHREAD_MUTEX_INITIALIZER
#define AcquireSRWLockExclusive pthread_mutex_lock
#define ReleaseSRWLockExclusive pthread_mutex_unlock
#define AcquireSRWLockShared pthread_mutex_lock
#define ReleaseSRWLockShared pthread_mutex_unlock
static LONG64 InterlockedIncrement64(volatile LONG64 *p) { return __atomic_add_fetch(p,1,__ATOMIC_SEQ_CST); }
static LONG64 InterlockedCompareExchange64(volatile LONG64 *p,LONG64 exchange,LONG64 expected) {
 __atomic_compare_exchange_n(p,&expected,exchange,0,__ATOMIC_SEQ_CST,__ATOMIC_SEQ_CST);return expected;
}
struct mad_descriptor { UINT64 gpu_va,texture_view_id,metadata; };
struct mad_heap { int shader_visible; struct mad_descriptor *cpu; UINT count; int type;
 volatile LONG64 table_version; struct mad_heap *table_version_next; };
''' + versions + r'''
struct mad_rootsig { int unused; };
struct mad_pso { int unused; };
struct madeira_ir_air_range { int unused; };
struct skin { int tab_on; };
struct mad_exec { UINT64 root_version[2], root[4], croot[4]; struct mad_heap *srv,*smp; struct skin *sk;
''' + cache + r'''
};
static int g_replay_split,g_dump_tables;
static volatile LONG64 g_air_cache_hits,g_air_cache_misses;
static UINT64 mad_tick(void) { return 0; }
enum { RS_TABLES };
static void mad_rs_step(int a,UINT64 *b) {}
static unsigned builds;
static int mad_air_build_tables_ex_body(struct mad_exec *e,const struct mad_rootsig *rs,const UINT64 *r,
 const UINT32 (*cs)[64],const struct mad_pso *p,UINT cb,UINT ar,UINT aq,UINT n,
 const struct madeira_ir_air_range *v,unsigned vis,obj_handle_t *b,UINT64 *bo,obj_handle_t *a,UINT64 *ao) {
 ++builds; *b=builds; *a=builds+100; *bo=8; *ao=16; return 1;
}
''' + lookup + r'''
static void *writer(void *heap) {
 struct mad_heap *h=heap;
 for(unsigned i=0;i<10000;i++) mad_descriptor_write(h->cpu,1,h->type);
 return NULL;
}
int main(void) {
 struct mad_descriptor da[8]={0},db[8]={0},ds[4]={0},staging[4]={0};
 struct mad_heap a={1,da,8,0},b={1,db,8,0},s={1,ds,4,1},stage={0,staging,4,0};
 mad_descriptor_register(&a); mad_descriptor_register(&b); mad_descriptor_register(&s);mad_descriptor_register(&stage);
 assert(mad_descriptor_version(&a)==1 && mad_descriptor_version(&stage)==0);
 struct mad_exec e={0};e.srv=&a;e.smp=&s;e.root_version[0]=1;
 struct mad_rootsig rs={0};struct mad_pso p={0};struct madeira_ir_air_range air={0};
 UINT32 constants[4][64]={0};obj_handle_t cb,arg;UINT64 co,ao;
 #define BUILD() mad_air_build_tables_ex(&e,&rs,e.root,constants,&p,1,2,4,1,&air,~0u,&cb,&co,&arg,&ao)
 assert(BUILD() && builds==1);assert(BUILD() && builds==1 && g_air_cache_hits==1);
 mad_descriptor_write(staging,1,0);mad_descriptor_write(db,1,0);mad_descriptor_write(da,1,RTV);
 assert(BUILD() && builds==1); // unrelated heap, staging and RTV changes cannot invalidate
 mad_descriptor_write(da+7,1,0);assert(BUILD() && builds==2);
 mad_descriptor_write(ds+3,1,1);assert(BUILD() && builds==3);
 mad_descriptor_write(da+8,1,0);assert(BUILD() && builds==3); // exact end excluded
 e.root_version[0]++;assert(BUILD() && builds==4);
 e.srv=&b;assert(BUILD() && builds==5); // heap identity in key, not just version
 g_dump_tables=1;assert(BUILD() && builds==6);g_dump_tables=0;
 struct skin sk={1};e.sk=&sk;assert(BUILD() && builds==7);e.sk=NULL;
 pthread_t threads[4];UINT64 before=mad_descriptor_version(&a);
 for(unsigned i=0;i<4;i++)pthread_create(&threads[i],NULL,writer,&a);
 for(unsigned i=0;i<4;i++)pthread_join(threads[i],NULL);
 assert(mad_descriptor_version(&a)==before+40000);
 mad_descriptor_unregister(&a);before=mad_descriptor_version(&a);mad_descriptor_write(da,1,0);
 assert(mad_descriptor_version(&a)==before);
 mad_descriptor_unregister(&b);mad_descriptor_unregister(&s);assert(!g_descriptor_heaps);
 puts("PASS: production AIR cache/versioning; bound heaps invalidate, unrelated writes reuse, roots/heap identity/debug bypass, concurrent writers and unregister");
 return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='madeira-air-cache-') as directory:
    exe = Path(directory) / 'test'
    subprocess.run(['xcrun','clang','-std=gnu11','-x','c','-pthread','-fsanitize=address,undefined',
                    '-o',str(exe),'-'],input=harness,text=True,check=True)
    subprocess.run([str(exe)],check=True,timeout=20)
assert 'g_descriptor_version' not in source
assert source.count('mad_descriptor_write(') == 7
