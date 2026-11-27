# RDR2 overnight changes: morning checklist (2026-11-27)

Nothing in this list has run on a device. Everything below was stacked overnight and
gets tested in one run. Each change has its own off switch: add the line to RDR2's
game config (or remove the profile line in app/Madeira/Library.swift) to rule it out.

## If the game does not start or hangs on the loading screen

Try in this order, one at a time:

| Suspect | Off switch |
|---|---|
| Social Club park (freezes the helper after 180 s) | `env.MADEIRA_SC_PARK = 0` |
| Social Club wait floor | `env.MADEIRA_WAIT_FLOOR =` (empty) |
| Social Club lighter command line | `env.MADEIRA_SC_CEF_LITE = 0` |
| DLL resource-tail trim in the JIT pool | `env.MADEIRA_POOL_IMAGE_TRIM = 0` |
| SMC fast path in the Mach handler | `env.MADEIRA_SMC_FAST_DECLINE = 0` |
| RockstarService / Social Club at utility QoS | remove `exe:RockstarService.exe=utility` from `MADEIRA_THREAD_QOS` |

## If the game runs slower than before

| Suspect | Off switch |
|---|---|
| RDR2 workers at user-initiated QoS | remove `;exe:RDR2.exe=initiated` from `MADEIRA_THREAD_QOS` |
| Audio padding cushion | `env.MADEIRA_AUDIO_CUSHION_MS = 0` |

## If there are graphical glitches

| Suspect | Off switch |
|---|---|
| Render-pass carry across command lists | `rp-carry = 0` |
| Dead attachment stores (discard proof) | `dead-store-discard = 0`, then `dead-store = 0` |
| Fused encoder end | `end-fuse = 0` |
| Root-signature dedup | `rs-dedup = 0` |
| AIR binding plans | `air-plan = 0` |
| View-info cache | `view-info-cache = 0` |

## Audio

| Change | Off switch |
|---|---|
| De-click fades around underruns (all games) | `env.MADEIRA_AUDIO_DECLICK = 0` |

## Log lines that prove each change ran

See docs/RDR2_GRAPHICS_IPHONE18_2026-11-27.md, docs/RDR2_CPU_CLOCK_AUDIO_2026-11-27.md and
docs/RDR2_LOAD_TIME_IPHONE18_2026-11-27.md for the per-change lines to check.

## Added later tonight

(appended as each overnight change lands)

### Yield-spin escalation, sampler and log volume (wine unix sync.c/debug.c, server_ios.c)
| Change | Default | Off switch |
|---|---|---|
| Yield spin becomes a short sleep after 32 yields (20 µs up to 100 µs) | RDR2.exe via profile | `env.MADEIRA_YIELD_ESCALATE = 0` (first suspect if slower or hanging) |
| Per-thread yield counts in `[xp-t]` | on | `env.MADEIRA_YIELD_STATS = 0` |
| Per-thread runnable sampling in `[xp-t]` | on | `env.MADEIRA_XP_RUNQ = 0` |
| Worker `[spin-pc]` samples | on with MADEIRA_SPIN_PC | `env.MADEIRA_SPIN_PC_WORKERS = 0` |
| FEX per-event log sampling | on | `env.MADEIRA_FEX_LOG_ALL = 1` |
| `[decommit-zero]` rate limit | on | `env.MADEIRA_DECOMMIT_LOG_ALL = 1` |
| Frame pacer (`present-cap-fps`, `present-pace-mw`) | OFF, in no profile | owner decision: `present-cap-fps = 30` to try a locked 30 |

### Registry read cache (wine unix registry.c, regcache_ios.c)
| Change | Default | Off switch |
|---|---|---|
| Read-only key opens/values answered in ntdll | RDR2.exe via profile | `env.MADEIRA_REG_CACHE = 0` (suspect on registry errors, `[bad-close]` near 0x7ff0xxxx) |
| `[reg-census]` naming hot keys every 30 s | RDR2.exe via profile | `env.MADEIRA_REG_CENSUS = 0` |

### Social Club hide and park (process_ios.c, virtual_ios.c, server_ios.c, queue_ios.c)
| Change | Default | Off switch |
|---|---|---|
| WasHidden on the helper's browsers 180 s after RDR2 starts (helper 2.4.0.302 only) | RDR2 via profile | `env.MADEIRA_SC_HIDE = 0` (suspect if the in-game Social Club overlay is blank) |
| Helper timed waits stretched to 5 s once armed, lifted on signal/SendMessage, Present watchdog | RDR2 via profile | `env.MADEIRA_SC_PARK = 0` (first suspect if the game hangs after ~3 minutes) |
| Signal lift for parked waits | on | `env.MADEIRA_SC_PARK_LIFT = 0` |
| `[sc-cpu]` helper CPU report every 30 s | on | `env.MADEIRA_SC_CPU = 0` |
| Named sampler for Chrome_InProcRendererThread | RDR2 via profile | `env.MADEIRA_SPIN_PC_NAMES =` (empty) |

Hang triage: a `[sc-park] WATCHDOG` line means presents stopped while the helper was parked;
if it repeats right after each `[sc-park] ON`, set `env.MADEIRA_SC_PARK = 0`.

### Gamepad reports only on change (wineserver hidpad_ios.c)
| Change | Default | Off switch |
|---|---|---|
| Pending HID reads complete only when the Xbox pad report changes, plus a 200 ms keep-alive | RDR2 via profile | `env.MADEIRA_HIDPAD_ON_CHANGE = 0` (suspect on stuck or laggy input) |
| Keep-alive interval | 200 ms | `env.MADEIRA_HIDPAD_KEEPALIVE_MS = 4` restores a report every tick |

### Incremental argument tables (madeira-d3d12)
| Change | Default | Off switch |
|---|---|---|
| Roots-changed table misses rewrite only the changed entries into a copy | on | `air-table-inc = 0` (first suspect for any new graphical glitch) |
| View-info cache 1024 slots | on | `view-info-cache-big = 0` |

### Frame-cap pacer fix
The pacer's early version that slipped into 641b711c is fixed here; the pacer itself stays off.

### Still to find out in the morning
- The ~75 s loading screen: the game is idle at full clocks with modest reads, so it is waiting on
  something. One run with the wait probe (`env.MADEIRA_PERF = 1`) over the loading screen names it.

### App-side threads (Swift, WineServerBridge.m, server_ios.c)
| Change | Default | Off switch |
|---|---|---|
| Native thread names in `[xp-t]` (`m<tid>~wineserver`, `~main`) | on | `env.MADEIRA_XP_TNAMES = 0` |
| `[app-cpu]` main thread / wineserver CPU every 30 s | on | madeira.cfg `env.MADEIRA_APP_CPU_LOG = 0` |
| ProMotion display link on its own thread (MAX/RAW/40 caps only) | on | madeira.cfg `env.MADEIRA_PROMOTE_LINK_THREAD = 0` (suspect if pacing changes at those caps) |
| Developer FPS overlay single 4 Hz tick | on | madeira.cfg `env.MADEIRA_HUD_LEAN = 0` |
| Log-tail poll parked while log display is paused | on | madeira.cfg `env.MADEIRA_UI_LOG_IDLE_POLL = 0` |

### Fastsync: RDR2's semaphores and multi-object waits in-process (wine unix sync.c, server event.c/semaphore.c)
Log 01:05 gameplay, per second: ~5.4k `select` (req29), ~1.2k `release_semaphore` (req41), ~0.7k
`event_op` (req31). ~1.7k of the selects were single semaphore waits (semaphores had no fastsync cells:
`sem=off`), ~2.3k were wait-any on several events (fastsync took only one-handle waits). The `tid` after
each `reqN` in `[srv-req]` is a space-saving heavy hitter (~25 % of every count is inherited), not the
one thread that sends them.
| Change | Default | Off switch |
|---|---|---|
| Semaphore cells and fast path for semaphores RDR2.exe creates (others keep server semaphores) | RDR2 via profile | `env.MADEIRA_FASTSYNC_SEM_EXES = 0` (FIRST suspect on any load stall or hang: the session-wide `MADEIRA_FASTSYNC_SEM` was pulled after a Dock load stall, dd39f3b2, never root-caused) |
| Non-alertable wait-any on 2-64 cell objects answered in-process (lowest signalled index; parks ≤ 2 ms, then the server) | RDR2 via profile | `env.MADEIRA_FASTSYNC_MULTI = 0` (second suspect) |
| Threads parked in a multi wait at once | 4 | `env.MADEIRA_FASTSYNC_MULTI_PARKED = N` (1-32) |

Lines that prove they ran: `[fastsync] ml2120 per-image: semaphore fast path for RDR2.exe; multi-object
wait-any fast path for RDR2.exe`, `[fastsync] ml2120 server: semaphore cells for MADEIRA_FASTSYNC_SEM_EXES=RDR2.exe
(first from pid …)` (RDR2's pid), and every 30 s `[fastsync] ml2120 multi-wait 30s: … no server N …`.
Compare `[srv-req]` req29/req41/req31 and `select by shape … first object: semaphore` with the 01:05 log.
Must not appear: `[fastsync] desync: … kind=sem`.

### Page waits and the ~1 s freezes (virtual_ios.c swap tier, madeira-d3d12)
Finding: 6 of the 7 slowest deferred swap-file F_PUNCHHOLE calls (0.7-2.1 s) were followed by a
Present gap of about the same length. Likely cause: the swap tier's disk-cap fstat runs under the VM
lock and waits behind the punch, so every Wine VM operation stalls. These are the drops followed by a
burst to 60.
| Change | Default | Off switch |
|---|---|---|
| Disk-cap check reads a cached swap-file size (refreshed off the lock every 250 ms) | on | `env.MADEIRA_SWAP_DISK_CACHE = 0` |
| Hot decommit/recommit of ≤1 MB resident ranges zeroed in place instead of rotated | on | `env.MADEIRA_SWAP_ZERO_INPLACE = 0` (bug if any `*** NOT ZEROED ***` with branch=swap-inplace) |
| `[pgw]` page-wait sampler and per-cause split every 2 s | on | `env.MADEIRA_PGW = 0` (also disables the disk cache refresher) |
| WILLNEED prefetch | off | enable: `env.MADEIRA_PGW_PREFETCH_KB = 512` |
| Upload buffers off the file tier | off | enable: `upload-anon-mb = 256` (adds up to 256 MB to the footprint) |
Check: `[swap] ml1291 F_PUNCHHOLE (deferred) … took` should no longer line up with `[present-api-hitch] entry_gap_ms` of ~1 s.

### Loading-screen wait census (server_ios.c, loadwait_ios.h, perf_ios.c, queue_ios.c, sync.c, socket.c, file.c)
Diagnostics only: no wait, timeout, signal, QoS or graphics setting changes. Background: in all seven RDR2
loads in `~/MadeiraLogs/iphone18` the main thread sits at 10-30 ms of CPU per 2 s for 9-70 s right after the
first loading frame (the "dead zone"; 18 s in 1127-0105-wave1, 00:59:04-00:59:22), and the zone ends with the
same pair of HTTPS requests (prod.ros 192.81.241.100, then an Akamai CDN host). The v3 wait probe never fires
there (it needs a window without Presents; the loading screen presents at ~55 fps). Details:
docs/RDR2_LOAD_TIME_IPHONE18_2026-11-27.md, section "The dead zone".
| Change | Default | Off switch |
|---|---|---|
| `[load-wait]` census of RDR2's waits (select/sleep/alert per thread, object, timeout; waker pid/tid), 180 s from the first Present, a report every ~2 s, then off for the session | RDR2 via profile (`env.MADEIRA_LOAD_WAIT = RDR2.exe:180`) | `env.MADEIRA_LOAD_WAIT = 0` |
| `[load-wait-now]` main thread + 3 longest waiters' current server wait, `[stall-wait-stack]` guest callers, `[stall-wait-srv]` / `[load-wait-srv]` object type, name, last setter | with the census | with the census |
| `[guest-log] F#` every line of Social Club's own log files (own 4000-line budget, tickets/emails/passwords redacted) | RDR2 via profile (`env.MADEIRA_GUEST_LOG_FILES = Social Club`) | `env.MADEIRA_GUEST_LOG_FILES =` (empty) |
| `[sock-tl] ... by=<pid>/<tid>`: which process made each connection | on (all games) | `env.MADEIRA_SOCK_TL_OWNER = 0` |

Cost: unset = one load per wait; armed = one short locked insert per RDR2 wait for 180 s; nothing after.
None of this can hang the game (reads only; the wineserver answers from its own tables).

Morning run (no extra settings needed; leave `MADEIRA_PERF` off):
1. Launch RDR2 as usual, touch nothing until gameplay, play ~1 minute, AirDrop `madeira-log.txt`.
2. Proof it ran: `[load-wait] madeira-bcd: following RDR2.exe`, `[load-wait] armed: pid=…`, then
   `[load-wait] +Ns …` every ~2 s and `[load-wait] done` ~180 s later; `[guest-log] madeira-bcd file mirror`;
   `[sock-tl] … by=`.
3. Find the dead zone: `[xp-t]` rows where the main thread (pid+4, e.g. 0450) has < 50 ms. In those
   windows read `[load-wait] +Ns … main 0450 waited …` and the entries below it (`0450*` = main thread):
   - `select … h=X` + `[load-wait-srv] … h=X type=… name=… last_set_tid=…` + `waker=tid/pid`: map the
     waker pid with the `[srv-own] new_thread … pid=` lines (launcher, SocialClubHelper.exe,
     RockstarService.exe or RDR2 itself). An Event/IoCompletion/pipe set by the launcher or the helper =
     RDR2 waits on Social Club's IPC; a Socket/File with `timeouts=` and `waker=server/` = a network
     or I/O timeout.
   - `sleep … tmo=Nms xK` = a poll loop; `[load-wait-now]` + `[stall-wait-stack]` name the code.
   - `alert addr=…` = a lock or condition variable.
4. `[guest-log] F#` lines (Social Club's logs) with their own timestamps around the zone's end, and which
   pid made the prod.ros + Akamai pair (`[sock-tl] … by=`).
5. If the Files app can reach the app's Documents folder, also copy
   `wine/drive_c/users/mobile/Documents/Rockstar Games/Social Club/*.log` and
   `wine/drive_c/users/mobile/AppData/Local/Rockstar Games/Social Club/ipc.log`.

## A/B tries for the morning

Opt-in switches that are in no profile. Run the normal launch first (A), then add the line to RDR2's
game config for the second launch (B), and compare.

| Try | Line to add | When |
|---|---|---|
| Social Club / RockstarService at default QoS until RDR2 has run 180 s (their `exe:…=utility` rules wait for the `MADEIRA_WAIT_FLOOR_AFTER = RDR2.exe:180` arming) | `env.MADEIRA_THREAD_QOS_EXE_AFTER = SocialClubHelper.exe;RockstarService.exe` | try if the loading dead zone is long |

What to compare between A and B:
- The dead-zone length: the `[xp-t]` rows after the first loading frame in which RDR2's main thread (pid+4,
  e.g. 0450) has < 50 ms CPU per 2 s; count the seconds until it runs again. Also the helper's
  (SocialClubHelper.exe) CPU in the same rows and the `[sc-cpu]` lines.
- The `[load-wait]` census lines in that window: whether the main thread's wait (`main 0450 waited …`,
  `[load-wait-srv] … last_set_tid=`, `waker=`) is set by the helper/RockstarService and ends sooner in B.
- Proof B ran: `[thread-qos] exe rules for SocialClubHelper.exe;RockstarService.exe deferred until rdr2.exe
  has run 180 s` once at start, `[thread-qos] rdr2.exe started (pid …): deferred exe rules apply 180 s from now`,
  then `[thread-qos] armed: exe rules now apply to SocialClubHelper.exe;RockstarService.exe` once ~180 s
  later, followed by `[thread-qos] <tid> (process rule, deferred) -> utility` lines for the helper's threads.
- Gameplay after the arming should match A (same classes from then on); a slower first 3 minutes of
  gameplay in B would mean the helper at default QoS costs the game more than the dead zone saves.

### Review notes (two read-only reviews, 2026-11-27 night)
- No confirmed hang, deadlock or corruption in any overnight change.
- Social Club park now uses 2 s slices (`env.MADEIRA_SC_PARK = 2`): a helper reply behind a
  non-alertable Sleep or a polled pipe can't be lifted, so a caller (launcher heartbeat, game IPC) could
  wait a whole slice. Watch for `[sc-park] WATCHDOG`, many `[sc-park] lifted`, or a launcher disconnect
  message; any of those: `env.MADEIRA_SC_PARK = 0`. If the 2 s run is clean, try `= 5`.
- Social Club overlay opened in play: may be blank for a few seconds and re-hides after 120 s
  (`env.MADEIRA_SC_HIDE = 0` if you need the overlay).
- `[pgw]` sampler runs for every game (diagnostic; `env.MADEIRA_PGW = 0` to remove its cost).
