# Detroit build validation and deeper decompilation — 2026-10-13

## Device run

Input: `/private/tmp/madeira-detroit-oct13.txt`, pulled from the iPad's
`Documents/madeira-log.txt`. Derived statistics and event correlations:
`/private/tmp/madeira-oct13-analysis.json`. The session ended normally after
5,790 seconds (96.5 minutes); this is not another abruptly ending crash log.
The urgent-only worker, conversion cache and runtime archive-budget markers are
present, confirming the new native library's relevant paths.

| Measurement | Previous crash run | New run |
| --- | ---: | ---: |
| Vulkan frames reported | 206,588 | 315,142 |
| Median 5-second reporting-window FPS | 52.3 | 57.3 |
| Frames over 50 ms | 1.49% | 0.55% |
| Largest reported frame interval | 19,209 ms | 943.5 ms |
| Peak footprint, MiB | 12,279 | 11,941 |
| Session end | Abrupt, likely OOM | Normal |

These are different playthroughs, not a controlled same-scene A/B benchmark.
Loading, menus and pauses are included. The previous 19-second interval must
not be interpreted as a 19-second shader compile. `[perf]`'s zero frame fields
are still unusable for Vulkan here; the measurements above use `[vk-fps]`.
The new run's closest sampled memory headroom was still only 346 MiB.

## Which changes were exercised

- **Archive cap:** 33 finished archive-object evictions. Final charged file
  bytes 1,599,564,976 (~1,525 MiB), below the unchanged 1,536 MiB budget. This is
  disk-equivalent charge, not exact CPU residency; active writers are excluded.
- **Conversion LRU:** final 15,321 hits / 16,726 misses (~47.8% lookup hit rate),
  16,172 evictions, 292 entries, 33,499,688 charged bytes under 32 MiB. It is
  bounded and actually useful, but a substantial working set still churns.
- **Urgent-only worker:** the 27 full-screen wait diagnostics over 100 ms report
  only 0–3 ms urgent queue delay. Compute diagnostics reach 341.9 ms with no
  initial urgent queue delay. Compilation/dependency time remains after pickup.
- **Fallback Metal completion helper:** no `[mvk-compile]` slow-fallback lines.
  This does not prove there were no short fallback calls, but does not establish
  benefit from the fallback-only change on the dominant archive path either.

The final timing summary reports 134 full-screen waits totaling 9.05 s, 1,004
frame-budget waits totaling 8.94 s, and a background queue high-water mark of
2,659. Only 31 queued jobs remained at the last summary. 111,466 skipped draw
events are counted, **not** 111,466 dropped frames. Pipeline jobs succeeded;
the last summary records zero failures. Archive lookup hit rate was about 59.7%
(7,712 hits / 5,196 misses). Archive miss work averages 58.7 ms, hits 10.8 ms.

## Remaining stutters

The ten worst frame-report windows all have shader-preparation or dependency
wait diagnostics within six seconds, using the nearest preceding `[xp]` clock.
This is window-level correlation, not precise per-frame causality.

| Elapsed | Worst frame interval | Nearby library preparation |
| --- | ---: | --- |
| 27:44 | 943.5 ms | 566.4, 202.0 and 142.2 ms library work, plus compute waits |
| 53:27 | 724.4 ms | 605.8 ms library work + 58.4 ms compute wait |
| 55:53 | 673.5 ms | 559.8 ms library work + 159.7 ms compute wait |
| 96:14 | 601.0 ms | 535.1 ms library work + 75.0 ms compute wait |

The dominant remaining cold-shader stall is synchronous MSL-to-Metal library
preparation **before** the async PSO queue, not its urgent dequeue delay. The
32 shader-prep diagnostics total about 8.46 s library time vs 0.52 s conversion
time. A background `slow-compile 11312.5ms` is not an 11-second frame stall.
Archive work, shard waits and OS scheduling can contribute to that job's time;
current diagnostics do not separate those components.

At the end CPU usage was about 5–7 cores while the nearby GPU sample was 28%
busy, indicating CPU-side pressure in that stretch. Several XP samples show
mostly E-core execution / low P-core clocks. The app's device monitor explicitly
reports thermal `fair` at 22:55:03 and `serious` at 23:00:53, about 21 minutes
after launch; no later recovery is logged. Heat is therefore a real concern,
though these samples cannot isolate how much FPS loss comes from throttling
versus scene complexity, emulation and concurrent compilation.

Pointer lock was granted while playing and released on focus loss. That does
not verify mouse dragging correctness. An audio-interruption resume returned
`start=0` (success). Eight primitive-restart warnings remain. There is no
evidence here that identifies the reported floating-hair/stray-mesh cause.
One startup `VK_INCOMPLETE` cache-serialization diagnostic is followed by a
successful size-only cache query (`result=0`, 36 bytes); it is not a logged
pipeline-compile failure or device-loss event.

## Decompilation continued

The follow-up completed successfully with Ghidra against the same captured EXE:
`/private/tmp/detroit-followup-decompiled.c`, with 33 normalized selected entry
points in its latest pass (some revisit the earlier 92 targets). The discovery
script now follows PE chained-unwind records, avoiding mistaking cold function
fragments for independent routines. No original-source claim or full-game
decompilation claim is warranted. No executable was patched.

Additional recovered paths, relative to the EXE image base:

- `0x65d640`: a loading/compile job creates a per-worker Vulkan pipeline cache,
  instantiates shader modules, requests graphics compilation, releases its
  temporary shader objects and decrements the outstanding-job counter.
- `0x65dbb0`: graphics compilation lookup/insertion, checks the current thread,
  and either compiles directly or submits a job through the game scheduler.
- `0x65db10`: the submitted job invokes the graphics pipeline builder and
  decrements its completion counter.
- `0x646050` → `0x65f3c0`: render-time bind resolves a graphics pipeline; a cache
  miss creates it immediately, while an existing incomplete entry runs scheduler
  work until its pending flag clears. `0x647b80` does the compute-cache lookup
  and invokes `0x65fd80` on a miss before binding.
- `0x5a23b0`: scheduler allocation derives two worker-pool sizes from hardware
  concurrency: clamped `CPU count - 3` (1–12) and `CPU count - 1` (1–8). The
  actual pool utilization/core mapping still needs runtime measurement.
- `0x651430`: allocator block initialization chooses metadata implementations
  by algorithm flag. The generic implementation has a 0x98-byte object and
  vtable at RVA `0x1dfd8a0`; its allocation-request method is at vtable +0x50,
  RVA `0x64cf80`. This is a clearer starting point for version-gated allocator
  investigation, not sufficient ABI validation for native replacement.

This demonstrates that Detroit already has background pipeline compilation;
the useful next question is why particular late variants miss its warmup and
whether library preparation can be moved into that loading window safely.
Changing the game's thread count or allocator blindly is not justified.

## Practical next step

The last precompiled-library summary reports 14,283 disk loads and 53 source
compiles. A read-only device inventory found 28,609 `.metallib` files plus 117
captured `.metal`/`.json` pairs with **no corresponding library filenames**.
These may include older uncompiled captures; filenames alone do not establish
that every pair was used this run or that all offline compiles will succeed.

The existing tool can compile and transfer those missing libraries with the
original recorded options and the iPadOS-compatible deployment target:

```sh
PATH=/usr/bin:$PATH tools/precompile-msl.sh 00008142-000A38AE0147801C 9a5cb11eba58918b
```

This has **not** been run in this analysis-only continuation: it writes device
cache files. It needs no app rebuild; restart the game after transfer and replay
the shader-heavy section to measure disk hits. This can reduce repeated library
compilation, not eliminate truly new shader variants or all PSO compilation.

No runtime code or device config changed, and no build/commit/push occurred.
The next implementation candidate after that low-risk cache refresh is bounded
earlier library preparation, with source/options dedupe and lifetime checks,
not a larger unbounded pipeline cache or an unverified game-binary patch.
