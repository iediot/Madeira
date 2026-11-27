#!/usr/bin/env python3
"""madeira_d3d12's opt-in present pacer (present-cap-fps / present-pace-mw); no Wine runs.

RDR2 under the iPhone 18 Pro's energy clamp (log 2026-11-27 01:05) alternated ~1 W windows,
6-10 s at ~1.5 W, one window with the cores mostly idle and a burst at 2.3-2.8 GHz. The
pacer holds the game's Present caller to a steady interval; it is off unless configured
and no profile turns it on.

Compiles mad_pace_present from madeira-d3d12/src/pe/madeira_d3d12.c against a fake clock:
  - off (no keys): no sleep, no log, no state;
  - present-cap-fps = 30 with 10 ms frames: intervals of 33.3 ms, also when every sleep
    overshoots by 0.3 ms (the deadline advances from the previous deadline);
  - frames slower than the cap: never a sleep;
  - after a 500 ms stall no catch-up burst (re-based, intervals stay >= the period);
  - present-pace-mw: windows above the budget lower the target ~10 % each, never below
    present-pace-min-fps; two windows under 85 % raise it 2 fps, never above the cap;
    a fixed cap logs the published power and ignores it;
  - DXGI_PRESENT_TEST never paces;
and textually: the unix probe (server_ios.c) publishes the window energy only into a block
long enough for it, seq last; the g_xp fields are appended after the mirrored layout; no
profile in Library.swift sets the pacer keys.
Needs python3 and a C compiler (AddressSanitizer/UBSan).
"""
from pathlib import Path
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
d3d = (root / 'madeira-d3d12/src/pe/madeira_d3d12.c').read_text()
server = (root / 'build/ntdll-unix/server_ios.c').read_text()
library = (root / 'app/Madeira/Library.swift').read_text()


def function(source, signature):
    start = source.index(signature)
    return source[start:source.index('\n}', start) + 2] + '\n'


# --- textual -----------------------------------------------------------------------
xp = d3d[d3d.index('static struct mad_xp {'):d3d.index('} g_xp;')]
fields = re.findall(r'LONG64 ([^;]+);', xp)
names = [n.strip() for f in fields for n in f.split(',')]
assert names[-3:] == ['pace_seq', 'pace_uj', 'pace_dt_us'], names
assert len(names) == 23, len(names)   # 20 mirrored in server_ios.c ios_xp_pe, then the three pacer fields
pe = server[server.index('struct ios_xp_pe { int64_t magic'):]
pe = pe[:pe.index('};')]
assert len(re.findall(r'\b\w+\b', pe.split('{', 1)[1].replace('int64_t', ''))) == 20
pub = server[server.index('/* madeira-bcd present-pace: this window'):][:1400]
assert 'blen >= sizeof(struct ios_xp_pe) + sizeof(struct ios_xp_pace_pub)' in pub
assert 'if (pub && __atomic_load_n( &pub->seq, __ATOMIC_ACQUIRE ) && dt_ms > 0)' in pub, 'nothing written unless the pacer asked'
assert 'InterlockedCompareExchange64(&g_xp.pace_seq, 1, 0);' in d3d
assert pub.index('&pub->uj') < pub.index('&pub->dt_us') < pub.index('__atomic_add_fetch( &pub->seq, 1, __ATOMIC_RELEASE )')
assert 'struct ios_xp_pace_pub { int64_t seq, uj, dt_us; };' in server
present = function(d3d, 'static HRESULT STDMETHODCALLTYPE swap_Present(IDXGISwapChain4 *T, UINT sync, UINT flags)')
assert 'if (!(flags & DXGI_PRESENT_TEST)) mad_pace_present();' in present
assert present.index('mad_pace_present();') < present.index('stamp = mad_tick();') < present.index('swap_Present_pool(T, sync, flags)')
# the owner turned on a fixed 30 fps cap for RDR2 (2026-11-27); nothing else sets the pacer keys
assert library.count('present-cap-fps = 30') == 1, 'RDR2 profile: present-cap-fps = 30'
for key in ('present-pace-mw', 'present-pace-min-fps'):
    assert key not in library, key
print('PASS: g_xp appends the pacer fields after the 20 mirrored ones; the probe writes only into long blocks whose pacer asked (seq != 0), seq last')
print('PASS: Present paces before its timing stamp, never for DXGI_PRESENT_TEST; only RDR2's profile sets present-cap-fps = 30')

# --- compiled ------------------------------------------------------------------------
start = d3d.index('static struct {\n    int on;                       /* -1 unread, 0 off, 1 on */')
code = d3d[start:]
code = code[:code.index('static HRESULT STDMETHODCALLTYPE swap_Present(IDXGISwapChain4 *T')]

harness = r'''
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <stdarg.h>
#include <math.h>
typedef long long LONG64; typedef int LONG; typedef unsigned char BOOLEAN; typedef unsigned int DWORD;
typedef union { LONG64 QuadPart; } LARGE_INTEGER;
typedef struct { void *p; } SRWLOCK;
#define WINAPI
#define FALSE 0
#define FAIL(...) do { fprintf(stderr, __VA_ARGS__); exit(1); } while (0)
static int locked;
static void AcquireSRWLockExclusive( SRWLOCK *l ) { (void)l; if (locked++) FAIL("lock re-entered\n"); }
static void ReleaseSRWLockExclusive( SRWLOCK *l ) { (void)l; if (--locked) FAIL("lock imbalance\n"); }
#define MemoryBarrier() __sync_synchronize()
static LONG64 clk = 1000000000ll;            /* 10 MHz ticks */
static LONG64 overshoot;                     /* added to every sleep */
static int delays, sleeps_ms;
static void QueryPerformanceFrequency( LARGE_INTEGER *f ) { f->QuadPart = 10000000; }
static void QueryPerformanceCounter( LARGE_INTEGER *c ) { c->QuadPart = clk; }
static LONG WINAPI fake_delay( BOOLEAN a, const LARGE_INTEGER *t )
{
    (void)a;
    if (locked) FAIL("slept holding the lock\n");
    if (t->QuadPart >= 0) FAIL("absolute or zero delay\n");
    delays++; clk += -t->QuadPart + overshoot; return 0;
}
static void *GetModuleHandleA( const char *n ) { return strcmp( n, "ntdll.dll" ) ? NULL : (void *)1; }
static void *GetProcAddress( void *m, const char *n ) { return m && !strcmp( n, "NtDelayExecution" ) ? (void *)fake_delay : NULL; }
static void Sleep( DWORD ms ) { sleeps_ms++; clk += (LONG64)ms * 10000; }
static long long cfg_cap, cfg_mw, cfg_min = 30;
static long long mad_cfg_int_pe( const char *key, long long dflt )
{
    if (!strcmp( key, "present-cap-fps" )) return cfg_cap;
    if (!strcmp( key, "present-pace-mw" )) return cfg_mw;
    if (!strcmp( key, "present-pace-min-fps" )) return cfg_min ? cfg_min : dflt;
    FAIL("unknown key %s\n", key);
}
static char logbuf[1 << 16]; static int logn, loglines;
static void d3d12_log( const char *fmt, ... )
{
    va_list ap; va_start( ap, fmt );
    if (logn < (int)sizeof(logbuf) - 1024) logn += vsnprintf( logbuf + logn, sizeof(logbuf) - logn, fmt, ap );
    va_end( ap ); loglines++;
}
static struct { volatile LONG64 pace_seq, pace_uj, pace_dt_us; } g_xp;
static LONG64 InterlockedCompareExchange64( volatile LONG64 *p, LONG64 x, LONG64 c ) { return __sync_val_compare_and_swap( p, c, x ); }
''' + code + r'''
static void reset_all( long long cap, long long mw, long long lo )
{
    memset( &g_pace, 0, sizeof(g_pace) ); g_pace.on = -1;
    memset( (void *)&g_xp, 0, sizeof(g_xp) );
    cfg_cap = cap; cfg_mw = mw; cfg_min = lo; overshoot = 0; delays = sleeps_ms = 0; logn = loglines = 0; logbuf[0] = 0;
}
/* frames of `work` ms; returns the mean and min interval between pacer returns over n frames (after 3 warm-up) */
static void run( int n, double work, double *mean, double *mn, double *mx )
{
    LONG64 last = 0; double sum = 0, lo = 1e9, hi = 0; int i, c = 0;
    for (i = 0; i < n + 3; i++)
    {
        clk += (LONG64)(work * 10000);
        mad_pace_present();
        if (i >= 3 && last) { double iv = (clk - last) / 10000.0; sum += iv; c++; if (iv < lo) lo = iv; if (iv > hi) hi = iv; }
        last = clk;
    }
    *mean = sum / c; *mn = lo; *mx = hi;
}
static void publish( double mw, double secs )
{
    g_xp.pace_uj = (LONG64)(mw * secs * 1000.0); g_xp.pace_dt_us = (LONG64)(secs * 1e6);
    __sync_synchronize(); g_xp.pace_seq++;
}
int main( void )
{
    double mean, mn, mx; int i;

    reset_all( 0, 0, 30 );
    run( 50, 10, &mean, &mn, &mx );
    if (delays || sleeps_ms || loglines || g_pace.on != 0) FAIL("off must do nothing (%d %d %d)\n", delays, sleeps_ms, loglines);
    if (g_xp.pace_seq) FAIL("off must not ask the probe for energy\n");
    if (fabs( mean - 10 ) > 0.01) FAIL("off changed the interval: %f\n", mean);
    printf("PASS: no keys: no sleep, no log\n");

    reset_all( 30, 0, 30 );
    run( 300, 10, &mean, &mn, &mx );
    if (fabs( mean - 33.333 ) > 0.05 || mn < 33.2 || mx > 33.5) FAIL("cap 30: mean %f min %f max %f\n", mean, mn, mx);
    if (g_xp.pace_seq != 1) FAIL("on must ask the probe for energy (seq %lld)\n", (long long)g_xp.pace_seq);
    if (!strstr( logbuf, "[present-pace] ON: Presents held to 30 fps" )) FAIL("no ON line: %s\n", logbuf);
    if (!strstr( logbuf, "[present-pace] target 30.0 fps (33.33 ms)" ) || !strstr( logbuf, "fixed cap" )) FAIL("no window line: %s\n", logbuf);
    if (sleeps_ms) FAIL("used Sleep instead of NtDelayExecution\n");
    reset_all( 30, 0, 30 ); overshoot = 3000;   /* every sleep 0.3 ms late */
    run( 300, 10, &mean, &mn, &mx );
    if (fabs( mean - 33.333 ) > 0.05) FAIL("overshoot accumulated: mean %f\n", mean);
    printf("PASS: present-cap-fps = 30: 33.33 ms intervals with 10 ms frames, also with 0.3 ms late wake-ups\n");

    reset_all( 30, 0, 30 );
    run( 100, 50, &mean, &mn, &mx );
    if (delays) FAIL("slept although every frame was slower than the cap (%d)\n", delays);
    if (fabs( mean - 50 ) > 0.01) FAIL("slow frames changed: %f\n", mean);
    printf("PASS: frames slower than the cap never sleep\n");

    reset_all( 30, 0, 30 );
    run( 20, 10, &mean, &mn, &mx );
    clk += 5000000;   /* a 500 ms stall */
    run( 30, 10, &mean, &mn, &mx );
    if (mn < 33.2) FAIL("catch-up burst after a stall: min interval %f\n", mn);
    if (!g_pace.rebased && !strstr( logbuf, "re-based" )) FAIL("no re-base counted\n");
    printf("PASS: a 500 ms stall banks no catch-up frames\n");

    reset_all( 0, 1200, 30 );
    run( 10, 5, &mean, &mn, &mx );
    if (fabs( g_pace.target_fps - 60 ) > 1e-9 || fabs( mean - 16.667 ) > 0.05) FAIL("adaptive start: %f fps, %f ms\n", g_pace.target_fps, mean);
    {
        double prev = 60, expect;
        for (i = 0; i < 12; i++)
        {
            publish( 1500, 2.0 );
            run( 2, 5, &mean, &mn, &mx );
            expect = prev - (prev * 0.1 < 2 ? 2 : prev * 0.1);
            if (expect < 30) expect = 30;
            if (fabs( g_pace.target_fps - expect ) > 1e-6) FAIL("over budget step %d: %f, expected %f\n", i, g_pace.target_fps, expect);
            prev = expect;
        }
        if (fabs( g_pace.target_fps - 30 ) > 1e-9) FAIL("floor not reached: %f\n", g_pace.target_fps);
        publish( 1100, 2.0 ); run( 2, 5, &mean, &mn, &mx );   /* between 85 % and 100 %: hold */
        if (fabs( g_pace.target_fps - 30 ) > 1e-9) FAIL("moved inside the band: %f\n", g_pace.target_fps);
        publish( 900, 2.0 ); run( 2, 5, &mean, &mn, &mx );
        if (fabs( g_pace.target_fps - 30 ) > 1e-9) FAIL("raised after one window: %f\n", g_pace.target_fps);
        publish( 900, 2.0 ); run( 2, 5, &mean, &mn, &mx );
        if (fabs( g_pace.target_fps - 32 ) > 1e-9) FAIL("not raised after two windows: %f\n", g_pace.target_fps);
        run( 5, 5, &mean, &mn, &mx );   /* no new window: no change */
        if (fabs( g_pace.target_fps - 32 ) > 1e-9) FAIL("changed without a window: %f\n", g_pace.target_fps);
        for (i = 0; i < 40; i++) { publish( 100, 2.0 ); run( 2, 5, &mean, &mn, &mx ); }
        if (fabs( g_pace.target_fps - 60 ) > 1e-9) FAIL("cap not respected: %f\n", g_pace.target_fps);
    }
    reset_all( 40, 900, 35 );
    run( 3, 5, &mean, &mn, &mx );
    for (i = 0; i < 10; i++) { publish( 5000, 2.0 ); run( 2, 5, &mean, &mn, &mx ); }
    if (fabs( g_pace.target_fps - 35 ) > 1e-9) FAIL("custom floor: %f\n", g_pace.target_fps);
    printf("PASS: present-pace-mw: -10 %% per window over budget down to the floor, +2 fps after two windows under 85 %%, up to the cap\n");

    reset_all( 30, 0, 30 );
    run( 3, 10, &mean, &mn, &mx );
    for (i = 0; i < 6; i++) { publish( 2500, 2.0 ); run( 30, 10, &mean, &mn, &mx ); }
    if (fabs( g_pace.target_fps - 30 ) > 1e-9) FAIL("fixed cap followed the power: %f\n", g_pace.target_fps);
    if (!strstr( logbuf, "power 2500 mW over the last 2.0 s window" )) FAIL("power not logged: %s\n", logbuf);
    printf("PASS: a fixed cap logs the published window power and ignores it\n");
    return 0;
}
'''

with tempfile.TemporaryDirectory() as tmp:
    src = Path(tmp) / 'pace.c'
    exe = Path(tmp) / 'pace'
    src.write_text(harness)
    subprocess.run(['cc', '-std=gnu11', '-O1', '-g', '-Wall', '-Wno-unused-function', '-Wno-unused-variable',
                    '-fsanitize=address,undefined', '-fno-sanitize-recover=undefined',
                    str(src), '-o', str(exe), '-lm'], check=True)
    out = subprocess.run([str(exe)], capture_output=True, text=True)
    print(out.stdout, end='')
    if out.returncode:
        raise SystemExit('FAIL: ' + out.stderr)
print('ALL PASS: check-present-pace')
