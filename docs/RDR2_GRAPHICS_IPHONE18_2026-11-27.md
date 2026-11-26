# RDR2 graphics path on the iPhone 18 Pro, 2026-11-27

This note covers the evidence and the changes from one session. None of these
changes has been measured on a device yet.

## Which renderer RDR2 uses

The RDR2 profile in `Library.swift` sets `WINE_D3D_CONFIG=renderer=no3d`. The
log shows `device created: madeira-d3d12 M2 Nov 24 2026 13:31:47 [arm64ec]`, so
the path is the custom madeira-d3d12, through winemetal, to Metal. RDR2 does not
use MoltenVK on this route. Most shaders go through the sm5/dxbc (AIRCONV)
backend, and the config is `async-submit = 0`, `fence-chain = 6`.

## Evidence

Source log: `~/MadeiraLogs/iphone18/1127-0019-ran/madeira-log.prev.txt`, game
pid 048c, render thread 0500, gameplay from 23:30.

### What the `[xp]` fields mean

The `[xp]` line is built in `build/ntdll-unix/server_ios.c`. Its counters come
from `g_xp` in `madeira_d3d12.c`:

| Field | Meaning |
| --- | --- |
| `pres a/b` | Present calls entered / Present calls that reached the end of the Metal-side work |
| `flush` | `mad_device_flush_all` time inside Present |
| `lat n/ms` | ml1070 frames-in-flight GPU waits (frame N-2) |
| `draw` | Time in `nextDrawable` |
| `cmt` | Present blit, `presentDrawable` and commit |
| `caller-ecl n/ms` | Wall time the game's threads spent inside `ExecuteCommandLists` |

All times are milliseconds summed over the roughly 2 s window.

### Steady gameplay (ml1108/ml1119 `[perf]` lines)

- Frames take 45-53 ms (19-22 fps).
- The GPU is busy only 38-42% of wall time, at 18.5-21 ms per frame.
  `cpu_blocked_in_retire` is about 0.
- Present costs 0.3-0.7 ms, and `lat` and `draw` are near 0.
- These numbers mean the frames are not limited by the GPU or by Present.
- `ExecuteCommandLists` takes 25-29 ms of each frame on the game's render
  thread. It replays synchronously.
- Of that time:
  - Metal encode calls take about 4-5 ms.
  - Encoder open and end take about 4-5 ms, across 260 encoders.
  - About 17 ms is the replay itself: tables, bindings and state.
- The render thread uses only about 18 ms of CPU per frame (from `[xp-t]`
  EP0500: 554 ms P plus 163 ms E per 2 s), but spends 25 ms of wall time in
  `ExecuteCommandLists`. So a large part of the replay is spent off the CPU or
  on the E cores at 1.44-1.8 GHz.

### Argument tables and root signatures

- The AIR table cache hit only about 18% of lookups: about 600 hits against
  about 4,800 misses per frame.
- RDR2 called `CreateRootSignature` 15,634 times in the run, and only 2 of
  those objects were ever destroyed.
- The root signatures carry up to 74 descriptor ranges (ml1009 high-water
  mark).

### Attachment traffic

- Per frame, the passes load about 392-413 MB and store about 445-465 MB, across
  about 159 passes.
- `possible with rp-carry 5.0-6.0 (177-215 MB)`: about 5 command-list
  boundaries per frame store and then reload the same targets.
- rp-discard would save nothing in this game: "loads of what they left
  undefined 0.0 MB" and "discarded 0.0".

### Shader and pipeline building

- Pipeline building during play is small. Lazy builds at first draw grew from
  about 600 to 750 over about 3 minutes, at about 1.2 ms each, with one burst of
  108.
- Every app rebuild starts the shader cache cold, because the cache directory
  hashes `MADEIRA_APP_IMAGE_UUID`. The log shows "removed 1 older build cache(s)".
  This affects loading, not the frame rate.

### The 60 fps phase (23:32:25-23:33:00)

In this window `draw` is about 1700 ms per 2 s, so Present was limited by the
display. P-core clocks ran at 3-4 GHz there, against 1.44 GHz during gameplay.
The thread-QoS work in `thread_ios.c` by another agent addresses the gameplay
clocks.

## Changes (madeira-d3d12, PE side)

| Change | What it does | Switch (default on) | Log marker |
| --- | --- | --- | --- |
| air-plan | Matching each DXBC declaration to its root parameter, range and offset, and computing each stage's root mask, used to happen at every bind. That work now happens once per pipeline stage and root-signature serial (`mad_air_route_find`/`apply`). | `air-plan` | `%ld stage plans` in the AIR line |
| view-info cache | Lookups of a texture's flat view and array length, and of a typed buffer's count and first element, are cached per queue. The cache is invalidated when the device's `vmap_gen` changes (an entry removed or replaced). Before, each lookup took `view_lock` twice per texture per stage. | `view-info-cache` | `view-info cache N hits, M misses` |
| use-dedup hash | The declared-resource set uses an open-addressed index instead of a linear scan of up to 96 entries. It gives the same answers as the scan. | (`use-dedup`) | — |
| rs-dedup | Identical root-signature blobs on the same device now return the same live object, as D3D12 on Windows does. | `rs-dedup` | `rs-dedup: ... gets the live object`, `(rs-dedup: N distinct, M calls given a live one)` in the pso time line |
| AIR miss reasons | Each cache miss is now classified: new stage, bound heap changed, or roots changed. This tells the next round what to fix. | — | `misses: ... new stage, ... bound heap changed, ... its roots changed` |
| RDR2 profile | `rp-carry = 1` is turned on. It is an existing upstream switch, and it is the only GPU-side change. | — | `rp-carry = 1: a render pass stays open ...`, gpu-traffic `carried N (M MB)` |

## Tests and builds

- Host tests:
  - New: `tests/host/check-air-plan.py`, with
    `check-air-plan.oldresolve.c` as a fixture holding the previous resolver.
    280k resolves agree with the previous resolver, including every message
    and the visibility-ALL fallback. The view-info cache and the use-dedup
    hash also agree with the uncached lookups and the linear scan.
  - New: `tests/host/check-rs-dedup.py`, including a concurrent
    create/release stress run.
  - Updated: `check-air-table-cache.py`, for the new signatures and the miss
    reasons.
  - The other d3d12 checks give the same results before and after the change.
    Their failures were already there before it: the catalog checks and the
    missing `GameRecommendations.swift`.
- `PATH=/usr/bin:$PATH bash build/madeira-d3d12/build-pe.sh`. The shader cache
  converter identity is unchanged (`71d7ea9b5dd2f88c`), so the device keeps its
  shader cache, apart from the existing app-UUID effect above. Both arm64ec
  copies are staged.

## What to check in the next device log

- **ml1119** `caller thread per frame: ExecuteCommandLists`: was 25-29 ms.
- **`[xp]`** `caller-ecl`: was about 1000-1200 ms per 2 s.
- **AIR line:** the hit rate and the miss split, `stage plans`, and the
  view-info hit rate.
- **pso time line:** the rs-dedup distinct and shared counts.
- **gpu-traffic:** the `carried` count and MB (expected about 5 per frame and
  about 180-215 MB). Load and store per frame should drop by that amount.
- **Visual checks of the carried passes:** check the G-buffer, shadows and
  water. If they look wrong, remove the `rp-carry = 1` line.
- **For attributing the rest of `ExecuteCommandLists`:** run once with
  `replay-split = 1` in `madeira.cfg`. It splits the call into lock wait,
  batch open, flush, draw and tables.
- **A further option, not enabled:** `async-submit = 1` moves the replay off
  the game's render thread (upstream ml1120). It needs its own A/B run.

## Second round: dead stores, encoder ends, ECL attribution (same day)

None of this has been measured on a device yet.

### What the 1127-0019 log says about the stores

- `ml1137 attachments` in gameplay: store 435-451 MB per census frame. Of that,
  "no use in the rest of the list" is 217-233 MB, "cleared next" 9.5-11.6 MB
  and "discarded next" 0 MB. DiscardResource runs about 55 times a frame
  ("whole discards 55.5"), but the old census only looked at the rest of the
  same list, so it could not say where those 217-233 MB go next.
- Each ExecuteCommandLists call has about 4 lists (about 78 lists and 19 calls
  per frame). RDR2 signals after almost every call, and a Signal commits the
  command buffer. A store can only be decided before its encoder ends, so the
  proof has to come from the same call.

### dead-store (default on; `dead-store = 0` turns it off)

A pass leaves out an attachment's store only when D3D12 itself proves that
nothing can read it. The proof is the first thing that happens to the
attachment after the pass's last draw, in the rest of the list or in the later
lists of the same call. Three things count as proof:

- a whole clear of the same view and aspect (always proof)
- a whole `DiscardResource` (proof by D3D12's rules; `dead-store-discard = 0`
  turns this one off)
- an aliasing barrier that names it as `pResourceBefore`, for a texture with
  the default layout and without simultaneous access. Off unless
  `dead-store-alias = 1`. The census counts it either way.

Anything else that names the attachment first keeps the store: a draw while it
is bound, a barrier that names it, a barrier that names every resource (except
a UAV-all barrier), a copy, a resolve, a partial clear or discard, the end of
the call, or 8192 commands. Another queue cannot see the gap, because it can
only follow the call through a fence signalled after the whole call.

Lists record a 64-bit `kill_bloom` filter: one bit for each whole clear, whole
discard and aliasing `pResourceBefore`. A pass takes Unknown store actions only
when the filter shows that a proof is possible later in the call. Its end then
scans once for all attachments and sets the store actions through op 24.
Capture frames and winemetal builds without op 24 keep the old stores.
Memoryless or transient textures are not used: whether a texture is ever
loaded is not known when it is created.

### end-fuse (default on; `end-fuse = 0` turns it off)

New winemetal MadeiraCtl op 25 (`dxmt/src/winemetal/unix/winemetal_unix.c`)
does three steps in one PE-to-unix transition: it sends the encoder's last
encode-batch chain, sets the late store actions and calls `endEncoding`.
Before, these were three separate calls. The Metal calls stay the same, in the
same order and on the same thread. The DLL probes for op 25 once. An old
winemetal or remote mode keeps the separate calls. At about 210 encoders a
frame, this saves about 210 transitions a frame, plus 2 more for each pass
whose stores are decided at its end.

### New counters (all per frame, always on unless noted)

- `[perf] ecl-steps`: ExecuteCommandLists wall time, split into queue lock
  wait, pipeline prebuild, command buffer open, list replay and mid-call
  commits. It also gives encoders opened by kind (render, compute, blit), open
  and end times shown separately, and how many ends op 25 fused.
- `[perf] dead-store`: passes decided at their end; attachments not stored, by
  proof, with MB; attachments stored, because something used them first or
  because no proof was possible ahead; commands scanned.
- `[perf] ml1137 stores with no use in their list` (census frames): this line
  splits the old "no use" bucket into these cases:
  - deactivated later in the same list
  - in the later lists of the call: cleared, discarded, deactivated, one of
    those behind a barrier that names every resource, used, or nothing
  - for "nothing": what names the texture first after the call (cleared,
    discarded, deactivated, loaded by a pass, other use) or nothing within
    3 presents
- `replay-split = 1` adds "of which pass changes" (passes begun through
  `exec_begin_render`) and "of which draws' residency" (useResource).

### What to read in the next device log

- `[madeira-d3d12] dead-store = 1 (discard proof 1, aliasing proof 0)`,
  `end-fuse = 1: ... (MadeiraCtl op 25)` and
  `rp-discard: stores before a discard are decided when the pass ends`. If
  op 24 or op 25 says "kept" or "separate calls", the app has an old
  `libdxmt_combined.a`.
- gpu-traffic `not stored` MB per frame should equal the dead-store line's
  not-stored total. Store MB should drop by the same amount.
- If the census line shows large "after the call: cleared/discarded" numbers,
  most of the dead stores are across calls, which this change cannot prove. If
  it shows large "deactivated", try `dead-store-alias = 1`, check visually and
  compare the numbers.
- Visual check: the G-buffer, shadows, water, reflections and post effects. If
  anything changes, set `dead-store-discard = 0` first, then `dead-store = 0`.
- `ecl-steps`: list replay against lock wait and prebuild. The encoder end time
  should drop compared with the old combined "encoder open+end" 3.2-3.8 ms.
