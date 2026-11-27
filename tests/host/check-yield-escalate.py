#!/usr/bin/env python3
"""Yield-spin escalation and the per-thread run-state / yield counts of [xp-t]; no Wine runs.

RDR2 on the iPhone 18 Pro under the energy clamp (log 2026-11-27 01:05, wave1): ~25 threads
runnable on 6 cores ([xp] run=50 s per 2 s window), 5,000-11,000 yields a second, mostly
Sleep(0), rising with the clock, and six job workers at ~4 instructions per cycle.

Compiles the production code from wine/dlls/ntdll/unix/sync.c and checks:
  - ios_yesc_step: the first `after` yields of a streak stay ordinary yields, then a yield
    sleeps start_us, doubling to cap_us and never above it (also under a random fuzz);
    more than work_ns of the thread's own CPU between two yields resets the streak, as does
    a clock that goes backwards; ios_yesc_reset (a real wait) starts over;
  - ios_yesc_image_listed: the image's last path component against a ';' / ',' list,
    case-insensitive, whole names only;
  - ios_ystat_get + server_ios.c ios_xp_ystat: the same hash and table, so the sampler finds
    every slot a thread claimed (also from several threads at once);
  - server_ios.c ios_xp_tok_extra: the [xp-t] token suffix;
  - NtYieldExecution's escalation hunk with ios_poll_yield: the yields ntdll makes after a
    failed poll (server_wait's STATUS_TIMEOUT yield, the fastsync peek) never sleep and
    never count toward the streak, however many there are and however they interleave with
    SwitchToThread / Sleep(0), which still escalate after `after` of their own; a timed
    sleep's yield never escalates; ios_poll_yield restores the caller's mark;
and textually: every real-wait entry point resets the streak (eight of them), NtDelayExecution
marks its own yield (timed sleep = not a spin, Sleep(0) = a spin), the poll paths
(server_ios.c / server.c server_wait, the fastsync peek) yield through ios_poll_yield and
nothing else in unix ntdll calls NtYieldExecution, NtYieldExecution escalates
before the ml1063 adaptive yield, the switch is env.MADEIRA_YIELD_ESCALATE (off unless set),
only the RDR2 profile sets it, the [xp] loop samples run state per slice with
MADEIRA_XP_RUNQ=0 as the off switch, the worker [spin-pc] gate is capped, and [xp-api]
reports escalations.
Needs python3 and a C compiler (AddressSanitizer/UBSan).
"""
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
sync = (root / 'wine/dlls/ntdll/unix/sync.c').read_text()
server = (root / 'build/ntdll-unix/server_ios.c').read_text()
library = (root / 'app/Madeira/Library.swift').read_text()


def function(source, signature):
    start = source.index(signature)
    return source[start:source.index('\n}', start) + 2] + '\n'


# --- textual -----------------------------------------------------------------------
waits = ('NTSTATUS WINAPI NtWaitForMultipleObjects(', 'NTSTATUS WINAPI NtWaitForSingleObject(',
         'NTSTATUS WINAPI NtSignalAndWaitForSingleObject(', 'NTSTATUS WINAPI NtDelayExecution(',
         'NTSTATUS WINAPI NtWaitForKeyedEvent(', 'NTSTATUS WINAPI NtRemoveIoCompletion(',
         'NTSTATUS WINAPI NtRemoveIoCompletionEx(', 'NTSTATUS WINAPI NtWaitForAlertByThreadId(')
for fn in waits:
    body = function(sync, fn)
    assert body.count('IOS_YESC_WAIT( timeout );') == 1, fn
    at = body.index('IOS_YESC_WAIT( timeout );')
    for later in ('server_wait(', 'inproc_wait(', 'madeira_fast_wait(', 'select( 0', 'SERVER_START_REQ'):
        if later in body:
            assert at < body.index(later), (fn, later)
    if 'ios_wait_floor(' in body:
        assert body.index('ios_wait_floor(') < at, fn   # the floored timeout decides
assert sync.count('IOS_YESC_WAIT( timeout );') == len(waits)
assert '#define IOS_YESC_WAIT(timeout) do { if (ios_yesc_on > 0 && (!(timeout) || (timeout)->QuadPart)) ios_yesc_reset( &ios_yesc ); } while (0)' in sync
print('PASS: all eight real-wait entry points reset the streak (a poll does not), after the wait floor and before waiting')

delay = function(sync, 'NTSTATUS WINAPI NtDelayExecution(')
mark = delay.index('ios_yesc_in_timed_delay = when ? 1 : 2;')
call = delay.index('status = NtYieldExecution();')
clear = delay.index('ios_yesc_in_timed_delay = 0;')
assert mark < call < clear < delay.index('if (!when) return status;')
assert 'timeout->QuadPart ? &ys->shortsleep : &ys->zero' in delay
yld = function(sync, 'NTSTATUS WINAPI NtYieldExecution(')
assert yld.index('ios_yesc_step(') < yld.index('ml1063: ADAPTIVE YIELD')
assert 'if (ys && (!ios_yesc_in_timed_delay || ios_yesc_in_timed_delay == 3)) __atomic_add_fetch( &ys->yields' in yld
assert 'if ((!ios_yesc_in_timed_delay || ios_yesc_in_timed_delay == 2) && ios_yesc_thread_on())' in yld
assert 'clock_gettime_nsec_np( CLOCK_THREAD_CPUTIME_ID )' in yld and 'usleep( us );' in yld
init = function(sync, 'static void ios_yesc_init(void)')
assert 'getenv( "MADEIRA_YIELD_ESCALATE" )' in init and "ios_yesc_mode = 0; return;" in init
assert 'cap > 500 ? 500' in init
get = function(sync, 'static struct ios_ystat *ios_ystat_get(void)')
assert 'getenv( "MADEIRA_YIELD_STATS" )' in get and 'if (!ios_ystat_on) { ios_ystat_none = 1; return NULL; }' in get
for key, default in (('yield-esc-after', '32'), ('yield-esc-us', '20'), ('yield-esc-cap-us', '100'),
                     ('yield-esc-work-us', '250')):
    assert 'madeira_cfg_int( "%s", %s )' % (key, default) in init, key
print('PASS: Sleep(0) escalates, a timed sleep\'s own yield does not; escalation precedes ml1063; env switch, cfg defaults')

# the yields after a failed poll go through ios_poll_yield (no escalation, no streak)
server_c = (root / 'wine/dlls/ntdll/unix/server.c').read_text()
private_h = (root / 'wine/dlls/ntdll/unix/unix_private.h').read_text()
poll_yield = function(sync, 'void ios_poll_yield(void)')
assert 'ios_yesc_in_timed_delay = 3;' in poll_yield and 'ios_yesc_in_timed_delay = prev;' in poll_yield
assert 'extern void ios_poll_yield(void);' in private_h
for name, src in (('server_ios.c', server), ('server.c', server_c)):
    wait = function(src, 'unsigned int server_wait( const union select_op *select_op')
    assert 'if (ret == STATUS_TIMEOUT) ios_poll_yield();' in wait, name
budget = function(sync, 'static inline int madeira_peek_budget(void)')
assert 'ios_poll_yield();' in budget and 'NtYieldExecution' not in budget
yield_calls = re.findall(r'\bNtYieldExecution\(\);', sync)
assert len(yield_calls) == 2, 'unix sync.c: NtYieldExecution called outside NtDelayExecution / ios_poll_yield'
assert 'status = NtYieldExecution();' in delay and 'NtYieldExecution();' in poll_yield
assert not re.search(r'\bNtYieldExecution\(\);', server), 'server_ios.c: a yield outside ios_poll_yield'
print('PASS: server_wait (server_ios.c, server.c) and the fastsync peek yield through ios_poll_yield; no other internal yields')

rdr2 = library[library.index('case "rdr2.exe", "playrdr2.exe":'):]
rdr2 = rdr2[:rdr2.index('case "gta5_enhanced.exe"')]
assert 'env.MADEIRA_YIELD_ESCALATE = RDR2.exe\n' in rdr2
code_lines = [l for l in library.splitlines() if 'MADEIRA_YIELD_ESCALATE' in l and not l.strip().startswith('//')]
assert code_lines == ['            env.MADEIRA_YIELD_ESCALATE = RDR2.exe'], code_lines   # only the RDR2 profile turns it on
print('PASS: only the RDR2 profile sets env.MADEIRA_YIELD_ESCALATE (RDR2.exe only)')

xp = function(server, 'static void ios_xprobe_main( void )')
assert 'getenv( "MADEIRA_XP_RUNQ" )' in xp and "runq = !(e && e[0] == '0')" in xp
assert xp.index('if (!runq) usleep( xp_us );') < xp.index('have_th = task_threads(') < xp.index('usleep( xp_us / rq_slices );') < xp.index('/* per-thread counters */')
assert 'while (shown < (rq_slices ? 24 : 14)' in xp, 'RUNQ=0 keeps 14 threads'
assert 'getenv( "MADEIRA_SPIN_PC_WORKERS" )' in xp and 'if (workers_on && worker_samples < 40' in xp
assert 'bi.run_state == TH_STATE_RUNNING' in xp and 'if (th[k] == self) continue;' in xp
assert 'r->rq_ms = rq_slices ? dt_ms * rq_cnt[k] / rq_slices : -1;' in xp
assert 'worker_samples < 40 && worker_gen != gen' in xp and 'pt + et >= 0.12 * dt_ms' in xp
assert xp.count('task_threads(') == 1, 'one thread list per window'
api = function(server, 'static void ios_xp_api_report(')
assert 'u[18] = ios_xp_yield_esc; u[19] = ios_xp_yield_esc_us;' in api and 'escalated %.0f/s %.1f ms/s' in api
assert 'if (have_u && u[18])' in api and '(slept %.0f/s%s)' in api, '[xp-api] unchanged until a yield escalates'
print('PASS: [xp] samples run state per slice (MADEIRA_XP_RUNQ=0 off), [xp-t] gets r=, worker [spin-pc] capped, [xp-api] escalations')

# --- compiled ------------------------------------------------------------------------
block = sync[sync.index('struct ios_ystat { volatile uint64_t tid;'):sync.index('static int ios_yesc_mode = -1;')]
srv_ystat = function(server, 'static int ios_xp_ystat( uint64_t tid, long long v[5] )')
srv_row = server[server.index('struct ios_xp_row {'):server.index('/* madeira-bcd yield-esc: the per-thread yield counts')]
tok = function(server, 'static int ios_xp_tok_extra(')
yld_hunk = yld[yld.index('    {   /* madeira-bcd yield-esc: per-thread counts'):yld.index('    /* iOS-Madeira ml1063: ADAPTIVE YIELD.')]
assert 'struct ios_ystat { volatile uint64_t tid; volatile long long yields, zero, shortsleep, esc, esc_us; };' in server
assert 'extern struct ios_ystat ios_ystat_tab[1024];' in server and '#define IOS_YSTAT_N 1024' in block

harness = r'''
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <pthread.h>
typedef uint16_t WCHAR;
#define FAIL(...) do { fprintf(stderr, __VA_ARGS__); exit(1); } while (0)
static __thread uint64_t fake_tid;
static int pthread_threadid_np_fake( pthread_t t, uint64_t *id ) { (void)t; *id = fake_tid; return 0; }
#define pthread_threadid_np pthread_threadid_np_fake
''' + block + r'''
#undef pthread_threadid_np
struct ios_xp_tcd { uint64_t instr, cyc, ut, st, nj; };
''' + srv_row.replace('struct ios_xp_row', 'struct ios_xp_row', 1) + srv_ystat + tok + r'''
/* NtYieldExecution's yield-esc hunk as built, then an ordinary yield (returns 1) */
typedef int NTSTATUS;
#define STATUS_SUCCESS 0
static volatile long long ios_xp_yield;
static unsigned long long fake_cpu_ns;
static unsigned int slept_us, escalated;
static __thread struct ios_yesc_state ios_yesc;
static __thread int ios_yesc_in_timed_delay;
static struct ios_yesc_cfg ios_yesc_conf = { 32, 20, 100, 250000 };
static int ios_yesc_thread_on(void) { return 1; }
#define clock_gettime_nsec_np(clk) (fake_cpu_ns)
#define usleep(us) (slept_us = (us), escalated++)
static NTSTATUS NtYieldExecution(void)
{
    slept_us = 0;
''' + yld_hunk + r'''
    return 1;
}
#undef usleep
''' + poll_yield + r'''
/* one yield of each kind, 3 us (a poll: 1 us) of the thread's CPU after the previous one;
 * returns the sleep, 0 = none. (More than work_ns of CPU between two counted yields is
 * work and resets the streak by design, polls included, so the polls here stay below it.) */
static unsigned int guest_yield(void) { fake_cpu_ns += 3000; NtYieldExecution(); return slept_us; }
static unsigned int poll_yield_now(void) { fake_cpu_ns += 1000; slept_us = 0; ios_poll_yield(); return slept_us; }
static unsigned int sleep0(void)
{
    unsigned int us;
    fake_cpu_ns += 3000; ios_yesc_in_timed_delay = 2; NtYieldExecution(); us = slept_us; ios_yesc_in_timed_delay = 0;
    return us;
}
static unsigned int timed(void)
{
    unsigned int us;
    fake_cpu_ns += 3000; ios_yesc_in_timed_delay = 1; NtYieldExecution(); us = slept_us; ios_yesc_in_timed_delay = 0;
    return us;
}

static void *claim( void *arg )
{
    uintptr_t base = (uintptr_t)arg; int i;
    for (i = 0; i < 64; i++)
    {
        struct ios_ystat *s;
        fake_tid = base + i * 7919;
        ios_ystat_self = NULL; ios_ystat_none = 0;
        s = ios_ystat_get();
        if (!s) FAIL("no slot\n");
        __atomic_add_fetch( &s->yields, (long long)(base + i), __ATOMIC_RELAXED );
        if (ios_ystat_get() != s) FAIL("slot not cached\n");
    }
    return NULL;
}
static WCHAR wbuf[256];
static int listed( const char *path, const char *list )
{
    int n = 0;
    for (; path[n]; n++) wbuf[n] = (unsigned char)path[n];
    return ios_yesc_image_listed( wbuf, n, list );
}
int main( void )
{
    struct ios_yesc_cfg c = { 4, 20, 200, 1000000 };
    struct ios_yesc_state s; unsigned i, us, last = 0; unsigned long long cpu = 5000;
    unsigned expect[] = { 0, 0, 0, 0, 20, 40, 80, 160, 200, 200, 200 };
    memset( &s, 0, sizeof(s) );
    for (i = 0; i < sizeof(expect) / sizeof(expect[0]); i++)
    {
        cpu += 3000;   /* a 3 us spin between yields */
        us = ios_yesc_step( &s, &c, cpu );
        if (us != expect[i]) FAIL("step %u: %u, expected %u\n", i, us, expect[i]);
    }
    cpu += 1000001;   /* real work: more than work_ns of CPU since the last yield */
    if ((us = ios_yesc_step( &s, &c, cpu ))) FAIL("work did not reset: %u\n", us);
    for (i = 0; i < 3; i++) if (ios_yesc_step( &s, &c, cpu += 10 )) FAIL("streak restarted too early\n");
    if (ios_yesc_step( &s, &c, cpu += 10 ) != 20) FAIL("streak did not restart at 20\n");
    ios_yesc_reset( &s );   /* a real wait */
    for (i = 0; i < 4; i++) if (ios_yesc_step( &s, &c, cpu += 10 )) FAIL("real wait did not reset\n");
    if (ios_yesc_step( &s, &c, cpu - 5 )) FAIL("backwards clock must reset\n");
    {   /* odd cap: doubling stops at the cap */
        struct ios_yesc_cfg c2 = { 0, 30, 100, 1000000 }; struct ios_yesc_state s2 = { 0 };
        unsigned e2[] = { 30, 60, 100, 100 };
        for (i = 0; i < 4; i++) if ((us = ios_yesc_step( &s2, &c2, 10 + i )) != e2[i]) FAIL("cap step %u: %u\n", i, us);
    }
    srand( 7 );
    memset( &s, 0, sizeof(s) ); cpu = 1;
    for (i = 0; i < 200000; i++)   /* fuzz: never above the cap, never a sleep inside the first `after` of a streak */
    {
        int r = rand() % 100;
        if (r < 2) { ios_yesc_reset( &s ); last = 0; continue; }
        cpu += r < 5 ? 2000000 : (unsigned)(rand() % 5000);
        us = ios_yesc_step( &s, &c, cpu );
        if (us > c.cap_us) FAIL("above the cap: %u\n", us);
        if (us && s.streak <= c.after) FAIL("slept inside the grace streak\n");
        if (us && last && us < last) FAIL("sleep shrank without a reset\n");
        last = us;
    }
    printf("PASS: ios_yesc_step: grace streak, 20 us doubling to the cap, work/real-wait/backwards resets, fuzz bound\n");

    if (!listed( "C:\\Program Files\\Rockstar Games\\Red Dead Redemption 2\\RDR2.exe", "RDR2.exe" )) FAIL("RDR2\n");
    if (!listed( "C:\\games\\rdr2.EXE", "Foo.exe; RDR2.exe" )) FAIL("case/list\n");
    if (!listed( "/unix/path/RDR2.exe", "a.exe,RDR2.exe" )) FAIL("slash/comma\n");
    if (listed( "C:\\Program Files\\Rockstar Games\\Launcher\\PlayRDR2.exe", "RDR2.exe" )) FAIL("suffix matched\n");
    if (listed( "C:\\x\\RDR2.exe2", "RDR2.exe" )) FAIL("prefix matched\n");
    if (listed( "C:\\x\\SocialClubHelper.exe", "RDR2.exe" )) FAIL("other image\n");
    if (listed( "C:\\x\\RDR2.exe", "" )) FAIL("empty list\n");
    printf("PASS: ios_yesc_image_listed: last component, case-insensitive, whole names, ';' and ',' lists\n");

    {
        pthread_t t[8]; uintptr_t k;
        for (k = 0; k < 8; k++) pthread_create( &t[k], NULL, claim, (void *)(0x100000 + k * 1000003) );
        for (k = 0; k < 8; k++) pthread_join( t[k], NULL );
        for (k = 0; k < 8; k++)
            for (i = 0; i < 64; i++)
            {
                long long v[5];
                uint64_t tid = 0x100000 + k * 1000003 + i * 7919;
                if (!ios_xp_ystat( tid, v )) FAIL("sampler missed tid %llu\n", (unsigned long long)tid);
                if (v[0] != (long long)(0x100000 + k * 1000003 + i)) FAIL("wrong slot for %llu\n", (unsigned long long)tid);
            }
        {
            long long v[5] = { 1, 1, 1, 1, 1 };
            if (ios_xp_ystat( 42, v ) || v[0] || v[4]) FAIL("unknown tid must read as zero\n");
        }
        if (ios_ystat_full) FAIL("table reported full\n");
    }
    printf("PASS: 512 threads claim slots concurrently; the sampler's lookup finds each and only its own counts\n");
    ios_ystat_on = 0; ios_ystat_self = NULL; ios_ystat_none = 0; fake_tid = 777777;
    if (ios_ystat_get() || !ios_ystat_none) FAIL("MADEIRA_YIELD_STATS=0 still claimed a slot\n");
    { long long v[5]; if (ios_xp_ystat( 777777, v )) FAIL("slot exists with stats off\n"); }
    printf("PASS: MADEIRA_YIELD_STATS=0: no slot, no counting\n");

    {
        struct ios_xp_row r; char buf[128]; int n;
        memset( &r, 0, sizeof(r) );
        r.rq_ms = 1890.4; r.yv[0] = 0; r.yv[1] = 310; r.yv[3] = 40;
        n = ios_xp_tok_extra( buf, sizeof(buf), &r ); buf[n] = 0;
        if (strcmp( buf, ":r1890z310e40" )) FAIL("token: %s\n", buf);
        r.rq_ms = -1; memset( r.yv, 0, sizeof(r.yv) );
        n = ios_xp_tok_extra( buf, sizeof(buf), &r ); buf[n] = 0;
        if (n || buf[0]) FAIL("no sampling, no yields: '%s'\n", buf);
        if (ios_xp_tok_extra( buf, 32, &r ) != 0) FAIL("small buffer\n");
    }
    printf("PASS: [xp-t] token suffix ':r<ms>' then y/z/s/e counts only when non-zero\n");

    {   /* Y1: the yields after failed polls */
        struct ios_ystat *ys;
        long long y0;
        unsigned int k;
        ios_ystat_on = 1; ios_ystat_self = NULL; ios_ystat_none = 0; fake_tid = 4242;
        if (!(ys = ios_ystat_get())) FAIL("no ystat slot\n");
        ios_yesc_reset( &ios_yesc ); escalated = 0;
        y0 = ys->yields;
        for (k = 0; k < 5000; k++) if (poll_yield_now()) FAIL("poll yield %u slept\n", k);
        if (ios_yesc.streak || escalated) FAIL("poll yields counted toward the streak (%u)\n", ios_yesc.streak);
        if (ys->yields - y0 != 5000) FAIL("poll yields must stay in [xp-t] y\n");
        if (ios_yesc_in_timed_delay) FAIL("ios_poll_yield left its mark\n");
        for (k = 0; k < 32; k++) if (guest_yield()) FAIL("guest yield %u slept inside the grace streak\n", k);
        if (guest_yield() != 20) FAIL("the 33rd guest yield did not escalate\n");
        for (k = 0; k < 200; k++) if (poll_yield_now()) FAIL("poll yield slept during an escalated streak\n");
        if (guest_yield() != 40) FAIL("polls reset or changed the guest streak\n");
        ios_yesc_reset( &ios_yesc );   /* a real wait */
        for (k = 0; k < 320; k++)      /* 32 guest yields spread over 288 polls */
        {
            unsigned int us = (k % 10 == 9) ? guest_yield() : poll_yield_now();
            if (us) FAIL("slept at %u: polls counted toward the streak\n", k);
        }
        if (ios_yesc.streak != 32) FAIL("streak %u, expected 32 guest yields\n", ios_yesc.streak);
        if (sleep0() != 20) FAIL("Sleep(0) after 32 guest yields did not escalate\n");
        if (timed()) FAIL("a timed sleep's yield escalated\n");
        ios_yesc_in_timed_delay = 2; slept_us = 1; ios_poll_yield();
        if (ios_yesc_in_timed_delay != 2 || slept_us) FAIL("ios_poll_yield inside a Sleep(0) mark\n");
        ios_yesc_in_timed_delay = 0;
    }
    printf("PASS: yields after failed polls never sleep or count toward the streak; SwitchToThread/Sleep(0) still escalate\n");
    return 0;
}
'''

with tempfile.TemporaryDirectory() as tmp:
    src = Path(tmp) / 'yesc.c'
    exe = Path(tmp) / 'yesc'
    src.write_text(harness)
    subprocess.run(['cc', '-std=gnu11', '-O1', '-g', '-Wall', '-Wno-unused-function', '-Wno-unused-variable',
                    '-fsanitize=address,undefined', '-fno-sanitize-recover=undefined', '-pthread',
                    str(src), '-o', str(exe)], check=True)
    out = subprocess.run([str(exe)], capture_output=True, text=True)
    print(out.stdout, end='')
    if out.returncode:
        raise SystemExit('FAIL: ' + out.stderr)
print('ALL PASS: check-yield-escalate')
