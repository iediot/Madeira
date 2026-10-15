#!/usr/bin/env python3
"""Exercise imported NtRaiseException repair with mocked mapping queries."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / 'build/ntdll-unix/thread_ios.c').read_text()
begin = source.index('    if (rec && rec->ExceptionCode == 0xE06D7363')
end = source.index('    /* ml845:', begin)
body = source[begin:end]
harness = r'''
#include <stdint.h>
#include <stddef.h>
#include <assert.h>
typedef int LONG;
typedef uintptr_t ULONG_PTR;
typedef struct { unsigned ExceptionCode, NumberParameters; ULONG_PTR ExceptionInformation[4]; void *ExceptionAddress; } EXCEPTION_RECORD;
typedef struct { unsigned Type; void *AllocationBase; } MEMORY_BASIC_INFORMATION;
static uint64_t alias, image;
static int query_ok, query_image, calls;
#define MEM_IMAGE 0x1000000
#define MemoryBasicInformation 0
#define NtCurrentProcess() ((void*)-1)
static int NtQueryVirtualMemory(void *p, void *address, int kind, MEMORY_BASIC_INFORMATION *m, size_t n, void *out) {
 ++calls; m->Type=query_image?MEM_IMAGE:0; m->AllocationBase=(void*)0x400000; return query_ok?0:1;
}
uint64_t ios_jit_reverse_translate(uint64_t a, uint64_t *base) { *base=image; return alias; }
static LONG InterlockedIncrement(LONG *p) { return ++*p; }
static void quiet(const char *fmt, ...) {}
#define ERR_(channel) quiet
static void repair(EXCEPTION_RECORD *rec) {
''' + body + r'''
}
int main() {
 repair(NULL);
 EXCEPTION_RECORD r={0xE06D7363,4,{0,0,0x100100,0},0};
 alias=0x400100; image=0x400000; repair(&r);
 assert(r.ExceptionInformation[2]==0x400100 && r.ExceptionInformation[3]==0x400000 && !calls);
 alias=image=0; query_ok=query_image=1;
 r.ExceptionInformation[2]=0x400100; r.ExceptionInformation[3]=0; repair(&r);
 assert(r.ExceptionInformation[3]==0x400000 && calls==1);
 r.ExceptionInformation[3]=0x900000; repair(&r); assert(calls==1 && r.ExceptionInformation[3]==0x900000);
 r.ExceptionInformation[3]=0; query_image=0; repair(&r); assert(!r.ExceptionInformation[3]);
 query_ok=0; query_image=1; repair(&r); assert(!r.ExceptionInformation[3]);
 calls=0; r.NumberParameters=3; repair(&r); assert(!calls);
 r.NumberParameters=4; r.ExceptionCode=0xC0000005; repair(&r); assert(!calls);
 r.ExceptionCode=0xE06D7363; r.ExceptionInformation[2]=0; repair(&r); assert(!calls);
 return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='madeira-cxx-throw-') as directory:
    exe = Path(directory) / 'test'
    subprocess.run(['xcrun','clang','-x','c','-fsanitize=address,undefined','-o',str(exe),'-'],
                   input=harness,text=True,check=True)
    subprocess.run([str(exe)],check=True)
print('PASS: actual throw-repair block; aliases, zero base, preserved base, failed/non-image query and non-C++/32-bit exceptions')
