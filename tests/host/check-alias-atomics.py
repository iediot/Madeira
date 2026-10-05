#!/usr/bin/env python3
"""ml2202: execute production alias atomics and the Mach PC step under sanitizers.

Operation/width/order, register, refusal and concurrent-RMW test ideas ported
from llucasandersen/Madeira's check-mach-lse-alias.py.
"""
from pathlib import Path
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / "build/ntdll-unix/signal_arm64_ios.c").read_text()
helpers = source[source.index("/* ml2200: Darwin keeps FP/LR"):source.index("/* ml938/ml939:")]
case4 = source[source.index("/* 4. Emulate stores to JIT-pool RX aliases"):]
assert "if (ios_mach_emulate_store( insn, fault_addr, &state" in case4
assert "(insn & 0xff20fc00u) == 0xf8208000u" in case4
assert "ios_mono_bridge_capture(" in case4
step = case4[case4.index("__darwin_arm_thread_state64_set_pc_fptr(state,"):]
step = step[:step.index(";") + 1]
code = r'''
#include <assert.h>
#include <signal.h>
#include <sys/ucontext.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <sys/mman.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <mach/arm/thread_status.h>
static uintptr_t guest, host, host2, smc_page;
static size_t alias_bytes;
uintptr_t ios_jit_anon_alias_lookup(uintptr_t a)
{
    if (host2 && a >= guest + vm_page_size && a - guest - vm_page_size < vm_page_size)
        return host2 + a - guest - vm_page_size;
    return a >= guest && a - guest < alias_bytes ? host + a - guest : 0;
}
int ios_wow_store_needs_guest_fault(unsigned long long a)
{
    return smc_page && a >= smc_page && a - smc_page < 4096;
}
'''
bus = source[source.index("        uint32_t alias_insn = 0;"):]
bus = bus[:bus.index("        if (!is_exec_fault && !alias_atomic")]
assert "if (!handled)" in case4[:case4.index("uint64_t fault_pc")]
assert "ios_alias_atomic_insn( insn ) &&" in source[:source.index("/* 4. Emulate stores")]
code += helpers
code += r'''
#define PC_sig(ctx) ((ctx)->uc_mcontext->__ss.__pc)
static unsigned signal_fixed, signal_noted;
static int ios_fault_read_insn(uint64_t pc, uint32_t *insn)
{
    memcpy(insn, (void *)(uintptr_t)pc, 4); return 1;
}
static void ios_fixup_x18_for_return(ucontext_t *ctx) { (void)ctx; ++signal_fixed; }
void ios_jit_anon_alias_note_write(unsigned long long a) { (void)a; ++signal_noted; }
static void signal_atomic(ucontext_t *bus_ctx, uintptr_t fault, int is_exec_fault)
{
    uintptr_t rx = 0, rw = 0; size_t pool_sz = 0;
    void *pc = (void *)(uintptr_t)PC_sig(bus_ctx);
'''
code += bus
code += "}\n"
code += r'''
static int emulate(uint32_t insn, uintptr_t fault_addr, arm_thread_state64_t *saved,
                    uintptr_t rx, uintptr_t rw, size_t sz)
{
    arm_thread_state64_t state = *saved;
    uintptr_t fault_pc = state.__pc;
    if (!ios_mach_emulate_store(insn, fault_addr, &state, NULL, 0, rx, rw, sz)) return 0;
'''
code += step
code += r'''
    *saved = state;
    return 1;
}
static uint32_t lse(unsigned size, unsigned op, unsigned flags, unsigned rs, unsigned rt, unsigned rn)
{
    return 0x38200000u | (size << 30) | (flags << 22) | (rs << 16) | (op << 12) | (rn << 5) | rt;
}
static uint64_t oracle(unsigned op, unsigned bytes, uint64_t a, uint64_t b)
{
    uint64_t mask = bytes == 8 ? UINT64_MAX : (1ULL << (8 * bytes)) - 1;
    a &= mask; b &= mask;
    int64_t sa = bytes == 8 ? (int64_t)a : (int64_t)(a << (64 - 8 * bytes)) >> (64 - 8 * bytes);
    int64_t sb = bytes == 8 ? (int64_t)b : (int64_t)(b << (64 - 8 * bytes)) >> (64 - 8 * bytes);
    switch (op)
    {
    case 0: return (a + b) & mask; case 1: return a & ~b; case 2: return a ^ b; case 3: return a | b;
    case 4: return sa > sb ? a : b; case 5: return sa < sb ? a : b;
    case 6: return a > b ? a : b; case 7: return a < b ? a : b; default: return b;
    }
}
static uint64_t read_width(const void *p, unsigned bytes)
{
    uint64_t value = 0;
    memcpy(&value, p, bytes);
    return value;
}
static unsigned char *mem, *alias;
static arm_thread_state64_t fresh(void)
{
    arm_thread_state64_t s = {0};
    s.__pc = 0x152039e60;
    s.__sp = guest;
    s.__x[26] = guest;
    s.__x[10] = 0x8877665544332211ULL;
    s.__x[11] = UINT64_MAX;
    s.__cpsr = 0x1234;
    return s;
}
static void refuse(uint32_t insn, uintptr_t fault, arm_thread_state64_t s,
                    uintptr_t rx, uintptr_t rw, size_t sz)
{
    arm_thread_state64_t before = s;
    unsigned char snapshot[32]; memcpy(snapshot, alias, sizeof(snapshot));
    assert(!emulate(insn, fault, &s, rx, rw, sz));
    assert(!memcmp(&s, &before, sizeof(s)) && !memcmp(snapshot, alias, sizeof(snapshot)));
}
/* ml2202: split guest pages deliberately have noncontiguous RW aliases. */
static unsigned char *at(size_t off)
{
    return off < vm_page_size ? alias + off : (unsigned char *)host2 + off - vm_page_size;
}
static void put(size_t off, uint64_t value, unsigned bytes)
{
    for (unsigned i = 0; i < bytes; ++i) *at(off + i) = (value >> (8 * i)) & 255;
}
static uint64_t get(size_t off, unsigned bytes)
{
    uint64_t value = 0;
    for (unsigned i = 0; i < bytes; ++i) value |= (uint64_t)*at(off + i) << (8 * i);
    return value;
}
static uint64_t returned_sum;
static size_t peer_offset;
static unsigned peer_size = 3;
static void *add_peer(void *unused)
{
    (void)unused;
    uint64_t sum = 0;
    for (unsigned i = 0; i < 20000; ++i)
    {
        arm_thread_state64_t s = fresh(); s.__x[6] = 1; s.__sp += peer_offset;
        assert(emulate(lse(peer_size, 0, 3, 6, 0, 31), guest + peer_offset, &s, 0, 0, 0));
        assert(s.__pc == 0x152039e64 && s.__x[6] == 1);
        sum += s.__x[0];
    }
    __atomic_fetch_add(&returned_sum, sum, __ATOMIC_RELAXED);
    return NULL;
}
int main(void)
{
    mem = mmap(NULL, vm_page_size * 2, PROT_READ, MAP_PRIVATE | MAP_ANON, -1, 0);
    alias = mmap(NULL, vm_page_size * 3, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
    assert(mem != MAP_FAILED && alias != MAP_FAILED);
    guest = (uintptr_t)mem; host = (uintptr_t)alias; alias_bytes = vm_page_size;
    host2 = host + 2 * vm_page_size;
    arm_thread_state64_t s = fresh();
    /* ml2202: the two device address suffixes select CAS128 and split-lock.
     * 0xb8e48304 is SWPAL W4,W4,[X24], including the overlapping source/result. */
    const size_t device_offsets[] = {0x95, 0x20f, 1, 7, vm_page_size - 1};
    for (unsigned i = 0; i < 5; ++i)
    {
        size_t off = device_offsets[i];
        s = fresh(); s.__x[24] = guest + off; s.__x[4] = 3;
        put(off, 7, 4); *at(off - 1) = *at(off + 4) = 0x5a;
        assert(emulate(0xb8e48304, guest + off + (i == 4), &s, 0, 0, 0));
        assert(get(off, 4) == 3 && s.__x[4] == 7 && s.__pc == 0x152039e64);
        assert(*at(off - 1) == 0x5a && *at(off + 4) == 0x5a);
    }
    /* ml2202: execute bus_handler's admission/recovery with a READ syndrome. */
    uint32_t device_insn = 0xb8e48304;
    struct __darwin_mcontext64 mc = {0}; ucontext_t ctx = {0}; ctx.uc_mcontext = &mc;
    mc.__ss = fresh(); mc.__ss.__pc = (uintptr_t)&device_insn;
    mc.__ss.__x[24] = guest + 0x95; mc.__ss.__x[4] = 3;
    mc.__es.__esr = 0x9200000f; put(0x95, 7, 4);
    signal_atomic(&ctx, guest + 0x95, 0);
    assert(get(0x95, 4) == 3 && mc.__ss.__x[4] == 7);
    assert(mc.__ss.__pc == (uintptr_t)&device_insn + 4 && signal_fixed == 1 && signal_noted == 1);
    mc.__ss.__pc = (uintptr_t)&device_insn;
    signal_atomic(&ctx, guest + 0x95, 1);
    assert(mc.__ss.__pc == (uintptr_t)&device_insn && signal_fixed == 1);
    s = fresh();
    assert(emulate(0x880bff4a, guest, &s, 0, 0, 0));
    assert(read_width(alias, 4) == 0x44332211 && s.__x[11] == 0 && s.__pc == 0x152039e64);
    assert(s.__x[10] == 0x8877665544332211ULL && s.__cpsr == 0x1234 && read_width(mem, 8) == 0);
    for (unsigned size = 0; size < 4; ++size) for (unsigned release = 0; release < 2; ++release)
    {
        unsigned bytes = 1u << size;
        uint32_t insn = 0x080b7f4a | (size << 30) | (release << 15);
        s = fresh(); memset(alias, 0x5a, 32);
        assert(emulate(insn, guest, &s, 0, 0, 0));
        assert(read_width(alias, bytes) == read_width(&s.__x[10], bytes));
        assert(s.__x[11] == 0 && s.__pc == 0x152039e64 && alias[bytes] == 0x5a);
    }
    for (unsigned size = 0; size < 2; ++size) for (unsigned release = 0; release < 2; ++release)
    {
        unsigned width = 4u << size;
        uint32_t insn = 0x882b334a | (size << 30) | (release << 15); /* STXP W11,W10,W12,[X26] */
        s = fresh(); s.__x[12] = 0xffeeddccbbaa0099ULL; memset(alias, 0x5a, 32);
        assert(emulate(insn, guest, &s, 0, 0, 0));
        assert(read_width(alias, width) == read_width(&s.__x[10], width));
        assert(read_width(alias + width, width) == read_width(&s.__x[12], width));
        assert(s.__x[11] == 0 && s.__pc == 0x152039e64 && alias[width * 2] == 0x5a);
    }
    /* ml2201: all status/source fields include FP/LR/ZR; SP stays a base. */
    for (unsigned rs = 0; rs < 32; ++rs) for (unsigned rt = 0; rt < 32; ++rt)
    {
        s = fresh(); ios_store_set_gpr(&s, rs, UINT64_MAX); ios_store_set_gpr(&s, rt, 0x1234);
        uint32_t insn = 0xc8007c00 | (rs << 16) | (31 << 5) | rt;
        assert(emulate(insn, guest, &s, 0, 0, 0));
        assert(read_width(alias, 8) == (rt == 31 ? 0 : 0x1234));
        assert(ios_store_gpr(&s, rs) == 0 && s.__sp == guest);
    }
    const uint64_t values[] = {0, 1, UINT64_MAX, 0x80, 0x8000, 0x80000000ULL,
                               0x8000000000000000ULL, 0x123456789abcdef0ULL};
    const size_t offsets[] = {0, 1, 5, 7, 15, vm_page_size - 1};
    for (unsigned oi = 0; oi < 6; ++oi)
    for (unsigned size = 0; size < 4; ++size) for (unsigned op = 0; op <= 8; ++op)
    for (unsigned flags = 0; flags < 4; ++flags) for (unsigned ai = 0; ai < 8; ++ai)
    for (unsigned bi = 0; bi < 8; ++bi)
    {
        unsigned bytes = 1u << size;
        size_t off = offsets[oi];
        s = fresh(); s.__sp += off; s.__x[6] = values[bi]; put(off, values[ai], bytes);
        *at(off + bytes) = 0x5a;
        assert(emulate(lse(size, op, flags, 6, 0, 31), guest + off, &s, 0, 0, 0));
        assert(s.__x[0] == read_width(&values[ai], bytes));
        assert(get(off, bytes) == oracle(op, bytes, values[ai], values[bi]));
        assert(s.__pc == 0x152039e64 && s.__x[6] == values[bi] && *at(off + bytes) == 0x5a);
    }
    for (unsigned rs = 0; rs < 32; ++rs) for (unsigned rt = 0; rt < 32; ++rt)
    {
        s = fresh(); ios_store_set_gpr(&s, rs, 3); uint32_t word = 7; memcpy(alias, &word, 4);
        assert(emulate(lse(2, 0, 3, rs, rt, 31), guest, &s, guest, host, 8));
        assert(read_width(alias, 4) == (rs == 31 ? 7 : 10));
        assert(ios_store_gpr(&s, rt) == (rt == 31 ? 0 : 7) && s.__sp == guest);
    }
    /* ml2202: CAS/CASP success and failure at every path and alignment. */
    for (unsigned oi = 0; oi < 6; ++oi)
    for (unsigned size = 0; size < 4; ++size) for (unsigned flags = 0; flags < 4; ++flags)
    for (unsigned match = 0; match < 2; ++match)
    {
        unsigned bytes = 1u << size;
        uint32_t insn = 0x08a07c00 | (size << 30) | ((flags & 1) << 22) |
                        ((flags >> 1) << 15) | (29 << 16) | (31 << 5) | 30;
        size_t off = offsets[oi];
        s = fresh(); s.__sp += off; s.__fp = match ? 7 : 6; s.__lr = 3;
        put(off, 7, bytes); *at(off + bytes) = 0x5a;
        assert(emulate(insn, guest + off, &s, 0, 0, 0));
        assert(get(off, bytes) == (match ? 3 : 7));
        assert(s.__fp == 7 && s.__lr == 3 && s.__pc == 0x152039e64 && *at(off + bytes) == 0x5a);
    }
    for (unsigned oi = 0; oi < 6; ++oi)
    for (unsigned size = 0; size < 2; ++size) for (unsigned flags = 0; flags < 4; ++flags)
    for (unsigned match = 0; match < 2; ++match)
    {
        unsigned width = 4u << size;
        uint32_t insn = 0x08207c00 | (size << 30) | ((flags & 1) << 22) |
                        ((flags >> 1) << 15) | (2 << 16) | (31 << 5) | 4;
        size_t off = offsets[oi];
        s = fresh(); s.__sp += off; s.__x[2] = 7; s.__x[3] = match ? 9 : 8; s.__x[4] = 3; s.__x[5] = 4;
        put(off, 7, width); put(off + width, 9, width); *at(off + width * 2) = 0x5a;
        assert(emulate(insn, guest + off, &s, 0, 0, 0));
        assert(get(off, width) == (match ? 3 : 7));
        assert(get(off + width, width) == (match ? 4 : 9));
        assert(s.__x[2] == 7 && s.__x[3] == 9 && s.__x[4] == 3 && s.__x[5] == 4);
        assert(s.__pc == 0x152039e64 && *at(off + width * 2) == 0x5a);
    }
    const uint32_t atomics[] = {0x880bff4a, 0xc80b7f4a, 0xc82b334a, 0xb8e60020, 0xc8e2fc24, 0x4862fc24};
    for (unsigned i = 0; i < sizeof(atomics) / sizeof(*atomics); ++i)
    {
        uint32_t insn = (atomics[i] & ~(31u << 5)) | (26 << 5);
        if (i < 3)
        {
            s = fresh(); s.__x[26] = guest + 1; refuse(insn, guest + 1, s, 0, 0, 0);
            s.__x[26] = guest + vm_page_size - 1; refuse(insn, guest + vm_page_size, s, 0, 0, 0);
        }
        s = fresh(); refuse(insn, guest + 16, s, 0, 0, 0);
        smc_page = guest; refuse(insn, guest, s, 0, 0, 0); smc_page = 0;
        alias_bytes = 1; refuse(insn, guest, s, 0, 0, 0); alias_bytes = vm_page_size;
        refuse(insn, guest, s, guest, host, 1);
        ++host; refuse(insn, guest, s, 0, 0, 0); --host;
        alias_bytes = 0;
        assert(!mprotect(mem, vm_page_size, PROT_READ | PROT_WRITE));
        refuse(insn, guest, s, 0, 0, 0);
        assert(!mprotect(mem, vm_page_size, PROT_READ));
        alias_bytes = vm_page_size;
        s.__x[26] = UINTPTR_MAX - 1; refuse(insn, s.__x[26], s, 0, 0, 0);
        refuse(insn, guest, fresh(), guest, UINTPTR_MAX - 1, vm_page_size);
    }
    /* ml2201: loads, reserved atomic opcodes and odd CASP pairs must decline. */
    const uint32_t invalid[] = {0x885fff4a, 0xc87f334a, 0xb8209000, 0xd503201f, 0x4863fc24, 0x4862fc25};
    for (unsigned i = 0; i < sizeof(invalid) / sizeof(*invalid); ++i)
    {
        s = fresh(); s.__x[1] = s.__x[0] = guest;
        refuse(invalid[i], guest, s, 0, 0, 0);
    }
    /* ml2202: validate both pages before mutation, including either SMC page,
     * a missing alias, ordinary writable memory and a truncated pool alias. */
    for (unsigned failure = 0; failure < 5; ++failure)
    {
        size_t off = vm_page_size - (failure == 4 ? 3 : 1);
        s = fresh(); s.__x[24] = guest + off; s.__x[4] = 3;
        put(off, 7, 4); arm_thread_state64_t before = s;
        uintptr_t saved_host2 = host2;
        if (failure == 0) host2 = 0;
        if (failure == 1) smc_page = guest + vm_page_size - 4096;
        if (failure == 2) smc_page = guest + vm_page_size;
        if (failure == 3) { alias_bytes = 0; assert(!mprotect(mem, vm_page_size, PROT_READ | PROT_WRITE)); }
        assert(!emulate(0xb8e48304, guest + vm_page_size, &s,
                        failure == 4 ? guest : 0, host, vm_page_size - 1));
        host2 = saved_host2; alias_bytes = vm_page_size; smc_page = 0;
        assert(!mprotect(mem, vm_page_size, PROT_READ));
        assert(!memcmp(&s, &before, sizeof(s)) && get(off, 4) == 7);
    }
    /* ml2202: independent guest threads share both CAS and split-lock paths;
     * exact old-value sums also detect duplicate/lost RMWs. */
    pthread_t peers[4];
    for (unsigned oi = 0; oi < 6; ++oi)
    {
        peer_offset = offsets[oi]; peer_size = oi == 1 || oi == 2 ? 2 : 3;
        put(peer_offset, 0, 1u << peer_size); returned_sum = 0;
        for (unsigned i = 0; i < 4; ++i) assert(!pthread_create(&peers[i], NULL, add_peer, NULL));
        for (unsigned i = 0; i < 4; ++i) assert(!pthread_join(peers[i], NULL));
        assert(get(peer_offset, 1u << peer_size) == 80000 && returned_sum == 80000ULL * 79999 / 2);
    }
    puts("PASS: device SWPAL; READ-fault admission; CAS64/CAS128/split-lock/page paths; all LSE widths/orders; CAS/CASP; exclusive refusals; PC+4; 480000 concurrent increments");
}
'''
with tempfile.TemporaryDirectory(prefix="madeira-alias-atomics-") as tmp:
    c = Path(tmp) / "test.c"
    c.write_text(code)
    for sanitizer in ["address,undefined", "thread"]:
        exe = Path(tmp) / sanitizer.replace(",", "-")
        subprocess.run(["xcrun", "--sdk", "macosx", "clang", "-std=gnu11", "-O1", "-Wall", "-Wextra", "-Werror",
                        "-pthread", "-fsanitize=" + sanitizer, "-fno-omit-frame-pointer", str(c), "-o", str(exe)], check=True)
        result = subprocess.run([str(exe)], check=False, timeout=120, capture_output=True, text=True,
                                env=dict(os.environ, TSAN_OPTIONS="halt_on_error=1"))
        if result.returncode:
            print(result.stdout, result.stderr)
            result.check_returncode()
        logs = [line for line in result.stderr.splitlines() if "ml2202 misaligned atomic" in line]
        assert len(logs) == 8, result.stderr
        assert "path=cas" in logs[0] and "path=lock" in logs[1], logs
        assert "path=cas" in logs[2] and "path=lock" in logs[4], logs
        print(f"{sanitizer}: {result.stdout.strip()}")
