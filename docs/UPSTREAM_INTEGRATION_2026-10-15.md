# Selective upstream integration — October 15

Compared local HEAD `7e7ea128` against fetched upstream main
`279fd0f83170425db002fe25d6bd62f6355f497d`. Git reports local57/upstream212
unique commits. Upstream-only history has 211 non-merge commits and one final
60-parent bookkeeping merge; first-parent history has 74 entries. It includes
original PR branch commits alongside squashed integration copies, not 212
independent new fixes. Reviewed the complete subject inventory and aggregate
diff, then the selected diffs and dependencies; this is not a line-by-line
audit of every large runtime series.

No history merge, commit or push was made. Seven upstream commits' relevant
changes were selectively applied/adapted to the dirty working tree. No commit
messages/trailers were copied. Backup of previous tracked diff and untracked
files: `/private/tmp/madeira-upstream-integration.YQXJYb`. Existing changes are
preserved. No layout/style/UI redesign, device writes, save edits, dependency
pin changes or downloaded replacement DLL farm.

## Integrated

| Upstream | Why retained | Local adaptation |
| --- | --- | --- |
| `b70afa70` / #272 | Prevent null monitor-source dereference during display queries | Three guards in win32u; native build verified |
| `48c4b3fc` / #304 | Repainting installer windows must not starve timers forever | Retains 100ms fairness threshold and opt-out; actual timer host simulation passes |
| `56bcf0bd` / #309 | Repair C++ exception metadata referring to a JIT alias or zero owning-image base | Uses our existing reverse translator; actual repair-block ASan/UBSan tests added |
| `cb293856` / #316 + `f4f6dca3` | Correct shared-depot owner manifest placement without hiding base games with their own depots | Preserves our existing shared-install-folder/save protection; tests cover cross-folder owner cleanup and keeping real installs |
| `3fce589f` / #314 | Game-local controller mode/output takes precedence | Preserves our Xbox default and global/environment fallbacks; empty mode ignored |
| `7828babe` / #319 | Declare the app's Game Mode support | Plist declaration only, no promised FPS improvement |

Shared-depot handling changes only local application logic. No install or
uninstall was performed on the iPad. The removal test runs on disposable Mac
fixtures, not real games.

## Not imported wholesale

- #321 profile-folder creation: would repeat the save-path redirection already
  experienced here. Keep LocalLow-only behavior and original/recovered saves.
- #277 misaligned atomics: our fork already has true atomic work; do not
  replace it with a different implementation without equivalence/race tests.
- #278/#279/#287/#298 overlap our self-suspend, sampler safety, socket cleanup
  and bounded/leak-free child-environment implementations. Keep ours rather
  than adding duplicates or replacing ownership rules.
- #297 overlaps our diagnostic cadence. Launcher QoS/power policies need
  renderer-independent activity detection and foreground/IPC testing.
- #267–268 and #273–296 include coupled trampoline, exception, view-quarantine,
  JIT retirement and alias changes. Late upstream fixes depend on the earlier
  series. Review/integrate as coherent projects; isolated tip hunks are unsafe.
- #280/D3D12, #285 pool reuse and #294 wineserver protocol/doorbell/direct-mode
  series require their matching sources, protocol halves and rebuilt native/PE
  components. Not validated by copying the latest DLL farm. Still candidates,
  not rejected as useless and not reported merged here.
- Recommended-settings UI, installer reset/management UI, display/UI changes
  excluded. Broad swap coverage remains opt-in rather than silently forced.
- Upstream submodule pins and DLL farm not substituted for our published
  FEX/Wine/DXMT sources or MoltenVK changes. No FEX source edited.

## Verification

Passed: iPhoneOS Swift type-check; plist lint; actual timer-fairness test;
Dock contract incl shared-depot lender/base-game cases; actual C++ throw repair
ASan/UBSan; actual shared-owner removal ASan Swift test incl preserved shared
folder save; existing self-suspend and LocalLow/save-path tests; diff checks.

Native scripts completed using `PATH=/usr/bin:$PATH`:

- `build/ntdll-unix/build.sh`: staged5134704bytes, 42/42 units.
- `build/wineserver/build.sh`: staged1322504bytes.
- `build/win32u-unix/build.sh`: staged16592224bytes.

Three broader host suites are NOT green. Baseline extraction of unchanged HEAD
reproduces the same failures: `check-steam-games.py` has old UI assumptions and
missing LibraryStatus marker; `check-dock-installers.py` fails case-insensitive
path resolution on this host; `check-steam-library.py` rejects existing
SteamSession UserDefaults usage and the background-task identifier. These were
not hidden, waived as new successes or fixed by changing UI in this task.

User Xcode install/test: normal Dock launch, controller mode/rumble with and
without game overrides; an installer that previously stalled while repainting;
display-query/C++-throw affected games if available. Shared-depot behavior needs
a matching owned title/install scenario; do not uninstall real games just to
exercise a test. No app xcodebuild was run.

## Separate Detroit retest requested by user

User reports the missing-save incident also reset graphics to High. The last
admission comparison is therefore uncontrolled. At the user's request admission
default is restored, retaining explicit `MVK_MADEIRA_ASYNC_ADMISSION=0` as an off
switch. Tests check default-on and explicit-off under pressure; MoltenVK rebuilt
to5113440bytes. This does NOT disprove the measured 5.4s producer waits or
establish benefit at corrected settings. Compare same scene/settings before
claiming regression or improvement. No device config or game graphics changed
by this integration.
