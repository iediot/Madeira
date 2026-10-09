# Persistent MoltenVK MSL libraries

Run the game once with the updated MoltenVK to collect successfully compiled MSL,
then, with the iPhone connected and the app installed:

```sh
tools/precompile-msl.sh <device-id> [game-dir-hash]
```

Omitting the hash discovers all game directories. Restart the game after the
transfer to measure disk hits independently of the process-wide LRU. New shader
variants still compile and are collected; run the tool again to add them.

## Files

Inside the `com.iediot.madeora` app data container:

```text
Library/Caches/mvk-binary-archive/<game-dir-hash>/msl/
  src/<key16hex>.metal
  src/<key16hex>.json
  lib/<key16hex>.metallib
```

The game directory is exactly the existing binary-archive directory: FNV-1a of
`v1-<Metal registryID>-<Metal device name>-<MADEIRA_GAME_KEY>`, with the process
name as the game-key fallback. `MVK_CONFIG_IOS_BINARY_ARCHIVE_DIR` overrides the
runtime root; the transfer tool targets the default container path above.

The Mac mirror is `~/Library/Caches/madeira-msl/<game-dir-hash>/`, containing
`src/`, `lib/`, `module-cache/`, per-device push receipts, and `failures.log`.
Failed compiles/transfers are logged and do not stop other shaders/games. The
summary reports failures and the command exits nonzero if any occurred.

Compilation uses `xargs -P $(sysctl -n hw.ncpu)`. Content, options, compiler/SDK
version and script fingerprints determine freshness. The script preserves the
runtime's filename key; it never calculates a replacement shader key. Only new
or changed libraries are pushed, in directory batches of at most 256 files.
Remote presence checks repair libraries removed by app reinstall/cache eviction.
Temporary AIR files are deleted; successful libraries and freshness stamps are
published atomically. Concurrent runs serialize per game mirror.

## Option mapping

The JSON records the actual `MTLCompileOptions` used by the source compiler.

| JSON field | Offline Metal option |
| --- | --- |
| `languageVersion` | Decode `(major << 16) + minor`; `-std=ios-metalX.Y` for Metal 1/2, `-std=metalX.Y` for Metal 3+ (the CLI rejects `ios-metal3.x`) |
| `mathMode` | 0/1/2 → `-fmetal-math-mode=safe/relaxed/fast` |
| `mathFloatingPointFunctions` | 0/1 → `-fmetal-math-fp32-functions=fast/precise` |
| `fastMathEnabled` | Older OS fallback when `mathMode` is absent: `-ffast-math` / `-fno-fast-math` |
| `preserveInvariance` | `-fpreserve-invariance` when true |
| `optimizationLevel` | 0 → `-O2` (MoltenVK's default), 1 → `-Os` |
| `preprocessorMacros` | One `-Dname=value` argument each; values use the original NSNumber textual representation, avoiding JSON float rounding |

Both compile and metallib linking use `xcrun -sdk iphoneos`. Unknown enum/schema
values fail that shader instead of silently using different options.

## Runtime controls

- `MVK_MADEIRA_PRECOMPILED=0`: disable disk library loading.
- `MVK_MADEIRA_MSL_COLLECT_MB=2048`: default per-game source-byte quota; `0` disables collection.
- `MVK_MADEIRA_LIBRARY_CACHE`: existing process LRU control, independent of disk loading/collection.

Collection runs on a serial queue with atomic file publication and a 32 MiB
pending-source limit. A full queue/quota or I/O failure never fails a shader
compile. Disk libraries must load successfully and contain the expected entry
point; otherwise MoltenVK compiles the source. Every 2048 successful disk loads
or source compiles, `[mvk-precomp]` reports loaded/compiled/collected totals.

## Validation and build

```sh
tests/moltenvk/test-precompiled.sh
git -C research/MoltenVK diff > patches/moltenvk-iediot.patch
PATH=/usr/bin:$PATH bash build/moltenvk-ios/build.sh
strings app/Madeira/gl/libMoltenVK.dylib | rg '\[mvk-precomp\]'
```

The host test extracts the actual runtime hash/collection helpers, exercises
concurrent collection and metadata, compiles/links iOS metallibs using the same
Python option mapping, checks freshness and failure recovery, and simulates
container transfers to test discovery, repeat runs and eviction repair. It does
not load an iOS metallib on macOS; final library loading is validated by Metal on
the iPhone, with runtime fallback if incompatible.
