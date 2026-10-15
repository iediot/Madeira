#!/usr/bin/env python3
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
src = (root / 'build/ntdll-unix/thread_ios.c').read_text()
start = src.index('NTSTATUS WINAPI NtSuspendThread(')
body = src[start:src.index('/******************************************************************************', start + 1)]
assert body.index('NtQueryInformationThread(') < body.index('SERVER_START_REQ')
harness = r'''
#include <assert.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#define WINE_IOS 1
#define WINAPI
typedef unsigned NTSTATUS, ULONG; typedef void *HANDLE; typedef int BOOL;
struct { struct { HANDLE UniqueThread; } ClientId; } teb = {{(HANDLE)123}};
#define NtCurrentTeb() (&teb)
typedef struct { struct { HANDLE UniqueThread; } ClientId; } THREAD_BASIC_INFORMATION;
#define ThreadBasicInformation 0
#define SELECT_INTERRUPTIBLE 1
#define ERR(...) ((void)0)
#define GetCurrentThreadId() 123
#define GetCurrentThread() ((HANDLE)(intptr_t)-2)
#define wine_server_obj_handle(h) (h)
static unsigned result, selections, queries;
static int NtQueryInformationThread(HANDLE h, int k, THREAD_BASIC_INFORMATION *i, size_t s, void *r) {
    (void)k; (void)s; (void)r; ++queries;
    i->ClientId.UniqueThread = h == (HANDLE)10 ? (HANDLE)123 : (HANDLE)456;
    return h == (HANDLE)30;
}
struct request { HANDLE handle; }; struct reply { unsigned count; };
#define SERVER_START_REQ(name) do { struct request request; struct reply answer={2}; struct request *req=&request; struct reply *reply=&answer;
#define SERVER_END_REQ } while(0)
static unsigned wine_server_call(struct request *r) { (void)r; return result; }
static void server_select(void *a, int b, int c, int d, void *e, void *f) {
    assert(!a && !b && c == SELECT_INTERRUPTIBLE && !d && !e && !f); ++selections;
}
''' + body + r'''
int main(void) {
    ULONG count = 99;
    assert(!NtSuspendThread(GetCurrentThread(), &count) && count == 2 && selections == 1 && !queries);
    assert(!NtSuspendThread((HANDLE)10, NULL) && selections == 2 && queries == 1);
    assert(!NtSuspendThread((HANDLE)20, NULL) && selections == 2);
    assert(!NtSuspendThread((HANDLE)30, NULL) && selections == 2);
    result = 0xc0000008; count = 99;
    assert(NtSuspendThread(GetCurrentThread(), &count) == result && selections == 2 && count == 99);
    puts("PASS: pseudo/real self handles park, other/invalid targets do not, failed suspend preserves count");
}
'''
with tempfile.TemporaryDirectory(prefix='madeira-self-suspend-') as directory:
    path = Path(directory); c = path / 'suspend.c'; c.write_text(harness)
    exe = path / 'suspend'
    subprocess.run(['xcrun', 'clang', '-std=c11', '-fsanitize=address,undefined', str(c), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
