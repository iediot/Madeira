#!/usr/bin/env python3
"""ml2120: fastsync's per-image semaphore path (MADEIRA_FASTSYNC_SEM_EXES) and
multi-object WaitAny fast path (MADEIRA_FASTSYNC_MULTI).

1. The multi-wait core, cut out of wine/dlls/ntdll/unix/sync.c and compiled
   on the host against the shipping header, under fastsync-multirace.c:
   lowest-index order, token conservation, no lost wakeup (with a control
   that must stall), generation safety, manual reset, a single and a multi
   waiter sharing one token, and the poll/herd/budget bounds.
   FASTSYNC_TSAN=1 also runs it under ThreadSanitizer.
2. The server's image matcher (madeira_image_listed in server/event.c).
3. Source checks: both switches are off unless set and need fastsync's cells;
   the server gives a semaphore a cell only for a listed creator; every write
   that can satisfy or retire a cell is followed by the multi kick; the call
   site takes only non-alertable wait-any on more than one handle; the cell
   is still 32 bytes; the RDR2 profile carries both lines.

Needs the wine submodule and `cc` on PATH.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
header_path = root / 'wine/include/wine/madeira_fastsync.h'
if not header_path.exists():
    print('FAIL: wine/include/wine/madeira_fastsync.h missing (check out the wine submodule)')
    sys.exit(1)
header = header_path.read_text()
sync = (root / 'wine/dlls/ntdll/unix/sync.c').read_text()
event = (root / 'wine/server/event.c').read_text()
semaphore = (root / 'wine/server/semaphore.c').read_text()
library = (root / 'app/Madeira/Library.swift').read_text()
cc = os.environ.get('CC') or shutil.which('cc') or shutil.which('clang')
failures = []


def check(cond, what):
    print(('PASS: ' if cond else 'FAIL: ') + what)
    if not cond:
        failures.append(what)


def between(text, start, end, include_end=True):
    i = text.index(start)
    j = text.index(end, i + len(start))
    return text[i:j + (len(end) if include_end else 0)]


def func(text, first_line):
    return between(text, first_line, '\n}\n')


# ------------------------------------------------------------------ 1. the model
core = between(sync, '#define MADEIRA_MULTI_LADDER', 'static int madeira_multi_core(', include_end=False)
core += func(sync, 'static int madeira_multi_core(') + '\n'
alive = func(sync, 'static inline int madeira_cell_alive(') + '\n'
inc = alive + core
if not cc:
    check(False, 'a C compiler (cc) on PATH')
else:
    runs = [([], '')]
    if os.environ.get('FASTSYNC_TSAN') == '1':
        runs.append((['-fsanitize=thread'], ' (ThreadSanitizer)'))
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / 'multi_core.inc').write_text(inc)
        for extra, label in runs:
            exe = Path(tmp) / ('multirace' + ('-tsan' if extra else ''))
            r = subprocess.run([cc, '-O2' if not extra else '-O1', '-g', '-Wall', '-Wno-unused-function', *extra,
                                '-I' + tmp, '-I' + str(root / 'wine/include'), '-o', str(exe),
                                str(root / 'tests/host/fastsync-multirace.c'), '-lpthread'],
                               capture_output=True, text=True)
            check(r.returncode == 0, 'fastsync-multirace' + label + ' compiles from sync.c and the shipping header'
                  + ('' if not r.returncode else ': ' + r.stderr[-3000:]))
            if r.returncode:
                continue
            r = subprocess.run([str(exe), os.environ.get('FASTSYNC_MULTI_SECS', '3')],
                               capture_output=True, text=True, timeout=900)
            sys.stdout.write(r.stdout)
            check(r.returncode == 0 and 'WARNING: ThreadSanitizer' not in r.stderr,
                  f'fastsync-multirace{label} passes (exit {r.returncode})'
                  + ('' if r.returncode == 0 else ': ' + r.stderr[-1500:]))

        # -------------------------------------------------------------- 2. image matcher
        matcher = func(event, 'int madeira_image_listed(')
        harness = r'''
#include <stdio.h>
#include <string.h>
#include <wchar.h>
typedef unsigned short WCHAR;
''' + matcher + r'''
static int failed;
static void t( const char *path, const char *list, int want )
{
    WCHAR w[256]; unsigned int n = 0;
    if (path) while (path[n] && n < 255) { w[n] = (unsigned char)path[n]; n++; }
    int got = madeira_image_listed( path ? w : NULL, n * sizeof(WCHAR), list );
    printf( "%s: \"%s\" in \"%s\" -> %d\n", got == want ? "PASS" : "FAIL", path ? path : "(null)", list, got );
    failed += got != want;
}
int main(void)
{
    t( "\\??\\C:\\Games\\Red Dead Redemption 2\\RDR2.exe", "RDR2.exe", 1 );
    t( "\\??\\C:\\Games\\Red Dead Redemption 2\\rdr2.EXE", "RDR2.exe", 1 );
    t( "\\??\\C:\\Games\\Red Dead Redemption 2\\PlayRDR2.exe", "RDR2.exe", 0 );
    t( "\\??\\C:\\Program Files\\Rockstar Games\\Launcher\\Launcher.exe", "RDR2.exe", 0 );
    t( "\\??\\C:\\x\\RDR2.exe", "Launcher.exe;RDR2.exe", 1 );
    t( "\\??\\C:\\x\\RDR2.exe", "Launcher.exe, RDR2.exe", 1 );
    t( "\\??\\C:\\x\\RDR2.exe", "RDR2.ex", 0 );
    t( "\\??\\C:\\x\\RDR2.exe", "RDR2.exe2", 0 );
    t( "C:/x/RDR2.exe", "RDR2.exe", 1 );
    t( "\\??\\C:\\x\\steam.exe", "1", 1 );
    t( "\\??\\C:\\x\\steam.exe", "all", 1 );
    t( NULL, "RDR2.exe", 0 );
    t( "\\??\\C:\\x\\", "RDR2.exe", 0 );
    t( "\\??\\C:\\x\\RDR2.exe", ";;", 0 );
    return failed != 0;
}
'''
        src = Path(tmp) / 'listed.c'
        src.write_text(harness)
        exe = Path(tmp) / 'listed'
        r = subprocess.run([cc, '-O1', '-Wall', '-o', str(exe), str(src)], capture_output=True, text=True)
        check(r.returncode == 0, 'madeira_image_listed compiles on its own' + ('' if not r.returncode else ': ' + r.stderr[-2000:]))
        if r.returncode == 0:
            r = subprocess.run([str(exe)], capture_output=True, text=True)
            sys.stdout.write(r.stdout)
            check(r.returncode == 0, 'the server image matcher: listed names only, case-insensitive, 1/all = every image')

        # the cell layout is unchanged: 32 bytes, mwaiters where `reserved' was
        lay = Path(tmp) / 'layout.c'
        lay.write_text('#include "wine/madeira_fastsync.h"\n#include <stddef.h>\n'
                       'unsigned int madeira_multi_epoch;\nstruct madeira_sync_cell madeira_sync_cells[MADEIRA_SYNC_CELLS];\n'
                       '_Static_assert( sizeof(struct madeira_sync_cell) == 32, "cell size" );\n'
                       '_Static_assert( offsetof(struct madeira_sync_cell, mwaiters) == 28, "mwaiters offset" );\n'
                       'int main(void) { madeira_cell_kick_multi( &madeira_sync_cells[0] ); return madeira_multi_epoch != 0; }\n')
        r = subprocess.run([cc, '-O1', '-Wall', '-I' + str(root / 'wine/include'), '-o', str(Path(tmp) / 'layout'), str(lay)],
                           capture_output=True, text=True)
        ok = r.returncode == 0 and subprocess.run([str(Path(tmp) / 'layout')]).returncode == 0
        check(ok, 'the cell stays 32 bytes, and a kick with no multi waiter registered changes nothing'
              + ('' if not r.returncode else ': ' + r.stderr[-1500:]))

# ------------------------------------------------------------------ 3. source checks
parse = func(sync, 'static void madeira_fast_parse_env(void)')
check('sem_exes = mode != MADEIRA_FS_MODE_OFF && madeira_fast_list_set( e );' in parse and
      'multi = mode != MADEIRA_FS_MODE_OFF && madeira_fast_list_set( e );' in parse,
      'client: both switches need fastsync cells (off with fastsync off or under madsync)')
lst = func(sync, 'static int madeira_fast_list_set(')
check("return e && *e && !(e[0] == '0' && !e[1]);" in lst, 'client: unset, empty and "0" are off')
semen = func(sync, 'static inline int madeira_fast_sem_enabled(void)')
check(semen.index('madeira_fast_sem, __ATOMIC_RELAXED )) return 1;') < semen.index('madeira_fast_sem_exes_on'),
      'client: MADEIRA_FASTSYNC_SEM still means every process; the list only adds this thread\'s image')
srv = func(event, 'int madeira_fastsync_sem_exes_set(void)')
check("int on = e && *e && !(e[0] == '0' && !e[1]);" in srv and 'madeira_fastsync_enabled()' in srv,
      'server: the list is off unless set, and needs fastsync cells')
alloc = func(event, 'int madeira_sem_cell_alloc(')
check('if (!madeira_fastsync_sem_enabled() && !madeira_sem_exes_current()) return -1;' in alloc,
      'server: a semaphore gets a cell for MADEIRA_FASTSYNC_SEM, or for a listed creator only')
cur = func(event, 'static int madeira_sem_exes_current(void)')
check('current->process->image' in cur and 'madeira_image_listed(' in cur, 'server: the creator is the requesting process')
check('cell->mwaiters    = 0;' in func(event, 'static int madeira_cell_alloc_kind('), 'server: a new cell starts with no multi waiters')
dis = func(semaphore, 'static void semaphore_sync_disable_cell(')
check('if (madeira_fastsync_sem_exes_set()) wake_up( &sem->obj, 0 );' in dis,
      'server: a disabled semaphore re-runs its queue (only with MADEIRA_FASTSYNC_SEM_EXES set)')

# every write that can satisfy or retire a cell kicks the multi waiters
for text, fname, name in [
        (event, 'static void madeira_cell_free( int idx )\n{', 'event.c cell free'),
        (event, 'static void event_sync_disable_cell(', 'event.c disable'),
        (event, 'void madeira_event_sync_unclaim(', 'event.c unclaim'),
        (event, 'static int event_sync_signal( struct object *obj, unsigned int access, int signal )\n{', 'event.c set'),
        (semaphore, 'static void semaphore_cell_wake(', 'semaphore.c release'),
        (semaphore, 'static void semaphore_sync_disable_cell(', 'semaphore.c disable'),
        (semaphore, 'void madeira_semaphore_sync_unclaim(', 'semaphore.c unclaim'),
        (sync, 'static enum madeira_fast_result madeira_fast_sem_release(', 'client release'),
        (sync, 'static enum madeira_fast_result madeira_fast_event_op(', 'client set')]:
    body = func(text, fname)
    k = body.find('madeira_cell_kick_multi( cell )')
    w = body.find('madeira_fast_wake( madeira_cell_futex( cell )')
    check(k > 0 and w > 0 and k > w, f'{name}: the multi kick follows the state write and the futex wake')

nt = between(sync, 'NTSTATUS WINAPI NtWaitForMultipleObjects(', '\n}\n')
call = nt.index('madeira_fast_wait_multi(')
pre = nt[:call]
check(pre.rfind('#ifdef WINE_IOS') > pre.rfind('#endif'), 'NtWaitForMultipleObjects: the multi call is under #ifdef WINE_IOS')
check('if (count > 1 && type != WaitAll && !alertable)' in nt[pre.rfind('#ifdef WINE_IOS'):call],
      'NtWaitForMultipleObjects: only a non-alertable wait-any on more than one handle')
check(call > nt.index('inproc_wait( count, handles, type, alertable, timeout )'),
      'NtWaitForMultipleObjects: madsync\'s inproc_wait still answers first')
mw = func(sync, 'static NTSTATUS madeira_fast_wait_multi(')
check('madeira_fast_armed()' in mw and 'madeira_fastsync_enabled()' not in mw,
      'the multi path needs the armed wake path and never counts towards arming it')
check(mw.index('madeira_fast_multi_on') < mw.index('madeira_fast_lookup('),
      'the multi path looks nothing up (no learn request) unless the switch is on')
mcore = func(sync, 'static int madeira_multi_core(')
check('MADEIRA_MULTI_MAX_ROUNDS' in mcore and 'MADEIRA_MULTI_MAX_TRIES' in mcore and 'budget_ns - spent' in mcore,
      'the multi core is bounded by rounds, tries and the budget')
for text, name in [(sync, 'sync.c'), (event, 'event.c'), (semaphore, 'semaphore.c')]:
    added = [l for l in text.splitlines() if 'ml2120' in l or 'multi' in l.lower()]
    check(not any(re.search(r'\b(malloc|calloc|realloc)\b', l) for l in added), f'{name}: no allocation in the ml2120 lines')

check('env.MADEIRA_FASTSYNC_SEM_EXES = RDR2.exe' in library and 'env.MADEIRA_FASTSYNC_MULTI = RDR2.exe' in library,
      'Library.swift: the RDR2 profile turns both on for RDR2.exe')

print('check-fastsync-multi:', 'FAIL' if failures else 'PASS')
sys.exit(1 if failures else 0)
