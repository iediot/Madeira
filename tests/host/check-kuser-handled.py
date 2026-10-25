#!/usr/bin/env python3
"""Execute the actual KUSER redirect block; emulated accesses preserve registers."""
from pathlib import Path
import subprocess
import tempfile

root=Path(__file__).resolve().parents[2]
source=(root/'build/ntdll-unix/signal_arm64_ios.c').read_text()
start=source.index('if (!handled && !srcwatch_hit && fault_addr >= 0x7FFE0000')
block=source[start:source.index('/* 2. Redirect execution faults',start)]
code=r'''
#include <stdint.h>
#include <assert.h>
#include <stdio.h>
static uint64_t ios_exc_usd=0x123400000ull,ios_exc_usd_fixes;
static void run(int handled,int srcwatch_hit,uintptr_t fault_addr,int should_redirect) {
    struct { uint64_t __x[29]; } state={{0}};
    state.__x[0]=0x7ffe0283;state.__x[1]=42;
''' + block + r'''
    assert(state.__x[1]==42);
    assert(state.__x[0] == (should_redirect ? ios_exc_usd+0x283 : 0x7ffe0283));
}
int main(void) {
    run(1,0,0x7ffe0010,0); /* emulated access, rcx-like data must not be rebased */
    run(0,1,0x7ffe0010,0); /* source-watch service owns the exception */
    run(0,0,0x7ffe0010,1); /* unhandled KUSER mapping still redirects */
    run(0,0,0x7fff0000,0);run(0,0,0x7ffdffff,0);
    assert(ios_exc_usd_fixes==1);
    puts("PASS: handled/source-watch accesses preserve KUSER constants; real unhandled access still redirects");
}
'''
with tempfile.TemporaryDirectory(prefix='madeira-kuser-test-') as directory:
    exe=Path(directory)/'test'
    subprocess.run(['xcrun','clang','-x','c','-fsanitize=address,undefined','-o',str(exe),'-'],
                   input=code,text=True,check=True)
    subprocess.run([str(exe)],check=True)
