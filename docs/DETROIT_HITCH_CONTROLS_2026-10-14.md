# Detroit hitch follow-up, October 14

## Evidence

Latest read-only snapshot: `DetroitBecomeHuman.exe-2026-10-14_20-19-43.txt`,
copied at 20:59. About 2369 seconds, 467 FPS windows; median window 29.7 FPS,
4.31% of frames over 50 ms, worst interval 546.7 ms. Between elapsed 840–1200s,
median is 21.75 FPS and 21.28% of frames exceed 50 ms. These are not controlled
comparisons with older scenes or frame-rate settings.

Observed synchronous library preparations still reach 406.1 ms. Async job queue
high-water is 2179. Footprint reaches 12262 MiB; thermal changes to serious at
elapsed ~1209s. No old audio detach timeout appears. Final timing milestone
reports 39563 skipped draws; this can explain temporary missing geometry, not
prove the cause of persistent floating hair/stray meshes. Primitive-restart
warnings alone do not establish that an affected draw used a restart index.

## Implemented in this follow-up

- **Device regression / default withdrawn:** the subsequent 21:09 run dropped
  to 1.9 FPS near elapsed 465s. Its 5473.9ms frame coincides with multiple
  5418–5431.5ms producer admission waits near elapsed 472s. There are 3738
  logged admission waits; their 506640.9ms total is across concurrent producers,
  not wall-clock game stall time. This is strong evidence of a new admission
  regression, not just the old shader hitches. Peak footprint was 11905 MiB;
  memory and thermal constraints also remain. Blocking admission is now OFF
  by default. Only explicit `env.MVK_MADEIRA_ASYNC_ADMISSION = 1` enables the
  experiment; do not recommend it for Detroit. A nonblocking ownership/admission
  design is still required to bound backlog without freezing creation threads.
- When explicitly enabled, admission bounds unique queued/running jobs at 256 normally, 64
  under the existing pressure signal. Duplicates share a job even at capacity.
  Producers wait before the scheduler copies a descriptor/adds an owner
  completion. Existing jobs are never canceled to enforce a lowered cap.
  Transient completion/destruction work and caller-owned objects are not a byte
  budget. Duplicate completion subscribers are not separately capped.
- `MVK_MADEIRA_ASYNC_MAX_JOBS` / `MVK_MADEIRA_ASYNC_PRESSURE_JOBS` accept 1–4096;
  pressure cannot raise the normal cap. Invalid values use 256/64. Admission
  logs waits >=5ms as `[mvk-async-admission] producer_wait_ms=...`.
  This is backpressure, not a free performance gain: pipeline creation can
  stall while work drains. Compare overall frame pacing and memory on device.
- Speculative library prefetch avoids new loads while memory pressure is
  active. Actual shader demand is unchanged. New counters distinguish missing
  hints, already-cached libraries, missing/refused disk libraries, pressure
  skips, request admission drops and learning admission drops.
- Opt-in `env.MVK_MADEIRA_ASYNC_NO_SKIP = 1` waits for pending graphics shaders
  instead of omitting their draws. It preserves async creation and urgent
  scheduling. Failed pipelines still fail. This is a diagnostic correctness
  mode and can increase hitches; it is off by default and was NOT enabled on
  the iPad. Compare the same visible defect, then remove the line/restart.

## Verification and device test

ASan/UBSan production-code admission/no-skip tests cover concurrent admission,
duplicate requests, pressure downshift, mutex release on wait and failed
completion. Existing actual prefetch, PSO/library pressure and archive-budget
tests pass. Rebuild native MoltenVK using the handoff script with Apple's PATH
first; no app build, commit, push or save/settings edits.

User Xcode install: replay a familiar Dock-launched scene first with unchanged
settings. Confirm admission reports `enabled=0` and no producer waits. Check peak footprint, compressed
memory and frame pacing. Separately compare no-skip mode in the same scene for
temporary missing geometry. For a persistent visible defect, request a frame
capture while it is present; capture blits can serialize work and mask a sync
race, so retain a normal screenshot too. Do not infer a graphics fix from a
clean captured frame alone.

## Explicitly unfinished

Synchronous library preparation has NOT been moved behind pipeline construction
in this batch. `newMTLRenderPipelineDescriptor` calls conversion/function
creation while reading caller-owned Vulkan state and filling pipeline binding
metadata. Full asynchronous preparation needs bounded owned snapshots, retained
module/layout/cache/render-pass lifetimes, extension/feedback eligibility,
publication synchronization for metadata read before draws, and shutdown/error
tests. Dispatching these calls with borrowed create-info pointers is unsafe.

Persistent mesh/hair correctness, precise barrier narrowing and game-owned live
pipeline retention are still investigations, not fixed by admission limits or
no-skip mode. Do not report all three requested tasks complete.
