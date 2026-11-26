#!/usr/bin/env python3
"""Per-event log lines turned into samples and counts; no Wine runs.

RDR2 log 2026-11-27 01:05 (232k lines): 40,732 FEX "Unhandled JIT SIGBUS" (each followed by an
empty line), 19,353 "Handled inline self-modifying code", 4,882 "Add SMC interval", 642 "Failed
to mprotect last page of code buffer", and 16,647 "[decommit-zero] ... branch=swap-zero".

Compiles madeira_fex_log_drop from wine/dlls/ntdll/unix/debug.c (unixcall_wine_dbg_write, where
the PE ntdll's __wine_dbg_output lines, FEX's included, are written) and checks: each kind keeps its first lines, then one in
`every` together with a "[fex-log]" count line; other FEX lines, Wine lines, "Add image SMC
interval" and malformed prefixes pass; env.MADEIRA_FEX_LOG_ALL=1 writes everything as before.
Textually: virtual_ios.c rate-limits only the swap-zero branch of the no-read-back line.
Needs python3 and a C compiler (AddressSanitizer/UBSan).
"""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
debug = (root / 'wine/dlls/ntdll/unix/debug.c').read_text()
thread = (root / 'wine/dlls/ntdll/thread.c').read_text()
virtual = (root / 'build/ntdll-unix/virtual_ios.c').read_text()


def function(source, signature):
    start = source.index(signature)
    return source[start:source.index('\n}', start) + 2] + '\n'


wr = function(debug, 'NTSTATUS unixcall_wine_dbg_write( void *args )')
assert 'if (madeira_fex_log_drop( params->str, params->len )) return params->len;' in wr
assert wr.index('madeira_fex_log_drop') < wr.index('return write( 2, params->str, params->len );')
assert 'madeira_fex_log_drop' not in thread, 'the PE ntdll stays stock'
dc = virtual[virtual.index('else if (strcmp( dc_branch, "swap-zero" ) || dc_n <= 8 || (dc_n % 4096) == 0 || ios_decommit_log_all())'):][:900]
assert '"(no read-back: this branch does not zero)\\n"' in dc
assert virtual.count('(no read-back: this branch does not zero)') == 1
helper = function(virtual, 'static int ios_decommit_log_all(void)')
assert 'getenv( "MADEIRA_DECOMMIT_LOG_ALL" )' in helper and "e[0] == '1'" in helper
print('PASS: the unix dbg_write filters whole FEX lines (PE ntdll untouched); swap-zero [decommit-zero] 8 then 1 in 4096, MADEIRA_DECOMMIT_LOG_ALL=1 logs all')

drop = function(debug, 'static int madeira_fex_log_drop( const char *str, unsigned int len )')
harness = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define FAIL(...) do { fprintf(stderr, __VA_ARGS__); exit(1); } while (0)
static char last[256]; static int writes;
static long fake_write( int fd, const char *s, size_t len )
{
    if (fd != 2 || len >= sizeof(last) || s[len - 1] != '\n') FAIL("bad count line\n");
    memcpy( last, s, len ); last[len] = 0; writes++; return (long)len;
}
#define write fake_write
''' + drop + r'''
static int passed( const char *line, int n ) { int i, p = 0; for (i = 0; i < n; i++) p += !madeira_fex_log_drop( line, strlen( line ) ); return p; }
int main( int argc, char **argv )
{
    int p;
    if (argc > 1)   /* MADEIRA_FEX_LOG_ALL=1 */
    {
        if (passed( "D 1D8 Handled inline self-modifying code: pc: 1\n", 5000 ) != 5000 || writes) FAIL("LOG_ALL dropped lines\n");
        printf("PASS: MADEIRA_FEX_LOG_ALL=1 writes every line, no count lines\n");
        return 0;
    }
    p = passed( "D 1D8 Handled inline self-modifying code: pc: 137FFE65C rip: 125E8BFCE fault: 125E8C102\n", 32 + 2048 * 3 );
    if (p != 32 + 3) FAIL("smc kept %d\n", p);
    if (!strstr( last, "[fex-log] 6144 \"Handled inline self-modifying code\" lines so far; after the first 32, one in 2048" )) FAIL("%s", last);
    if (writes != 3) FAIL("count lines %d\n", writes);
    p = passed( "E 11C Unhandled JIT SIGBUS: PC: 0x134fac14c Instruction: 0x38000028\n\n", 64 + 1024 );
    if (p != 65) FAIL("sigbus kept %d\n", p);
    p = passed( "D 114 Add SMC interval: 70F7CE0000 - 70F7CE1000\n", 32 + 1024 );
    if (p != 33) FAIL("interval kept %d\n", p);
    p = passed( "E 4C0 Failed to mprotect last page of code buffer.\n", 16 + 256 * 2 );
    if (p != 18) FAIL("mprotect kept %d\n", p);
    if (passed( "D 26C Add image SMC interval: 70CF580000 - 70CF590000\n", 5000 ) != 5000) FAIL("image interval dropped\n");
    if (passed( "D 26C Load module dwmapi.dll (dwmapi.dll-760d0268): 70CF580000\n", 100 ) != 100) FAIL("other FEX line dropped\n");
    if (passed( "0450:fixme:module:LdrLockLoaderLock flags 1 not supported\n", 100 ) != 100) FAIL("wine line dropped\n");
    if (passed( "X 1D8 Handled inline self-modifying code: pc: 1\n", 100 ) != 100) FAIL("unknown level dropped\n");
    if (passed( "D Handled inline self-modifying code: pc: 1\n", 100 ) != 100) FAIL("no tid dropped\n");
    if (passed( "Dx1D8 Handled inline self-modifying code: pc: 1\n", 100 ) != 100) FAIL("bad separator dropped\n");
    if (passed( "D 1D8 Handled inline", 100 ) != 100) FAIL("short line dropped\n");
    if (madeira_fex_log_drop( "D 1D8 Handled inline self-modifying code: ", 20 )) FAIL("read past len\n");
    if (passed( "", 10 ) != 10) FAIL("empty dropped\n");
    printf("PASS: FEX per-event lines: first lines kept, then 1 in N with a [fex-log] count; everything else passes\n");
    return 0;
}
'''

import os
with tempfile.TemporaryDirectory() as tmp:
    src = Path(tmp) / 'logvol.c'
    exe = Path(tmp) / 'logvol'
    src.write_text(harness)
    subprocess.run(['cc', '-std=gnu11', '-O1', '-g', '-Wall', '-Wno-unused-function',
                    '-fsanitize=address,undefined', '-fno-sanitize-recover=undefined',
                    str(src), '-o', str(exe)], check=True)
    env = dict(os.environ); env.pop('MADEIRA_FEX_LOG_ALL', None)
    for args, extra in (([], {}), (['all'], {'MADEIRA_FEX_LOG_ALL': '1'})):
        out = subprocess.run([str(exe)] + args, capture_output=True, text=True, env={**env, **extra})
        print(out.stdout, end='')
        if out.returncode:
            raise SystemExit('FAIL: ' + out.stderr)
print('ALL PASS: check-log-volume')
