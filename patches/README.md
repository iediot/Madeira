# Madeira dependency patch snapshots

FEX, DXMT and Wine now point at public `iediot` fork commits that already include
all this fork's local changes. A recursive clone gets the complete source; do
not apply these snapshots again to those pins. They remain here for review and
porting to their previous baseline commits:

| Snapshot | Previous baseline |
| --- | --- |
| `fex-iediot.patch` | `3bec2ac498bf78156ab47c0c194b0e8cb2849756` |
| `dxmt-iediot.patch` | `db546ee466ad50ec6463edb5fa183de11db43761` |
| `wine-iediot.patch` | `257f271cfffed9f22f7987cac53bc00095d092fe` |

The full Wine snapshot includes the older OpenGL-specific snapshot plus the
other current Wine edits; the older file is retained as a historical porting
reference, not an additional patch to apply. MoltenVK's fetched source still
receives `moltenvk-iediot.patch` through its build script.

After source changes, the DLLs are rebuilt with
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
- `dxmt-iediot.patch` also carries dre4moff/Madeira's r29/r30 texture-upload work (direct copies into the
  local staging block for `UpdateSubresource` and resource creation, through `copyTextureUpload`), opt-in with
  `env.DXMT_DIRECT_TEXTURE_UPLOAD = 1` as in their release.
- `dxmt-iediot.patch`: single-level texture halving for BC1/BC3, RGBA8 and R8/A8
  at any size (ml2100/2101/2113), texture census, Metal 4 atomic fix.

To refresh a snapshot after committing dependency changes, diff its recorded
baseline against the new HEAD (`git -C FEX diff <baseline> HEAD`, similarly for
DXMT/Wine). Before committing, use `git diff <baseline>` and include any new
source files in that diff with `git add -N`. Publish the dependency commit before
updating Madeira's gitlink so recursive clones can fetch it.

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
