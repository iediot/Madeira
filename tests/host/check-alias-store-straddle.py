#!/usr/bin/env python3
"""ml2200: execute the Mach store emulator against split aliases and real VM protections."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / "build/ntdll-unix/signal_arm64_ios.c").read_text()
helpers = source[source.index("/* ml2200: Darwin keeps FP/LR"):source.index("/* ml938/ml939:")]
harness = r'''
#include <assert.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <sys/mman.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <mach/arm/thread_status.h>
static uintptr_t anon_va[2], anon_rw[2], smc_page;
uintptr_t ios_jit_anon_alias_lookup(uintptr_t a)
{
    for (unsigned i = 0; i < 2; ++i)
        if (anon_va[i] && a >= anon_va[i] && a - anon_va[i] < vm_page_size)
            return anon_rw[i] + a - anon_va[i];
    return 0;
}
int ios_wow_store_needs_guest_fault(unsigned long long a)
{
    return smc_page && a >= smc_page && a - smc_page < 4096;
}
'''
harness += helpers
harness += r'''
static unsigned char *mem, *alias[2];
static arm_neon_state64_t neon;
static const uint64_t v0 = 0x8877665544332211ull, v1 = 0xffeeddccbbaa0099ull;
static unsigned checks;
static void reset(void)
{
    memset(mem, 0, vm_page_size * 2);
    memset(alias[0], 0, vm_page_size);
    memset(alias[1], 0, vm_page_size);
    anon_va[0] = (uintptr_t)mem;
    anon_va[1] = (uintptr_t)mem + vm_page_size;
    anon_rw[0] = (uintptr_t)alias[0];
    anon_rw[1] = (uintptr_t)alias[1];
    smc_page = 0;
}
static void setbase(arm_thread_state64_t *st, unsigned rn, uintptr_t value)
{
    if (rn == 31) st->__sp = value;
    else if (rn == 29) st->__fp = value;
    else if (rn == 30) st->__lr = value;
    else st->__x[rn] = value;
}
static void check_store(uint32_t insn, unsigned bytes, unsigned tail, int64_t offset,
                        int post, int wb, int simd, int pair, unsigned rn, int ordinary)
{
    reset();
    if (ordinary) anon_va[0] = 0;
    uintptr_t ea = (uintptr_t)mem + vm_page_size - tail;
    uintptr_t base = post ? ea : ea - offset;
    arm_thread_state64_t st = {0};
    st.__x[0] = v0; st.__x[1] = v1;
    setbase(&st, rn, base);
    insn |= rn << 5;
    assert(ios_mach_emulate_store(insn, (uintptr_t)mem + vm_page_size, &st, &neon, 1, 0, 0, 0));
    unsigned char expected[32] = {0};
    unsigned width = bytes / (pair ? 2 : 1);
    memcpy(expected, simd ? (void *)&neon.__v[0] : (void *)&v0, width);
    if (pair) memcpy(expected + width, simd ? (void *)&neon.__v[1] : (void *)&v1, width);
    unsigned char *first = ordinary ? mem : alias[0];
    assert(!memcmp(first + vm_page_size - tail, expected, tail));
    assert(!memcmp(alias[1], expected + tail, bytes - tail));
    assert(first[vm_page_size - tail - 1] == 0 && alias[1][bytes - tail] == 0);
    uint64_t after = rn == 31 ? st.__sp : ios_store_gpr(&st, rn);
    assert(after == (wb ? base + offset : base));
    ++checks;
}
int main(void)
{
    mem = mmap(NULL, vm_page_size * 2, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
    for (unsigned i = 0; i < 2; ++i)
        alias[i] = mmap(NULL, vm_page_size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
    assert(mem != MAP_FAILED && alias[0] != MAP_FAILED && alias[1] != MAP_FAILED);
    for (unsigned i = 0; i < 32; ++i) ((unsigned char *)&neon.__v[0])[i] = i + 1;
    const unsigned regs[] = {2, 29, 30, 31};
    for (unsigned mode = 0; mode < 4; ++mode)
        for (unsigned neg = 0; neg < 2; ++neg)
            for (unsigned r = 0; r < 4; ++r)
                for (unsigned ordinary = 0; ordinary < 2; ++ordinary)
                    for (unsigned tail = 1; tail < 32; ++tail)
                    {
                        int off = neg ? -32 : 32;
                        uint32_t insn = 0xac000400u | (mode << 23) | (((off / 16) & 127) << 15);
                        check_store(insn, 32, tail, off, mode == 1, mode == 1 || mode == 3,
                                    1, 1, regs[r], ordinary);
                    }
    /* ml2200: the exact device instruction faults six bytes after X1. */
    reset(); anon_va[0] = 0;
    arm_thread_state64_t st = {0};
    st.__x[1] = (uintptr_t)mem + vm_page_size - 6;
    assert(ios_mach_emulate_store(0xac810420, (uintptr_t)mem + vm_page_size, &st, &neon, 1, 0, 0, 0));
    assert(st.__x[1] == (uintptr_t)mem + vm_page_size + 26);
    assert(!memcmp(mem + vm_page_size - 6, &neon.__v[0], 6));
    assert(!memcmp(alias[1], (char *)&neon.__v[0] + 6, 26));

    for (unsigned scale = 1; scale <= 3; ++scale)
    {
        unsigned bytes = 1u << scale;
        check_store(0x39000000u | (scale << 30) | (7 << 10), bytes, 1, 7 * bytes, 0, 0, 0, 0, 2, 1);
        check_store(0x089ffc00u | (scale << 30), bytes, 1, 0, 0, 0, 0, 0, 2, 1);
        check_store(0x19000000u | (scale << 30) | (0x1fd << 12), bytes, 1, -3, 0, 0, 0, 0, 2, 1);
        for (unsigned mode = 0; mode < 4; ++mode)
            check_store(0x38000000u | (scale << 30) | (0x1fd << 12) | (mode << 10),
                        bytes, 1, -3, mode == 1, mode == 1 || mode == 3, 0, 0, 31, 1);
    }
    check_store(0x29000000u | (1 << 10) | (0x7d << 15), 8, 3, -12, 0, 0, 0, 1, 2, 1);
    check_store(0xa9000000u | (1 << 10) | (0x7d << 15), 16, 5, -24, 0, 0, 0, 1, 2, 1);
    const uint32_t simd[] = {0xbc000000, 0xfc000000, 0x3c800000};
    for (unsigned s = 0; s < 3; ++s)
    {
        unsigned bytes = 4u << s;
        for (unsigned mode = 0; mode < 4; ++mode)
            if (mode != 2)
                check_store(simd[s] | (0x1fd << 12) | (mode << 10), bytes, 1, -3,
                            mode == 1, mode == 1 || mode == 3, 1, 0, 31, 1);
        check_store(simd[s] | 0x01000000 | (7 << 10), bytes, 1, 7 * bytes, 0, 0, 1, 0, 2, 1);
    }
    /* ml2200: register extensions must use the saved offset, including signed Wm and ZR. */
    for (unsigned simd_reg = 0; simd_reg < 2; ++simd_reg)
        for (unsigned option = 2; option <= 7; ++option)
            if (option == 2 || option == 3 || option == 6 || option == 7)
                for (unsigned shifted = 0; shifted < 2; ++shifted)
                {
                    reset(); st = (arm_thread_state64_t){0};
                    uintptr_t ea = (uintptr_t)mem + vm_page_size - 1;
                    uint64_t raw = option >= 6 ? (uint64_t)-2 : 2;
                    uint64_t off = shifted ? raw << (simd_reg ? 4 : 3) : raw;
                    st.__x[2] = ea - off; st.__x[3] = raw; st.__x[0] = v0;
                    uint32_t insn = (simd_reg ? 0x3ca00800u : 0xf8200800u) |
                                    (3 << 16) | (option << 13) | (shifted << 12) | (2 << 5);
                    assert(ios_mach_emulate_store(insn, ea + 1, &st, &neon, 1, 0, 0, 0));
                    assert(alias[0][vm_page_size - 1] == (simd_reg ? 1 : 0x11));
                    assert(alias[1][0] == (simd_reg ? 2 : 0x22));
                }
    /* ml2200: a pool alias can border ordinary memory on either side. */
    for (unsigned pool_first = 0; pool_first < 2; ++pool_first)
    {
        reset(); anon_va[0] = anon_va[1] = 0;
        uintptr_t ea = (uintptr_t)mem + vm_page_size - 6;
        st = (arm_thread_state64_t){0}; st.__x[1] = ea;
        uintptr_t rx = (uintptr_t)mem + (pool_first ? 0 : vm_page_size);
        assert(ios_mach_emulate_store(0xac810420, ea + 6, &st, &neon, 1, rx, (uintptr_t)alias[0], vm_page_size));
        assert(!memcmp((pool_first ? alias[0] : mem) + vm_page_size - 6, &neon.__v[0], 6));
        assert(!memcmp(pool_first ? mem + vm_page_size : alias[0], (char *)&neon.__v[0] + 6, 26));
    }
    /* ml2200: rejection must leave both the first segment and writeback untouched. */
    for (unsigned failure = 0; failure < 5; ++failure)
    {
        reset();
        uintptr_t ea = (uintptr_t)mem + vm_page_size - 6;
        st = (arm_thread_state64_t){0}; st.__x[1] = ea;
        if (failure == 0)
        {
            anon_va[1] = 0;
            assert(!mprotect(mem + vm_page_size, vm_page_size, PROT_READ));
        }
        if (failure == 1) smc_page = ea & ~(uintptr_t)4095;
        uintptr_t fault = failure == 2 ? ea + 32 : failure == 3 ? ea - 1 : ea + 6;
        assert(!ios_mach_emulate_store(0xac810420, fault, &st, &neon, failure != 4, 0, 0, 0));
        assert(st.__x[1] == ea && alias[0][vm_page_size - 6] == 0 && alias[1][0] == 0);
        assert(!mprotect(mem + vm_page_size, vm_page_size, PROT_READ | PROT_WRITE));
    }
    reset(); st = (arm_thread_state64_t){0};
    st.__sp = (uintptr_t)mem + vm_page_size - 1;
    assert(ios_mach_emulate_store(0xf83f6bff, st.__sp + 1, &st, &neon, 1, 0, 0, 0)); /* STR XZR,[SP,XZR] */
    assert(alias[0][vm_page_size - 1] == 0 && alias[1][0] == 0);
    printf("PASS: %u split stores, exact device regression, register offsets, pool boundaries and refusal paths\n", checks);
    return 0;
}
'''
with tempfile.TemporaryDirectory(prefix="madeira-alias-store-") as tmp:
    c = Path(tmp) / "test.c"
    exe = Path(tmp) / "test"
    c.write_text(harness)
    subprocess.run(["xcrun", "--sdk", "macosx", "clang", "-std=gnu11", "-Wall", "-Wextra", "-Werror",
                    "-fsanitize=address,undefined", str(c), "-o", str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
