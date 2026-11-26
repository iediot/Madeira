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
