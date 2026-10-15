#!/usr/bin/env python3
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
server = (root / 'build/ntdll-unix/server_ios.c').read_text()
virtual = (root / 'build/ntdll-unix/virtual_ios.c').read_text()
assert 'if (sample && !ios_lean_diag)' in server
assert 'if (!ios_lean_diag) { ++gen; ios_guest_rip_profile( gen ); }' in server
assert 'if (stuck_check) ios_stuck_store_check();' in server
assert virtual.count('cycle % ios_warmer_sweep_cycles()') == 2
harness = r'''
#include <assert.h>
#include <stdio.h>
#include "runtime_diagnostics_policy.h"
int main(void) {
    assert(!ios_diagnostics_lean(NULL)); assert(!ios_diagnostics_lean("0"));
    assert(ios_diagnostics_lean("1"));
    assert(ios_diagnostics_sweep_cycles(NULL, NULL) == 30);
    assert(ios_diagnostics_sweep_cycles(NULL, "1") == 300);
    assert(ios_diagnostics_sweep_cycles("5", "1") == 5);
    assert(ios_diagnostics_sweep_cycles("100000", NULL) == 100000);
    const char *bad[] = { "", "0", "-1", "100001", "garbage", "3oops", "999999999999999999999999" };
    for (unsigned i = 0; i < sizeof(bad)/sizeof(bad[0]); ++i)
        assert(ios_diagnostics_sweep_cycles(bad[i], NULL) == 30);
    puts("PASS: minute default, lean cadence, explicit override, invalid/overflow values, correctness checks retained");
}
'''
with tempfile.TemporaryDirectory(prefix='madeira-diag-policy-') as directory:
    path = Path(directory); c = path / 'policy.c'; c.write_text(harness)
    exe = path / 'policy'
    subprocess.run(['xcrun', 'clang', '-std=c11', '-fsanitize=address,undefined',
                    '-I', str(root / 'build/ntdll-unix'), str(c), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
