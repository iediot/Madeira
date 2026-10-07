#!/usr/bin/env python3
"""Furniture-ceiling relax rule for a caller's limit_low (virtual_ios.c ios_limit_low_is_free); no Wine runs.

Compiles the production predicate and checks that the thread stacks' limit_4g (0x100000000) and the
lowest iOS placement leave the ceiling relaxable, while a limit_low above any possible placement
(a real caller constraint) does not. Regression: comparing against address_space_start, which a
native win64 process lowers to 0x10000, made every thread stack non-relaxable, and they failed hard
once the band was full.
"""
from pathlib import Path
import subprocess, tempfile

root = Path(__file__).resolve().parents[2]
src = (root / "build/ntdll-unix/virtual_ios.c").read_text()
a = src.index("static const ULONG_PTR ios_lowest_placement")
b = src.index("#ifdef _WIN64", a)
rule = src[a:b]
harness = r"""
#include <stdio.h>
#include <stdint.h>
typedef uintptr_t ULONG_PTR;
""" + rule + r"""
int main(void)
{
    int failed = 0;
#define CHECK(c, m) do { int ok_ = (c); printf("%s: %s\n", ok_ ? "PASS" : "FAIL", m); failed += !ok_; } while (0)
    CHECK(ios_limit_low_is_free(0), "no limit_low is relaxable");
    CHECK(ios_limit_low_is_free(0x100000000ULL), "the thread stacks' limit_4g is relaxable");
    CHECK(ios_limit_low_is_free(0x100010000ULL), "a limit_low at the lowest placement is relaxable");
    CHECK(!ios_limit_low_is_free(0x100020000ULL), "a limit_low above the lowest placement is a real constraint");
    CHECK(!ios_limit_low_is_free(0x7400000000ULL), "a high limit_low is a real constraint");
    return failed != 0;
}
"""
with tempfile.TemporaryDirectory() as d:
    c = Path(d) / "relax.c"; exe = Path(d) / "relax"
    c.write_text(harness)
    subprocess.run(["cc", "-std=c11", "-Wall", "-Werror", "-o", str(exe), str(c)], check=True)
    raise SystemExit(subprocess.run([str(exe)]).returncode)
