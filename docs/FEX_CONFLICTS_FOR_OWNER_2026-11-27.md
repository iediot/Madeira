# FEX upstream conflicts for the owner, 2026-11-27

This is analysis only. Nothing in `FEX/` was edited, resolved, committed or
pushed. Every conflict below was produced in a throwaway worktree and then
discarded. FEX's rule ("AI must not be used to generate code for
contributions", `FEX/CLAUDE.md`, `FEX/AGENTS.md`) means a human resolves every
conflict here. This note only says where the conflicts are, what each side
protects, and how big each resolution is.

## State this was measured against

- Submodule `FEX` on branch `fex-upstream-perf-2026-11-26`, HEAD
  `1a66a7f4c7597f0b17584209b074d53682af69a5` ("Regenerate MadeiraCodegenId for
  the upstream perf picks"). This is the same commit as `origin/madeira`.
  The working tree was clean, with three stashes.
- `fexemu/main` (FEX-Emu/FEX) has **no merge base** with the fork. The fork's
  history begins at a root snapshot, `707f213f5` (Will Faust, 2026-05-18,
  3,541 files). Every upstream commit therefore has to come over as a
  cherry-pick.
- **Caveat on blame.** For most fork-side hunks, `git blame` gives
  `707f213f5` (the root snapshot) or `f5a5349ec` ("Preserve local Madeira FEX
  changes", 2026-10-13, a squash). A `707f213f5` attribution usually means the
  line is **older upstream code** that the fork never updated. It does not
  mean fork-specific logic. Where a hunk carries real fork behaviour, the
  section names the commit and the `ml` note that explains it.
- How the conflicts were produced: `git -C FEX worktree add --detach
  /tmp/claude-501/fexwt 1a66a7f4c`. Then, per commit, `git cherry-pick
  --no-commit <c>`; capture `git status`, `git diff` and the conflict-marker
  regions; then `cherry-pick --abort` / `--quit`, and `reset --hard` back to
  `1a66a7f4c`. Dependency chains were probed the same way (Appendix B). The
  worktree has been removed and pruned. `git -C FEX worktree list` shows only
  the main checkout. HEAD, branch, status and stash list match what was
  recorded before the work started, byte for byte.
- Conflict regions are pasted verbatim with git's default markers:
  `<<<<<<< HEAD` is the fork, `>>>>>>> <sha>` is upstream. The `--- path:N`
  line above each region is the line number in the conflicted file, added for
  orientation.

## Ranking for RDR2 on iOS

RDR2 on the device is translation-bound and runs under a CPU power cap of
about 1 W, with TSO on. "Payoff" below combines how often RDR2 is likely to
reach the changed path with how much each hit saves. None of these commits
changes how memory ordering is emitted (no atomics, barriers, or LDAPR/STLR
selection), so TSO cost is unaffected either way. No figure here was measured
on a device. The upstream figures come from the upstream commit messages.

| # | Upstream commit(s) | What | Expected RDR2 payoff | Resolution | Must land first |
|---|---|---|---|---|---|
| 1 | `638329278` + `ff887adb1` `e2f0cf41d` `0206479a0` `af8770005` (PR #5981) | Inline PCMPESTRI/PCMPESTRM/PCMPISTRI/PCMPISTRM instead of a C++ helper call | **High per hit** (upstream: 8.8x to 19.4x per instruction). The hit rate is unknown. It only matters if RDR2 or its parsers use SSE4.2 string intrinsics. The cost is almost zero. | **Mechanical.** Only 3 InstCountCI JSON files conflict, and the code applies cleanly. After that, the 4 follow-ups apply cleanly (probed). | nothing |
| 2 | `179b4423a` (PR #5925) | NtReadFile no longer holds `ThreadCreationMutex` + `CodeInvalidationMutex` across the blocking read; it invalidates after the read | **Uncertain, possibly large for load stalls.** It only helps when a read lands in a tracked RWX range. For every other read it adds work. It has a correctness hazard on write-protected code pages. | **Design decision** (3 hunks; the fork's ml411/ml412/ml612 hardening sits in the same function) | an `AppConfigName` assignment (upstream `681636cd6`, which also conflicts, or by hand) |
| 3 | `61d94ac11` (PR #5904) | Faster PMULHRSW (`rshrn`) | Small to moderate if RDR2 runs SSSE3 fixed-point code (audio, video): xmm form 11 to 4 host instructions, mm form 12 to 9 | **Mechanical.** One JSON hunk, and it is the SVE256 expectation, which the device never runs. | nothing |
| 4 | `ba800514b` (PR #6139) | Don't track reserve-only allocations in the invalidation tracker | Small. There are fewer intervals, so executable-range queries during decode and SMC handling get cheaper. | **Mechanical, small.** One ARM64EC hunk; WOW64 applies cleanly. | nothing |
| 5 | `9f7913edb` + `01b344b44` `eedb8be51` (PR #6047) | Inline 128/64 unsigned divide (Knuth/divllu) instead of a helper call | Small. The fork already has an inline path when RDX is 0. This only speeds up a true 128-bit dividend (upstream: about 4x on X2 Elite). | **Mechanical** (3 hunks: delete `LUDIV`/`LDIV`, and do **not** add `RDRANDFallback`). The follow-ups apply cleanly (probed). | **nothing functional.** The RDRAND commits are only textual neighbours (see Appendix A). |
| 6 | `19dba4a3e` (PR #5858) | Re-insert the looked-up L1 entry after an L1 resize | Tiny (one fewer L2 lookup per resize) | Mechanical, but **semantic trap**: the cleanly merged `AddL1Entry` uses the unhashed L1 index. On iOS it must use `L1Index()`. | nothing |
| 7 | `41241d750` (PR #5660) | WOW64 suspend spin waits on WFE | About zero for RDR2, which is 64-bit (ARM64EC). It is a power fix only for 32-bit processes in a suspend spin. | Mechanical, one hunk | nothing |
| 8 | `8cfbc4448` `b3fe634c8` `a164315b7` `ed6d7092a` `d85f5b439` `1e95181b3` `9d936a898` `574328294` `152314254` | Remove implicit `SRC_RAX`/`DST_RAX` operand flags | **None at runtime.** They reduce conflicts for later picks. | Mechanical but sequential: 9 table-line conflicts, plus `FLAGS_LITERAL_PATCHABLE`/`REX2` noise that must be dropped | each needs the previous one |
| 9 | `229151f87` `4b418a81a` `502a1900e` `26d8ec55f` | Syscall/ThreadRemoveCodeEntry jump to the dispatcher; L1 lookup after a syscall returns | **Zero or negative.** `229151f87` is the regression that `502a1900e` repairs. They are needed only for upstream code-buffer reuse and DiskCache. | Large; it needs upstream's syscall-IR chain | `66cad978c`, `91bbce5a9`, `1a0412969`, `05293166c`, DiskCache |
| 10 | `561c32b45` + 74 DiskCache commits | Upstream on-disk code cache | **About zero for RDR2.** RDR2 runs with the fork's code cache off (`MADEIRA_FEX_CODE_CACHE=0`) because Rockstar's anti-tamper re-encrypts code. | **Design decision**: DiskCache vs MadeiraCodeCache | ~75 commits |

Top five, in order: PCMPXSTRX, NtReadFile, PMULHRSW, reserve-only, inline divide.
Items 1, 3 and 4 together are perhaps 30 minutes of hand resolution. If the
priority is the 78 s loading screen (`docs/RDR2_LOAD_TIME_IPHONE18_2026-11-27.md`),
item 2 is the only commit here that could plausibly affect it. It should be
measured on the device before anyone adopts upstream's semantics.

## Things that apply to every item

### InstCountCI JSON files: what they are, and whether they can be regenerated

- `unittests/InstructionCountCI/**/*.json` hold expected host code for each
  x86 instruction. They are **test data only**. All three Madeira FEX builds
  pass `-DBUILD_TESTING=OFF` (`build/fex-ios/build.sh:11`,
  `build/fex-arm64ec/build.sh:20`, `build/fex-wow64/build.sh:21`), so these
  files are never built or run by Madeira and do not affect any shipped binary.
- How upstream maintains them: the CI workflow `.github/workflows/instcountci.yml`
  configures with `-DENABLE_VIXL_DISASSEMBLER=True`, builds `CodeSizeValidation
  instcountci_test_files`, runs `instcountci_tests`, then
  `instcountci_update_tests`, which runs `Scripts/UpdateInstructionCountJson.py`
  per file and rewrites `ExpectedInstructionCount`/`ExpectedArm64ASM` in place.
  `Scripts/update_instcountci.sh` wraps the same steps and **ends with
  `git add -u` + `git commit -sm "InstCountCI: Update"`**. Do not run that
  script as is if you want to control the commit.
- The command, for a Linux AArch64 host (on x86-64 Linux add
  `-DENABLE_VIXL_SIMULATOR=True`), run from a checkout where the code side of
  the pick is already resolved:

  ```sh
  cmake -S FEX -B build-icc -G Ninja -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++ \
    -DBUILD_TESTING=True -DENABLE_VIXL_DISASSEMBLER=True -DENABLE_VIXL_SIMULATOR=False \
    -DENABLE_LTO=False -DENABLE_ASSERTIONS=True -DENABLE_X86_HOST_DEBUG=True -DBUILD_FEXCONFIG=False
  cmake --build build-icc --target CodeSizeValidation instcountci_test_files
  cmake --build build-icc --target instcountci_tests || true   # fails where numbers changed
  cmake --build build-icc --target instcountci_update_tests     # rewrites the JSON in the source tree
  git -C FEX diff --stat -- unittests/InstructionCountCI
  ```

- **It cannot run natively on this Mac.**
  - The top-level `FEX/CMakeLists.txt` (around line 632) does
    `if (NOT APPLE) add_subdirectory(Source/)`. `CodeSizeValidation` lives in
    `Source/Tools/CodeSizeValidation`, so it is never configured on Darwin,
    and `unittests/InstructionCountCI/CMakeLists.txt` launches
    `${CMAKE_BINARY_DIR}/Bin/CodeSizeValidation`.
  - `CodeSizeValidation` also pulls in the Linux frontend
    (`Source/Tools/LinuxEmulation`).
  - A Linux arm64 VM is possible: `colima` and `limactl` are installed in
    `/opt/homebrew/bin`. colima is not running and there is no lima instance.
    Nothing was started.
  - It is unverified whether this fork's FEXCore still builds for Linux. The
    fork maintains only iOS, ARM64EC and WOW64 builds.
- **Regeneration would not stay limited to the conflicted files.**
  - The fork's `CPUState`/`JITPointers` layout differs from upstream's. It adds
    `private_gdt[32]`, `SplitSwapLock`, `LUDIV`/`LDIV` and
    `LUDIVHandler`/`LDIVHandler`, and lacks `Dr[]`/`Dr6`/`Dr7`/`FIP`,
    `RDRANDFallback` and the dispatch-handler pointers. So every
    `[x28, #offset]` expectation in the suite may move.
  - A Linux build also has no `FEX_IOS_HOST`, so it does not reproduce device
    codegen (for example the hashed L1 index).
- **The lowest-effort mechanical option, for the owner to choose:**
  - During the conflicted pick, take upstream's version of each conflicted
    JSON file: `git checkout --theirs -- <json files> && git add <json files>`.
  - This matches how the 44 clean picks already carried upstream JSON
    unchanged.
  - Stage 3 (`--theirs`) during a cherry-pick is the file as it is in the
    picked commit. The probe in Appendix B used exactly those files and showed
    that the PCMPXSTRX follow-up commits then apply cleanly.

### After any port

- `build/fex-arm64ec/build.sh` regenerates
  `FEX/FEXCore/Source/Interface/Core/MadeiraCodegenId.h`, a hash of the
  codegen-affecting sources that keys the Madeira code cache, inside the FEX
  tree. Commit it with the port, as `1a66a7f4c` did.
- Items 1, 3 and 5 change the `JITPointers` layout: `638329278` adds a named
  constant (the H0F38 expectation moves from `#2736` to `#2752`), and
  `9f7913edb` removes four pointers.
  - `Source/Windows/ARM64EC/Module.S` uses only TEB/PEB offsets, not frame
    offsets, so no hand-written assembly offset needs to move. That was
    checked by grep, not by a build.
  - Rebuild every FEX artifact anyway.

---

## 1. `638329278` Inlined PCMPXSTRX, with its PR follow-ups

Upstream PR #5981, "Vector: Inline PCMPXSTRX SSE4.2 Instructions", by Justin
Becker. Commits in the PR:

- `638329278` (the change)
- `ff887adb1` "Include all immediates" (JSON only)
- `e2f0cf41d` "More cleanup" (Vector.cpp + JSON)
- `0206479a0` "Clarify new tests" (asm tests)
- `0ddd46ba4` "Bump DiskCacheVersion" (**skip**: `DiskCache.h` does not exist
  in the fork)
- `af8770005` "Clang format"

**What upstream changes and why.** PCMPESTRI/M and PCMPISTRI/M used to be an
IR op that spilled everything and called a C++ fallback
(`StringCompareFallbacks.cpp`). The new lowering emits NEON inline
(`OpcodeDispatcher/Vector.cpp` +302, `JIT/VectorOps.cpp` +40, new IR in
`IR.json`). Commit message microbenchmark, against the C++ implementation:

| | Equal Any | Ranges | Equal Each | Equal Ordered |
|---|---|---|---|---|
| pcmpestri | 13.40x | 9.71x | 19.38x | 14.07x |
| pcmpestrm | 13.12x | 9.54x | 18.73x | 13.80x |
| pcmpistri | 11.75x | 9.19x | 17.63x | 12.80x |
| pcmpistrm | 11.39x | 8.83x | 16.31x | 12.26x |

**Why it would help RDR2.**
- Every hit currently costs a full static-register spill and fill around a
  C++ call. Under a ~1 W cap, that is real time on any loop that scans text
  with SSE4.2 (XML/meta parsing, string search).
- It does nothing if RDR2 never executes these instructions. No device data
  says whether it does.
- It does not interact with TSO: the old path's spills go to the context, not
  to guest memory.

**Conflicts.**
- All of the code (`CPUBackend.cpp`, `InterpreterFallbacks.cpp`,
  `StringCompareFallbacks.cpp`, `VectorFallbacks.h`, `JIT/VectorOps.cpp`,
  `OpcodeDispatcher.h`, `OpcodeDispatcher/Vector.cpp`, `IR.json`,
  `IRDumper.cpp`, `IR/IR.h`) and both asm tests apply cleanly.
- Only these files conflict:
  - `unittests/InstructionCountCI/Crypto/H0F38.json` (1 region)
  - `unittests/InstructionCountCI/FlagM/HotBlocks.json` (1 region)
  - `unittests/InstructionCountCI/SSE42_Strings.json` (12 regions)

<details><summary>All 14 regions of 638329278, verbatim (721 lines)</summary>

```text
=== FILE unittests/InstructionCountCI/Crypto/H0F38.json
--- unittests/InstructionCountCI/Crypto/H0F38.json:34
<<<<<<< HEAD
        "ldr x0, [x28, #2736]",
=======
        "ldr x0, [x28, #2752]",
>>>>>>> 638329278 (Inlined PCMPXSTRX)

=== FILE unittests/InstructionCountCI/FlagM/HotBlocks.json
--- unittests/InstructionCountCI/FlagM/HotBlocks.json:894
<<<<<<< HEAD
        "str x30, [sp, #-16]!",
        "mov v0.16b, v16.16b",
        "mov v1.16b, v17.16b",
        "mov w0, #0xd",
        "ldr x1, [x28, #2376]",
        "ldr x3, [x28, #2384]",
        "blr x1",
        "ldr x30, [sp], #16",
        "mov w20, w0",
        "mov w27, #0x0",
        "uxth w21, w20",
=======
        "ldr q2, [x28, #3136]",
        "cmeq v3.8h, v16.8h, #0",
        "movi v4.8h, #0x8",
        "bsl v3.16b, v2.16b, v4.16b",
        "uminv h3, v3.8h",
        "cmeq v5.8h, v17.8h, #0",
        "bsl v5.16b, v2.16b, v4.16b",
        "uminv h5, v5.8h",
        "umov w20, v3.h[0]",
        "umov w21, v5.h[0]",
        "dup v3.8h, v3.h[0]",
        "cmgt v3.8h, v3.8h, v2.8h",
>>>>>>> 638329278 (Inlined PCMPXSTRX)

=== FILE unittests/InstructionCountCI/SSE42_Strings.json
--- unittests/InstructionCountCI/SSE42_Strings.json:45
<<<<<<< HEAD
        "ldr x3, [x28, #2360]",
        "ldr x0, [x28, #2368]",
        "stp x0, x30, [sp, #-16]!",
        "mov x0, x4",
        "mov x1, x5",
        "mov w2, #0x0",
        "mov v0.16b, v16.16b",
        "mov v1.16b, v17.16b",
        "blr x3",
        "ldp xzr, x30, [sp], #16",
        "mov w20, w0",
        "uxth w0, w20",
        "fmov s16, w0",
        "mov w26, #0x1",
=======
        "ldr q2, [x28, #3168]",
>>>>>>> 638329278 (Inlined PCMPXSTRX)

--- unittests/InstructionCountCI/SSE42_Strings.json:158
<<<<<<< HEAD
        "ldr x3, [x28, #2360]",
        "ldr x0, [x28, #2368]",
        "stp x0, x30, [sp, #-16]!",
        "mov x0, x4",
        "mov x1, x5",
        "mov w2, #0x1",
        "mov v0.16b, v16.16b",
        "mov v1.16b, v17.16b",
        "blr x3",
        "ldp xzr, x30, [sp], #16",
        "mov w20, w0",
        "uxth w0, w20",
        "fmov s16, w0",
        "mov w26, #0x1",
=======
        "ldr q2, [x28, #3136]",
>>>>>>> 638329278 (Inlined PCMPXSTRX)

--- unittests/InstructionCountCI/SSE42_Strings.json:248
<<<<<<< HEAD
        "ldr x3, [x28, #2360]",
        "ldr x0, [x28, #2368]",
        "stp x0, x30, [sp, #-16]!",
        "mov x0, x4",
        "mov x1, x5",
        "mov w2, #0x40",
        "mov v0.16b, v16.16b",
        "mov v1.16b, v17.16b",
        "blr x3",
        "ldp xzr, x30, [sp], #16",
        "mov w20, w0",
        "movi v2.2d, #0x0",
        "sbfx x21, x20, #0, #1",
        "mov v2.b[0], w21",
        "sbfx x21, x20, #1, #1",
        "mov v2.b[1], w21",
        "sbfx x21, x20, #2, #1",
        "mov v2.b[2], w21",
        "sbfx x21, x20, #3, #1",
        "mov v2.b[3], w21",
        "sbfx x21, x20, #4, #1",
        "mov v2.b[4], w21",
        "sbfx x21, x20, #5, #1",
        "mov v2.b[5], w21",
        "sbfx x21, x20, #6, #1",
        "mov v2.b[6], w21",
        "sbfx x21, x20, #7, #1",
        "mov v2.b[7], w21",
        "sbfx x21, x20, #8, #1",
        "mov v2.b[8], w21",
        "sbfx x21, x20, #9, #1",
        "mov v2.b[9], w21",
        "sbfx x21, x20, #10, #1",
        "mov v2.b[10], w21",
        "sbfx x21, x20, #11, #1",
        "mov v2.b[11], w21",
        "sbfx x21, x20, #12, #1",
        "mov v2.b[12], w21",
        "sbfx x21, x20, #13, #1",
        "mov v2.b[13], w21",
        "sbfx x21, x20, #14, #1",
        "mov v2.b[14], w21",
        "sbfx x21, x20, #15, #1",
        "mov v16.16b, v2.16b",
        "mov v16.b[15], w21",
        "mov w26, #0x1",
=======
        "ldr q2, [x28, #3168]",
>>>>>>> 638329278 (Inlined PCMPXSTRX)

--- unittests/InstructionCountCI/SSE42_Strings.json:389
<<<<<<< HEAD
        "ldr x3, [x28, #2360]",
        "ldr x0, [x28, #2368]",
        "stp x0, x30, [sp, #-16]!",
        "mov x0, x4",
        "mov x1, x5",
        "mov w2, #0x41",
        "mov v0.16b, v16.16b",
        "mov v1.16b, v17.16b",
        "blr x3",
        "ldp xzr, x30, [sp], #16",
        "mov w20, w0",
        "movi v2.2d, #0x0",
        "sbfx x21, x20, #0, #1",
        "mov v2.h[0], w21",
        "sbfx x21, x20, #1, #1",
        "mov v2.h[1], w21",
        "sbfx x21, x20, #2, #1",
        "mov v2.h[2], w21",
        "sbfx x21, x20, #3, #1",
        "mov v2.h[3], w21",
        "sbfx x21, x20, #4, #1",
        "mov v2.h[4], w21",
        "sbfx x21, x20, #5, #1",
        "mov v2.h[5], w21",
        "sbfx x21, x20, #6, #1",
        "mov v2.h[6], w21",
        "sbfx x21, x20, #7, #1",
        "mov v16.16b, v2.16b",
        "mov v16.h[7], w21",
        "mov w26, #0x1",
=======
        "ldr q2, [x28, #3136]",
>>>>>>> 638329278 (Inlined PCMPXSTRX)

--- unittests/InstructionCountCI/SSE42_Strings.json:1017
<<<<<<< HEAD
        "ldr x3, [x28, #2360]",
        "ldr x0, [x28, #2368]",
        "stp x0, x30, [sp, #-16]!",
        "mov x0, x4",
        "mov x1, x5",
        "mov w2, #0x0",
        "mov v0.16b, v16.16b",
        "mov v1.16b, v17.16b",
        "blr x3",
        "ldp xzr, x30, [sp], #16",
        "mov w20, w0",
=======
        "ldr q2, [x28, #3168]",
>>>>>>> 638329278 (Inlined PCMPXSTRX)

--- unittests/InstructionCountCI/SSE42_Strings.json:1125
<<<<<<< HEAD
        "ldr x3, [x28, #2360]",
        "ldr x0, [x28, #2368]",
        "stp x0, x30, [sp, #-16]!",
        "mov x0, x4",
        "mov x1, x5",
        "mov w2, #0x1",
        "mov v0.16b, v16.16b",
        "mov v1.16b, v17.16b",
        "blr x3",
        "ldp xzr, x30, [sp], #16",
        "mov w20, w0",
=======
        "ldr q2, [x28, #3136]",
>>>>>>> 638329278 (Inlined PCMPXSTRX)

--- unittests/InstructionCountCI/SSE42_Strings.json:1721
<<<<<<< HEAD
        "str x30, [sp, #-16]!",
        "mov v0.16b, v16.16b",
        "mov v1.16b, v17.16b",
        "mov w0, #0x0",
        "ldr x1, [x28, #2376]",
        "ldr x3, [x28, #2384]",
        "blr x1",
        "ldr x30, [sp], #16",
        "mov w20, w0",
        "uxth w0, w20",
        "fmov s16, w0",
        "mov w26, #0x1",
=======
        "ldr q2, [x28, #3168]",
        "cmeq v3.16b, v16.16b, #0",
        "movi v4.16b, #0x10",
        "bsl v3.16b, v2.16b, v4.16b",
        "uminv b3, v3.16b",
        "cmeq v5.16b, v17.16b, #0",
        "bsl v5.16b, v2.16b, v4.16b",
        "uminv b4, v5.16b",
        "umov w20, v3.b[0]",
        "umov w21, v4.b[0]",
        "dup v3.16b, v3.b[0]",
        "cmgt v3.16b, v3.16b, v2.16b",
        "dup v4.16b, v4.b[0]",
        "cmp w20, #0x10 (16)",
        "cset x20, lo",
        "cmp w21, #0x10 (16)",
        "cset x21, lo",
        "cmgt v2.16b, v4.16b, v2.16b",
        "dup v4.16b, v16.b[0]",
        "mov v5.16b, v3.16b",
        "bsl v5.16b, v16.16b, v4.16b",
        "cmeq v4.16b, v17.16b, v4.16b",
        "dup v6.16b, v5.b[1]",
        "cmeq v6.16b, v17.16b, v6.16b",
        "orr v4.16b, v4.16b, v6.16b",
        "dup v6.16b, v5.b[2]",
        "cmeq v6.16b, v17.16b, v6.16b",
        "orr v4.16b, v4.16b, v6.16b",
        "dup v6.16b, v5.b[3]",
        "cmeq v6.16b, v17.16b, v6.16b",
        "orr v4.16b, v4.16b, v6.16b",
        "dup v6.16b, v5.b[4]",
        "cmeq v6.16b, v17.16b, v6.16b",
        "orr v4.16b, v4.16b, v6.16b",
        "dup v6.16b, v5.b[5]",
        "cmeq v6.16b, v17.16b, v6.16b",
        "orr v4.16b, v4.16b, v6.16b",
        "dup v6.16b, v5.b[6]",
        "cmeq v6.16b, v17.16b, v6.16b",
        "orr v4.16b, v4.16b, v6.16b",
        "dup v6.16b, v5.b[7]",
        "cmeq v6.16b, v17.16b, v6.16b",
        "orr v4.16b, v4.16b, v6.16b",
        "dup v6.16b, v5.b[8]",
        "cmeq v6.16b, v17.16b, v6.16b",
        "orr v4.16b, v4.16b, v6.16b",
        "dup v6.16b, v5.b[9]",
        "cmeq v6.16b, v17.16b, v6.16b",
        "orr v4.16b, v4.16b, v6.16b",
        "dup v6.16b, v5.b[10]",
        "cmeq v6.16b, v17.16b, v6.16b",
        "orr v4.16b, v4.16b, v6.16b",
        "dup v6.16b, v5.b[11]",
        "cmeq v6.16b, v17.16b, v6.16b",
        "orr v4.16b, v4.16b, v6.16b",
        "dup v6.16b, v5.b[12]",
        "cmeq v6.16b, v17.16b, v6.16b",
        "orr v4.16b, v4.16b, v6.16b",
        "dup v6.16b, v5.b[13]",
        "cmeq v6.16b, v17.16b, v6.16b",
        "orr v4.16b, v4.16b, v6.16b",
        "dup v6.16b, v5.b[14]",
        "cmeq v6.16b, v17.16b, v6.16b",
        "orr v4.16b, v4.16b, v6.16b",
        "dup v5.16b, v5.b[15]",
        "cmeq v5.16b, v17.16b, v5.16b",
        "orr v4.16b, v4.16b, v5.16b",
        "dup v3.16b, v3.b[0]",
        "and v3.16b, v4.16b, v3.16b",
        "and v2.16b, v3.16b, v2.16b",
        "ldr q3, [x28, #3440]",
        "movi v4.2d, #0x0",
        "and v2.16b, v2.16b, v3.16b",
        "addp v2.16b, v2.16b, v4.16b",
        "addp v2.16b, v2.16b, v4.16b",
        "addp v16.16b, v2.16b, v4.16b",
        "umov w22, v16.h[0]",
>>>>>>> 638329278 (Inlined PCMPXSTRX)

--- unittests/InstructionCountCI/SSE42_Strings.json:1832
<<<<<<< HEAD
        "str x30, [sp, #-16]!",
        "mov v0.16b, v16.16b",
        "mov v1.16b, v17.16b",
        "mov w0, #0x1",
        "ldr x1, [x28, #2376]",
        "ldr x3, [x28, #2384]",
        "blr x1",
        "ldr x30, [sp], #16",
        "mov w20, w0",
        "uxth w0, w20",
        "fmov s16, w0",
        "mov w26, #0x1",
=======
        "ldr q2, [x28, #3136]",
        "cmeq v3.8h, v16.8h, #0",
        "movi v4.8h, #0x8",
        "bsl v3.16b, v2.16b, v4.16b",
        "uminv h3, v3.8h",
        "cmeq v5.8h, v17.8h, #0",
        "bsl v5.16b, v2.16b, v4.16b",
        "uminv h4, v5.8h",
        "umov w20, v3.h[0]",
        "umov w21, v4.h[0]",
        "dup v3.8h, v3.h[0]",
        "cmgt v3.8h, v3.8h, v2.8h",
        "dup v4.8h, v4.h[0]",
        "cmp w20, #0x8 (8)",
        "cset x20, lo",
        "cmp w21, #0x8 (8)",
        "cset x21, lo",
        "cmgt v2.8h, v4.8h, v2.8h",
        "dup v4.8h, v16.h[0]",
        "mov v5.16b, v3.16b",
        "bsl v5.16b, v16.16b, v4.16b",
        "cmeq v4.8h, v17.8h, v4.8h",
        "dup v6.8h, v5.h[1]",
        "cmeq v6.8h, v17.8h, v6.8h",
        "orr v4.16b, v4.16b, v6.16b",
        "dup v6.8h, v5.h[2]",
        "cmeq v6.8h, v17.8h, v6.8h",
        "orr v4.16b, v4.16b, v6.16b",
        "dup v6.8h, v5.h[3]",
        "cmeq v6.8h, v17.8h, v6.8h",
        "orr v4.16b, v4.16b, v6.16b",
        "dup v6.8h, v5.h[4]",
        "cmeq v6.8h, v17.8h, v6.8h",
        "orr v4.16b, v4.16b, v6.16b",
        "dup v6.8h, v5.h[5]",
        "cmeq v6.8h, v17.8h, v6.8h",
        "orr v4.16b, v4.16b, v6.16b",
        "dup v6.8h, v5.h[6]",
        "cmeq v6.8h, v17.8h, v6.8h",
        "orr v4.16b, v4.16b, v6.16b",
        "dup v5.8h, v5.h[7]",
        "cmeq v5.8h, v17.8h, v5.8h",
        "orr v4.16b, v4.16b, v5.16b",
        "dup v3.8h, v3.h[0]",
        "and v3.16b, v4.16b, v3.16b",
        "and v2.16b, v3.16b, v2.16b",
        "ldr q3, [x28, #3440]",
        "movi v4.2d, #0x0",
        "uzp1 v2.16b, v2.16b, v4.16b",
        "and v2.16b, v2.16b, v3.16b",
        "addp v2.16b, v2.16b, v4.16b",
        "addp v2.16b, v2.16b, v4.16b",
        "addp v16.16b, v2.16b, v4.16b",
        "umov w22, v16.b[0]",
>>>>>>> 638329278 (Inlined PCMPXSTRX)

--- unittests/InstructionCountCI/SSE42_Strings.json:1920
<<<<<<< HEAD
        "str x30, [sp, #-16]!",
        "mov v0.16b, v16.16b",
        "mov v1.16b, v17.16b",
        "mov w0, #0x40",
        "ldr x1, [x28, #2376]",
        "ldr x3, [x28, #2384]",
        "blr x1",
        "ldr x30, [sp], #16",
        "mov w20, w0",
        "movi v2.2d, #0x0",
        "sbfx x21, x20, #0, #1",
        "mov v2.b[0], w21",
        "sbfx x21, x20, #1, #1",
        "mov v2.b[1], w21",
        "sbfx x21, x20, #2, #1",
        "mov v2.b[2], w21",
        "sbfx x21, x20, #3, #1",
        "mov v2.b[3], w21",
        "sbfx x21, x20, #4, #1",
        "mov v2.b[4], w21",
        "sbfx x21, x20, #5, #1",
        "mov v2.b[5], w21",
        "sbfx x21, x20, #6, #1",
        "mov v2.b[6], w21",
        "sbfx x21, x20, #7, #1",
        "mov v2.b[7], w21",
        "sbfx x21, x20, #8, #1",
        "mov v2.b[8], w21",
        "sbfx x21, x20, #9, #1",
        "mov v2.b[9], w21",
        "sbfx x21, x20, #10, #1",
        "mov v2.b[10], w21",
        "sbfx x21, x20, #11, #1",
        "mov v2.b[11], w21",
        "sbfx x21, x20, #12, #1",
        "mov v2.b[12], w21",
        "sbfx x21, x20, #13, #1",
        "mov v2.b[13], w21",
        "sbfx x21, x20, #14, #1",
        "mov v2.b[14], w21",
        "sbfx x21, x20, #15, #1",
        "mov v16.16b, v2.16b",
        "mov v16.b[15], w21",
        "mov w26, #0x1",
=======
        "ldr q2, [x28, #3168]",
        "cmeq v3.16b, v16.16b, #0",
        "movi v4.16b, #0x10",
        "bsl v3.16b, v2.16b, v4.16b",
        "uminv b3, v3.16b",
        "cmeq v5.16b, v17.16b, #0",
        "bsl v5.16b, v2.16b, v4.16b",
        "uminv b4, v5.16b",
        "umov w20, v3.b[0]",
        "umov w21, v4.b[0]",
        "dup v3.16b, v3.b[0]",
        "cmgt v3.16b, v3.16b, v2.16b",
        "dup v4.16b, v4.b[0]",
        "cmp w20, #0x10 (16)",
        "cset x20, lo",
        "cmp w21, #0x10 (16)",
        "cset x21, lo",
        "cmgt v2.16b, v4.16b, v2.16b",
        "dup v4.16b, v16.b[0]",
        "mov v5.16b, v3.16b",
        "bsl v5.16b, v16.16b, v4.16b",
        "cmeq v4.16b, v17.16b, v4.16b",
        "dup v6.16b, v5.b[1]",
        "cmeq v6.16b, v17.16b, v6.16b",
        "orr v4.16b, v4.16b, v6.16b",
        "dup v6.16b, v5.b[2]",
        "cmeq v6.16b, v17.16b, v6.16b",
        "orr v4.16b, v4.16b, v6.16b",
        "dup v6.16b, v5.b[3]",
        "cmeq v6.16b, v17.16b, v6.16b",
        "orr v4.16b, v4.16b, v6.16b",
        "dup v6.16b, v5.b[4]",
        "cmeq v6.16b, v17.16b, v6.16b",
        "orr v4.16b, v4.16b, v6.16b",
        "dup v6.16b, v5.b[5]",
        "cmeq v6.16b, v17.16b, v6.16b",
        "orr v4.16b, v4.16b, v6.16b",
        "dup v6.16b, v5.b[6]",
        "cmeq v6.16b, v17.16b, v6.16b",
        "orr v4.16b, v4.16b, v6.16b",
        "dup v6.16b, v5.b[7]",
        "cmeq v6.16b, v17.16b, v6.16b",
        "orr v4.16b, v4.16b, v6.16b",
        "dup v6.16b, v5.b[8]",
        "cmeq v6.16b, v17.16b, v6.16b",
        "orr v4.16b, v4.16b, v6.16b",
        "dup v6.16b, v5.b[9]",
        "cmeq v6.16b, v17.16b, v6.16b",
        "orr v4.16b, v4.16b, v6.16b",
        "dup v6.16b, v5.b[10]",
        "cmeq v6.16b, v17.16b, v6.16b",
        "orr v4.16b, v4.16b, v6.16b",
        "dup v6.16b, v5.b[11]",
        "cmeq v6.16b, v17.16b, v6.16b",
        "orr v4.16b, v4.16b, v6.16b",
        "dup v6.16b, v5.b[12]",
        "cmeq v6.16b, v17.16b, v6.16b",
        "orr v4.16b, v4.16b, v6.16b",
        "dup v6.16b, v5.b[13]",
        "cmeq v6.16b, v17.16b, v6.16b",
        "orr v4.16b, v4.16b, v6.16b",
        "dup v6.16b, v5.b[14]",
        "cmeq v6.16b, v17.16b, v6.16b",
        "orr v4.16b, v4.16b, v6.16b",
        "dup v5.16b, v5.b[15]",
        "cmeq v5.16b, v17.16b, v5.16b",
        "orr v4.16b, v4.16b, v5.16b",
        "dup v3.16b, v3.b[0]",
        "and v3.16b, v4.16b, v3.16b",
        "and v16.16b, v3.16b, v2.16b",
        "umaxv b2, v16.16b",
        "umov w22, v2.b[0]",
>>>>>>> 638329278 (Inlined PCMPXSTRX)

--- unittests/InstructionCountCI/SSE42_Strings.json:2059
<<<<<<< HEAD
        "str x30, [sp, #-16]!",
        "mov v0.16b, v16.16b",
        "mov v1.16b, v17.16b",
        "mov w0, #0x41",
        "ldr x1, [x28, #2376]",
        "ldr x3, [x28, #2384]",
        "blr x1",
        "ldr x30, [sp], #16",
        "mov w20, w0",
        "movi v2.2d, #0x0",
        "sbfx x21, x20, #0, #1",
        "mov v2.h[0], w21",
        "sbfx x21, x20, #1, #1",
        "mov v2.h[1], w21",
        "sbfx x21, x20, #2, #1",
        "mov v2.h[2], w21",
        "sbfx x21, x20, #3, #1",
        "mov v2.h[3], w21",
        "sbfx x21, x20, #4, #1",
        "mov v2.h[4], w21",
        "sbfx x21, x20, #5, #1",
        "mov v2.h[5], w21",
        "sbfx x21, x20, #6, #1",
        "mov v2.h[6], w21",
        "sbfx x21, x20, #7, #1",
        "mov v16.16b, v2.16b",
        "mov v16.h[7], w21",
        "mov w26, #0x1",
=======
        "ldr q2, [x28, #3136]",
        "cmeq v3.8h, v16.8h, #0",
        "movi v4.8h, #0x8",
        "bsl v3.16b, v2.16b, v4.16b",
        "uminv h3, v3.8h",
        "cmeq v5.8h, v17.8h, #0",
        "bsl v5.16b, v2.16b, v4.16b",
        "uminv h4, v5.8h",
        "umov w20, v3.h[0]",
        "umov w21, v4.h[0]",
        "dup v3.8h, v3.h[0]",
        "cmgt v3.8h, v3.8h, v2.8h",
        "dup v4.8h, v4.h[0]",
        "cmp w20, #0x8 (8)",
        "cset x20, lo",
        "cmp w21, #0x8 (8)",
        "cset x21, lo",
        "cmgt v2.8h, v4.8h, v2.8h",
        "dup v4.8h, v16.h[0]",
        "mov v5.16b, v3.16b",
        "bsl v5.16b, v16.16b, v4.16b",
        "cmeq v4.8h, v17.8h, v4.8h",
        "dup v6.8h, v5.h[1]",
        "cmeq v6.8h, v17.8h, v6.8h",
        "orr v4.16b, v4.16b, v6.16b",
        "dup v6.8h, v5.h[2]",
        "cmeq v6.8h, v17.8h, v6.8h",
        "orr v4.16b, v4.16b, v6.16b",
        "dup v6.8h, v5.h[3]",
        "cmeq v6.8h, v17.8h, v6.8h",
        "orr v4.16b, v4.16b, v6.16b",
        "dup v6.8h, v5.h[4]",
        "cmeq v6.8h, v17.8h, v6.8h",
        "orr v4.16b, v4.16b, v6.16b",
        "dup v6.8h, v5.h[5]",
        "cmeq v6.8h, v17.8h, v6.8h",
        "orr v4.16b, v4.16b, v6.16b",
        "dup v6.8h, v5.h[6]",
        "cmeq v6.8h, v17.8h, v6.8h",
        "orr v4.16b, v4.16b, v6.16b",
        "dup v5.8h, v5.h[7]",
        "cmeq v5.8h, v17.8h, v5.8h",
        "orr v4.16b, v4.16b, v5.16b",
        "dup v3.8h, v3.h[0]",
        "and v3.16b, v4.16b, v3.16b",
        "and v16.16b, v3.16b, v2.16b",
        "umaxv h2, v16.8h",
        "umov w22, v2.h[0]",
>>>>>>> 638329278 (Inlined PCMPXSTRX)

--- unittests/InstructionCountCI/SSE42_Strings.json:2687
<<<<<<< HEAD
        "str x30, [sp, #-16]!",
        "mov v0.16b, v16.16b",
        "mov v1.16b, v17.16b",
        "mov w0, #0x0",
        "ldr x1, [x28, #2376]",
        "ldr x3, [x28, #2384]",
        "blr x1",
        "ldr x30, [sp], #16",
        "mov w20, w0",
=======
        "ldr q2, [x28, #3168]",
        "cmeq v3.16b, v16.16b, #0",
        "movi v4.16b, #0x10",
        "bsl v3.16b, v2.16b, v4.16b",
        "uminv b3, v3.16b",
        "cmeq v5.16b, v17.16b, #0",
        "bsl v5.16b, v2.16b, v4.16b",
        "uminv b5, v5.16b",
        "umov w20, v3.b[0]",
        "umov w21, v5.b[0]",
        "dup v3.16b, v3.b[0]",
        "cmgt v3.16b, v3.16b, v2.16b",
        "dup v5.16b, v5.b[0]",
        "cmp w20, #0x10 (16)",
        "cset x20, lo",
        "cmp w21, #0x10 (16)",
        "cset x21, lo",
        "cmgt v5.16b, v5.16b, v2.16b",
        "dup v6.16b, v16.b[0]",
        "mov v7.16b, v3.16b",
        "bsl v7.16b, v16.16b, v6.16b",
        "cmeq v6.16b, v17.16b, v6.16b",
        "dup v8.16b, v7.b[1]",
        "cmeq v8.16b, v17.16b, v8.16b",
        "orr v6.16b, v6.16b, v8.16b",
        "dup v8.16b, v7.b[2]",
        "cmeq v8.16b, v17.16b, v8.16b",
        "orr v6.16b, v6.16b, v8.16b",
        "dup v8.16b, v7.b[3]",
        "cmeq v8.16b, v17.16b, v8.16b",
        "orr v6.16b, v6.16b, v8.16b",
        "dup v8.16b, v7.b[4]",
        "cmeq v8.16b, v17.16b, v8.16b",
        "orr v6.16b, v6.16b, v8.16b",
        "dup v8.16b, v7.b[5]",
        "cmeq v8.16b, v17.16b, v8.16b",
        "orr v6.16b, v6.16b, v8.16b",
        "dup v8.16b, v7.b[6]",
        "cmeq v8.16b, v17.16b, v8.16b",
        "orr v6.16b, v6.16b, v8.16b",
        "dup v8.16b, v7.b[7]",
        "cmeq v8.16b, v17.16b, v8.16b",
        "orr v6.16b, v6.16b, v8.16b",
        "dup v8.16b, v7.b[8]",
        "cmeq v8.16b, v17.16b, v8.16b",
        "orr v6.16b, v6.16b, v8.16b",
        "dup v8.16b, v7.b[9]",
        "cmeq v8.16b, v17.16b, v8.16b",
        "orr v6.16b, v6.16b, v8.16b",
        "dup v8.16b, v7.b[10]",
        "cmeq v8.16b, v17.16b, v8.16b",
        "orr v6.16b, v6.16b, v8.16b",
        "dup v8.16b, v7.b[11]",
        "cmeq v8.16b, v17.16b, v8.16b",
        "orr v6.16b, v6.16b, v8.16b",
        "dup v8.16b, v7.b[12]",
        "cmeq v8.16b, v17.16b, v8.16b",
        "orr v6.16b, v6.16b, v8.16b",
        "dup v8.16b, v7.b[13]",
        "cmeq v8.16b, v17.16b, v8.16b",
        "orr v6.16b, v6.16b, v8.16b",
        "dup v8.16b, v7.b[14]",
        "cmeq v8.16b, v17.16b, v8.16b",
        "orr v6.16b, v6.16b, v8.16b",
        "dup v7.16b, v7.b[15]",
        "cmeq v7.16b, v17.16b, v7.16b",
        "orr v6.16b, v6.16b, v7.16b",
        "dup v3.16b, v3.b[0]",
        "and v3.16b, v6.16b, v3.16b",
        "and v3.16b, v3.16b, v5.16b",
        "bsl v3.16b, v2.16b, v4.16b",
        "uminv b2, v3.16b",
        "umov w7, v2.b[0]",
        "cmp w7, #0x10 (16)",
        "cset x22, eq",
>>>>>>> 638329278 (Inlined PCMPXSTRX)

--- unittests/InstructionCountCI/SSE42_Strings.json:2792
<<<<<<< HEAD
        "str x30, [sp, #-16]!",
        "mov v0.16b, v16.16b",
        "mov v1.16b, v17.16b",
        "mov w0, #0x1",
        "ldr x1, [x28, #2376]",
        "ldr x3, [x28, #2384]",
        "blr x1",
        "ldr x30, [sp], #16",
        "mov w20, w0",
=======
        "ldr q2, [x28, #3136]",
        "cmeq v3.8h, v16.8h, #0",
        "movi v4.8h, #0x8",
        "bsl v3.16b, v2.16b, v4.16b",
        "uminv h3, v3.8h",
        "cmeq v5.8h, v17.8h, #0",
        "bsl v5.16b, v2.16b, v4.16b",
        "uminv h5, v5.8h",
        "umov w20, v3.h[0]",
        "umov w21, v5.h[0]",
        "dup v3.8h, v3.h[0]",
        "cmgt v3.8h, v3.8h, v2.8h",
        "dup v5.8h, v5.h[0]",
        "cmp w20, #0x8 (8)",
        "cset x20, lo",
        "cmp w21, #0x8 (8)",
        "cset x21, lo",
        "cmgt v5.8h, v5.8h, v2.8h",
        "dup v6.8h, v16.h[0]",
        "mov v7.16b, v3.16b",
        "bsl v7.16b, v16.16b, v6.16b",
        "cmeq v6.8h, v17.8h, v6.8h",
        "dup v8.8h, v7.h[1]",
        "cmeq v8.8h, v17.8h, v8.8h",
        "orr v6.16b, v6.16b, v8.16b",
        "dup v8.8h, v7.h[2]",
        "cmeq v8.8h, v17.8h, v8.8h",
        "orr v6.16b, v6.16b, v8.16b",
        "dup v8.8h, v7.h[3]",
        "cmeq v8.8h, v17.8h, v8.8h",
        "orr v6.16b, v6.16b, v8.16b",
        "dup v8.8h, v7.h[4]",
        "cmeq v8.8h, v17.8h, v8.8h",
        "orr v6.16b, v6.16b, v8.16b",
        "dup v8.8h, v7.h[5]",
        "cmeq v8.8h, v17.8h, v8.8h",
        "orr v6.16b, v6.16b, v8.16b",
        "dup v8.8h, v7.h[6]",
        "cmeq v8.8h, v17.8h, v8.8h",
        "orr v6.16b, v6.16b, v8.16b",
        "dup v7.8h, v7.h[7]",
        "cmeq v7.8h, v17.8h, v7.8h",
        "orr v6.16b, v6.16b, v7.16b",
        "dup v3.8h, v3.h[0]",
        "and v3.16b, v6.16b, v3.16b",
        "and v3.16b, v3.16b, v5.16b",
        "bsl v3.16b, v2.16b, v4.16b",
        "uminv h2, v3.8h",
        "umov w7, v2.h[0]",
        "cmp w7, #0x8 (8)",
        "cset x22, eq",
>>>>>>> 638329278 (Inlined PCMPXSTRX)
```

</details>

**What the fork side is.**
- Blame puts every fork line at `707f213f5`, the root snapshot. These are
  upstream's own older expectations.
- The fork side shows the old helper-call sequence
  (`ldr x3, [x28, #2360] ... blr x3`). The upstream side shows the new inline
  NEON code, plus one pointer offset that moved because the new constant was
  added (`#2736` to `#2752`).
- No fork behaviour is protected here.

**Follow-ups.**
- In isolation, `e2f0cf41d` conflicts in `Vector.cpp` and
  `SSE42_Strings.json`, and `af8770005` conflicts in `Vector.cpp`, because
  `638329278` is missing.
- Probe: the code part of `638329278` was applied with the JSON excluded, and
  upstream's versions of the three JSON files were taken. After that,
  `ff887adb1 e2f0cf41d 0206479a0 af8770005` applied **cleanly**
  (`cherry-pick -n` exit 0).
- With the code applied but the JSON missing, `af8770005` applies cleanly,
  while `e2f0cf41d`'s JSON conflict is the whole 43k-line `ff887adb1` delta.
  That is why `ff887adb1` must come first.

**Dependencies.** None outside the PR. Order: `638329278`, `ff887adb1`,
`e2f0cf41d`, `0206479a0`, `af8770005`.

**Resolution size.**
- Mechanical: resolve the three JSON files in `638329278` (for example with
  `git checkout --theirs`, see above, or by regenerating on Linux). The rest
  are clean picks.
- Then rebuild ARM64EC and WOW64 and regenerate `MadeiraCodegenId.h`.
- This is not a design decision.

---

## 2. `179b4423a` Wine: change behaviour around SMC and NtReadFile

Upstream PR #5925, by Ryan Houdek.

**What upstream changes and why.** From the commit message:

> Instead of forcing the Persona 5 code invalidation on to every process, only
> do it on P5R. This fixes a hang in msiexec with PhysX trying to do a blocking
> read and jitting code or creating threads.

- Today (fork and old upstream), the hook runs before every NtReadFile. It
  takes `ThreadCreationMutex`, then `CodeInvalidationMutex` exclusively, then
  calls `BeginUntrackedWriteLocked`.
  - If the buffer overlaps a tracked RWX interval, that call invalidates the
    range and **removes FEX's write protection** from those pages, and both
    locks stay held **until the read returns**.
  - Otherwise both locks are released at once.
- Upstream keeps that path only for `P5R.exe`. For every other process, it
  does nothing before the read. After a successful read it takes
  `ThreadCreationMutex` and calls `InvalidateAlignedInterval(Address, Size,
  false)`, which takes `CodeInvalidationMutex` exclusively inside
  `InvalidateIntervalInternal`.
- Upstream has not changed this function since.

**What this means for RDR2.** This is a judgement, not a measurement.
- Today, a read whose target overlaps an RWX interval blocks **all JIT
  compilation and thread creation in the process** for as long as the disk
  read takes.
- Every read, RWX or not, also waits for in-flight compiles to drain, because
  it needs the exclusive lock.
- RDR2 reads 93 to 878 MB per 2 s during the load burst
  (`docs/RDR2_LOAD_TIME_IPHONE18_2026-11-27.md`). If any of those reads land
  in RWX memory (an anti-tamper or self-loader reading code from disk would
  do that), upstream's change removes a stall that serializes compiles behind
  I/O.
- For reads that do **not** hit RWX memory, upstream is slightly **more**
  work than the fork.
  - Upstream: after the read, an exclusive `CodeInvalidationMutex`, then
    `InvalidateCodeBuffersCodeRange` (a range lookup on every code buffer's
    `LookupCache`), then `InvalidateThreadCachedCodeRange` for **every
    thread** (`ResetExecutableRangeCache`, plus a per-thread write lock and a
    `CachedCodePages` range lookup). See `FEXCore/Source/Interface/Core/Core.cpp:2372-2401`
    and `LookupCache.h:149,447`.
  - Fork: one interval query under the same locks.
- No current log shows how often reads hit RWX memory.
  `~/MadeiraLogs/iphone18/1127-0019-ran` has no line for it. Measuring it
  needs a FEX-side counter, which the owner has to write by hand.

**Correctness hazard in upstream's semantics.** Upstream names this as the
"only gotcha".
- FEX write-protects RWX pages it has compiled code from.
  `ProtectRWXIntervalsInternal(ForWriteLocked=true)` is what un-traps them
  today, before the read.
- With upstream's path, a read into such a page reaches Wine's
  `virtual_locked_read` (`wine/dlls/ntdll/unix/virtual.c:4722`). There,
  `read()` returns `EFAULT`, `check_write_access` sees no `PROT_WRITE` and
  returns `STATUS_INVALID_USER_BUFFER`, and the game's NtReadFile **fails**
  where it succeeds today.
- So taking upstream's side verbatim could break exactly the
  code-read-from-disk case that motivates the change.
- Upstream also notes that async reads are untracked in both versions.

**Conflicts.** Three regions: two in `Source/Windows/ARM64EC/Module.cpp`, one
in `Source/Windows/WOW64/Module.cpp`.
- These parts merged cleanly: the ARM64EC
  `static fextl::string AppConfigName {};` and the `StartupSleep` hunk that
  now compares against `AppConfigName`.
- So the resolved file **must assign `AppConfigName`**. Otherwise it stays
  empty, and both the P5R branch and `StartupSleepProcName` silently stop
  matching.

```text
=== FILE Source/Windows/ARM64EC/Module.cpp
--- Source/Windows/ARM64EC/Module.cpp:945
<<<<<<< HEAD
#ifdef FEX_IOS_HOST
  {
    const char* Xp = getenv("MADEIRA_FEX_XP");
    IosXpEnabled = Xp && Xp[0] == '1' && Xp[1] == 0;
  }
#endif
  const auto ExecutableName = FEX::Windows::BaseName(FEX::Windows::GetExecutableFilePath());
  FEX::Config::LoadConfig(fextl::string {ExecutableName}, _environ, FEX::ReadPortabilityInformation());
  FEXCore::Config::ReloadMetaLayer();
  FEX::Windows::Logging::Init();

#if defined(FEX_IOS_HOST) && defined(MADEIRA_ARENA_SELFTEST)
  /* Runs only when MADEIRA_ARENA_TEST is set, and only in builds that link the
   * self-test (ArenaSelfTest.cpp is not in CMakeLists, so this is compiled out
   * by default and the carver has NEVER executed on device).
   *
   * ml798 correction: an earlier version of this comment blamed the test's
   * std::thread/std::vector CRT constructors for the _lock(17) recursion that
   * killed every launch. The ml796 A/B REFUTED that -- unlinking the test
   * changed nothing. The real cause was three getenv() calls added to
   * rpmalloc's band selector, which runs before ucrtbase's DllMain; see the
   * ml797 note in ios_fex_band_select(). Keeping the false attribution here
   * would send the next reader to exonerated code. */
  FEX::Windows::Arena::SelfTest();
#endif
#ifdef FEX_IOS_HOST
  /* iOS-Madeira ml278: announce the atomic-alias geometry UNCONDITIONALLY, AFTER
   * Logging::Init().
   *
   * The first cut put this line inside IosAtomicWritableAlias (Arm64.cpp), on its first
   * call -- so it could only ever appear if the situation it exists to report had already
   * occurred. ml277 then logged nothing at all, and "the fix works" was indistinguishable
   * from "the helper was never called" and from "the env vars never arrived". A liveness
   * marker that depends on the event it measures is useless; print it at init. */
  {
    const char* RxEnv = getenv("WINE_IOS_JIT_RX");
    const char* SzEnv = getenv("WINE_IOS_JIT_SIZE");
    const char* RwEnv = getenv("WINE_IOS_JIT_RW");
    LogMan::Msg::EFmt("[atomic-alias] LIVE: WINE_IOS_JIT_RX={} SIZE={} RW={} -- atomics whose "
                      "target lands in the pool's RX alias get redirected to RX+WriteOffset",
                      RxEnv ? RxEnv : "<unset>", SzEnv ? SzEnv : "<unset>", RwEnv ? RwEnv : "<unset>");
  }

  /* iOS-Madeira ml293: UNCONDITIONAL BUILD IDENTITY.
   *
   * ml292 could only be attributed to a build by comparing the log's wall-clock start
   * against the install time, because every other marker in the binary is printed from
   * inside the very code path under test -- so "marker absent" meant either "old build"
   * or "new build, event did not fire", with no way to tell them apart. That is the same
   * defect as the [atomic-alias] note above, one level up.
   *
   * __DATE__/__TIME__ are baked at compile time and this line is printed before any guest
   * code runs. Grep it first in every pull.
   *
   * CAVEAT found immediately (ml294): __DATE__/__TIME__ stamp THIS translation unit's
   * compile time, so changing only another .cpp leaves the stamp stale and the ambiguity
   * half-returns. The MADEIRA_REV tag below fixes that: bump it for every deploy, which
   * necessarily edits this file and so refreshes the timestamp too. Self-enforcing. */
/* ml706: this tag went stale -- an ml705 binary still reported ml466, which is
 * exactly the "self-enforcing marker" failure it exists to prevent. The
 * __DATE__/__TIME__ below is compiler-generated and therefore the
 * authoritative identity; if the two disagree, the tag is wrong, not the
 * build. */
#define MADEIRA_REV "ml1132"
  LogMan::Msg::EFmt("[build-id] xtajit64 rev=" MADEIRA_REV " compiled " __DATE__ " " __TIME__);
  LogMan::Msg::EFmt("[xp-api] MADEIRA_FEX_XP={} ({})", IosXpEnabled ? 1 : 0,
                    IosXpEnabled ? "transition probes enabled" : "transition probes disabled");
#ifdef FEX_IOS_HOST
  /* ml751: flush the VA band selector's beacons.
   *
   * ios_fex_band_select() runs during rpmalloc init, before the ARM64EC TEB
   * exists, so it cannot call a logging API -- ml750 tried OutputDebugStringA
   * there and crashed every launch with x18=0x0. It appends to a plain byte
   * buffer instead; this is the first point where LogMan is known to work, as
   * the build-id line above demonstrates. Without this the selector that
   * decides where FEX's arena lives reports nothing at all. */
  {
    if (ios_va_log_len > 0) {
      ios_va_log[ios_va_log_len] = 0;
      LogMan::Msg::EFmt("[va-profile] ml751 deferred selector log ({} bytes):\n{}",
                        ios_va_log_len, ios_va_log);
    } else {
      LogMan::Msg::EFmt("[va-profile] ml751 selector emitted NOTHING -- it did not run");
    }
  }
#endif
#ifdef FEX_IOS_HOST
  {
    const uint32_t Off = IosTebTsdOffset;
    const bool Found = IosTebTsdImportFound != 0;
    LogMan::Msg::EFmt("[fex-tsd] imported offset={:#x} (ntdll export {})", Off,
                      Found ? "found" : "MISSING");
  }
#endif
#endif
=======
  FEX::Windows::SetupThreadHandlers();
  const auto ExecutablePath = FEX::Windows::GetExecutableFilePath();
  AppConfigName = FEX::Windows::BaseName(ExecutablePath);
  FEX::Config::LoadConfig(AppConfigName, _environ, FEX::ReadPortabilityInformation());
  FEXCore::Config::ReloadMetaLayer();
  FEX::Windows::Logging::Init();
  FEXCore::Config::Set(FEXCore::Config::CONFIG_APP_FILENAME, ExecutablePath);
  FEXCore::Config::Set(FEXCore::Config::CONFIG_APP_CONFIG_NAME, AppConfigName);
>>>>>>> 179b4423a (Wine: Change behaviour around SMC and NtReadFile)

--- Source/Windows/ARM64EC/Module.cpp:1883
<<<<<<< HEAD
  ThreadCreationMutex.lock();
  CTX->GetCodeInvalidationMutex().lock();
  if (InvalidationTracker->BeginUntrackedWriteLocked(reinterpret_cast<uint64_t>(Address), static_cast<uint64_t>(Size))) {
    GetFrontendThreadData(ThreadState)->InLockedRWXRead = true;
    *InLockedRead = true;
  } else {
    CTX->GetCodeInvalidationMutex().unlock();
    ThreadCreationMutex.unlock();
=======
  // On file read this can cause JIT code invalidations. FEX previously used to hold locks on `CodeInvalidationMutex` and `ThreadCreationMutex` during
  // this. This was a problem that file reads are blocking and may necessarily never return. A simple unit-test that holds a blocking read, then tries
  // to create threads or JIT code would immediately hang.
  //
  // - PhysX msiexec installer (From Dishonored AppID 205100) did exactly this and hung forever.
  //
  // The alternative approach is to let the read progress as normal and only notify invalidation when the read actually returns. This introduces an
  // unavoidable race which by my logic is fine.
  //
  // 1) If a thread is already jitting code from the address range then in a "real" x86 environment it may see old, new, or a combination of both.
  //   - NtReadFile isn't guaranteed to return new data atomically, in order, or anything of the sort.
  // 2) The calling thread only ever sees the before code, and the after code because `NtReadFile` never leaves "kernel" space before invalidation.
  //
  // The only "gotcha" is that FEX could have write-protected the memory region when a JIT executed the code, and once the `NtReadFile` tries
  // returning data, it faults. This requires coordination between Wine and FEX in order to handle this edge case.
  //
  // Additionally, async reads are still completely untracked with invalidation, which never worked before anyway.

  if (AppConfigName == "P5R.exe") {
    // Use the locking mechanism for (Persona 5 Royal AppID 1687950) which does exactly the problem described above.
    auto& InLockedRWXRead = GetFrontendThreadData(ThreadState)->InLockedRWXRead;
    if (!After) {
      ThreadCreationMutex.lock();
      CTX->GetCodeInvalidationMutex().lock();
      if (InvalidationTracker->BeginUntrackedWriteLocked(reinterpret_cast<uint64_t>(Address), static_cast<uint64_t>(Size))) {
        InLockedRWXRead = true;
      } else {
        CTX->GetCodeInvalidationMutex().unlock();
        ThreadCreationMutex.unlock();
      }
    } else {
      if (InLockedRWXRead) {
        InLockedRWXRead = false;
        CTX->GetCodeInvalidationMutex().unlock();
        ThreadCreationMutex.unlock();
      }
    }

    return;
  }

  if (After && Status == STATUS_SUCCESS) {
    std::scoped_lock Lock(ThreadCreationMutex);
    InvalidationTracker->InvalidateAlignedInterval(reinterpret_cast<uint64_t>(Address), static_cast<uint64_t>(Size), false);
>>>>>>> 179b4423a (Wine: Change behaviour around SMC and NtReadFile)

=== FILE Source/Windows/WOW64/Module.cpp
--- Source/Windows/WOW64/Module.cpp:2158
<<<<<<< HEAD
  // MADEIRA: wow64.dll calls this from NtReadFile, which the 32-bit loader uses before this
  // thread has any FEX state - so the slot read has to be the hardened one, and a thread with no
  // state has no RWX read to track. Same reasoning as the exception handler.
  auto* NotifyThread = GetTLS().ThreadStateIfInitialised();
  if (!NotifyThread) {
    return;
  }
  auto& InLockedRWXRead = GetFrontendThreadData(NotifyThread)->InLockedRWXRead;
  if (!After) {
    ThreadCreationMutex.lock();
    CTX->GetCodeInvalidationMutex().lock();
    if (InvalidationTracker->BeginUntrackedWriteLocked(reinterpret_cast<uint64_t>(Address), static_cast<uint64_t>(Size))) {
      InLockedRWXRead = true;
    } else {
      CTX->GetCodeInvalidationMutex().unlock();
      ThreadCreationMutex.unlock();
    }
  } else {
    if (InLockedRWXRead) {
      InLockedRWXRead = false;
      CTX->GetCodeInvalidationMutex().unlock();
      ThreadCreationMutex.unlock();
    }
=======
  auto* ThreadState = GetTLS().ThreadState();
  if (!InvalidationTracker || !ThreadState) {
    return;
  }

  // See arm64ec `BTCpu64NotifyReadFile` for why this like this.
  if (After && Status == STATUS_SUCCESS) {
    std::scoped_lock Lock(ThreadCreationMutex);
    InvalidationTracker->InvalidateAlignedInterval(reinterpret_cast<uint64_t>(Address), static_cast<uint64_t>(Size), false);
>>>>>>> 179b4423a (Wine: Change behaviour around SMC and NtReadFile)
```

**What the fork side of each hunk does.**

- **ARM64EC `ProcessInit` hunk** (`Module.cpp:944-1038`).
  - The fork side is iOS start-up diagnostics and setup:
    - the `MADEIRA_FEX_XP` switch
    - the `[atomic-alias] LIVE` and `[build-id] ... rev=ml1132` lines
      (ml278/ml293/ml706)
    - the deferred `[va-profile]` band-selector log (ml751, `053c385ec`)
    - the `[fex-tsd]` TEB slot report (`d21da240b`)
  - Blame: `49a7b55ec` "ios: #52 pool-alias RIP redirect..." (Will Faust),
    `053c385ec`, `38dddb320` "Allocator and call-return stack hardening",
    `f5a5349ec`, `c77105fa3`, `707f213f5`.
  - The upstream side is not about NtReadFile. It is upstream's newer
    `ProcessInit` prologue: `SetupThreadHandlers()`, which **does not exist in
    the fork** (it came with upstream `c038bb579`), plus `CONFIG_APP_FILENAME`
    from `681636cd6`.
  - **Keep the fork side.** Add only `AppConfigName = ...;` (and optionally
    the two `FEXCore::Config::Set` lines). Do not take upstream's lines
    verbatim.
- **ARM64EC `BTCpu64NotifyReadFile` hunk** (`Module.cpp:1871-1878`, function
  at 1851).
  - The fork rewrote the function, `90a301584` "iOS-Mythic: #60 drain
    self-repair + L1 cap + EC exception hardening (ml389-ml417)". There are
    two protections:
    - **ml411**: the "after" call checks the held flag *before* any early
      return. If the old order was kept and `ThreadState` was gone, the
      unlock was skipped, `CodeInvalidationMutex` stayed write-owned forever,
      and the Steam webhelper stalled ("webhelper is not responding").
    - **ml412**: the held flag is mirrored in a raw TEB slot,
      `Instrumentation[9]` at `TEB+0x1700`, not a `thread_local`, because
      mingw TLS is NULL during the loader's first NtReadFile (a crash at
      `BTCpu64NotifyReadFile+0x3c`).
  - `BTCpu64IosReleaseThreadHolds` (ml612, rule 3, `Module.cpp:1918-1988`)
    reads that slot. It drops the exclusive pair if a thread dies mid-read.
  - Upstream's side has none of this. Its P5R branch is the old unhardened
    version.
  - If upstream's semantics are adopted, the post-read path holds no lock
    across the read, so ml411/ml412/ml612 become relevant only to whatever
    pre-read locking is kept (the P5R branch, if kept).
- **WOW64 `BTCpuNotifyReadFile` hunk** (`Module.cpp:2158-2180`).
  - The fork uses `GetTLS().ThreadStateIfInitialised()`, because wow64.dll
    calls this from NtReadFile before the 32-bit thread has FEX state (the
    32-bit loader). Blame: `707f213f5` and `b8cd63b23` "FEX WOW64: run 32-bit
    guests on the iOS host" (125hz).
  - Upstream uses `GetTLS().ThreadState()`. Keep the hardened accessor
    whatever else is chosen.

**Dependencies.**
- `AppConfigName` must be assigned in `ProcessInit`. Upstream does it in
  `681636cd6` ("Win32: Actually set app config path and name", PR #5794).
- `681636cd6` itself conflicts in both modules, in the same `ProcessInit`
  prologues. The fork moved the WOW64 `ExecutablePath` into a named
  `fextl::string` (a Madeira fix for a dangling `BaseName` view), and the
  ARM64EC side has the `MADEIRA_FEX_XP` block.
- Its conflicts, verbatim:

```text
=== FILE Source/Windows/ARM64EC/Module.cpp
--- Source/Windows/ARM64EC/Module.cpp:944
<<<<<<< HEAD
#ifdef FEX_IOS_HOST
  {
    const char* Xp = getenv("MADEIRA_FEX_XP");
    IosXpEnabled = Xp && Xp[0] == '1' && Xp[1] == 0;
  }
#endif
  const auto ExecutableName = FEX::Windows::BaseName(FEX::Windows::GetExecutableFilePath());
=======
  const auto ExecutablePath = FEX::Windows::GetExecutableFilePath();
  const auto ExecutableName = FEX::Windows::BaseName(ExecutablePath);
>>>>>>> 681636cd6 (Win32: Actually set app config path and name)

=== FILE Source/Windows/WOW64/Module.cpp
--- Source/Windows/WOW64/Module.cpp:1087
<<<<<<< HEAD

#ifdef FEX_IOS_HOST
  /* MADEIRA: import the TEB TSD slot offset before anything reads a TEB. A missing or zero export
   * is fatal rather than a fallback - the fallback path (x18) is exactly the thing that is
   * unreliable, and a wrong TEB here silently corrupts every thread's TLS slots. BTCpuProcessInit
   * has no way to report failure to wow64.dll, so this is loud on stderr and then refuses to
   * continue rather than limping. */
  {
    const auto NtDllForTsd = GetModuleHandle("ntdll.dll");
    const auto Published = reinterpret_cast<uint32_t*>(GetProcAddress(NtDllForTsd, "ios_teb_tsd_offset"));
    IosTebTsdImportFound = Published != nullptr ? 1u : 0u;
    if (Published && *Published) {
      FEX::Windows::WOW64::IosTebTsdOffset = *Published;
      // Parity with ARM64EC/Module.cpp:841 - see the declaration near the top of this file.
      ::IosTebTsdOffset = *Published;
    }
    /* ml800: this is the FIRST thing that can fail in this module and it runs before LogMan
     * exists, so report the whole import unconditionally through the raw path. Which of the three
     * failure modes it is - no ntdll handle, no export, or an export the unix side never
     * published into THIS pseudo-process's ntdll .data copy - decides where the fix goes, and the
     * FIFTH DEVICE RUN could not tell them apart because the message never left the module. */
    IosRawReport("E [wow64-init] teb-tsd: ntdll={} export={} found={} value={:#x}", static_cast<void*>(NtDllForTsd),
                 static_cast<void*>(Published), IosTebTsdImportFound, FEX::Windows::WOW64::IosTebTsdOffset);
    if (!FEX::Windows::WOW64::IosTebTsdOffset) {
      IOS_WOW64_REPORT_AND_DIE("[FEX-iOS][wow64] ntdll!ios_teb_tsd_offset missing or zero (ntdll={} export={}) - "
                               "refusing to run with an unreliable TEB",
                               static_cast<void*>(NtDllForTsd), static_cast<void*>(Published));
    }
  }
#endif

  // MADEIRA: keep the path alive; BaseName returns a view into it (it used to point into a
  // temporary that was already destroyed).
  const fextl::string ExecutablePath = FEX::Windows::GetExecutableFilePath();
=======
  const auto ExecutablePath = FEX::Windows::GetExecutableFilePath();
>>>>>>> 681636cd6 (Win32: Actually set app config path and name)
```

**Resolution size.** **Design decision.** The choices:
- (a) keep the fork's behaviour;
- (b) take upstream's post-read invalidation for ARM64EC and WOW64, drop or
  keep the P5R branch, and accept the write-protected-page failure mode;
- (c) a hybrid: keep the un-trap and invalidate before the read, but release
  the locks before the blocking read and re-invalidate after it. Wine's write
  check then passes and nothing is held across I/O. This needs new FEX code
  written by a human.

The mechanical part (the `ProcessInit` hunks and the WOW64 accessor) is small.
Measure first: count the reads where `BeginUntrackedWriteLocked` returns true,
and the time held, during RDR2's load.

---

## 3. `61d94ac11` Potential improvement for *PMULHRSW

Upstream PR #5904, "Faster translation for *PMULHRSW", by jubecker.

**What upstream changes and why.**
- A new IR op lowers PMULHRSW to `smull`/`smull2` plus a rounding narrow
  `rshrn`/`rshrn2 #15`, instead of `sshr #14 / add 1 / shrn #1`.
- From the commit's own InstCountCI update:
  - `pmulhrsw xmm0, xmm1` goes from **11 to 4** host instructions.
  - `pmulhrsw mm0, mm1` goes from **12 to 9**.
  - The VEX/AVX128 forms shrink the same way.

**Why it would help RDR2.**
- PMULHRSW is SSSE3 Q15 fixed-point multiply, common in audio mixing, video
  decode and image filters.
- The win is a few instructions per use, so it matters only if a hot loop
  uses it.
- ALU only; TSO is irrelevant.

**Conflicts.**
- The code (`JIT/VectorOps.cpp`, `OpcodeDispatcher/Vector.cpp`, `IR.json`),
  the asm test, `AVX128/VEX_map2.json` and `H0F38.json` apply cleanly.
- Only `unittests/InstructionCountCI/VEX_map2.json` conflicts, in one region.
  That region is the **SVE256** expectation (`z` registers). Apple CPUs have
  no SVE, so this expectation never corresponds to device code.

```text
=== FILE unittests/InstructionCountCI/VEX_map2.json
--- unittests/InstructionCountCI/VEX_map2.json:321
<<<<<<< HEAD
        "asr z2.s, p7/m, z2.s, #14",
        "asr z3.s, p7/m, z3.s, #14",
        "mov z4.s, #1",
        "add z2.s, z2.s, z4.s",
        "add z3.s, z3.s, z4.s",
        "shrnb z2.h, z2.s, #1",
        "uzp1 z2.h, z2.h, z2.h",
        "shrnb z1.h, z3.s, #1",
        "uzp1 z1.h, z1.h, z1.h",
        "movprfx z16, z2",
        "splice z16.h, p6, z16.h, z1.h"
=======
        "rshrnb z0.h, z2.s, #15",
        "rshrnb z1.h, z3.s, #15",
        "uzp1 z16.h, z0.h, z1.h"
>>>>>>> 61d94ac11 (Potential improvement for *PMULHRSW)
```

**Fork side.** It is older upstream expectation text (blame `707f213f5`). No
fork behaviour is involved.

**Dependencies.** None.

**Resolution size.** Mechanical: one JSON hunk. Take upstream's side or
regenerate (see "InstCountCI JSON files" above).

---

## 4. `ba800514b` wow64/arm64ec: Stop tracking reserve-only allocations

Upstream PR #6139, by Ryan Houdek.

**What upstream changes and why.**
- In `NotifyMemoryAlloc`, `HandleMemoryProtectionNotification` is skipped
  when `(Type & (MEM_COMMIT | MEM_RESERVE)) == MEM_RESERVE`: reserved, not
  committed, so nothing can use the memory.
- Commit message: "Removes some overhead down the line." There are no numbers.

**Why it would help RDR2.**
- Fewer entries in the invalidation tracker's interval lists, so cheaper
  executable-range queries during decode and SMC handling.
- It matters only for reserve-only allocations with executable protection.
- Small, but safe: a later `MEM_COMMIT` is still tracked.

**Conflicts.** One region in `Source/Windows/ARM64EC/Module.cpp`. The WOW64
hunk applies cleanly.

```text
=== FILE Source/Windows/ARM64EC/Module.cpp
--- Source/Windows/ARM64EC/Module.cpp:1665
<<<<<<< HEAD
  if (After) {
    // MEM_RESET(_UNDO) ignores the passed permissions
    if (!Status && !(Type & (MEM_RESET | MEM_RESET_UNDO))) {
=======
  if (!After) {
    ThreadCreationMutex.lock();
  } else {
    // MEM_RESET(_UNDO) ignores the passed permissions.
    // MEM_RESERVE doesn't actually allow anything to use the memory.
    const bool WasSuccess = Status == STATUS_SUCCESS;
    const bool IsNotReset = (Type & (MEM_RESET | MEM_RESET_UNDO)) == 0;
    // (Type & MEM_COMMIT) = Need to track
    // (Type & (MEM_COMMIT | MEM_RESERVE)) = Need to track
    // (TYPE & MEM_RESERVE) = No need to track
    const bool IsNotReserveWithoutCommit = (Type & (MEM_COMMIT | MEM_RESERVE)) != MEM_RESERVE;
    if (WasSuccess && IsNotReset && IsNotReserveWithoutCommit) {
>>>>>>> ba800514b (wow64/arm64ec: Stop tracking reserve only allocations)
```

**Fork side.**
- The fork's ARM64EC `NotifyMemoryAlloc` (`Module.cpp:1660`) **deliberately
  does not take `ThreadCreationMutex`** around the allocation. Upstream's
  hold caused an AB-BA deadlock with Wine's heap lock.
  `docs/audit/FINDINGS.md` lists this as "[open, risk] ... Deliberate trade".
- Blame shows only the root snapshot `707f213f5`. The dropped lock came with
  the iOS port inside that snapshot.

**Dependencies.** None.

**Resolution size.**
- Mechanical: keep the fork's `if (After) { ... }` shape (no lock) and
  replace only the condition with upstream's three `const bool`s.
- Do **not** reintroduce the `if (!After) ThreadCreationMutex.lock();` branch.

---

## 5. `9f7913edb` JIT: Inline 128 bit unsigned divide, with `01b344b44` and `eedb8be51`

Upstream PR #6047, "ALUOps: Inline 128 bit division", by Justin Becker. PR
commits:

- `9f7913edb` (the change)
- `d21ae973a` "Tests: add DIV tests" (asm, applies cleanly per the survey)
- `646c442b8` DiskCache bump (**skip**)
- `01b344b44` "Slightly shorter path for EmitDivllu"
- `eedb8be51` comment nit
- `9cb94e1ef` (InstCountCI JSON)

**What upstream changes and why.**
- `LUDiv`/`LDiv` with a non-zero upper half used to spill and call a C
  helper (`LUDIV`/`LDIV` via `__int128` division) through dispatcher
  trampolines.
- `9f7913edb` emits libdivide's `divllu` (Knuth Algorithm D) inline, signed
  via absolute values, and deletes the helpers, trampolines and four
  `JITPointers` entries.
- Commit message: "Testing on X2 Elite suggests a ~4x speedup by this
  technique."

**Why it would help RDR2 (small).**
- The fork already divides inline when the upper half is 0
  (`ALUOps.cpp`, the `cbz Upper, Only64Bit` path), and that is the common
  compiler-generated `div r64` with `xor edx, edx`.
- Only true 128-by-64 divides (muldiv-style `mul` then `div`, fixed-point
  timing) take the helper today.

**Conflicts.** `FEXCore/Source/Interface/Core/JIT/JIT.cpp` (2 regions) and
`FEXCore/include/FEXCore/Core/CoreState.h` (1 region).
`Dispatcher.cpp`/`.h`, `ALUOps.cpp` and `JITClass.h` apply cleanly, including
removing `LUDIVHandler`/`LDIVHandler` and `EmitLongALUOpHandler`.

```text
=== FILE FEXCore/Source/Interface/Core/JIT/JIT.cpp
--- FEXCore/Source/Interface/Core/JIT/JIT.cpp:64
<<<<<<< HEAD
struct DivRem {
  uint64_t Quotient;
  uint64_t Remainder;
};

static struct DivRem LUDIV(uint64_t SrcHigh, uint64_t SrcLow, uint64_t Divisor) {
  __uint128_t Source = (static_cast<__uint128_t>(SrcHigh) << 64) | SrcLow;

  return {
    .Quotient = (uint64_t)(Source / Divisor),
    .Remainder = (uint64_t)(Source % Divisor),
  };
}

static struct DivRem
LDIV(uint64_t SrcHigh, uint64_t SrcLow, int64_t Divisor) {
  __int128_t Source = (static_cast<__uint128_t>(SrcHigh) << 64) | SrcLow;

  return {
    .Quotient = (uint64_t)(Source / Divisor),
    .Remainder = (uint64_t)(Source % Divisor),
  };
}

static void
PrintValue(uint64_t Value) {
=======
#ifndef _WIN32

static std::optional<uint64_t>
RDRANDFallback(uint64_t Reseed) {
  uint64_t Value {};
  FHU::Syscalls::getrandom(&Value, sizeof(Value), 0);
  return Value;
}

#else

// Windows does not have an equivalent to getrandom() without dynamically linking
// bcrypt et al. Since this fallback is just for compat it does not need to be
// cryptographic, so instead we vendor SplitMix64 as a naive fallback.

// Reference implementation by Sebastiano Vigna, public domain (CC0)
// https://prng.di.unimi.it/splitmix64.c
static std::atomic<uint64_t>
  RNGState {static_cast<uint64_t>(__builtin_readcyclecounter())};

static std::optional<uint64_t> RDRANDFallback(uint64_t Reseed) {
  const uint64_t State = RNGState.load(std::memory_order_relaxed) + 0x9E3779B97F4A7C15ULL;
  RNGState.store(State, std::memory_order_relaxed);
  uint64_t Value = (State ^ (State >> 30)) * 0xBF58476D1CE4E5B9ULL;
  Value = (Value ^ (Value >> 27)) * 0x94D049BB133111EBULL;
  return Value ^ (Value >> 31);
}
#endif

static void PrintValue(uint64_t Value) {
>>>>>>> 9f7913edb (JIT: Inline 128 bit unsigned divide)

--- FEXCore/Source/Interface/Core/JIT/JIT.cpp:796
<<<<<<< HEAD
    Ptrs.LUDIV = reinterpret_cast<uint64_t>(LUDIV);
    Ptrs.LDIV = reinterpret_cast<uint64_t>(LDIV);
=======
    Ptrs.RDRANDFallback = reinterpret_cast<uint64_t>(RDRANDFallback);
>>>>>>> 9f7913edb (JIT: Inline 128 bit unsigned divide)

=== FILE FEXCore/include/FEXCore/Core/CoreState.h
--- FEXCore/include/FEXCore/Core/CoreState.h:355
<<<<<<< HEAD
  uint64_t LUDIV {};
  uint64_t LDIV {};
=======
  uint64_t RDRANDFallback {};
>>>>>>> 9f7913edb (JIT: Inline 128 bit unsigned divide)
```

**Fork side.**
- It is the old upstream helper code (blame `707f213f5`): `LUDIV`/`LDIV` and
  their `JITPointers` slots.
- The upstream side shows `RDRANDFallback`, only because at upstream's parent
  those lines sat next to the RDRAND fallback from `c91c6efc8`.
- **Resolution: delete the fork's `LUDIV`/`LDIV` definitions, assignments and
  fields, and add nothing.** `RDRANDFallback` must not be added. Neither the
  fork nor this PR uses it.

**Interaction with fork behaviour; check this when resolving.**
- The fork's `DivideErrorCheck` (`OpcodeDispatcher.cpp:3749-3793`, Windows
  only) traps to #DE unless the unsigned dividend's high half is below the
  divisor. That guarantees `divllu`'s precondition (`numhi < den`) on the
  unsigned path.
- The fork's comment says signed IDIV quotient overflow is **not** trapped.
  Both the old helper and the new inline path then produce an undefined
  quotient where x86 would raise #DE, so this is not a regression. But the
  value may differ.

**Follow-ups.**
- In isolation, `01b344b44` and `eedb8be51` conflict, because `EmitDivllu`
  doesn't exist yet: the fork side is empty and the upstream side is the
  whole 150-line function from `9f7913edb`, so it is not repeated here.
- Probe: after applying `9f7913edb`'s `ALUOps.cpp` + `JITClass.h` parts, the
  cherry-pick of `01b344b44 eedb8be51` exits 0 (**clean**).

**Dependencies.** Functionally none. The user-supplied list included
`c91c6efc8`/`9243efc24`/`26f0e5017` (RDRAND fallback). They are textual
neighbours only, and they conflict heavily themselves (Appendix A).

**Resolution size.** Mechanical: delete three blocks. Then rebuild, and run
the DIV asm tests where available.

---

## 6. `19dba4a3e` LookupCache: Add L1 entry on shrink

Upstream PR #5858, by Ryan Houdek.

**What upstream changes and why.**
- After the dynamic L1 is resized, the entry just found in L2 is re-inserted
  into L1. That saves a repeat lookup.
- This is upstream's "on shrink". The code sits in the branch that grows L1
  and recomputes `L1Mask`, which since `40e2abaf8` (already picked) clears L1.

**Why it would help RDR2.** Tiny. Resizes happen at most once per heuristic
window per thread.

**Conflicts.** One region in `FEXCore/Source/Interface/Core/LookupCache.h`.

```text
=== FILE FEXCore/Source/Interface/Core/LookupCache.h
--- FEXCore/Source/Interface/Core/LookupCache.h:535
<<<<<<< HEAD
    auto& L1Entry = reinterpret_cast<LookupCacheEntry*>(L1Pointer)[L1Index(Address) & L1PointerMask];
    L1Entry.GuestCode = Address;
    L1Entry.HostCode = Entry.HostCode;
=======
    AddL1Entry(Address, Entry.HostCode);
>>>>>>> 19dba4a3e (LookupCache: Add L1 entry on shrink)
```

**Fork side; this is the trap.**
- On iOS the fork indexes the L1 by `L1Index(Address) = Address ^ (Address
  >> 17)` (MADEIRA ml2111, `LookupCache.h:476-493`; in the squash
  `f5a5349ec`). The JIT's inline lookups in `Dispatcher.cpp`/`BranchOps.cpp`
  use the same hash.
- The **cleanly merged** part of this commit adds
  `AddL1Entry(GuestAddress, HostCode)` with
  `L1Pointer[GuestAddress & L1PointerMask]`, the **unhashed** index (checked in
  the worktree: it lands near `LookupCache.h:523`).
- On iOS that writes the wrong slot. Lookups never find it, so it is a silent
  no-op, not a crash.
- Taking upstream's side of the conflict is worse. The conflict is in
  `CacheBlockMapping`, the main L1 insert. Upstream's side would route every
  L1 insert through the unhashed index. The C++ `FindBlock` and the JIT's
  inline L1 probes would then almost never hit, and every indirect branch
  would fall through to the L2 or dispatcher path: a silent, process-wide
  performance regression on the device.
- The resolution must make `AddL1Entry` use `L1Index(GuestAddress) &
  L1PointerMask`. The fork's surrounding iOS code (`GrantBigL1` /
  `BigL1Pointer`, ml-era) also has to keep working with the extra parameters.

**Dependencies.** None. `40e2abaf8` and fork `dfcc614901` are already present.

**Resolution size.** Mechanical: one hunk plus a one-token edit in a clean
hunk. The payoff is small. If it is skipped, nothing is lost.

---

## 7. `41241d750` Wow64: Spin loop on atomic with WFE

Upstream PR #5660, by Ryan Houdek.

**What upstream changes and why.**
- `BTCpuSuspendLocalThread` spun on the thread's control word until `IN_JIT`
  cleared. Upstream adds `SpinWaitLock::WaitBitMaskPred` (WFE plus exclusive
  load) and uses it there.
- Commit message: "Instead of burning roughly a million watts, put this
  spinloop on a WFE. This tends to occur on a crash during shutdown."

**Why it would help RDR2.**
- About zero. RDR2 is a 64-bit ARM64EC process, and this is the WOW64
  (32-bit) module.
- It is a power fix for 32-bit processes stuck in that spin.

**Conflicts.** `SpinWaitLock.h` applies cleanly. One region in
`Source/Windows/WOW64/Module.cpp`.

```text
=== FILE Source/Windows/WOW64/Module.cpp
--- Source/Windows/WOW64/Module.cpp:1892
<<<<<<< HEAD
  /* Spin until the JIT is interrupted.
   *
   * ml930: `yield` added. This was a NAKED relaxed-load spin: on iOS the waiter
   * and the JIT thread it is waiting for are frequently on the same cluster and a
   * bare load loop both burns a full core and starves the very thread that has to
   * reach the interrupt check. `yield` is an instruction hint, not a syscall — it
   * costs nothing and never enters the kernel, so it cannot become another
   * swtch_pri source (see the NtYieldExecution finding in WOW64_DESIGN.md §6). */
  while (TLS.ControlWord().load() & ControlBits::IN_JIT) {
    __asm__ volatile("yield" ::: "memory");
  }
=======
  // Spin until the JIT is interrupted
  FEXCore::Utils::SpinWaitLock::WaitBitMaskPred(TLS.ControlWordAddress(), ControlBits::IN_JIT, 0U, std::equal_to<>());
>>>>>>> 41241d750 (Wow64: Spin loop on atomic with WFE)
```

**Fork side.**
- `b8cd63b23` "FEX WOW64: run 32-bit guests on the iOS host" (125hz). ml930
  added a `yield` hint to the bare spin, so the waiter doesn't starve the
  thread it waits for when both share a cluster.
- WFE (upstream) also sleeps the core until the cache line changes, which is
  strictly better for power.
- `WFELoadAtomic` is already used on Apple builds through other
  `SpinWaitLock` users (`Core.cpp`, `CodeCache.cpp`, `Arm64.cpp`).

**Dependencies.** None.

**Resolution size.** Mechanical: replace the loop with upstream's line. The
ml930 comment can go or stay.

---

## 8. Remaining `SRC_RAX`/`DST_RAX` removals

Upstream PRs #5955 (`8cfbc4448` STOS, `b3fe634c8` moffset MOV), #5957
(`1e95181b3` ALU ops, `152314254` CMP/TEST, and the rest in the same series):
`a164315b7` BCD, `ed6d7092a` moffset dst, `d85f5b439` LODS, `9d936a898` ADC,
`574328294` SBB. All by Ryan Houdek.

**What upstream changes and why.**
- These are decoder cleanups: drop the implicit `FLAGS_SF_SRC_RAX`/
  `FLAGS_SF_DST_RAX` operands from the x86 tables and handle RAX explicitly
  in the OpcodeDispatcher (for example `ADCOp(..., bool DestRAX)`).
- The fork already has the earlier ones (XCHG, CQO/CDQE, SCAS, SALC, clzero,
  CPUID, FNSTSW).

**Why it would help RDR2.** No runtime effect expected. The value is fewer
conflicts on future picks that touch `BaseTables.cpp`/`OpcodeDispatcher`.

**Conflicts.**
- In every commit the OpcodeDispatcher code applies cleanly. The conflicts
  are table lines in `FEXCore/Source/Interface/Core/X86Tables/BaseTables.cpp`,
  and for `574328294` also in `OpcodeDispatcher.h` and
  `OpcodeDispatcher/PrimaryGroupTables.h`.
- The upstream side carries two things the fork does not have:
  - `FLAGS_LITERAL_PATCHABLE`, a DiskCache flag from upstream `f9623af07`.
    It appears **0 times** in the fork, so taking it breaks the build.
  - `"REX2"`, an entry name from upstream's APX work.
- The correct resolution of each line: the fork's line **minus**
  `FLAGS_SF_SRC_RAX`/`FLAGS_SF_DST_RAX`, without `FLAGS_LITERAL_PATCHABLE`,
  and with `""` instead of `"REX2"`.
- The regions overlap: `d85f5b439` and `152314254` show the STOS/LODS lines
  because `8cfbc4448` is absent, and `574328294`'s header and table conflicts
  are `9d936a898`'s missing `bool DestRAX`. So apply them in the order listed,
  resolving each before the next.

```text
=== FILE FEXCore/Source/Interface/Core/X86Tables/BaseTables.cpp
--- FEXCore/Source/Interface/Core/X86Tables/BaseTables.cpp:347
<<<<<<< HEAD
    {0xA9, 1, X86InstInfo{"TEST",   TYPE_INST, FLAGS_SF_DST_RAX | FLAGS_SRC_SEXT | FLAGS_DISPLACE_SIZE_DIV_2,                4}},
    {0xAA, 1, X86InstInfo{"STOS",   TYPE_INST, GenFlagsSameSize(SIZE_8BIT) | FLAGS_DEBUG_MEM_ACCESS | FLAGS_SF_SRC_RAX,                   0}},
    {0xAB, 1, X86InstInfo{"STOS",   TYPE_INST, FLAGS_DEBUG_MEM_ACCESS | FLAGS_SF_SRC_RAX,                                   0}},
=======
    {0xA9, 1, X86InstInfo{"TEST",   TYPE_INST, FLAGS_SF_DST_RAX | FLAGS_SRC_SEXT | FLAGS_DISPLACE_SIZE_DIV_2 | FLAGS_LITERAL_PATCHABLE,                4}},
    {0xAA, 1, X86InstInfo{"STOS",   TYPE_INST, GenFlagsSameSize(SIZE_8BIT) | FLAGS_DEBUG_MEM_ACCESS,                   0}},
    {0xAB, 1, X86InstInfo{"STOS",   TYPE_INST, FLAGS_DEBUG_MEM_ACCESS,                                   0}},
>>>>>>> 8cfbc4448 (OpcodeDispatcher: Removes SRC_RAX from STOS)
```
```text
=== FILE FEXCore/Source/Interface/Core/X86Tables/BaseTables.cpp
--- FEXCore/Source/Interface/Core/X86Tables/BaseTables.cpp:152
<<<<<<< HEAD
    {"MOV",    TYPE_INST, FLAGS_SF_SRC_RAX | FLAGS_MEM_OFFSET, 4, { .OpDispatch = &IR::OpDispatchBuilder::MOVOffsetOp } },
    {"MOV",    TYPE_INST, FLAGS_SF_SRC_RAX | FLAGS_MEM_OFFSET, 8, { .OpDispatch = &IR::OpDispatchBuilder::MOVOffsetOp } },
=======
    {"MOV",    TYPE_INST, FLAGS_MEM_OFFSET | FLAGS_LITERAL_PATCHABLE, 4, { .OpDispatch = &IR::OpDispatchBuilder::MOVOffsetOp } },
    {"MOV",    TYPE_INST, FLAGS_MEM_OFFSET | FLAGS_LITERAL_PATCHABLE, 8, { .OpDispatch = &IR::OpDispatchBuilder::MOVOffsetOp } },
>>>>>>> b3fe634c8 (OpcodeDispatcher: Remove SRC_RAX from moffset MOV)
```
```text
=== FILE FEXCore/Source/Interface/Core/X86Tables/BaseTables.cpp
--- FEXCore/Source/Interface/Core/X86Tables/BaseTables.cpp:167
<<<<<<< HEAD
    {"AAD",    TYPE_INST, GenFlagsSameSize(SIZE_8BIT) | FLAGS_SF_DST_RAX, 1, { .OpDispatch = &IR::OpDispatchBuilder::AADOp } },
    {"", TYPE_INVALID, FLAGS_NONE, 0, { .OpDispatch = nullptr } },
=======
    {"AAD",    TYPE_INST, GenFlagsSameSize(SIZE_8BIT), 1, { .OpDispatch = &IR::OpDispatchBuilder::AADOp } },
    {"REX2", TYPE_INVALID, FLAGS_NONE, 0, { .OpDispatch = nullptr } },
>>>>>>> a164315b7 (OpcodeDispatcher: Removes DST_RAX from BCD ops)
```
```text
=== FILE FEXCore/Source/Interface/Core/X86Tables/BaseTables.cpp
--- FEXCore/Source/Interface/Core/X86Tables/BaseTables.cpp:142
<<<<<<< HEAD
    {"MOV",    TYPE_INST, FLAGS_SF_DST_RAX | FLAGS_MEM_OFFSET, 4, { .OpDispatch = &IR::OpDispatchBuilder::MOVOffsetOp } },
    {"MOV",    TYPE_INST, FLAGS_SF_DST_RAX | FLAGS_MEM_OFFSET, 8, { .OpDispatch = &IR::OpDispatchBuilder::MOVOffsetOp } },
=======
    {"MOV",    TYPE_INST, FLAGS_MEM_OFFSET | FLAGS_LITERAL_PATCHABLE, 4, { .OpDispatch = &IR::OpDispatchBuilder::MOVOffsetOp } },
    {"MOV",    TYPE_INST, FLAGS_MEM_OFFSET | FLAGS_LITERAL_PATCHABLE, 8, { .OpDispatch = &IR::OpDispatchBuilder::MOVOffsetOp } },
>>>>>>> ed6d7092a (OpcodeDispatcher: Removes DST_RAX from moffset instructions)
```
```text
=== FILE FEXCore/Source/Interface/Core/X86Tables/BaseTables.cpp
--- FEXCore/Source/Interface/Core/X86Tables/BaseTables.cpp:347
<<<<<<< HEAD
    {0xA9, 1, X86InstInfo{"TEST",   TYPE_INST, FLAGS_SF_DST_RAX | FLAGS_SRC_SEXT | FLAGS_DISPLACE_SIZE_DIV_2,                4}},
    {0xAA, 1, X86InstInfo{"STOS",   TYPE_INST, GenFlagsSameSize(SIZE_8BIT) | FLAGS_DEBUG_MEM_ACCESS | FLAGS_SF_SRC_RAX,                   0}},
    {0xAB, 1, X86InstInfo{"STOS",   TYPE_INST, FLAGS_DEBUG_MEM_ACCESS | FLAGS_SF_SRC_RAX,                                   0}},
    {0xAC, 1, X86InstInfo{"LODS",   TYPE_INST, GenFlagsSameSize(SIZE_8BIT) | FLAGS_SF_DST_RAX | FLAGS_DEBUG_MEM_ACCESS,                                                      0}},
    {0xAD, 1, X86InstInfo{"LODS",   TYPE_INST, FLAGS_SF_DST_RAX | FLAGS_DEBUG_MEM_ACCESS,                                                      0}},
=======
    {0xA9, 1, X86InstInfo{"TEST",   TYPE_INST, FLAGS_SF_DST_RAX | FLAGS_SRC_SEXT | FLAGS_DISPLACE_SIZE_DIV_2 | FLAGS_LITERAL_PATCHABLE,                4}},
    {0xAA, 1, X86InstInfo{"STOS",   TYPE_INST, GenFlagsSameSize(SIZE_8BIT) | FLAGS_DEBUG_MEM_ACCESS,                   0}},
    {0xAB, 1, X86InstInfo{"STOS",   TYPE_INST, FLAGS_DEBUG_MEM_ACCESS,                                   0}},
    {0xAC, 1, X86InstInfo{"LODS",   TYPE_INST, GenFlagsSameSize(SIZE_8BIT) | FLAGS_DEBUG_MEM_ACCESS,                                                      0}},
    {0xAD, 1, X86InstInfo{"LODS",   TYPE_INST, FLAGS_DEBUG_MEM_ACCESS,                                                      0}},
>>>>>>> d85f5b439 (OpcodeDispatcher: Remove DST_RAX from LODS)
```
```text
=== FILE FEXCore/Source/Interface/Core/X86Tables/BaseTables.cpp
--- FEXCore/Source/Interface/Core/X86Tables/BaseTables.cpp:207
<<<<<<< HEAD
    {0x04, 1, X86InstInfo{"ADD",    TYPE_INST, GenFlagsSameSize(SIZE_8BIT) | FLAGS_SF_DST_RAX  ,                              1}},
    {0x05, 1, X86InstInfo{"ADD",    TYPE_INST, FLAGS_SF_DST_RAX | FLAGS_SRC_SEXT | FLAGS_DISPLACE_SIZE_DIV_2, 4}},
=======
    {0x04, 1, X86InstInfo{"ADD",    TYPE_INST, GenFlagsSameSize(SIZE_8BIT),                              1}},
    {0x05, 1, X86InstInfo{"ADD",    TYPE_INST, FLAGS_SRC_SEXT | FLAGS_DISPLACE_SIZE_DIV_2 | FLAGS_LITERAL_PATCHABLE, 4}},
>>>>>>> 1e95181b3 (OpcodeDispatcher: Removes DST_RAX from ALUOp operations.)

--- FEXCore/Source/Interface/Core/X86Tables/BaseTables.cpp:222
<<<<<<< HEAD
    {0x0C, 1, X86InstInfo{"OR",     TYPE_INST, GenFlagsSameSize(SIZE_8BIT) | FLAGS_SF_DST_RAX ,                              1}},
    {0x0D, 1, X86InstInfo{"OR",     TYPE_INST, FLAGS_SF_DST_RAX | FLAGS_SRC_SEXT | FLAGS_DISPLACE_SIZE_DIV_2, 4}},
=======
    {0x0C, 1, X86InstInfo{"OR",     TYPE_INST, GenFlagsSameSize(SIZE_8BIT),                              1}},
    {0x0D, 1, X86InstInfo{"OR",     TYPE_INST, FLAGS_SRC_SEXT | FLAGS_DISPLACE_SIZE_DIV_2 | FLAGS_LITERAL_PATCHABLE, 4}},
>>>>>>> 1e95181b3 (OpcodeDispatcher: Removes DST_RAX from ALUOp operations.)

--- FEXCore/Source/Interface/Core/X86Tables/BaseTables.cpp:253
<<<<<<< HEAD
    {0x24, 1, X86InstInfo{"AND",    TYPE_INST, GenFlagsSameSize(SIZE_8BIT) | FLAGS_SF_DST_RAX  ,                              1}},
    {0x25, 1, X86InstInfo{"AND",    TYPE_INST, FLAGS_SF_DST_RAX | FLAGS_SRC_SEXT | FLAGS_DISPLACE_SIZE_DIV_2, 4}},
=======
    {0x24, 1, X86InstInfo{"AND",    TYPE_INST, GenFlagsSameSize(SIZE_8BIT),                              1}},
    {0x25, 1, X86InstInfo{"AND",    TYPE_INST, FLAGS_SRC_SEXT | FLAGS_DISPLACE_SIZE_DIV_2 | FLAGS_LITERAL_PATCHABLE, 4}},
>>>>>>> 1e95181b3 (OpcodeDispatcher: Removes DST_RAX from ALUOp operations.)

--- FEXCore/Source/Interface/Core/X86Tables/BaseTables.cpp:266
<<<<<<< HEAD
    {0x2C, 1, X86InstInfo{"SUB",    TYPE_INST, GenFlagsSameSize(SIZE_8BIT) | FLAGS_SF_DST_RAX ,                              1}},
    {0x2D, 1, X86InstInfo{"SUB",    TYPE_INST, FLAGS_SF_DST_RAX | FLAGS_SRC_SEXT | FLAGS_DISPLACE_SIZE_DIV_2, 4}},
=======
    {0x2C, 1, X86InstInfo{"SUB",    TYPE_INST, GenFlagsSameSize(SIZE_8BIT),                              1}},
    {0x2D, 1, X86InstInfo{"SUB",    TYPE_INST, FLAGS_SRC_SEXT | FLAGS_DISPLACE_SIZE_DIV_2 | FLAGS_LITERAL_PATCHABLE, 4}},
>>>>>>> 1e95181b3 (OpcodeDispatcher: Removes DST_RAX from ALUOp operations.)

--- FEXCore/Source/Interface/Core/X86Tables/BaseTables.cpp:279
<<<<<<< HEAD
    {0x34, 1, X86InstInfo{"XOR",    TYPE_INST, GenFlagsSameSize(SIZE_8BIT) | FLAGS_SF_DST_RAX  ,                              1}},
    {0x35, 1, X86InstInfo{"XOR",    TYPE_INST, FLAGS_SF_DST_RAX | FLAGS_SRC_SEXT | FLAGS_DISPLACE_SIZE_DIV_2, 4}},
=======
    {0x34, 1, X86InstInfo{"XOR",    TYPE_INST, GenFlagsSameSize(SIZE_8BIT),                              1}},
    {0x35, 1, X86InstInfo{"XOR",    TYPE_INST, FLAGS_SRC_SEXT | FLAGS_DISPLACE_SIZE_DIV_2 | FLAGS_LITERAL_PATCHABLE, 4}},
>>>>>>> 1e95181b3 (OpcodeDispatcher: Removes DST_RAX from ALUOp operations.)
```
```text
=== FILE FEXCore/Source/Interface/Core/X86Tables/BaseTables.cpp
--- FEXCore/Source/Interface/Core/X86Tables/BaseTables.cpp:225
<<<<<<< HEAD
    {0x14, 1, X86InstInfo{"ADC",    TYPE_INST, GenFlagsSameSize(SIZE_8BIT) | FLAGS_SF_DST_RAX  ,                              1}},
    {0x15, 1, X86InstInfo{"ADC",    TYPE_INST, FLAGS_SF_DST_RAX | FLAGS_SRC_SEXT | FLAGS_DISPLACE_SIZE_DIV_2, 4}},
=======
    {0x14, 1, X86InstInfo{"ADC",    TYPE_INST, GenFlagsSameSize(SIZE_8BIT),                              1}},
    {0x15, 1, X86InstInfo{"ADC",    TYPE_INST, FLAGS_SRC_SEXT | FLAGS_DISPLACE_SIZE_DIV_2 | FLAGS_LITERAL_PATCHABLE, 4}},
>>>>>>> 9d936a898 (OpcodeDispatcher: Removes DST_RAX from ADC)
```
```text
=== FILE FEXCore/Source/Interface/Core/OpcodeDispatcher.h
--- FEXCore/Source/Interface/Core/OpcodeDispatcher.h:378
<<<<<<< HEAD
  void ADCOp(OpcodeArgs, uint32_t SrcIndex);
  void SBBOp(OpcodeArgs, uint32_t SrcIndex);
=======
  void ADCOp(OpcodeArgs, uint32_t SrcIndex, bool DestRAX);
  void SBBOp(OpcodeArgs, uint32_t SrcIndex, bool DestRAX);
>>>>>>> 574328294 (OpcodeDispatcher: Removes DST_RAX from SBB)

=== FILE FEXCore/Source/Interface/Core/OpcodeDispatcher/PrimaryGroupTables.h
--- FEXCore/Source/Interface/Core/OpcodeDispatcher/PrimaryGroupTables.h:12
<<<<<<< HEAD
  {OPD(FEXCore::X86Tables::TYPE_GROUP_1, OpToIndex(0x80), 2), 1, &OpDispatchBuilder::Bind<&OpDispatchBuilder::ADCOp, 1>},
  {OPD(FEXCore::X86Tables::TYPE_GROUP_1, OpToIndex(0x80), 3), 1, &OpDispatchBuilder::Bind<&OpDispatchBuilder::SBBOp, 1>},
=======
  {OPD(FEXCore::X86Tables::TYPE_GROUP_1, OpToIndex(0x80), 2), 1, &OpDispatchBuilder::Bind<&OpDispatchBuilder::ADCOp, 1, false>},
  {OPD(FEXCore::X86Tables::TYPE_GROUP_1, OpToIndex(0x80), 3), 1, &OpDispatchBuilder::Bind<&OpDispatchBuilder::SBBOp, 1, false>},
>>>>>>> 574328294 (OpcodeDispatcher: Removes DST_RAX from SBB)

--- FEXCore/Source/Interface/Core/OpcodeDispatcher/PrimaryGroupTables.h:26
<<<<<<< HEAD
  {OPD(FEXCore::X86Tables::TYPE_GROUP_1, OpToIndex(0x81), 2), 1, &OpDispatchBuilder::Bind<&OpDispatchBuilder::ADCOp, 1>},
  {OPD(FEXCore::X86Tables::TYPE_GROUP_1, OpToIndex(0x81), 3), 1, &OpDispatchBuilder::Bind<&OpDispatchBuilder::SBBOp, 1>},
=======
  {OPD(FEXCore::X86Tables::TYPE_GROUP_1, OpToIndex(0x81), 2), 1, &OpDispatchBuilder::Bind<&OpDispatchBuilder::ADCOp, 1, false>},
  {OPD(FEXCore::X86Tables::TYPE_GROUP_1, OpToIndex(0x81), 3), 1, &OpDispatchBuilder::Bind<&OpDispatchBuilder::SBBOp, 1, false>},
>>>>>>> 574328294 (OpcodeDispatcher: Removes DST_RAX from SBB)

--- FEXCore/Source/Interface/Core/OpcodeDispatcher/PrimaryGroupTables.h:40
<<<<<<< HEAD
  {OPD(FEXCore::X86Tables::TYPE_GROUP_1, OpToIndex(0x83), 2), 1, &OpDispatchBuilder::Bind<&OpDispatchBuilder::ADCOp, 1>},
  {OPD(FEXCore::X86Tables::TYPE_GROUP_1, OpToIndex(0x83), 3), 1, &OpDispatchBuilder::Bind<&OpDispatchBuilder::SBBOp, 1>},
=======
  {OPD(FEXCore::X86Tables::TYPE_GROUP_1, OpToIndex(0x83), 2), 1, &OpDispatchBuilder::Bind<&OpDispatchBuilder::ADCOp, 1, false>},
  {OPD(FEXCore::X86Tables::TYPE_GROUP_1, OpToIndex(0x83), 3), 1, &OpDispatchBuilder::Bind<&OpDispatchBuilder::SBBOp, 1, false>},
>>>>>>> 574328294 (OpcodeDispatcher: Removes DST_RAX from SBB)

=== FILE FEXCore/Source/Interface/Core/X86Tables/BaseTables.cpp
--- FEXCore/Source/Interface/Core/X86Tables/BaseTables.cpp:234
<<<<<<< HEAD
    {0x1C, 1, X86InstInfo{"SBB",    TYPE_INST, GenFlagsSameSize(SIZE_8BIT) | FLAGS_SF_DST_RAX  ,                              1}},
    {0x1D, 1, X86InstInfo{"SBB",    TYPE_INST, FLAGS_SF_DST_RAX | FLAGS_SRC_SEXT | FLAGS_DISPLACE_SIZE_DIV_2, 4}},
=======
    {0x1C, 1, X86InstInfo{"SBB",    TYPE_INST, GenFlagsSameSize(SIZE_8BIT),                              1}},
    {0x1D, 1, X86InstInfo{"SBB",    TYPE_INST, FLAGS_SRC_SEXT | FLAGS_DISPLACE_SIZE_DIV_2 | FLAGS_LITERAL_PATCHABLE, 4}},
>>>>>>> 574328294 (OpcodeDispatcher: Removes DST_RAX from SBB)
```
```text
=== FILE FEXCore/Source/Interface/Core/X86Tables/BaseTables.cpp
--- FEXCore/Source/Interface/Core/X86Tables/BaseTables.cpp:267
<<<<<<< HEAD
    {0x3C, 1, X86InstInfo{"CMP",    TYPE_INST, GenFlagsSameSize(SIZE_8BIT) | FLAGS_SF_DST_RAX  ,                              1}},
    {0x3D, 1, X86InstInfo{"CMP",    TYPE_INST, FLAGS_SF_DST_RAX | FLAGS_SRC_SEXT | FLAGS_DISPLACE_SIZE_DIV_2, 4}},
=======
    {0x3C, 1, X86InstInfo{"CMP",    TYPE_INST, GenFlagsSameSize(SIZE_8BIT),                              1}},
    {0x3D, 1, X86InstInfo{"CMP",    TYPE_INST, FLAGS_SRC_SEXT | FLAGS_DISPLACE_SIZE_DIV_2 | FLAGS_LITERAL_PATCHABLE, 4}},
>>>>>>> 152314254 (OpcodeDispatcher: Removes DST_RAX from CMP/TEST)

--- FEXCore/Source/Interface/Core/X86Tables/BaseTables.cpp:351
<<<<<<< HEAD
    {0xA8, 1, X86InstInfo{"TEST",   TYPE_INST, GenFlagsSameSize(SIZE_8BIT) | FLAGS_SF_DST_RAX ,                                             1}},
    {0xA9, 1, X86InstInfo{"TEST",   TYPE_INST, FLAGS_SF_DST_RAX | FLAGS_SRC_SEXT | FLAGS_DISPLACE_SIZE_DIV_2,                4}},
    {0xAA, 1, X86InstInfo{"STOS",   TYPE_INST, GenFlagsSameSize(SIZE_8BIT) | FLAGS_DEBUG_MEM_ACCESS | FLAGS_SF_SRC_RAX,                   0}},
    {0xAB, 1, X86InstInfo{"STOS",   TYPE_INST, FLAGS_DEBUG_MEM_ACCESS | FLAGS_SF_SRC_RAX,                                   0}},
    {0xAC, 1, X86InstInfo{"LODS",   TYPE_INST, GenFlagsSameSize(SIZE_8BIT) | FLAGS_SF_DST_RAX | FLAGS_DEBUG_MEM_ACCESS,                                                      0}},
    {0xAD, 1, X86InstInfo{"LODS",   TYPE_INST, FLAGS_SF_DST_RAX | FLAGS_DEBUG_MEM_ACCESS,                                                      0}},
=======
    {0xA8, 1, X86InstInfo{"TEST",   TYPE_INST, GenFlagsSameSize(SIZE_8BIT),                                             1}},
    {0xA9, 1, X86InstInfo{"TEST",   TYPE_INST, FLAGS_SRC_SEXT | FLAGS_DISPLACE_SIZE_DIV_2 | FLAGS_LITERAL_PATCHABLE,                4}},
    {0xAA, 1, X86InstInfo{"STOS",   TYPE_INST, GenFlagsSameSize(SIZE_8BIT) | FLAGS_DEBUG_MEM_ACCESS,                   0}},
    {0xAB, 1, X86InstInfo{"STOS",   TYPE_INST, FLAGS_DEBUG_MEM_ACCESS,                                   0}},
    {0xAC, 1, X86InstInfo{"LODS",   TYPE_INST, GenFlagsSameSize(SIZE_8BIT) | FLAGS_DEBUG_MEM_ACCESS,                                                      0}},
    {0xAD, 1, X86InstInfo{"LODS",   TYPE_INST, FLAGS_DEBUG_MEM_ACCESS,                                                      0}},
>>>>>>> 152314254 (OpcodeDispatcher: Removes DST_RAX from CMP/TEST)
```

**Fork side.** It is upstream's older table lines (blame `707f213f5`). No
fork behaviour.

**Dependencies.** Sequential, as listed.

**Resolution size.** Mechanical: about 20 table lines and 3 header/table
lines. It is tedious rather than hard. Zero runtime payoff, so do it last or
skip it.

---

## 9. `229151f87`, `4b418a81a`, `502a1900e`, `26d8ec55f`: syscall and dispatcher

- `229151f87` "Have the Syscall IR op jump to the dispatcher" (PR #5978).
  The Syscall op becomes a block terminator. It spills and branches to a new
  `ThreadDispatchSyscallHandler`, instead of calling the handler inline and
  continuing in the block. Upstream needs this for code invalidation and
  code-buffer reuse while a thread sits in a long syscall.
- `4b418a81a` "Have ThreadRemoveCodeEntry IR op jump to dispatcher"
  (PR #5979). This is the same for the self-invalidation op: it now takes
  `EntryToInvalidate, NewRIP` and ends the block.
- `502a1900e` "Optimize syscall return with L1 lookup" (PR #6060). The commit
  message says "Ever since #5978 this has been a performance regression."
  On AmpereOne a syscall went from about 103 ns to about 45 ns (966k to 2,204k
  syscalls/s).
- `26d8ec55f` "honour TF on the L1 fast path after SYSCALL and CPUID"
  (PR #6112). It adds a TF check to the L1 lookup that `502a1900e` and the
  CPUID-faulting stub (`05293166c`) introduced.

**Why it would help RDR2.**
- It doesn't. Without `229151f87`, the fork's Syscall op already continues
  inline in the block. `502a1900e` only claws back part of the regression
  that `229151f87` introduces, so taking all four is at best neutral and
  likely slower on syscall-heavy guest code.
- They matter only if upstream DiskCache or code-buffer reuse is adopted.

**Conflicts (isolated).** `229151f87`: `BranchOps.cpp`, `OpcodeDispatcher.cpp`,
and `DiskCache.h` (deleted in the fork, modified upstream).

```text
=== FILE FEXCore/Source/Interface/Core/DiskCache.h
=== FILE FEXCore/Source/Interface/Core/JIT/BranchOps.cpp
--- FEXCore/Source/Interface/Core/JIT/BranchOps.cpp:460
<<<<<<< HEAD
  auto Op = IROp->C<IR::IROp_Syscall>();
  // Arguments are passed as follows:
  // X0: SyscallHandler
  // X1: ThreadState
  // X2: Pointer to SyscallArguments

  PushDynamicRegs(TMP1);

  uint32_t GPRSpillMask = ~0U;
  uint32_t FPRSpillMask = ~0U;

  SpillStaticRegs(TMP1, {
                          .GPRSpillMask = GPRSpillMask,
                          .FPRSpillMask = FPRSpillMask,
                        });

  // Now that we are spilled, store in the state that we are in a syscall
  // Still without overwriting registers that matter
  // 16bit LoadConstant to be a single instruction
  // This gives the signal handler a value to check to see if we are in a syscall at all
  LoadConstant(ARMEmitter::Size::i64Bit, ARMEmitter::Reg::r0, GPRSpillMask & 0xFFFF);
  str(ARMEmitter::XReg::x0, STATE, offsetof(FEXCore::Core::CpuStateFrame, InSyscallInfo));

  uint64_t SPOffset = AlignUp(FEXCore::HLE::SyscallArguments::MAX_ARGS * 8, 16);
  sub(ARMEmitter::Size::i64Bit, ARMEmitter::Reg::rsp, ARMEmitter::Reg::rsp, SPOffset);
  for (uint32_t i = 0; i < FEXCore::HLE::SyscallArguments::MAX_ARGS; ++i) {
    if (Op->Header.Args[i].IsInvalid()) {
      continue;
    }
    str(GetReg(Op->Header.Args[i]).X(), ARMEmitter::Reg::rsp, i * 8);
  }

  ldr(ARMEmitter::XReg::x0, STATE, offsetof(FEXCore::Core::CpuStateFrame, Pointers.SyscallHandlerObj));
  ldr(ARMEmitter::XReg::x3, STATE, offsetof(FEXCore::Core::CpuStateFrame, Pointers.SyscallHandlerFunc));
  mov(ARMEmitter::Size::i64Bit, ARMEmitter::Reg::r1, STATE.R());

  // SP supporting move
  add(ARMEmitter::Size::i64Bit, ARMEmitter::Reg::r2, ARMEmitter::Reg::rsp, 0);
  if (!CTX->Config.DisableVixlIndirectCalls) [[unlikely]] {
    GenerateIndirectRuntimeCall<uint64_t, void*, void*, void*>(ARMEmitter::Reg::r3);
  } else {
    blr(ARMEmitter::Reg::r3);
  }

  add(ARMEmitter::Size::i64Bit, ARMEmitter::Reg::rsp, ARMEmitter::Reg::rsp, SPOffset);

  // Result is now in x0
  // Fix the stack and any values that were stepped on
  FillStaticRegs({
    .OptionalReg = ARMEmitter::Reg::r1,
    .OptionalReg2 = ARMEmitter::Reg::r2,
    .GPRFillMask = GPRSpillMask,
    .FPRFillMask = FPRSpillMask,
  });

  // Now the registers we've spilled are back in their original host registers
  // We can safely claim we are no longer in a syscall
  str(ARMEmitter::XReg::zr, STATE, offsetof(FEXCore::Core::CpuStateFrame, InSyscallInfo));

  PopDynamicRegs();

  const auto OSABI = CTX->SyscallHandler->GetOSABI();

  if (OSABI != FEXCore::HLE::SyscallOSABI::OS_GENERIC) {
    // Move result to its destination register.
    // Only if `NORETURNEDRESULT` wasn't set, otherwise we might overwrite the CPUState refilled with `FillStaticRegs`
    mov(ARMEmitter::Size::i64Bit, GetReg(Node), ARMEmitter::Reg::r0);
  }
=======
  SpillStaticRegs(TMP1);

  // Jump to the syscall dispatch handler. We won't return after this.
  ldr(TMP1, STATE, offsetof(FEXCore::Core::CpuStateFrame, Pointers.ThreadDispatchSyscallHandler));
  br(TMP1);
>>>>>>> 229151f87 (FEXCore: Have the Syscall IR op jump to the dispatcher)

=== FILE FEXCore/Source/Interface/Core/OpcodeDispatcher.cpp
--- FEXCore/Source/Interface/Core/OpcodeDispatcher.cpp:97
<<<<<<< HEAD
  auto SyscallOp = _Syscall(Arguments[0], Arguments[1], Arguments[2], Arguments[3], Arguments[4], Arguments[5], Arguments[6]);

  // Generic ABI doesn't store result in RAX.
  if (OSABI != FEXCore::HLE::SyscallOSABI::OS_GENERIC) {
    StoreGPRRegister(X86State::REG_RAX, SyscallOp);
  }

  if (Op->TableInfo->Flags & X86Tables::InstFlags::FLAGS_BLOCK_END) {
    // RIP could have been updated after coming back from the Syscall.
    NewRIP = _LoadContextGPR(GPRSize, offsetof(FEXCore::Core::CPUState, rip));
    ExitFunction(NewRIP);
  }
=======
  _Syscall();
>>>>>>> 229151f87 (FEXCore: Have the Syscall IR op jump to the dispatcher)
```

`4b418a81a`: 8 files, including `DiskCache.h` (deleted in the fork).

```text
=== FILE FEXCore/Source/Interface/Core/Core.cpp
--- FEXCore/Source/Interface/Core/Core.cpp:1166
<<<<<<< HEAD
          Thread->OpDispatcher->_ThreadRemoveCodeEntry();
          Thread->OpDispatcher->ExitFunction(Thread->OpDispatcher->_InlineEntrypointOffset(GPRSize, InstAddress - GuestRIP));
=======

          // Generate a relocatable entry for invalidation purposes.
          auto EntryToInvalidate = Thread->OpDispatcher->_EntrypointOffset(GPRSize, 0);
          auto NewRIP = Thread->OpDispatcher->_EntrypointOffset(GPRSize, InstAddress - GuestRIP);
          // Invalidate and exit the function
          Thread->OpDispatcher->_ThreadRemoveCodeEntry(EntryToInvalidate, NewRIP);
>>>>>>> 4b418a81a (FEXCore: Have ThreadRemoveCodeEntry IR op jump to dispatcher)

=== FILE FEXCore/Source/Interface/Core/DiskCache.h
=== FILE FEXCore/Source/Interface/Core/Dispatcher/Dispatcher.cpp
--- FEXCore/Source/Interface/Core/Dispatcher/Dispatcher.cpp:2940
<<<<<<< HEAD
=======
    Ptrs.ThreadDispatchSyscallHandler = ThreadDispatchSyscallHandler;
    Ptrs.ThreadDispatchRemoveCodeEntry = ThreadDispatchRemoveCodeEntry;
>>>>>>> 4b418a81a (FEXCore: Have ThreadRemoveCodeEntry IR op jump to dispatcher)

=== FILE FEXCore/Source/Interface/Core/Dispatcher/Dispatcher.h
--- FEXCore/Source/Interface/Core/Dispatcher/Dispatcher.h:84
<<<<<<< HEAD
=======
  uint64_t ThreadDispatchSyscallHandler {};
  uint64_t ThreadDispatchRemoveCodeEntry {};
>>>>>>> 4b418a81a (FEXCore: Have ThreadRemoveCodeEntry IR op jump to dispatcher)

=== FILE FEXCore/Source/Interface/Core/JIT/BranchOps.cpp
--- FEXCore/Source/Interface/Core/JIT/BranchOps.cpp:612
<<<<<<< HEAD
  PushDynamicRegs(TMP4);
  SpillStaticRegs(TMP4);
=======
  auto Op = IROp->C<IR::IROp_ThreadRemoveCodeEntry>();

  SpillStaticRegs(TMP1);

  // Store the new RIP to go to.
  str(GetReg(Op->NewRIP).X(), STATE, offsetof(FEXCore::Core::CpuStateFrame, State.rip));

  // Move the entry to ABI before saving state.
  mov(ARMEmitter::Size::i64Bit, ARMEmitter::Reg::r1, GetReg(Op->EntryToInvalidate));
>>>>>>> 4b418a81a (FEXCore: Have ThreadRemoveCodeEntry IR op jump to dispatcher)

--- FEXCore/Source/Interface/Core/JIT/BranchOps.cpp:632
<<<<<<< HEAD
  // TODO: Relocations don't seem to be wired up to this...?
  LoadConstant(ARMEmitter::Size::i64Bit, ARMEmitter::Reg::r1, Entry, CPU::Arm64Emitter::PadType::AUTOPAD);

  ldr(ARMEmitter::XReg::x2, STATE, offsetof(FEXCore::Core::CpuStateFrame, Pointers.ThreadRemoveCodeEntryFromJIT));
  if (!CTX->Config.DisableVixlIndirectCalls) [[unlikely]] {
    GenerateIndirectRuntimeCall<void, void*, void*>(ARMEmitter::Reg::r2);
  } else {
    blr(ARMEmitter::Reg::r2);
  }
  FillStaticRegs();

  // Fix the stack and any values that were stepped on
  PopDynamicRegs();
=======
  // Jump to the invalidate dispatch handler. We won't return after this.
  ldr(ARMEmitter::XReg::x2, STATE, offsetof(FEXCore::Core::CpuStateFrame, Pointers.ThreadDispatchRemoveCodeEntry));
  br(ARMEmitter::XReg::x2);
>>>>>>> 4b418a81a (FEXCore: Have ThreadRemoveCodeEntry IR op jump to dispatcher)

=== FILE FEXCore/Source/Interface/IR/IR.json
--- FEXCore/Source/Interface/IR/IR.json:204
<<<<<<< HEAD
      "ThreadRemoveCodeEntry": {
=======
      "ThreadRemoveCodeEntry GPR:$EntryToInvalidate, GPR:$NewRIP": {
>>>>>>> 4b418a81a (FEXCore: Have ThreadRemoveCodeEntry IR op jump to dispatcher)

=== FILE FEXCore/Source/Interface/IR/IREmitter.cpp
--- FEXCore/Source/Interface/IR/IREmitter.cpp:22
<<<<<<< HEAD
=======
  case OP_THREADREMOVECODEENTRY:
  case OP_SYSCALL:
>>>>>>> 4b418a81a (FEXCore: Have ThreadRemoveCodeEntry IR op jump to dispatcher)

=== FILE FEXCore/include/FEXCore/Core/CoreState.h
--- FEXCore/include/FEXCore/Core/CoreState.h:380
<<<<<<< HEAD
=======
  uint64_t ThreadDispatchSyscallHandler {};
  uint64_t ThreadDispatchRemoveCodeEntry {};
>>>>>>> 4b418a81a (FEXCore: Have ThreadRemoveCodeEntry IR op jump to dispatcher)
```

`502a1900e`: one region in `Dispatcher.cpp`. The upstream side contains
`229151f87`'s whole syscall handler and `4b418a81a`'s
`ThreadDispatchRemoveCodeEntry`.

```text
=== FILE FEXCore/Source/Interface/Core/Dispatcher/Dispatcher.cpp
--- FEXCore/Source/Interface/Core/Dispatcher/Dispatcher.cpp:773
<<<<<<< HEAD
    // Result is now in x0, x1
    if (!TMP_ABIARGS) {
      mov(TMP1, ARMEmitter::XReg::x0);
      mov(TMP2, ARMEmitter::XReg::x1);
=======
    // Fix the stack and any values that were stepped on
    // Syscall result is in any static register that the frontend desired.
    FillStaticRegs({
      .OptionalReg = ARMEmitter::Reg::r1,
      .OptionalReg2 = ARMEmitter::Reg::r2,
    });

    // Now the registers we've spilled are back in their original host registers
    // We can safely claim we are no longer in a syscall
    str(ARMEmitter::XReg::zr, STATE, offsetof(FEXCore::Core::CpuStateFrame, InSyscallInfo));

    {
      // L1 lookup after syscall. Mirrors the implementation in `ExitFunction`.
      // Most syscall instructions just return to the following RIP, so it is highly likely to be in the L1 in tight loops.
      ldr(TMP3, STATE_PTR(CpuStateFrame, State.rip));
      ldp<ARMEmitter::IndexType::OFFSET>(TMP1, TMP2, STATE, offsetof(FEXCore::Core::CpuStateFrame, State.L1Pointer));

      // Calculate (tmp1 + ((ripreg & L1_ENTRIES_MASK) << 4)) for the address
      // L1Mask is pre-shifted.
      and_(ARMEmitter::Size::i64Bit, TMP2, TMP2, TMP3, ARMEmitter::ShiftType::LSL, FEXCore::ilog2(sizeof(LookupCache::LookupCacheEntry)));
      add(TMP1, TMP1, TMP2);

      ldp<ARMEmitter::IndexType::OFFSET>(TMP2, TMP1, TMP1, 0);

      sub(TMP1, TMP1, TMP3);
      (void)cbnz(ARMEmitter::Size::i64Bit, TMP1, &LoopTop);
      br(TMP2);
    }
  }

  {
    // All dynamic and static registers are spilled coming in to this handler.
    // It's also the end of block and RIP might have changed, so we jump directly to the top of the loop.
    ThreadDispatchRemoveCodeEntry = GetCursorAddress<uint64_t>();

    // Arguments are already in x0, x1. Just jump to the handler.
    ldr(ARMEmitter::XReg::x2, STATE, offsetof(FEXCore::Core::CpuStateFrame, Pointers.ThreadRemoveCodeEntryFromJIT));
    if (!CTX->Config.DisableVixlIndirectCalls) [[unlikely]] {
      GenerateIndirectRuntimeCall<void, void*, void*>(ARMEmitter::Reg::r2);
    } else {
      blr(ARMEmitter::Reg::r2);
>>>>>>> 502a1900e (Dispatcher: Optimize syscall return with L1 lookup)
```

`26d8ec55f`: one region in `Dispatcher.cpp`. The upstream side is the
`EmitL1Lookup` lambda from `05293166c` (CPUID faulting).

```text
=== FILE FEXCore/Source/Interface/Core/Dispatcher/Dispatcher.cpp
--- FEXCore/Source/Interface/Core/Dispatcher/Dispatcher.cpp:754
<<<<<<< HEAD
  auto EmitLongALUOpHandler = [&](auto R, auto Offset) {
    auto Address = GetCursorAddress<uint64_t>();
=======
  auto EmitL1Lookup = [&](int rip_offset = 0) {
    // L1 lookup after syscall. Mirrors the implementation in `ExitFunction`.
    // Most syscall instructions just return to the following RIP, so it is highly likely to be in the L1 in tight loops.
    ldr(TMP3, STATE_PTR(CpuStateFrame, State.rip));

    // TF may be set: a single-stepped SYSCALL or CPUID. An L1 hit would jump into a block compiled without the
    // per-instruction single-step trap, and the trap would only be raised at the next dispatcher entry, instructions
    // later. The loop top checks TF and compiles single-step blocks.
    ldrb(TMP1, STATE_PTR(CpuStateFrame, State.flags[X86State::RFLAG_TF_RAW_LOC]));
    (void)cbnz(ARMEmitter::Size::i32Bit, TMP1, &LoopTop);

    ldp<ARMEmitter::IndexType::OFFSET>(TMP1, TMP2, STATE, offsetof(FEXCore::Core::CpuStateFrame, State.L1Pointer));
>>>>>>> 26d8ec55f (Dispatcher: honour TF on the L1 fast path after SYSCALL and CPUID)
```

**Fork side.**
- The fork's inline Syscall emitter (`BranchOps.cpp:460-527`), the
  `_Syscall(args...)` + `StoreGPRRegister(RAX)` + `ExitFunction` sequence, and
  the call-and-return `ThreadRemoveCodeEntry`. All are older upstream code
  (blame `707f213f5`).
- The fork's `ThreadRemoveCodeEntry` + `ExitFunction` in `Core.cpp:1166` is
  the path the fork's full-SMC-detection work depends on (`81edb473c0`,
  `8c56b842de`, `3adb516ef1` in the survey: a branch to itself in writable
  code, and the tail of a split block).
- The fork's `Dispatcher.cpp:754` region is the long-divide trampoline that
  item 5 deletes.

**Dependencies.** Upstream's syscall-IR chain: `66cad978c` "Removes syscall
optimization", `91bbce5a9` "Fixes syscall instruction on Linux", `1a0412969`
"Support ThreadRemoveCodeEntry as relocatable", `05293166c` CPUID faulting,
and `DiskCache.h`. All were classed NO or APPLIES3 in the survey.

**Resolution size.** Large, and a design decision tied to item 10.
Recommendation: skip unless DiskCache is adopted.

---

## 10. `561c32b45` Disk Cache initial implementation (and dependents)

Upstream PR #5832, by Pierre-Loup A. Griffais.

**What upstream changes and why.**
- It serializes compiled blocks to disk and reloads them in later runs.
  The commit says: "Disabled by default, key and versioning still needs work."
- It is the first of **75 commits** touching `DiskCache.*` through
  `fexemu/main`, which add:
  - anonymous-memory caching
  - inline-data detection and patching
  - relocations
  - validation-only mode
  - CRC patching of `ValidateCode`
  - pruning
  - memory-mapped reads
- The survey classed 72 of the 78 DiskCache-related commits NO (they do not
  apply even three-way).

**Why it would help RDR2.**
- About zero now. RDR2 already runs with the fork's cache off. In
  `app/Madeira/Library.swift` (the `socialClub` preset), Rockstar titles get
  `env.MADEIRA_FEX_CODE_CACHE = 0`. The comment says the anti-tamper
  "decrypts the launcher's and the games' code just before it runs and
  re-encrypts it; with cached translations the launcher ran its
  still-encrypted bytes".
- Whether upstream's validation would cope with that is unknown and was not
  tested. The fork's cache already validates an XXH3-128 hash of the guest
  bytes on install (`MadeiraCodeCache.h`).

**Conflicts.** Five files conflict. `Context.cpp`, `CPUBackend.h`, `Core.cpp`
(2 regions), `JIT/JIT.cpp` and `Source/Tools/FEXServer/SquashFS.cpp`. The new
files (`DiskCache.cpp`, `DiskCache.h`), `Config.json.in`, `File.h`,
`WinAPI/IO.cpp` and `winternl.h` apply cleanly.

```text
=== FILE FEXCore/Source/Interface/Context/Context.cpp
--- FEXCore/Source/Interface/Context/Context.cpp:58
<<<<<<< HEAD
=======

bool FEXCore::Context::ContextImpl::RequiresRelocatableConstants() const {
  // Support relocation when generating a cache or when generating reference code for validation
  return CodeCache.IsGeneratingCache || FEXCore::Config::Get_ENABLECODECACHEVALIDATION() || DiskCache.IsWritingDiskCache();
}

>>>>>>> 561c32b45 (Disk Cache initial implementation)

=== FILE FEXCore/Source/Interface/Core/CPUBackend.h
--- FEXCore/Source/Interface/Core/CPUBackend.h:19
<<<<<<< HEAD
#ifdef FEX_IOS_HOST
#include <atomic>
#include <mutex>
#endif
#include "Interface/Core/MadeiraCodeCache.h"
=======
#include <span>

namespace FEXCore::DiskCache {
struct BlobEntryPoint;
}
>>>>>>> 561c32b45 (Disk Cache initial implementation)

=== FILE FEXCore/Source/Interface/Core/Core.cpp
--- FEXCore/Source/Interface/Core/Core.cpp:2314
<<<<<<< HEAD
  // Clear any relocations that might have been generated
  if (!CodeCache.IsGeneratingCache) {
    Thread->CPUBackend->ClearRelocations();
  }

#ifdef FEX_IOS_HOST
  /* ml455: delivery-mode block — return for one-shot execution WITHOUT any
   * publication.  Guest-range registration, the lookup insert and the codemap
   * all take locks an interrupted frame below may own. */
  if (IosUnpublished) {
    return (uintptr_t)CodePtr;
  }
#endif

=======
>>>>>>> 561c32b45 (Disk Cache initial implementation)

--- FEXCore/Source/Interface/Core/Core.cpp:2377
<<<<<<< HEAD
#ifdef MADEIRA_FEX_CODE_CACHE_BUILD
  // ml2400: published and its pages write-protected; queue it for the cache file.
  if (IosCacheEligible && !IosFromCache && NeedsAddGuestCodeRanges) {
    MadeiraCache::Record(this, Thread, GuestRIP, CompiledCode, IosMonoBlock);
  }
#endif

  if (CodeMapWriter) {
    auto Region = SyscallHandler->LookupExecutableFileSection(Thread, GuestRIP);
    if (Region && Region->FileStartVA != 0) {
      CodeMapWriter->AppendBlock(*Region, GuestRIP);
    }
=======
  // Clear any relocations that might have been generated
  if (!CodeCache.IsGeneratingCache) {
    Thread->CPUBackend->ClearRelocations();
>>>>>>> 561c32b45 (Disk Cache initial implementation)

=== FILE FEXCore/Source/Interface/Core/JIT/JIT.cpp
--- FEXCore/Source/Interface/Core/JIT/JIT.cpp:1469
<<<<<<< HEAD
#ifdef MADEIRA_FEX_CODE_CACHE_BUILD
/* iOS-Madeira ml2400: capture the finished block for the persistent code cache.
 * Taken from the temp buffer, i.e. before publication: the published copy gets
 * linked (its exit stubs patched) as soon as it runs, this one never is. Offsets
 * of this compile's relocations are still temp-buffer (= block) relative here;
 * PublishTempCode rebases them onto the code buffer afterwards. Skipped for an
 * ml455 delivery compile: it can interrupt an armed compile on this thread and
 * must not overwrite that compile's capture. */
void Arm64JITCore::MadeiraCaptureBlock(const uint8_t* TempCodeBuffer, size_t Size, size_t PrevNumAllocations) {
  auto& C = MadeiraCapture;
  if (!C.Active || FEXCore::Utils::WritePriorityMutex::IosUnpublishedCompileActive()) {
    return;
  }
  C.Entry = Entry;
  C.Code.assign(TempCodeBuffer, TempCodeBuffer + Size);
  C.Relocs.clear();
  for (size_t Idx = PrevNumAllocations; Idx != Relocations.size(); ++Idx) {
    const auto& R = Relocations[Idx];
    const uint32_t Offset = static_cast<uint32_t>(R.Header.Offset);
    switch (R.Header.Type) {
    case RelocationTypes::RELOC_NAMED_SYMBOL_LITERAL:
      C.Relocs.push_back({Offset, uint32_t(MadeiraCache::RelocKind::ExitLinkerLiteral), uint64_t(R.NamedSymbolLiteral.Symbol)});
      break;
    case RelocationTypes::RELOC_GUEST_RIP_LITERAL:
      C.Relocs.push_back({Offset, uint32_t(MadeiraCache::RelocKind::GuestRIPLiteral), R.GuestRIP.GuestRIP});
      break;
    case RelocationTypes::RELOC_GUEST_RIP_MOVE:
      C.Relocs.push_back({Offset, uint32_t(MadeiraCache::RelocKind::GuestRIPMove), R.GuestRIP.GuestRIP});
      break;
    default:
      // Thunk moves (host library pointers) and anything new: not reproducible in another process.
      C.Uncacheable = true;
      break;
    }
  }
  C.Valid = true;
}

/* iOS-Madeira ml2400: place a cached block. It is copied into a temp buffer,
 * its ExitFunctionLinker literals rewritten for this process (the dispatcher
 * lives at a different pool address every run), and then handed to the very
 * publish tail a fresh compile uses, so code buffer rollover, ResetCallRetStack,
 * the RW-alias copy and the kernel icache flush are identical for both. */
CPUBackend::CompiledCode Arm64JITCore::InstallCachedCode(uint64_t Entry, const uint8_t* Code, uint32_t Size,
                                                         const fextl::vector<uint64_t>& LinkerLiteralOffsets,
                                                         const fextl::vector<std::pair<uint64_t, uint32_t>>& Entries,
                                                         FEXCore::Core::DebugData* DebugData) {
  FEXCORE_PROFILE_SCOPED("Arm64::InstallCachedCode");
  this->Entry = Entry;
  this->DebugData = DebugData;
  this->IR = nullptr;

  const uint32_t DesiredBufferRange = AlignUp(FEXCore::Utils::FEX_PAGE_SIZE * 2 + Size, FEXCore::Utils::FEX_PAGE_SIZE);
  auto TempCodeBufferInfo = TempAllocator.ReownOrClaimBufferWithSize(DesiredBufferRange);
  auto TempCodeBuffer = TempCodeBufferInfo.Ptr;
  if (!TempCodeBuffer || TempCodeBufferInfo.Size < FEXCore::Utils::FEX_PAGE_SIZE + Size) {
    TempAllocator.DelayedDisownBuffer();
    return {};
  }
  SetBuffer(TempCodeBuffer, TempCodeBufferInfo.Size - FEXCore::Utils::FEX_PAGE_SIZE);
#if defined(__APPLE__) || defined(FEX_IOS_HOST)
  SetWriteOffset(0); // heap temp buffer, as in CompileCode
#endif

  memcpy(TempCodeBuffer, Code, Size);
  const uint64_t Linker = GetNamedSymbolLiteral(*CTX, RelocNamedSymbolLiteral::NamedSymbol::SYMBOL_LITERAL_EXITFUNCTION_LINKER);
  for (uint64_t Offset : LinkerLiteralOffsets) {
    memcpy(TempCodeBuffer + Offset, &Linker, sizeof(Linker));
  }

  CodeData.BlockBegin = TempCodeBuffer;
  CodeData.EntryPoints.clear();
  for (const auto& [Guest, HostOffset] : Entries) {
    CodeData.EntryPoints.emplace(Guest, TempCodeBuffer + HostOffset);
  }
  CodeData.Size = Size;
  SetCursorOffset(Size);

  return PublishTempCode(TempCodeBuffer, TempCodeBuffer + sizeof(JITCodeHeader), Size - sizeof(JITCodeHeader), Relocations.size());
}
#endif

=======
CPUBackend::CompiledCode Arm64JITCore::LoadCachedCode(std::span<const uint8_t> HostBytes, std::span<const DiskCache::BlobEntryPoint> EntryPoints) {
  // we stored it aligned, better still be?
  LOGMAN_THROW_A_FMT(HostBytes.size() % 16 == 0, "Needs to be 16B aligned!");
  auto AllocatedInfo = AllocateCodeBufferInSharedCache(HostBytes.size());

  uint8_t* Dest = AllocatedInfo.BufferAllocationOffset;
  memcpy(Dest, HostBytes.data(), HostBytes.size());
  ClearICache(Dest, HostBytes.size());

  CPUBackend::CompiledCode Result;
  Result.BlockBegin = Dest;
  Result.Size = HostBytes.size();
  Result.HostCodeOffset = Dest - CurrentCodeBuffer->Ptr;
  for (const auto& Ep : EntryPoints) {
    Result.EntryPoints[Ep.GuestRIP] = Dest + Ep.HostOffset;
  }
  return Result;
}

>>>>>>> 561c32b45 (Disk Cache initial implementation)

=== FILE Source/Tools/FEXServer/SquashFS.cpp
--- Source/Tools/FEXServer/SquashFS.cpp:87
<<<<<<< HEAD
bool DowngradeRootFSPipeToReadLock() {
  flock lk {
=======
static bool DowngradeRootFSPipeToReadLock() {
  struct flock lk {
>>>>>>> 561c32b45 (Disk Cache initial implementation)
```

**Fork side.**
- **MadeiraCodeCache** (ml2400): `MadeiraCodeCache.cpp` (1,429 lines),
  `MadeiraCodeCache.h`, `MadeiraCodeCacheHooks.h` and
  `Source/Windows/Common/MadeiraCodeCacheIO.cpp`.
  - `JIT.cpp:1468-1549` (functions at 1476 and 1511) is `MadeiraCaptureBlock` and `InstallCachedCode`. They
    capture the pristine temp-buffer block with its relocations, and install
    cached code through the same `PublishTempCode` tail as a fresh compile
    (RW-alias copy, icache flush, code-buffer generation checks).
  - `Core.cpp:2324` records the block for the cache file.
  - `CPUBackend.h` includes `MadeiraCodeCache.h`.
  - Blame: `f5a5349ec` (squash) and `156653c06` "iOS-Mythic: JIT-lock family
    (#74/#75/#80) + code-buffer lifetime hardening".
- `Core.cpp:2281` (the fork side of the first `Core.cpp` region) also carries the **ml455 delivery-mode** early return
  (`IosUnpublished`). It returns a block for one-shot execution without
  publishing it, because publication takes locks that an interrupted frame
  below may own. Any merge must keep it.
- `SquashFS.cpp`: the fork made `DowngradeRootFSPipeToReadLock` non-static.
  Not relevant on iOS.

**Dependencies.** The 74 later DiskCache commits, plus `File.h`/WinAPI
plumbing. Items 8 and 9 grow `FLAGS_LITERAL_PATCHABLE` and relocation
dependencies on it.

**Resolution size.** **Design decision**: one cache or two? Two persistent
caches with different keys, validation and storage, in the same JIT publish
path, is a maintenance cost. Recommendation for RDR2 work: defer.

---

## Appendix A: RDRAND fallback (`c91c6efc8`, `9243efc24`, `26f0e5017`), not needed

Upstream PR #5968, "Add software fallback for RDRAND/RDSEED", by Justin Becker.

- It adds a `SoftwareRNG` config, **default false** after `9243efc24`. When it
  is enabled, the host lacks FEAT_RNG and the host RNG is available, CPUID
  reports RDRAND/RDSEED and the JIT calls a software generator (SplitMix64 on
  Windows).
- Payoff for RDR2: none. It is off by default, and RDR2 does not need RDRAND.
- Only `9f7913edb` (item 5) "needs" it, and only for textual context.
- Its own conflicts touch:
  - the fork's guest-window `GetGuestBase()` (`Context.h`, `d8f9d483c`
    "FEXCore: 32-bit guest-window base register and codegen for the iOS host")
  - the fork's `MADEIRA_FEX_AVX=2` CPUID leaf 7 (`SupportsAVX2()`, fork
    `38f34e840f`)
  - the Apple shims in `FEXHeaderUtils/Syscalls.h`
  - `DiskCache.Init` in `Core.cpp`

Listed for completeness:

<details><summary>c91c6efc8 regions (verbatim)</summary>

```text
=== FILE FEXCore/Source/Interface/Context/Context.h
--- FEXCore/Source/Interface/Context/Context.h:370
<<<<<<< HEAD
  // Host address of guest address 0, see Config.GuestBase. A constant 0 when the build has no
  // guest-window support, so `GetGuestBase() + GuestAddress` compiles to exactly `GuestAddress`.
#ifdef FEX_GUEST_WINDOW
  uint64_t GetGuestBase() const {
    return Config.GuestBase;
  }
#else
  static constexpr uint64_t GetGuestBase() {
    return 0;
  }
#endif
=======
  bool SoftwareRNGEnabled() const {
    return Config.SoftwareRNG() && HostRNGAvailable;
  }
  bool HostRNGAvailable {};
>>>>>>> c91c6efc8 (Software fallback for RDRAND/RDSEED)

=== FILE FEXCore/Source/Interface/Core/CPUID.cpp
--- FEXCore/Source/Interface/Core/CPUID.cpp:673
<<<<<<< HEAD
    Res.eax = 0x0;
    Res.ebx = (1 << 0) |                               // FS/GS support
              (0 << 1) |                               // TSC adjust MSR
              (0 << 2) |                               // SGX
              (SupportsAVX() << 3) |                   // BMI1
              (0 << 4) |                               // Intel Hardware Lock Elison
              (SupportsAVX2() << 5) |                  // AVX2 support (iOS-Madeira: MADEIRA_FEX_AVX=2 hides it)
              (1 << 6) |                               // FPU data pointer updated only on exception
              (1 << 7) |                               // SMEP support
              (SupportsAVX() << 8) |                   // BMI2
              (SupportsEnhancedREPMOVS << 9) |         // Enhanced REP MOVSB/STOSB
              (1 << 10) |                              // INVPCID for system software control of process-context
              (0 << 11) |                              // Restricted transactional memory
              (0 << 12) |                              // Intel resource directory technology Monitoring
              (1 << 13) |                              // Deprecates FPU CS and DS
              (0 << 14) |                              // Intel MPX
              (0 << 15) |                              // Intel Resource Directory Technology Allocation
              (0 << 16) |                              // AVX512-F
              (0 << 17) |                              // AVX512-DQ
              (CTX->HostFeatures.SupportsRAND << 18) | // RDSEED
              (1 << 19) |                              // ADCX and ADOX instructions
              (0 << 20) |                              // SMAP Supervisor mode access prevention and CLAC/STAC instructions
              (0 << 21) |                              // AVX512-IFMA
              (0 << 22) |                              // PCOMMIT (deprecated?)
              (1 << 23) |                              // CLFLUSHOPT instruction
              (1 << 24) |                              // CLWB instruction
              (0 << 25) |                              // Intel processor trace
              (0 << 26) |                              // AVX512-PF
              (0 << 27) |                              // AVX512-ER
              (0 << 28) |                              // AVX512-CD
              (Features.SHA << 29) |                   // SHA instructions
              (0 << 30) |                              // AVX512-BW
              (0 << 31);                               // AVX512-VL
=======
    // TODO: For now, subfunction 1 only exposes AVX-VNNI so we make it conditional
    // on AVX-VNNI support. We should revisit this if/when we add more to this leaf.
    Res.eax = SupportsAVXVNNI;
    Res.ebx = (1 << 0) |                       // FS/GS support
              (0 << 1) |                       // TSC adjust MSR
              (0 << 2) |                       // SGX
              (SupportsAVX() << 3) |           // BMI1
              (0 << 4) |                       // Intel Hardware Lock Elison
              (SupportsAVX() << 5) |           // AVX2 support
              (1 << 6) |                       // FPU data pointer updated only on exception
              (1 << 7) |                       // SMEP support
              (SupportsAVX() << 8) |           // BMI2
              (SupportsEnhancedREPMOVS << 9) | // Enhanced REP MOVSB/STOSB
              (1 << 10) |                      // INVPCID for system software control of process-context
              (0 << 11) |                      // Restricted transactional memory
              (0 << 12) |                      // Intel resource directory technology Monitoring
              (1 << 13) |                      // Deprecates FPU CS and DS
              (0 << 14) |                      // Intel MPX
              (0 << 15) |                      // Intel Resource Directory Technology Allocation
              (0 << 16) |                      // AVX512-F
              (0 << 17) |                      // AVX512-DQ
              (SupportsRAND << 18) |           // RDSEED
              (1 << 19) |                      // ADCX and ADOX instructions
              (0 << 20) |                      // SMAP Supervisor mode access prevention and CLAC/STAC instructions
              (0 << 21) |                      // AVX512-IFMA
              (0 << 22) |                      // PCOMMIT (deprecated?)
              (1 << 23) |                      // CLFLUSHOPT instruction
              (1 << 24) |                      // CLWB instruction
              (0 << 25) |                      // Intel processor trace
              (0 << 26) |                      // AVX512-PF
              (0 << 27) |                      // AVX512-ER
              (0 << 28) |                      // AVX512-CD
              (Features.SHA << 29) |           // SHA instructions
              (0 << 30) |                      // AVX512-BW
              (0 << 31);                       // AVX512-VL
>>>>>>> c91c6efc8 (Software fallback for RDRAND/RDSEED)

=== FILE FEXCore/Source/Interface/Core/Core.cpp
--- FEXCore/Source/Interface/Core/Core.cpp:306
<<<<<<< HEAD
=======

#ifndef _WIN32
  // Check if the kernel supports getrandom().
  uint64_t Probe {};
  HostRNGAvailable = FHU::Syscalls::getrandom(&Probe, sizeof(Probe), 0) == sizeof(Probe);
#else
  HostRNGAvailable = true;
#endif

  DiskCache.Init(this);
>>>>>>> c91c6efc8 (Software fallback for RDRAND/RDSEED)

=== FILE FEXHeaderUtils/FEXHeaderUtils/Syscalls.h
--- FEXHeaderUtils/FEXHeaderUtils/Syscalls.h:105
<<<<<<< HEAD
#elif defined(__APPLE__)
inline int32_t getcpu(uint32_t* cpu, uint32_t* node) {
  if (cpu) *cpu = 0;
  if (node) *node = 0;
  return 0;
}

inline int32_t gettid() {
  uint64_t tid;
  pthread_threadid_np(nullptr, &tid);
  return static_cast<int32_t>(tid);
}

inline int32_t tgkill(pid_t tgid, pid_t tid, int sig) {
  return pthread_kill(pthread_self(), sig);
=======

inline ssize_t getrandom(void* buf, size_t buflen, unsigned int flags) {
  return ::syscall(SYS_getrandom, buf, buflen, flags);
>>>>>>> c91c6efc8 (Software fallback for RDRAND/RDSEED)

=== FILE unittests/FEXLinuxTests/CMakeLists.txt
--- unittests/FEXLinuxTests/CMakeLists.txt:66
<<<<<<< HEAD
    if (ARCHITECTURE_x86_64 AND NOT TEST_NAME STREQUAL "thunk_testlib")
=======
    if(TEST_NAME STREQUAL "rdrand")
    # Force use of the software falllback to excercise that path
    set_property(TEST "${TEST_CASE}.jit.flt" APPEND PROPERTY ENVIRONMENT "FEX_HOSTFEATURES=disablerng")
    endif()

    if(TEST_NAME STREQUAL "vulkan_procaddr")
      set_property(TEST "${TEST_CASE}.jit.flt" APPEND PROPERTY ENVIRONMENT
        "FEX_THUNKCONFIG=${CMAKE_SOURCE_DIR}/Data/CI/VulkanThunks.json;FEX_THUNKHOSTLIBS=${HOSTLIBS_DATA_DIRECTORY}/HostThunks;FEX_THUNKGUESTLIBS=${CMAKE_INSTALL_PREFIX}/share/fex-emu/GuestThunks")
    endif()

    if (ARCHITECTURE_x86_64 AND NOT TEST_NAME STREQUAL "thunk_testlib" AND NOT TEST_NAME STREQUAL "vulkan_procaddr")
>>>>>>> c91c6efc8 (Software fallback for RDRAND/RDSEED)
```

</details>

<details><summary>9243efc24 and 26f0e5017 regions (verbatim)</summary>

```text
=== FILE FEXCore/Source/Interface/Config/Config.json.in
--- FEXCore/Source/Interface/Config/Config.json.in:140
<<<<<<< HEAD
=======
      "SoftwareRNG": {
        "Type": "bool",
        "Default": "false",
        "AffectsCodeGen": "true",
        "Desc": [
          "Emulates RDRAND and RDSEED in software when the host does not implement FEAT_RNG."
        ]
      },
>>>>>>> 9243efc24 (Default RDRAND fallback to false)
```
```text
=== FILE FEXCore/Source/Interface/Core/JIT/JIT.cpp
--- FEXCore/Source/Interface/Core/JIT/JIT.cpp:88
<<<<<<< HEAD
=======
#ifndef _WIN32

static std::optional<uint64_t>
RDRANDFallback(uint64_t Reseed) {
  uint64_t Value {};
  FHU::Syscalls::getrandom(&Value, sizeof(Value), 0);
  return Value;
}

#else

// Windows does not have an equivalent to getrandom() without dynamically linking
// bcrypt et al. Since this fallback is just for compat it does not need to be
// cryptographic, so instead we vendor SplitMix64 as a naive fallback.

// Reference implementation by Sebastiano Vigna, public domain (CC0)
// https://prng.di.unimi.it/splitmix64.c
static std::atomic<uint64_t>
  RNGState {static_cast<uint64_t>(__builtin_readcyclecounter())};

static std::optional<uint64_t> RDRANDFallback(uint64_t Reseed) {
  const uint64_t State = RNGState.load(std::memory_order_relaxed) + 0x9E3779B97F4A7C15ULL;
  RNGState.store(State, std::memory_order_relaxed);
  uint64_t Value = (State ^ (State >> 30)) * 0xBF58476D1CE4E5B9ULL;
  Value = (Value ^ (Value >> 27)) * 0x94D049BB133111EBULL;
  return Value ^ (Value >> 31);
}
#endif

>>>>>>> 26f0e5017 (Fix Linux test failure, and formatting)

=== FILE unittests/FEXLinuxTests/CMakeLists.txt
--- unittests/FEXLinuxTests/CMakeLists.txt:66
<<<<<<< HEAD
    if (ARCHITECTURE_x86_64 AND NOT TEST_NAME STREQUAL "thunk_testlib")
=======
    if(TEST_NAME STREQUAL "rdrand")
      # Force use of the software fallback to exercise that path
      set_property(TEST "${TEST_CASE}.jit.flt" APPEND PROPERTY ENVIRONMENT "FEX_HOSTFEATURES=disablerng;FEX_SOFTWARERNG=1")
    endif()

    if(TEST_NAME STREQUAL "vulkan_procaddr")
      set_property(TEST "${TEST_CASE}.jit.flt" APPEND PROPERTY ENVIRONMENT
        "FEX_THUNKCONFIG=${CMAKE_SOURCE_DIR}/Data/CI/VulkanThunks.json;FEX_THUNKHOSTLIBS=${HOSTLIBS_DATA_DIRECTORY}/HostThunks;FEX_THUNKGUESTLIBS=${CMAKE_INSTALL_PREFIX}/share/fex-emu/GuestThunks")
    endif()

    if (ARCHITECTURE_x86_64 AND NOT TEST_NAME STREQUAL "thunk_testlib" AND NOT TEST_NAME STREQUAL "vulkan_procaddr")
>>>>>>> 26f0e5017 (Fix Linux test failure, and formatting)
```

</details>

## Appendix B: how to reproduce the probes (read-only for the real branch)

```sh
git -C FEX worktree add --detach /tmp/claude-501/fexwt 1a66a7f4c7597f0b17584209b074d53682af69a5
cd /tmp/claude-501/fexwt
# isolated conflict
git cherry-pick --no-commit <sha>; git status --short; git diff
git cherry-pick --abort 2>/dev/null; git cherry-pick --quit 2>/dev/null; git reset -q --hard 1a66a7f4c; git clean -qfd
# PCMPXSTRX chain (upstream JSON taken for the 3 conflicted files):
git show --format= 638329278 -- . ':(exclude)unittests/InstructionCountCI' | git apply --index -3
git checkout 638329278 -- unittests/InstructionCountCI/SSE42_Strings.json \
  unittests/InstructionCountCI/Crypto/H0F38.json unittests/InstructionCountCI/FlagM/HotBlocks.json
git cherry-pick --no-commit ff887adb1 e2f0cf41d 0206479a0 af8770005   # exit 0
# divide follow-ups:
git show --format= 9f7913edb -- FEXCore/Source/Interface/Core/JIT/ALUOps.cpp FEXCore/Source/Interface/Core/JIT/JITClass.h | git apply --index -3
git cherry-pick --no-commit 01b344b44 eedb8be51                         # exit 0
# cleanup
cd /; git -C /Users/iediot/Projects/Madeira/FEX worktree remove /tmp/claude-501/fexwt; git -C /Users/iediot/Projects/Madeira/FEX worktree prune
```

A multi-commit `cherry-pick -n` that stops on a conflict leaves sequencer
state in the worktree's git directory. Run `git cherry-pick --quit` before
the next probe, or it refuses with "cherry-pick is already in progress". That
state lives under `.git/modules/FEX/worktrees/fexwt/` and goes away when the
worktree is removed.
