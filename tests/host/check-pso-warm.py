#!/usr/bin/env python3
"""Run the production native warm-up queue with mock Metal builders/objects.

Tests ownership, deep copies, queue bounds, low-memory skip and all three job
kinds; not a GPU compilation benchmark or a guarantee of an iPad speedup.
"""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
code = r'''
#import <Foundation/Foundation.h>
#import <QuartzCore/QuartzCore.h>
#include <mach/mach.h>
#include <stdatomic.h>
#include <assert.h>
#include <unistd.h>
#define DXMT_NATIVE 1
#include "winemetal.h"
static _Atomic unsigned live, builds, headroom = 512, remote;
static _Atomic int fail_build;
@interface Token : NSObject
@property int tag;
@end
@implementation Token
- (id)init { self = [super init]; if (self) atomic_fetch_add(&live, 1); return self; }
- (void)dealloc { atomic_fetch_sub(&live, 1); [super dealloc]; }
@end
static Token *token(int tag) { Token *t = [Token new]; t.tag = tag; return t; }
static uint64_t madeira_avail_memory(uint64_t foot) { return (uint64_t)atomic_load(&headroom) << 20; }
static int wmtr_enabled(void) { return atomic_load(&remote); }
static long long madeira_cfg_int(const char *key, long long fallback) { return 2; }
static dispatch_semaphore_t gate;
struct unixcall_mtldevice_newcomputepso {
    obj_handle_t device; struct WMTConstMemoryPointer info; obj_handle_t ret_error, ret_pso;
};
struct unixcall_mtldevice_newrenderpso {
    obj_handle_t device; struct WMTConstMemoryPointer info; obj_handle_t ret_error, ret_pso;
};
struct unixcall_mtldevice_newrenderpso_vd {
    obj_handle_t device; struct WMTConstMemoryPointer info, vd; obj_handle_t ret_error, ret_pso;
};
static void validate(obj_handle_t device, obj_handle_t function, struct WMTConstMemoryPointer lookup, unsigned count) {
    dispatch_semaphore_wait(gate, DISPATCH_TIME_FOREVER);
    assert([(Token *)(uintptr_t)device tag] == 5);
    assert([(Token *)(uintptr_t)function tag] == 23);
    if (count) {
        assert(count == 1);
        assert([(Token *)(uintptr_t)((obj_handle_t *)lookup.ptr)[0] tag] == 77);
    }
    atomic_fetch_add(&builds, 1);
}
static int _MTLDevice_newComputePipelineState(void *ptr) {
    struct unixcall_mtldevice_newcomputepso *a = ptr;
    const struct WMTComputePipelineInfo *c = a->info.ptr;
    validate(a->device,c->compute_function,c->binary_archives_for_lookup,c->num_binary_archives_for_lookup);
    assert(c->immutable_buffers == 42 && !c->binary_archive_for_serialization);
    a->ret_pso = atomic_load(&fail_build) ? 0 : (obj_handle_t)token(91); return 0;
}
static int _MTLDevice_newRenderPipelineState(void *ptr) {
    struct unixcall_mtldevice_newrenderpso *a = ptr;
    const struct WMTRenderPipelineInfo *r = a->info.ptr;
    validate(a->device,r->vertex_function,r->binary_archives_for_lookup,r->num_binary_archives_for_lookup);
    assert(r->immutable_vertex_buffers == 42 && !r->binary_archive_for_serialization);
    assert([(Token *)(uintptr_t)r->fragment_function tag] == 23);
    a->ret_pso = atomic_load(&fail_build) ? 0 : (obj_handle_t)token(91); return 0;
}
static int _MTLDevice_newRenderPipelineStateVD(void *ptr) {
    struct unixcall_mtldevice_newrenderpso_vd *a = ptr;
    assert(((const struct WMTVertexDescriptorInfo *)a->vd.ptr)->attribute_mask == 0x123);
    struct unixcall_mtldevice_newrenderpso b = {0};
    b.device = a->device; b.info = a->info;
    _MTLDevice_newRenderPipelineState(&b); a->ret_pso = b.ret_pso; return 0;
}
#include "pipeline_warm.h"
static void drain(unsigned target) {
    for (unsigned i = 0; i < 5000; i++) {
        if (atomic_load(&madeira_warm_done) == target && !atomic_load(&madeira_warm_pending)) return;
        usleep(1000);
    }
    assert(!"warm-up queue did not drain");
}
int main(void) { @autoreleasepool {
    gate = dispatch_semaphore_create(0);
    Token *device = token(5);
    for (unsigned kind = 0; kind < 3; kind++) {
        Token *fn = token(23), *archive = token(77);
        obj_handle_t archives[] = {(obj_handle_t)archive};
        struct WMTRenderPipelineInfo r = {0};
        r.vertex_function = r.fragment_function = (obj_handle_t)fn;
        r.immutable_vertex_buffers = 42; r.binary_archives_for_lookup.ptr = archives;
        r.num_binary_archives_for_lookup = 1; r.binary_archive_for_serialization = (obj_handle_t)archive;
        struct WMTComputePipelineInfo c = {0};
        c.compute_function = (obj_handle_t)fn; c.immutable_buffers = 42;
        c.binary_archives_for_lookup.ptr = archives; c.num_binary_archives_for_lookup = 1;
        c.binary_archive_for_serialization = (obj_handle_t)archive;
        struct WMTVertexDescriptorInfo vd = {0}; vd.attribute_mask = 0x123;
        struct madeira_warm_request q = {(obj_handle_t)device, (uint64_t)(kind == 2 ? (void *)&c : (void *)&r), (uint64_t)&vd};
        assert(madeira_warm_submit(&q, kind) == 1);
        memset(&r,0,sizeof r); memset(&c,0,sizeof c); memset(&vd,0,sizeof vd);
        archives[0] = 0; [fn release]; [archive release];
    }
    for (unsigned i = 0; i < 3; i++) dispatch_semaphore_signal(gate);
    drain(3); assert(atomic_load(&builds) == 3 && atomic_load(&live) == 1);
    assert(madeira_warm_workers == 2);
    Token *fn = token(23);
    struct WMTRenderPipelineInfo r = {0};
    r.vertex_function = r.fragment_function = (obj_handle_t)fn; r.immutable_vertex_buffers = 42;
    struct madeira_warm_request q = {(obj_handle_t)device, (uint64_t)&r, 0};
    for (unsigned i = 0; i < MADEIRA_WARM_PENDING + 10; i++) assert(madeira_warm_submit(&q,0) == 1);
    assert(atomic_load(&madeira_warm_pending) == MADEIRA_WARM_PENDING);
    assert(atomic_load(&madeira_warm_skipped) >= 10);
    atomic_store(&headroom,0);
    [fn release];
    for (unsigned i = 0; i < MADEIRA_WARM_PENDING; i++) dispatch_semaphore_signal(gate);
    drain(3 + MADEIRA_WARM_PENDING);
    assert(atomic_load(&live) == 1);
    assert(madeira_warm_submit(&q,0) == 1); /* low headroom: no dereference of the now-dead fn */
    assert(!atomic_load(&madeira_warm_pending));
    atomic_store(&remote,1); assert(madeira_warm_submit(&q,0) == 0);
    atomic_store(&remote,0); assert(madeira_warm_submit(&q,99) == 0);
    atomic_store(&headroom,512); atomic_store(&fail_build,1);
    fn = token(23); r.vertex_function = r.fragment_function = (obj_handle_t)fn;
    assert(madeira_warm_submit(&q,0) == 1); [fn release];
    drain(4 + MADEIRA_WARM_PENDING);
    assert(atomic_load(&madeira_warm_failed) == 1 && atomic_load(&live) == 1);
    [device release]; assert(atomic_load(&live) == 0);
    dispatch_release(gate);
    puts("PASS: warm-up deep copies, retained lifetimes, render/VD/compute, bounded admission and memory-pressure release");
} return 0; }
'''
with tempfile.TemporaryDirectory(prefix='madeira-warm-test-') as directory:
    path = Path(directory)
    (path / 'test.m').write_text(code)
    subprocess.run(['xcrun','clang','-fblocks','-fsanitize=address,undefined','-g',
                    '-I'+str(root/'dxmt/src/winemetal'), '-I'+str(root/'dxmt/src/winemetal/unix'),
                    '-framework','Foundation','-framework','QuartzCore',
                    str(path/'test.m'),'-o',str(path/'test')],check=True)
    subprocess.run([str(path/'test')],check=True,timeout=20)
