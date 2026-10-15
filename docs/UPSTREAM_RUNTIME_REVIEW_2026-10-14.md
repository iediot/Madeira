# Upstream runtime/fork review — 2026-10-14

Scope: stability, memory, CPU/thermal cost and game compatibility. No app UI
changes selected. This is a review, not a merge or implementation.

Reviewed 200 recently updated PR records and metadata for 212 distinct forks
(newest two pages plus starred forks). Fetched 27 selected PR heads and six
fork branches into `refs/review/oct14/*` for source/history comparisons. The
six fork branches are dre4moff, bahacan16, 125hz, jakub, u-tek and
Mattieus-sudo/Prototype2Fix. This is not an exhaustive source audit of all forks.
GitHub page counters and API snapshots differ slightly; do not infer an exact
live open-PR count from this snapshot.

Runtime, device configuration, app/native binaries and dependency pins were
not changed. Existing local research edits were preserved; no commit/push.
External device-test numbers below are authors' reports, not our own results.

## Recommended implementation order

### 1. Remove unsafe profiler/watchdog suspension windows

[PR #279](https://github.com/willfaust/Madeira/pull/279), especially commit
`e32dddc1`. **Definitely worth doing.** Current local
`build/ntdll-unix/server_ios.c:1030` suspends a thread and then calls
`pthread_from_mach_thread_np`; the watchdog at ~4915 logs through the app
before `thread_resume` at ~4968. Both remain present locally. The former may
take a thread-list lock held by the suspended thread; the latter can allocate
or take logging locks. Move lookup before suspend and resume before logging.
Keep the new unsuspended fine classifier; audit all suspension windows and
adapt the PR's host checker to our source.

This avoids introducing a freeze while measuring performance. It is not a
claim that the Oct13 audio hitches were caused by this sampler.

### 2. Audio callback lifetime/detach fix — our own runtime work

The newest local investigation correlated 26/27 ~600 ms hitch windows with a
501 ms stream-detach wait. Implement real read-side callback protection and
safe retirement rather than waiting for future callbacks. The inspected
dre4moff audio driver still has the same `cb_epoch`/two-future-pass wait, so
that fork is not a ready-made solution. See
[the local performance investigation](DETROIT_OCT14_PERFORMANCE_RESEARCH.md).
Test stopped/interrupted endpoints, concurrent release/resume and multiple
clients. Applies to the native audio driver used across games.

### 3. Small process/thread correctness fixes

- [#287](https://github.com/willfaust/Madeira/pull/287), `87a1c5c9`: close a
  child's unused server socket when boot fails before registration. Our boot
  fallback currently unlocks/releases its slot but lacks this staged socket
  cleanup. Prevents a parent/installer waiting forever for a phantom child.
  Verify FD ownership and our actual boot stages to avoid a double close.
- [#298](https://github.com/willfaust/Madeira/pull/298), `d08c4940`: services
  build an environment from registry rather than inheriting the JIT RW/RX
  variables; fill only missing values. Avoids a helper generating code through
  the wrong alias and faulting per store. Local helper is absent. Adapt its
  temporary-environment lifetime: the submitted code explicitly leaks one
  allocated block per affected spawn; do not copy that leak into a memory fix.
- [#278](https://github.com/willfaust/Madeira/pull/278), `b3512e39`: make a
  thread suspending itself wait for resume. Our native `NtSuspendThread` does
  not include this. Important for intro-video/worker loops; integrate with our
  existing cooperative WoW64/FEX suspend work, not around it.
- Jakub [registry reclamation](https://github.com/jakub/Madeira/commit/1363531f)
  plus [CAS/revalidation follow-up](https://github.com/jakub/Madeira/commit/c4b63414):
  our registry already reuses dead rows, but claims by TEB and does not carry
  the same saved TID/port-claim protocol. Review purge/register/reap races
  together and avoid reaping a recycled TEB's live lock. Treat both commits
  as design/reference, not a drop-in replacement of our larger handler.

### 4. Reduce background/diagnostic work, preserving useful measurements

Select from [#297](https://github.com/willfaust/Madeira/pull/297):

- `dfb565c4`: throttle expensive address-map/inventory sweeps to about a minute
  instead of every ~10 s. Local `virtual_ios.c` still uses `%5` map walks and
  `%15` inventory. Changes diagnostic cadence, not guest allocation policy.
- `c5cc7032`: opt-in lean diagnostics disables the thread-suspending burst
  profilers and makes scans rare. Retain cheap frame, memory and our fine
  classifier measurements when needed; do not conflate their switches.
- `4e20535b`: launcher/service threads can run at utility QoS while the game
  presents. Useful but the submitted game detector relies on D3D role-thread
  profiling; adapt it to Vulkan/OpenGL presents and actual session identity
  so this benefits more than D3D12. Ensure sign-in and IPC-dependent tasks are
  not starved. Existing audio RT threads must not be demoted indiscriminately.

Leave the larger power governor/helper-QoS/power-probe switches experimental.
The governor deliberately trades throughput for power; it is not free FPS.
An optional thermal frame limiter based on
[#246](https://github.com/willfaust/Madeira/pull/246) may improve sustained
consistency; it caps peak FPS and needs present-path validation per renderer.
No UI component is required to implement an opt-in runtime policy.

### 5. JIT capacity and cache memory: promising, needs lifetime proofs

Selectively study [#285](https://github.com/willfaust/Madeira/pull/285), not
its complete 22-commit launcher/pool/window bundle:

- `1a7cbb8a` tracks ownership of FEX code-buffer carves and retires those of
  exited pseudo-processes. Local carves have only `off,size,free` and ordinary
  process image cleanup does not cover this ownership. This can stop helper
  exits consuming JIT capacity and forcing small-buffer retranslations.
  It is **pool reuse**, not proof of reduced app physical footprint: the pool
  was reserved/claimed earlier. A grace period plus current-PC scan alone is
  not enough to prove a parked thread has no future return into old code.
  Verify process/thread quiescence, saved contexts, return paths and FEX stale
  aliases before enabling reuse.
- `6294615e`, opt-in pure-x64 image no-copy: potentially avoids duplicate
  images in blessed pool space. Hybrid ARM64EC images must still be copied;
  self-modification, loader, protection and alias transitions need validation.
  Earlier implementations crashed Steam. Pilot per game with rollback, never
  flip globally because the PR reports large GTA pool savings.

Separately, our own graphics caches could trim unused entries on memory
pressure, with hysteresis and existing GPU/command-buffer lifetime references
preserved. Keep disk caches. Do not evict a live texture or PSO. Archive-file
bytes are a proxy, not resident Metal memory; measure actual physical footprint
before quoting memory saved. Bigger shader/PSO caches are not an automatic
solution with ~437 MiB sampled headroom in Detroit.

### 6. Wineserver cost — useful medium-risk project

[#294](https://github.com/willfaust/Madeira/pull/294): prioritize the unchanged
ClipCursor dedupe and request doorbell/coalesced wake design. The existing
server still scans client descriptors without these PR mechanisms. Benefits
are lower request overhead/heat, especially with several launchers/helpers.
Maintain partial-request handling, socket EOF processing, full-scan fallback
and wake correctness. Its direct-handler mode changes the server's threading
model; keep off until stronger stress/lifetime tests. FD-in-reply requires
matched client/server changes; never update one protocol half alone.

[#304](https://github.com/willfaust/Madeira/pull/304) avoids expired timers
being starved by endless paints. Useful for stuck installers, not a rendering
FPS improvement. Since it changes normal message priority, retain a per-game
opt-out rather than advertise it as universal compatibility.

## Worth keeping for targeted compatibility work

| Source | Recommendation |
| --- | --- |
| [#267](https://github.com/willfaust/Madeira/pull/267) | Audit stack-free x18 trampolines and derived-address repairs. Local code still saves a scratch register on SP; compare instruction shapes against our newer patcher, with aligned/misaligned stack tests. Broad ARM64EC stability candidate, medium risk. |
| [#268](https://github.com/willfaust/Madeira/pull/268) | Explicit iOS instruction-cache flush/build guards are good portability hardening. Current `wine/build-macos/include/config.h` has `HAVE___CLEAR_CACHE=1`, so our existing branch already flushes; no evidence this explains today's stutters. |
| [#302](https://github.com/willfaust/Madeira/pull/302), [#308](https://github.com/willfaust/Madeira/pull/308) | Address-space exhaustion fixes, particularly 63 GB iPad maps/mixed sessions. We already ported older stack relaxation, so compare newer spill paths. Smaller guest stack minimum primarily saves reserved VA, not all those MiB of physical RAM. Keep per-exe. |
| [#309](https://github.com/willfaust/Madeira/pull/309) | Additional C++ throw repair. Upstream RtlPcToFileHeader alias fix is already present; author says the added repair was not isolated after it. Port when a compatible signature is observed or targeted exception tests justify it. |
| [#321](https://github.com/willfaust/Madeira/pull/321) | Useful save-folder repair for the actual Wine user. Local bridge repairs template symlinks and LocalLow but not the complete actual-user folder set. Preserve valid symlinks/user data and validate names. |
| [#241](https://github.com/willfaust/Madeira/pull/241), [#306](https://github.com/willfaust/Madeira/pull/306) | Build missing Wine/MFC DLLs against our pins when needed. Enables more games/audio; costs bundle size, not a universal FPS upgrade. |
| [#226](https://github.com/willfaust/Madeira/pull/226) | LOVE/LuaJIT GC64 compatibility with our OpenGL backend. Useful to test Balatro/LOVE; older LuaJIT bytecode compatibility is a real limitation. |
| [#242](https://github.com/willfaust/Madeira/pull/242) | x64 DXMT D3D9 frontend for broader renderer coverage. Evaluate separately with D3D9 games and exact native/PE protocol pins. |
| [#280](https://github.com/willfaust/Madeira/pull/280), [#299](https://github.com/willfaust/Madeira/pull/299) | D3D12 shader-cache native IO, encode batching/dedup and early PSO warm-up are promising later. Match DXMT control operations and measure on D3D12. Not MoltenVK/Detroit fixes. Skip placeholder shader pipelines and unrelated game-specific overrides in a blanket merge. |

## Already present or not a good default

- [#244](https://github.com/willfaust/Madeira/pull/244): DXMT census throttling
  already set by our bridge at ~1059, unless overridden. No duplicate port.
- [#284](https://github.com/willfaust/Madeira/pull/284): our alias registration
  already has per-process receivers, locking and retirement (128 receivers).
  Do not replace it with the PR's different cross-process drain policy blindly.
- [#219](https://github.com/willfaust/Madeira/pull/219), session-exit fixes,
  native texture upload and several older fork fixes are already integrated.
- [#227](https://github.com/willfaust/Madeira/pull/227): thin reservations report
  1–2 GB while providing a smaller usable head; future commits into the tail
  deliberately fail. This is a game-specific VA workaround, not safe general
  RAM reduction. It risks hiding the true reservation failure until later.
- [#323](https://github.com/willfaust/Madeira/pull/323) changes swap eligibility
  by selecting blocks coverage. The user's earlier blocks experiments used
  more memory or performed worse. Metal-owned allocations are not covered by
  this guest swap tier. Keep per-game/off until measured.
- [#273](https://github.com/willfaust/Madeira/pull/273) dead-stack quarantine:
  diagnostics/reuse-race protection may be useful, but retaining reserved VA
  for eight frees is not physical-RAM saving; reconcile with registry ownership.
- [#277](https://github.com/willfaust/Madeira/pull/277): our handler already has
  stronger aliased LSE/atomic work. Submitted bytewise native emulation must
  not replace true atomicity. Audit missing instruction cases selectively.
- [#228](https://github.com/willfaust/Madeira/pull/228) 5 ms audio periods: more
  callbacks/wakeups, not a fix for the stalled-callback teardown wait.
- [#319](https://github.com/willfaust/Madeira/pull/319) Game Mode plist key:
  small metadata addition, but no measured cross-device performance gain in
  this review. Not a substitute for reducing background/runtime work.
- Do not clear shader caches, enlarge swap, shrink staging or force full x87
  precision as global performance defaults.

## Fork-specific result

- **bahacan16 / 125hz:** the interesting changes are on PR topic branches;
  inspected main heads add no commits beyond current upstream. Select topic
  commits, not a whole-fork main merge.
- **jakub:** valuable latest registry CAS/identity review. Older audio naming,
  session-end, stack relaxation and executable-heap fixes already ported.
- **dre4moff:** native texture-copy work is already in ours (opt-in).
  [r31 report](https://github.com/dre4moff/Madeira/blob/codex/cache-cleanup-performance-r31/docs/R31_PERFORMANCE_REPORT.md)
  explicitly says movement stutters remain unresolved; smaller staging in r29
  regressed and was reverted in r30, and a cache-disabled trial did not help.
  Its cleanup work is disk housekeeping, not evidence of lower gameplay RAM.
- **u-tek main:** no ahead-of-upstream commits at inspected head.
- **Prototype2Fix:** inspected differences are predominantly workflows and
  uploaded changes, not a reviewed generic engine/runtime performance patch.
  No binary-only replacement recommended.

No reviewed change is guaranteed to improve all games. For implementation,
start with profiler safety, audio lifetime, child cleanup and JIT-environment
tests; then reduce diagnostic scans and measure before deeper reuse/protocol
changes. Native rebuilds must use `PATH=/usr/bin:$PATH`; user installs in Xcode.
