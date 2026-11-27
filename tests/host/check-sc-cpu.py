#!/usr/bin/env python3
"""Social Club helper CPU during a game: sc-lite switches, the wait floor, the futex
bucket back-off and the spin sampler; no Wine runs.

RDR2 logs 2026-11-26 20:11 and 2026-11-27 00:19 (iPhone 18 Pro): with the helper's
Chrome_* / ThreadPool* threads at background QoS and its other threads above, those
others (HangWatcher, NetworkService, CacheThread_BlockFile, wine_threadpool_waitqueue)
ran ~1 core each at ~6 instructions per cycle for 40 s and 6+ minutes, and the hidden
launcher page kept producing off-screen frames (~1 core).

Compiles the production code and checks:
  - process_ios.c sc_lite_cmdline (env.MADEIRA_SC_CEF_LITE): EnableHangWatcher goes into
    the last --disable-features= list once, anti-throttling switches are removed (also
    with a value), background-network / raster switches and --off-screen-frame-rate are
    added once, a too small buffer fails;
  - process_ios.c ios_wait_floor (env.MADEIRA_WAIT_FLOOR / _AFTER) with a model TEB, clock
    and process table: only the listed image's relative waits of 1 ms..floor are raised;
    polls, infinite, absolute and sub-millisecond waits are not; with _AFTER nothing changes
    before the game starts or before its delay, and the floor goes off once the game is gone;
  - wine/dlls/ntdll/sync.c spin_lock (the shared futex bucket lock): uncontended it neither
    yields nor sleeps and stores the caller's tid; held, it spins 127 times, yields 32 times,
    sleeps 64 x 50 us then 0.5 ms (never 1 ms, which the floor would stretch) and reports the
    holder once at 200 sleeps;
and the call sites textually (six wait entry points in the unix sync.c, NtCreateUserProcess,
the [xp-t] loop's MADEIRA_SPIN_PC gate, the RDR2 profile).
Needs python3 and a C compiler (AddressSanitizer/UBSan).
"""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
proc = (root / 'build/ntdll-unix/process_ios.c').read_text()
unix_sync = (root / 'wine/dlls/ntdll/unix/sync.c').read_text()
pe_sync = (root / 'wine/dlls/ntdll/sync.c').read_text()
server = (root / 'build/ntdll-unix/server_ios.c').read_text()
library = (root / 'app/Madeira/Library.swift').read_text()


def function(source, signature):
    start = source.index(signature)
    return source[start:source.index('\n}', start) + 2] + '\n'


def block(source, start_marker, end_marker):
    start = source.index(start_marker)
    return source[start:source.index(end_marker, start)]


# --- call sites ---------------------------------------------------------------------
for fn in ('NTSTATUS WINAPI NtWaitForMultipleObjects(', 'NTSTATUS WINAPI NtWaitForSingleObject(',
           'NTSTATUS WINAPI NtDelayExecution(', 'NTSTATUS WINAPI NtRemoveIoCompletion(',
           'NTSTATUS WINAPI NtRemoveIoCompletionEx(', 'NTSTATUS WINAPI NtWaitForAlertByThreadId('):
    body = function(unix_sync, fn)
    assert 'ios_wait_floor( timeout, &ios_floor_store );' in body, fn
    call = body.index('ios_wait_floor(')
    for later in ('server_wait', 'madeira_fast_wait', 'inproc_wait', 'select( 0', 'SERVER_START_REQ'):
        if later in body:
            assert call < body.index(later), (fn, later)
assert unix_sync.count('ios_wait_floor( timeout, &ios_floor_store );') == 6
print('PASS: the six wait entry points apply ios_wait_floor before any wait or server request')

create = function(proc, 'NTSTATUS WINAPI NtCreateUserProcess(')
lite_at = create.index('sc_lite_cmdline( nbuf, o, fps, lbuf, lcap, &lhow )')
assert create.index('o = sc_browser_cmdline( params->CommandLine.Buffer') < lite_at
assert 'params->CommandLine.Buffer = nbuf;' in create[lite_at:lite_at + 3000], 'the lite buffer is what the child gets'
assert 'getenv( "MADEIRA_SC_CEF_LITE" )' in create and "lite[0] == '1'" in create
assert create.index('if (!success)') < create.index('ios_wait_floor_note_process(') < create.index('status = STATUS_SUCCESS;\n\ndone:')
print('PASS: NtCreateUserProcess applies sc-lite after the browser rewrite and arms the floor only after a successful start')

xp = server[server.index('/* madeira-bcd spin-pc: see ios_spin_pc_sample */'):][:600]
assert 'getenv( "MADEIRA_SPIN_PC" )' in xp and "e[0] == '1'" in xp
assert 'pt + et >= 0.3 * dt_ms' in xp and '>= 4.0' in xp
sample = function(server, 'static void ios_spin_pc_sample(')
assert 'printed >= 96' in sample and sample.count('thread_resume( port );') == 2
print('PASS: [spin-pc] is opt-in (MADEIRA_SPIN_PC=1), only for >= 30 % of a core at >= 4 IPC, capped, and always resumes')

rdr2 = library[library.index('case "rdr2.exe", "playrdr2.exe":'):]
rdr2 = rdr2[:rdr2.index('case ', 40)]
qos = rdr2[rdr2.index('env.MADEIRA_THREAD_QOS = '):]
qos = qos[:qos.index('\n')]
assert 'exe:SocialClubHelper.exe=utility' in qos, qos
for line in ('env.MADEIRA_SC_CEF_LITE = 1', 'env.MADEIRA_WAIT_FLOOR = SocialClubHelper.exe=50',
             'env.MADEIRA_WAIT_FLOOR_AFTER = RDR2.exe:180', 'env.MADEIRA_SPIN_PC = 1'):
    assert line in rdr2, line
assert not any(r in qos for r in ('Chrome_*', 'Cr*', 'ThreadPool*', 'Compositor*')), 'one QoS class for the whole helper'
print('PASS: the RDR2 profile runs the helper at one class and turns on sc-lite, the floor and [spin-pc]')

# --- process_ios.c under test ---------------------------------------------------------
lite_src = block(proc, 'enum { SC_LITE_HANG = 1', '/* madeira-bcd wait-floor (2026-11-27)')
wf_src = block(proc, '#define IOS_WF_MAX 8', '#endif\n\n/**********************************************************************\n *           NtCreateUserProcess')
helpers = function(proc, 'static int sc_switch_end(') + function(proc, 'static int ios_image_name_is(')

harness = r'''
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <pthread.h>
#include <time.h>
#include <unistd.h>   /* usleep: the sc-park watchdog (process_ios.c) */
typedef uint16_t WCHAR;
typedef unsigned int NTSTATUS, ULONG;
typedef long long LONGLONG;
typedef void *HANDLE;
typedef union { LONGLONG QuadPart; } LARGE_INTEGER;
typedef struct { unsigned short Length, MaximumLength; WCHAR *Buffer; } UNICODE_STRING;
typedef struct { UNICODE_STRING ImagePathName; } RTL_USER_PROCESS_PARAMETERS;
typedef struct { RTL_USER_PROCESS_PARAMETERS *ProcessParameters; } PEB;
typedef struct { int dummy; } OBJECT_ATTRIBUTES;
typedef struct { HANDLE UniqueProcess, UniqueThread; } CLIENT_ID;
typedef struct { PEB *Peb; CLIENT_ID ClientId; } TEB;   /* ClientId: sc-park (process_ios.c) */
#define HandleToULong(h) ((ULONG)(uintptr_t)(h))
volatile uint64_t ios_xp_pe_block, ios_xp_pe_len;   /* server_ios.c: the sc-park watchdog reads it */
typedef struct { NTSTATUS ExitStatus; } PROCESS_BASIC_INFORMATION;
#define STATUS_PENDING 0x103
#define STATUS_INVALID_CID 0xc000000b
#define STATUS_PROCESS_IS_TERMINATING 0xc000010a
#define PROCESS_QUERY_LIMITED_INFORMATION 0x1000
#define ProcessBasicInformation 0
#define InitializeObjectAttributes(a, b, c, d, e) ((a)->dummy = 0)
#define ULongToHandle(x) ((HANDLE)(uintptr_t)(x))
#define FAIL(...) do { fprintf(stderr, __VA_ARGS__); exit(1); } while (0)
static int logged;
#define dprintf(fd, ...) (logged++, fprintf( stderr, __VA_ARGS__ ))
static const char *debugstr_us( const UNICODE_STRING *s ) { (void)s; return "image"; }
static TEB teb; static PEB peb; static RTL_USER_PROCESS_PARAMETERS pp; static WCHAR image_buf[256];
static TEB *NtCurrentTeb( void ) { return &teb; }
static long long fake_ns = 1000000000ll;
#define clock_gettime(c, ts) ((ts)->tv_sec = fake_ns / 1000000000ll, (ts)->tv_nsec = fake_ns % 1000000000ll, 0)
static unsigned int live_pid; static int opens;
static NTSTATUS NtOpenProcess( HANDLE *h, ULONG a, OBJECT_ATTRIBUTES *o, CLIENT_ID *c )
{
    (void)a; (void)o; opens++;
    if ((uintptr_t)c->UniqueProcess != live_pid) return STATUS_INVALID_CID;
    *h = (HANDLE)1; return 0;
}
static NTSTATUS NtQueryInformationProcess( HANDLE h, int c, void *i, ULONG l, ULONG *r )
{ (void)h; (void)c; (void)l; (void)r; ((PROCESS_BASIC_INFORMATION *)i)->ExitStatus = STATUS_PENDING; return 0; }
static NTSTATUS NtClose( HANDLE h ) { (void)h; return 0; }
volatile int ios_tq_after_pending;   /* thread_ios.c MADEIRA_THREAD_QOS_EXE_AFTER */
static int tq_polls;
void ios_thread_qos_after_poll( void ) { tq_polls++; }
''' + helpers + lite_src + wf_src + r'''
static WCHAR *w( const char *s, int *len )
{
    static WCHAR buf[4][4096];
    static int k;
    WCHAR *o = buf[k++ & 3];
    int n = 0;
    for (; s[n]; n++) o[n] = (unsigned char)s[n];
    o[n] = 0;
    *len = n;
    return o;
}
static const char *a( const WCHAR *s, int len )
{
    static char buf[4096];
    int i;
    for (i = 0; i < len; i++) buf[i] = (char)s[i];
    buf[len] = 0;
    return buf;
}
static int count( const char *hay, const char *needle )
{
    int n = 0; const char *p = hay;
    while ((p = strstr( p, needle ))) { n++; p += strlen( needle ); }
    return n;
}
static const char *lite( const char *in, int fps, int *how )
{
    static WCHAR out[4096];
    int len, o; WCHAR *cl = w( in, &len );
    o = sc_lite_cmdline( cl, len, fps, out, 4096, how );
    if (o < 0) FAIL("sc_lite_cmdline failed for %s\n", in);
    if (out[o]) FAIL("not terminated\n");
    return a( out, o );
}
static void set_image( const char *path )
{
    int n = 0;
    for (; path[n]; n++) image_buf[n] = (unsigned char)path[n];
    pp.ImagePathName.Buffer = image_buf; pp.ImagePathName.Length = n * 2;
    peb.ProcessParameters = &pp; teb.Peb = &peb;
    ios_wf_thread_ms = -1;
}
static LONGLONG floored( LONGLONG t )
{
    LARGE_INTEGER in, store; const LARGE_INTEGER *r;
    in.QuadPart = t; store.QuadPart = 12345;
    r = ios_wait_floor( &in, &store );
    if (r != &in && r != &store) FAIL("foreign pointer\n");
    return r->QuadPart;
}

/* MADEIRA_THREAD_QOS_EXE_AFTER: the arming accessors, and the start recorded with no floor on */
static int qos_after( int on )
{
    int len, nlen; char name[64]; long long d = -1;
    WCHAR *img = w( "C:\\Games\\RDR2.exe", &nlen );
    unsetenv( "MADEIRA_WAIT_FLOOR" );
    setenv( "MADEIRA_WAIT_FLOOR_AFTER", "RDR2.exe:180", 1 );
    if (on) setenv( "MADEIRA_THREAD_QOS_EXE_AFTER", "SocialClubHelper.exe;RockstarService.exe", 1 );
    else unsetenv( "MADEIRA_THREAD_QOS_EXE_AFTER" );
    set_image( "C:\\Program Files\\Rockstar Games\\Social Club\\SocialClubHelper.exe" );
    if (!ios_wf_arming_game( name, sizeof(name), &d ) || strcmp( name, "rdr2.exe" ) || d != 180000000000ll)
        FAIL("ios_wf_arming_game: %s %lld\n", name, d);
    if (ios_wf_state != 0 || ios_wf_track_game != on) FAIL("state %d track %d\n", ios_wf_state, ios_wf_track_game);
    if (ios_wf_game_armed()) FAIL("armed before the game started\n");
    ios_wait_floor_note_process( w( "C:\\x\\PlayRDR2.exe", &len ), len, 0x40 );
    live_pid = 0x48c;
    ios_wait_floor_note_process( img, nlen, 0x48c );
    if (!on)
    {
        if (ios_wf_started_ns || ios_wf_game_armed()) FAIL("off: the start was recorded with no floor on\n");
        ios_tq_after_pending = 0;
        if (floored( -10000 ) != -10000 || tq_polls) FAIL("off: wait changed or polled (%d)\n", tq_polls);
        puts("PASS: MADEIRA_THREAD_QOS_EXE_AFTER unset: no floor, no start recorded, no arming poll");
        return 0;
    }
    if (!ios_wf_started_ns) FAIL("the game's start not recorded\n");
    fake_ns += 179000000000ll;
    if (ios_wf_game_armed()) FAIL("armed before 180 s\n");
    ios_tq_after_pending = 1;
    if (floored( -10000 ) != -10000 || floored( -1000 ) != -1000 || tq_polls != 1)
        FAIL("pending: wait changed or poll count %d (want 1: sub-ms waits do not poll)\n", tq_polls);
    ios_tq_after_pending = 0;
    if (floored( -10000 ) != -10000 || tq_polls != 1) FAIL("not pending but polled\n");
    fake_ns += 2000000000ll;
    if (!ios_wf_game_armed()) FAIL("not armed after 180 s\n");
    live_pid = 0;                        /* the game exits: the accessor says so (the latch is thread_ios.c's) */
    fake_ns += 6000000000ll;
    if (ios_wf_game_armed()) FAIL("armed after the game left\n");
    puts("PASS: MADEIRA_THREAD_QOS_EXE_AFTER: the game's start is recorded with no floor on, armed from 180 s while it runs, "
         "timed waits poll only while pending");
    return 0;
}

int main( int argc, char **argv )
{
    const char *base = "\"C:\\Program Files\\Rockstar Games\\Social Club\\SocialClubHelper.exe\"  --lang=en "
                       "--off-screen-rendering-enabled --disable-features=PartitionAllocBackupRefPtr --single-process";
    const char *r; int how, len, i;
    WCHAR small[64];

    if (argc > 1) return qos_after( !strcmp( argv[1], "qos-after" ) );
    r = lite( base, 30, &how );
    if (!strstr( r, "--disable-features=EnableHangWatcher,PartitionAllocBackupRefPtr " )) FAIL("hang: %s\n", r);
    if (count( r, "EnableHangWatcher" ) != 1) FAIL("hang twice: %s\n", r);
    if (!strstr( r, " --disable-background-networking" ) || !strstr( r, " --disable-component-update" ) ||
        !strstr( r, " --disable-domain-reliability" ) || !strstr( r, " --num-raster-threads=1" ) ||
        !strstr( r, " --disable-smooth-scrolling" ) || !strstr( r, " --off-screen-frame-rate=30" )) FAIL("adds: %s\n", r);
    if (how != (SC_LITE_HANG | SC_LITE_ADDED | SC_LITE_FPS)) FAIL("how %d\n", how);
    if (strncmp( r, base, 60 )) FAIL("prefix changed: %s\n", r);
    /* idempotent: a second pass adds nothing */
    {
        char once[4096]; strcpy( once, r );
        r = lite( once, 30, &how );
        if (strcmp( r, once ) || how) FAIL("second pass changed it (how %d): %s\n", how, r);
    }
    puts("PASS: sc-lite: EnableHangWatcher first in the list, five switches and 30 fps added once, a second pass is a no-op");

    r = lite( "x.exe --disable-renderer-backgrounding --a --disable-background-timer-throttling=1 "
              "--disable-backgrounding-occluded-windows --disable-features=X,EnableHangWatcher --off-screen-frame-rate=60 "
              "--num-raster-threads=4", 30, &how );
    if (strstr( r, "backgrounding" ) || strstr( r, "throttling" )) FAIL("strip: %s\n", r);
    if (!strstr( r, "x.exe --a --disable-features=X,EnableHangWatcher --off-screen-frame-rate=60 --num-raster-threads=4" ))
        FAIL("strip damaged the rest: %s\n", r);
    if (count( r, "EnableHangWatcher" ) != 1 || count( r, "--off-screen-frame-rate" ) != 1 ||
        count( r, "--num-raster-threads" ) != 1) FAIL("duplicates: %s\n", r);
    if (!(how & SC_LITE_STRIPPED) || (how & (SC_LITE_HANG | SC_LITE_FPS))) FAIL("how %d\n", how);
    r = lite( "x.exe --disable-features=EnableHangWatcherX --disable-renderer-backgroundingX", 0, &how );
    if (count( r, "EnableHangWatcher," ) != 1 || !strstr( r, "--disable-renderer-backgroundingX" ) ||
        strstr( r, "--off-screen-frame-rate" )) FAIL("look-alikes / fps 0: %s\n", r);
    if (sc_lite_cmdline( w( base, &len ), len, 30, small, 64, &how ) != -1) FAIL("small buffer accepted\n");
    puts("PASS: sc-lite removes anti-throttling switches (also with a value), keeps look-alikes and the app's own values");

    /* --- the wait floor --- */
    if (ios_wf_parse( "SocialClubHelper.exe=50; other.exe = 30 ;bad;=4;x.exe=0;y.exe=5000" ) != 3 ||
        strcmp( ios_wf_rules[0].name, "socialclubhelper.exe" ) || ios_wf_rules[0].ms != 50 ||
        strcmp( ios_wf_rules[1].name, "other.exe" ) || ios_wf_rules[1].ms != 30 || ios_wf_rules[2].ms != 1000)
        FAIL("parse\n");
    {
        char name[64]; long long d;
        if (!ios_wf_parse_after( " RDR2.exe:120", name, sizeof(name), &d ) || strcmp( name, "rdr2.exe" ) ||
            d != 120000000000ll) FAIL("after\n");
        if (!ios_wf_parse_after( "Game.exe", name, sizeof(name), &d ) || d) FAIL("after no delay\n");
        if (ios_wf_parse_after( "", name, sizeof(name), &d )) FAIL("after empty\n");
    }
    puts("PASS: MADEIRA_WAIT_FLOOR / _AFTER parse (names lower-cased, bad entries skipped, 1 s cap)");

    setenv( "MADEIRA_WAIT_FLOOR", "SocialClubHelper.exe=50", 1 );
    setenv( "MADEIRA_WAIT_FLOOR_AFTER", "RDR2.exe:120", 1 );
    set_image( "C:\\Program Files\\Rockstar Games\\Social Club\\SocialClubHelper.exe" );
    if (floored( -10000 ) != -10000) FAIL("on before the game started\n");
    {
        int nlen; WCHAR *img = w( "C:\\Games\\RDR2.exe", &nlen );
        ios_wait_floor_note_process( w( "C:\\x\\PlayRDR2.exe", &len ), len, 0x40 );
        if (ios_wf_started_ns) FAIL("armed by another image\n");
        live_pid = 0x48c;
        ios_wait_floor_note_process( img, nlen, 0x48c );
        if (!ios_wf_started_ns) FAIL("not armed\n");
    }
    fake_ns += 119000000000ll;
    if (floored( -10000 ) != -10000) FAIL("on before the delay\n");
    fake_ns += 2000000000ll;
    if (floored( -10000 ) != -500000 || floored( -160000 ) != -500000 || floored( -499999 ) != -500000)
        FAIL("short waits not raised\n");
    if (floored( -500000 ) != -500000 || floored( -600000 ) != -600000) FAIL("long waits changed\n");
    if (floored( 0 ) != 0 || floored( -9999 ) != -9999 || floored( -5000 ) != -5000 || floored( 133000000000000000ll ) != 133000000000000000ll)
        FAIL("poll / sub-ms / absolute changed\n");
    {
        const LARGE_INTEGER *n = ios_wait_floor( NULL, NULL );
        if (n) FAIL("infinite changed\n");
    }
    set_image( "C:\\Games\\RDR2.exe" );
    if (floored( -10000 ) != -10000) FAIL("the game's own waits raised\n");
    set_image( "C:\\Program Files\\Rockstar Games\\Social Club\\SocialClubHelper.exe" );
    if (ios_wf_raised < 3) FAIL("not counted\n");
    live_pid = 0;                        /* the game exits */
    fake_ns += 6000000000ll;
    if (floored( -10000 ) != -10000 || ios_wf_started_ns) FAIL("still on after the game left\n");
    for (i = 0; i < 3; i++) fake_ns += 6000000000ll, floored( -10000 );
    if (opens > 2) FAIL("process checks while off: %d\n", opens);
    if (tq_polls || ios_wf_track_game) FAIL("thread-qos arming polled with MADEIRA_THREAD_QOS_EXE_AFTER unset\n");
    puts("PASS: wait floor: only the helper's 1 ms..50 ms relative waits are raised, from 120 s after RDR2.exe starts until it is gone");
    return 0;
}
'''

# --- PE ntdll spin_lock under test ----------------------------------------------------
pe_src = block(pe_sync, 'static LONG futex_spin_reports;', 'static void spin_unlock( LONG *lock )')
pe_harness = r'''
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
typedef int LONG; typedef unsigned int ULONG, NTSTATUS, DWORD; typedef uintptr_t ULONG_PTR; typedef void *HANDLE;
typedef union { long long QuadPart; } LARGE_INTEGER;
typedef struct { HANDLE UniqueProcess, UniqueThread; } CLIENT_ID;
typedef struct { int dummy; } OBJECT_ATTRIBUTES;
typedef struct { NTSTATUS ExitStatus; void *TebBaseAddress; CLIENT_ID ClientId; } THREAD_BASIC_INFORMATION;
struct list { struct list *next, *prev; };
struct futex_queue { struct list queue; LONG lock; };
static struct futex_queue table[256];
static struct futex_queue *ios_get_futex_table( void ) { return table; }
#define FALSE 0
#define ThreadBasicInformation 0
#define THREAD_QUERY_LIMITED_INFORMATION 0x800
#define STATUS_INVALID_CID 0xc000000b
#define STATUS_PENDING 0x103
#define InitializeObjectAttributes(a, b, c, d, e) ((a)->dummy = 0)
#define ULongToHandle(x) ((HANDLE)(uintptr_t)(x))
#define InterlockedCompareExchange(p, x, c) __sync_val_compare_and_swap( (p), (c), (x) )
#define InterlockedIncrement(p) __sync_add_and_fetch( (p), 1 )
#define FAIL(...) do { fprintf(stderr, __VA_ARGS__); exit(1); } while (0)
static int pauses, yields, sleeps50, sleeps500, sleeps_other, reports, release_after = -1, opens;
static LONG *held;
static DWORD self_tid = 0x260;
static DWORD GetCurrentThreadId( void ) { return self_tid; }
#define YieldProcessor() (pauses++)
static NTSTATUS NtYieldExecution( void ) { yields++; return 0; }
static NTSTATUS NtDelayExecution( int alertable, const LARGE_INTEGER *t )
{
    (void)alertable;
    if (t->QuadPart == -500) sleeps50++; else if (t->QuadPart == -5000) sleeps500++; else sleeps_other++;
    if (release_after > 0 && sleeps50 + sleeps500 == release_after) *held = 0;   /* the holder ran */
    return 0;
}
static NTSTATUS NtOpenThread( HANDLE *h, ULONG a, OBJECT_ATTRIBUTES *o, CLIENT_ID *c )
{ (void)h; (void)a; (void)o; (void)c; opens++; return STATUS_INVALID_CID; }
static NTSTATUS NtQueryInformationThread( HANDLE h, int c, void *i, ULONG l, ULONG *r )
{ (void)h; (void)c; (void)i; (void)l; (void)r; return 1; }
static NTSTATUS NtClose( HANDLE h ) { (void)h; return 0; }
#define ERR(...) (reports++, fprintf( stderr, __VA_ARGS__ ))
''' + pe_src + r'''
int main( void )
{
    held = &table[7].lock;
    spin_lock( held );
    if (*held != 0x260 || pauses || yields || sleeps50 || sleeps500) FAIL("uncontended path changed\n");
    *held = 0;
    puts("PASS: uncontended: one CAS, the lock holds the caller's tid, no yield or sleep");

    *held = 0x25c;                 /* a holder that does not run for 1000 back-off sleeps */
    release_after = 1000;
    spin_lock( held );
    if (*held != 0x260) FAIL("not acquired after the holder left\n");
    if (pauses != 127 || yields != 32 || sleeps50 != 64 || sleeps500 != 936 || sleeps_other)
        FAIL("back-off: pauses %d yields %d 50us %d 0.5ms %d other %d\n", pauses, yields, sleeps50, sleeps500, sleeps_other);
    if (reports != 1 || opens != 1) FAIL("reports %d opens %d\n", reports, opens);
    puts("PASS: held: 127 pauses, 32 yields, 64 x 50 us then 0.5 ms sleeps (none of 1 ms), one [futex-spin] report naming the holder");
    return 0;
}
'''

with tempfile.TemporaryDirectory() as tmp:
    for name, src in (('sc_cpu', harness), ('futex_spin', pe_harness)):
        c = Path(tmp) / (name + '.c')
        exe = Path(tmp) / name
        c.write_text(src)
        subprocess.run(['cc', '-std=gnu11', '-O1', '-Wall', '-Wno-unused-function', '-Wno-unused-variable', '-Wno-format',
                        '-fsanitize=address,undefined', '-fno-sanitize-recover=all', str(c), '-o', str(exe), '-lpthread'],
                       check=True)
        for args in ([[]] if name != 'sc_cpu' else [[], ['qos-after'], ['qos-after-off']]):
            out = subprocess.run([str(exe)] + args, capture_output=True, text=True)
            print(out.stdout, end='')
            if out.returncode:
                print(out.stderr)
                raise SystemExit('FAIL: %s %s exited %d' % (name, ' '.join(args), out.returncode))
print('ALL PASS')
