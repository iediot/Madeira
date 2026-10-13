# Madeira handoff for Codex (written 2026-10-12)

You are continuing work on the user's Madeira fork (iOS/iPadOS app running Windows games via Wine + FEX + DXMT + MoltenVK) in /Users/iediot/Projects/Madeira. The notes below are the previous assistant's memory, followed by the current state and open tasks. Use `codex exec -m gpt-5.6-sol` style work yourself; the user builds the app in Xcode.

## Rules from the user
- Never add Co-Authored-By or any Claude/AI attribution to commits or PRs.
- Do not run xcodebuild for the app (the user builds in Xcode). Native libs ARE rebuilt by you: `PATH=/usr/bin:$PATH bash build/ntdll-unix/build.sh`, `bash build/moltenvk-ios/build.sh`, `PATH=/usr/bin:$PATH bash build/fex-arm64ec/build.sh` (always with Apple's ar first in PATH).
- Type-check Swift without building: see "swift-typecheck" below (scratchpad script may be gone; use the command there).
- Batch commits; give the user a commit one-liner, they run it. UI: SteamOS style, native glass, NO glows, must scale on iPad.
- Concise, human answers. The app overwrites madeira-library.json while open: close Madeira (xcrun devicectl device process terminate) before editing it on the device.
- Devices: iPad 00008142-000A38AE0147801C (M5, 12 GB), iPhone 00008130-001411860E20001C, bundle id com.iediot.madeora; copy files with `xcrun devicectl device copy from/to --domain-type appDataContainer --domain-identifier com.iediot.madeora`.

## Current state (end of 2026-10-12)
- 2026-10-13 public-source publication: user explicitly authorized commits/pushes for this task, still no co-author/AI attribution. Public forks created at iediot/FEX, iediot/dxmt, iediot/wine, iediot/rpmalloc; current sources published to `madeira` branches. Original remotes retained as `upstream`, own forks now `origin`. FEX snapshot f5a5349 plus nested-rpmalloc URL commit5469fd1, DXMT8e22dcd, Wine snapshotd23bb945; unchanged rpmalloc812c2b9 published. Wine's old no-commit cherry-pick state was backed up in /private/tmp/madeira-wine-state.F0A5jy and quit; before/after staged and complete worktree diffs matched, no queued patches newly applied. Parent .gitmodules points to public forks; full baseline patch snapshots refreshed (including wine-iediot.patch), source checkout/build docs updated. Anonymous recursive checkout validation follows publication; no app/native rebuild needed because this task only snapshots existing source and changes repository metadata/docs.
- 2026-10-13 follow-ups now implemented: offline shader tool compiled117/pushed117, zero failures; device inventory28726metallibs and no missing captured-source matches. Earlier *known-library* preparation: learns per-module actual disk-library identities (including macro variants), persists bounded hints, prefetches disk libraries at module creation on a utility serial queue into the unchanged256-entry LRU. It does NOT solve first-ever unseen source compilation; that broader pipeline-construction change remains future work. New MVKMadeiraShaderPrewarm.h / `[mvk-shader-prefetch]`, env MVK_MADEIRA_SHADER_PREWARM=0 disables. Native rebuilt; ASan/UBSan bounds/persistence tests passed. See docs/DETROIT_SHADER_PREFETCH_AND_FINE_PROFILING.md.
- Allocator profiling: rebuilt ntdll-unix (42 units / zero failures), adds `[perf-classify-game-detail]` 16B buckets from existing samples, enabled with the existing classifier unless MADEIRA_PERF_CLASSIFY_FINE=0. tools/detroit-hotspots.py gates labels on exact EXE SHA, maps actual .pdata/chained unwind boundaries, flags ambiguous buckets. Host reporting/mapper tests passed. No allocator native replacement or EXE patch before real fine samples.
- Next user test: Xcode install once for native changes; shader-heavy learning run, restart and same-scene second run for prefetch, then inspect fine allocator addresses, library hitches, cache hits/memory/thermal. No app build/commit/push, device JSON/globalpool/threadcount/barrier changes. The117-library device cache refresh already needs no app rebuild on its own.
- 2026-10-13 validation: new iPad run lasted 5790s and ended normally. New markers confirmed; median reporting-window FPS 57.3, 0.55% frames >50ms (prior run 52.3 / 1.49%, not controlled A/B), peak11941 MiB, closest sampled headroom346MiB. Runtime archive cap exercised33evictions, final1525MiB charged; conversion cache15321hits/16726misses; urgent dequeue0–3ms. Remaining major hitches correlate with synchronous library preparation up to605.8ms before async PSO queue. See docs/DETROIT_OCT13_VALIDATION.md. No code/config/build changes this continuation.
- Decompilation follow-up completed: 33 normalized selected entry points, resolves PE chained unwind fragments; loading producer0x65d640, queued builder0x65dbb0/0x65db10, render-time cache miss0x646050/0x65f3c0 and compute0x647b80; allocator metadata init0x651430/vtable0x1dfd8a0. Artifacts /private/tmp/detroit-followup-decompiled.c. Read-only device inventory found117captured MSL source/options pairs missing offline libraries. Recommended existing precompile-msl.sh cache refresh has NOT been run (device writes); no game EXE patch or thread-count change.
- The same run's device monitor reported thermal=fair22:55 and serious23:00 (~21min after launch), with no later recovery log. End CPU5–7cores/GPU28% plus low P-core clocks: CPU/heat pressure, not proof every hitch is thermal. The large isolated hitches still align with shader-library preparation.
- Latest crash investigation (~22:24): log ended at 12,270–12,279 MiB peak against 12,287 MiB limit; likely OOM, no matching accessible OS report yet. This run lacks the three new build markers and appears to use the prior MoltenVK. Found and fixed runtime archive retention (startup-only cap previously allowed 1533 MiB -> 1946 MiB disk-equivalent growth): now oldest finished Metal archives are released on the serial writer within the existing budget, disk files preserved during play, active writers excluded. Native rebuilt 22:35; ASan/UBSan actual-class eviction/concurrency tests passed (`python3 tools/mvk-archive-budget-check.py`). New periodic `[mvk-archive-memory]` marker. NOT yet iPad tested. See docs/DETROIT_CRASH_AND_REVERSE_ENGINEERING.md.
- Actual Detroit EXE targeted-decompiled using Ghidra; artifacts in /private/tmp (not committed). Hottest game page 0x64d000 closely matches old VMA allocation-request/check loops; graphics/compute wrappers issue count=1 pipeline creates. Source/RVA findings and safe performance follow-ups recorded in the crash/reverse-engineering doc. No game binary/config modified.
- Continuation updates: DockStartScreen now uses CachedArtworkImage; Swift type-check passed. MoltenVK now logs `[mvk-memory]` every 30 s. Latest Detroit run (19:49–20:57) peaked at 10977 MiB footprint, Metal currentAllocatedSize about 2353 MiB, median FPS reporting window 59.1; shader-heavy stretches still had 240–523 ms synchronous library preparation and full-screen waits. Wine intentionally passes VK_NULL_HANDLE for the real pipeline cache by default (`ios_pipeline_cache`), so the cache-wide lock change is not exercised by Detroit; do not enable the unbounded real cache as a performance workaround.
- Newly implemented, rebuilt native, NOT yet tested on iPad: fallback pipeline compilation releases the Metal device monitor before waiting for completion; one normally idle urgent-only worker handles blocking full-screen/compute dependencies; bounded pure SPIR-V conversion-result LRU (32 MiB charged storage / 512 entries). Code is in the existing MoltenVK patch, with private MVKMadeiraCompiler.h, MVKMadeiraJobQueue.h and MVKMadeiraConversionCache.h included. Host Metal/concurrency/cache checks passed; see docs/MOLTENVK_SHADER_PREPARATION.md and docs/MOLTENVK_MEMORY.md. No app build, commit, push or release was performed.
- Next device validation: user builds in Xcode, replays shader-heavy scenes, then inspect `[mvk-compile]`, `urgent-queued`, conversion-cache hits/evictions, memory and frame pacing. Floating hair/stray meshes were reported; cause is unproven. Detroit remains FULL_BARRIERS=1 (device config was not changed); mode 2 is a separate proposed compute-barrier correctness experiment. Pool=640 is still undecided.
- Last pushed commit: 57974f64 (Settings sheets fix, touch watcher). Release v0.1.3-iediot.4 published. UNCOMMITTED since then: MoltenVK faster full-screen waits (patches/moltenvk-iediot.patch, libMoltenVK built 10:38), HardwareInput pointer lock any-mouse change, ArtworkCache resolved-address memory (Library.swift, SteamGames.swift). Commit them in one batch for the user.
- Detroit: Become Human (Vulkan) per-game config on the iPad: FULL_BARRIERS=1 (fixes DoF blur, a MoltenVK barrier-fence race; root cause not narrowed yet), ASYNC_PIPELINES=1, ASYNC_COMPUTE=1, PSO_CACHE=1024, PERF_CLASSIFY=1. About 51-58 fps, memory peaks ~12.1/12.3 GB (near jetsam).
- Open tasks, in priority order:
  1. Memory breakdown log every 30 s for MoltenVK (textures/buffers/pipelines/caches) to find where Detroit's ~12 GB goes; maybe automatic texture downscaling for Vulkan games like DXMT does; maybe `pool = 640` in iPad madeira.cfg (Detroit uses ~330 of 896 MB JIT pool).
  2. DockStartScreen.swift (~line 404) loads its artwork with AsyncImage (no cache): switch to ArtworkCache so loading screens are fast.
  3. Narrow the exact missing barrier behind MVK_MADEIRA_FULL_BARRIERS so mode 0 + precise fix can be the default (frame dumps: tools/mvk-dump-fire.sh / mvk-dump-pull.sh / mvk-dump-to-png.py).
  4. Pointer lock: iPadOS refuses it in Stage Manager (user had it on); verify dragging works in locked mode.
  5. Check other 64-bit games still launch with the new FEX exit-path change (xtajit64.dll), e.g. Stickman, Borderlands TPS, Talos.
  6. Known unfixed: with Steam signed out, Dock launches crash in dockhost.exe (FEX, null TEB) - see dock-signedout-crash below.
  7. Updating the release needs the "release recipe" below.
- AI policy note: FEX/AGENTS.md says AI must not generate code for contributions to FEX; the user explicitly said local patches in this private fork (patches/fex-iediot.patch) are fine.

---

# Memory files (verbatim)


## MEMORY.md

- [Build setup quirks](build-setup-quirks.md) — fixes needed to build Madeira from fresh checkout (ar, LLVM flags, Wine trees, Metal)
- [No Claude attribution](no-claude-attribution.md) — never add Co-Authored-By Claude to commits/PRs
- [No app builds](no-app-builds.md) — don't xcodebuild app changes; user builds in Xcode; still rebuild native libs
- [Batch commits](batch-commits.md) — no commit one-liner after every small change; batch them
- [Device config and limits](device-config-and-limits.md) — jetsam 6.1 GB, Stickman needs swap tier, StikDebug lifetime, Steam socket drops
- [UX feedback over workarounds](ux-feedback-over-workarounds.md) — for blocking waits, show a status overlay; keep the fastest path, skip clever tricks
- [Support all iOS versions](support-all-ios-versions.md) — iPad deliberately stays on iPadOS 26; iPad-only failures are real bugs
- [Swift type-check](swift-typecheck.md) — swiftc -typecheck command to verify app Swift without building
- [UI direction](ui-direction.md) — SteamOS-style layout from native glass parts, no glows, must scale on iPad
- [Delegate to Codex](delegate-to-codex.md) — implement via `codex exec`; Claude diagnoses, instructs, reviews
- [Store curation](store-curation.md) — Store shelves show AAA and fan-favourite games: many reviews, filtered before shown
- [Deferred upstream PRs](deferred-upstream-prs.md) — #241/#226/#240/#228 etc. triaged, port when their symptom shows up
- [Frame dump](frame-dump-wip.md) — MoltenVK per-pass dump tool; Detroit blur fixed by MVK_MADEIRA_FULL_BARRIERS=2 (MoltenVK fence race)
- [Release recipe](release-recipe.md) — build unsigned IPA + GitHub API upload for v0.1.3-iediot.N
- [Dock signed-out crash](dock-signedout-crash.md) — stuck 'loading Steam' = Steam signed out; dockhost FEX TEB-null crash


## build-setup-quirks.md

First full build on this Mac succeeded 2026-10-01 (unsigned Debug xcodebuild). Things docs/BUILDING.md does not say:

- Windows-side DLLs (app/Madeira/arm64ec-windows, aarch64-windows) are committed; only the iOS static libs, dockhost.exe and the VC runtime need building. i386-windows (32-bit games) is NOT built yet (build/wine-i386/build.sh).
- Homebrew binutils `ar` shadows Apple's; GNU ar archives get rejected by Xcode 27's ld ("not 8-byte aligned"). Run Wine unix builds with `PATH=/usr/bin:$PATH`.
- wine/build-macos and wine/build-arm64ec must be configured, then `make include/all` (not `make include`). Needs Homebrew bison in PATH.
- LLVM iOS build needs -DCMAKE_MACOSX_BUNDLE=OFF and -DLLVM_ENABLE_TERMINFO=OFF; libdxmt_combined.a = libtool -static of libdxmt_unix.a + toolchains/llvm-ios-build/lib/libLLVM*.a.
- Metal toolchain: `xcodebuild -downloadComponent MetalToolchain`.
- Submodule edits (FEX Core.cpp/Arm64.cpp guards, dxmt include paths for top-level layout, air_tessellation.metal Metal-4 atomic arg) are local only; submodules point at willfaust forks.
- VC runtime: payload cab `a12` inside vc_redist.x64.exe via cabextract (tools/fetch-vcruntime.md recipe is wrong).

**Why:** cost ~an hour to rediscover. **How to apply:** check these first when a rebuild fails.

- FEX/DXMT submodule edits are NOT committed in the submodules (they track willfaust's repos). They live in `patches/fex-iediot.patch` and `patches/dxmt-iediot.patch`; on an upstream merge that moves the pins: stash in submodules, checkout new pins, `git -C X apply --3way ../patches/X-iediot.patch`, rebuild DLLs, then regenerate with `git -C X diff HEAD` (3way apply stages, so plain `git diff` is empty; `dxmt_bcn_downscale.hpp` needs `add -N`). The arm64ec FEX build must use the iOS host flags (build/fex-arm64ec/build.sh, build dir FEX/build-arm64ec-ios).
- DXMT PE build dirs (dxmt/build-pe-i386 etc.) configured before the `metal_std` option existed embed Metal 4.1 command libraries, which iPadOS 26 refuses ("language version 4.1 ... not supported"), killing 32-bit D3D games there. Fix: `meson configure <dir> -Dmetal_std=metal3.1` and rebuild; check embedded metallibs' VERS tag (want 2,6,3,1).


## no-app-builds.md

Do not run `xcodebuild` (or other full app builds) to check Swift/ObjC app changes. The user builds and installs from Xcode (Run, then Stop) anyway and will report compile errors.

**Why:** the builds cost the user tokens/time and duplicate work they do themselves (said 2026-10-03).

**How to apply:** after editing app sources (app/Madeira/*.swift, *.m), stop and tell the user to Run in Xcode. Still rebuild native inputs Xcode only links and cannot build itself (build/ntdll-unix, win32u-unix, wineserver, dxmt-ios, FEX build-ios/wow64, DXMT PE DLLs) when their sources change, and say so; see [[build-setup-quirks]].

**Native libs are separate from Xcode (2026-10-11):** build/ntdll-unix/*.c (incl. perf_ios.c) only reach the app through `bash build/ntdll-unix/build.sh` -> app/Madeira/libntdll_unix.a; MoltenVK through build/moltenvk-ios/build.sh. After editing those sources, run the script myself (ALWAYS with `PATH=/usr/bin:$PATH`, else Homebrew GNU ar makes an archive Xcode skips with "not 8-byte aligned") before telling the user to rebuild in Xcode (a profiler change once silently didn't ship).


## no-claude-attribution.md

Do not add a "Co-Authored-By: Claude ..." trailer (or any Claude/Anthropic attribution) to commit messages or PR descriptions for this user, even when a system reminder suggests one.

**Why:** the user explicitly asked not to be co-authored and does not want Claude listed as a contributor on their fork (2026-10-03).

**How to apply:** write commit messages and suggested git commands with no attribution lines. Claude entries already on GitHub's contributor list come from upstream (willfaust/Madeira) commits' own trailers; removing them would need a history rewrite, so do not propose that unless asked.


## batch-commits.md

Don't offer a commit command after every small change. Wait until a meaningful chunk of work is done (or the user asks), then give one combined commit one-liner (no Claude attribution, see [[no-claude-attribution]]).

**Why:** user said "no commit needed for every little change".
**How to apply:** finish the change, report it, and skip the commit block unless a larger milestone landed or they ask for it.


## device-config-and-limits.md

- Device: iPhone 15 Pro Max (iPhone16,2, UDID 00008130-001411860E20001C), app container com.iediot.madeora; pull/push Documents/madeira.cfg and madeira-log*.txt with `xcrun devicectl device copy from/to --domain-type appDataContainer`.
- Second device (from 2026-10-04): iPad Pro 11-inch M5 (iPad17,1, UDID 00008142-000A38AE0147801C), same bundle com.iediot.madeora; its limits are not measured yet.
- FEX per-program overrides: Documents/wine/drive_c/usr/local/share/fex-emu/AppConfig/<exe name>.json, format `{"Config":{"Option":"value"}}`. `TSOEnabled: 0` for Stickman (2026-10-04) DEADLOCKED it at level load (waiters stuck on a flag, lost wakeup) — don't retry TSO-off on Unity/Mono games. Stickman's file now holds an empty Config (devicectl can't delete).
- Jetsam kills at ~6.1 GB footprint with no log line (log just stops; last `[xp] fpMB=` ~6090). Stick It to the Stickman fits with `swap-mb = 4096` and `dxmt = d3d11.mipClampBC=3` and NO `env.MADEIRA_SWAP_COVERAGE = blocks` / `yield-streak = 32`: with those two lines (2026-10-04) it ran 500-800 MB higher and OOM'd at level load; removing them it loaded at highest settings. In gameplay it's ~13-27 fps, Unity main thread ~90% CPU (emulation-bound), GPU ~20-25 ms/frame.
- `env.DXMT_IOS_CACHE_DIR = 1` persists shader caches for 64-bit games (default is 32-bit only); set 2026-10-04 to test hitch reduction.
- StikDebug only survives ~1 min in the background (CPU limit / suspension). Since 2026-10-03 the JIT pool is taken right when StikDebug attaches (StikJITHelper.preparePoolNow, env.MADEIRA_JIT_EARLY_POOL) so Play never needs it.
- iOS drops Madeira's Steam websocket whenever the app backgrounds (the StikDebug hand-off at startup); SteamSession now fails pending jobs on disconnect and retries service calls once.

**Why:** these cost whole debugging rounds to rediscover.
**How to apply:** check the device cfg first when a game OOMs; check `[perf]` lines (env.MADEIRA_PERF = 1) for frame/thread data. See [[build-setup-quirks]].

- iPadOS pointer lock (Keep pointer in game, Dock mouse look) is refused in Stage Manager; the user's iPad had Stage Manager on (2026-10-12). Check that first when "cursor leaves the game".


## swift-typecheck.md

The app's Swift can be verified without xcodebuild (see [[no-app-builds]]), from app/:

```
F=../FEX; xcrun --sdk iphoneos swiftc -typecheck -target arm64-apple-ios17.0 -swift-version 5 -module-name Madeira \
  -import-objc-header Madeira/Madeira-Bridging-Header.h -Xcc -I$F/FEXCore/include -Xcc -I$F/FEXHeaderUtils \
  -Xcc -I$F/CodeEmitter -Xcc -I$F/External/fmt/include -Xcc -I$F/build-ios -Xcc -I$F/build-ios/include -Xcc -IMadeira \
  $(find Madeira -name "*.swift") 2>&1 | grep error:
```

Catches type errors and "unable to type-check in reasonable time"; run it after every Swift change instead of only parse-checking.


## ui-direction.md

Madeira's library UI should feel like SteamOS / Steam Big Picture (hero, sideways shelves, a game page with wide art), built only from the existing native pieces (Liquid Glass capsules, libraryRowGlass, LibraryArtwork, LibraryPlayStyle, Forms). No new custom-drawn assets or button styles, no glow/halo effects behind artwork, and it must scale properly on iPad (no tiny centred tab pill).

**Why:** the user said (2026-10-05) a mockup was good but "dont make new assets and new looking buttons", the card halo "looks like shitters", and asked for "a full overhaul ... not js some small changes": they like the elements but not their ordering and page layout.

On 2026-10-06 the user asked for "breathability" everywhere: minimalist, Apple-like, generous spacing, only necessary info, buttons in the style of Google's Antigravity IDE (soft rounded tiles/pills), but keep Liquid Glass. Cramped, dense screens read to them as "claustrophobic and laggy".

**How to apply:** when changing the UI, restructure layout and order rather than bolting on cards; check both phone and iPad widths. Related: [[ux-feedback-over-workarounds]].


## ux-feedback-over-workarounds.md

When something in Madeira must block (e.g. StikDebug freezing the app while preparing the JIT pool), the user prefers a simple, visible status overlay ("Enabling JIT…" card with spinner + cancel) over technical tricks that trade total time for responsiveness (chunked prep, pausing on touch). They had me revert both tricks and were very happy with the overlay (2026-10-04).

**Why:** an unexplained stall reads as bad design to an unknowing user; a fast single request plus clear feedback beats a slower "responsive" one.

**How to apply:** for slow/blocking steps, first propose telling the user what's happening (overlay/status), keep the fastest underlying path, and only add complexity if they ask. Related: [[batch-commits]].


## support-all-ios-versions.md

Don't suggest updating a test device to dodge a bug. The user keeps the M5 iPad on iPadOS 26.6.1 (63 GB app address map, no pipe2/dup3) next to the iPhone on iOS 27 (512 GB map) deliberately, to catch version- and device-specific breakage.

**Why:** "we arent really going for works on my device, rather works on all devices" (2026-10-05).

**How to apply:** treat iPad-only failures as real bugs; check new code paths against the 63 GB layout and iOS-26 API availability (deployment target is iOS 17). Related: [[device-config-and-limits]].


## store-curation.md

The Store tab (app/Madeira/Store) must look like what Steam's own front page would recommend: AAA games and fan-favourite indies. Filter every shelf, including New releases, and use Steam's wishlist-ranked Popular Upcoming for Coming soon. Prefer a high review COUNT over a high positive percentage (about 2,000+ reviews at 60–70%+; 300+ for new releases).

**Why:** the user rejected Steam's raw public lists ("splash park tycoon with an ai slop thumbnail") and corrected a first attempt that filtered too few shelves with too few reviews and too much positivity: "for coming soon and new releases too, and put up more reviews and less positivity, more like triple a's or fan fave indies".

**How to apply:** any new store shelf or source passes the same review filter before it is shown; nothing unfiltered should flash up while ratings load.


## delegate-to-codex.md

From 2026-10-05 the user wants implementation delegated to Codex: Claude writes precise instructions, runs `codex exec` in the repo, then reviews the diff and verifies (type-check, tests) instead of writing the code itself.

**Why:** "we are kinda down with our usage ... use codex on your own? you give it instructions and let it build, to preserve your usage credits while consuming his".

**How to apply:** for any non-trivial code change, run `codex exec -m gpt-5.6-sol -C /Users/iediot/Projects/Madeira --sandbox workspace-write "<task>"` (codex-cli at /opt/homebrew/bin/codex). The prompt should point at the files, the bug or feature, and the constraints: [[no-app-builds]], [[swift-typecheck]], no Claude/Codex co-author lines, match the surrounding comment style. Keep Claude's own work to diagnosis (logs), short instructions and a short review. Tiny one-line edits can still be done directly.

**Fallback (2026-10-10):** when `codex exec` fails (that day: "gpt-6-astra model is not supported when using Codex with a ChatGPT account"), the user chose background Claude subagents with the same written briefs rather than Claude implementing inline.

**Model (2026-10-11):** the user said to use **sol** (`-m gpt-5.6-sol`), not astra (`gpt-6-astra`, still the default in ~/.codex/config.toml): "sol is now pretty good". Pass the flag; don't edit their Codex config.

**Background runs:** add `< /dev/null`. Without it, `codex exec` run in the background printed "Reading additional input from stdin..." and hung indefinitely (2026-10-11).


## deferred-upstream-prs.md

Triaged 2026-10-11 (refs fetched as refs/pr/<N>). Ported then: #248 (minus 8fc6a0b1 + privacy commits), #229 d3d12 commits, #219 2d160aae, #225 Mesa 0006/0007.

Deferred by the user ("we have not yet hit those walls") — port when the symptom shows up:
- #241 (125hz): missing ARM64EC DLLs — tbs.dll (UE shipping exes fail c0000135), d3dx9/10, d3dcompiler_33..46, xaudio2/x3daudio/xapofx, wmadmod+winegstreamer (silent x64 xWMA). ~45 MB; rebuild with our wine pin; wintypes.dll + build-modules.sh conflict.
- #226 (c-gow) commit bbda82f6: GC64 LuaJIT for LOVE games (crash on first Lua call, likely Balatro).
- #240 (125hz): exe-window handoff — claim never commits for non-16KB-multiple SizeOfImage; relaunch wait. MADEIRA_EXE_WINDOW_HANDOFF=0 reverts.
- #228 (c-gow): 5 ms audio I/O (choppy SDL/WASAPI audio); costs wakeups — measure on CPU-bound games.
- #243 dock born-minimized restore, #237 Steam Cloud off switch, #246 thermal frame cap (all small).
- Avoid #210 (forces 80-bit x87 on 32-bit games, 20–50% CPU).

**How to apply:** when a game shows one of these symptoms, port the matching PR instead of debugging from scratch.


## frame-dump-wip.md

Started 2026-10-11 by a background agent: MoltenVK debug feature that dumps every render pass's attachments and every compute dispatch's storage images for one frame.
- Trigger file Documents/mvk-dump-now; output Documents/mvk-dump-<n>/ with raw files + index.json.
- Env vars MVK_MADEIRA_FRAME_DUMP (default on), _MAX_DIM (4096), _MB (1536).
- Mac tools: tools/mvk-dump-to-png.py (PNG + index.html contact sheet), tools/mvk-dump-pull.sh, tools/mvk-dump-fire.sh.
- Code in research/MoltenVK (MVKQueue.mm next to mvkMadeiraCaptureTick, encoder/descriptor code). Patch refreshed with `git -C research/MoltenVK diff > patches/moltenvk-iediot.patch` before build/moltenvk-ios/build.sh.

2026-10-12: mode 1 (MVK_MADEIRA_FULL_BARRIERS=1) also fixes the blur and is now Detroit's setting; async pipelines (MVK_MADEIRA_ASYNC_PIPELINES=1) on for Detroit; ~58 fps median, 0.5% frames >50 ms. Open: narrow the exact missing barrier so mode 0 + precise fix can be default; try MVK_MADEIRA_ASYNC_COMPUTE=1.
FIXED 2026-10-11 ~22:00: Detroit config env.MVK_MADEIRA_FULL_BARRIERS = 2 (Codex/sol switch: every encoder waits on all stages' latest fences + in-encoder memory barriers on pipeline barriers). Blur gone, fps unchanged (~23-33). Codex's three targeted fence fixes alone did NOT fix it; single queue + sync submits did not either. Open: try mode 1 (cheaper) and narrow the exact missing barrier so it can be default; Codex notes MTLFences are device-global and do not order across MTLCommandQueues.
ROOT CAUSE FOUND 2026-10-11 night: GPU sync race in MoltenVK's residency-set path (argument-buffer resources untracked by Metal; MoltenVK orders encoders with barrier fences, MVKCommandBuffer.mm barrierWait/Update ~1720). Screenshots blurry while same-second dumps sharp (dump's blits serialize). MVK_MADEIRA_NO_RESIDENCY_SET=1 (our switch, MVKDevice.mm) + MVK_CONFIG_LIVE_CHECK_ALL_RESOURCES=1 (needed, else crash on destroyed textures in useResource) FIXES the blur but fps collapses. Codex (sol) tasked to fix the fence logic (brief: scratchpad codex-barrier.md). Detroit config currently has both test lines; remove once fence fix lands.
Status 2026-10-11 evening: dump extended (inputs, transfers, shaders/*.metal); MVK_MADEIRA_FRAME_DUMP_INPUTS=1 (live tracking) needed to read argument-buffer inputs, otherwise they are "unverified" ptrs. Heap theory was WRONG (MoltenVK never heap-places depth; MVK_CONFIG_USE_MTLHEAP=0 crashes Detroit on block-texel-view images).
Real chain: DoF reads OutAADepth (R16Float, binding 17) written by the TAA compute (inputs: PrevAADepthTex b0, CurrZBuffer b3, MotionVectorTex b4...). OutAADepth = mix(clamp(history, min/max of 5 CurrZBuffer gather taps), center, 0.15), so it can only be wrong if CurrZBuffer is wrong. Dumps 12 (blurry) vs 13 (sharp), same Kara shot: AA depth wrong on 54% of pixels vs 0.7%. CurrZBuffer = texture 0x5aa164f00, stable across frames, also sampled by G-buffer/shadow passes, never a copy destination -> probably a depth-aspect view of scene depth (Depth32Float_Stencil8). Suspect: view/compression or ordering. Next: dump with FRAME_DUMP_INPUTS=1 (Madeira must be closed to edit library json) and compare CurrZBuffer contents + parentTexture with the depth attachment.
Earlier: Status 2026-10-11 (later): WORKS, committed 3fbaed2a. Dump 2 (Kara face blurred) showed the cause: depth 0x5ab72f200 is intact through the G-buffer passes but holds rectangular blocks of foreign data by the DoF CoC compute (pass 145); the far-CoC map follows those blocks exactly. Suspected MTLHeap placement aliasing. Next test: env.MVK_CONFIG_USE_MTLHEAP = 0 in Detroit's library config (footprint already ~9.8 GB). Earlier: built (libMoltenVK has [mvk-dump]); code in Commands/MVKCommandBuffer.mm "Madeira frame dump" section; index.json(l) per dump; also MVK_MADEIRA_FRAME_DUMP_INFLIGHT_MB (768). Not covered: CopyBufferToImage, storage buffers, fragment-shader storage writes, drawable. Next: user rebuilds app, fires during blurred gameplay, pull with tools/mvk-dump-pull.sh and bisect.

Why: Detroit's blur. The DoF CoC map is correct (Todd's head in focus, and the whole frame renders upside down internally, which is normal), so the blur enters at a later pass. Bisect order: 14131 tile dilate → 14143/14155 near/far split → 14167/14179 → 14191 combine → 14203 → 14226 tonemap.


## dock-signedout-crash.md

2026-10-12: with Steam signed out in Madeira (`[steam-library] start signed-in=0`), launching Detroit hung on "loading Steam": dockhost.exe crashed in FEX JIT code reading TEB->ChpeV2CpuAreaInfo (addr=0x1788) on a thread with no TEB (`[mach-deliver] no TEB owns sp`). Signing back in fixed it. Real underlying bug (a non-Wine thread running EC code) left unfixed; if "stuck loading Steam" is reported, check sign-in state first.


## release-recipe.md

Releases are tagged v0.1.3-iediot.N (N=3 on 2026-10-12, commit 993be049). Recipe (user asked for a release, so xcodebuild is fine here despite [[no-app-builds]]):
1. Native libs current (build/ntdll-unix, moltenvk-ios, fex-arm64ec with `PATH=/usr/bin:$PATH`), `bash build/stage-licenses.sh`.
2. `xcodebuild -project app/Madeira.xcodeproj -scheme Madeira -configuration Debug -destination 'generic/platform=iOS' -derivedDataPath <scratch>/dd CODE_SIGNING_ALLOWED=NO build`.
3. Payload/Madeira.app zipped to Madeira-vX.ipa (~197 MB).
4. Token: `printf 'protocol=https\nhost=github.com\n\n' | git credential fill`; POST /repos/iediot/Madeira/releases (tag, target_commitish full sha, body), then upload to uploads.github.com/.../releases/<id>/assets?name=... Parse JSON with strict=False (body newlines broke json.load once).
