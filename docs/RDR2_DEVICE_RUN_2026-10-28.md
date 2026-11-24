# RDR2 device evidence check — October 28, 2026

Read `HEADROOM_HANDOFF.md` completely; it supersedes older current-state instructions.

## Verified local state

- Root HEAD: `a2cd64e4418e3bfa61bb40d0d2f160da2df0fb54`.
- Wine: `9af0395beb76820b63c9899c5f0781bf99022d56`; DXMT:
  `58bd95759dbda586f3faeb7211d48764ad6aa5cf`; FEX:
  `a7d7c4c0fa64e5d0e4fc37439b55e501596bc873`. These three working trees are clean.
- Initial root status: user changes in `app/Madeira.xcodeproj/project.pbxproj`
  and `app/Madeira/Info.plist`, plus untracked `HEADROOM_HANDOFF.md`.
  Those files were left untouched.
- No root or ancestor `AGENTS.md` found. Repository instructions found only in
  `FEX/AGENTS.md` and `madeira-dock/AGENTS.md`; neither subtree was edited.
- Recursive Downloads search found no `.txt`, `.log`, `.ips`, `.zip`, `.gz`,
  or filename matching RDR/Madeira. The full directory listing contained only
  unrelated documents, images, a presentation and an application disk image.
  There is no new device evidence to preserve or analyze.
- Both staged DLLs at `app/Madeira/arm64ec-windows/{d3d12,madeira_d3d12}.dll`
  have SHA256
  `5965e241da0a42b6cd8ebab57c804ada0af76625657a10d88472b8e9d90801ff`.
- `strings app/Madeira/libntdll_unix.a` contains
  `[perf-hitch] diagnostics v3`. Its timestamp is October 25, 23:39:21;
  `libwineserver.a` is October 25, 23:23:37 (local filesystem times).
  This verifies staged inputs, not the linked app or installed device build.
- Current DXMT native dispatch includes ops 8/9, 20–22 and 24. The native
  warmer still requires explicit `pso-warm-native=1` and positive `pso-warm`.
  No missing-op patch or warmer activation is justified by this check.
- `perf_ios.c` gates v3 startup on `MADEIRA_PERF=1`. Current RDR2 built-in
  defaults in `Library.swift` do not set that variable; historical device
  configuration cannot establish its current value. They set
  `MADEIRA_PERF_CLASSIFY=0`; classifier activation is not required for v3.

## Required next device run

1. In Xcode open `app/Madeira.xcodeproj`, select the `Madeira` scheme and the
   test iPad, then build/install/run using these staged native inputs.
2. Confirm the existing RDR2 game configuration enables
   `env.MADEIRA_PERF = 1`. If absent, explicitly enable that diagnostic setting
   in the app's game configuration UI before launch. Do not infer it from the
   built-in defaults or change rendering settings. No device configuration
   file was edited in this check.
3. Launch Steam RDR2 (appid 1174180 / PlayRDR2) via Madeira Dock, following the
   tested launch route. Keep graphics settings, save and shader caches intact.
   Play the opening and house/town area for 5–10 minutes, including the
   stutter-prone route. Avoid backgrounding/rotation; note any unavoidable
   interruption and approximate times of major freezes or texture delays.
4. Export/AirDrop the complete new RDR2 text log to Downloads, including
   startup. It must contain `[perf-hitch] diagnostics v3` and game-owned
   `[d3d12-frame-window]` records. If the startup marker is missing, the run
   cannot establish that v3 was active; check profiling and installed build.
5. Before analysis, copy the new raw log to a durable directory outside this
   repository and record its SHA256, size and source filename. Do not rely on
   `/private/tmp`. Verify logged renderer/config/build markers before drawing
   conclusions; the v3 marker alone does not identify every integrated change.

Use game-owned Present-entry windows as submission timings, not physical
display FPS. Native `[perf] scope=all-processes` counters are mixed-process.
Separate loading/first-use compilation and interruptions from steady play.
For periodic gaps follow wait object → setter/waker → producer state/fault →
wake-to-return/server response latency, correlated with VM/swap markers.

No runtime changes, host test reruns, native rebuilds, app builds, device
access, configuration changes, commits or pushes were performed. No device
performance improvement is claimed. A fresh v3 device capture is the next
dependency; another speculative runtime patch would not resolve it.
