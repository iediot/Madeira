# Upstream wave 2026-11-24

Root was 242 commits behind upstream/main; upstream pins FEX 1dc6ed49, dxmt 3d62913b,
madeira-dock 67780ba9, wine ddf92bab. Ported by Codex (per-file 3-way), finished and
reviewed in Claude. Backup refs: refs/backup/pre-upstream-20261124 (root),
refs/backup/pre-subport-20261124 (each submodule).

## Ported
- madeira-dock: five upstream commits, clean.
- wine: 44 of 58 upstream commits fully in (dircache 11f6aa3a, TLS JIT sync 694442e4,
  CS/TLS x64 fast paths, SYNC_LEAN, GDI publication, APC busy queue, async cancel, bcrypt
  CBC, xinput, class atoms, SCM on demand, ...); the other 14 kept as fork versions on purpose
  (d2d1 readback) or superseded.
- Root deps for the wine ports: unixcall_ios_sync_pe_write (loader_ios.c wrap 13,
  virtual_ios.c) and madeira_dircache_changed hooks in build/wineserver/fd_ios.c.
- dxmt: upstream merged; fork op 10 PSO warming kept (single case 10) beside upstream op 11;
  FPS-governor weak-symbol fix kept; DXMT_DYN_KEEP (off by default); 0a690ad7 rename-failure
  retry ported onto the fork's refreshMirror.
- FEX: clean upstream cherry-picks only.
- Root: #550 Dock registry values (with SteamInstallValue/SteamInstallRegistry,
  rewrite(hives:), keys(hive,key)), #556 vector regs + JIT pool leaks, #579 wineserver
  start-up, #604 protobuf, #576 depot overlap/write errors, Steam CM failover, entitlement CF
  lifetime, input wake/pointer policy, Cloud/.NET installer rules, FFmpeg 7.1.5 (#607).

## Deferred
- dxmt: upstream D3D9 direct-map buffer rewrite (880241a2, 4842a041); conflicts with the
  fork's aliasing + write-watch design. Human decision; compare on device.
- FEX: one nominally clean commit that depended on a conflicting invalidation-tracker change;
  e37c0a20 SMC write traps per 16 KB page. Human resolution.
- Need new files / pbxproj entries: DockGameSession (held window for game #549, service
  decision), GOG store, EA app, itch/Epic/EA library sections, Japanese locale #567, zh-Hans
  #571, Steam Workshop #491, SteamLANPeer.
- Worth porting next (existing files): touch controls #553/#558, iPhone mouse #552,
  #517 device-log follow-ups, 58c89904, 916c2cb8, 255f11a4.
- Owner design call: MADEIRA_FEX_AVX=2 reports AVX in FEX but server_ios.c's HARDWARE
  registry mirrors only "1".

## Builds (PATH=/usr/bin:$PATH)
libntdll_unix.a, libwineserver.a, libdxmt_combined.a, DXMT PE DLLs, Wine PE DLLs (aarch64 +
arm64ec), FEX iOS/ARM64EC/WOW64, Dock, FFmpeg. Metal Toolchain installed (27B47).
Settings catalog regenerated. Swift type-check passes.

## Host tests
Baseline 83 failing of 183. After: 71 failing of 196; none new (5 new upstream
tests adapted to the fork's layout; check-steam-malformed-protobuf skips its two
SteamLANPeer assertions). Added check-dircache.py, check-tls-jit-sync.py.

## Device validation
Xcode build + device run. Look for [tls-jit-sync] in ARM64EC processes, directory listings
after server-side deletes/renames, [d9-rename-wait] under VRAM pressure (GTA IV), desktop
cursor visibility before cursor reports go live.

## Follow-up 2026-11-25: Social Club helper builds (missed in the wave)

- #559 (15ebc3d8, helper 2.4.0.296): ported. The wave's `merge -s ours`
  recorded it as an ancestor without importing it; RDR2's launcher window
  stayed black on the iPhone 18 Pro because [sc-rph] logged "not patched:
  another build".
- Open PR #580 (edd299df, helper 2.4.0.302, TimeDateStamp 0x6aeb6937, the
  build the launcher now installs): ported on top of #559.
  `MADEIRA_SC_HELPER_EXE=<device copy> tests/host/check-sc-helper-binary.py`
  passes against the device's SocialClubHelper.exe.
- Test conflicts (check-sc-ipc-split.py, check-sc-render-loop.py,
  sc-render-loop.S) resolved by taking upstream's versions; the assembler-based
  sc checks need GNU `as --64` and cannot run on this Mac (unavailable, not
  passing).
- tbs.dll (arm64ec + aarch64) added to the PE farm: the Rockstar Games
  Launcher now imports it and stopped with c0000135 without it.
- Rebuilt: libntdll_unix.a. Device validation pending.

## Follow-up 2026-11-26: RDR2 on the iPhone 18 Pro (8 GB jetsam limit)

- #556 vector contexts: RDR2 died after its calibration screen (null read at
  +0x28 in game code); env.MADEIRA_CTX_FP = 0 avoided it. ARM64EC
  GetThreadContext converts the native context (signal_arm64ec.c), so live
  NEON captured while the target ran FEX/host code, or a Unix-call frame
  (q8-q15 only), reached the game as XMM. mach_ios.c now reports FP only from
  real syscall frames or when the PC is in translated x64 code
  (check-ctx-fp.py extended). Device validation pending.
- JIT pool: with no region C at launch, ios_pool_recycle_code_range refused
  every main-pool code buffer, and image loads failed (D3D11.DLL,
  d3dcompiler_47.dll on a renderer restart -> "graphics driver error") with
  37-47 free carves listed. Main-pool carves are now eligible when region C is
  absent, same guards. check-pool-low.py fails before and after (its helper
  finds ios_swap_is_fexjit's prototype); other pool checks pass.
- RDR2 profile: vram-mb 2048 + vram-trim-mb 2560 when os_proc_available_memory
  is under 10 GB (high settings filled 7.4 of 8.2 GB, 1 fps); 4096 otherwise.
- Not changed: the swap tier. Low-settings run: page waits ~2% of thread time;
  the 50 -> 20 fps drop follows P-core clocks 4.45 -> 1.45 GHz (thermal).
