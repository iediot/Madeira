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

## The dead zone: what the loading screen waits on (evening analysis, 2026-11-27)

Read from every RDR2 log in `~/MadeiraLogs/iphone18` (per-process CPU summed from the `[xp-t]` rows,
thread-to-process from `[srv-own] new_thread`). No log has v3 wait-probe lines (`[stall-wait*]`,
`[wait-done]`): `MADEIRA_PERF` was off, and even on, its snapshot needs a window with no completed Present,
which the loading screen (~55 fps) never has.

### The load has three phases

| Run (log) | First loading frame | Dead zone: main thread <= 30 ms CPU / 2 s | Length | Then main thread CPU-bound | Ends with |
|---|---|---|---|---|---|
| 1126-1419 (`RDR2.exe-…13-30-41`) | 13:31:00 | 13:31:18-13:31:29 | ~10 s | 13:31:30-13:32:00 | 192.81.241.100 `tx=3/802B rx=6/420B` + Akamai 23.209.17.207 `tx=3/776B rx=6/306B` (L176230/L176238) |
| 1126-1445 | 14:41:17 | 14:41:36-14:41:45 | ~9 s | 14:41:46-14:42:00 | same pair, 14:41:44 (L177906/L177918) |
| 1126-1513 | 15:04:44 | 15:04:59-15:06:08 (main a flat 13-14 ms / 2 s) | ~70 s | 15:06:10-15:06:25 | same pair, 15:06:06 (L183672/L183677) |
| 1126-1719c | 17:13:02 | 17:13:16-17:13:26 (+17:13:51-17:14:01) | ~10 s (+10 s) | 17:13:28-17:13:45 | pair not logged; first 60 s heartbeat 17:13:35 |
| 1126-2017e | 20:07:14 | 20:07:22-20:07:38 | ~16 s | 20:07:40-20:08:01 | same pair, 20:07:38 (L179438/L179452) |
| 1127-0019 (`.prev`) | 23:28:37 | 23:28:43-23:29:10 | ~27 s | 23:29:12-23:29:31 | same pair, 23:29:08 (L187282/L187293) |
| 1127-0105-wave1 | 00:58:56 | 00:59:04-00:59:22 | ~18 s | 00:59:22-00:59:32 | same pair, 00:59:20 (L176694/L176700) |

After that comes the parallel load (main 0.4-0.8 s, streaming/job threads 0.3-2 s each per 2 s) until
gameplay. In wave1: dead zone 18 s, main-thread phase ~10-20 s, parallel phase ~40 s.

In the dead zone RDR2 uses ~0.5-1.1 s CPU per 2 s, nearly all its render thread drawing the loading
screen (04b8 ~340 ms) plus audio; its job threads are idle. Reads are 0-35 MB per 2 s, page waits small.

### Ranked candidates

1. **RDR2 waits on the Social Club / Rockstar online sign-in path (strongest, not yet proven).**
   - `socialclub.dll` loads into RDR2 at 00:58:54 (wave1 L153484), two seconds before the first frame.
   - 00:59:00-00:59:08: ~40 HTTPS connections to Rockstar services (prod.ros, crews, ugc, tm, app-rgl,
     inbox, feed, posse, prs, prod-core, prod-locator-cloud, prs55, prod.cloud) and sentry.io; then quiet.
   - The SocialClubHelper process (CEF, V8 `--jitless`, utility QoS) runs 2-4.9 s of CPU per 2 s in the
     dead zone (wave1 pid 0268: 3.7, 3.0, 3.2, 2.1 s at 00:59:04-00:59:10; 0019 pid 025c: 2.3-4.9 s;
     1513: one helper thread, 0354, 1.14 s per 2 s for the whole 70 s).
   - Every zone ends with the same prod.ros request + Akamai download (table above), and the 60 s
     `192.81.241.13x tx=1/35B rx=2/31B` heartbeat starts 2-11 s later in every run but 1513.
   - The length varies 9-70 s with the same build family: an outside dependency, not RDR2's own work.
   - Chrome IPC named pipes are the launcher link (`\??\pipe\chrome.rgsc_launcher_0`, `rgsc_ipc_118_channel_0`
     on the helper's command line).
   - Not proven: which object the main thread waits on and who signals it. The `[load-wait]` run decides.
2. **Not a network timeout.** All Rockstar connections complete: ClientHello -> server flight ~115-120 ms
   (one round trip), whole requests 0.35-0.95 s, DNS 0-70 ms, no connect failures or retries in the zone.
   One slow Akamai IPv6 connection (wave1 fd 3267, 4.5 s, L172701) is a single request, inside the burst.
3. **Not TLS crypto under FEX.** Handshakes are round-trip bound (see 2); RDR2's TLS CPU is small.
4. **Not disk.** 0-35 MB read per 2 s and low page wait in the zone; the big reads come later.
5. **Not GPU fences.** `[perf] ml1109 sync per frame … game blocked 0.0 ms/frame` in the zone (wave1
   00:59:08).
6. **No sign of Wine services (rpcss, services.exe) in the zone.** They are absent from the `[srv-req]`
   heavy hitters; the busiest requesters are RDR2's dinput worker (04dc, HID reads) and the helper.
7. **The main-thread phase after the zone is CPU, not waiting**: ~1.9 s per 2 s at IPC 4.2,
   `[spin-pc]` in RDR2.exe+0x2e5915b (wave1 L177146; the phase before the first frame looks the same, L132907), with heavy self-modifying-code
   faults (anti-tamper). FEX's code cache stays off for RDR2 (it re-encrypts its code).
8. **The parallel phase is short of cores**: runnable 8-14 s per 2 s against 5-6 s of CPU, with the helper
   still at 1.3-1.8 s per 2 s. QoS work, unchanged here.

### What was added to settle candidate 1 (diagnostics only)

- `[load-wait]` (`MADEIRA_LOAD_WAIT`, RDR2 profile `RDR2.exe:180`): every RDR2 wait for 180 s from the first
  Present, per (thread, kind, object, timeout): count, sum, max, timeouts, last waker (Wine tid/pid from
  the wineserver's wake record). Kinds: `select` (every wineserver wait, including IOCP and async pipe /
  socket / file I/O), `sleep` (non-alertable NtDelayExecution), `alert` (critical sections, SRW, condition
  variables, WaitOnAddress).
- `[load-wait-now]`, `[stall-wait-stack]`, `[stall-wait-srv]`, `[load-wait-srv]`: the main thread's and the
  three longest waiters' current waits, their guest callers (module+offset on the stack, e.g.
  socialclub.dll), and the server's view of the objects (type, name, waiters, who set an event last).
- `[guest-log] F#` (`MADEIRA_GUEST_LOG_FILES = Social Club`): every line of Social Club's own logs. The
  keyword mirror's 64 lines were used up by the launcher's "Start Menu" errors a minute before RDR2 started.
- `[sock-tl] … by=<pid>/<tid>` (`MADEIRA_SOCK_TL_OWNER`, on): which process made each connection.

Switches, off lines and the morning procedure: docs/RDR2_MORNING_CHECKLIST.md, "Loading-screen wait census".
