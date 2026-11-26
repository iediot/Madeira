# RDR2 on iPhone 18 Pro: CPU clock, thread QoS and audio (2026-11-27)

Evidence: `~/MadeiraLogs/iphone18/1127-0019-ran/madeira-log.prev.txt` (RDR2 pid
0x48c, gameplay from 23:30). Cross-checked against `1126-2017e`, `1126-2158-hang`,
`1126-2243-hang2`. Host analysis only; nothing here is device-validated yet.

## What holds the CPU clock down

- Loading (23:28:40-23:30:01): CPU at P 4.7 / E 2.5 GHz, process CPU energy
  6-7 W ([xp] `mJ`/`dt`), GPU nearly idle (ml1108 `gpu_busy` 44-67 ms per 5 s).
- 23:30:02, ~580 J into the session (~510 J of it above 2.3 W): both clusters drop
  together to P 1.44 / E 1.8 GHz and the process gets 1.0-1.5 W for the rest of
  gameplay. Thermal state stays `nominal` (only `fair` at 23:32:34).
- The cap is not a utilization/DVFS misjudgement: during the clamp the P cluster
  is 75-92 % busy and the E cluster 66-83 % ([xp] `P=`/`E=` ms per 2 s), which a
  utilization governor would answer with high clocks; the E cluster even runs
  above the P cluster's clock. It matches the energy-budget clamp measured earlier
  (ml1133, commit 3930d185: "clamps after ~250 J above ~2.3 W") and shows in the
  other logs (1126-2017e: 0.5-1.5 W at P 1.44 while presenting; 2.5-4 W at P
  2.4-2.9 while loading with no presents).
- It lifts when the frame gets lighter for the CPU: the 23:32:29-23:33:04 cutscene
  ran P 2.6-4.0 GHz at ~2-2.7 W and 50-60 fps; gameplay windows with small
  `gpu_busy` briefly reach 3-4 GHz too. Whether the GPU's draw is what the budget
  trades against cannot be read from this log (no GPU energy counter; the old
  power probe is no longer in the tree).
- Consequence: QoS, spin/sleep or workgroup tricks cannot lift the cap itself.
  Under a fixed budget the levers are (a) the critical path gets the fast cores
  first, (b) less wasted energy (spinning helpers, redundant GPU traffic: ml1137
  reports ~230 MB/frame of stored attachments nothing reads), (c) efficient cores
  for parallel work.

## Threads (RDR2 names none of its own)

madeira_d3d12 roles from `[xp-t]`: 0490 main (E role), 0500 render (E+P roles),
04f8/04c8/0514/05b8/05c4 other ExecuteCommandLists callers; job workers 04b8,
04bc, 04c0, 04c4, 0554, 0564-0574 at ~550 ms per 2 s each. All guest threads
ran at user-interactive, so on two P cores the main thread spent 384 of 852 ms
(45 %) on E cores and the render thread 142 of 692 ms (20 %).

Change: `exe:RDR2.exe=initiated` in RDR2's `MADEIRA_THREAD_QOS`, and
`ios_thread_qos_frame_critical()` (thread_ios.c) lifts back to user-interactive:
madeira_d3d12 role threads (server_ios.c `ios_xp_set_role`), audio writers and the
audio timer (audio_null_ios.c), threads that wait on the GPU timeline or build
pipelines (winemetal_unix.c: the fence worker and pipeline-build pool), and
threads the game raises to THREAD_PRIORITY_HIGHEST or above. Only a
user-initiated/default exe rule opts in; a utility/background process (Social
Club's helper) is never lifted. `[win-prio]` logs what the game asks for.

## Audio

- Slot 1 is RDR2's own WASAPI stream: shared, flags 0 (polled, no event),
  30 ms requested, ring floored to 100 ms. Its ml1065 sample counts per ~10 s:
  100 % before the clamp, 92-98 % at 31-40 fps, 65-75 % in clamped gameplay at
  19-22 fps, 95-100 % in the 50-60 fps cutscene. The deficit tracks presents
  closely (about fps/29.5 in 23:31:43-23:31:57) and also the clock, so the game's
  sound production stalls with its frame; no driver buffer can make up a sustained
  30 % deficit.
- 1126-2158/2243 (menu, ~53 fps): 100 %. 1126-2017e (21-25 fps): 54-68 %.
- Polled streams had no underrun statistics (only event streams print ml1230).

Changes (audio_null_ios.c):
- ml1240 report for every stream, from its writer thread: wrote %, device short
  passes, GetBuffer sizes/refusals, the real fill at the client's polls, longest
  write gap, writer TID + CPU time + QoS class, presents/s in the same window.
- De-click: 2 ms fade-out before an underrun gap and fade-in after it (default
  on, `MADEIRA_AUDIO_DECLICK=0` off). Changes nothing while the ring keeps up.
- Padding cushion for polled clients (`MADEIRA_AUDIO_CUSHION_MS`, RDR2 = 40):
  grows 10 ms per look while the device runs short, decays after 20 s clean;
  absorbs short stalls of a fill-to-target client. GetPosition unchanged.

## Next device run: lines to read

- `[thread-qos] xxxx (process rule) -> user-initiated` for RDR2 threads and
  `(frame-critical: ...) -> user-interactive` for 0490/0500/... and the audio writer.
- `[win-prio]` lines: which RDR2 threads it raises/lowers.
- `[xp-t]`: P/E split of 0490 and 0500 (was 468/384 and 550/142 ms per 2 s);
  `pres` per 2 s against the 38-61 baseline in comparable scenes.
- `[ios_audio] ml1240 slot=1 polled`: wrote %, presents/s, writer CPU % and
  padding at polls (a fill level near 30 ms with a high writer CPU % means a
  starved mixer; low writer CPU with wrote% ~ fps/30 means frame-locked audio).

## Wave1 log (01:05): runnable oversubscription, yield spins, bursts

Evidence: `~/MadeiraLogs/iphone18/1127-0105-wave1/madeira-log.txt` (RDR2 pid 0x44c,
gameplay 01:00:20-01:03:50). Host analysis only.

- Clamped windows: cpu 8.4-9.2 s per 2 s (P 2.1-2.6 s of 4, E 6.1-6.9 s of 8), `run`
  48-57 s (~25 threads runnable), ~1 W, 40-50 presents, ~45 mJ per frame. The 1.5 W
  windows (P 1.55-1.75 GHz) cost 55-87 mJ per frame. Collapse windows (01:02:02.8,
  01:03:02.7) show cpu ~4.1 s with `run` still 23-30 s: runnable threads, idle cores.
- Six RDR2 workers (0514, 0524, 0528, 052c, 0530, 0534) each ran ~410 ms per window
  (P ~120 / E ~290) at ~2,800 M instructions, i.e. ~4.1 instructions per cycle: a wait
  loop's signature (main thread 0450: ~1.1 IPC, render 04c0: ~2.7). Below the old
  `[spin-pc]` gate (>= 30 % of a core), so never sampled.
- `[xp-api]`: 5,100-10,900 yields/s, 3,400-8,200 of them Sleep(0), 400-2,000 Sleep(1);
  Sleep(0) rises with the clock (more CPU, more spinning). The ml1063 adaptive yield
  slept only 76-280 times/s in clamped windows: its streak resets after a 2 ms gap,
  shorter than one sched_yield with 25 runnable threads.
- SocialClubHelper's thread 034c (pid 0268, utility) used ~560 ms of E core per window.
- Log: ~165 lines/s in play; FEX's per-event SIGBUS/SMC lines and `[decommit-zero]
  branch=swap-zero` were most of it.

Changes (all host-tested, none device-validated):
- `[xp-t]`: run-state sampling per ~125 ms slice (`:r<ms>` runnable time per thread,
  `| runnable N of M threads: <pid>=<avg>`), per-thread y/z/s/e yield counts, 24 threads;
  `MADEIRA_XP_RUNQ=0` turns sampling off. Worker `[spin-pc]` gate: >= 12 % of a core at
  >= 3.5 IPC, one per window, 40 in all.
- Yield escalation (`env.MADEIRA_YIELD_ESCALATE = RDR2.exe` in the RDR2 profile):
  after 32 yields with no real wait (and < 250 us of own CPU between them) a yield sleeps
  20 us doubling to 100 us (cfg max 500); any real wait resets. `[xp-api] escalated N/s M ms/s`.
- Present pacer (`present-cap-fps`, `present-pace-mw`, `present-pace-min-fps`): off by
  default, in no profile. `[present-pace]` every ~5 s.
- FEX per-event lines sampled in the unix `unixcall_wine_dbg_write` (`[fex-log]` counts;
  `env.MADEIRA_FEX_LOG_ALL = 1` off); swap-zero `[decommit-zero]` lines rate-limited
  (`env.MADEIRA_DECOMMIT_LOG_ALL = 1` off).
- Off switches that restore the old behaviour exactly: `env.MADEIRA_YIELD_ESCALATE = 0`,
  `env.MADEIRA_YIELD_STATS = 0`, `env.MADEIRA_XP_RUNQ = 0`, `env.MADEIRA_SPIN_PC_WORKERS = 0`,
  `env.MADEIRA_FEX_LOG_ALL = 1`, `env.MADEIRA_DECOMMIT_LOG_ALL = 1`; the pacer is off unless
  `present-cap-fps` / `present-pace-mw` are set.
