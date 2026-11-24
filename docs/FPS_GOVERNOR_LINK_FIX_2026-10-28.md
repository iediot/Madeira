# Optional DXMT FPS governor link fix

The owner's Xcode link failed with undefined `_ios_fps_cap_dxmt_pace` after
the compatible upstream ports. Current DXMT source calls this native hook,
but this fork's `server_ios.c` does not implement the upstream governor.
The previously staged DXMT archive had a strong undefined reference. Earlier
native compilation checks missed this cross-library dependency; no successful
app link or device test had been established.

Changed `dxmt/src/winemetal/unix/winemetal_unix.c` to supply a weak, linkable
default returning zero. The existing call remains; a host's strong governor
definition overrides the default. Zero means no additional limiter call, so
the existing pacing/vsync behavior is retained. This does not implement or
enable upstream's governor, change settings, or shorten an existing wait.

Added `tests/host/check-optional-fps-governor.py`, which extracts the production
default/call and links and executes it both without a provider and with a
strong provider. The original source reproduced the undefined-symbol link
failure; the final source passes both cases and retains the strong provider's
returned pacing interval. An initial weak-import-only attempt still failed
the missing-provider link test and was replaced with the defined weak default.
A standalone iOS arm64 dylib also links the production default without a
governor provider. This is a native linker check, not an app build.

Rebuilt using:

```sh
PATH=/usr/bin:$PATH MADEIRA_ONLY=winemetal_unix bash build/dxmt-ios/build.sh
```

The script also rebuilds its native frontend objects: final result **65
succeeded, 0 failed**. It refreshed and staged `libdxmt_combined.a`.
`nm -m` on the staged archive now shows a **defined weak external**
`_ios_fps_cap_dxmt_pace`. Build and staged combined-archive SHA256 values match.
No PE source or ABI changed, so no DXMT PE DLL rebuild was required.

Backup refs were created before root/submodule edits and after submodule edits
with suffix `20261028-fps-link`. The uncommitted submodule source diff and
build/test logs are preserved outside the repo in
`/Users/iediot/Madeira-evidence/fps-link-20261028/`. Post-port refs point to the
unchanged commit; the saved patch preserves the uncommitted change.

No app xcodebuild, project/Info.plist edits, FEX changes, commits or pushes.
No new settings were introduced. No Swift changed; owner/Claude Swift checking
and the owner's Xcode app link/device validation remain external checks.
Next step: build again in Xcode, then resume the planned RDR2 v3 capture.
