# Detroit performance investigation, 2026-10-14

Read-only iPad investigation and targeted decompilation. No game EXE, device
configuration, app runtime, native library, commit or push changed in this work.
The profiler label tool was extended to name newly inspected functions.

## Runs inspected

The device's latest log is a short run from October 13, 21:59–22:01. The longer
archived run is `DetroitBecomeHuman.exe-2026-10-13_18-55-18.txt`, ending at 20:00.
Both end with a normal memory-session-end marker, not an abrupt crash.

| Metric | Long run | Latest short run |
| --- | ---: | ---: |
| Session duration | 3906 s | 161 s |
| Logged FPS windows / frames | 773 / 196553 | 25 / 7279 |
| Median reporting-window FPS | 55.7 | 58.8 |
| Frames over 50 ms | 1.60% | 0.26% |
| Worst reported frame interval | 1131.7 ms | 376.4 ms |
| Memory-session peak | 11850 MiB | 7979 MiB |
| Minimum sampled available memory | 437 MiB | 4308 MiB |

Different scene lengths and thermal states: these are not a controlled A/B.
Use `[vk-fps]`, not DXMT-only `[perf]` frame fields. The lower-level footprint
counter reports a slightly different long-run peak (11860 MiB).

## New strongest hitch lead: audio teardown

There are 42 `detach quiesce TIMED OUT after 501 ms` messages in the long run.
Of 27 FPS windows whose worst interval is 550–650 ms, 26 have a timeout within
the preceding six seconds. These are approximate correlations using the last
`[xp] +elapsed` timestamp, not exact instrumented frame stacks. The exception is
the 624.5 ms frame near 957 s, which correlates with shader-library preparation.

`build/ntdll-unix/audio_null_ios.c:950`, `ios_dev_detach`, unpublishes the stream,
then waits for **two future completed RemoteIO passes**. If callbacks are not
arriving but `g_dev.running` remains true, it sleeps 1 ms up to 501 times. Release
joins the stream's timer first and frees stream/ring storage after this wait.
The endpoint's `running` flag records a successful start call, not current
callback progress. Interruption/resume can therefore invalidate that inference.
The log does not establish the exact reason for each callback stoppage.

The timeout is a substantial performance problem and also not a lifetime proof:
it eventually frees storage even if a callback might still hold the old pointer.
Simply deleting/shortening it is unsafe. The concrete fix to implement next is
real callback read-side protection, ordered against slot unpublication, with
deferred retirement if an actual reader remains. Requirements:

- No allocation, blocking mutex or syscall in the real-time render callback.
- If no callback holds a stream, detach must not require future callbacks.
- A callback that captured the old stream must finish before storage is freed.
- Stop/interruption/early-return paths must not strand reader accounting.
- Concurrent attach/release/resume must preserve the process-wide endpoint and
  other clients. Test paused callbacks and publication races under sanitizers.

Do not restart RemoteIO on every teardown as a shortcut: it can interrupt other
clients, and earlier per-client endpoint churn caused a caulk crash.

## Shader work: cache works, remaining stages still cost time

The long run's last counters show six source-library compilations, average
217.0 ms, versus thousands of disk-library loads (`[mvk-precomp]` last milestone
6138 loads / six compilations). Logged slow shader preparations contain up to
598.0 ms of library work with only 8.6 ms conversion in that case; this closely
matches the nearby 624.5 ms frame. All 16 preparation diagnostics together
contain 1879.6 ms library time and 447.4 ms conversion time (thresholded logs,
not all calls).

The prefetch path is active: final counters requests=18598, loaded=451,
learned=6676, dropped=3920, write_failures=0. `dropped` includes both metadata
learning and prefetch admissions, so it is **not** 3920 proven useful libraries
missed. Request counts are also not the denominator of a cache-hit rate.
The shared serial queue admits at most 32 jobs, stores only 512 module hints
and two variants/module, and the library LRU remains 256 entries. Module bursts
can outrun this bounded prefetch. Blindly growing the Metal-library LRU is risky
with only 437 MiB sampled memory headroom.

Final pipeline timing: 286 full-screen waits / 12758.8 ms accumulated and 1703
budget waits / 15343.4 ms. Async pipeline backlog high-water=1773. Logged urgent
dequeue delays remain about 0–3 ms, so priority dequeue is not the principal
remaining full-screen delay. Older queued-job ages of tens of seconds are
background ages, not equal-duration frame freezes.

Next shader work: split prefetch outcomes (hint missing, already cached,
admission dropped, disk missing/refused, loaded then consumed/evicted) before
changing bounds; move first-time library preparation behind a lifetime-safe
async pipeline construction path. Offline refresh can cover newly captured
sources, but the existing helper writes the device cache and was not run here.
Do not enable Wine's unbounded real Vulkan pipeline cache.

## Targeted game decompilation

Captured EXE SHA-256 remains
`ecf52321921387e683904e089082d76b973326fc093af14e524056715519c1cf`.
Ghidra inspected 14 selected entries, using PE unwind boundaries for the
interior sampled PCs. Artifacts stay outside the repository:

- `/private/tmp/detroit-fine-hotspots-decompiled.c`
- `/private/tmp/madeira-ghidra-scripts/DetroitFineHotspots.java`
- Existing `/private/tmp/DetroitInspection.gpr` project and original EXE.

No proprietary pseudocode or executable is included in this document/repo.
Decompilation has incomplete indirect-call/type recovery; inferred purpose is
explicitly distinguished from source matches.

| RVA | Finding |
| --- | --- |
| `0x64cf80` | VMA generic allocation request. Hottest `0x64d3e0` bucket falls in the descending free-candidate search immediately before `CheckAllocation`, not the reclaim-lost-allocations branch. |
| `0x64d8b0` | Alignment, size and neighboring buffer/image checks; contains variable integer division. |
| `0x64d790` | Free-by-allocation scans a linked list for the allocation pointer. |
| `0x64d7f0` | Free-by-offset scans the same list for the offset. |
| `0x5df3f0` | Nested object-list scan/cache fill. An outer iteration repeatedly walks a list from the start to find its indexed item; then scans a child list for an ID. Semantic subsystem remains unproven. |
| `0x5a5030` / `0x5a5130` | Size-bin heap search under a critical section, candidate removal and block splitting. |
| `0x5a8c90` | Bytewise buffered reader with refill at boundaries; mainly a startup/loading lead so far. |
| `0x5a3000` | Scheduler dequeue/execute, also called while waiting for a pipeline build to finish. |
| `0x6674b0` / `0x669ce0` | Renderer buffer/binding state updates (purpose inferred), not allocator search. |

The four VMA search/free routines account for 8738 of 11134 **printed, mapped
fine samples for thread 019c** (~78.5%). This is top-entry reporting with a
sampling/selection bias, NOT 78.5% of total app CPU or a promised FPS gain.
Main-thread `0x5df3f0` has 1172 printed samples. No call counts or list-length
histograms exist yet.

The allocation/free control flow matches the public
[VMA v2.3.0 generic metadata implementation](https://github.com/GPUOpen-LibrariesAndSDKs/VulkanMemoryAllocator/blob/v2.3.0/src/vk_mem_alloc.h),
not proof of the exact bundled version. A full VMA replacement/TLSF upgrade is
not ABI-compatible with its existing object layout and vtables.

Promising next game-specific experiment: an opt-in, exact-EXE-gated native
leaf/helper path that preserves the existing metadata, allocation order,
alignment and output fields. Begin with call/list-length measurement and a
host equivalence harness against public source for free-list search and
allocation feasibility. Validate boundary flags/overflow/reclaim paths and
the x64-to-native return bridge before any runtime detour. Replacing a search
with a different placement policy may alter fragmentation and memory usage.
The nested object lookup needs mutation/invalidation analysis before any index
cache; a stale lookup could break gameplay.

MoltenVK advertises `bufferImageGranularity = mtlBufferAlignment` (16 on the
native Apple3+ path). Do not spoof it to 1 merely to skip the neighbor loops:
that needs a separate correctness proof for mixed buffer/texture bindings.

## Heat and implementation order

Thermal events in the long run: fair 19:01:25, serious 19:10:35, fair 19:48:10,
nominal 19:57:00. Median reporting-window FPS by elapsed segment: 0–15 min 58.3,
15–30 min 46.8, 30–45 min 40.2, 45 min–end 59.0. Scene changes, audio stalls and
shader work confound the thermal comparison.

Recommended order:

1. Fix/test callback lifetime and audio detach stalls in the native driver.
2. Improve shader outcome measurement and first-time async preparation, keeping
   memory bounds; do not add compile workers blindly under thermal pressure.
3. Prototype measured, version-gated native allocator leaf acceleration.
4. Investigate nested-list lookup/cache invalidation only after mapping callers.

The research changes need no iPad rebuild. Actual audio/shader runtime changes
will require native rebuilds here and the user's Xcode install, then the same
scene with comparable thermal state. Graphics correctness bugs remain separate;
FULL_BARRIERS and all device settings were left untouched.
