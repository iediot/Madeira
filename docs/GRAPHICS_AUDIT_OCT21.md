# Graphics implementation audit, October21

## Scope and evidence

Read-only review of the50most recently updated upstream Madeira PRs, older
graphics-specific PRs, newest75/top40fork listings, and default-branch compare
results for10selected active/popular forks. GitHub REST API was used because
`gh` is absent. This is a targeted sample, not a claim to audit every branch
of every fork. No bulk merge, ref checkout, commit or push.

Relevant distinction: DXMT already handles D3D11. RDR2's tested D3D12 route is
our `madeira-d3d12` directly calling winemetal/Metal, not Vulkan/MoltenVK.
MoltenVK fixes therefore cannot automatically cure this route's submission
cost. Its Vulkan route remains important for other games such as Detroit.

Latest bad run's200–330s windows:34.84FPS and median windowp95100.1ms,
GPU13.21ms, versus previous40.55/42.6/GPU12.97. Different routes/scenes mean
not controlledA/B. Renderer reports21–24FPS late, GPU38–45%wall busy and
15–20ms callerExecuteCommandLists: CPU submission is a meaningful target,
not proof that AIR table assembly alone causes all of that time.

## Mature implementation comparison

- [vkd3d-proton command.c](https://github.com/HansKristian-Work/vkd3d-proton/blob/master/libs/vkd3d/command.c):
  `d3d12_command_list_update_descriptors` uses separate root-descriptor,
  root-constant and descriptor-table dirty state. Our AIR cache used one
  graphics/compute root version, invalidating every stage on any root change.
- [Microsoft root signature overview](https://learn.microsoft.com/en-us/windows/win32/direct3d12/root-signatures-overview):
  individual bindings change independently and the driver versions bindings.
  This supports dependency-specific reuse, not ignoring descriptor mutations.
- [vkd3d-proton requirements](https://github.com/HansKristian-Work/vkd3d-proton#drivers):
  Vulkan1.3, extensive descriptor indexing and other features must be matched
  against the actual iOS driver before promising a replacement route.
- [CrossOver source](https://www.codeweavers.com/crossover/source): public FOSS
  components are available; that does not make every proprietary renderer a
  source-level, redistributable iOS drop-in. No GameHub internals assumed known.

## Upstream triage

| Source | Finding and disposition |
| --- | --- |
| [#340](https://github.com/willfaust/Madeira/pull/340), commit7236cc5 | Ported ONLY the typed-view hash index, with an allocation cap and independent tests. Did not import RGB32 conversions or the larger shadow-copy retention/refresh policy. |
| [#402](https://github.com/willfaust/Madeira/pull/402) | Ported the small handled-KUSER guard: don't rebase registers after the access has already been emulated. Correctness fix, not an advertised FPS gain. |
| [#403](https://github.com/willfaust/Madeira/pull/403) | Per-process default keyed events address a real shared-handle issue. Deferred for a separate process-lifetime/handle-failure audit; no evidence it explains this frame-time regression. |
| [#401](https://github.com/willfaust/Madeira/pull/401) | Opt-in thread-name QoS, backed by Dark SoulsIII evidence. Deferred: RDR2's relevant names/classes and an A/B have not been established. |
| [#386](https://github.com/willfaust/Madeira/pull/386) | Arena placement/startup protection; not evidence for improving an already-running RDR2 frame loop. No FEX source changes made. |
| [#353](https://github.com/willfaust/Madeira/pull/353) | Important experimental vkd3d-proton/DXVK alternative route. Author explicitly reports vkd3d-proton has not rendered a device frame yet. Keep as a bring-up investigation, not a proven faster replacement. |
| [#400](https://github.com/willfaust/Madeira/pull/400) | Build-time opt-in Vulkan route/fixtures. Useful validation ideas; would overlap our already-working Vulkan plumbing. No wholesale loader/driver replacement. |

Fork compares: wamei has substantive process/correctness work; dre4moff has
graphics/texture-policy/cache work; c-gow has OpenGL/audio/thin-reservation
work. JonathanStrout's differences are chiefly build automation. Sampled
Tomba247/cb93ka/SynesisDev/125hz/NevesHenry default branches show no commits
ahead of upstream; that does NOT mean their PR branches have no changes.
kuywoh's default is behind upstream without unique ahead commits.

[dre4moff](https://github.com/dre4moff/Madeira)'s diff includes texture mip
reduction, read-only barrier elision and heap/ring reclamation work. Did not
import its DLSS/texture reductions or change our synchronization: texture
quality is already a concern, and a broad integration would obscure this
regression. Our code already has several corresponding lifecycle fixes and
an opt-in read-to-read fence switch. Each remaining difference needs focused
correctness tests and device evidence rather than title-based cherry-picking.

## Implemented batch

1. **AIR dependencies:** per-root-parameter graphics/compute versions, plus a
   conservative shader/register/space dependency mask. Mask includes matching
   parameters of every visibility because the resolver has an ALL-visibility
   fallback. Signature changes invalidate every parameter; heap identity and
   heap descriptor versions still invalidate as before. Graphics/compute bind
   points are explicit cache keys. No new unbounded cache or GPU allocations.
   New mask is reused from matching cache-layout entries rather than reparsed
   for every root-only miss. Debug/capture bypass and batch lifetime remain.
   Logs count reuse across unrelated root changes. `air-table-cache=0` bypasses
   the cache through the unchanged table builder for same-build A/B.
2. **Typed-view index:** PR340's complete-key open-address index, keyed by
   format/offset/count/SRV-or-UAV. Starts at32views, load<=1/2; at most4MiB
   index per resource, then scan fallback. Allocation failure also drops to
   the original scan. Stable view indices, existing view_lock, no eviction,
   freed with the resource. `tv-hash=0` preserves the old scan.
3. **KUSER guard:** PR402, preserving the already-emulated register state.
4. **Warmer rollback/default safety:** the new native op10 is OFF unless BOTH
   `pso-warm-native=1` and `pso-warm>0`. Existing game `pso-warm=2` alone no
   longer enables the unproven work. Explicit log distinguishes declined/off
   from active. This is a conservative rollback, not a claim the warmer caused
   the late regression; its logged workload was earlier. The separate switch
   permits a true same-build warm-on/off experiment if wanted later.

## Verification and device handoff

- Production functions compiled/run under ASan+UBSan: AIR dependency/cache
  tests (used/unused roots, visibility fallback dependencies, bounded/unbounded
  ranges, spaces, constants, graphics/compute isolation, descriptor changes,
  capture bypass and cache-off); typed-view index versus scan across16384views,
  collisions, all key fields, invalidation, growth/OOM/cap/opt-out; actual KUSER
  redirect block; native warm queue enabled and disabled policy/lifetimes.
- Legacy `check-skin-check.py` could not start because this checkout lacks its
  referenced `app/Madeira/GameRecommendations.swift`. Not reported as passing;
  dedicated typed-view test covers the new index code independently.
- Native D3D12 PE, ntdll(42/0) and DXMT(65/0) rebuilt with PATH=/usr/bin first.
  Both D3D12 DLLs copied from build/madeira-d3d12/out-pe into
  app/Madeira/arm64ec-windows; all four SHA256 checks agree:
  cba460a7383f47a717c15bc22aa488230140ef6a9221695b79084ff33e5f948f.
  Native scripts staged their static archives; warmer-off string verified in
  the app's libdxmt_combined.a. No app build.
- No UI/device config/save/FEX/Wine changes. Existing project.pbxproj/Info.plist
  edits are unrelated and excluded from this batch.

User test: Xcode build/install, same opening/house route for5–10minutes. Check
warmer-off log, cache reuse/misses, callerECL and p95, visuals, memory and extent
counts. No measured device speedup claimed. If bad lows persist, same-build
cache-off A/B and sampled command-replay profiling are next, not more barriers
removed speculatively.

Next larger shader project: our native builders already accept Metal binary
archive lookup/serialization descriptors, but madeira-d3d12 does not wire a
device-owned persistent PSO archive into them. [Apple's archive workflow](https://developer.apple.com/documentation/metal/creating-binary-archives-from-device-built-pipeline-state-objects)
is a concrete design to evaluate for repeat-run first-use costs. It requires
per-device/version keys, safe concurrency, bounded archive memory/disk and
miss fallback; not implemented or represented as solved in this batch.
