# Madeira / RDR2 handoff for Claude

Written October24,2026. Work described below was implemented/tested on October21;
repository state and staged DLL hashes were checked again on October24. This
file consolidates this performance/UI work, not the entire historical fork.
Read it before acting. Older CODEX_HANDOFF.md is useful historical context but
its task ordering and claims about the current dirty tree are stale.

## User priorities and working rules

1. RDR2 stutters, then frame-time lows, then texture streaming quality. General
   game stability/memory improvements matter too. No unrelated UI work now.
2. Follow authorized work through implementation, tests and native builds.
   Do not finish an actionable diagnosis with "we should do X" and wait for
   another "do it". Hand back when device testing, a decision or authority is
   genuinely needed. Send concise progress updates while working.
3. No `xcodebuild` or other full app builds. User builds/installs from Xcode.
   Native libraries MUST be rebuilt/staged by the agent when changed.
4. Always `PATH=/usr/bin:$PATH` for native builds (Apple ar, not GNU ar).
5. Do not generate FEX source. Only upstream cherry-picks if FEX work is needed.
   None of the current uncommitted batch edits FEX or Wine submodule source.
6. No commits/pushes without a fresh explicit request. Batch any suggested
   command; no Co-Authored-By or AI attribution in commit/PR text. Submodule
   changes must be committed/published before recording their pins in root.
7. Preserve user changes to `app/Madeira.xcodeproj/project.pbxproj` and
   `app/Madeira/Info.plist`; exclude them from performance commit suggestions.
8. Do not delete/clean game saves or alter graphics settings as an optimization.
   A previous save-path/graphics reset caused major trouble. Close Madeira on
   iPad before any authorized `madeira-library.json` edit; it overwrites it.
9. No long device-log polling, no hanging large devicectl pulls. User AirDrops
   logs into Downloads. Preserve new logs before analysis, preferably somewhere
   durable outside the repo: the previous /private/tmp archive is now gone.
10. UI, when explicitly requested: SteamOS-style/native glass, no new glows.

Memory directory:
`/Users/iediot/.claude/projects/-Users-iediot-Projects-Madeira/memory/`
Read MEMORY.md, rdr2-perf-oct21.md, rdr2-ffffffff.md, follow-through.md,
no-long-polling.md, no-app-builds.md, batch-commits.md and
no-claude-attribution.md. Some earlier memory claims are corrected here.

## Actual graphics route / configuration

- Tested Steam RDR2: appid1174180, PlayRDR2 via Madeira Dock.
- RDR2 is custom `madeira-d3d12` -> native winemetal -> Metal. It is NOT using
  MoltenVK on this route. DXMT serves D3D11; MoltenVK serves Vulkan games such
  as Detroit. Do not assume a MoltenVK optimization fixes custom D3D12.
- Last logged configuration: async-submit=0 (replay on caller), replay-split=0,
  fence-chain=6, reported VRAM4096MB, pso-warm=2, MADEIRA_PERF=1,
  MADEIRA_PERF_CLASSIFY=1, swap tier8192MB/broad coverage. These are historical
  device observations, not a newly fetched config. No config edits this batch.
- Native experimental PSO warm-up is now OFF by default despite pso-warm=2;
  enabling requires BOTH pso-warm-native=1 AND pso-warm>0. Do not re-enable it
  casually: an improvement was not established and first-use work remains.

## Git state checked October24

Root HEAD:
`fe2d84ee Add swap decommit rotation and diagnostics, native pipeline warm-up,
heap-scoped AIR caching, and shuffled high-resolution library footage`.

Submodule HEADs:

- DXMT: `2ddf975abf98e7e855dd3fafe5a4718664560876`, dirty in TWO files below.
- Wine: `51885939146201f509a2f6169cb7c50c5c623e42`, clean.
- FEX: `a7d7c4c0fa64e5d0e4fc37439b55e501596bc873`, clean.
- Their current local branch is madeira-local-20261013. Do not infer push
  status from these local checks; verify remotes if publication is requested.

Uncommitted tracked root files for this work:

```text
app/Madeira/arm64ec-windows/d3d12.dll
app/Madeira/arm64ec-windows/madeira_d3d12.dll
build/ntdll-unix/perf_ios.c
build/ntdll-unix/server_ios.c
build/ntdll-unix/signal_arm64_ios.c
build/ntdll-unix/virtual_ios.c
madeira-d3d12/src/pe/madeira_d3d12.c
tests/host/check-air-table-cache.py
tests/host/check-pso-warm.py
```

Dirty DXMT files:

```text
dxmt/src/winemetal/unix/pipeline_warm.h
dxmt/src/winemetal/unix/winemetal_unix.c
```

New untracked files include this handoff plus:

```text
docs/GRAPHICS_AUDIT_OCT21.md
docs/RDR2_HITCH_DIAGNOSTICS.md
docs/RDR2_LOG_231619_ANALYSIS.md
tests/host/check-hitch-logging.py
tests/host/check-kuser-handled.py
tests/host/check-typed-view-index.py
```

Root shows `m dxmt` for dirty contents, not a new recorded pin yet. The Xcode
project and Info.plist are also dirty USER files, not part of this batch.
Static archives are staged native build outputs; do not assume git status
lists every rebuilt binary. Run fresh git status before any staging command.

## Changes already committed before this dirty batch

### Earlier launch/correctness fixes

- `00744169`: RDR2 FFFFFFFF root cause fixed: shared win32u raw-input device
  list closed another pseudo-process's handle. Steam Rockstar settings/service
  startup, D3D12 view-map spin/black screen fixes and other launch plumbing.
  The old diagnosis "FEX fixed it" was wrong. See rdr2-ffffffff.md.
- `77242b97`: kernel32 GetTickCount/64 switched to NtQueryPerformanceCounter,
  avoiding the trapped KUSER shared-data clock path; initial eight-entry AIR
  argument-table cache and profiling work. Actual game classifier remained ON
  because explicit game configuration overrides a default-off setting.

### Root fe2d84ee / DXMT2ddf975 batch

- AIR invalidation changed from one descriptor-global version to versions of
  the actually bound shader-visible heaps. Unrelated staging/RTV/DSV writes
  no longer flush all tables. Writer paths/copies covered; protected heap
  registry unregisters before destruction. Existing lifetime/debug bypasses
  preserved. Device AIR hit rate remained low; no proven overall FPS win.
- Swap decommit stopped punching a live file extent under virtual_mutex.
  It remaps fresh zero sparse backing at the same guest address, retires old
  offsets only after successful remap/metadata update, and punches retired
  offsets on a worker. Never asynchronously punch live backing: recommit
  writes could otherwise be erased. Bounded sync fallbacks remain.
- Whole covered split ranges compact to one fresh mapping/record to reduce
  metadata fragmentation. Neighbor ranges preserved, census/fallback/compaction
  counters, slow lookup/discard/mmap/retire and VM-lock timings added.
  Deferred APFS operations can still be slow; they do NOT explain every hitch.
- Native op10 PSO preparation implemented in DXMT: deep-copied descriptors,
  retained functions/device/archive references, two utility workers,128pending
  cap,8archive cap and known256MiB headroom floor. Uses normal native builders,
  releases completed PSOs and relies on Metal caches. Later made opt-in by the
  CURRENT dirty batch because benefit was unproven.
- Classifier report-cost logging measures slow30s reporting/symbolization.
- UI: hero/launch screens use Steam/Epic game footage; high-resolution images,
  shuffled image rotation/crossfades, video completion handling and short
  background resume preservation. No initial hero-art flash when footage is
  selected; launch screen uses pictures only. Hero title smaller, tags removed
  ONLY there, played-ago under Play. Files: Library.swift, LibraryHome.swift,
  DockStartScreen.swift, Store/SteamStore.swift and Store/StoreView.swift.
  See committed check-footage-backdrops.py and docs/RDR2_PERF_OCT21.md.

## Current UNCOMMITTED implementation

### 1. AIR root dependency refinement

`madeira-d3d12/src/pe/madeira_d3d12.c`:

- Per-root-parameter versions, separately graphics/compute, instead of treating
  every root change as invalidating every shader's tables.
- Conservative dependency masks match reflected register kind/space/range;
  include matching parameters of all visibilities because the resolver has an
  ALL-visibility fallback. Signature changes invalidate all parameters.
- Bound heap identities/versions remain in the key. Graphics/compute bindpoint
  explicit; reuse existing layout masks instead of rebuilding every miss.
- Counter for unrelated-root-change reuse; `air-table-cache=0` keeps the old
  uncached builder available for same-build diagnosis. No new GPU allocations.

### 2. Typed-buffer-view hash lookup

Same PE file, adapted narrowly from upstream PR340 commit7236cc5:

- Full key=format/offset/count/SRV-or-UAV; open-addressed index under existing
  view_lock, stable view indices, freed with resource; starts at32views,
  load<=1/2, at most4MiB index/resource. OOM/cap -> original scan.
- `tv-hash=0` diagnostic opt-out. No new view eviction, RGB32 shadow retention,
  broad refresh policy or barrier changes imported.

### 3. KUSER emulation guard

`build/ntdll-unix/signal_arm64_ios.c`: upstream PR402 handled-access guard.
An already-emulated fault must not then rebase KUSER-like register constants.
Correctness change, not an advertised FPS gain. This is NOT FEX source.

### 4. Native warmer opt-in safety

`dxmt/.../pipeline_warm.h`: requires pso-warm-native=1 AND pso-warm>0;
explicit off marker. PE decline log updated. No device config edits.
Earlier warm activity does not prove it caused a later regression; no matched
warm-on/off experiment demonstrated a benefit. Off remains the safe default.

### 5. Hitch instrumentation v1 and v2

- DXMT winemetal_unix.c: slow nextDrawable log includes Metal layer, Wine pid/
  tid, native thread ID and whether the shared frame hook counted it.
- PE: game-owned five-second Present-entry frame windows, API-entry gaps,
  Present timings split into reclaim/flush, control+GPU-latency, setup,
  drawable acquisition and commit. Queue residence vs execution, list/command
  counts, caller ECL, queue-drain and run-ahead throttle slow events.
- PE: first-use PSO lock/library-restore/Metal compile time and shader names;
  slow resource/heap creation shapes and cumulative resource counts.
- virtual_ios.c: read/read-at wrappers preserve original result/errno;
  completed counts/bytes/time/max/errors/short-or-EOF, plus slow call owner,
  fd path, byte count and offset. Profiling-off fast path remains. Does not
  profile every mmap fault, write, socket read or GPU upload.
- perf_ios.c:512static per-thread active-read slots, no per-read allocation;
  overflow reported. Gap snapshots show in-flight reads, PC/LR/run state and
  safe guest RIP resolution, capped16selected threads/32active reads/max1Hz.
  No additional thread suspension or frame-pointer stack walk.
- server_ios.c: zero-completed-Present XP windows trigger snapshots. v1 had a
  real bug: pres_done only incremented in async worker, but RDR2 is synchronous.
- v2 FIX: increment pres_done in shared mad_present_run normal end; remove
  worker increment to avoid double counting. This is Metal-side call
  completion, NOT display/GPU retirement. Trigger policy extracted/tested.
  Last device log confirms v2 snapshots fire. No rendering-policy change.
- Native `[perf]` now says scope=all-processes. Prefer game-owned PE windows
  for RDR2 FPS; older mixed-process p95 comparisons are not game-only proof.

## Research completed, not blindly merged

Read docs/GRAPHICS_AUDIT_OCT21.md. Sampled50recent upstream PRs, older graphics
PRs, newest/top fork lists and10fork default-branch compares; not every branch.
Reviewed vkd3d-proton root-binding dirty state and official D3D12 binding docs.

- Ported only PR340 view hash and PR402 guard above.
- PR403 per-process keyed events: real class of shared-handle bug, deferred
  pending ownership/lifetime/failure-path audit. No proven RDR2 linkage yet.
- PR401 QoS/thread names: deferred without RDR2 thread evidence and A/B.
- PR353 vkd3d/DXVK route: experimental; author had not rendered a device frame
  when inspected. NOT a proven drop-in replacement for custom D3D12.
- No bulk upstream/fork merge, FEX edits or speculative barrier elision.
- Public CrossOver components do not imply proprietary D3DMetal/GameHub source
  is available or redistributable on iOS. Do not claim internals were audited.
- Persistent device-built Metal PSO binary archive is NOT implemented. Native
  builder hooks exist but custom D3D12 does not wire a safe bounded persistent
  device archive into them. Shader-library cache is not equivalent to PSO cache.

## Latest device evidence and corrected interpretation

Latest analyzed file: `PlayRDR2.exe-2026-10-21_23-16-19.txt`,16.8MB.
Detailed metrics/excerpts: docs/RDR2_LOG_231619_ANALYSIS.md.

Game-owned Present-entry windows (weighted rates, NOT physical display FPS):

| Period | FPS | >50ms / intervals | Worst interval |
| --- | ---: | ---: | ---: |
| >=100s |47.31|225 /20,279|6,201.5ms|
| 200–330s |29.25|179 /3,750|6,201.5ms|
| >=330s |53.30|30 /10,671 (0.28%)|174.3ms|

No >300ms game interval after330s. Different scenes/routes/caches mean no
controlled A/B. The user felt the diagnostic build was much smoother, but it
contained no intentional performance fix: do not claim logging made it faster.

Important findings:

1. Preceding22:51 log's89 emitted~100ms drawable-wait samples belonged to
   pid0224/layer0x12b2ba580, NOT RDR2 pid040c/layer0x12b2b9140. Do not use
   mixed-process native FPS/p95/drawable waits as unquestioned RDR2 metrics.
2. First-use batch~200–212s coincides with6.2s game entry gap. Render048c is
   in kevent_id with redacted Apple LR; many parallel PSOs compile, measured
   individual compile times up to1034ms. Final619lazy pipelines /25.2s summed
   across threads. These sums are NOT wall-clock stall time. Near-zero restore
   time points at device PSO construction, not slow metallib reads, for logged
   calls. Exact entire6.2s wait chain is not proven by PC-only snapshots.
3. Recurring~0.7s gaps around244/274/304s: render thread pid040c/tid048c,
   native1569640, WAITING in read+8 with LRwait_select_reply+0x38. Present
   returns~0.0–0.1ms afterward; no caller ECL/presents during sampled zeros,
   negligible disk/page-wait counters, no in-flight profiled asset reads.
   This is Wine object-wait completion, NOT proof wineserver is buggy: the
   game's producer/event/fence/timer may be delayed elsewhere.
4. IMPORTANT CORRECTION to earlier wording: some other game PCs at these gaps
   were Mach state4=UNINTERRUPTIBLE, NOT actively running. State1=RUNNING,
   state3=WAITING. Registers/state are separate live samples, not atomic.
   VM/page-in or other kernel wait remains plausible even with low aggregate
   page-wait counters. Do NOT narrow the investigation to server scheduling.
   RDR2 base0x7175530000; captured state4 RVAs:
   tid0410:0x7df911; tid04e8:0x257f854/0x257daba;
   tid05a0:0x2545d8d/0x2ea152a; tid0454:0x281687a.
   These are addresses, not decompiled function identities or proven culprits.
5. Slow deferred APFS punches don't explain every periodic gap. No slow
   classifier-report marker in this run. Four slow asset reads were startup
   audio only,21–28ms; no slow heap/allocation markers. Sampled-marker absence
   is not a blanket proof against I/O/memory causes.
6. All12 new snapshots cost43.87ms total,2.64–5.17ms each. They don't explain
   the~700ms gap duration; overall profiling overhead is still not zero.

## Raw log availability — checked October24

The old `/private/tmp/madeira-rdr2-oct21.6xlOQM/` archive and temporary build
logs NO LONGER EXIST. They were preserved on October21, but temporary storage
did not survive. No matching Oct21–24 RDR2 .txt files found by the Downloads
filename search on October24. Saved analyses, measurements and excerpts in
repo/memory remain. Do not promise fresh analysis of missing raw logs, or
claim a new full log inspection occurred while writing this handoff.

## Verification / staged native outputs

Previously passed on October21:

```sh
python3 tests/host/check-air-table-cache.py
python3 tests/host/check-typed-view-index.py
python3 tests/host/check-pso-warm.py
python3 tests/host/check-kuser-handled.py
python3 tests/host/check-hitch-logging.py
python3 tests/host/check-swap-decommit.py
python3 tests/host/check-sampler-safety.py
python3 tests/host/check-runtime-diagnostics-policy.py
```

New code tests execute production functions under ASan/UBSan where applicable:
AIR dependency/lifetime invalidation,16ktyped views/hash collisions/OOM,
warm descriptor deep-copy/retained ownership/caps/default-off, handled KUSER
register preservation, read result/errno/disabled behavior/concurrent counters/
active-slot overflow, stall trigger/rate policy and shared sync/async counter
wiring. Sampler-safety/policy tests include structural checks, not device proof.
Legacy check-skin-check.py could not start due to its missing referenced
app/Madeira/GameRecommendations.swift; do NOT report it passing.

Exact native commands used (do not use app xcodebuild):

```sh
PATH=/usr/bin:$PATH bash build/ntdll-unix/build.sh
PATH=/usr/bin:$PATH MADEIRA_ONLY=winemetal_unix bash build/dxmt-ios/build.sh
PATH=/usr/bin:$PATH bash build/madeira-d3d12/build-pe.sh
cp build/madeira-d3d12/out-pe/d3d12.dll app/Madeira/arm64ec-windows/d3d12.dll
cp build/madeira-d3d12/out-pe/madeira_d3d12.dll app/Madeira/arm64ec-windows/madeira_d3d12.dll
```

ntdll42units/0failures, DXMT65objects/0failures, D3D12PE succeeded. Scripts
stage native archives; DLLs explicitly copied. v2 ntdll archive was5355784bytes.
October24 rechecked both staged DLLs SHA256:
`599edc91f763a17f8972cd6ee6ee194d2430d24e976646de0d509574d3f486fe`.
No full app build, commit/push, save/config edits or device writes in this batch.
Tests/builds were not rerun merely to write this handoff.

## Actual next implementation — not yet done

1. Publish bounded active-wait metadata around server_select /wait_select_reply
   in build/ntdll-unix/server_ios.c (~2627 and~3073 in current file): operation,
   object handles, flags, timeout/deadline, cookie, native/Wine identity,
   caller context and slow completion result/duration. Join with stall-PC
   snapshots so we know WHAT render048c waits for and WHO should signal it.
   Preserve APC/retry/nested wait lifetimes and every cleanup/return path.
   Raw wait pipes are synchronization, not asset reads: don't mislabel them.
2. Preserve/interpret the uninterruptible producer states too. Add sufficient
   VM/kernel-wait attribution to distinguish delayed producer work from a
   delayed server wakeup. Avoid assuming game PCs mean a CPU hot loop.
3. Host-test the actual tracking functions and failure/lifetime/disabled cases,
   rebuild native inputs, then ask for one Xcode/device run and AirDropped log.
4. Separate shader project: evaluate bounded correct PSO reuse/persistent
   device archive/preparation. Need full pipeline descriptor/function identity,
   version/device keys, lifetime, concurrency, pressure caps and fallback.
   Identical VS/PS names alone are NOT a complete PSO key. Don't blindly turn
   warmer on, skip full-screen draws, drop barriers or eagerly compile all PSOs.

Do not shorten wait timeouts/force-signals/enable async submission to hide the
symptom. No active-wait tracking or persistent PSO archive has yet been written.
The next dependency is implementation, NOT another identical device run before
the missing wait-target evidence can be captured.

## Copy/paste continuation prompt

```text
Continue Madeira RDR2 performance work in /Users/iediot/Projects/Madeira.
First read CLAUDE_HANDOFF_2026-10-24.md completely, then the memory files and
focused reports it names. It is the current handoff; older CODEX_HANDOFF.md
is historical. Inspect the actual dirty tree and preserve existing work.

Act on the handoff's "Actual next implementation" now: bounded active Wine
wait-target tracking joined to the working v2 stall snapshots, preserving
APC/nesting/cleanup and also investigating uninterruptible producer waits.
Do not stop at another proposal. Implement, host-test, rebuild/stage affected
native libraries with the exact PATH-prefixed commands, and hand back when
I genuinely need to install/run in Xcode or make a decision.

RDR2 currently uses custom D3D12 -> Metal, not MoltenVK, and async-submit=0.
The snapshots now work, but some other game threads were state4 (Darwin
UNINTERRUPTIBLE), not running. Render048c waited in wait_select_reply during
~0.7s periodic gaps; that does not prove wineserver is faulty. No blind
timeout/barrier/async/warmer changes. Raw /private/tmp logs have disappeared;
use saved analyses until another raw log is provided.

No app xcodebuild, FEX source generation, save/config deletion, commit/push,
AI attribution or Co-Authored-By. Leave project.pbxproj and Info.plist alone.
Keep changes batched, communicate progress briefly, and save new findings.
```
