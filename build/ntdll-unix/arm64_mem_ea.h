/* Effective address of an ARM64 load/store, for stall diagnostics.
 *
 * A thread the kernel reports UNINTERRUPTIBLE with its PC in JIT or game code
 * is usually inside a page fault on the instruction at that PC. Decoding that
 * instruction against the sampled registers names the memory it touched, so
 * the page can be asked about (present, compressed, file/swap backed).
 * Registers and PC are separate live samples, so the result is a candidate,
 * not proof. Covers the integer/FP load/store classes FEX and compilers emit:
 * unsigned-offset, unscaled/pre/post-index, register-offset, pair, literal,
 * exclusive/acquire-release, LSE atomics and SIMD structure loads. Returns 0
 * for anything else. x[31] is SP for a base register. */
#ifndef MADEIRA_ARM64_MEM_EA_H
#define MADEIRA_ARM64_MEM_EA_H

#include <stdint.h>

static inline int64_t ios_ea_sext( uint64_t v, unsigned bits )
{
    uint64_t m = 1ull << (bits - 1);
    v &= (bits == 64) ? ~0ull : ((1ull << bits) - 1);
    return (int64_t)((v ^ m) - m);
}

static inline uint64_t ios_ea_extend( uint64_t v, unsigned option )
{
    switch (option)
    {
    case 2: return (uint32_t)v;                      /* UXTW */
    case 3: return v;                                /* LSL/UXTX */
    case 6: return (uint64_t)(int64_t)(int32_t)v;    /* SXTW */
    case 7: return v;                                /* SXTX */
    default: return v;
    }
}

/* regs[0..30] = x0..x30, sp separate. Returns 1 and sets ea and bytes for a memory op. */
static inline int ios_arm64_mem_ea( uint32_t insn, uint64_t pc, const uint64_t *regs, uint64_t sp,
                                    uint64_t *ea, unsigned *bytes )
{
    unsigned rn = (insn >> 5) & 31, size = insn >> 30, v = (insn >> 26) & 1, opc = (insn >> 22) & 3;
    uint64_t base = rn == 31 ? sp : regs[rn];
    unsigned scale = size;

    if (v && size == 0 && (opc & 2)) scale = 4;      /* 128-bit Q register */

    if ((insn & 0x3B000000) == 0x39000000)           /* unsigned 12-bit offset */
    {
        *ea = base + (((insn >> 10) & 0xfff) << scale);
        *bytes = 1u << scale;
        return 1;
    }
    if ((insn & 0x3B200000) == 0x38000000)           /* imm9: unscaled, post, unpriv, pre */
    {
        int64_t imm = ios_ea_sext( insn >> 12, 9 );
        *ea = ((insn >> 10) & 3) == 1 ? base : base + (uint64_t)imm;   /* post-index uses the old base */
        *bytes = 1u << scale;
        return 1;
    }
    if ((insn & 0x3B200C00) == 0x38200800)           /* register offset */
    {
        unsigned rm = (insn >> 16) & 31, option = (insn >> 13) & 7, s = (insn >> 12) & 1;
        uint64_t off = ios_ea_extend( rm == 31 ? 0 : regs[rm], option );
        *ea = base + (s ? off << scale : off);
        *bytes = 1u << scale;
        return 1;
    }
    if ((insn & 0x3B200C00) == 0x38200000)           /* LSE atomics: [Xn] */
    {
        *ea = base;
        *bytes = 1u << size;
        return 1;
    }
    if ((insn & 0x3A000000) == 0x28000000)           /* pair: post, offset, pre, no-allocate */
    {
        unsigned popc = insn >> 30, mode = (insn >> 23) & 3, ps;
        int64_t imm = ios_ea_sext( insn >> 15, 7 );
        if (v) ps = popc == 0 ? 2 : popc == 1 ? 3 : 4;
        else ps = popc == 2 ? 3 : 2;
        *ea = mode == 1 ? base : base + (uint64_t)(imm * (1ll << ps));
        *bytes = 2u << ps;
        return 1;
    }
    if ((insn & 0x3B000000) == 0x18000000)           /* literal */
    {
        *ea = pc + (uint64_t)(ios_ea_sext( insn >> 5, 19 ) * 4);
        *bytes = (insn >> 30) == 1 ? 8 : (insn >> 30) == 2 ? (v ? 16 : 4) : 4;
        return 1;
    }
    if ((insn & 0x3F000000) == 0x08000000)           /* exclusive / acquire / release */
    {
        *ea = base;
        *bytes = 1u << size;
        return 1;
    }
    if ((insn & 0xBF000000) == 0x0C000000)           /* SIMD LD1-4/ST1-4 (+ post-index) */
    {
        *ea = base;
        *bytes = 16;
        return 1;
    }
    return 0;
}

#endif /* MADEIRA_ARM64_MEM_EA_H */
