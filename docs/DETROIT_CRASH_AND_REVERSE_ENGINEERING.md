# Detroit crash and targeted reverse engineering — 2026-10-12

## Crash evidence

The latest device log was copied to `/private/tmp/madeira-detroit-crash-latest.txt`.
It ends abruptly after about 4,220 seconds, with no logged Windows exception,
device-lost error, or normal session end. The last memory report says 12,215 MiB
footprint, 12,270 MiB peak, 72 MiB available, and a 12,287 MiB process limit.
A separate footprint report reaches 12,279 MiB. An OOM/jetsam kill is therefore
the leading explanation, **not confirmed**: the accessible systemCrashLogs and
Retired listings had no matching new report when checked.

This log lacks `urgent-only`, `urgent-queued`, `conversion_cache=`, and
`[mvk-compile]`. It appears to be from the previous native library, not the three
compiler/cache changes built at 21:51. Do not call those changes regressions or
claim that this run validated them.

The `[perf]` frame/GPU fields are all zero in this run even while `[vk-fps]`
reports frames. Use the Vulkan reports, not those zero fields. Across 828
reporting windows there are 206,588 frames, 1.49% over 50 ms, and median window
FPS 52.3. These include loading/menu/pause periods; the largest interval, 19.2 s,
is not proof of a 19.2 s shader compile. Nine logged full-screen waits over 100 ms
sum to 1,523 ms. Synchronous shader-library preparation still reaches 593.6 ms.

## Memory and the implemented fix

The first periodic memory snapshot has 8,162 MiB footprint and 1,560 MiB Metal
current allocations; the last has 12,142 MiB and 2,940 MiB respectively. These are
snapshot values, not the separately sampled peak. The last standalone buffer
counter is about 705 MiB (842 objects), versus 54 MiB initially. Shader SPIR-V
payload is about 273 MiB, compressed MSL 77 MiB, live pipelines 10,549, PSO LRU
1,024. Heap placements overlap heap backing; do not add them to a total.
The remaining footprint is not measured by these GPU-resource counters: guest
memory, Wine/FEX/JIT, Metal driver/compiler/library/archive allocations, etc.

The archive log loaded 91 pages / 1,533 MiB at startup. The post-crash device
directory contains 116 archive files totaling 1,946 MiB. Source inspection
confirmed that each finished runtime page stayed open indefinitely: the existing
1,536 MiB cap applied only at startup. This is a real retention-growth bug, but
does not prove it is the sole cause of the kill.

`MVKBinaryArchiveCache` now trims the oldest finished Metal archive objects on
its serial writer queue whenever their backing-file bytes exceed
`MVK_MADEIRA_ARCHIVE_MB` (default unchanged at 1,536; zero remains unlimited).
It releases only cache-owned archive references, never live pipeline states or
shader modules, and leaves evicted disk files intact during play. Existing
startup disk pruning is unchanged. A future request may miss and recompile an
evicted pipeline. One completed batch may overshoot until trimming; the sixteen
active writer archives are outside this finished-page budget. File bytes are a
proxy, **not** an exact Metal CPU-residency measurement or total-process cap.

The new `[mvk-archive-memory]` line every 30 seconds reports `frozen_pages`,
`frozen_file_bytes`, `budget_file_bytes`, and `evictions`, explicitly identifying
unknown Metal residency and excluded active writers. Trimming locks only one
shard at a time and runs after releasing the writer's original shard lock.

Verification: `python3 tools/mvk-archive-budget-check.py` compiles the actual
archive class with host profiling stubs and ASan/UBSan. It checks cross-shard
eviction, byte/count accounting, concurrent shard readers, disk preservation,
and zero-budget unlimited mode. The exact native iOS build command succeeded:
`PATH=/usr/bin:$PATH bash build/moltenvk-ios/build.sh`. The patch reverse-apply
check passed, and the staged dylib contains the new marker. No app build,
device config edit, commit, or push was performed.

## Actual executable inspection

The device's 55 MiB x86-64 executable was copied to
`/private/tmp/DetroitBecomeHuman.exe`, SHA-256
`ecf52321921387e683904e089082d76b973326fc093af14e524056715519c1cf`.
LLVM disassembly plus PE `.pdata` unwind boundaries located string/IAT xrefs
and functions overlapping the profiler's hottest 4 KiB RVA pages. Targeted
Ghidra 11.4.2 headless decompilation produced 92 selected function outputs in
`/private/tmp/detroit-decompiled.c`; some unwind entries are function fragments,
and inferred types are not original source. Proprietary binaries/pseudocode
remain outside the repository. The Ghidra distribution was verified against
the [official release checksum](https://github.com/NationalSecurityAgency/ghidra/releases/tag/Ghidra_11.4.2_build).

Useful recovered locations (RVA, relative to the EXE image base):

| RVA | Interpretation / evidence |
| --- | --- |
| `0x64cf80` | Allocation-request search: free-block binary search, strategy constants `0x10000` / `0x10000000`, fallback traversal. |
| `0x64d8b0` | Allocation feasibility: size/alignment, neighboring buffer/image granularity conflicts, reclaimable allocations. |
| `0x6594f0` | Vulkan renderer/cache initialization, `ShaderCache/VkPipelineCache.bin`, `vkCreatePipelineCache`. |
| `0x65e620` | Pipeline-cache persistence: queries `vkGetPipelineCacheData`, writes after growth greater than `0x1ffff` (128 KiB), adds a `0x11c` game header. |
| `0x65ebf0` | Graphics descriptor assembly followed by `vkCreateGraphicsPipelines(..., count=1, ...)`. |
| `0x65fd80` | Compute descriptor assembly followed by `vkCreateComputePipelines(..., count=1, ...)`. |
| `0x66b2c0` | Shader module creation, optional `QDIF` metadata prefix stripped before `vkCreateShaderModule`. |

The most frequently reported game page is `0x64d000` (15,661 samples across the
run). Its allocator routines closely match `VmaBlockMetadata_Generic`'s
`CreateAllocationRequest` and `CheckAllocation` in the public
[VMA v2.3.0 implementation](https://github.com/GPUOpen-LibrariesAndSDKs/VulkanMemoryAllocator/blob/v2.3.0/src/vk_mem_alloc.h).
This is a source/control-flow match, not proof of the exact bundled VMA version.
The profiler rounds PCs to pages and prints only ten top entries per report:
these counts must not be interpreted as a whole-process CPU percentage.

## Next performance work, in useful order

1. Rebuild/install from Xcode and validate the three earlier compiler fixes plus
   runtime archive trimming in the same long scene sequence. Confirm build
   markers before comparing runs. Track archive evictions, total footprint,
   buffer growth and frame intervals; the cache cap alone cannot bound the game.
2. For cold-shader hitches, investigate earlier preparation/prewarming of actual
   pipeline descriptors, not merely shader blobs: the game creates single PSOs
   and has its own persistent Vulkan cache, but Wine intentionally suppresses
   the unbounded real host cache (`ios_pipeline_cache`). Do not turn that host
   cache back on to remove stutters: it previously OOMed Detroit.
3. Refine guest PC sampling within the allocator page before attempting a
   version-gated native allocator fast path. Decompiled control flow makes a
   targeted hook plausible, but ABI/layout/ownership and executable-hash checks
   are prerequisites. Do not blindly patch the EXE, change resource lifetimes,
   disable TSO, or report a speculative speed-up as measured.

Floating hair/stray meshes remain a separate correctness issue. This crash and
decompilation do not identify their cause. FULL_BARRIERS remains 1; mode 2 remains
a separate compute-ordering experiment, not a silently applied performance fix.
