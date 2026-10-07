#!/usr/bin/env python3
"""A session ends when its initial process ends, on whichever thread ends it.

Only the thread __wine_main runs on can leave through the exit() shim's longjmp
(build/ntdll-unix/shims/wine_ios_exit.h) back to wine_process_thread, which used
to be the only place the session ended. When a worker ended the process
(RimWorld: Mono's unhandled exception -> NtTerminateProcess(-1) on a worker),
the first thread was killed by the server, died through pthread_exit on its
next request, and wine_process_is_running() stayed 1 for good.

Compiled from the production sources and run with real pthreads on the host:
  - WineProcessBridge.m: the exit-shim TLS, the session state,
    wine_launched_process_did_exit, wine_session_end, wine_process_is_running;
  - server_ios.c: process_exit_wrapper's branch for the session's initial
    process, followed by its exit();
  - shims/wine_ios_exit.h: the exit() shim itself.
Scenarios: a worker ends the process after the first thread died (the device
run) or while it is parked; the first thread ends it itself (normal quit); a
call for an older session. A control build without the new session end in the
hook must reproduce the hang, so the check covers the bug.

No Wine build, SDK or device needed: python3 and a C compiler with pthreads.
"""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
cc = os.environ.get('CC') or shutil.which('cc') or shutil.which('clang') or shutil.which('gcc')
failures = []


def check(cond, what):
    print(('PASS: ' if cond else 'FAIL: ') + what)
    if not cond:
        failures.append(what)


def read(rel):
    return (root / rel).read_text(encoding='utf-8').replace('\r\n', '\n')


def between(text, start, end, include_end=False):
    i = text.index(start)
    j = text.index(end, i + len(start))
    return text[i:j + (len(end) if include_end else 0)]


def func(text, header_line):
    """A C function from its first line to the closing brace at column 0."""
    return between(text, header_line, '\n}\n', include_end=True)


bridge = read('app/Madeira/WineProcessBridge.m')
server = read('build/ntdll-unix/server_ios.c')
shims = root / 'build/ntdll-unix/shims'

tls = between(bridge, '_Thread_local jmp_buf wine_ios_exit_jmpbuf;',
              '_Thread_local int wine_ios_exit_initialized = 0;', include_end=True)
state = between(bridge, 'static volatile int g_wine_running = 0;',
                'static void wine_session_end(unsigned gen);', include_end=True)
hook = between(bridge, 'static uint64_t g_launch_exit = 0;', 'static char *g_prefix_path')
session_end = func(bridge, 'static void wine_session_end(unsigned gen) {')
is_running = func(bridge, 'int wine_process_is_running(void) {')
initial_branch = between(server, "        /* No slot: this is the session's initial process", '    }\n#else')
wrapper = func(server, 'void process_exit_wrapper( int status )')
thread_fn = func(bridge, 'static void *wine_process_thread(void *arg) {')
start_fn = func(bridge, 'int wine_process_start(const char *prefix_path) {')

# ------------------------------------------------------------ source checks
close_at = initial_branch.find('close( fd_socket );')
hook_at = initial_branch.find('wine_launched_process_did_exit( status );')
check(0 <= close_at < hook_at,
      "process_exit_wrapper: the initial process's master socket closes before the app hook runs")
check(wrapper.rfind('exit( status );') > wrapper.find('wine_launched_process_did_exit( status );'),
      'process_exit_wrapper: exit() comes after the hook')
check('wine_ios_exit_initialized && pthread_equal(pthread_self(), wine_ios_main_thread)' in hook,
      "the hook leaves the first thread's session end to its longjmp (the shim's own test)")
check(hook.index('__atomic_store_n(&g_launch_exit') < hook.index('wine_session_end('),
      'the hook stores the exit status before it ends the session')
jmp = thread_fn.index('setjmp(wine_ios_exit_jmpbuf)')
check(thread_fn.find('wine_session_end(gen);', jmp) > jmp,
      'wine_process_thread ends its own session after __wine_main / the longjmp')
check('const unsigned gen = __atomic_load_n(&g_session_gen' in thread_fn
      and thread_fn.index('const unsigned gen') < jmp,
      'wine_process_thread takes its generation before setjmp (never written after it)')
bump = start_fn.find('__atomic_add_fetch(&g_session_gen')
check(0 <= bump < start_fn.index('pthread_create(&g_wine_thread'),
      'wine_process_start starts a new generation before the thread exists')
check('g_wine_running = 0;' not in session_end and
      '__atomic_store_n(&g_wine_running, 0, __ATOMIC_RELEASE);' in session_end,
      'wine_session_end clears running with a release store')

# ------------------------------------------------------------ the harness
harness = r'''
#include <pthread.h>
#include <setjmp.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#define WINE_IOS 1
#include "wine_ios_exit.h"          /* the production exit() shim */

static int stop_calls;
void wineserver_stop(void) { __atomic_add_fetch(&stop_calls, 1, __ATOMIC_SEQ_CST); }
int madeira_live_game_children(char *buf, int len, double max_age)
{
    (void)max_age;
    if (buf && len > 0) buf[0] = 0;
    return 0;
}
static void wine_log_write(const char *fmt, ...) { (void)fmt; }
static int fd_socket = -1;

''' + tls + '\n' + state + '\n' + hook + '\n' + session_end + '\n' + is_running + r'''

int wine_crash_exit_status(uint32_t *status);

/* process_exit_wrapper for the session's initial process (no slot) */
static void initial_process_teardown(int status)
{
''' + initial_branch + r'''
    exit( status );
}

static unsigned start_session(void)      /* wine_process_start's part */
{
    g_wine_running = 1;
    return __atomic_add_fetch(&g_session_gen, 1, __ATOMIC_ACQ_REL);
}

enum { BOOT_DIES, BOOT_PARKS, BOOT_EXITS };
static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t cond = PTHREAD_COND_INITIALIZER;
static int boot_go, boot_home;
struct boot { int mode; unsigned gen; int status; };

/* wine_process_thread's shape: one generation, setjmp, then the session end */
static void *boot_thread(void *arg)
{
    struct boot *b = arg;
    const unsigned gen = b->gen;
    wine_ios_main_thread = pthread_self();
    wine_ios_exit_initialized = 1;
    if (setjmp(wine_ios_exit_jmpbuf) == 0)
    {
        if (b->mode == BOOT_DIES) pthread_exit(NULL);    /* killed: pthread_exit_wrapper */
        if (b->mode == BOOT_EXITS) initial_process_teardown(b->status);
        pthread_mutex_lock(&lock);                       /* BOOT_PARKS: an in-process wait */
        while (!boot_go) pthread_cond_wait(&cond, &lock);
        pthread_mutex_unlock(&lock);
        initial_process_teardown(b->status);             /* it reaches exit() late */
    }
    wine_session_end(gen);
    __atomic_store_n(&boot_home, 1, __ATOMIC_RELEASE);
    return NULL;
}

static void *worker_thread(void *arg)
{
    initial_process_teardown(*(int *)arg);
    return NULL;
}

static int wait_ended(int ms)
{
    while (ms-- > 0)
    {
        if (!wine_process_is_running()) return 1;
        usleep(1000);
    }
    return !wine_process_is_running();
}

static void report(const char *name)
{
    uint32_t st = 0;
    int crash = wine_crash_exit_status(&st);
    printf("%s running=%d stops=%d crash=%d status=%08x home=%d\n", name, wine_process_is_running(),
           __atomic_load_n(&stop_calls, __ATOMIC_SEQ_CST), crash, crash ? st : 0,
           __atomic_load_n(&boot_home, __ATOMIC_ACQUIRE));
}

int main(int argc, char **argv)
{
    const char *s = argc > 1 ? argv[1] : "";
    pthread_t boot, worker;
    struct boot b = { 0 };
    int status;

    fd_socket = dup(1);
    if (!strcmp(s, "worker-after-first-died") || !strcmp(s, "worker-while-first-parked"))
    {
        b.mode = !strcmp(s, "worker-after-first-died") ? BOOT_DIES : BOOT_PARKS;
        b.gen = start_session();
        b.status = 0x67fe2394;
        status = (int)0xc0000409u;
        pthread_create(&boot, NULL, boot_thread, &b);
        if (b.mode == BOOT_DIES) pthread_join(boot, NULL);
        pthread_create(&worker, NULL, worker_thread, &status);
        pthread_join(worker, NULL);
        wait_ended(1000);
        report(s);
        if (b.mode == BOOT_PARKS)
        {   /* the first thread wakes later and reaches exit() itself */
            pthread_mutex_lock(&lock); boot_go = 1; pthread_cond_broadcast(&cond); pthread_mutex_unlock(&lock);
            pthread_join(boot, NULL);
            report("first-thread-home-later");
        }
    }
    else if (!strcmp(s, "first-thread-exits"))
    {
        b.mode = BOOT_EXITS;
        b.gen = start_session();
        b.status = 0;
        pthread_create(&boot, NULL, boot_thread, &b);
        pthread_join(boot, NULL);
        report(s);
    }
    else if (!strcmp(s, "stale-generation"))
    {
        unsigned old = start_session();
        wine_session_end(old);
        start_session();
        wine_session_end(old);              /* a thread of the finished session */
        report(s);
    }
    else return 2;
    return 0;
}
'''

if not cc:
    print('FAIL: no C compiler (set CC)')
    sys.exit(1)

with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    (tmp / 'os').mkdir()
    (tmp / 'os/log.h').write_text('#define OS_LOG_DEFAULT 0\n#define os_log_error(...) ((void)0)\n')

    def build(name, source):
        (tmp / f'{name}.c').write_text(source)
        r = subprocess.run([cc, '-std=gnu11', '-O1', '-pthread', f'-I{tmp}', f'-I{shims}',
                            '-o', str(tmp / name), str(tmp / f'{name}.c')],
                           capture_output=True, text=True)
        if r.returncode:
            print(r.stderr)
            print(f'FAIL: {name} does not compile')
            sys.exit(1)
        return tmp / name

    def run(binary, scenario):
        r = subprocess.run([str(binary), scenario], capture_output=True, text=True, timeout=30)
        rows = {}
        for line in r.stdout.splitlines():
            name, *fields = line.split()
            rows[name] = dict(f.split('=') for f in fields)
        return r, rows

    fixed = build('session-end', harness)

    r, rows = run(fixed, 'worker-after-first-died')
    row = rows.get('worker-after-first-died', {})
    check(row.get('running') == '0' and row.get('stops') == '1',
          'first thread already gone (the device run): the worker that ends the process ends the session '
          f'({row})')
    check(row.get('crash') == '1' and row.get('status') == 'c0000409',
          'the exit status the worker reported is visible once running reads 0')
    check('ending the session from that thread' in r.stderr, 'the worker path says so in the log')

    r, rows = run(fixed, 'worker-while-first-parked')
    row = rows.get('worker-while-first-parked', {})
    check(row.get('running') == '0' and row.get('stops') == '1',
          f'first thread parked in a wait it never leaves: the session still ends ({row})')
    late = rows.get('first-thread-home-later', {})
    check(late.get('home') == '1' and late.get('stops') == '1',
          f'the first thread coming home later does not stop the wineserver again ({late})')

    r, rows = run(fixed, 'first-thread-exits')
    row = rows.get('first-thread-exits', {})
    check(row.get('running') == '0' and row.get('stops') == '1' and row.get('home') == '1',
          f'normal quit on the first thread: ends through the longjmp, once ({row})')
    check('ending the session from that thread' not in r.stderr,
          'the hook leaves the first thread alone')

    r, rows = run(fixed, 'stale-generation')
    row = rows.get('stale-generation', {})
    check(row.get('running') == '1' and row.get('stops') == '1',
          f'a call for a finished session leaves the next one running ({row})')

    # control: the hook without the new session end is the old behaviour
    call = 'wine_session_end(__atomic_load_n(&g_session_gen, __ATOMIC_ACQUIRE));'
    check(call in harness, 'control: found the session end in the hook')
    old = build('session-end-old', harness.replace(call, ';'))
    r, rows = run(old, 'worker-after-first-died')
    row = rows.get('worker-after-first-died', {})
    check(row.get('running') == '1' and row.get('stops') == '0',
          f'control: without it the device run stays running for good ({row})')

if failures:
    print(f'\n{len(failures)} check(s) failed')
    sys.exit(1)
print('\nall checks passed')
