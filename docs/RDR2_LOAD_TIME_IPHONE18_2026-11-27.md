# RDR2 loading on the iPhone 18 Pro, 2026-11-27

This note covers what one session measured about RDR2's loading time and the
changes it made. None of the changes has been measured on a device yet.

Source logs (device, both kept in `~/MadeiraLogs/iphone18/`):

- `1127-0019-ran/madeira-log.prev.txt`: game pid 048c, main thread 0490.
- `1126-2017e/madeira-log.txt`, for comparison.

The times below come from the `[xp]` and `[xp-t]` lines, which are written
every 2 s.

## Timeline (1127-0019)

| Wall time | Phase | Evidence |
| --- | --- | --- |
| 23:28:18 | RDR2.exe spawned | `[session-log] ... RDR2.exe-2026-11-26_23-28-18` |
| 23:28:18-23:28:33 | The main thread runs about one whole P core (1.7-2.0 s CPU per 2 s, 23-40 G instructions per window) | `[xp-t]` -0490 |
| 23:28:25-23:28:31 | Within that phase, 49,374 `LdrLockLoaderLock flags 1 not supported` FIXME lines from the main thread, one log write each | `0490:fixme:module:LdrLockLoaderLock` |
| ~23:28:33 | D3D12 device created; the shader cache starts **cold** ("removed 1 older build cache(s)") | line 155117, 156181, 162375 |
| 23:28:33-23:28:37 | 2,000 graphics and 719 compute pipelines on four game threads | `pso time ... 1869 ms, 719 compute 907 ms; conversion+cache 1251 ms` |
| 23:28:37 | First loading-screen frame | `[xp] ... pres 105/105` |
| 23:28:37-23:28:43 | Read burst: 414, 130, 878 and 93 MB per 2 s; up to 88k page-ins and 337 ms page wait per 2 s | `[xp]` rdKB, pin, pgw |
| 23:28:43-23:30:01 | **78 s of loading screen.** RDR2's own threads use about 1.0-1.5 s CPU per 2 s, and the main thread about 10 ms per 2 s from 23:28:50 to 23:29:10. Reads are 1-80 MB per 2 s. | `[xp-t]` |
| 23:30:02 | Gameplay begins (`caller-ecl` jumps to 1407 ms per 2 s) | `[xp]` |

- From spawn to the first frame took about 19 s, and from spawn to gameplay
  about 104 s.
- In 1126-2017e the times were 17 s and 91 s.
- In both logs the PE shader cache started cold, and the unix-side shader pack
  was warm (256 of 256 hits).

During the 78 s loading screen, most of the CPU went to things other than
RDR2:

- SocialClubHelper's `Chrome_InProcRendererThread` (tid 0350, utility QoS)
  used 1.3-1.7 s of E-core time per 2 s, at 1.5-2.0 GHz.
- The process's runnable time (threads waiting for a core) was 7-16 s per 2 s.
- The P cores, by contrast, ran only 0.5-1.1 s per 2 s, at 1.8-3 GHz.

That points at scheduling and QoS, which `thread_ios.c` handles, and at
whatever the game waits on during that window. It does not point at shader or
pipeline work. Nothing in this session changed scheduling.

## Shader and pipeline caches

| Cache | Location / key | Survives an app rebuild? |
| --- | --- | --- |
| madeira-d3d12 PE shader cache | `LOCALAPPDATA\Madeira\ShaderCache\<build>\<switch set>`. `<build>` was the DLL's converter stamp plus `MADEIRA_APP_IMAGE_UUID`. | **Was no; fixed.** The image UUID changes with every Xcode build. |
| Unix DXBC/DXIL cache and pack (`Documents/shadercache`, `pack/*.mdpk`) | `MAD_SC_BUILD` = `"converter " MADEIRA_IR_CONVERTER_ID`, a hash of the converter's sources, flags, LLVM config and the MSC library | Yes (256/256 pack hits after the rebuild in both logs) |
| DXMT D3D11 shader DB (`dxmt/<exe>/shaders_<v>.db`, under Caches with `DXMT_IOS_CACHE_DIR`) | `kDXMTShaderCacheVersion` (19), bumped by hand | Yes. It is invalidated only when someone bumps the constant, which is how upstream does it. RDR2 does not use it (it is D3D12). |
| Metal's own pipeline cache | DXMT asks for `dxmt/<exe>/com.apple.metal`, but `WMTSetMetalShaderCachePath` fails ("Failed to set Metal cache path, fallback to system default"), so the app's default `com.apple.metal` is used. That is the right result for in-process pseudo-processes, because the path is per process. | Yes in these logs. Pipelines cost about 0.2 ms each apart from conversion, and lazy first-draw builds about 0.4 ms each (227 in 90 ms), so Metal's cache was warm right after the rebuild. |
| pso-archive (Metal binary archives, opt-in, off for RDR2) | `LOCALAPPDATA\Madeira\PipelineArchive\<switch set>-<exe>`, outside the build directory | Yes. An entry is found only for the same shader. |

What a cold PE cache costs, from the same build launched twice
(`1126-2158-hang`, cold `.prev` run against the warm current run):

| | Cold | Warm |
| --- | --- | --- |
| Graphics pipelines | 2,785 in 2,256 ms | 922 ms |
| Compute pipelines | 719 in 963 ms | 290 ms |
| Shader conversion and cache | 1,465 ms | 598 ms |

So a cold PE cache costs about 2 s of game-thread CPU, and about 0.5 s of wall
time before the first frame (four threads).

A fully cold converter costs much more. With the unix pack empty
(`1125-1505`), pipeline creation took 18.6 s of thread time, 13.9 s of it in
conversion.

## Changes

1. **PE shader cache keyed on the converter's identity, not the app image UUID**
   (`madeira-bcd sc-conv-id`).
   - `WineProcessBridge.m` `madeira_export_image_uuid` reads the literal
     `"converter <16 hex>"` (`MAD_SC_BUILD`) from the app image's
     `__TEXT,__cstring` and exports it as `MADEIRA_IR_CONVERTER_BUILD`.
     It needs exactly one distinct value; if there is none, or more than one,
     the variable stays unset.
   - `madeira_d3d12.c` `mad_sc_init` keys the build directory on the DLL stamp
     plus `"unix " + that identity`.
   - Without the identity (an older app), it is byte-for-byte the previous
     UUID formula, so such an app keeps its cache.
   - The identity is read from the image instead of through a new exported
     function. Any edit to `madeira-d3d12/src/unix` or `build/dxmt-ios/build.sh`
     changes `MADEIRA_IR_CONVERTER_ID`, and so would empty every shader cache
     once (about 14 s of conversion for RDR2, minutes for Ghost of Tsushima).
   - The converter identity stays `71d7ea9b5dd2f88c`.
   - What the first launch after installing this does: the cache moves once
     from the UUID-keyed directory to the identity-keyed one (one cold PE run,
     about 2 s of CPU, with the unix pack still warm). After that, Xcode
     rebuilds keep it.
2. **`LdrLockLoaderLock` flag 0x1 is noted once instead of once per call**
   (`wine/dlls/ntdll/loader.c`).
   - Flag 0x1 means "raise on errors". It only matters on the error paths,
     which return their status as before.
   - Unknown flags still log every time.
   - This removes about 50,000 log writes from RDR2's main thread while it
     starts. The cost per write was not measured; it is estimated at a few
     microseconds each, so about 0.2-0.5 s in total.

## What to check in the next device log

- `[madeira-d3d12] shader cache build identity: converter 71d7ea9b5dd2f88c`.
  The line `app image UUID (the app exports no converter identity)` means the
  app build does not have the bridge change.
- The shader cache ON path uses a new `<build>` directory, and the first run
  logs "removed 1 older build cache(s)".
- After **another Xcode rebuild** with the same converter:
  - The `<build>` directory name is unchanged.
  - `shader cache: 1000 hits, 0 misses` appears.
  - The pso time line shows about 900 ms for graphics and about 300 ms for
    compute, against about 2,250 / 950 ms before.
- No line saying "the app's converter is ..., this DLL was built for ...". If
  it appears, the DLL and the app were built from different converter sources.
- `LdrLockLoaderLock` appears once, as `flags 1: 0x1 (raise on errors) not
  supported ... noted once`, instead of about 50,000 times.

## Not changed, for others to follow up

- The 78 s loading-screen phase. To find what RDR2 waits on there, a run with
  `MADEIRA_PERF=1` (v3 wait probe) over the loading screen is needed. Social
  Club's renderer and the runnable-time numbers above belong to the QoS work.
- FEX translation cannot be cached for RDR2 (`MADEIRA_FEX_CODE_CACHE=0`,
  because Rockstar's anti-tamper re-encrypts code), so the main thread's
  first-phase work is translated fresh on every launch.
- Faster hashing for `mad_sc_key` and `mad_libshare_key`. Both are byte-serial
  at about 1.6 ns/byte on the M-series host. The estimate is a 0.2-0.4 s CPU
  saving per load. The persistent key would change, so this is left for a
  change that alters the converter identity anyway.
