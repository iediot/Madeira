#!/usr/bin/env python3
"""Run production read telemetry with fault-injected I/O and caller registry."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
vm = (root / 'build/ntdll-unix/virtual_ios.c').read_text()
perf = (root / 'build/ntdll-unix/perf_ios.c').read_text()
server = (root / 'build/ntdll-unix/server_ios.c').read_text()
pe = (root / 'madeira-d3d12/src/pe/madeira_d3d12.c').read_text()

def function(source, name):
    start = source.index(name + '(')
    start = source.rfind('\n', 0, start) + 1
    opening = source.index('{', start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]

code = r'''
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/types.h>
typedef int BOOL;
#define FALSE 0
#define TRUE 1
#define clock_gettime_nsec_np test_clock
#define pthread_mach_thread_np test_thread_self
#define F_GETPATH 50
static int ios_frame_stats_on, inner_calls, clock_calls;
static ssize_t answer;
static uint64_t clock_value, duration;
static uint64_t clock_gettime_nsec_np(int c) {
    (void)c; clock_calls++; uint64_t v=clock_value; clock_value+=duration; return v;
}
static ssize_t ios_locked_pread_inner(int fd,void *p,size_t n,off_t off) {
    assert(fd==-1 && p==(void*)123 && n==128 && off==4096);
    inner_calls++; errno=EDOM; return answer;
}
static ssize_t ios_locked_read_inner(int fd,void *p,size_t n) {
    assert(fd==-1 && p==(void*)123 && n==128);
    inner_calls++; errno=EDOM; return answer;
}
typedef unsigned thread_t;
static uintptr_t fake_tebs[3];
static unsigned registry_count=3;
static int ios_thread_registry_count(void) {return registry_count;}
static thread_t ios_thread_registry_mach(int i) {return i+1;}
static uintptr_t ios_thread_registry_teb(int i) {return fake_tebs[i];}
static thread_t pthread_mach_thread_np(pthread_t t) {(void)t;return 2;}
static int perf_read(uint64_t a,void *out,size_t n) {
    if (!a || a==0x40) return 0; memcpy(out,(void*)(uintptr_t)a,n); return 1;
}
static _Atomic uint64_t perf_read_calls,perf_read_bytes,perf_read_errors,perf_read_short;
static _Atomic uint64_t perf_read_ns,perf_read_max_ns;
'''
code += function(perf, 'perf_max') + '\n'
code += perf[perf.index('#define PERF_IO_ACTIVE'):perf.index('static void perf_active_reads')] + '\n'
code += function(perf, 'ios_perf_read_done') + '\n'
code += function(perf, 'ios_perf_caller_ids') + '\n'
code += function(server, 'ios_xp_stall_due') + '\n'
code += function(vm, 'ios_profile_locked_read') + '\n'
code += function(vm, 'virtual_locked_read') + '\n'
code += function(vm, 'virtual_locked_pread') + '\n'
code += r'''
static void *count_reads(void *p) {
    (void)p; for(int i=0;i<1000;i++) {
        int slot=ios_perf_read_enter(3,128,4096,1,12345);
        assert(slot>=0 && (perf_io_active[slot].seq & 1));
        assert(perf_io_active[slot].fd==3 && perf_io_active[slot].started==12345);
        ios_perf_read_done(128,64,10); ios_perf_read_leave(slot);
        assert(!(perf_io_active[slot].seq & 1));
    } return NULL;
}
static void *overflow_slot(void *p) {
    (void)p;
    assert(ios_perf_read_enter(3,128,4096,1,12345)==-1);
    ios_perf_read_leave(-1); return NULL;
}
int main(void) {
    uint64_t last=0;
    assert(!ios_xp_stall_due(0,1,100,100,1000000000ull,&last));
    assert(!ios_xp_stall_due(1,0,100,100,1000000000ull,&last));
    assert(!ios_xp_stall_due(1,1,0,0,1000000000ull,&last)); /* no frames yet */
    assert(!ios_xp_stall_due(1,1,2,1,1000000000ull,&last)); /* progress */
    /* Repeated sync-completed counts trigger just like async-completed counts. */
    assert(ios_xp_stall_due(1,1,200,200,1000000000ull,&last));
    assert(!ios_xp_stall_due(1,1,200,200,1250000000ull,&last));
    assert(!ios_xp_stall_due(1,1,200,200,1999999999ull,&last));
    assert(ios_xp_stall_due(1,1,200,200,2000000000ull,&last));
    assert(!ios_xp_stall_due(1,1,1,200,2250000000ull,&last)); /* reset */
    assert(ios_xp_stall_due(1,1,1,1,1000000000ull,&last)); /* clock reset */
    uint64_t teb[12]={0}; teb[8]=0x1234;teb[9]=0x5678;fake_tebs[1]=(uintptr_t)teb;
    assert(ios_perf_caller_ids()==0x123400005678ull);
    fake_tebs[1]=0;assert(ios_perf_caller_ids()==0);registry_count=0;
    assert(ios_perf_caller_ids()==0);
    answer=128;duration=25000000;
    assert(virtual_locked_pread(-1,(void*)123,128,4096)==128 && errno==EDOM);
    assert(inner_calls==1 && clock_calls==0 && perf_read_calls==0);
    ios_frame_stats_on=1;
    assert(virtual_locked_pread(-1,(void*)123,128,4096)==128 && errno==EDOM);
    answer=-1;
    assert(virtual_locked_pread(-1,(void*)123,128,4096)==-1 && errno==EDOM);
    answer=0;duration=1;
    assert(virtual_locked_read(-1,(void*)123,128)==0 && errno==EDOM);
    assert(perf_read_calls==3 && perf_read_bytes==128 && perf_read_errors==1 && perf_read_short==1);
    assert(perf_read_ns==50000001 && perf_read_max_ns==25000000);
    pthread_t threads[8];
    for(int i=0;i<8;i++) assert(!pthread_create(&threads[i],NULL,count_reads,NULL));
    for(int i=0;i<8;i++) pthread_join(threads[i],NULL);
    assert(perf_read_calls==8003 && perf_read_bytes==128+8000*64 && perf_read_short==8001);
    assert(perf_read_ns==50080001 && perf_read_max_ns==25000000);
    assert(perf_io_slots==9 && perf_io_untracked==0);
    /* An unregistered late thread above the cap must not index out of bounds. */
    perf_io_slots=PERF_IO_ACTIVE;
    pthread_t overflow; assert(!pthread_create(&overflow,NULL,overflow_slot,NULL));
    pthread_join(overflow,NULL); assert(perf_io_untracked==1);
    puts("PASS: production read wrappers preserve results/errno, profiling-off fast path, owner IDs, concurrent counters");
}
'''
# The newly added gap sampler must not add another thread suspension or unsafe
# stack walk to a stalled application. Existing periodic sampler is separate.
snapshot = function(perf, 'ios_perf_stall_snapshot')
assert 'thread_suspend(' not in snapshot and 'thread_resume(' not in snapshot
assert 'if (nids > 16) nids = 16;' in snapshot
# Crucial wiring regression: a synchronous game must update the same counter
# as async submission, and the async worker must not count a frame twice.
present = pe[pe.index('static void mad_present_run(struct mad_swapchain *s, UINT idx) {'):]
present = present[:present.index('static HRESULT STDMETHODCALLTYPE swap_GetBuffer')]
worker = pe[pe.index('static DWORD WINAPI mad_sub_worker(void *arg) {'):]
worker = worker[:worker.index('static void mad_queue_drain')]
completion = 'InterlockedIncrement64(&g_xp.pres_done);'
assert present.count(completion) == 1 and completion not in worker
assert present.index('MTLCommandBuffer_commit(cb)') < present.index(completion)
assert pe.count(completion) == 1
with tempfile.TemporaryDirectory(prefix='madeira-hitch-test-') as directory:
    exe = Path(directory) / 'test'
    subprocess.run(['xcrun', 'clang', '-x', 'c', '-std=c11', '-fsanitize=address,undefined',
                    '-pthread', '-o', str(exe), '-'], input=code, text=True, check=True)
    subprocess.run([str(exe)], check=True)
