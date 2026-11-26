#!/usr/bin/env python3
"""Frame-critical threads under MADEIRA_THREAD_QOS (build/ntdll-unix/thread_ios.c); no Wine runs.

Compiles the production thread-QoS section (rules, store, madeira_guest_qos_self, exe rules,
thread start/naming, ios_thread_qos_frame_critical, ios_thread_qos_win_priority) against a fake
TEB/PEB and a recording pthread_set_qos_class_self_np, one pthread per guest thread, and checks:
  - an "exe:RDR2.exe=initiated" rule starts the game's threads at user-initiated;
  - ios_thread_qos_frame_critical lifts the calling thread to user-interactive once;
  - THREAD_PRIORITY_HIGHEST or above lifts (self, or another thread before it starts),
    NORMAL and below do not;
  - a process held at utility (Social Club's helper) is never lifted, by either path;
  - a process with no exe rule, and a run with MADEIRA_THREAD_QOS unset, are untouched;
and the wiring: ios_xp_set_role, the audio driver, winemetal's GPU-timeline wait and pipeline
builds, and NtSetInformationThread(ThreadBasePriority) call in.
"""
from pathlib import Path
import os, subprocess, tempfile

root = Path(__file__).resolve().parents[2]
src = (root / "build/ntdll-unix/thread_ios.c").read_text()
a = src.index("#define TQ_MAX_RULES")
b = src.index(" *           start_thread", a)
b = src.rindex("/****", a, b)
section = src[a:b]
assert "void ios_thread_qos_frame_critical(" in section
assert "static void ios_thread_qos_win_priority(" in section

# wiring
assert "ios_thread_qos_win_priority( handle, (int)*base_priority )" in src
server = (root / "build/ntdll-unix/server_ios.c").read_text()
role = server[server.index("void ios_xp_set_role("):]
role = role[:role.index("\n}\n")]
assert "ios_thread_qos_frame_critical(" in role
assert role.index("ios_thread_qos_frame_critical(") < role.index("pthread_threadid_np")
audio = (root / "build/ntdll-unix/audio_null_ios.c").read_text()
grb = audio[audio.index("static NTSTATUS ios_get_render_buffer("):]
assert "ios_thread_qos_frame_critical(" in grb[:grb.index("\n}\n")]
tl = audio[audio.index("static NTSTATUS ios_timer_loop("):]
tl = tl[:tl.index("\n}\n")]
assert tl.index("ios_thread_qos_frame_critical(") < tl.index("ios_audio_timer_thread_rt()")
wm = (root / "dxmt/src/winemetal/unix/winemetal_unix.c").read_text()
for fn in ("_MTLSharedEvent_waitUntilSignaledValue(void *obj) {", "_MTLDevice_newRenderPipelineState(void *obj) {",
           "_MTLDevice_newRenderPipelineStateVD(void *obj) {", "_MTLDevice_newComputePipelineState(void *obj) {"):
    body = wm[wm.index(fn):]
    body = body[:body.index("\n}\n")]
    assert "ios_thread_qos_frame_critical(" in body, fn

harness = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <pthread.h>
#include <pthread/qos.h>
typedef uint16_t WCHAR; typedef unsigned short USHORT; typedef void *HANDLE; typedef uintptr_t ULONG_PTR;
typedef struct { USHORT Length, MaximumLength; WCHAR *Buffer; } UNICODE_STRING;
typedef struct { UNICODE_STRING ImagePathName; } RTL_USER_PROCESS_PARAMETERS;
typedef struct { RTL_USER_PROCESS_PARAMETERS *ProcessParameters; } PEB;
typedef struct { struct { HANDLE UniqueProcess, UniqueThread; } ClientId; PEB *Peb; } TEB;
static __thread TEB *cur_teb;
static TEB *NtCurrentTeb(void) { return cur_teb; }
#define GetCurrentThread() ((HANDLE)~(uintptr_t)1)
#define THREAD_QUERY_LIMITED_INFORMATION 0x800
struct fake_req { HANDLE handle; unsigned access; unsigned tid; };
#define SERVER_START_REQ(x) { struct fake_req fr_, *req = &fr_, *reply = &fr_; memset(&fr_, 0, sizeof fr_);
#define SERVER_END_REQ }
#define wine_server_obj_handle(h) (h)
static int wine_server_call(struct fake_req *r) { r->tid = (unsigned)(uintptr_t)r->handle >> 4; return 0; }   /* handle 0x6000 -> tid 0x600 */
static __thread qos_class_t applied = QOS_CLASS_UNSPECIFIED;
static int fake_set_qos(qos_class_t c, int rel) { (void)rel; applied = c; return 0; }
#define pthread_set_qos_class_self_np fake_set_qos
volatile int ios_eco_gen = 1;
int madeira_guest_qos_self(qos_class_t cls, int rel);
void ios_eco_apply_self(void) { madeira_guest_qos_self(QOS_CLASS_USER_INTERACTIVE, 0); }   /* sync.c with eco off */
""" + section + r"""
static int fails;
#define CHECK(c, ...) do { if (!(c)) { printf("FAIL " __VA_ARGS__); printf("\n"); fails++; } } while (0)
struct job { const char *exe; unsigned tid; int (*fn)(struct job *); };
static void set_exe(TEB *t, PEB *p, RTL_USER_PROCESS_PARAMETERS *pp, WCHAR *buf, const char *exe)
{
    size_t i, n = strlen(exe);
    for (i = 0; i < n; i++) buf[i] = (WCHAR)exe[i];
    pp->ImagePathName.Buffer = buf; pp->ImagePathName.Length = (USHORT)(n * 2);
    p->ProcessParameters = pp; t->Peb = p;
}
static void *run(void *arg)
{
    struct job *j = arg;
    TEB t; PEB p; RTL_USER_PROCESS_PARAMETERS pp; WCHAR buf[128];
    memset(&t, 0, sizeof t);
    set_exe(&t, &p, &pp, buf, j->exe);
    t.ClientId.UniqueThread = (HANDLE)(uintptr_t)j->tid;
    cur_teb = &t;
    applied = QOS_CLASS_USER_INTERACTIVE;   /* start_thread's ios_eco_apply_self */
    ios_thread_qos_start(j->tid);
    return (void *)(intptr_t)j->fn(j);
}
static int in_thread(const char *exe, unsigned tid, int (*fn)(struct job *))
{
    pthread_t th; void *r; struct job j = { exe, tid, fn };
    pthread_create(&th, NULL, run, &j); pthread_join(th, &r);
    return (int)(intptr_t)r;
}
static int game_present(struct job *j)
{
    CHECK(applied == QOS_CLASS_USER_INITIATED, "RDR2 thread did not start at user-initiated (%x)", applied);
    ios_thread_qos_frame_critical("Present");
    CHECK(applied == QOS_CLASS_USER_INTERACTIVE, "Present thread not lifted (%x)", applied);
    applied = QOS_CLASS_UNSPECIFIED;
    ios_thread_qos_frame_critical("Present");   /* once per thread */
    CHECK(applied == QOS_CLASS_UNSPECIFIED, "second mark re-applied");
    ios_eco_apply_self();                       /* a later wait re-applies: stays interactive */
    CHECK(applied == QOS_CLASS_USER_INTERACTIVE, "lifted thread fell back (%x)", applied);
    ios_thread_qos_exit(j->tid);
    return 0;
}
static int game_worker(struct job *j)
{
    CHECK(applied == QOS_CLASS_USER_INITIATED, "worker not at user-initiated (%x)", applied);
    ios_thread_qos_win_priority(GetCurrentThread(), 0);
    ios_thread_qos_win_priority(GetCurrentThread(), 1);
    ios_eco_apply_self();
    CHECK(applied == QOS_CLASS_USER_INITIATED, "NORMAL/ABOVE_NORMAL lifted a worker (%x)", applied);
    ios_thread_qos_win_priority(GetCurrentThread(), -2);
    ios_eco_apply_self();
    CHECK(applied == QOS_CLASS_USER_INITIATED, "LOWEST changed a worker (%x)", applied);
    ios_thread_qos_win_priority(GetCurrentThread(), 2);
    CHECK(applied == QOS_CLASS_USER_INTERACTIVE, "HIGHEST did not lift (%x)", applied);
    ios_thread_qos_win_priority((HANDLE)(uintptr_t)0x6000, 15);   /* TIME_CRITICAL on tid 0x600, not started yet */
    ios_thread_qos_exit(j->tid);
    return 0;
}
static int game_later(struct job *j)
{
    (void)j;
    CHECK(applied == QOS_CLASS_USER_INTERACTIVE, "thread raised before it started did not start interactive (%x)", applied);
    return 0;
}
static int helper(struct job *j)
{
    CHECK(applied == QOS_CLASS_UTILITY, "Social Club helper not at utility (%x)", applied);
    ios_thread_qos_frame_critical("audio render client");
    ios_eco_apply_self();
    CHECK(applied == QOS_CLASS_UTILITY, "frame-critical lifted a utility process (%x)", applied);
    ios_thread_qos_win_priority(GetCurrentThread(), 15);
    ios_eco_apply_self();
    CHECK(applied == QOS_CLASS_UTILITY, "priority lifted a utility process (%x)", applied);
    ios_thread_qos_exit(j->tid);
    return 0;
}
static int other(struct job *j)
{
    CHECK(applied == QOS_CLASS_USER_INTERACTIVE, "process with no exe rule changed at start (%x)", applied);
    ios_thread_qos_frame_critical("Present");
    ios_thread_qos_win_priority(GetCurrentThread(), 2);
    CHECK(!tq_lookup(j->tid, NULL), "process with no exe rule got an entry");
    return 0;
}
int main(int argc, char **argv)
{
    if (argc > 1 && !strcmp(argv[1], "off")) {
        in_thread("C:\\Games\\RDR2.exe", 0x700, other);
        if (fails) return 1;
        puts("ok");
        return 0;
    }
    in_thread("C:\\Program Files\\Rockstar Games\\Red Dead Redemption 2\\RDR2.exe", 0x500, game_present);
    in_thread("C:\\Games\\rdr2.EXE", 0x554, game_worker);
    in_thread("C:\\Games\\RDR2.exe", 0x600, game_later);
    in_thread("C:\\Program Files\\Rockstar Games\\Social Club\\SocialClubHelper.exe", 0x270, helper);
    in_thread("C:\\Steam\\steam.exe", 0x100, other);
    if (fails) return 1;
    puts("ok");
    return 0;
}
"""

with tempfile.TemporaryDirectory() as d:
    c = Path(d) / "t.c"
    exe = Path(d) / "t"
    c.write_text(harness)
    subprocess.run(["cc", "-std=gnu11", "-O1", "-Wall", "-Wno-unused-function", "-Wno-unused-variable",
                    "-Wno-int-to-pointer-cast", "-fsanitize=address,undefined", "-o", str(exe), str(c)], check=True)
    env = dict(os.environ, MADEIRA_THREAD_QOS="exe:SocialClubHelper.exe=utility;Chrome_*=background;exe:RDR2.exe=initiated")
    r = subprocess.run([str(exe)], capture_output=True, text=True, env=env)
    print(r.stdout, end="")
    if r.returncode or r.stdout.strip() != "ok":
        print(r.stderr[-2000:])
        raise SystemExit("check-thread-qos-critical: FAILED")
    env.pop("MADEIRA_THREAD_QOS")
    r = subprocess.run([str(exe), "off"], capture_output=True, text=True, env=env)
    print(r.stdout, end="")
    if r.returncode or r.stdout.strip() != "ok":
        print(r.stderr[-2000:])
        raise SystemExit("check-thread-qos-critical: FAILED (switch off)")
print("PASS: initiated exe rule; frame-critical and HIGHEST+ lift to user-interactive (once, before start too); "
      "utility process and no-rule process untouched; switch off untouched; wiring in server/audio/winemetal/NtSetInformationThread")
