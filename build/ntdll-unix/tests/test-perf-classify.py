#!/usr/bin/env python3
"""Host-only boundary checks of the actual classifier helpers (no iOS runtime)."""
from pathlib import Path
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / 'perf_ios.c').read_text()

def section(start, end):
    return source[source.index(start):source.index(end)]

code = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include "perf_ios.h"
#define MAXTHREADNAMESIZE 64
#define PERF_THREADS 512
typedef unsigned thread_t;
struct perf_thread { uint64_t id, cpu_ns; uint32_t seen; };
void *ios_jit_rx_base_global;
size_t ios_jit_pool_size_global;
uintptr_t ios_fex_arena_base_unix, ios_fex_arena_end_unix;
'''
code += section('#define CLASS_DYLIBS', 'static void class_refresh_dyld')
code += section('static enum perf_class class_pc(', 'static thread_t class_pick(')
code += section('static void class_bucket_add(', 'static void class_report(void)')
code += section('static void class_report_top(', 'static void *perf_classifier(')
code += r'''
static enum perf_class classify(uintptr_t pc) {
    const char *name; uintptr_t offset;
    return class_pc(pc, 0x140000, &name, &offset);
}
int main(void) {
    const char *name; uintptr_t offset;
    assert(class_pe_kind("D3D11.DLL") == C_DXMT);
    assert(class_pe_kind("d3d9-emulated.dll") == C_DXMT);
    assert(class_pe_kind("/dir/d3d10core.dll") == C_DXMT);
    assert(class_pe_kind("winemetal") == C_DXMT);
    assert(class_pe_kind("libarm64ecfex.dll") == C_FEX);
    assert(class_pe_kind("LIBWOW64FEX.DLL") == C_FEX);
    assert(class_pe_kind("xtajit64.dll") == C_FEX);
    assert(class_pe_kind("d3d11helper.dll") == C_PE);
    assert(class_pe_kind("kernelbase.dll") == C_PE);
    assert(class_pe_kind("?") == C_PE);
    ios_jit_rx_base_global = (void *)0x100000;
    ios_jit_pool_size_global = 0x100000;
    class_npe = 1;
    class_pe[0] = (struct ios_perf_image){ .base=0x110000, .size=0x2000, .name="d3d11.dll" };
    class_pe_classes[0] = class_pe_kind(class_pe[0].name);
    assert(classify(0x100000) == C_GAME);
    assert(classify(0x10ffff) == C_GAME);
    assert(class_pc(0x110123, 0, &name, &offset) == C_DXMT);
    assert(!strcmp(name, "d3d11.dll") && offset == 0x123);
    assert(classify(0x111fff) == C_DXMT);
    assert(classify(0x112000) == C_GAME);
    assert(classify(0x13ffff) == C_GAME);
    assert(classify(0x140000) == C_FEX);
    assert(classify(0x143fff) == C_FEX);
    assert(classify(0x144000) == C_GAME);
    assert(classify(0x200000) == C_OTHER);
    ios_fex_arena_base_unix = 0x300000;
    ios_fex_arena_end_unix = 0x400000;
    assert(classify(0x300000) == C_FEX);
    assert(classify(0x400000) == C_OTHER);
    class_ndylibs = 3;
    class_dylibs[0] = (struct class_image){ .lo=0x500000, .hi=0x510000, .base=0x500000, .kind=C_UNIX, .name="Madeira" };
    class_dylibs[1] = (struct class_image){ .lo=0x600000, .hi=0x610000, .base=0x600000, .kind=C_METAL, .name="Metal" };
    class_dylibs[2] = (struct class_image){ .lo=0x700000, .hi=0x710000, .base=0x700000, .kind=C_SYSTEM, .name="libsystem_kernel" };
    class_ntraps = 1; class_traps[0].lo=0x701000; class_traps[0].hi=0x701020;
    assert(classify(0x500100) == C_UNIX);
    assert(classify(0x600100) == C_METAL);
    assert(classify(0x700fff) == C_SYSTEM);
    assert(classify(0x701000) == C_BLOCKED);
    assert(classify(0x70101f) == C_BLOCKED);
    assert(classify(0x701020) == C_SYSTEM);
    assert(classify(0x710000) == C_OTHER);
    struct class_row r = { .id=1, .tid=0x42 };
    for (unsigned i=0; i<12; ++i)
        for (unsigned j=0; j<=i; ++j) class_bucket_add(&r, C_DXMT, "d3d11.dll", i*0x1000+3);
    class_bucket_add(&r, C_GAME, "ignored", 0);
    class_report_top();
    for (unsigned i=0; i<CLASS_BUCKETS; ++i) assert(!class_buckets[i].hits);
    for (unsigned i=0; i<CLASS_BUCKETS+1; ++i) class_bucket_add(&r, C_PE, "ntdll.dll", i*0x1000);
    assert(class_dropped == 1);
    puts("classifier boundaries, ownership, bucket ranking and overflow: PASS");
}
'''
with tempfile.TemporaryDirectory(prefix='madeira-classify-') as tmp:
    test = Path(tmp) / 'test.c'
    exe = Path(tmp) / 'test'
    test.write_text(code)
    subprocess.run([os.environ.get('CC', 'cc'), '-std=c11', '-D_DARWIN_C_SOURCE',
                    '-I', str(root), str(test), '-o', str(exe)], check=True)
    result = subprocess.run([str(exe)], check=True, capture_output=True, text=True)
    lines = result.stderr.splitlines()
    assert len(lines) == 8, lines
    assert [int(line.rsplit('samples=', 1)[1]) for line in lines] == list(range(12, 4, -1)), lines
    print(result.stdout, end='')
