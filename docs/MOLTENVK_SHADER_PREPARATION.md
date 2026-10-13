# MoltenVK shader preparation locking

Madeira narrows `MVKPipelineCache` synchronization so synchronous shader preparation for unrelated shader-module keys can run concurrently. The pipeline-cache map uses a shared/exclusive lock, and each `MVKShaderLibraryCache` bucket has its own mutex. A lookup holds a shared map lock and one bucket lock while it checks, converts, and creates the Metal library. This preserves lookup/creation deduplication for identical module keys without keeping every other module key behind the conversion or Metal compiler call.

`MVKShaderModule::_accessLock` still serializes use of its SPIR-V converter, including when a module is used with different pipeline caches. The lock order on the preparation path is module, pipeline-cache map, then bucket. Cache serialization takes only the map lock exclusively, so it cannot observe a bucket while its vector is changing and does not introduce a reverse bucket-to-map edge. Merges take exclusive locks on internally synchronized destination and source caches; `std::lock` avoids deadlock for opposing merges, and self-merges are skipped. An exclusive source lock is necessary because a shared source lock would still permit bucket mutation. Caches with Vulkan's externally synchronized flag retain their caller-synchronized behavior, with the internally synchronized merge flag honored separately.

The cached serialization size is atomic because independent buckets can now invalidate it concurrently. Exclusive serialization still provides the stable map and bucket iterators needed to calculate or write one snapshot.

## Stall logging

Shader preparation intervals of at least 50 ms are logged by default. Set `MVK_MADEIRA_SHADER_PREP_LOG_MS` to a different positive integer before device creation to change the threshold, or to `0` to disable these diagnostic lines.

Each `[mvk-shader-prep]` line reports:

- `map-wait`: acquisition and, for a new key, creation/reacquisition of the cache-wide map lock.
- `bucket-wait`: waiting behind preparation of the same shader-module key.
- `convert`: synchronous SPIR-V-to-MSL conversion time on a cache miss.
- `library`: synchronous `MVKShaderLibrary`/Metal library creation time after conversion.
- `prep`: total work after both cache locks were acquired, including lookup and failure handling.
- `added` and `success`: whether a cache entry was created and whether a library was returned.

`[mvk-shader-module]` reports `module-wait` separately when a call waits on the module's converter mutex before reaching the pipeline cache. That interval is not included in the cache preparation fields. No per-shader lines are emitted below the configured threshold. Existing `[mvk-time]` aggregate counters remain unchanged. Conversion and library creation still run synchronously; this change removes serialization between unrelated module keys rather than moving those operations to background workers.

## Compiler submission and urgent dependencies

Async-worker fallback compilation now submits Metal's completion-handler API while holding the device monitor, then releases that monitor before waiting for completion. Descriptor ownership remains with the scheduler job; callback state and errors are retained until the worker has received them. `[mvk-compile]` separates `device-lock`, `submit`, and `completion` time for slow fallback calls. Library misses and archive-shard contention can still block; this does not make all shader preparation asynchronous.

The scheduler has its configured background workers plus one normally idle urgent-only worker. Full-screen waits and pending compute dispatches mark their dependencies urgent. The dedicated worker does not take speculative background jobs; regular workers still help with urgent work. Selection searches for an urgent job even when ordinary draw reprioritization moves a non-urgent job to the front. Shutdown drains work with the regular workers and joins every worker. `urgent-queued` measures time after the blocking dependency was requested, separately from the job's total pre-existing queue age.

The extra worker may increase CPU/compiler load while urgent jobs exist. It cannot preempt an already-running Metal compilation or bypass a busy archive shard, so reduced hitching still needs device measurement. The graphics wait cap and draw-skipping behavior are unchanged; compute dispatches continue to wait rather than being skipped.

## Bounded conversion-result reuse

Before converting SPIR-V, module-local misses can consult a process-wide LRU containing pure conversion results. Hashes select a candidate bucket; the complete SPIR-V vector and MoltenVK's existing configuration matcher determine a hit. Reflection/used-resource flags are restored with the existing `alignWith` operation. Only successful conversions are stored. Debug-code logging and explicit shader dumps bypass reuse so their output remains available.

The cache defaults to 32 MiB of charged payload/container storage and 512 entries, and evicts before exceeding either limit. It does not retain modules, `MTLLibrary`, `MTLFunction`, or pipeline objects. `MVK_MADEIRA_CONVERSION_CACHE_MB=0` disables it. The periodic memory breakdown includes occupancy, charged bytes, hits, misses and evictions. Actual conversion timings continue to count real conversions; cache hits are counted separately.

Verification: the iOS native build passes. A standalone test of the production Metal completion helper compiled three compute/render pipelines concurrently on macOS. Tests of the production queue selector cover urgent work behind a non-urgent head and shutdown. Tests of the conversion-cache container cover forced hash collisions, configuration/reflection handling, duplicates, oversize rejection, both limits, eviction and concurrent access under AddressSanitizer/UndefinedBehaviorSanitizer. These host checks do not establish iPad frame pacing or graphical correctness.
