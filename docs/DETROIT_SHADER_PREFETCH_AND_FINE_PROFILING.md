# Detroit follow-ups implemented — 2026-10-13

## 1. Offline libraries installed

Ran the existing tool with Apple's tools first in PATH:

```sh
PATH=/usr/bin:$PATH tools/precompile-msl.sh 00008142-000A38AE0147801C 9a5cb11eba58918b
```

Result: **117 compiled, 28,609 up-to-date, 117 pushed, zero failures**.
A subsequent device inventory showed 28,726 libraries and zero missing matches
for the 117 captured sources. Options and iPadOS-compatible deployment targets
were handled by the existing tool; runtime cache keys were preserved.
Log: `/private/tmp/madeira-oct13-precompile.log`.

This part needs no app rebuild. Restart Detroit and replay the section that
previously hitched. Actual device library-load success and performance still
need a new log; transfer success alone is not a measured stutter reduction.

## 2. Earlier preparation for observed library variants

`MVKShaderModule` now records the disk-cache identity, source length and exported
function name of the actual library selected for each shader, including macro
specialization variants. On a later SPIR-V module creation, a utility-priority
serial queue can load its previously observed libraries from disk into the
existing process LRU. This moves some disk-library preparation to module loading
rather than the pipeline's first use. It never guesses Vulkan binding layouts,
replays conversion settings, binds a hinted function, or changes shader output.
The normal conversion result and complete normal library key still select the
library used by the pipeline. A collision in the module hint can only request
an unnecessary prefetch; it does not select a different shader.

Bounds and lifetime:

- 32 admitted metadata/prefetch jobs; one loader active at a time.
- Four cached game/device namespaces, 512 module hints per namespace, two
  observed variants per hint; plist read/write size limited to 256 KiB.
- Coalesced atomic metadata writes, at most one delayed writer per context.
- No shader-module/Vulkan-device pointers in queued work. Metal device and
  Foundation payload lifetimes are retained; the game directory is captured
  before enqueue so a later game switch cannot redirect the load.
- No new unbounded library cache: the existing library LRU capacity is unchanged
  (256 by default). Prefetch can still displace another LRU entry, so its effect
  and cache-hit behavior must be measured.
- Missing/refused disk files fall back to the unchanged normal path. Prefetch
  itself does not compile source, enlarge the PSO cache, or fail a Vulkan shader.

The hints are stored at
`Library/Caches/mvk-binary-archive/<game>/msl/module-prefetch-v1.plist`.
Learning/request hooks run when async graphics pipelines are enabled. Set
`MVK_MADEIRA_SHADER_PREWARM=0` to disable learning and prefetch if needed.
Every 30 seconds `[mvk-shader-prefetch]` reports requests, successful loads,
learned associations, pending jobs, dropped jobs and write failures.

**Limits:** this needs a learning run before cross-launch hints exist. It helps
previously observed disk libraries, not genuinely unseen shader variants.
The remaining first-ever synchronous source compilation cannot safely be
eliminated by guessing a configuration at `vkCreateShaderModule`; the layout,
render-pass outputs and specialization choices do not exist there yet.
Fully deferring unknown library preparation would need a broader pipeline-
construction/lifetime change and remains follow-up work, not a claimed fix.

Implementation is in `MVKMadeiraShaderPrewarm.h` and the request/remember hooks in
`GPUObjects/MVKShaderModule.mm`; the header is included in
`patches/moltenvk-iediot.patch`. It preserves existing disk-library hash keys.

Verification:

```sh
bash tests/moltenvk/test-shader-prefetch.sh
bash tests/moltenvk/test-precompiled.sh
PATH=/usr/bin:$PATH bash build/moltenvk-ios/build.sh
```

The actual prefetch header is tested with host cache/Metal stubs under
AddressSanitizer/UBSan: disable behavior, admission/metadata/variant limits,
recipe validation, captured namespace, cache misses and persistence/reload.
The existing offline-library suite checks real host collection helpers, iOS
compilation/options, cache keys, freshness, failures and simulated transfers.
The native iOS build and patch reverse-apply check passed. iPad runtime benefit
has not yet been measured.

## 3. Finer allocator investigation

`build/ntdll-unix/perf_ios.c` still emits the existing 4 KiB page report, and now
also emits the top ten `[perf-classify-game-detail]` 16-byte buckets. Both use
the same already-collected samples and guest-module lookup. There are no new
thread suspensions or sampling frequency changes. An extra bounded aggregation
array costs roughly 448 KiB, and aggregation work runs only on the existing
report interval. Percentages retain the original per-thread sampled-game
denominator, not a whole-process CPU percentage. Top-ten printing remains a
selection bias; a 16-byte address bucket is not an exact instruction profile.
`MADEIRA_PERF_CLASSIFY_FINE=0` suppresses the fine report. Detroit's existing
`MADEIRA_PERF_CLASSIFY=1` is enough; its device config was not edited.

After collecting a new run:

```sh
python3 tools/detroit-hotspots.py /path/to/new-log.txt /path/to/DetroitBecomeHuman.exe
```

The tool verifies the captured EXE SHA-256 before applying known reverse-
engineered names, reads the actual PE `.pdata` function boundaries, follows
chained unwind entries, and flags buckets crossing different function roots as
ambiguous. Fixtures successfully map `0x64d8b0` to the allocation-feasibility/
alignment routine and `0x64d0f0` to the allocation-request search. These fixtures
are **not** real runtime fine samples; the current device log predates this
profiler. No native allocator fast path or EXE patch has been made: that decision
depends on which inner loops are actually hot in the next run.

Verification:

```sh
python3 tests/perf/test-guest-detail.py
python3 tests/perf/test-detroit-hotspots.py
PATH=/usr/bin:$PATH bash build/ntdll-unix/build.sh
```

The aggregation test compiles the actual C reporting functions under ASan/UBSan
with host fixtures, checking aggregation, denominators, unknown modules,
disable behavior and reset. The mapper checks ambiguity, gaps and chained
fragments, and was exercised against the actual captured executable. Native
build: 42 translation units succeeded, zero failures, updated
`app/Madeira/libntdll_unix.a`; the new report marker is present in the archive.

## iPad test

Build/install once from Xcode to link the two rebuilt native libraries. Play a
shader-heavy section to learn hints and collect fine allocator addresses, then
restart Detroit and replay the same section to exercise cross-launch prefetch.
Compare `[mvk-shader-prefetch] loaded`, library preparation durations, frame
intervals and total footprint; keep an eye on thermal pressure too. The cache
refresh already on the iPad can help either run independently of the new hints.

No app build, commit or push was performed here. No library JSON, JIT pool,
thread-count or barrier setting was changed. Hair/mesh correctness remains a
separate unresolved investigation.
