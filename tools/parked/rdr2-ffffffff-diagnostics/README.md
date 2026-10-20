# RDR2 "Unknown error FFFFFFFF" diagnostics (parked 2026-10-20)

Taken out of the build once the error stopped after the FEX 2608-2610 upstream
correctness fixes. Apply with `git -C wine apply wine-diagnostics.patch` and
`git apply ntdll-unix-diagnostics.patch` (the latter also carries the short-read
completion in virtual_ios.c, which stayed in the tree; skip that hunk).

What they log (env-gated):
- MADEIRA_AT_TRACE=<program.exe>: [at] queries a guard makes (process/thread/system/
  object info, debug registers, own-image memory queries and protection changes,
  window lookups), [at-exc]/[at-cont] every exception with instruction bytes and
  where the handler resumes, [at-read] failed or short NtReadFile.
- MADEIRA_AT_DUMP=1: when a message box opens, the program image (decrypted, file
  offset = RVA), the thread's stack and the heap it points at, two levels deep, to
  %USERPROFILE%\Documents\madeira-dump-*.bin. [msgbox] lines log the code around
  each return address on the stack.
- MADEIRA_WAKE_TRACE=<seconds>: [wake-trace] who wakes a thread idle that long.
- [single-step]/[debug-regs]: contexts set with the trap flag or debug registers.

Finding: the box came from RAGE's streaming loop (exe+0x26043a8, check at
exe+0x2604640): a device read returning a size other than the one requested is
fatal with code -1.
