#!/usr/bin/env python3
"""Vector registers in a cross-thread context (build/wineserver/mach_ios.c); no Wine runs.

The server's Mach capture read only ARM_THREAD_STATE64, so every cross-thread GetThreadContext
returned zeroed XMM registers. Persona 5 Royal reads another thread's floating-point context into
a CONTEXT inside its image and jumps to the XMM13 slot, and executed zeros. Checks that the syscall
frame offsets the capture reads match struct syscall_frame, that the syscall dispatcher saves every
vector register there, that the capture is gated by MADEIRA_CTX_FP and runs while the thread is
halted, that only vector registers known to be the guest's are reported (live in translated x64
code, saved by a syscall -- not a Unix call -- frame, nothing elsewhere), and the switch itself: unset or 1 captures, 0 restores the zeroed registers, and the
madeira.cfg line wins over the environment.
"""
from pathlib import Path
import re, subprocess, tempfile

root = Path(__file__).resolve().parents[2]
src = (root / "build/wineserver/mach_ios.c").read_text()
sig = (root / "build/ntdll-unix/signal_arm64_ios.c").read_text()

def function(source, start):
    a = source.index(start); b = source.index("{", a); depth = 1; c = b + 1
    while depth:
        depth += (source[c] == "{") - (source[c] == "}"); c += 1
    return source[a:c]

offs = dict(re.findall(r"#define (IOS_SCF_\w+)\s+(0x[0-9a-f]+)\b", src))
frame = sig[sig.index("struct syscall_frame\n{"):]
frame = frame[:frame.index("};") + 2]
harness = r"""
#include <stddef.h>
#include <stdint.h>
typedef uint64_t ULONG64; typedef uint32_t ULONG;
typedef struct { uint64_t Low; int64_t High; } NEON128;
""" + frame + "\n"
for name, field in (("FP", "fp"), ("LR", "lr"), ("SP", "sp"), ("PC", "pc"), ("CPSR", "cpsr"),
                    ("FPCR", "fpcr"), ("FPSR", "fpsr"), ("V", "v")):
    harness += f"_Static_assert(offsetof(struct syscall_frame, {field}) == {offs['IOS_SCF_' + name]}, \"{name}\");\n"
harness += f"_Static_assert(sizeof(struct syscall_frame) == {offs['IOS_SCF_SIZE']}, \"SIZE\");\n"
harness += "int main(void) { return 0; }\n"
with tempfile.TemporaryDirectory() as t:
    c = Path(t) / "layout.c"; c.write_text(harness)
    subprocess.run(["cc", "-std=gnu11", "-Wall", str(c), "-o", str(Path(t) / "layout")], check=True)
print("PASS: the IOS_SCF offsets match struct syscall_frame, including fpcr, fpsr and v[32]")

dispatcher = sig[sig.index("__ASM_GLOBAL_FUNC( __wine_syscall_dispatcher,"):]
dispatcher = dispatcher[:dispatcher.index("__ASM_GLOBAL_FUNC(", 10)]
for q in range(0, 32, 2):
    assert re.search(rf'"stp q{q},\s+q{q + 1},\s+\[x10, #0x{0x130 + q * 16:x}\]', dispatcher), q
assert '"str w9, [x10, #0x128]' in dispatcher and '"str w9, [x10, #0x12c]' in dispatcher
print("PASS: the syscall dispatcher saves q0-q31, fpcr and fpsr where the capture reads them")

fill = function(src, "int ios_fill_thread_context(")
neon = fill.index("thread_get_state( port, ARM_NEON_STATE64")
assert fill.index("thread_suspend( port )") < fill.index("thread_get_state( port, ARM_THREAD_STATE64") < neon
assert neon < fill.index("f + IOS_SCF_V") < fill.rindex("thread_resume( port );")
assert "if (have_native && ios_ctx_fp_enabled())" in fill[:neon]
assert "ios_ctx_fp_source( thread, arm.__pc, arm.__sp, &fp_frame )" in fill[:neon]
assert "source == IOS_CTX_FP_LIVE" in fill[:neon]
assert "ios_scf_is_syscall( f )" in fill[neon:fill.index("f + IOS_SCF_V")]
assert fill.count("native->flags |= SERVER_CTX_FLOATING_POINT;") == 2
print("PASS: the vector capture is gated by MADEIRA_CTX_FP, runs while the thread is halted, and "
      "reports live registers only for translated code and frame registers only for a syscall frame")

# The rule itself, compiled: which source vouches for the guest's vector registers.
rule = "\n".join(function(src, n) for n in ("static int ios_scf_is_syscall(", "static int ios_pc_is_x64_jit(",
                                         "static int ios_ctx_fp_source("))
enum = re.search(r"enum \{ IOS_CTX_FP_NONE[^;]*;", src).group(0)
defs = "".join(f"#define {k} {v}\n" for k, v in offs.items())
defs += "".join(f"#define {k} {v}\n" for k, v in
                re.findall(r"#define (IOS_TEB_(?:SYSCALL_FRAME|KERNEL_STACK)_OFF)\s+(0x[0-9a-fA-F]+)", src))
harness = r"""
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <stdio.h>
struct thread { void *teb; };
void *ios_jit_rx_base_global; size_t ios_jit_pool_size_global;
static uintptr_t img_lo, img_hi, low_lo, low_hi;
int ios_jit_pool_image_pc( uintptr_t pc, uintptr_t *pe ) { (void)pe; return pc >= img_lo && pc < img_hi; }
int ios_jit_low_contains( uintptr_t a ) { return a >= low_lo && a < low_hi; }
static int ios_safe_read( uint64_t a, void *out, size_t n ) { memcpy( out, (void *)(uintptr_t)a, n ); return 1; }
""" + defs + enum + "\n" + rule + r"""
#define CHECK(x) do { if (!(x)) { printf("failed line %d: %s\n", __LINE__, #x); return 1; } } while (0)
static unsigned char teb[0x2000], f[IOS_SCF_SIZE];
int main(void)
{
    struct thread t = { teb };
    uint64_t fr = 0, kstack = 0x1000, frame = 0x8000;
    ios_jit_rx_base_global = (void *)0x100000; ios_jit_pool_size_global = 0x100000;
    img_lo = 0x180000; img_hi = 0x190000; low_lo = 0x50000; low_hi = 0x60000;
    /* not in a syscall: live registers only in translated code */
    CHECK(ios_ctx_fp_source( &t, 0x120000, 0x9000, &fr ) == IOS_CTX_FP_LIVE);
    CHECK(ios_ctx_fp_source( &t, 0x058000, 0x9000, &fr ) == IOS_CTX_FP_LIVE);
    CHECK(ios_ctx_fp_source( &t, 0x184000, 0x9000, &fr ) == IOS_CTX_FP_NONE);   /* image copy (EC, FEX module) */
    CHECK(ios_ctx_fp_source( &t, 0x300000, 0x9000, &fr ) == IOS_CTX_FP_NONE);   /* host library */
    CHECK(ios_ctx_fp_source( &t, 0x0ff000, 0x9000, &fr ) == IOS_CTX_FP_NONE);
    /* inside a syscall frame: the frame, whatever the pc */
    memcpy( teb + IOS_TEB_SYSCALL_FRAME_OFF, &frame, 8 ); memcpy( teb + IOS_TEB_KERNEL_STACK_OFF, &kstack, 8 );
    CHECK(ios_ctx_fp_source( &t, 0x120000, 0x4000, &fr ) == IOS_CTX_FP_SYSCALL_FRAME && fr == frame);
    CHECK(ios_ctx_fp_source( &t, 0x120000, 0x9000, &fr ) == IOS_CTX_FP_LIVE);
    /* syscall dispatcher frame: lr = caller's x9, pc = x30; Unix call frame: both x30 */
    *(uint64_t *)(f + IOS_SCF_LR) = 0x1111; *(uint64_t *)(f + IOS_SCF_PC) = 0x2222;
    CHECK(ios_scf_is_syscall( f ));
    *(uint64_t *)(f + IOS_SCF_LR) = 0x2222;
    CHECK(!ios_scf_is_syscall( f ));
    *(uint64_t *)(f + IOS_SCF_PC) = 0; *(uint64_t *)(f + IOS_SCF_LR) = 0x1;
    CHECK(!ios_scf_is_syscall( f ));
    return 0;
}
"""
with tempfile.TemporaryDirectory() as t:
    c = Path(t) / "rule.c"; c.write_text(harness); exe = Path(t) / "rule"
    subprocess.run(["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function",
                    str(c), "-o", str(exe)], check=True)
    run = subprocess.run([str(exe)], capture_output=True, text=True)
    print(run.stdout, end=""); run.check_returncode()
print("PASS: live vector registers only in translated x64 code, frame registers only from a syscall "
      "frame (not a Unix call), nothing elsewhere")

# The Unix call dispatcher really stores x30 as both lr and pc, which is what tells the frames apart.
unix = sig[sig.index("__ASM_GLOBAL_FUNC( __wine_unix_call_dispatcher,"):]
unix = unix[:unix.index("blr x16")]
assert '"stp x30, x9, [x10, #0xf0]' in unix and '"stp x30, x9, [x10, #0x100]' in unix
assert '"stp x9, x19, [x10, #0xf0]' in dispatcher and '"stp x30, x9, [x10, #0x100]' in dispatcher
assert "stp q0," not in unix
print("PASS: the Unix call dispatcher stores lr == pc and no q0-q7; the syscall dispatcher stores lr = x9")

switch = function(src, "static int ios_ctx_fp_enabled(void)")
harness = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static const char *cfg_value;
static int madeira_cfg_get(const char *key, char *buf, size_t cap)
{
    if (strcmp(key, "env.MADEIRA_CTX_FP") || !cfg_value) return 0;
    snprintf(buf, cap, "%s", cfg_value);
    return 1;
}
/* the server's clock: the answer is kept for a second, so the checks step it */
typedef long long timeout_t;
#define TICKS_PER_SEC 10000000
static timeout_t monotonic_time;
""" + switch + r"""
#define CHECK(x) do { if (!(x)) { printf("failed line %d: %s\n", __LINE__, #x); return 1; } } while (0)
#define LATER (monotonic_time += TICKS_PER_SEC)
int main(void)
{
    unsetenv("MADEIRA_CTX_FP");
    CHECK(ios_ctx_fp_enabled());
    LATER; setenv("MADEIRA_CTX_FP", "1", 1); CHECK(ios_ctx_fp_enabled());
    LATER; setenv("MADEIRA_CTX_FP", "0", 1); CHECK(!ios_ctx_fp_enabled());
    /* within the second the kept answer stands: no file read for a program that captures often */
    setenv("MADEIRA_CTX_FP", "1", 1); CHECK(!ios_ctx_fp_enabled());
    LATER; CHECK(ios_ctx_fp_enabled());
    LATER; cfg_value = "0"; CHECK(!ios_ctx_fp_enabled());
    LATER; cfg_value = "1"; setenv("MADEIRA_CTX_FP", "0", 1); CHECK(ios_ctx_fp_enabled());
    LATER; unsetenv("MADEIRA_CTX_FP"); cfg_value = "0"; CHECK(!ios_ctx_fp_enabled());
    return 0;
}
"""
with tempfile.TemporaryDirectory() as t:
    c = Path(t) / "switch.c"; c.write_text(harness)
    exe = Path(t) / "switch"
    subprocess.run(["cc", "-std=c11", "-D_POSIX_C_SOURCE=200809L", "-Wall", "-Wextra", "-Werror",
                    str(c), "-o", str(exe)], check=True)
    run = subprocess.run([str(exe)], capture_output=True, text=True)
    print(run.stdout, end="")
    run.check_returncode()
print("PASS: MADEIRA_CTX_FP unset or 1 captures, 0 restores zeroed registers, madeira.cfg wins over the environment")
