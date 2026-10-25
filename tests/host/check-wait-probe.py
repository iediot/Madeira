#!/usr/bin/env python3
"""Wait-target probe: run the production wait registry and load/store decoder.

- wait_registry_ios.h under ASan/UBSan/TSan-free threads: publish/unpublish,
  nested (kernel APC) waits restoring the outer wait, slot collisions, a dead
  owner's leftover entry, torn-read-free snapshots against a busy writer,
  wake records matched by cookie and time only, never by slot alone.
- arm64_mem_ea.h against instructions assembled by the host compiler (this
  check needs an arm64 host) with known register values.
- Source wiring: every server_select path publishes and unpublishes once,
  the server notes the waker before writing the reply, the server loop polls
  the probe, the snapshot adds no thread suspension.
"""
from pathlib import Path
import platform
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
ntdll = root / 'build/ntdll-unix'
server = (ntdll / 'server_ios.c').read_text()
perf = (ntdll / 'perf_ios.c').read_text()
queue = (root / 'build/wineserver/queue_ios.c').read_text()
fd = (root / 'build/wineserver/fd_ios.c').read_text()
thread = (root / 'wine/server/thread.c').read_text()
vm = (ntdll / 'virtual_ios.c').read_text()

registry_test = r'''
#include <assert.h>
#include <pthread.h>
#include <stdio.h>
#include "wait_registry_ios.h"
static struct ios_wait_entry table[IOS_WAITREG_SLOTS];

static struct ios_wait_info info(unsigned tid, uint64_t native, uint64_t t0, int op) {
    struct ios_wait_info in; memset(&in, 0, sizeof(in));
    in.wine_tid = tid; in.wine_pid = 0x40c; in.native_tid = native; in.t0_ns = t0; in.op = op;
    in.count = 9;   /* clamped */
    for (int i = 0; i < IOS_WAITREG_HANDLES; i++) in.handles[i] = 0x100 + i;
    in.cookie = 0x7000 + t0; in.key = 0x55;
    return in;
}

static volatile int stop;
static void *writer(void *p) {
    (void)p;
    for (unsigned n = 1; !stop; n++) {
        struct ios_wait_token tok;
        struct ios_wait_info in = info(0x48c, 77, n, 1);
        for (int i = 0; i < IOS_WAITREG_HANDLES; i++) in.handles[i] = n;   /* all equal per publish */
        in.key = n;
        assert(ios_wait_publish(table, &in, &tok));
        for (volatile int spin = 0; spin < 200; spin++) ;   /* stay visible for a moment */
        ios_wait_unpublish(&tok);
    }
    return NULL;
}

int main(void) {
    struct ios_wait_token outer, inner, other;
    struct ios_wait_info in, out;
    unsigned depth;
    unsigned waker, wpid; int status; uint64_t wake_ns;

    assert(ios_wait_slot_index(0x48c) == (0x48c >> 2) % IOS_WAITREG_SLOTS);
    in = info(0x48c, 77, 1000, 1);
    assert(ios_wait_publish(table, &in, &outer));
    struct ios_wait_entry *e = &table[ios_wait_slot_index(0x48c)];
    assert(ios_wait_read(e, &out, &depth) && depth == 1 && out.count == IOS_WAITREG_HANDLES);
    assert(out.wine_tid == 0x48c && out.t0_ns == 1000 && out.handles[7] == 0x107 && out.key == 0x55);
    assert(!(e->gen & 1));

    /* nested wait inside a kernel APC: visible while it runs, outer comes back */
    in = info(0x48c, 77, 2000, 6);
    assert(ios_wait_publish(table, &in, &inner) && inner.nested);
    assert(ios_wait_read(e, &out, &depth) && depth == 2 && out.op == 6 && out.t0_ns == 2000);
    ios_wait_unpublish(&inner);
    assert(ios_wait_read(e, &out, &depth) && depth == 1 && out.op == 1 && out.t0_ns == 1000);
    assert(e->phase == IOS_WAIT_PHASE_APC);

    /* a colliding thread cannot steal a slot whose owner is waiting */
    unsigned clash = 0x48c + 4 * IOS_WAITREG_SLOTS;
    assert(ios_wait_slot_index(clash) == ios_wait_slot_index(0x48c));
    in = info(clash, 88, 3000, 1);
    assert(!ios_wait_publish(table, &in, &other) && !other.slot);
    ios_wait_unpublish(&other);   /* untracked: no-op */
    assert(ios_wait_read(e, &out, &depth) && out.wine_tid == 0x48c);

    /* wake records: only this wait's cookie, never older than its entry */
    ios_wait_note_wake(table, 0x48c, 0x7000 + 1000, 0, 0x5a0, 0x40c, 999);
    assert(!ios_wait_take_wake(e, 0x7000 + 1000, 1000, &waker, &wpid, &status, &wake_ns)); /* before t0 */
    ios_wait_note_wake(table, 0x48c, 0x7000 + 1000, 258, 0x5a0, 0x40c, 1500);
    assert(ios_wait_take_wake(e, 0x7000 + 1000, 1000, &waker, &wpid, &status, &wake_ns));
    assert(waker == 0x5a0 && wpid == 0x40c && status == 258 && wake_ns == 1500);
    assert(!ios_wait_take_wake(e, 0x1234, 1000, &waker, &wpid, &status, &wake_ns));       /* other cookie */
    ios_wait_note_wake(table, clash, 0x7000 + 1000, 1, 0x1, 0x1, 1600);                     /* not the owner */
    assert(ios_wait_take_wake(e, 0x7000 + 1000, 1000, &waker, &wpid, &status, &wake_ns) && waker == 0x5a0);
    ios_wait_note_wake(table, 0x48c, 0, 1, 0x1, 0x1, 1700);                                 /* no cookie */
    assert(ios_wait_take_wake(e, 0x7000 + 1000, 1000, &waker, &wpid, &status, &wake_ns) && waker == 0x5a0);

    ios_wait_unpublish(&outer);
    assert(!ios_wait_read(e, &out, &depth));

    /* idle owner: the colliding thread takes the slot over, and a fresh wait clears old wake data */
    assert(ios_wait_publish(table, &in, &other) && other.slot == e && !other.nested);
    assert(!ios_wait_take_wake(e, 0x7000 + 1000, 0, &waker, &wpid, &status, &wake_ns));
    ios_wait_unpublish(&other);

    /* a thread killed inside a wait never unpublished; a new thread reusing its id starts clean */
    in = info(0x600, 90, 4000, 1);
    assert(ios_wait_publish(table, &in, &outer));
    in = info(0x600, 91, 5000, 2);
    assert(ios_wait_publish(table, &in, &inner) && !inner.nested);
    ios_wait_unpublish(&inner);
    assert(!ios_wait_read(&table[ios_wait_slot_index(0x600)], &out, &depth));

    /* readers never see a mixed entry while the owner republishes */
    memset(table, 0, sizeof(table));
    pthread_t t; assert(!pthread_create(&t, NULL, writer, NULL));
    unsigned seen = 0;
    for (int i = 0; i < 2000000 && seen < 20000; i++) {
        if (!ios_wait_read(&table[ios_wait_slot_index(0x48c)], &out, &depth)) continue;
        for (int h = 1; h < IOS_WAITREG_HANDLES; h++) assert(out.handles[h] == out.handles[0]);
        assert(out.key == out.handles[0] && out.t0_ns == out.key && depth == 1);
        seen++;
    }
    stop = 1; pthread_join(t, NULL);
    assert(seen > 100);
    printf("PASS: wait registry publish/nest/collision/stale-owner/wake matching; %u consistent concurrent reads\n", seen);
    return 0;
}
'''

decoder_test = r'''
#include <assert.h>
#include <stdio.h>
#include "arm64_mem_ea.h"
extern const uint32_t insns[];
__asm__(".text\n.p2align 2\n.globl _insns\n_insns:\n"
    "ldr x0, [x1, #16]\n"           /* 0 unsigned offset, 8 bytes  */
    "ldrb w0, [x2, #3]\n"           /* 1 byte                       */
    "ldur x0, [x1, #-8]\n"          /* 2 unscaled                   */
    "ldr x0, [x1], #32\n"           /* 3 post-index: old base       */
    "ldr x0, [x1, #-16]!\n"         /* 4 pre-index                  */
    "ldr x0, [x1, x2, lsl #3]\n"    /* 5 register offset, scaled    */
    "ldr w0, [x1, w3, sxtw]\n"      /* 6 sign-extended index        */
    "ldp x0, x1, [sp, #-32]\n"      /* 7 pair, sp base              */
    "ldp q0, q1, [x4, #64]\n"       /* 8 128-bit pair               */
    "ldxr x0, [x5]\n"               /* 9 exclusive                  */
    "ldadd x0, x1, [x6]\n"          /* 10 LSE atomic                */
    "ld1 {v0.16b}, [x7]\n"          /* 11 SIMD structure            */
    "ldr q0, [x1, #32]\n"           /* 12 128-bit unsigned offset   */
    "add x0, x1, x2\n"              /* 13 not memory                */
    "str x0, [x1, #8]\n"            /* 14 store                     */
    "ldr x0, 1f\n"                  /* 15 literal +8                */
    "nop\n"
    "1: .quad 0\n");
int main(void) {
    uint64_t r[31] = {0}, ea; unsigned bytes;
    r[1] = 0x10000; r[2] = 0x20; r[3] = 0xfffffffe; r[4] = 0x40000; r[5] = 0x50000; r[6] = 0x60000; r[7] = 0x70000;
    uint64_t sp = 0x90000, pc = 0x1000;
    struct { int i; uint64_t ea; unsigned bytes; } want[] = {
        {0, 0x10010, 8}, {1, 0x23, 1}, {2, 0xfff8, 8}, {3, 0x10000, 8}, {4, 0xfff0, 8},
        {5, 0x10100, 8}, {6, 0xfffe, 4}, {7, 0x8ffe0, 16}, {8, 0x40040, 32}, {9, 0x50000, 8},
        {10, 0x60000, 8}, {11, 0x70000, 16}, {12, 0x10020, 16}, {14, 0x10008, 8}, {15, 0x1000 + 15 * 4 + 8, 8},
    };
    for (unsigned k = 0; k < sizeof(want) / sizeof(want[0]); k++) {
        int ok = ios_arm64_mem_ea(insns[want[k].i], pc + want[k].i * 4, r, sp, &ea, &bytes);
        if (!ok || ea != want[k].ea || bytes != want[k].bytes)
            { printf("FAIL insn %d (%08x): ok=%d ea=%llx bytes=%u\n", want[k].i, insns[want[k].i], ok, (unsigned long long)ea, bytes); return 1; }
    }
    assert(!ios_arm64_mem_ea(insns[13], pc, r, sp, &ea, &bytes));
    printf("PASS: load/store effective addresses for %zu assembled instruction forms\n", sizeof(want) / sizeof(want[0]));
    return 0;
}
'''

# ---- source wiring ----
tracked = server[server.index('static unsigned int server_select_tracked( const union select_op *select_op, data_size_t size, UINT flags,\n                                           timeout_t abs_timeout, struct context_data *context,\n                                           struct user_apc *user_apc, void *caller )\n{'):]
tracked = tracked[:tracked.index('\n}\n') + 3]
assert tracked.count('ios_wait_enter(') == 1 and tracked.count('ios_wait_leave(') == 1
assert tracked.index('ios_wait_enter(') < tracked.index('do\n') < tracked.index('ios_wait_leave(')
assert tracked.index('ios_wait_leave(') < tracked.index('return ret;')
assert tracked.count('return') == 1, 'every exit must pass ios_wait_leave'
assert tracked.index('IOS_WAIT_PHASE_PIPE') < tracked.index('wait_select_reply( &cookie )')
public = server[server.index('unsigned int server_select( const union select_op'):]
public = public[:public.index('\n}\n')]
assert 'server_select_tracked(' in public
wait = server[server.index('unsigned int server_wait( const union select_op'):]
wait = wait[:wait.index('\n}\n')]
assert 'server_select_tracked(' in wait and 'ios_wait_enter' not in wait and 'server_select(' not in wait
send = thread[thread.index('static int send_thread_wakeup('):]
send = send[:send.index('\n}\n')]
assert send.index('ios_wait_wakeup_hook(') < send.index('write( get_unix_fd( thread->wait_fd )')
assert 'ios_wait_probe_service();' in fd
service = queue[queue.index('void ios_wait_probe_service( void )'):]
service = service[:service.index('\n}\n')]
for forbidden in ('wake_thread', 'end_wait', 'set_event', 'release_object( f.found', 'kill_thread'):
    assert forbidden not in service, forbidden
snapshot = perf[perf.index('void ios_perf_stall_snapshot('):]
snapshot = snapshot[:snapshot.index('\n}\n')]
assert 'thread_suspend(' not in snapshot and 'thread_resume(' not in snapshot
assert 'if (nids > 16) nids = 16;' in snapshot and 'ios_perf_wait_report(ids, nids);' in snapshot
assert snapshot.count('mach_port_deallocate') == 2 and 'if (!keep) mach_port_deallocate' in snapshot
punch = vm[vm.index('static int ios_swap_punch_timed('):]
punch = punch[:punch.index('\n}\n')]
assert punch.index('ios_swap_busy_set(') < punch.index('fcntl( ios_swap_fd, F_PUNCHHOLE') < punch.index('ios_swap_busy_clear(')

with tempfile.TemporaryDirectory(prefix='madeira-wait-probe-') as directory:
    exe = Path(directory) / 'registry'
    subprocess.run(['xcrun', 'clang', '-x', 'c', '-std=c11', '-O1', '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-pthread',
                    '-I', str(ntdll), '-o', str(exe), '-'], input=registry_test, text=True, check=True)
    subprocess.run([str(exe)], check=True)
    if platform.machine() == 'arm64':
        exe = Path(directory) / 'decoder'
        subprocess.run(['xcrun', 'clang', '-x', 'c', '-std=c11', '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-march=armv8.1-a',
                        '-I', str(ntdll), '-o', str(exe), '-'], input=decoder_test, text=True, check=True)
        subprocess.run([str(exe)], check=True)
    else:
        print('SKIP: decoder test needs an arm64 host')
print('PASS: server_select publishes/unpublishes once on every path; waker noted before the reply; probe is read-only; snapshot adds no suspension')
