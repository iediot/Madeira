# RDR2 performance follow-up, October 21

## Log identity and preserved copies

All six AirDropped files in Downloads were copied to
`/private/tmp/madeira-rdr2-oct21.6xlOQM/` before analysis. They represent THREE
runs, not six: each PlayRDR2 log is byte-identical (SHA-256 checked) to its RDR2
counterpart. Latest/current pair: PlayRDR2 12:47:41 / RDR2 12:48:19, containing
the D3D12 build dated 12:03:27 and AIR cache counters. Earlier pairs are 09:24:52
/09:25:31 and 11:50:24/11:51:06, with the older 00:38:37 D3D12 build and no AIR
cache counters. The newest pair's SHA-256 is
`b5f2a4e01932bd9114f37b21fa8911d67050198cf9c6440ed3baad6de55533d1`.

## Comparison, with limits

These figures use positive-FPS `[perf]` reporting windows after elapsed100s.
FPS is the unweighted mean of those windows; p50/p95 are medians of window
percentiles, NOT a whole-run percentile or a controlled same-scene A/B. The
notes' baseline57FPS/p50=21ms/p95=47ms/GPU5.7ms used a different selection.

| Pair | Windows | Mean FPS | Median p50 / p95 | Mean GPU ms/frame |
| --- | ---: | ---: | ---: | ---: |
| 09:24 old build | 214 | 49.75 | 19.2 / 31.1 ms | 15.73 |
| 11:50 old build | 46 | 60.20 | 16.6 / 17.65 ms | 7.47 |
| 12:47 cache build | 64 | 51.95 | 18.85 / 34.85 ms | 10.10 |

The scene/GPU load differs too much to claim a measured win or regression.
Latest AIR counters:2667086hits /28281754misses =8.6177% hit rate. Global
descriptor invalidation is overly broad, but these counters alone do not
prove every miss is caused by it; roots and the eight-entry capacity also matter.

Latest >300ms reporting windows:190s/622.2ms,220/802.5,251/556.8,281/597.4,
311/479.8,368/14287.5,398/335.4. These are windows containing a long interval,
not a complete count of individual stalls. The recurring intervals are now
roughly30s, not consistently60s.

The14.3s outlier follows an app-inactive event near347s and is accompanied by a
14.565s drawable wait. Do NOT call it a proven spontaneous game/CPU freeze.
Foreground-only stall reporting is still needed. Other recurring windows lack
this same evidence and remain a separate investigation.

`replay-split=0` is active, but the intended classifier-off state is NOT:
the log applies default `MADEIRA_PERF_CLASSIFY=0`, then explicit game `=1`, and
prints `[perf-classify] on`. Thirty-second report work is therefore a candidate
to measure, not a confirmed cause. No device/game config was changed here.

VRAM is reported as4096MB. The log's mip-clamp/DXMT lines also cover launcher
D3D11 clients; do not attribute those automatically to RDR2's D3D12 horse
textures. No streaming short-read/error cause was established for that visual
symptom. Retain current budget pending stronger residency/read evidence.

## Implemented

- AIR table invalidation now versions the bound shader-visible descriptor
  heaps separately. Staging descriptors, RTV/DSV copies and other heaps no
  longer invalidate every table. All CBV/SRV/UAV/sampler creation and both
  descriptor-copy APIs mark the destination heap(s).
- Intrusive heap registry is protected by a shared/exclusive lock; heap
  destruction unregisters before freeing CPU storage or the heap object.
  Writers hold the shared lock through atomic version updates. No new heap
  reference retention, worker thread or persistent cache allocation.
- Cache keys retain root version and all prior pipeline/stage fields, and now
  also compare both bound heap identities and versions. Debug/table/skin
  capture bypasses remain. Per-range invalidation is not implemented yet.
- Classifier's30s detailed report logs `[perf-classify-cost]` if it takes50ms
  or longer, split into top, FEX symbol, system symbol and guest stages.
  Next deadline starts after report completion. This measures diagnostic cost;
  it does not disable profiling or claim the periodic stutter solved.

## Verification / handoff

- `python3 tests/host/check-air-table-cache.py`: ASan/UBSan executes production
  version/lookup functions with stub table allocation. Checks independent heaps,
  bound-resource/sampler invalidation, staging/RTV writes, end boundary, root
  changes, heap identity, debug bypass, concurrent writers and unregister.
- Existing `build/ntdll-unix/tests/test-perf-classify.py` passes.
- Native builds with `PATH=/usr/bin:$PATH` completed:
  `build/madeira-d3d12/build-pe.sh` and `build/ntdll-unix/build.sh`.
  Both rebuilt D3D12 DLLs staged in `app/Madeira/arm64ec-windows/`, identical
  SHA256085c8a6b732512950c06e34a5099e32bbdcc2f59fc315d17e7765d1345966965.
  ntdll archive staged5342864bytes. No app xcodebuild.
- No FEX/Wine source edit, device transfer/config edit, commit or push.
  Existing project.pbxproj and Info.plist edits remain untouched and must be
  excluded from the suggested commit.

User Xcode install, then same-route/same-settings RDR2 play. Next AirDropped
pair: compare cache-hit rate, caller ExecuteCommandLists cost, foreground-only
stalls, p95/GPU load, `[perf-classify-cost]` and lazy-PSO deltas. A high hit rate
is not itself proof of an FPS gain. Do not disable lazy PSOs or eagerly compile
all pipelines without a memory/lifetime plan; existing parallel first-use and
`pso-warm=2` are already active. Persistent texture quality remains unproven.

## Evening device check: 19:20:18 run

Preserved `RDR2.exe-2026-10-21_19-20-18.txt` in the same temporary log folder.
D3D12 build timestamp19:13:42 confirms the per-heap cache build; this play
preceded the footage UI change. Same positive-FPS, after100s window selection:
61windows, mean47.95FPS, median windowp5023.4ms/p9539.4ms, meanGPU9.97ms.
Prior12:47 run:51.95FPS/18.85ms/34.85ms/GPU10.10ms. Not a controlled route A/B.
Peak footprint6256MiB versus6258MiB. AIR2248140hits/27375280misses=7.59%:
the per-heap change has not demonstrated a hit-rate or performance benefit.

Long-frame reporting windows (>300ms) are110/170/201/231/261/291/352/377/407s,
with maxima547/509/1361/914/519/1153/1408/1111/311ms. Unlike the previous
14s outlier, these windows do not show a matching multi-second drawable wait.
Classifier remains on, but no >=50ms classifier report warning appears.
Some zero-present intervals have no new lazyPSO work, negligible reads and
low CPU, so shader compilation alone cannot explain the long stalls.

Concrete lead: `[swap] F_PUNCHHOLE (decommit)` takes1345ms near196.57s,
1161ms near286.35s,1416ms near346.58s and1373ms near376.54s. These align
with several of the largest long-frame windows; additional punches take
113/358/338ms. `ios_swap_punch_resv` in `virtual_ios.c` calls the timed
fcntl synchronously from decommit under virtual_mutex. Existing deferred
punching only covers returned, unmapped file ranges, NOT these live extents.
This is strong evidence of a swap/decommit lock stall, not proof that every
remaining hitch has this cause. Shorter PSO/other stalls remain separate.

Next fix should remove slow file-hole work from the global memory-lock path
while preserving zero-on-recommit and file-offset ownership. Do NOT simply
enqueue a punch of a still-live extent: a later recommit could write new data
before the worker punches it, corrupting memory. A safe design must detach or
replace/quarantine the old backing before asynchronous cleanup, with checked
failure paths and an explicit memory/streaming tradeoff. This log review made
no runtime/config/save changes and did not rebuild the app.

## Swap decommit fix implemented (awaiting iPad)

- Reservation-backed decommit now maps a fresh zero-filled sparse-file range
  at the same guest address, preserves both neighbouring extents and queues
  the old, detached offsets for the existing punch worker. New writes cannot
  be erased by delayed cleanup. Recommits remain file-backed, not anonymous.
- Coverage is validated before changing mappings. Requests spanning extents
  split by previous decommits process each piece, retaining file backing.
  No new thread/persistent buffer; uses the existing bounded queue and tables.
- Queue room, extent slots and zero offsets are checked before replacement.
  Mapping failure returns the unused zero offsets without punching them.
  Disabled/unavailable worker, full queue, exhausted offsets/table or failed
  mmap use the old synchronous correctness fallback. This can still hitch,
  and is counted in `[swap] decommit rotations N, synchronous fallbacks M`.
- Retired offsets remain quarantined until punching succeeds. Failed punches
  are never reused. Old disk blocks may remain allocated longer while queued;
  this is not a claim of reduced disk usage or zero remaining stutters.
- `tests/host/check-swap-decommit.py` executes the production allocator,
  rotation and worker on real macOS mappings/file punches under ASan/UBSan:
  whole/prefix/suffix/middle, blocked-worker immediate writes, zero reuse,
  neighbours/metadata, failed punch/map, no offsets/slots/queue room, disabled
  worker, coverage gaps, larger cross-split decommit and128 randomized overlapping
  decommits. Existing partial-page decommit regression test passes too.
- Rebuilt ntdll with `PATH=/usr/bin:$PATH bash build/ntdll-unix/build.sh`;
  archive staged by script in `app/Madeira/libntdll_unix.a`. No app build,
  device config/save edit, FEX source edit, commit or push.

Device test: build/install in Xcode, same-route RDR2 play for5–10minutes,
AirDrop log. Compare foreground >300ms windows and p95, rotation/fallback
counts, deferred punch timing, extent count, memory/disk footprint and errors.
Slow `deferred` punches may still appear; they should no longer block memory
operations through virtual_mutex. Slow `decommit` punches indicate fallback.

## First swap-fix device run:19:50:12

Preserved Downloads `RDR2.exe-2026-10-21_19-50-12.txt` in the same tempfolder.
Rotation messages confirm >=19456rotations; no unavailable warnings, no slow
synchronous decommit punches, census holes=0. No final rotation summary emitted.

Same after100s positive-FPS selection:85windows, mean46.42FPS, median
windowp5023.1ms/p9536.4ms, GPU14.58ms. Previous61windows:47.95FPS,
23.4/39.4ms,GPU9.97ms. Different route/workload: no mean-FPS win established.
Worst selected frame660.9ms versus1408.1ms. >300ms windows115/140/175/205/
231/261/322s (7 versus9 in previous shorter run). None>300ms after322s;
after400s all26reporting windows max<=120ms.

Middle250–400s:39.73FPS, medianp9547.05ms. Final400–529s:49.47FPS,
medianp9530.8ms. User final area lacked houses: cannot separate scene changes
from warming. Lazy first-draw totals533/21005ms@259s,696/28107ms@399s,
726/28621ms@528s: only514ms added after399s versus7102ms in preceding140s.
Aggregate compile work is not necessarily serial or equal to stall duration.

Seven slow DEFERRED punches remain:897/900/1027/321/478/279/371ms. First
three overlap zero-present/lowCPU episodes around200/230/320s; lazyPSO count
unchanged around200s. Removing virtual_mutex from fcntl did not eliminate
all coincident stalls. Shared-file APFS contention is a hypothesis, not proven.
Later punches409/469/529s do not produce >300ms frame windows. Next: time
mmap/MS_KILLPAGES/lock acquisition separately and correlate worker activity.

XP peak6266MiB versus6256MiB. Last census9433extents out of16384slots;
rotation fragmentation needs attention for longer sessions before exhaustion
causes synchronous fallback. Disk4510MB; logical offset highwater34781MB is
NOT physical disk use. AIR4892607hits/44332345misses=9.94%, still low.
Thermal became fair near394s: late improvement is not evidence of cooling.
No runtime/config/save changes in this review.

## Follow-up implementation: cleanup diagnostics and real shader warm-up

### Subsequent21:23:45 device result — regression, do not call this a win

Latest log preserved in the same temporary directory. Compare200–330s windows:
prior19:50 run40.55FPS/p95median42.6ms/GPU12.97ms; new34.84FPS/p95median
100.1ms/GPU13.21ms (25windows each). Different gameplay, not matched A/B, but
user reports unplayable lows and the logged tails support it. Later native
renderer reports21–24FPS, GPU busy38–45%wall, callerECL15–20ms/frame,
retire waits~0: not evidence that the GPU alone is saturated.

Warm-up active,416submitted by~78s,384completed at last emitted checkpoint,
3167skipped,21403ms aggregatework. No final drain checkpoint emitted: do not
assert exact completion time. Bulk logged warm work~67–78s; cannot attribute
200–330s regression to simultaneous warm threads from these counters alone.
Lazy firstdraw544/19750ms byend~330s, still substantial. Previous619/25079ms
by299s but scene/first-use counts differ. No demonstrated warm-up performance win.

Swap census10478rotations/fallback0/compact690/6658extents atlastcheckpoint;
no >=20ms successful-rotation phase warnings, five VM-lock waits20–40ms.
Only two slow deferred punches834ms@48s and134ms@78s, not matching later
periodic long frames. Thus latest long gameplay pauses cannot be explained
by the earlier synchronous F_PUNCHHOLE diagnosis. XPpeak6420MiB, prior6266.
Selected after100s45windows mean46.77FPS/p95median46.8ms masks poor gameplay
because first100–200s include highFPS/loading. Need controlled warmer-off A/B
and submission-path profiling before further speculative tuning.

User explicitly asks about REUSING actual mature CrossOver/GameHub stack code,
not merely similar features. Answer: yes, audit reusable upstream/native backend
implementation; Wine/FEX/DXMT/MoltenVK already reused, but RDR2 uses custom
madeira-d3d12, not CrossOver's D3DMetal or vkd3d-proton. Need compare bindings/
submission/PSO caching against mature sources. CrossOver publishes FOSS sources;
don't assume proprietary D3DMetal/GameHub internals are available or iOS drop-in.
This review made no runtime change or rebuild; do not silently call rollback done.

- Swap rotation now validates a whole reservation-backed range, maps it once
  using one contiguous zero offset range, and retires all old pieces afterwards.
  A larger decommit therefore collapses earlier fragments instead of retaining
  every split. Preserves untouched neighbours and bounded queue/table checks;
  single-piece operations avoid a whole-table retirement scan. This reduces
  fragmentation, not a promise that arbitrary tiny ranges can never fill the table.
- Census now includes `rotate`, `fallback`, `compact` counts. Slow successful
  rotations (>=20ms) log lookup/allocation, MS_KILLPAGES, mmap and retirement
  phases. NtFreeVirtualMemory decommit lock acquisition logs waits>=20ms.
  Both warning streams are rate limited. This measures the suspected shared-file
  contention; it does not yet establish or eliminate that remaining cause.
- Important discovery: previous logs explicitly say `pso-warm: winemetal took
  no warm-up request ... warm-up off`. The PE caller exists but native MadeiraCtl
  had NO op10 handler. Config `pso-warm=2` alone never enabled it in these builds.
- Implemented native op10 in DXMT winemetal, shared by graphics/vertex-descriptor/
  compute warm jobs. Uses the normal descriptor conversion/builders, copies all
  descriptor data and archive arrays, retains device/functions/lookup archives,
  and does not mutate serialization archives. Releases the resulting PSO at once;
  the intended benefit is Metal's compiled-code cache, not retained GPU pipelines.
- Up to2 utility-QoS serial workers,128pending/active jobs,8lookup archives per
  job and256MiB minimum known headroom. Admission never waits for queue space;
  full/low-memory jobs skip without disabling the PE caller. Worker rechecks
  headroom before compiling. Remote/WoW64 stay unsupported. Config0 stays off;
  existing RDR2 `pso-warm=2` should now activate it without device config edits.
- `[pso-warm] native handler active` plus completed/submitted/skipped/failed/
  pending/work counters allow the next log to verify actual activity. Warm-up
  failures do not change a game's pipeline state or skip any foreground draws.
- ASan/UBSan swap tests updated for cross-split compaction and128 overlapping
  random decommits; partial-page regression PASS. New check-pso-warm.py executes
  the actual queue with mock Metal objects/builders: all3kinds, descriptor/VD/
  archive deep-copy, post-submit caller destruction, queue saturation, pressure
  skip/drain, build failure and zero remaining owned objects PASS. This tests
  lifetime/policy, NOT actual GPU warm-cache effectiveness.
- Native builds PASS: ntdll42/0 and DXMT65/0 (MADEIRA_ONLY=winemetal_unix script
  mode also recompiles the native substrate). Scripts refreshed/staged both app
  archives, and the new handler string is present in libdxmt_combined.a. No app
  build, FEX edit, device config/save change, commit or push.

Next device run: Xcode build/install; play the troublesome opening/house area
for5–10minutes. Compare first-draw pipeline time, startup duration, frame-time
tails, warm activity/skips, swap phase/lock warnings, extent growth/compact,
memory/disk and visual correctness. A repeat of the same route is more useful
than comparing town against countryside. Expect no guaranteed FPS gain yet.

Batched commit command for the user (includes the preceding UI/perf changes;
explicit paths exclude project.pbxproj/Info.plist, no automatic commit performed):

```sh
cd /Users/iediot/Projects/Madeira && git -C wine push origin HEAD:madeira && git -C FEX push origin HEAD:madeira && git -C dxmt add src/winemetal/unix/pipeline_warm.h src/winemetal/unix/winemetal_unix.c src/winemetal/winemetal.h && git -C dxmt commit -m "Implement bounded native pipeline warm-up with safe descriptor ownership" && git -C dxmt push origin HEAD:madeira && git add dxmt build/ntdll-unix/virtual_ios.c build/ntdll-unix/perf_ios.c madeira-d3d12/src/pe/madeira_d3d12.c app/Madeira/arm64ec-windows/d3d12.dll app/Madeira/arm64ec-windows/madeira_d3d12.dll app/Madeira/DockStartScreen.swift app/Madeira/Library.swift app/Madeira/LibraryHome.swift app/Madeira/Store/SteamStore.swift app/Madeira/Store/StoreView.swift tests/host/check-air-table-cache.py tests/host/check-footage-backdrops.py tests/host/check-swap-decommit.py tests/host/check-swap-coverage.py tests/host/check-pso-warm.py docs/RDR2_PERF_OCT21.md && git commit -m "Reduce swap decommit stalls and fragmentation, enable native shader warm-up, and refine library footage" && git push
```
