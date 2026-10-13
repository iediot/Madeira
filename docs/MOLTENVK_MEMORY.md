# MoltenVK memory breakdown

Madeira's MoltenVK fork writes one `[mvk-memory]` line to standard error every 30 seconds. Logging is enabled by default. Set `MVK_MADEIRA_MEMORY_LOG=0` before device creation to disable both the sampler thread and its log lines.

The sampler does not enumerate Vulkan or Metal objects. Allocation and destruction paths update relaxed atomic counters, and the timer reads those counters. It does not retain resources, compile pipelines, create lazy textures, or scan caches. The only locks taken by the timer are the existing PSO-cache lock for its entry count and, separately, the residency-set monitor for pending-removal bytes.

## Fields

Byte values are raw bytes. Values written as `count/bytes` contain the number of live objects followed by their byte count.

- `phys_footprint`: `TASK_VM_INFO.phys_footprint` for the process. This includes CPU, Metal, driver, mapped, compressed-accounting, and other process memory; it is not a MoltenVK subtotal. It is `0` if the task query fails.
- `metal_current_allocated`: Metal's `MTLDevice.currentAllocatedSize`. Apple does not expose a category breakdown for this value.
- `vk_device_memory`: live `VkDeviceMemory` allocation count and the byte sizes requested by `vkAllocateMemory`. This is Vulkan address-space accounting, not proof that Metal has committed the same number of bytes.
- `metal_heap_backing`: live placement `MTLHeap` count and heap sizes allocated by MoltenVK.
- `metal_device_memory_buffer`: live standalone `MTLBuffer` objects allocated as the backing of a `VkDeviceMemory`, with their `allocatedSize`.
- `metal_buffer`: live standalone `MTLBuffer` count and each resource's `allocatedSize`.
- `metal_heap_buffer_placements`: live `MTLBuffer` objects placed in an `MTLHeap`, with their `allocatedSize`.
- `metal_texture`: live standalone `MTLTexture` count and each resource's `allocatedSize`.
- `metal_heap_texture_placements`: live `MTLTexture` objects placed in an `MTLHeap`, with their `allocatedSize`.
- `residency_pending_removal`: bytes removed from the residency set but retained by an uncommitted lazy residency-set update. It returns to zero when the set is committed. This is a retention diagnostic, not an allocation total.
- `pipelines_live`: live Vulkan `VkPipeline` objects, including graphics and compute pipelines.
- `pso_cache`: process-global retained Metal pipeline-state cache entries and configured capacity, written as `entries/capacity`.
- `pipeline_cache_objects`: live Vulkan `VkPipelineCache` objects.
- `pipeline_cache_buckets`: live shader-module-key buckets owned by Vulkan pipeline caches.
- `pipeline_cache_entries`: live shader-library entries in those buckets.
- `shader_spirv_payload`: live retained SPIR-V vectors and their logical payload bytes.
- `shader_msl_compressed_payload`: live compressed MSL payload vectors and their logical payload bytes. This includes module-local and Vulkan-pipeline-cache shader libraries, including specialization copies. Container capacity and Metal's private `MTLLibrary` storage are not exposed here.
- `conversion_cache`: process-wide conversion-result cache entries and charged payload/container bytes, written as `entries/bytes`. Default limits are 32 MiB and 512 entries. It retains SPIR-V, converter configuration, MSL and reflection results, never Metal libraries or pipeline states. `conversion_cache_cap`, `conversion_hits`, `conversion_misses`, and `conversion_evictions` show the budget and reuse. The charged byte count includes vector/string capacity and conservative container allowances; allocator/index metadata is not a measured allocation total. `MVK_MADEIRA_CONVERSION_CACHE_MB=0` disables reuse; positive budgets are capped at 256 MiB.
- `driver_pipeline_bytes=unknown`: explicit reminder that Metal exposes neither per-pipeline-state allocation sizes nor a driver/pipeline-memory total. MoltenVK does not estimate it.

## Accounting boundaries

Do not add the fields into a total:

- `phys_footprint` and `metal_current_allocated` overlap the categorized allocations and each other in platform-defined ways.
- Heap placement fields describe resources inside `metal_heap_backing`; their bytes overlap the heap reservation. They are useful for identifying heap occupancy, not additional backing.
- `vk_device_memory` is the application's requested Vulkan allocation space. It can back Metal heaps, buffers, and textures and therefore overlaps all corresponding Metal fields.
- `residency_pending_removal` is already represented by the associated resources until their releases complete.

Texture views, buffer-backed texture views, heap aliases created only to form image views, IOSurface-backed textures, and retained references to an existing allocation are deliberately not counted as new allocations. Imported Metal resources are also excluded because MoltenVK did not allocate their backing. Drawable allocations and Metal/driver-private storage for pipeline states, shader libraries, binary archives, residency sets, command buffers, and encoders have no reliable public per-object byte size and remain part of the gap between the categorized fields and the process/Metal observations.

Small utility allocations outside the logical-device ownership graph (the optional watermark), and frame-dump readback buffers that can outlive a command completion while their file write is queued, are not included. Normal command-resource pools, descriptor pools, visibility/query buffers, dummy resources, Vulkan image/buffer backing, and headless swapchain textures are included.

Counters are updated after successful Metal allocation and before MoltenVK releases its owning reference. Metal command buffers or residency sets may keep an allocation alive beyond that release, so these counters describe MoltenVK-owned backing rather than all outstanding driver allocations. Buffers wrapping imported host pointers may share storage with guest memory; their `allocatedSize` is not an additional process-memory cost. Rebinding releases owned backing before replacing it. Device shutdown stops and joins the sampler before destroying device-owned caches, avoiding logger/resource lock inversion and use after device destruction.
