# Detroit memory investigation — 2026-10-17

## Result

The existing logs do not identify one 9 GiB leak. They do establish that the
large remainder is CPU/process memory, much of it compressed, and that the
small caches with byte counters are not large enough to explain it.

At the highest MoltenVK sample in the 2026-10-16 run, process footprint was
10,977.8 MiB and Metal `currentAllocatedSize` was 2,435.6 MiB. The subtraction
is an 8,542.2 MiB non-Metal gap. The nearest task-ledger peak was 11,012 MiB:
4,251 MiB internal, 4,654 MiB compressed, 226 MiB external and 17 MiB reusable.
Compression later reached 5,110 MiB. The same run had at most 13,325 live
Vulkan pipelines, 349.2 MiB retained SPIR-V, 101.6 MiB compressed MSL, a full
32 MiB conversion cache, 1,024 cached PSOs, and a 256-job async queue.

The 2026-10-14 run reached 11,752.6 MiB at a MoltenVK sample, with 2,688.1 MiB
reported by Metal: a 9,064.5 MiB gap. Its task-ledger peak was 11,775 MiB, with
3,797 MiB internal, 5,125 MiB compressed, 74 MiB external and 38 MiB reusable;
compressed memory reached 7,173 MiB elsewhere in the run. The async queue
reached 2,178 jobs, versus 256 on October 16. It also reached 13,706 live
pipelines, 363.4 MiB SPIR-V, 105.4 MiB compressed MSL, 32 MiB conversion data
and 1,024 cached PSOs.

The JIT pool is not a 896 MiB committed allocation. Its logged high-water head
was 325.5 MiB of the 896 MiB virtual reservation in both runs. Tail allocations
also exist and the RX/RW mappings alias the same backing, so neither reservation
size nor the sum of both mappings is a valid resident-memory total.

The binary-archive log reports about 1,534 MiB of open archive *file-equivalent*
bytes (93–95 frozen pages). Metal does not expose their resident CPU cost. This
is a credible part of the unattributed gap, but 1,534 MiB must not be reported
as measured RAM. Likewise, PSO states, `MTLLibrary` objects, pipeline-owned C++
state and descriptor graphs have counts but no public byte-size API.

### What is measured versus inferred

Measured in the old logs:

- process ledgers, compression and Metal's device-wide allocated size;
- SPIR-V, compressed-MSL and conversion-cache payload bytes;
- live pipeline and cache entry counts;
- the JIT bump allocator's high-water head;
- async queue depth and binary-archive file-size proxy.

Inferred, not yet attributable from the old logs:

- most of the remaining 7.5–8.2 GiB after subtracting the explicitly counted
  CPU payloads from the non-Metal gap is guest/Wine/FEX heap and mappings,
  allocator slack, pipeline/library driver objects, or queued descriptor graphs;
- the October 14 queue is suspicious because it had 1,922 more retained jobs
  than October 16, but queue depth alone does not provide bytes;
- the 12–13k live pipelines and thousands of shader libraries can carry
  significant driver-private memory, but assigning a per-object estimate would
  be guesswork.

Fields overlap. In particular, malloc allocations live inside VM tags, JIT RX
and RW alias one object, Vulkan allocation sizes overlap Metal backing, and
Metal's counter is not guaranteed to be disjoint from every task ledger.

## Diagnostic added

MoltenVK now writes one `[mem-breakdown]` line every 30 seconds. It replaces the
old detailed `[mvk-memory]` sample; the existing startup marker remains. The
walk is performed by the existing utility sampler thread, not a render thread.

The line includes:

- `phys`, `internal`, `compressed`, `external`, `reusable`, and `metal`;
- aggregate malloc `used/allocated` bytes across all zones and per-zone values
  for the first 32 zones;
- `vm_tags`, the top 16 Mach tags by dirty+swapped bytes, encoded as
  `tag:dirty/swapped/resident`, plus `vm_tags_other`;
- JIT reservation and separate RX/RW `dirty/swapped/resident` bytes. Do not add
  RX and RW because they are aliases;
- async jobs as `total/queued/running`, completion-block count, and
  `owned_min`. This is a measured lower bound containing the job, key,
  descriptor object shell and copied block allocations; nested Metal descriptor
  graphs and retained functions/libraries remain outside it;
- live pipelines, live shader-library wrappers, PSO and process-wide library
  caches, SPIR-V, compressed MSL, conversion data and pending source collection;
- binary-archive page count/file-size proxy/budget and Vulkan/Metal resource
  counters.

`driver_pipeline_bytes=unknown` is deliberate. The log does not manufacture a
byte estimate for an API that Metal does not expose.

`MVK_MADEIRA_MEMORY_LOG=0` disables the 30-second line. It remains enabled by
default because the cadence is low and the VM/malloc walks are the evidence
needed to locate the gap.

## Safe memory controls added

No texture resolution, render feature, shader result, game setting or save file
is changed.

- The existing low-headroom policy still enters pressure mode below 512 MiB and
  recovers after 30 seconds continuously above 1 GiB. It already reduces PSO,
  process-wide `MTLLibrary`, and completed binary-archive retention.
- The pure SPIR-V-to-MSL conversion LRU is now trimmed to 8 MiB while pressure
  is active. `MVK_MADEIRA_CONVERSION_PRESSURE_MB=N` changes that target; `0`
  purges it under pressure. Its normal cap remains
  `MVK_MADEIRA_CONVERSION_CACHE_MB` (32 MiB by default).
- A Darwin `DISPATCH_SOURCE_TYPE_MEMORYPRESSURE` warn/critical event immediately
  invokes the same safe cache trims. Set
  `MVK_MADEIRA_MEMORYPRESSURE_SOURCE=0` to disable only this event source.
- On first entry to low-headroom pressure and on Darwin warn/critical events,
  `malloc_zone_pressure_relief(NULL, 0)` asks all malloc zones to return unused
  pages. `MVK_MADEIRA_MALLOC_PRESSURE_RELIEF=0` disables it. The returned byte
  count is logged; zero is a valid result.
- `MVK_MADEIRA_MEMORY_PRESSURE=0` remains the master opt-out for adaptive
  pressure handling, including the new event source.

The async admission cap remains off unless
`MVK_MADEIRA_ASYNC_ADMISSION=1`. It was not re-enabled as a memory fix: the
October 14 experiment produced multi-second producer stalls. Queued jobs cannot
simply be discarded because their completion blocks publish pipeline state to
live Vulkan pipelines. This change measures their lower-bound ownership first.

Shader modules retain SPIR-V and converted libraries because Vulkan permits a
live module to be used for later pipeline variants. Pipeline and shader-module
lifetime was not weakened. Pressure trimming releases only additional cache
references; independently owned live pipelines, modules, functions and
libraries remain valid.

## Verification

- `python3 tests/moltenvk/test-memory-pressure.py`: ASan/UBSan coverage for
  pressure hysteresis and real PSO, library and conversion-cache eviction.
- `python3 tests/moltenvk/test-admission.py`: admission/dedupe/pressure behavior,
  including the default-off policy.
- `python3 tests/moltenvk/test-no-skip.py`: wait/completion behavior and the
  default nonblocking path.
- `python3 tests/moltenvk/test-precompiled.py`: collection, quota, metadata,
  cache freshness, device-sync simulation and eviction repair.
- `bash tests/moltenvk/test-shader-prefetch.sh`: prefetch admission, bounds,
  persistence and reload behavior against the actual implementation.
- `PATH=/usr/bin:$PATH bash build/moltenvk-ios/build.sh`: succeeded and staged
  `app/Madeira/gl/libMoltenVK.dylib` (5,116,720 bytes).
- The generated patch was refreshed with
  `git -C research/MoltenVK diff HEAD` and passes reverse-apply checking.
- No application `xcodebuild`, commit, device write, game-setting change or save
  modification was performed.

## iPad test

Build and install once from Xcode, then replay the same Detroit chapter for at
least 30 minutes. Prefer the same graphics settings and a similar cold/warm
cache state. Capture the complete log.

Inspect every `[mem-breakdown]` line, especially the sample before a large
compression hitch:

1. If malloc `used` grows with `internal+compressed`, inspect the per-zone
   values. Large `allocated-used` is allocator slack; large `used` is live heap.
2. If one `vm_tags` dirty/swapped pair grows while malloc is flat, the memory is
   mapping-backed rather than ordinary malloc. Record the tag number.
3. Compare JIT RX and RW separately and use the larger alias, never their sum.
4. Compare `async_jobs`, `completions`, and `owned_min` with queue depth and
   hitch timing. A small `owned_min` does not clear the queue: it excludes the
   retained Metal object graph by design.
5. Watch `pipelines`, `shader_libraries`, `spirv`, `msl_compressed`, `pso`,
   `lib_cache`, and `archive_file_proxy` through scene changes. Counts that fall
   without footprint falling identify driver/allocator retention rather than a
   still-live MoltenVK owner.
6. On pressure, look for `[mvk-memory-pressure] dispatch=warn|critical` or
   `active=1`, followed by `malloc_relief_bytes=...`. Confirm conversion bytes
   fall toward 8 MiB and PSO/library/archive capacities contract.

For one controlled follow-up only, set
`MVK_MADEIRA_MALLOC_PRESSURE_RELIEF=0` and repeat the same scene to determine
whether allocator relief changes peak footprint or hitch severity. Do not turn
on async admission for this comparison.
