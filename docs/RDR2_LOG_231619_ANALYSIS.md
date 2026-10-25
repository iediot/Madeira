# RDR2 device log 23:16:19 — October 21

Preserved `PlayRDR2.exe-2026-10-21_23-16-19.txt` (16.8MB) at
`/private/tmp/madeira-rdr2-oct21.6xlOQM/`. No runtime/config/save changes or
builds during this analysis. Diagnostics v2 is active and completion counters
now advance in synchronous mode: snapshots actually fired (12 windows).

## Game-owned frame intervals

Numbers below use `[d3d12-frame-window]`, not the mixed-process native `[perf]`
FPS. Window times are aligned approximately to the preceding XP sample; they
can lag the frame that caused an outlier. Present-entry intervals are not
display-completion intervals. Different routes/scenes prevent controlled A/B.

| XP period | Weighted submission FPS | Intervals | >50ms | >100ms | Maximum |
| --- | ---: | ---: | ---: | ---: | ---: |
| >=100s | 47.31 | 20,279 | 225 | 57 | 6,201.5ms |
| 200–330s | 29.25 | 3,750 | 179 | 53 | 6,201.5ms |
| >=330s | 53.30 | 10,671 | 30 | 1 | 174.3ms |

Late >50ms share is 0.28%, with no >300ms interval after330s. Prior22:51 run's
late400–520s was39FPS/12of4694>50ms; these are different periods/scenes, not a
proven code speedup. Thermal became fair around398s; improvement is not evidence
of cooling. New async-submit0 matches prior runs; diagnostics did not enable it.

## Two distinct classes of stall

1. First-use pipeline burst around200–212s. RDR2's render thread048c is in
   `kevent_id` during repeated snapshots201–207s, with a redacted Apple caller.
   Many parallel first-use PSOs compile in that interval; individual measured
   Metal compiles reach1034/953/950ms. The~6.2s entry gap coincides with the
   batch, but live PC-only samples cannot prove the full wait chain or charge
   the entire gap to any one pipeline. Library restore times are near0.
   Final cumulative lazy pipeline count619 /25,207ms across threads; this sum
   is NOT wall-clock stall time. Conversion/cache time36.4s is also cumulative.
   Identical stage names occur for distinct PSO objects; stage names alone do
   not establish identical render state or a safe whole-pipeline dedupe key.

2. Periodic~0.7s gaps around244/274/304s. The confirmed rendering-role native
   thread1569640 /Winepid040c/tid048c is WAITING in `read+8`, LR
   `wait_select_reply+0x38`. Other game threads also wait there. Some sampled
   game PCs belong to threads in Mach state4, which is an **uninterruptible
   wait**, NOT evidence that those threads were actively executing game code.
   Present itself takes~0.0–0.1ms after
   the gap. The sampled intervals have0 presents and0 caller ECL, negligible
   page waits, near-zero disk reads, no in-flight profiled reads, and no slow
   PSO log in those precise windows. This localizes the render thread to
   Wine object-wait completion, NOT a Metal drawable or asset read. It does
   NOT prove wineserver is slow/buggy: the game may legitimately wait for a
   producer, timer, event or fence whose signal is delayed upstream.

The filesystem worker has slow deferred punches576ms@93.5s/594ms@153.7s,
193ms@213.3s/123ms@393.2s/359ms@513.3s. None explains all244/274/304s gaps.
No >=50ms classifier-report marker. Four slow read logs are only startup audio
archives21–28ms. No allocation/heap-hitch markers and no stall-io markers.
Absence of sampled logs does not rule out every short syscall/VM delay.

Snapshot cost is a few milliseconds (examples2.6–4.4ms), with no added thread
suspension. Sampling includes some launcher/native threads; use Wine pid to
separate them from RDR2. Slow native drawable ownership was already established
as another process in the preceding log, not evidence of a100ms RDR frame.

## Most useful implementation follow-through

`wait_select_reply` is at `build/ntdll-unix/server_ios.c:2627`; its raw pipe
read is deliberately outside profiled asset-read wrappers. `server_select`
has the missing facts before it waits: select operation, object handles,
flags, absolute/relative timeout, native/Wine IDs, cookie, and optional caller
PC/context. Publish a bounded per-thread active-wait snapshot at that boundary,
clear on every exit/APC/retry, and join it with `[stall-pc]`. Then distinguish:
timer expiry, game job/event dependency, GPU completion/fence signaling,
or delayed wineserver wakeup. Capture slow completion durations/results too.
Do not shorten waits, force-signals, enable async replay, or alter barriers
based solely on a waiting PC. Host-test lifetime/cleanup, nested/APC cases and
disabled overhead before native rebuild. No such new instrumentation or
speculative performance patch was implemented in this read-only analysis.

The separate shader path needs bounded, correct PSO reuse/preparation with
complete descriptor/function identity and lifetimes; turning the experimental
warmer back on is not evidence-backed. Existing persistent shader libraries
do not by themselves cache every device-built Metal PSO.

## Additional analysis and availability correction (October24 handoff)

Darwin's SDK defines state1=RUNNING, state3=WAITING, state4=UNINTERRUPTIBLE.
At the periodic gaps, the following game-relative PCs were captured in state4
(RDR2 image base0x7175530000): tid0410 RVA0x7df911; tid04e8 RVAs0x257f854 and
0x257daba; tid05a0 RVAs0x2545d8d and0x2ea152a; tid0454 RVA0x281687a.
These are useful addresses to investigate, not established function names or
proof that the sampled instruction caused the kernel wait. State and registers
are sampled in separate calls, so they are not an atomic snapshot. VM/page-in
or other uninterruptible kernel work remains a candidate despite negligible
process page-wait counters. Do not restrict the investigation to wineserver.

All12 new snapshots together cost43.87ms; individual costs2.64–5.17ms. This
does not account for the~700ms periodic gaps, although profiling overhead is
not globally zero.

On October24, the earlier `/private/tmp/madeira-rdr2-oct21.6xlOQM/` archive
and temporary native build logs no longer exist. No matching Oct21–24 RDR2
text logs were found directly by the Downloads filename search. The saved
metrics/excerpts in repository reports and memory survive; the raw logs are
not currently available for fresh reanalysis. Do not claim otherwise.
