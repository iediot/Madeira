#!/usr/bin/env python3
"""Loading-screen wait census (madeira-bcd load-wait), [sock-tl] owner, guest-log files.

- loadwait_ios.h under ASan/UBSan: MADEIRA_LOAD_WAIT parsing, image matching,
  timeout buckets, the per-(thread, kind, object, timeout) table (aggregation,
  overflow counted not lost silently, sorted take), the arming state machine
  (needs the followed pid AND a Present; reports every 2 s; one final report),
  and eight threads recording while the sampler takes windows.
- Source wiring: off = one load in ios_wait_leave and in sync.c's two wrappers,
  which then call the unchanged functions; sleep notes only non-alertable
  delays (alertable ones are server waits); the sampler step runs from the [xp]
  loop; perf_ios.c posts both probes; the wineserver answers the handle probe
  from its handle table, read-only.
- The guest-log region of wine/dlls/ntdll/unix/file.c, compiled twice against
  real files: with MADEIRA_GUEST_LOG_FILES unset its output is byte-identical to
  the committed region's; with it set, listed files are mirrored in full with
  their own budget and redaction, others still go through the keyword mirror.
- [sock-tl]: by=<pid>/<tid> recorded once per connection, MADEIRA_SOCK_TL_OWNER=0
  prints the old line.
"""
from pathlib import Path
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
ntdll = root / 'build/ntdll-unix'
server = (ntdll / 'server_ios.c').read_text()
perf = (ntdll / 'perf_ios.c').read_text()
queue = (root / 'build/wineserver/queue_ios.c').read_text()
sync = (root / 'wine/dlls/ntdll/unix/sync.c').read_text()
socket_c = (root / 'wine/dlls/ntdll/unix/socket.c').read_text()
file_c = (root / 'wine/dlls/ntdll/unix/file.c').read_text()
library = (root / 'app/Madeira/Library.swift').read_text()
failures = []


def check(cond, what):
    print(('ok: ' if cond else 'FAIL: ') + what)
    if not cond:
        failures.append(what)


def body(text, start, end_marker='\n}\n'):
    i = text.index(start)
    return text[i:text.index(end_marker, i) + len(end_marker)]


census_test = r'''
#include <assert.h>
#include <pthread.h>
volatile int ios_lw_state;
#include "loadwait_ios.h"

static uint16_t W[256];
static int wide(const char *s) { int n = 0; while (s[n]) { W[n] = (unsigned char)s[n]; n++; } return n; }

static struct ios_lw_key key(unsigned tid, int kind, uint64_t obj, int tmo) {
    struct ios_lw_key k; memset(&k, 0, sizeof(k));
    k.tid = tid; k.kind = kind; k.op = kind == IOS_LW_SELECT ? 0 : -1; k.count = kind == IOS_LW_SELECT;
    k.obj = obj; k.tmo_ms = tmo; return k;
}

static volatile int stop;
static void *recorder(void *p) {
    unsigned tid = (unsigned)(uintptr_t)p;
    for (unsigned i = 0; !stop; i++) {
        struct ios_lw_key k = key(tid, IOS_LW_SELECT, 0x100 + (i % 4), -1);
        ios_lw_record(&k, 1000, 1, 0x50, 0x44c, 0, 0);
    }
    return NULL;
}

int main(void) {
    char exes[192]; int secs, fin;
    struct ios_lw_ent rows[IOS_LW_SLOTS];
    uint32_t dropped, waits;

    /* parse */
    assert(!ios_lw_parse(NULL, exes, sizeof(exes), &secs));
    assert(!ios_lw_parse("", exes, sizeof(exes), &secs));
    assert(!ios_lw_parse("0", exes, sizeof(exes), &secs));
    assert(!ios_lw_parse("180", exes, sizeof(exes), &secs) && !exes[0]);
    assert(!ios_lw_parse(":60", exes, sizeof(exes), &secs));
    assert(!ios_lw_parse("RDR2.exe:abc", exes, sizeof(exes), &secs));
    assert(ios_lw_parse("RDR2.exe", exes, sizeof(exes), &secs) && !strcmp(exes, "RDR2.exe") && secs == 180);
    assert(ios_lw_parse("RDR2.exe:120", exes, sizeof(exes), &secs) && !strcmp(exes, "RDR2.exe") && secs == 120);
    assert(ios_lw_parse("a.exe;b.exe:5", exes, sizeof(exes), &secs) && !strcmp(exes, "a.exe;b.exe") && secs == 10);
    assert(ios_lw_parse("a.exe:5000", exes, sizeof(exes), &secs) && secs == 900);

    /* image names: file name only, case-insensitive, ; or , lists */
    int n = wide("C:\\Program Files (x86)\\Steam\\steamapps\\common\\Red Dead Redemption 2\\RDR2.exe");
    assert(ios_lw_image_listed(W, n, "RDR2.exe"));
    assert(ios_lw_image_listed(W, n, "rdr2.EXE"));
    assert(ios_lw_image_listed(W, n, "Launcher.exe;RDR2.exe"));
    assert(ios_lw_image_listed(W, n, "Launcher.exe,rdr2.exe"));
    assert(!ios_lw_image_listed(W, n, "DR2.exe"));
    assert(!ios_lw_image_listed(W, n, "RDR2.exe2"));
    n = wide("C:\\Program Files (x86)\\Steam\\steamapps\\common\\Red Dead Redemption 2\\PlayRDR2.exe");
    assert(!ios_lw_image_listed(W, n, "RDR2.exe"));
    assert(!ios_lw_image_listed(W, 0, "RDR2.exe"));

    /* timeout buckets: server_select abs_timeout */
    assert(ios_lw_tmo_bucket(0x7fffffffffffffffll, 5, 0) == IOS_LW_TMO_INF);
    assert(ios_lw_tmo_bucket(0, 5, 0) == 0);
    assert(ios_lw_tmo_bucket(132000000000000000ll, 5, 0) == IOS_LW_TMO_ABS);
    /* asked 16 ms at qpc 1e9; 10 ms already passed when it returned early */
    assert(ios_lw_tmo_bucket(-(1000000000ll + 160000), 10000000ull, 1000000000ll + 100000) == 16);
    /* deadline passed: the request is the elapsed time */
    assert(ios_lw_tmo_bucket(-(1000000000ll + 160000), 16000000ull, 1000000000ll + 160000) == 16);
    assert(ios_lw_tmo_bucket(-(1000000000ll + 12340000ll), 0, 1000000000ll) == 1230);
    assert(ios_lw_tmo_bucket(-(1000000000ll + 123456789ll), 0, 1000000000ll) == 12000);
    { long long t = -160000; assert(ios_lw_nt_tmo_bucket(&t) == 16); }
    { long long t = 0; assert(ios_lw_nt_tmo_bucket(&t) == 0); }
    { long long t = 5; assert(ios_lw_nt_tmo_bucket(&t) == IOS_LW_TMO_ABS); }
    assert(ios_lw_nt_tmo_bucket(NULL) == IOS_LW_TMO_INF);

    /* table: aggregation, waker of the last woken wait, timeouts, immediate */
    struct ios_lw_key a = key(0x450, IOS_LW_SELECT, 0x1a4, -1), b = key(0x450, IOS_LW_SLEEP, 0, 10);
    struct ios_lw_key c = key(0x4b8, IOS_LW_ALERT, 0x7000123, -1);
    ios_lw_record(&a, 500000000ull, 1, 0x5c0, 0x118, 0, 0);
    ios_lw_record(&a, 900000000ull, 1, 0x5c4, 0x118, 0, 0);
    ios_lw_record(&a, 20000ull, 0, 0, 0, 0, 0);
    ios_lw_record(&b, 10000000ull, 0, 0, 0, 0x102, 1);
    ios_lw_record(&c, 30000000ull, 0, 0, 0, 0, 0);
    unsigned m = ios_lw_take(rows, IOS_LW_SLOTS, &dropped, &waits);
    assert(m == 3 && dropped == 0 && waits == 5);
    assert(rows[0].k.tid == 0x450 && rows[0].k.kind == IOS_LW_SELECT && rows[0].n == 3);
    assert(rows[0].sum_ns == 1400020000ull && rows[0].max_ns == 900000000ull);
    assert(rows[0].woken == 2 && rows[0].waker == 0x5c4 && rows[0].waker_pid == 0x118 && rows[0].immediate == 1);
    assert(rows[1].k.kind == IOS_LW_ALERT && rows[2].k.kind == IOS_LW_SLEEP && rows[2].timeouts == 1);
    { char line[600]; ios_lw_format(line, sizeof(line), &rows[0], 0x450);
      assert(strstr(line, "0450* select op=0 n=1 h=1a4 tmo=inf x3 sum=1400.0ms max=900.0ms"));
      assert(strstr(line, "waker=05c4/0118 (2 of 3 recorded)"));
      ios_lw_format(line, sizeof(line), &rows[1], 0x450);
      assert(strstr(line, "04b8 alert addr=7000123 tmo=inf x1") && strstr(line, "waker=none/"));
      ios_lw_format(line, sizeof(line), &rows[2], 0x450);
      assert(strstr(line, "0450* sleep - tmo=10ms x1") && strstr(line, "timeouts=1")); }
    assert(ios_lw_take(rows, IOS_LW_SLOTS, &dropped, &waits) == 0 && waits == 0);   /* a new window */

    /* overflow: counted, the table stays bounded */
    for (unsigned i = 0; i < 400; i++) { struct ios_lw_key k = key(0x450, IOS_LW_SELECT, i + 1, -1); ios_lw_record(&k, 1, 0, 0, 0, 0, 0); }
    m = ios_lw_take(rows, IOS_LW_SLOTS, &dropped, &waits);
    assert(m == IOS_LW_SLOTS - IOS_LW_SLOTS / 8 && dropped == 400 - m && waits == 400);

    /* a wait finishing in a signal handler during this thread's own insert is skipped, not deadlocked */
    ios_lw_busy = 1;
    { struct ios_lw_key k = key(0x450, IOS_LW_SELECT, 1, -1); ios_lw_record(&k, 1, 0, 0, 0, 0, 0); }
    ios_lw_busy = 0;
    assert(ios_lw_take(rows, IOS_LW_SLOTS, &dropped, &waits) == 0 && waits == 0);

    /* arming: pid AND a Present; 2 s reports; final at the end */
    ios_lw.secs = 10;
    ios_lw_state = IOS_LW_WAITING;
    assert(!ios_lw_tick(1000000000ull, 1, 5, &fin) && ios_lw_state == IOS_LW_WAITING);   /* no pid yet */
    ios_lw.pid = 0x44c;
    assert(!ios_lw_tick(1100000000ull, 0, 5, &fin) && ios_lw_state == IOS_LW_WAITING);  /* no counter block */
    assert(!ios_lw_tick(1200000000ull, 1, 0, &fin) && ios_lw_state == IOS_LW_WAITING);  /* no Present yet */
    assert(!ios_lw_tick(2000000000ull, 1, 1, &fin) && ios_lw_state == IOS_LW_ARMED);
    assert(ios_lw.t0_ns == 2000000000ull && ios_lw.until_ns == 12000000000ull);
    assert(!ios_lw_tick(3000000000ull, 1, 50, &fin));
    assert(ios_lw_tick(4000000000ull, 1, 90, &fin) && !fin);
    ios_lw.last_report_ns = 4000000000ull;
    assert(!ios_lw_tick(5999999999ull, 1, 90, &fin));
    assert(ios_lw_tick(12000000000ull, 1, 90, &fin) && fin && ios_lw_state == IOS_LW_DONE);
    assert(!ios_lw_tick(14000000000ull, 1, 90, &fin) && !fin);   /* done stays done */

    /* concurrent recorders against the sampler */
    pthread_t th[8];
    for (unsigned i = 0; i < 8; i++) pthread_create(&th[i], NULL, recorder, (void *)(uintptr_t)(0x500 + 4 * i));
    uint64_t total = 0;
    for (int r = 0; r < 200; r++) {
        m = ios_lw_take(rows, IOS_LW_SLOTS, &dropped, &waits);
        assert(m <= 32 && dropped == 0);
        uint64_t s = 0;
        for (unsigned i = 0; i < m; i++) { assert(rows[i].k.tid >= 0x500 && rows[i].k.tid < 0x520); s += rows[i].n; if (i) assert(rows[i - 1].sum_ns >= rows[i].sum_ns); }
        assert(s == waits);
        total += s;
    }
    stop = 1;
    for (unsigned i = 0; i < 8; i++) pthread_join(th[i], NULL);
    printf("census ok (%llu concurrent waits)\n", (unsigned long long)total);
    return 0;
}
'''

with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    (tmp / 't.c').write_text(census_test)
    exe = tmp / 't'
    r = subprocess.run(['cc', '-std=gnu11', '-O1', '-g', '-Wall', '-Werror', '-Wno-unused-function',
                        '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-pthread',
                        '-I' + str(ntdll), str(tmp / 't.c'), '-o', str(exe)], capture_output=True, text=True)
    check(r.returncode == 0, 'loadwait_ios.h compiles with -Wall -Werror under ASan/UBSan' + ('' if r.returncode == 0 else ': ' + r.stderr[-2000:]))
    if r.returncode == 0:
        r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=120)
        check(r.returncode == 0, 'census: parse, image match, timeout buckets, table, reentrancy, arming, 8 concurrent recorders'
              + ('' if r.returncode == 0 else ': ' + (r.stdout + r.stderr)[-2000:]))

# ---- source wiring: off state, hooks, probes ----
leave = body(server, 'static void ios_wait_leave(')
check('__atomic_load_n( &ios_lw_state, __ATOMIC_RELAXED ) != IOS_LW_OFF' in leave and leave.index('ios_lw_note_select') < leave.index('ios_wait_report_done'),
      'ios_wait_leave: off = one load of ios_lw_state, census before [wait-done] and unpublish')
check(leave.count('ios_wait_unpublish( tok );') == 1 and leave.count('ios_wait_report_done( tok, ret );') == 1,
      'ios_wait_leave still reports and unpublishes exactly once')
note = body(server, 'static void ios_lw_note_select(')
check('IOS_LW_ARMED' in note and 'ios_lw.pid' in note and 'ios_wait_take_wake' in note and 'ios_wait_copy_out' in note,
      'select census: only while armed, only the followed pid, waker from the server record')
check('ios_lw_record(' in note and 'STATUS_TIMEOUT' in note, 'select census records duration, waker, status, timeout')
other = body(server, 'void ios_lw_note_other(')
check('IOS_LW_ARMED' in other and 'ios_lw.pid' in other, 'sleep/alert census: only while armed, only the followed pid')
check('ios_lw_sampler( pe.magic == 0x3130305058444d4dLL, pe.pres_enq );' in server, 'the [xp] loop runs the census step with the Present counter')
samp = body(server, 'static void ios_lw_sampler( int pe_valid, long long pres )\n{')
check('getenv( "MADEIRA_LOAD_WAIT" )' in samp and 'IOS_LW_WAITING' in samp, 'MADEIRA_LOAD_WAIT read once by the sampler; unset leaves the state off')
check('ios_perf_load_wait_now(' in samp and 'named[' in samp, 'reports name current waits and new top handles (each handle at most once per 20 s)')
check('server_select_tracked' in server and server.count('ios_lw_note_select( tok, ret )') == 1, 'one select hook')
now = body(perf, 'void ios_perf_load_wait_now(')
check('ios_wait_probe.gen' in now and 'ios_handle_probe.gen' in now and 'perf_wait_caller(' in now and 'ios_wineserver_wake' in now,
      'perf_ios.c: [load-wait-now] + caller stack, posts the wait and handle probes')
check('thread_suspend' not in now and 'pthread_kill' not in now, 'no thread is suspended or signalled')
hp = body(queue, 'static void ios_handle_probe_service(')
check('get_process_from_id' in hp and 'get_handle_obj' in hp and hp.count('release_object( process )') == 2 and 'release_object( obj );' in hp,
      'wineserver names the handle from its process handle table and releases what it took')
check('clear_error();' in hp and 'set_event' not in hp and 'wake_thread' not in hp, 'handle probe is read-only')
check('ios_handle_probe_service();' in body(queue, 'void ios_wait_probe_service('), 'the server loop polls the handle probe with the wait probe')

delay = body(sync, 'NTSTATUS WINAPI NtDelayExecution( BOOLEAN alertable, const LARGE_INTEGER *timeout )\n{\n    if')
check('== 2 /* IOS_LW_ARMED */' in delay and '!alertable' in delay and 'return ios_delay_execution( alertable, timeout );' in delay,
      'NtDelayExecution: off = one load then the unchanged function; only non-alertable delays are timed')
alert = body(sync, 'NTSTATUS WINAPI NtWaitForAlertByThreadId( const void *address, const LARGE_INTEGER *timeout )\n{\n    if')
check('== 2 /* IOS_LW_ARMED */' in alert and 'return ios_wait_for_alert_by_thread_id( address, timeout );' in alert,
      'NtWaitForAlertByThreadId: off = one load then the unchanged function')
check('#define NtDelayExecution ios_delay_execution\nstatic\n#endif\nNTSTATUS WINAPI NtDelayExecution(' in sync
      and '#define NtWaitForAlertByThreadId ios_wait_for_alert_by_thread_id\nstatic\n#endif\nNTSTATUS WINAPI NtWaitForAlertByThreadId(' in sync
      and sync.count('#undef NtDelayExecution') == 1 and sync.count('#undef NtWaitForAlertByThreadId') == 1,
      'the original bodies keep their text (renamed on iOS only); non-iOS builds are unchanged')
check(sync.index('NTSTATUS WINAPI NtDelayExecution(') < sync.index('#undef NtDelayExecution')
      and sync.index('NTSTATUS WINAPI NtWaitForAlertByThreadId(') < sync.index('#undef NtWaitForAlertByThreadId'),
      'the wrappers come after the bodies they call')

# ---- [sock-tl] owner ----
flush = body(socket_c, 'static void ios_tl_flush(')
check('by=%04x/%04x' in flush and 'ios_tl_owner_on()' in flush, '[sock-tl] prints by=pid/tid when the owner switch is on')
check(flush.count('rev=ml591') == 2 and 'host=%s rev=ml591' in flush, 'MADEIRA_SOCK_TL_OWNER=0 prints the unchanged line')
check('getenv( "MADEIRA_SOCK_TL_OWNER" )' in socket_c and "!(e && e[0] == '0')" in body(socket_c, 'static int ios_tl_owner_on('),
      'owner switch defaults on, 0 turns it off')
check('e->wpid = (unsigned int)(ULONG_PTR)NtCurrentTeb()->ClientId.UniqueProcess;' in socket_c, 'owner recorded once, when the connection is first seen')

# ---- profile ----
check('env.MADEIRA_LOAD_WAIT = RDR2.exe:180' in library and 'env.MADEIRA_GUEST_LOG_FILES = Social Club' in library,
      'RDR2 profile turns the census and the Social Club log mirror on')

# ---- guest-log files: committed region vs new region, real files ----
begin, end = '/* guest-log-test:begin', '/* guest-log-test:end */'
new_region = file_c[file_c.index(begin):file_c.index(end)]
old_file = subprocess.run(['git', '-C', str(root / 'wine'), 'show', 'HEAD:dlls/ntdll/unix/file.c'],
                          capture_output=True, text=True).stdout
old_region = old_file[old_file.index(begin):old_file.index(end)] if begin in old_file else None
check(old_region is not None, 'committed guest-log region found')

harness = r'''
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <unistd.h>
static unsigned int GetCurrentThreadId(void) { return 0x450; }
#include "region.c"
static void put(const char *path, const char *text) {
    int fd = open(path, O_WRONLY | O_CREAT | O_APPEND, 0600);
    if (fd < 0) { perror(path); exit(2); }
    ios_guest_log_mirror(fd, text, (unsigned int)strlen(text));
    close(fd);
}
int main(int argc, char **argv) {
    const char *d = argv[1];
    char sc[PATH_MAX], other[PATH_MAX], bin[PATH_MAX], txt[PATH_MAX];
    snprintf(sc, sizeof(sc), "%s/Social Club/socialclub.log", d);
    snprintf(other, sizeof(other), "%s/Launcher/launcher.log", d);
    snprintf(bin, sizeof(bin), "%s/Social Club/data.bin", d);
    snprintf(txt, sizeof(txt), "%s/Social Club/notes.dat", d);
    put(other, "[DISPLAY] start\nError: Can't find Start Menu folder!\n");
    put(sc, "Signing in...\nticket=AAAA1234 user ok\nGPU adapter found\nsame\nsame\nemail: x@y.z done\n");
    put(bin, "Signing in error\n");
    put(txt, "Signing in error\n");
    put(other, "gpu driver failure two\n");
    put(sc, "sign in complete, password=hunter2\n");
    for (int i = 0; i < 20; i++) { char b[64]; snprintf(b, sizeof(b), "line %d failure\n", i); put(sc, b); }
    return 0;
}
'''


def run_region(region, env_extra):
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        (t / 'region.c').write_text('#define WINE_IOS 1\n' + region)
        (t / 'h.c').write_text(harness)
        exe = t / 'h'
        r = subprocess.run(['cc', '-std=gnu11', '-O1', '-g', '-Wno-unused-function', '-fsanitize=address,undefined',
                            '-fno-sanitize-recover=all', '-I' + str(t), str(t / 'h.c'), '-o', str(exe)],
                           capture_output=True, text=True)
        if r.returncode:
            return None, r.stderr[-2000:]
        data = t / 'data'
        (data / 'Social Club').mkdir(parents=True)
        (data / 'Launcher').mkdir()
        env = {k: v for k, v in os.environ.items() if not k.startswith('MADEIRA_GUEST_LOG')}
        env.update(env_extra)
        r = subprocess.run([str(exe), str(data)], capture_output=True, text=True, env=env, timeout=60)
        if r.returncode:
            return None, (r.stdout + r.stderr)[-2000:]
        return r.stderr.replace(os.path.realpath(data), 'DATA').replace(str(data), 'DATA'), ''


if old_region is not None:
    for env_extra, label in (({}, 'default'), ({'MADEIRA_GUEST_LOG': 'all'}, 'all'),
                             ({'MADEIRA_GUEST_LOG': '0'}, 'off'), ({'MADEIRA_GUEST_LOG_LIMIT': '3'}, 'limit 3')):
        old_out, err1 = run_region(old_region, env_extra)
        new_out, err2 = run_region(new_region, env_extra)
        check(old_out is not None and new_out is not None and old_out == new_out,
              'guest-log mirror with MADEIRA_GUEST_LOG_FILES unset is byte-identical to the committed one (%s)' % label
              + ('' if old_out == new_out else ': ' + (err1 or err2 or '\nOLD:\n' + str(old_out) + '\nNEW:\n' + str(new_out))[-1500:]))
    out, err = run_region(new_region, {'MADEIRA_GUEST_LOG_FILES': 'social club', 'MADEIRA_GUEST_LOG_FILES_LIMIT': '12'})
    check(out is not None, 'guest-log files build and run' + ('' if out is not None else ': ' + err))
    if out is not None:
        fl = [l for l in out.splitlines() if l.startswith('[guest-log] F')]
        check('[guest-log] F file DATA/Social Club/socialclub.log' in out, 'a listed file is announced once')
        check(any('Signing in...' in l for l in fl) and any('sign in complete' in l for l in fl),
              'every line of a listed file is mirrored, keywords or not')
        check(any('ticket=<redacted> user ok' in l for l in fl) and any('email: <redacted> done' in l for l in fl)
              and any('password=<redacted>' in l for l in fl) and 'AAAA1234' not in out and 'hunter2' not in out and 'x@y.z' not in out,
              'tickets, emails and passwords are redacted')
        check(sum('same' in l for l in fl) == 1, 'consecutive repeats are dropped')
        check(len([l for l in fl if l.startswith('[guest-log] F#')]) == 12, 'the files budget is its own (12 lines)')
        check('data.bin' not in out and 'notes.dat' not in out, 'only *.log / *.txt files, never other files in the folder')
        check(any(l.startswith('[guest-log] #') and 'Start Menu' in l for l in out.splitlines()),
              'unlisted files still go through the keyword mirror')
        check(not any(l.startswith('[guest-log] #') and 'Signing in' in l for l in out.splitlines()),
              'listed files are not mirrored twice')

if failures:
    print('check-load-wait: %d FAILED' % len(failures))
    raise SystemExit(1)
print('check-load-wait: all passed')
