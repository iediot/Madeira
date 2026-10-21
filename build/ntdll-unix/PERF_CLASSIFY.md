# Main-thread time classifier

Enable in madeira.cfg before starting a Wine session:

```ini
env.MADEIRA_PERF_CLASSIFY = 1
```

Default off. Works independently of `MADEIRA_PERF=1`; enable both to correlate
with frame/GPU/CPU reports and the existing guest-RIP stack snapshots.

A normal-priority pthread sleeps 5 ms between samples. Once a second it selects
the registered Wine thread with the greatest user+system CPU delta over the
previous second (one initial second establishes the baseline). Ties prefer the
previous target. Native app/UI/driver threads are excluded. It retains a Mach
send right and reads ARM_THREAD_STATE64 without suspending the target. Exiting
threads and failed reads are skipped, with failed reads reported as `missed`.

Every five seconds `[perf-classify]` reports PC-class percentages of successful
samples. If the target changed, there is a separate line for each sampled
thread, never a mixture labelled with the final thread's ID. `tid` is the Wine
TEB ClientId.UniqueThread; `name` is the host thread name.

| Class | PC ownership |
| --- | --- |
| game | Translated guest code in registered FEX CodeBuffers, including JIT-pool buffers, outside registered PE copies and the selected EC dispatcher |
| dxmt | d3d11, d3d10core, dxgi, d3d9, d3d9-emulated, winemetal PE copies |
| winePE | Other registered native PE copies |
| wineUnix | Madeira's main Mach-O executable, corrected at report time when its symbol belongs to statically linked FEXCore |
| fex | libarm64ecfex/libwow64fex/xtajit* PE copies, dispatcher/compiler/lookup helpers, the non-CodeBuffer FEX arena, and symbol-identified FEXCore code in Madeira |
| mvk/metal | MoltenVK, Metal, IOGPU, AGX Mach-O images |
| system | Remaining Mach-O dylibs |
| blocked | Cached kernel wait/trap function PC ranges |
| other | Unknown PC |

`[perf-classify-state]` is a second, independent table of sample counts:
`running` (Mach TH_STATE_RUNNING, which includes runnable), `blocked` (WAITING
or UNINTERRUPTIBLE), `other` (e.g. stopped), `unknown` (state query failed).
The state query follows the PC query, so these are not an atomic snapshot.
A trap PC alone does **not** prove blocking: `read` can return immediately and
wait wrappers can be running. Compare the two tables. Percentages represent
sampled wall time, not on-core CPU time; a waiting thread can retain a PC outside
a known trap. Sleep and reporting overhead make the rate approximate.

Every thirty seconds `[perf-classify-top]` lists the eight hottest
module-relative 4 KB buckets across dxmt/winePE/wineUnix, including the owning
thread ID and sample count. PE offsets are RVAs in the copy; Mach-O offsets are
relative to the loaded image header. Buckets combine copies of the same named
module for a given thread. These can be resolved against matching binaries
without runtime symbolication of every sample.

`[perf-classify-fex]` separately lists the top 25 FEX symbols for the sampled
threads. Raw PCs are accumulated in a fixed table and passed to `dladdr()` only
at report time. When `dli_sname` is unavailable (JIT dispatcher, PE copy, FEX
arena, or a stripped/private symbol), the report uses image/region + 4 KB
offset. `pct` is the symbol's share of that thread's attributed FEX samples in
the 30-second window. The five-second line also splits FEX into dispatcher and
lookup, compile, syscall and thunk, signal and fault, and other groups. These
groups are report-time symbol-name heuristics; unsymbolized dispatcher pages
are still recognized from their known allocation.

`[perf-classify-system]` similarly lists the top ten system symbols per
30-second window. Raw system PCs are kept in a separate fixed table and passed
to `dladdr()` only at report time, so libsystem/libobjc waits, memcpy and other
native work remain in `system` but are identifiable.

`[perf-classify-game]` lists the top ten guest module + 4 KB offsets for JIT-pool
samples. It records the sampled PC and x28 block pointer, then reuses the
existing perf-stack host-PC-to-guest-RIP decoder and guest loader module map at
report time. A block retired before the report cannot be resolved and is
omitted.

## Implementation and limits

- The PE-copy registry owns cached export names. Publication, retirement and
  the once-per-second snapshot share `ios_pool_lock`. No sampler dereference
  reaches into an unloadable PE image. A newly loaded/unloaded/reused range can
  be misattributed until the next snapshot. Unknown export names are winePE.
- Mach-O `__TEXT` ranges and copied names are cached, refreshed when dyld's image
  count changes. Header/name reads are fault-safe. An unload+load that leaves
  the count unchanged can leave stale attribution until a later count change.
- Wait ranges are resolved once from libsystem_kernel with dlsym/dladdr, bounded
  by the next symbol and capped at 256 bytes. Missing/private symbols, longer
  wrappers and unlisted waits can be missed.
- FEX publishes its existing `VirtualName` allocation records to the unix side.
  Exact `FEXMemJIT` CodeBuffer ranges are snapshotted once per second and count
  as game even when iOS 27 places them inside `ios_fex_arena`. Later named owners
  retire overlapping stale CodeBuffer records. Dispatcher, CPU-backend, lookup
  and other arena allocations remain FEX. For pool-hosted ARM64EC dispatchers,
  TEB+0x1788 -> CPUArea+0x40 still identifies the four-page dispatcher without
  calling into FEX from the sampler.
- FEXCore and the iOS syscall/signal glue are static archives linked into the
  Madeira executable, not a framework. Image-range classification therefore
  initially sees that native code as wineUnix. The raw-PC report pass recognizes
  FEX symbol names and corrects the five-second totals without putting `dladdr`
  in the sampling loop.
- `game` means translated/generated pool code, not proof of the original x86
  module's identity: translated x86 Wine code and standalone pool trampolines
  also land there. PE-contained ARM64EC thunks count with their module; x18
  trampolines appended beyond SizeOfImage count as game. Static code in the
  main executable, including app glue, counts as wineUnix. There is no LR
  reassignment (LR is read alongside PC).
- The existing `[perf-stack]` resolves guest RIP using x28's block metadata.
  The classifier reads only BlockBegin while sampling and walks the mutable
  block/RIP tables at report time.
- Storage is fixed: 512 Wine threads/PE copies/FEX CodeBuffer ranges, 2048
  Mach-O text ranges, 1024 top buckets, 4096 FEX/native PCs, 2048 system PCs,
  2048 guest PCs, and eight per-report target
  rows. Overflowed tables are counted explicitly; a saturated table biases the
  top list toward PCs first seen in the interval. There are no explicit
  heap/VM allocations in the sampling loop, nor task_threads enumeration.
  Dyld refresh and logging may use library-internal allocation/locks. Logging,
  Mach queries and the one-second registry copy add profiling overhead.
