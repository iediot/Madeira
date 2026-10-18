# Madeira code audit (started 2026-10-18)

Scope: Madeira's own code only. Wine, DXMT and FEX upstream are left alone except
where Madeira changed them or calls into them.

Order: FEX (Madeira's changes vs FEX-2607) -> build/ntdll-unix -> build/wineserver,
build/win32u-unix -> madeira-d3d12 -> app.

Each finding: file:line, what is wrong, how it shows up, severity
(crash / wrong result / leak / perf / dead code), status (open / fixed / not a bug).

## FEX

- [fixed] Source/Windows/Common/ImageTracker.cpp: VolatileMetadata=0 was never
  honoured on ARM64EC; PE volatile metadata always applied, so TSO was off for every
  load not listed. Deadlock's lock-free job queue (rendersystemdx11.dll+0x4aa5e)
  followed a stale NULL. Crash.
- [fixed] Source/Windows/ARM64EC/IosJitAlias.cpp IosJitReverseTranslate: misses were
  never cached, and CompileBlock calls it for every guest RIP (every L1 miss, ~27M in
  a Stickman run). Ordinary x64 code always misses, so each call walked the whole alias
  table (one entry per image ever mapped, never shrinks). Now an address outside the
  span of registered pool copies returns at once. Perf (dispatcher slow path).
- [open, perf] Core.cpp ResetCallRetStack: every code-buffer migration decommits and
  recommits each thread's full 16 MB callret stack (~10k resets per run per the ml610
  counters). Prediction state only; cost is syscalls + refaults. Revisit with numbers.
- [open, noise] Core.cpp InitializeCompiler/CreateThread: six [TI-IC] EFmt lines per
  thread, unconditional (Deadlock: ~100 threads). Error-level logging on a hot-ish path.
- [fixed] Core.cpp CompileBlock: a Thumper/FMOD debug probe ran on every call for
  every game. At seven hard-coded RIPs below 0x140000000 (the default x64 exe base)
  it dereferenced guest RBX, *RBX and a vtable unchecked -> possible crash in any game
  whose code sits there. Removed with the 4-slot per-thread callret tracker (shared
  globals written by every thread every call) and a stale hard-coded pool-range probe.
- [fixed] Core.cpp CompileBlock [CB_SUMMARY]: two shared atomics per call from every
  thread (cache-line contention on the slow path). Now opt-in, MADEIRA_FEX_CB_SUMMARY=1.
- [fixed] ARM64EC/Module.cpp ThreadInit: a scan-and-patch of 8 KB of the shared EnterEC
  dispatcher on every new thread, for a long-fixed emitter fault (always logged
  count=0; it wrote shared code other threads were running), plus ~8 raw WriteFile
  trace lines per thread. Removed.
- [open, risk] ARM64EC/Module.cpp NotifyMemoryAlloc/Free/Protect: the iOS port dropped
  upstream's ThreadCreationMutex hold across the memory operation (AB-BA deadlock with
  Wine's heap lock). Leaves a window where a block can be compiled from memory being
  freed/reprotected before the invalidation lands. Deliberate trade; keep in mind for
  stale-code crashes after unload.
- [fixed] InvalidationTracker.cpp: the ml623 IR capture defaulted to
  mono-2.0-bdwgc.dll+0x4db25b when MADEIRA_IRCAP_RVA was unset, arming host-code
  dumps in every Unity Mono game. Opt-in only now.
- [fixed] InvalidationTracker.cpp: [iOS-xins] (every exec section of every image) and
  [iOS-xrem] via=protect / via=section were uncapped EFmt on memory-map paths. Capped
  at 64 each.
- [fixed] FEX thread setup: ~9 [TI-IC] error-level lines per thread (Deadlock ~900 per
  session) from a long-closed crash hunt. Removed; zero-scrub logs only when stale.

## ntdll-unix

- [fixed] virtual_ios.c ios_trace_image_patch: getenv on every NtProtectVirtualMemory.
- [fixed] thread_ios.c NtSuspendThread/NtResumeThread/get_thread_context: getenv on
  every call (hot for GCs and profilers). Read once.
- [fixed] signal_arm64_ios.c: a Thumper DXGI-range probe dumped full state on EVERY
  fault inside 0x140079000.. / 0x1400e0000.. (any game's exe), uncapped. Removed.
- [fixed, device config] MADEIRA_PERF=1 was left on globally (iPad and iPhone
  madeira.cfg): frame-stat hooks, reporter thread, wineserver event/wait census and
  stack snapshots of busy threads in every game. Off. iPhone also had
  MADEIRA_FEX_LOOKUP_STATS=1 (off). Detroit's PERF_CLASSIFY (5 ms thread sampling) and
  PASS_TIME lines ("remove after") removed.
- [fixed] server_ios.c [xp] probe: every 250 ms in every session (all threads' counters + 2 log lines). Now 2 s unless MADEIRA_XP_FAST=1 or MADEIRA_PERF=1.
