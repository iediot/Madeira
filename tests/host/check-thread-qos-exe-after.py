#!/usr/bin/env python3
"""MADEIRA_THREAD_QOS_EXE_AFTER (build/ntdll-unix/thread_ios.c); no Wine runs.

Compiles the production thread-QoS section against a fake TEB/PEB, a recording
pthread_set_qos_class_self_np, a fake clock and a controllable arming
(process_ios.c's ios_wf_arming_game / ios_wf_game_armed are stubbed), one pthread per
guest thread, all alive across the arming, and checks:
  - off (switch unset): exe rules apply at thread start exactly as before, no poll
    pending, the arming accessors are never asked;
  - deferral: before the arming, threads of the listed processes start and stay at
    the default class (no entry stored), also after naming themselves with a name no
    rule matches; name rules still apply; unlisted exe rules (RDR2.exe=initiated) are
    untouched; one "deferred until" line;
  - arming: polled at most every 250 ms, latches once, bumps ios_eco_gen, clears the
    pending flag, one "armed:" line;
  - pickup: at their next re-apply (ios_eco_apply_self, as a wait does) the listed
    processes' threads take their exe rule; a name rule still wins; a thread started
    after the arming takes it at start;
  - ignored: no MADEIRA_WAIT_FLOOR_AFTER game, or no exe: rule for any listed name,
    leaves the rules as they are today;
and the wiring in process_ios.c (the poll in ios_wait_floor, the start recorded for the
switch, the accessors on ios_wf_game_active_after), that the RDR2 profile does not set
the switch, the morning checklist line and the settings catalog entry.
"""
from pathlib import Path
import os, subprocess, tempfile, json

root = Path(__file__).resolve().parents[2]
src = (root / "build/ntdll-unix/thread_ios.c").read_text()
a = src.index("#define TQ_MAX_RULES")
b = src.index(" *           start_thread", a)
b = src.rindex("/****", a, b)
section = src[a:b]
assert "void ios_thread_qos_after_poll(" in section

# --- wiring --------------------------------------------------------------------------
proc = (root / "build/ntdll-unix/process_ios.c").read_text()
wf = proc[proc.index("const LARGE_INTEGER *ios_wait_floor("):]
wf = wf[:wf.index("\n}\n")]
poll = wf.index("if (__builtin_expect( ios_tq_after_pending, 0 )) ios_thread_qos_after_poll();")
assert wf.index("timeout->QuadPart > -10000) return timeout;") < poll < wf.index("if (ios_wf_state <= 0) return timeout;")
note = proc[proc.index("static void ios_wait_floor_note_process("):]
note = note[:note.index("\n}\n")]
assert "if ((ios_wf_state <= 0 && !ios_wf_track_game) || !ios_wf_after_name[0]) return;" in note
armed = proc[proc.index("int ios_wf_game_armed(void)"):]
armed = armed[:armed.index("\n}\n")]
assert "ios_wf_game_active_after( ios_wf_now_ns(), ios_wf_after_ns )" in armed
init = proc[proc.index("static void ios_wf_init(void)"):]
init = init[:init.index("\n}\n")]
assert 'getenv( "MADEIRA_THREAD_QOS_EXE_AFTER" )' in init
library = (root / "app/Madeira/Library.swift").read_text()
assert "MADEIRA_THREAD_QOS_EXE_AFTER" not in library, "the switch must stay out of the profiles (owner A/B)"
checklist = (root / "docs/RDR2_MORNING_CHECKLIST.md").read_text()
assert "## A/B tries for the morning" in checklist
assert "env.MADEIRA_THREAD_QOS_EXE_AFTER = SocialClubHelper.exe;RockstarService.exe" in checklist
catalog_hits = [p for p in (root / "app/Madeira").rglob("*atalog*") if p.is_file() and
                "MADEIRA_THREAD_QOS_EXE_AFTER" in p.read_text(errors="ignore")]
assert catalog_hits, "settings catalog not regenerated (python3 build/tools/gen-config-catalog.py)"

harness = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <stdint.h>
#include <time.h>
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
static int wine_server_call(struct fake_req *r) { r->tid = (unsigned)(uintptr_t)r->handle >> 4; return 0; }
static __thread qos_class_t applied = QOS_CLASS_UNSPECIFIED;
static int fake_set_qos(qos_class_t c, int rel) { (void)rel; applied = c; return 0; }
#define pthread_set_qos_class_self_np fake_set_qos
volatile int ios_eco_gen = 1;
int madeira_guest_qos_self(qos_class_t cls, int rel);
void ios_eco_apply_self(void) { madeira_guest_qos_self(QOS_CLASS_USER_INTERACTIVE, 0); }   /* sync.c, eco off */
/* the log */
static pthread_mutex_t log_lock = PTHREAD_MUTEX_INITIALIZER;
static char logbuf[16384];
#define dprintf(fd, ...) do { char l_[512]; snprintf(l_, sizeof l_, __VA_ARGS__); pthread_mutex_lock(&log_lock); \
    strncat(logbuf, l_, sizeof(logbuf) - strlen(logbuf) - 1); pthread_mutex_unlock(&log_lock); } while (0)
/* the clock */
static volatile long long fake_ns = 5000000000ll;
static int fake_clock(struct timespec *ts) { ts->tv_sec = fake_ns / 1000000000ll; ts->tv_nsec = fake_ns % 1000000000ll; return 0; }
#define clock_gettime(c, ts) fake_clock(ts)
/* process_ios.c's arming */
static int have_game = 1, game_armed, arming_asked, armed_asked;
int ios_wf_arming_game(char *name, int size, long long *delay)
{ arming_asked++; if (!have_game) return 0; snprintf(name, size, "rdr2.exe"); *delay = 180000000000ll; return 1; }
int ios_wf_game_armed(void) { armed_asked++; return game_armed; }
""" + section + r"""
#undef clock_gettime
static int fails;
#define CHECK(c, ...) do { if (!(c)) { printf("FAIL " __VA_ARGS__); printf("\n"); fails++; } } while (0)
static int count(const char *needle) { int n = 0; const char *p = logbuf; while ((p = strstr(p, needle))) { n++; p += strlen(needle); } return n; }

/* a reusable barrier (no pthread_barrier on macOS) */
static pthread_mutex_t bl = PTHREAD_MUTEX_INITIALIZER; static pthread_cond_t bc = PTHREAD_COND_INITIALIZER;
static int bn, bwait, bgen;
static void barrier(void)
{
    pthread_mutex_lock(&bl);
    int g = bgen;
    if (++bwait == bn) { bwait = 0; bgen++; pthread_cond_broadcast(&bc); }
    else while (g == bgen) pthread_cond_wait(&bc, &bl);
    pthread_mutex_unlock(&bl);
}

enum { PRE, POST };
struct job { const char *exe; unsigned tid; void (*fn)(struct job *, int); pthread_t th; };
static void *run(void *arg)
{
    struct job *j = arg;
    TEB t; PEB p; RTL_USER_PROCESS_PARAMETERS pp; WCHAR buf[160];
    size_t i, n = strlen(j->exe);
    memset(&t, 0, sizeof t);
    for (i = 0; i < n; i++) buf[i] = (WCHAR)j->exe[i];
    pp.ImagePathName.Buffer = buf; pp.ImagePathName.Length = (USHORT)(n * 2);
    p.ProcessParameters = &pp; t.Peb = &p;
    t.ClientId.UniqueThread = (HANDLE)(uintptr_t)j->tid;
    cur_teb = &t;
    applied = QOS_CLASS_USER_INTERACTIVE;   /* start_thread's ios_eco_apply_self */
    ios_thread_qos_start(j->tid);
    j->fn(j, PRE);
    barrier();     /* main arms */
    barrier();
    ios_eco_apply_self();   /* IOS_ECO_POLL at the next wait: the generation moved */
    j->fn(j, POST);
    barrier();
    ios_thread_qos_exit(j->tid);
    return NULL;
}
static void start_job(struct job *j) { pthread_create(&j->th, NULL, run, j); }

static const char *mode;
static int deferring(void) { return !strcmp(mode, "on"); }
static const char *cn(qos_class_t c) { return tq_class_name(c); }

static void helper(struct job *j, int phase)   /* SocialClubHelper.exe, unnamed, then a name no rule matches */
{
    if (phase == PRE)
    {
        if (deferring()) {
            CHECK(applied == QOS_CLASS_USER_INTERACTIVE, "helper not at the default class before arming (%s)", cn(applied));
            CHECK(!tq_lookup(j->tid, NULL), "helper got an entry before arming");
            ios_thread_qos_named(GetCurrentThread(), "NetworkService");
            ios_eco_apply_self();
            CHECK(applied == QOS_CLASS_USER_INTERACTIVE && !tq_lookup(j->tid, NULL), "named helper left the default class before arming (%s)", cn(applied));
        } else {
            CHECK(applied == QOS_CLASS_UTILITY, "helper not at utility at start (%s)", cn(applied));
        }
        return;
    }
    CHECK(applied == QOS_CLASS_UTILITY, "helper not at utility after arming (%s)", cn(applied));
    ios_thread_qos_named(GetCurrentThread(), "HangWatcher");
    ios_eco_apply_self();
    CHECK(applied == QOS_CLASS_UTILITY, "helper renamed after arming left utility (%s)", cn(applied));
}
static void rockstar(struct job *j, int phase)   /* RockstarService.exe */
{
    qos_class_t want = (phase == PRE && deferring()) ? QOS_CLASS_USER_INTERACTIVE : QOS_CLASS_UTILITY;
    (void)j;
    ios_eco_apply_self();
    CHECK(applied == want, "RockstarService %s: %s, want %s", phase == PRE ? "before" : "after", cn(applied), cn(want));
}
static void chrome(struct job *j, int phase)   /* helper thread with a name rule: the name wins either way */
{
    (void)j;
    if (phase == PRE) ios_thread_qos_named(GetCurrentThread(), "Chrome_IOThread");
    ios_eco_apply_self();
    CHECK(applied == QOS_CLASS_BACKGROUND, "Chrome_* name rule lost (%s, %s)", cn(applied), phase == PRE ? "before" : "after");
}
static void game(struct job *j, int phase)   /* RDR2.exe: not listed, untouched */
{
    (void)j; (void)phase;
    ios_eco_apply_self();
    CHECK(applied == QOS_CLASS_USER_INITIATED, "RDR2 not at user-initiated (%s)", cn(applied));
}
static void other(struct job *j, int phase)   /* no exe rule */
{
    ios_eco_apply_self();
    CHECK(applied == QOS_CLASS_USER_INTERACTIVE && !tq_lookup(j->tid, NULL), "steam changed (%s)", cn(applied));
    (void)phase;
}
static void *late(void *arg)   /* a helper thread started after the arming */
{
    TEB t; PEB p; RTL_USER_PROCESS_PARAMETERS pp; WCHAR buf[64]; const char *exe = "C:\\SC\\SocialClubHelper.exe";
    size_t i, n = strlen(exe);
    (void)arg;
    memset(&t, 0, sizeof t);
    for (i = 0; i < n; i++) buf[i] = (WCHAR)exe[i];
    pp.ImagePathName.Buffer = buf; pp.ImagePathName.Length = (USHORT)(n * 2); p.ProcessParameters = &pp; t.Peb = &p;
    t.ClientId.UniqueThread = (HANDLE)(uintptr_t)0x2a0; cur_teb = &t;
    applied = QOS_CLASS_USER_INTERACTIVE;
    ios_thread_qos_start(0x2a0);
    CHECK(applied == QOS_CLASS_UTILITY, "helper thread started after arming not at utility (%s)", cn(applied));
    ios_thread_qos_exit(0x2a0);
    return NULL;
}

int main(int argc, char **argv)
{
    struct job jobs[] = {
        { "C:\\Program Files\\Rockstar Games\\Social Club\\SocialClubHelper.exe", 0x270, helper },
        { "C:\\Program Files\\Rockstar Games\\Launcher\\RockstarService.exe", 0x280, rockstar },
        { "C:\\Program Files\\Rockstar Games\\Social Club\\socialclubhelper.EXE", 0x274, chrome },
        { "C:\\Games\\RDR2.exe", 0x500, game },
        { "C:\\Steam\\steam.exe", 0x100, other },
    };
    int i, n = sizeof(jobs) / sizeof(jobs[0]), gen0;
    pthread_t lt;
    mode = argc > 1 ? argv[1] : "on";
    if (!strcmp(mode, "nogame")) have_game = 0;
    bn = n + 1;
    for (i = 0; i < n; i++) start_job(&jobs[i]);
    barrier();                       /* every thread did its PRE checks */

    /* the arming, from some thread's timed wait (ios_wait_floor polls while pending) */
    gen0 = ios_eco_gen;
    if (deferring())
    {
        CHECK(ios_tq_after_pending == 1, "no poll pending while deferred");
        ios_thread_qos_after_poll();                 /* game not armed yet */
        CHECK(armed_asked == 1 && ios_eco_gen == gen0 && !tq_after_armed, "armed too early");
        game_armed = 1;
        fake_ns += 100000000ll;
        ios_thread_qos_after_poll();                 /* within 250 ms: not asked */
        CHECK(armed_asked == 1 && !tq_after_armed, "poll not rate-limited (%d)", armed_asked);
        fake_ns += 200000000ll;
        ios_thread_qos_after_poll();
        CHECK(armed_asked == 2 && tq_after_armed && ios_eco_gen == gen0 + 1 && !ios_tq_after_pending,
              "not armed (asked %d, gen %d -> %d, pending %d)", armed_asked, gen0, ios_eco_gen, ios_tq_after_pending);
        fake_ns += 300000000ll;
        ios_thread_qos_after_poll();                 /* latched: no second bump */
        CHECK(ios_eco_gen == gen0 + 1, "second arming bumped again");
    }
    else
    {
        CHECK(!ios_tq_after_pending && !tq_after_on, "poll pending with the switch %s", mode);
        CHECK(!armed_asked, "arming asked with the switch %s", mode);
        ios_eco_gen++;                               /* some other re-apply (eco toggle): nothing moves */
    }
    barrier();
    barrier();                       /* POST checks */
    for (i = 0; i < n; i++) pthread_join(jobs[i].th, NULL);
    pthread_create(&lt, NULL, late, NULL); pthread_join(lt, NULL);

    if (!strcmp(mode, "on")) {
        CHECK(count("[thread-qos] exe rules for SocialClubHelper.exe;rockstarservice.exe deferred until rdr2.exe has run 180 s\n") == 1,
              "deferral line missing or repeated");
        CHECK(count("[thread-qos] armed: exe rules now apply to SocialClubHelper.exe;rockstarservice.exe\n") == 1, "armed line missing or repeated");
        CHECK(count("no exe:Nope.exe rule, ignored") == 1, "unknown name not reported");
        CHECK(count("(process rule, deferred) -> utility") >= 2, "pickup not logged per thread");
    } else {
        CHECK(!count("deferred until") && !count("armed:"), "deferral/arming logged with the switch %s", mode);
        if (!strcmp(mode, "off")) CHECK(!arming_asked, "accessor asked with the switch unset");
        if (!strcmp(mode, "nogame")) CHECK(count("MADEIRA_WAIT_FLOOR_AFTER names no game") == 1, "no-game line missing");
        if (!strcmp(mode, "norule")) CHECK(count("no exe:Nope.exe rule, ignored") == 1 && !arming_asked, "norule");
    }
    if (fails) { fputs(logbuf, stderr); return 1; }
    puts("ok");
    return 0;
}
"""

RULES = "exe:SocialClubHelper.exe=utility;exe:RockstarService.exe=utility;Chrome_*=background;exe:RDR2.exe=initiated"
runs = [
    ("on", "SocialClubHelper.exe; rockstarservice.exe ;Nope.exe;"),
    ("off", None),
    ("nogame", "SocialClubHelper.exe;RockstarService.exe"),
    ("norule", "Nope.exe"),
]
with tempfile.TemporaryDirectory() as d:
    c = Path(d) / "t.c"
    exe = Path(d) / "t"
    c.write_text(harness)
    subprocess.run(["cc", "-std=gnu11", "-O1", "-Wall", "-Wno-unused-function", "-Wno-unused-variable",
                    "-Wno-int-to-pointer-cast", "-fsanitize=address,undefined", "-o", str(exe), str(c), "-lpthread"],
                   check=True)
    for mode, after in runs:
        env = dict(os.environ, MADEIRA_THREAD_QOS=RULES)
        env.pop("MADEIRA_THREAD_QOS_EXE_AFTER", None)
        if after is not None:
            env["MADEIRA_THREAD_QOS_EXE_AFTER"] = after
        r = subprocess.run([str(exe), mode], capture_output=True, text=True, env=env)
        print(mode + ": " + r.stdout, end="")
        if r.returncode or r.stdout.strip() != "ok":
            print(r.stderr[-3000:])
            raise SystemExit("check-thread-qos-exe-after: FAILED (%s)" % mode)
print("PASS: off = exe rules at start as before; listed processes stay at the default class until the arming "
      "(name rules still apply), pick up their rule at the next re-apply, one deferral and one armed line; "
      "no game / no rule = ignored; wiring, profile, checklist, catalog")
