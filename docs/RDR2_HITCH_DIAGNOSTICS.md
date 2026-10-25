# RDR2 hitch diagnostics — October 21

This build adds measurements, not a claimed performance fix. It preserves
rendering, synchronization, residency, file-read results, game settings and saves.
RDR2 uses the custom D3D12-to-Metal renderer, not MoltenVK.

## New observations available in the next device log

- `nextDrawable ... layer=... wine_pid=... wine_tid=... native_tid=...`:
  attribute the previously global ~100ms drawable waits. `frame_hook` says
  whether the call also contributed to the shared native frame counters.
  Unknown Wine ownership is zero; native thread ID remains available.
- `[d3d12-frame-window]`: per-swapchain, per-process Present-entry rate, mean,
  maximum interval and counts above 33.3/50/100ms every five seconds. These
  are application submission intervals, **not display completions**. Shared
  native `[perf]` counters are now explicitly labeled `scope=all-processes`.
- `[present-api-hitch]`: spacing between application Present entries versus
  time spent inside Present. A long spacing with a short call points upstream
  of Present, but does not itself identify a game function.
- `[present-hitch]`: worker-side reclaim/flush, capture/control/GPU-latency,
  setup, drawable acquisition and blit/commit time, plus committed/completed
  GPU serials. `control_latency` combines control work and latency wait;
  existing XP counters distinguish their GPU-wait component.
- `[queue-hitch]`: job kind, list/command count, queue residence versus worker
  execution time (including autorelease-pool cleanup). `[ecl-caller-hitch]`,
  `[queue-drain-hitch]` and `[present-throttle-hitch]` cover caller backpressure.
- `[pso-hitch]`: pipeline identity/stage names and separate lock, library
  restore and Metal compilation costs. `[pso-lock-hitch]` also catches a
  caller waiting for another thread to build the same pipeline.
- `[allocation-hitch]`, `[heap-hitch]`, `[allocation-census]`: resource/heap
  creation stalls, dimensions/mips/format, requested heap bytes and cumulative
  resource creation counts. Dimensions are not resident-byte estimates.
- `[perf-io]`: completed read/read-at count, bytes, aggregate call time,
  maximum, errors and short/EOF count per reporting window, across processes.
  Aggregate elapsed time can exceed wall time because reads overlap. This
  does not cover mmap page faults, socket recv, writes or all GPU uploads.
- `[io-hitch]`: slow or failed read with process/thread IDs, file descriptor,
  requested/result bytes, offset, elapsed time and fd path when obtainable.
  Includes alias/short-read repair and VM-lock delay, not just kernel I/O.
  File paths are diagnostic fd lookups, not persistent handle identities.
- `[stall-window]`, `[stall-pc]`, `[stall-io]`: during an XP sample with no
  completed D3D12 Presents, sample rendering-role and busiest native threads,
  PC/LR, run/suspend state, fault-safe guest RIP resolution where possible,
  and in-flight reads that have not returned yet. `[stall-pc-cost]` measures
  the diagnostic work itself. These are live PC samples, **not coherent call
  stacks**. No new thread suspension or stack walk is added.

## Cost and interpretation boundaries

The existing `MADEIRA_PERF=1` activates native read instrumentation and gap
snapshots; no device config edits are needed for the Steam RDR2 entry.
Snapshot selection is capped at 16 threads and at most once per second, from
the existing ~250ms XP probe. It can miss a shorter spike or a stall between
samples. Loading, pause screens and backgrounding can also trigger it.
Role registrations can include old threads; missing PCs are not proof of idle
CPU. Failed Mach state reads and unresolved guest RIPs are expected limitations.

Read tracking uses 512 static per-thread slots, no per-read allocations and no
target-thread suspension. Slot exhaustion only drops in-flight tracking and is
reported as `untracked_threads`; completed-read telemetry continues. In-flight
snapshots prioritize positioned reads and show at most 32 active calls.

Slow-path events use first-occurrence and periodic sampling to keep logging
from dominating normal frames; major >=100ms I/O, resource, pipeline and worker
job events bypass their lower-cost sampling. Native drawable lines retain the
existing first-16/every-64 policy. No per-draw print loop is added. Large logs
are acceptable, but event absence still cannot prove the absence of a stall.

## Verification and device run

- `tests/host/check-hitch-logging.py`: actual production read wrappers,
  result/errno preservation, disabled fast path, caller IDs, multithreaded
  counters, active-slot lifetime and slot exhaustion under ASan/UBSan.
- Existing AIR cache, typed-view index, PSO warmer and KUSER tests pass.
- Existing swap-decommit, sampler-safety and diagnostic-policy tests pass.
- Native D3D12 PE build, ntdll build (42 units / zero failures) and native
  DXMT build (65 objects / zero failures) pass with `/usr/bin` first in PATH.
  Archives staged by scripts; both D3D12 DLLs explicitly copied into the app.
  DLL SHA256 after v2 completion-counter correction:
  `599edc91f763a17f8972cd6ee6ee194d2430d24e976646de0d509574d3f486fe`.
- No app build, device config/save change, FEX source edit, commit or push.

Build/install from Xcode, play the opening and a house/town area for 5–10
minutes without changing graphics settings or backgrounding, then AirDrop the
new RDR2 log. Confirm the `[perf-hitch] diagnostics v2` marker. Correlate the
game-owned frame windows and gaps with pipeline, queue, I/O and PC events;
do not reuse the mixed-process native FPS as an unquestioned game-only metric.

## Device finding and v2 correction

The 22:51:26 log confirmed that sampled ~100ms native drawable waits belong to
pid0224/layer0x12b2ba580, not RDR2 pid040c/layer0x12b2b9140. It also exposed a
diagnostic blind spot: RDR2 uses synchronous replay (`async-submit=0`), whereas
the old XP `pres_done` counter incremented only inside the async worker. Thus
the zero-present snapshot condition never armed for this game.

Completion counting now lives in `mad_present_run`, shared by both execution
modes, and is removed from the worker to avoid double counting. This counts
Metal-side Present calls reaching their normal end, not GPU/display retirement.
Rendering and submission policy remain unchanged; async submission is not
enabled as a workaround. The trigger is tested for startup, progress, stalled
completion counts, one-second rate limiting, reset, disabled and invalid cases.
Source wiring checks also ensure the shared path increments exactly once and
the worker does not increment again. ntdll and D3D12 rebuilt/staged after this
correction; DXMT was unchanged from the preceding verified native build.

## v3 (October 24): wait targets, wakers, uninterruptible pages

The 23:16 log put RDR2's render thread (tid 048c) in `wait_select_reply`
during the ~0.7 s gaps, with other game threads Mach-UNINTERRUPTIBLE at game
PCs. v3 measures what the wait is for and why it ends late. Still diagnostics
only: no wait, timeout, signal, swap or rendering policy changed.

- The in-flight wait registry (ml585) now has one slot per Wine thread id
  (`build/ntdll-unix/wait_registry_ios.h`, shared with the wineserver).
  `server_select` publishes every server wait itself: op, handles, keyed-event
  key or signal handle, flags, timeout, cookie, Wine/native ids, syscall frame
  and phase (request / wait-pipe / kernel APC). A wait nested in a kernel APC
  restores the outer one; a thread killed mid-wait cannot leak its entry to the
  next thread with the same id. The old TEB>>16 hash put several threads in
  one slot.
- `[wait-done]` (MADEIRA_PERF=1): a slow server wait of a thread a snapshot
  selected in the last 5 s (>= 20 ms), or of a role thread (>= 100 ms), with
  return status, `waker`/`waker_pid` (the thread whose request ended it;
  `server/0000` = timeout, timer or exit, written by the server before the
  reply) and `wake_to_return_ms`. Long wait + short wake_to_return = the
  producer signalled late; long wake_to_return = the woken thread ran late.
  `deadline_left_ms` < 0 on a timeout = timer lateness. Capped 40 lines/s.
- `[stall-wait]` + `[stall-wait-stack]`: selected threads' current waits with
  age and phase, syscall-frame pc/lr, fp chain and guest-module stack words
  (module+RVA per process; stack words are candidates, not a call chain).
  `[stall-waits]`: other waits of the same processes begun in the last 2 s.
  `[stall-wait-summary]`: how many selected threads are not in a server wait
  (running, faulting, fast-sync parked, Mach/mutex waits).
- `[stall-wait-srv]`: answered by the wineserver thread on its next pass
  (`answered_after_ms`), from its own wait record: select op, timeout left,
  every waited object with type, name, waiter count, the event's lifetime
  set/reset counts and `last_set_tid` (who normally signals it) and its state.
  Read only. `wine/server/thread.c` gained the waker hook and a wait accessor,
  `wine/server/event.c` an event-stats accessor.
- `[stall-thread]`: name, run state, cumulative user/system ms and priority
  per selected thread. `[stall-pc2]`: run state and PC again 15 ms later
  (state 4 with `same-pc` = a long kernel wait).
- `[stall-fault]`: for a non-running thread outside any library, the load/
  store at its PC decoded against its registers (`arm64_mem_ea.h`), then the
  page's kernel state (present / compressed-or-paged-out / file-backed),
  region tag/share mode/resident/swapped-out pages and whether it lies in the
  swap-tier file with its offset. Registers and PC are live samples.
- `[stall-vm]`: task faults/pageins/COW/decompressions and host pageins,
  (de)compressions, swap-ins/outs over the XP window leading into the stall.
- `[stall-swap]`: whether virtual_mutex is held at that moment, the punch
  queue depth, and any swap-file punch/msync/mmap in flight with its age.

Reading the next log: find each `[stall-window]` with zero completed
Presents; take the render thread's `[stall-wait]`/`[stall-wait-srv]` object
and its `last_set_tid`, then that thread's `[wait-done]` waker and
wake_to_return; check whether the producer tid is itself in `[stall-waits]`,
uninterruptible in `[stall-thread]`, or faulting on a swap/compressed page in
`[stall-fault]` while `[stall-swap]` shows a punch or rotation in flight.

Verification: `tests/host/check-wait-probe.py` runs the production registry
(publish, nesting, collision, dead-owner reuse, wake matching, 20k concurrent
consistent reads) and the decoder on 15 instruction forms assembled by the
host compiler, under ASan/UBSan with recovery off; plus wiring checks.
Existing AIR, typed-view, PSO-warm, KUSER, hitch-logging, swap-decommit,
sampler and policy tests pass. `check-swap-coverage.py` needs Linux
(`linux/falloc.h`) and was not run. ntdll (42/0) and wineserver rebuilt and
staged with `/usr/bin` first in PATH; no D3D12/DXMT change.
