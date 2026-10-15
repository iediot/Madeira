# Runtime stability/performance batch — 2026-10-14

Implemented the first tested cross-game batch from the upstream review and
Detroit investigation. No game executable patch, FEX source change, UI change,
device configuration edit, app build, commit or push.

## Shipped native/source changes

1. **Sampler safety:** pthread lookup precedes suspension; the watchdog resumes
   before entering logging/allocator code. Host checker audits eight windows.
2. **Audio lifetime:** published stream slots use sequentially consistent hazard
   protection plus validation before dereference. Overlapping callbacks take a
   nonblocking gate and emit silence rather than sharing hazard slots. Detach
   unpublishes under the existing device mutex, without waiting for future
   callback epochs. Only streams actually held by a callback are retired to a
   coalesced utility reaper. Started/valid flags are atomic. Real-time code takes
   no mutex, allocates nothing and frees nothing. Other Wine client calls still
   rely on the driver's existing client/timer lifetime contract.
3. **Child startup:** failures before server registration close their owned
   socket copy; descriptor duplication and pthread-start errors clean up their
   own descriptors. Missing JIT RX/RW/SIZE entries are supplied through a
   bounded temporary UTF-16 environment; original entries and parent parameters
   remain unchanged, and the allocation is freed on the shared exit path.
4. **Diagnostic cost:** map/inventory walks run every 30 ~2s cycles instead of
   every five/15. Existing page warming and correctness work are unchanged.
   Optional lean mode skips heavy suspended profiler bursts but retains the
   enabled stuck-store correctness check, counters and fine guest classifier.
   Suspended profilers already required an opt-in before this batch; this is
   not an additional default profiler-disable performance gain.
5. **Self-suspend:** successful NtSuspendThread on the calling thread parks
   through server_select until resumed. Real handles are resolved before the
   suspend request, so no identity query is needed while suspended. Existing
   WoW64 cooperative suspend routing is unchanged.
6. **Profile folders — regression withdrawn:** creating Saved Games redirected
   Detroit from its install-relative `Quantic Dream` saves to an empty profile
   tree. The original 54 files remained intact. Restrict repair to the actual
   user's longstanding AppData/LocalLow only; never create other known folders
   or replace dangling links. Host tests now check absent known folders stay
   absent and fallback saves remain intact. Device recovery backs up both trees
   outside the repo before copying original progress into the new profile path.
7. **MoltenVK memory pressure:** a utility thread samples available process
   headroom every two seconds. Below 512 MiB it lowers cached PSO/library
   capacities to a quarter (floors 128/32, never raising user-selected smaller
   caps), and finished archive retention to half (128 MiB floor). Normal
   capacities return after 30 seconds continuously above 1 GiB. Unknown
   headroom does not initiate trimming and resets the recovery interval.
   Cache references only are released; active shader/pipeline owners and
   command-buffer references remain valid. Finished-archive work is coalesced
   on the existing serial archive writer. Active writers, live textures and
   buffers are untouched; disk cache files stay intact. Logging can be disabled
   independently of protection.

For Detroit's current settings, pressure-mode PSO cap is 256 instead of 1024,
library cap 64 instead of 256, and finished archive budget 768 MiB instead of
1536 MiB **file-equivalent** bytes. These are capacity limits, not a measured
resident-memory saving. Reducing reuse can increase future compilation work;
the purpose is avoiding severe pressure/jetsam, not claiming free FPS.

## Controls and logs

- `env.MADEIRA_JIT_ENV_INHERIT = 0`: disable filling missing child pool vars.
- `env.MADEIRA_WARMER_SWEEP_CYCLES = 5`: former ~10s map-walk cadence.
- `env.MADEIRA_LEAN_DIAG = 1`: rare (~10 min) full walks unless the cadence is
  explicitly overridden; heavy profiler bursts off. No device setting changed.
- `env.MVK_MADEIRA_MEMORY_PRESSURE = 0`: disable adaptive cache capacities.
- `[ios-audio-retire]`: an actual callback held a released stream; disposal
  was deferred instead of blocking the game.
- `[child-boot-fail]`, `[jit-env]`, `[diag-sweep]`, `[lean-diag]`, `[profile]`:
  corresponding paths taken.
- `[mvk-memory-pressure] active=1/0 available_mib=...`: pressure/recovery.
  Existing `[mvk-memory]` and `[mvk-archive-memory]` show effective capacities.

## Verification

- New host tests: check-sampler-safety.py, check-audio-lifetime.py,
  check-process-startup.py, check-runtime-diagnostics-policy.py,
  check-self-suspend.py, check-profile-folders.py and
  tests/moltenvk/test-memory-pressure.py.
- Audio ASan/UBSan: idle/stalled/overlapping callbacks, 1000 unrelated releases
  during a held callback, and 100000 publication/free races. Existing downmix
  and WoW64 dispatch/scratch tests passed.
- Process ASan/UBSan: override preservation, malformed bounds, 10000 temporary
  environments freed and early socket EOF; existing process-exit tests passed.
- Actual PSO/library cache classes tested with reference-counted Foundation
  state/library substitutes: eviction does not invalidate an independently
  retained object; pressure/recovery hysteresis and small/zero caps checked.
- Actual archive class tested for half-budget eviction, concurrent readers and
  preserved files. Prefetch and offline compilation regressions passed.
- `WineProcessBridge.m` passed iPhoneOS-target clang syntax checking (two
  preexisting nested-comment warnings). No Swift source changed.
- Native ntdll build: 42 units succeeded, zero failed; staged archive
  5133592 bytes. MoltenVK built with the prescribed script and staged dylib
  5111928 bytes. The generated MoltenVK patch reverse-apply check passed.

All native build scripts ran with `PATH=/usr/bin:$PATH`. No Xcode app build.

## iPad test / remaining work

User builds/installs once in Xcode. Replay a familiar Detroit section, including
cutscene/scene transitions, save/load and a longer session. Check audio after
backgrounding/interruption. Then launch a D3D11 game and an installer/helper
workflow. Inspect whether 501ms detach timeouts disappear, whether cache
pressure enters/recoveries occur, and compare frame hitches plus footprint with
the previous logs under similar thermal state. Host tests cannot establish
device FPS, sound quality or RAM saved.

Not completed by this batch: full thread-registry generation/reaping redesign,
cross-game launcher QoS detection, JIT dead-process reuse with lifetime proof,
wineserver doorbells/direct protocol changes, first-time asynchronous Metal
library preparation, native Detroit allocator replacement, missing-DLL/LOVE
compatibility packs and graphics correctness fixes. These remain separate
follow-ups; the risky reservation/swap defaults rejected in the review remain
unchanged. Further lifetime/protocol changes should not obscure validation of
this first batch.
