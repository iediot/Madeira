# Local FEX and DXMT changes

The FEX and DXMT submodules point at upstream's pinned commits; this fork's
changes to them are kept here and applied on top (`git -C FEX apply --3way
../patches/fex-iediot.patch`, same for `dxmt`), then the DLLs are rebuilt with
`build/fex-arm64ec/build.sh`, `build/fex-wow64/build.sh`,
`build/dxmt-pe/build.sh d3d11` and `build/dxmt-ios/build.sh`.

- `fex-iediot.patch`: `[decode-fail]` byte dump when the decoder refuses an entry block (shows a real
  unsupported instruction apart from code that was never written), folded L1 lookup index (ml2111), `[lookup-stats]` (ml2110,
  `env.MADEIRA_FEX_LOOKUP_STATS = 1`), iOS host build guards, and the ARM64EC
  JIT alias table grown from 256 to 2048 entries (ml1201, from vcvkk's fork, without
  its log line, which crashed when called from native code without a TEB:
  steam.exe + webhelper + a game filled 256 and the game crashed at load).
  - Big L1 for the hottest threads (LookupCache.h GrantBigL1): a thread at the 128K-entry
    L1 ceiling still missing 20,000+ times a second gets a 512K-entry L1, at most 4 threads
    (env.MADEIRA_FEX_BIG_L1 = 0 turns it off).
- `dxmt-iediot.patch`: single-level texture halving for BC1/BC3, RGBA8 and R8/A8
  at any size (ml2100/2101/2113), texture census, Metal 4 atomic fix.

Regenerate both after editing a submodule; `dxmt_bcn_downscale.hpp` is a new
file, so add it with `git -C dxmt add -N` before `git -C dxmt diff`.

- `wine-opengl-winios.patch` (from c-gow's fork): opengl32 for iOS -- 32-bit
  guest-pointer conversion in the generated wow64 thunks, OpenGL ES context
  handling, a GL_EXTENSIONS cap for old 32-bit games, and a normal ARM64EC
  image base. Apply with `git -C wine apply ../patches/wine-opengl-winios.patch`,
  then rebuild `build/ntdll-unix/build.sh` (opengl32's unix side) and
  `make -C wine/build-arm64ec dlls/opengl32/arm64ec-windows/opengl32.dll`
  (Homebrew bison first in PATH), strip it, copy to app/Madeira/arm64ec-windows/.
  The same patch also carries Madeira's own Wine edits: the `[sock-tl]` connection
  log names each TLS connection's server (SNI) in `host=` (dlls/ntdll/unix/socket.c).
  And in a Madeira Dock session, once the game is running, Valve's client
  (dockhost.exe) threads drop to utility QoS (dlls/ntdll/unix/sync.c,
  env.MADEIRA_DOCK_LOW_QOS = 0 turns it off).
  windows.gaming.input does not wait for its monitor thread when the caller holds the
  loader lock (dlls/windows.gaming.input/main.c): Party Animals deadlocked at start-up.
  The wine server copies every console write into the session log as `[console <pid>]`
  lines (server/console.c, built by build/wineserver/build.sh; MADEIRA_CONSOLE_LOG=0 off).
