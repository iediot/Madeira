#!/usr/bin/env python3
"""Execute production suspension policy across sessions in one native process."""
from pathlib import Path
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
src = (root / 'build/wineserver/mach_ios.c').read_text()
start = src.index('int ios_real_suspend_enabled(void)')
brace = src.index('{', start)
end, depth = brace + 1, 1
while depth:
    depth += (src[end] == '{') - (src[end] == '}')
    end += 1
fn = src[start:end]
harness = r'''#include <stdio.h>
#include <stdlib.h>
#include <assert.h>
''' + fn + r'''
int main(void) {
    unsetenv("MADEIRA_REAL_SUSPEND"); assert(ios_real_suspend_enabled() == 0);
    setenv("MADEIRA_REAL_SUSPEND", "1", 1); assert(ios_real_suspend_enabled() == 1);
    assert(ios_real_suspend_enabled() == 1);
    setenv("MADEIRA_REAL_SUSPEND", "0", 1); assert(ios_real_suspend_enabled() == 0);
    setenv("MADEIRA_REAL_SUSPEND", "1", 1); assert(ios_real_suspend_enabled() == 1);
    unsetenv("MADEIRA_REAL_SUSPEND"); assert(ios_real_suspend_enabled() == 0);
    setenv("MADEIRA_REAL_SUSPEND", "", 1); assert(ios_real_suspend_enabled() == 0);
    puts("PASS: production suspension policy follows session environment transitions");
}
'''
with tempfile.TemporaryDirectory() as tmp:
    p = Path(tmp)
    (p / 'probe.c').write_text(harness)
    subprocess.run([os.environ.get('CC', 'clang'), '-O1', '-fsanitize=address,undefined',
                    '-fno-sanitize-recover=all', str(p / 'probe.c'), '-o', str(p / 'probe')], check=True)
    subprocess.run([str(p / 'probe')], check=True)
