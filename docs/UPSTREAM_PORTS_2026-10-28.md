# Compatible upstream ports — October 28, 2026

Reviewed upstream main `e96065a0f146d29a601876cadca1dd8b6d7800cc` against
local `a2cd64e4418e3bfa61bb40d0d2f160da2df0fb54`. The user chose to preserve
this fork's architecture, UI, save paths and FEX work while porting compatible
fixes. This is a selective integration, not a complete upstream merge.
No merge commit or history-only merge was made; remaining upstream work is
still visible to Git and must not be represented as integrated.

## Applied

| Source | Change |
| --- | --- |
| `0b4eee5a` (#472) | Steam Cloud discards partial hashes on read failure and leaves differing files without a complete local checksum for explicit conflict resolution. |
| `28ce806a` (#465) | Decode the initial working-directory override as UTF-8; handle allocation/decoding failures through the existing fallback. |
| `16b3382b` (#471) | Reserve the actual working-directory capacity in initial and child startup parameter blocks. |
| Upstream `build/wineserver/mach_ios.c` delta | Read real-suspension policy for each session instead of caching the first session's environment forever. No UI/preset changes. |
| Upstream D3D12 lifetime-lock hunks | Flush deferred render/compute resource declarations before releasing the device view-list lock, so Metal receives them while their resources are still protected. |
| `5048059d` | Cross-page store handling validates live alias bounds and guest protections before any part is written; covers low aliases, FP/LR operands/writeback and address overflow. This changes Wine-side code, not FEX source. |
| Final upstream backup tool/tests | Add `tools/backup-saves.sh` including archive validation follow-ups. Only temporary test fixtures were used; no real prefix was accessed. |
| Final upstream dependency tool/tests | Add `tools/check-game-deps.py`, including standard/delayed-import and malformed-input tests. |

Kept existing fork fixes rather than replacing them: bounded/tombstone-safe
D3D12 view-map probing, AIR table caching, typed-view indexing, v3 diagnostics,
safe swap rotation, process-owned TLS clearing, per-thread fault accounting,
poll-cache invalidation, root-certificate cursor enumeration and launcher work.

## Validation and staging

- UTF-8 CWD production-decoder checks, startup layout checks (24 cases under
  ASan/UBSan), and Steam Cloud production hashing/planning checks pass.
- Encode-batch regression: the two missing lifetime flush assertions failed
  before the change and pass after it. The production-wrapper harness also
  checks a queued resource declaration is sent before the simulated lifetime
  unlock, with no declaration left queued afterward; existing replay/order,
  memory-copy, flush-point and thread-local checks pass.
- Fixed one stale test expectation: encode-batch's generated catalog entry
  uses integer configuration, so the check now accepts integer or boolean.
  The product catalog was not changed.
- New suspension test executes the production function under ASan/UBSan
  across unset/on/off/on/unset/empty environment transitions in one process.
- Cross-page alias test passes, including protected second-page rejection,
  FP/LR and writeback handling. Backup and dependency-tool checks pass.
- Existing wait probe, hitch logging, AIR cache and typed-view checks pass.
- Linux-only `check-swap-coverage.py` was updated by the upstream patch but
  was not run on this Mac. No Linux test success is claimed.
- Native builds used `PATH=/usr/bin:$PATH`: ntdll **42 succeeded, 0 failed**;
  wineserver succeeded and staged its archive; D3D12 PE succeeded. After the
  additional signal-source port, ntdll was rebuilt again, **42/0**, and staged.
  Both D3D12 DLLs were explicitly copied into `app/Madeira/arm64ec-windows`.
- Final staged ntdll still contains `[perf-hitch] diagnostics v3`.
- `git diff --check` passes. SHA256 verifies both protected user files are
  byte-for-byte unchanged from the start of this integration.

Staged SHA256 values:

| Artifact | SHA256 |
| --- | --- |
| Both D3D12 DLLs | `63f2fb7a86c5b57861abf132fdd0c7c573f93c997ab36d6d68c8f30005bfb68e` |
| `libntdll_unix.a` | `f3c6e9414107c87e3c91dc39bb8f08074c27c14ac9ac835844851b22177429db` |
| `libwineserver.a` | `5cc1022603266357e4bfbccf8575d81341ba2ec06c5856a1e05c4d3ae36087d8` |

Build/test logs and protected-file backups are durable outside the repository:
`/Users/iediot/Madeira-evidence/upstream-20261028/`.

## Remaining upstream scope and device checkpoint

The isolated trial full merge produced **86 conflicted paths**, out of 472
upstream changed files. It was aborted; the main checkout never entered that
conflicted merge. The trial exposed incompatible replacements and duplicated
already-ported changes, so resolving all conflicts by preferring either side
would discard fork behavior or revive intentionally deleted components.

Not integrated in this batch:

- Windows profile/save-path migration and automatic migration at app startup.
- UI/JIT/signing replacements, external-drive integration and Epic replacement
  flows; these cross this fork's different app structure and/or the protected
  project/Info.plist files.
- New graphics backend packaging and dependency-pin replacements (FEX, Wine,
  DXMT, Dock). These need paired source/ABI and packaging review rather than
  accepting upstream binaries or pins wholesale.
- Remaining runtime changes such as directory-cache APIs, PE-loader TLS-copy
  synchronization, registry flushing, thread-slot reuse, pool quarantine and
  altered child-failure policy. Some patches apply textually, which does not
  establish compatibility with the retained pins, ownership rules or failure
  handling. They remain candidates, not completed ports.

The next checkpoint is an Xcode build/install using these staged inputs:
verify app startup, Dock/Steam login and RDR2 launch, then the unchanged-save,
unchanged-settings 5–10 minute RDR2 route described in
`RDR2_DEVICE_RUN_2026-10-28.md`. Enable `env.MADEIRA_PERF = 1` explicitly if
absent and export the complete startup/game log. This validates the current
runtime batch before adding another broad set of interacting runtime changes.
No app build, installed-device validation or performance improvement is claimed.

No commits, pushes, FEX edits, save migrations, real-save writes, device config
edits or unrelated UI changes were made. Root HEAD and submodule pins remain
unchanged; ports and rebuilt tracked DLLs are uncommitted in the main checkout.
