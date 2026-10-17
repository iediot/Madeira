# MoltenVK tile-store elision (2026-10-17)

## Scope and controls

This change targets attachment stores from Apple TBDR tile memory to system memory. It is disabled by default and does not change the existing Madeira barrier, asynchronous-pipeline, PSO-cache, FPS-cap, or memory-logging switches.

- `MVK_MADEIRA_STORE_ELISION=0` (default): no learning, logging, or store-action changes.
- `MVK_MADEIRA_STORE_ELISION=1`: learn and log; learned signatures are not applied. The static depth/stencil and MSAA-resolve cases described below can apply.
- `MVK_MADEIRA_STORE_ELISION=2`: learn, log, and apply signatures after the confidence threshold.
- `MVK_MADEIRA_STORE_ELISION_FRAMES=N`: consecutive wasted frames required before learned application; default `240`.

No additional Vulkan `LOAD_OP_LOAD` is removed. Full-attachment `CLEAR` and `DONT_CARE` already map to Metal clear/discard load actions. Those existing actions are used as proof that the preceding store was wasted. Partial render areas, resumed rendering, internal Metal render-pass restarts, and non-final subpass uses are excluded.

## What is tracked

Each `MVKImage` has a monotonic ID, an atomic last-consumed frame stamp, an atomic touch serial, and an atomic pending-store bit. The common descriptor-read path updates atomics; it enters the locked learning state only when a store is pending. Texture/view creation registers the Metal texture against its owning image so direct bindings and argument-buffer residency operations can recover the `MVKImage`.

A candidate signature contains:

- render-pass identity;
- subpass index;
- Vulkan attachment index and aspect;
- framebuffer width and height.

At a final whole-attachment `MTLStoreActionStore` or `MTLStoreActionStoreAndMultisampleResolve`, the tracker records the image, signature, format, extent, and estimated bytes. If the next tracked touch is a full first-use `CLEAR` or `DONT_CARE`, that signature receives one wasted-frame observation. Gaps reset the consecutive streak, and multiple observations in one frame count once. Any read or conservative/unknown touch before the overwrite permanently disqualifies that signature.

Mode 2 changes an eligible `Store` to `DontCare`, or `StoreAndMultisampleResolve` to `MultisampleResolve`. A subsequent read after an elided store emits an `ERROR consumed-after-elision` line, increments `reverted`, permanently disqualifies the signature, and restores stores on later uses.

The static cases are:

- a transient depth/stencil `STORE` image with no declared `SAMPLED`, `INPUT_ATTACHMENT`, `TRANSFER_SRC`, `STORAGE`, or `HOST_TRANSFER` usage;
- a transient MSAA attachment with a native resolve target and none of those declared read usages.

These apply in mode 1. The extra `TRANSIENT_ATTACHMENT` requirement is intentional: absence of sampled/input/transfer usage alone does not forbid a later attachment `LOAD_OP_LOAD`, so the broader proposed depth/stencil rule is not provably safe under Vulkan. The remaining proof assumes the title uses transient images according to their declared lifetime. A later attachment `LOAD` is still tracked and triggers the loud rollback path. This assumption must be validated in mode 1 on the target title before treating the estimate as safe.

Every approximately ten seconds, one `[mvk-store-elision]` summary reports tracked, eligible, actively elided, reverted, estimated bytes per frame not stored, and the top five signatures by cumulative wasted bytes. Formats are logged by MoltenVK name and Vulkan numeric value. The byte number is an estimate based on format footprint, framebuffer extent, layers, and sample count; it is not a hardware-counter measurement and does not include compression or implementation-specific tile behavior.

## Read and conservative-touch coverage

The following paths disqualify a pending store:

- sampled, storage, combined-image-sampler, and input-attachment descriptor images when a draw or dispatch binds/makes the resource resident;
- direct texture bindings and Metal argument-buffer resource-use operations;
- render-pass `LOAD_OP_LOAD`, including a requested load that Metal memoryless storage cannot honor;
- `vkCmdCopyImage`, `vkCmdBlitImage`, and `vkCmdResolveImage` sources;
- image-to-buffer copies;
- presentation;
- host image-to-image and image-to-memory copies;
- host-read image barriers and device-to-host synchronization/pull paths.

The tracker deliberately treats these write paths as reads because they can be partial or otherwise ambiguous: transfer/resolve destinations, buffer-to-image copies, host memory-to-image/image-to-image destinations, and explicit image clears. This loses optimization opportunities but prevents a later full overwrite from incorrectly classifying an earlier store as the next touch.

Shader descriptor writes do not count as use. Consumption is recorded later, when MoltenVK binds or makes the referenced resource resident for a draw or dispatch. Images and Metal texture views that alias the same texture are all conservatively marked.

## Risks and mitigations

- A read that exists outside the covered paths, or a read encoded out of CPU order across independently submitted Vulkan queues, can escape learning. Keep mode 1 running through gameplay, pause menus, chapter transitions, photo mode, screenshots, and video/cutscene paths before enabling mode 2.
- A very rare read can appear only after the 240-frame threshold. The first such read after application can show one incorrect frame. The atomic late-read check then logs loudly and permanently disables that signature for the process.
- Render-pass pointer identity is process-local. Learned state is not persisted across launches, so stale signatures cannot cross a game restart.
- Byte savings are estimated, not measured. Apple GPU compression, memoryless behavior, and driver decisions can make real traffic savings smaller or larger.
- Static usage-bit elision relies on valid Vulkan usage declarations. A title that later uses the same image as an attachment with `LOAD_OP_LOAD` is caught, but the first unexpected load after elision remains a possible bad frame.
- Mode 0 keeps the tracker and texture registry inactive. Mode 1/2 adds a texture-registry lookup at descriptor binding and atomic image updates; there are no per-draw heap allocations.

## Device test plan

Use the same Detroit scene, graphics settings, 30 fps cap, camera path, and approximate thermal starting state for every run. Preserve `MVK_MADEIRA_FULL_BARRIERS`, async-pipeline/compute, PSO-cache, FPS-cap, and memory-log settings.

1. Baseline: run with `MVK_MADEIRA_STORE_ELISION=0`. Record at least ten minutes of `[vk-gpu] busy ms/s (%)`, FPS, thermal state, and scene notes.
2. Learn-only: set `MVK_MADEIRA_STORE_ELISION=1` and leave `MVK_MADEIRA_STORE_ELISION_FRAMES=240`. Exercise normal play plus pause, menus, chapter transitions, cutscenes, and a screenshot. Save the periodic summaries and verify there are no `consumed-after-elision` errors from the static cases.
3. Inspect the top five. Confirm their format/extent are plausible scene attachments. Treat `estimated-bytes/frame` only as a ranking/traffic estimate.
4. Apply: set `MVK_MADEIRA_STORE_ELISION=2`, restart the game, and repeat the same scene long enough to relearn past 240 observations. Compare median and p90 `[vk-gpu] busy ms/s` and FPS against baseline and mode 1.
5. Correctness: compare the same camera frames, depth-of-field scenes, pause UI, screenshots, and transitions. Any `ERROR consumed-after-elision` means that signature reverted; capture the full line and the visual frame.
6. Repeat a mode-0 control after mode 2 if temperature or clocks changed materially. Report GPU busy and FPS as measured. Report estimated bytes, predicted bandwidth benefit, and causal attribution as assumptions unless Metal System Trace or equivalent hardware counters confirm them.

## Host validation

`python3 tests/moltenvk/test-store-elision.py` compiles the production state machine with ASan/UBSan and covers learn-only mode, consecutive-frame qualification, same-frame de-duplication, mode-2 application, static mode-1 application, late-read rollback, and permanent ineligibility.
