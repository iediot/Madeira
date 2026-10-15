#!/usr/bin/env python3
"""Reject lock-taking work in native sampler suspension windows."""
from pathlib import Path
import re

root = Path(__file__).resolve().parents[2]
src = (root / 'build/ntdll-unix/server_ios.c').read_text()
code = re.sub(r'/\*.*?\*/', lambda m: '\n' * m[0].count('\n'), src, flags=re.S)
code = re.sub(r'//[^\n]*', '', code)
lines = code.splitlines()
forbidden = re.compile(r'\b(?:pthread_from_mach_thread_np|pthread_getname_np|pthread_kill|'
                       r'wine_log_write|wine_ui_log|dprintf|fprintf|printf|malloc|calloc|'
                       r'realloc|free|dladdr|ios_ts_sym|ios_ts_map_for_teb|'
                       r'pthread_mutex_lock|os_unfair_lock_lock)\s*\(')
windows = 0
for i, line in enumerate(lines):
    if not re.search(r'\bthread_suspend\s*\(', line):
        continue
    end = next((j for j in range(i + 1, min(i + 200, len(lines)))
                if re.match(r'^\s*thread_resume\s*\(.*\);\s*$', lines[j])), None)
    assert end is not None, f'unclosed suspension at {i + 1}'
    windows += 1
    skip = set()
    assigned = re.search(r'(\w+)\s*=\s*thread_suspend\s*\(', line)
    if assigned and re.match(r'\s*if\s*\(\s*' + assigned[1] +
                             r'\s*!=\s*KERN_SUCCESS\s*\)\s*\{', lines[i + 1]):
        depth = 0
        for j in range(i + 1, end):
            skip.add(j)
            depth += lines[j].count('{') - lines[j].count('}')
            if depth == 0:
                break
    for j in range(i + 1, end):
        if j not in skip:
            assert not forbidden.search(lines[j]), f'unsafe suspended work at {j + 1}: {lines[j]}'
assert windows >= 6
print(f'PASS sampler safety: {windows} suspension windows checked')
