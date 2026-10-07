#!/usr/bin/env python3
"""Exercise the production hot-stack sampler with failed Mach calls and bad frames."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
src = (root / 'build/ntdll-unix/perf_ios.c').read_text()
prod = src[src.index('static int perf_read('):src.index('static int perf_busy_cmp(')]
harness = r'''
#include <stdint.h>
#include <stddef.h>
#include <assert.h>
#include <string.h>
typedef unsigned thread_t;
typedef unsigned mach_msg_type_number_t;
typedef unsigned long vm_size_t;
typedef unsigned long vm_address_t;
typedef int kern_return_t;
typedef void *thread_state_t;
typedef struct { uint64_t __x[29], pc, lr, fp; } arm_thread_state64_t;
typedef struct { const char *dli_fname, *dli_sname; void *dli_saddr, *dli_fbase; } Dl_info;
#define ARM_THREAD_STATE64_COUNT 68
#define ARM_THREAD_STATE64 6
#define arm_thread_state64_get_pc(s) ((s).pc)
#define arm_thread_state64_get_lr(s) ((s).lr)
#define arm_thread_state64_get_fp(s) ((s).fp)
static int held, suspend_fail, state_fail, resumes, prints, reads;
static unsigned mach_task_self(void) { return 1; }
static int thread_suspend(thread_t t) { if (suspend_fail) return 1; assert(!held); held=1; return 0; }
static int thread_resume(thread_t t) { assert(held); held=0; resumes++; return 0; }
static int thread_get_state(thread_t t, int f, void *out, unsigned *n) {
    assert(held);
    arm_thread_state64_t *s=out; memset(s,0,sizeof(*s));
    s->pc=0x100000; s->lr=0x100004; s->fp=0x200000; s->__x[28]=0x300000;
    return state_fail;
}
static int vm_read_overwrite(unsigned t, vm_address_t a, size_t n, vm_address_t o, vm_size_t *got) {
    assert(held); reads++; memset((void *)o,0,n); *got=n;
    if(a==0x200000) { uint64_t *p=(void *)o; p[0]=a; p[1]=0x100008; } /* cyclic frame */
    else if(a==0x400048) *(unsigned *)o=0xa8;
    else return 1; /* unreadable FEX block */
    return 0;
}
static int dladdr(void *pc, Dl_info *di) { assert(!held); return 0; }
static int dprintf(int fd, const char *fmt, ...) { assert(!held); prints++; return 0; }
int ios_thread_registry_count(void) { return 1; }
uintptr_t ios_thread_registry_teb(int i) { return 0x400000; }
thread_t ios_thread_registry_mach(int i) { return 42; }
uint64_t ios_native_rip_from_hostpc(uint64_t b, uint64_t pc, const char **why) { assert(!held); return 0; }
'''
harness += prod + r'''
int main(void) {
    suspend_fail=1; perf_hot_stack(42,99,"Main Thread"); assert(!held && !resumes && !prints);
    suspend_fail=0; state_fail=1; perf_hot_stack(42,99,"Main Thread"); assert(!held && resumes==1 && !prints);
    state_fail=0; perf_hot_stack(42,99,"Main Thread");
    assert(!held && resumes==2 && prints==4 && reads==3);
    return 0;
}
'''
with tempfile.TemporaryDirectory() as d:
    c = Path(d)/'sampler.c'; exe=Path(d)/'sampler'; c.write_text(harness)
    subprocess.run(['cc','-std=c11',str(c),'-o',str(exe)],check=True)
    subprocess.run([str(exe)],check=True)
print('PASS: suspend failure, state failure, unreadable JIT state and cyclic frame; every halt balanced; output only after resume')
