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
