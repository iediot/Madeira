#!/usr/bin/env python3
"""App-side CPU under the iOS energy clamp: [xp-t] thread names, [app-cpu], lean timers; no Wine runs.

RDR2 on the iPhone 18 Pro (log 2026-11-27 01:05, wave1): iOS holds the app to ~1 W of CPU
energy once its budget is spent, and two busy threads with no Wine tid showed in [xp-t] only
as m1475797 and m1476036. Checks:
  - server_ios.c ios_xp_mname, compiled and run on this Mac (proc_pidinfo answers the same
    PROC_PIDTHREADID64INFO flavor here): "~main" for the main thread, the kernel name of a
    named thread with every character outside [A-Za-z0-9._-] made '_' and at most 24 kept,
    "~pri<n>" for an unnamed one, nothing for an unknown thread id or a small buffer, and
    nothing at all with MADEIRA_XP_TNAMES=0;
  - the m-row print uses it ("m<tid>" then the name, then the counters, so "m<tid>" and the
    ':'-separated fields parse as before), and the xprobe and wineserver threads are named;
  - wineserver_thread_cpu_us, the [app-cpu] line (AppCPULog, started from
    runWineFullSequence), and the off state of every new switch (env.MADEIRA_APP_CPU_LOG,
    MADEIRA_PROMOTE_LINK_THREAD, MADEIRA_HUD_LEAN, MADEIRA_UI_LOG_IDLE_POLL,
    MADEIRA_XP_TNAMES) is the code that ran before, and each is in the settings catalog.
Needs python3 and a C compiler (AddressSanitizer/UBSan).
"""
from pathlib import Path
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
server = (root / 'build/ntdll-unix/server_ios.c').read_text()
fps = (root / 'app/Madeira/FPSOverlay.swift').read_text()
tail = (root / 'app/Madeira/LogTail.swift').read_text()
content = (root / 'app/Madeira/ContentView.swift').read_text()
wsb = (root / 'app/Madeira/WineServerBridge.m').read_text()
wsh = (root / 'app/Madeira/WineServerBridge.h').read_text()
winios = (root / 'app/Madeira/Winios/Winios.m').read_text()
catalog = (root / 'app/Madeira/ConfigCatalog.generated.swift').read_text()


def function(source, signature):
    start = source.index(signature)
    return source[start:source.index('\n}', start) + 2] + '\n'


def method(source, signature):
    """A member: from its signature to the closing brace at its own indentation."""
    start = source.index(signature)
    indent = signature[:len(signature) - len(signature.lstrip())]
    return source[start:source.index('\n' + indent + '}', start) + len(indent) + 2] + '\n'


# --- textual -----------------------------------------------------------------------
mrow = server[server.index('madeira-bcd thread-names: "m<tid>~<name>" (ios_xp_mname)'):]
mrow = mrow[:mrow.index('tn += ios_xp_tok_extra( tl + tn, sizeof(tl) - tn, &rows[best] );')]
assert 'snprintf( tl + tn, sizeof(tl) - tn, " m%llu", (unsigned long long)rows[best].tid );' in mrow
assert 'tn += ios_xp_mname( tl + tn, sizeof(tl) - tn, rows[best].tid );' in mrow
assert '":%.0f/%.0f:%.1f/%.1f:%.0f",' in mrow
assert mrow.index('" m%llu"') < mrow.index('ios_xp_mname(') < mrow.index('":%.0f/%.0f')
assert 'pthread_setname_np( "madeira-xprobe" );' in function(server, 'static void ios_xprobe_main( void )')
print('PASS: [xp-t] m rows print m<tid>, then the name, then the counters; the xprobe thread is named')

thread_fn = function(wsb, 'static void *wineserver_thread_func(void *arg) {')
assert 'pthread_setname_np("wineserver");' in thread_fn
assert thread_fn.count('__atomic_store_n(&g_wineserver_mach,') == 2   # set at the start, cleared at the end
assert '__atomic_store_n(&g_wineserver_mach, 0, __ATOMIC_RELEASE);' in function(wsb, 'void fatal_error( const char *err, ... ) {')
assert 'long long wineserver_thread_cpu_us(void);' in wsh
assert 'pthread_setname_np("winios-freeze");' in winios and 'pthread_setname_np("winios-freeze-super");' in winios
print('PASS: wineserver names its thread, keeps its port only while it runs (fatal_error too), and reports its CPU')

app_cpu = fps[fps.index('enum AppCPULog {'):fps.index('/// "[mem]" lines in the game log')]
assert 'guard timer == nil, MadeiraConfig.flag("MADEIRA_APP_CPU_LOG") else { return }' in app_cpu
assert 'Timer.scheduledTimer(withTimeInterval: 30, repeats: true)' in app_cpu
assert 'CFRunLoopActivity.afterWaiting.rawValue' in app_cpu and 'wineserver_thread_cpu_us()' in app_cpu
assert '"[app-cpu] main=%.1f ms/s (user %.1f sys %.1f) wakes=%.0f/s"' in app_cpu
assert 'stop()' in method(app_cpu, '    private static func tick() {')
assert 'AppCPULog.start()' in method(content, '    private func runWineFullSequence(profile: LibraryEntry? = nil) {')
print('PASS: [app-cpu] every 30 s from runWineFullSequence (main ms/s, wakes/s, wineserver ms/s); env.MADEIRA_APP_CPU_LOG=0 = none')

pm = fps[fps.index('final class ProMotionIntent {'):fps.index('/// Opt-in `env.MADEIRA_THERMAL_CAP = 1`')]
assert 'guard MadeiraConfig.flag("MADEIRA_PROMOTE_LINK_THREAD") else { return nil }' in pm
setactive = method(pm, '    func setActive(_ active: Bool, maxHz: Int = ProMotionIntent.panelMaxFPS) {')
# off (or the thread did not start): the old main-run-loop link, same range
main_arm = setactive[setactive.index('} else {'):]
assert 'l.add(to: .main, forMode: .common)' in main_arm and 'l.preferredFrameRateRange = range' in main_arm
assert 'let range = CAFrameRateRange(minimum: lo, maximum: want, preferred: want)' in setactive
assert 'let lo = Float(min(60, maxHz))' in setactive
assert 'l.add(to: .current, forMode: .common)' in setactive
assert 'if armed && activeMax == want { return }' in setactive
# maxHz(for:) is untouched: nothing new arms a link, nothing lowers a rate
assert 'if mode == 1 { return holdMaximum ? panelMaxFPS : 0 }' in pm
assert 'if mode == 3 { return holdMaximum ? min(60, panelMaxFPS) : 0 }' in pm
assert 'return panelMaxFPS                                                // MAX, RAW, 40 cap' in pm
assert 'ready.wait(timeout: .now() + 1) == .success' in fps
print('PASS: the ProMotion link keeps its range and arming rules; on its own thread unless env.MADEIRA_PROMOTE_LINK_THREAD=0')

timers = method(fps, '    private func startTimers() {')
lean = timers[timers.index('if MadeiraConfig.flag("MADEIRA_HUD_LEAN") {'):timers.index('        // 100ms sampling')]
assert 'withTimeInterval: 0.25' in lean and 'return' in lean and 'samples.count > 20' in lean
assert 'fps = computeAdaptiveFPS()' in lean and 'presentCount = cur' in lean
old = timers[timers.index('        // 100ms sampling'):]
assert 'Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true)' in old and 'withTimeInterval: 0.25' in old
assert 'if delta >= 3 && span >= 1.0 {' in fps
print('PASS: the developer FPS overlay ticks at 4 Hz (>= 2 Hz), same >= 1 s window; env.MADEIRA_HUD_LEAN=0 = 100 ms + 250 ms')

park = method(tail, '    private func parkPollTimer() {')
assert 'guard displayPaused, MadeiraConfig.flag("MADEIRA_UI_LOG_IDLE_POLL") else { return }' in park
assert 'pollTimer?.schedule(deadline: .distantFuture, repeating: .never)' in park
pause = method(tail, '    func setDisplayPaused(_ paused: Bool) {')
assert 'self.parkPollTimer()' in pause and 'self.pollTimer?.schedule(deadline: .now() + 0.25, repeating: 0.25)' in pause
assert 'parkPollTimer()   // paused before the file opened' in tail
assert '.suspend()' not in park   # rescheduled, never suspended: stop() needs no balance
print('PASS: the log tail backup poll is parked while display parsing is paused; env.MADEIRA_UI_LOG_IDLE_POLL=0 = keeps firing')

for key in ('env.MADEIRA_APP_CPU_LOG', 'env.MADEIRA_PROMOTE_LINK_THREAD', 'env.MADEIRA_HUD_LEAN',
            'env.MADEIRA_UI_LOG_IDLE_POLL', 'env.MADEIRA_XP_TNAMES'):
    assert f'key: "{key}"' in catalog, key
print('PASS: every new switch is in the settings catalog')

# --- compiled ------------------------------------------------------------------------
pthinfo = server[server.index('struct ios_xp_pthinfo'):server.index('static int ios_xp_mname(')]
mname = function(server, 'static int ios_xp_mname( char *out, size_t cap, uint64_t tid )')
harness = r'''
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <pthread.h>
#include <dlfcn.h>
#include <unistd.h>
extern int proc_pidinfo( int pid, int flavor, uint64_t arg, void *buffer, int buffersize );
#define FAIL(...) do { fprintf(stderr, __VA_ARGS__); exit(1); } while (0)
''' + pthinfo + mname + r'''
static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t cv = PTHREAD_COND_INITIALIZER;
static int ready, done;
static uint64_t named_tid, colon_tid, long_tid, plain_tid;
static void *worker( void *arg )
{
    const char *name = arg;
    uint64_t id;
    if (name) pthread_setname_np( name );
    pthread_threadid_np( NULL, &id );
    pthread_mutex_lock( &mu );
    if (!name) plain_tid = id;
    else if (!strcmp( name, "wineserver" )) named_tid = id;
    else if (strchr( name, ':' )) colon_tid = id;
    else long_tid = id;
    ready++;
    pthread_cond_broadcast( &cv );
    while (!done) pthread_cond_wait( &cv, &mu );
    pthread_mutex_unlock( &mu );
    return NULL;
}
static void expect( uint64_t tid, const char *want )
{
    char buf[64];
    int n;
    memset( buf, 'x', sizeof(buf) );
    n = ios_xp_mname( buf, sizeof(buf), tid );
    if (n != (int)strlen( want ) || (n && strcmp( buf, want ))) FAIL("tid %llu: got %d \"%.*s\", want \"%s\"\n",
                                                                   (unsigned long long)tid, n, n > 0 ? n : 0, buf, want);
}
int main( int argc, char **argv )
{
    pthread_t t[4];
    char *names[4] = { "wineserver", "AURemoteIO::IOThread", "com.apple.a-very-long-thread-name-beyond-24", NULL };
    uint64_t main_tid;
    char buf[64];
    int i;
    for (i = 0; i < 4; i++) pthread_create( &t[i], NULL, worker, names[i] );
    pthread_mutex_lock( &mu );
    while (ready < 4) pthread_cond_wait( &cv, &mu );
    pthread_mutex_unlock( &mu );
    pthread_threadid_np( NULL, &main_tid );
    if (argc > 1)
    {   /* MADEIRA_XP_TNAMES=0 */
        if (ios_xp_mname( buf, sizeof(buf), main_tid ) || ios_xp_mname( buf, sizeof(buf), named_tid )) FAIL("names with the switch off\n");
        printf("off ok\n");
    }
    else
    {
        expect( main_tid, "~main" );
        expect( named_tid, "~wineserver" );
        expect( colon_tid, "~AURemoteIO__IOThread" );
        expect( long_tid, "~com.apple.a-very-long-th" );   /* '~' + 24 */
        if (ios_xp_mname( buf, sizeof(buf), plain_tid ) < 5 || strncmp( buf, "~pri", 4 )) FAIL("unnamed: %s\n", buf);
        for (i = 4; buf[i]; i++) if (buf[i] < '0' || buf[i] > '9') FAIL("unnamed: %s\n", buf);
        if (ios_xp_mname( buf, sizeof(buf), 0x7fffffffffffULL )) FAIL("an unknown thread id got a name\n");
        if (ios_xp_mname( buf, 39, named_tid )) FAIL("wrote into a buffer under 40 bytes\n");
        printf("on ok (unnamed %s)\n", buf);
    }
    pthread_mutex_lock( &mu ); done = 1; pthread_cond_broadcast( &cv ); pthread_mutex_unlock( &mu );
    for (i = 0; i < 4; i++) pthread_join( t[i], NULL );
    return 0;
}
'''
with tempfile.TemporaryDirectory() as d:
    src, exe = Path(d) / 'mname.c', Path(d) / 'mname'
    src.write_text(harness)
    subprocess.run(['cc', '-std=gnu11', '-Wall', '-Werror', '-Wno-unused-function', '-g', '-fsanitize=address,undefined',
                    '-fno-sanitize-recover=all', str(src), '-o', str(exe)], check=True)
    env = dict(os.environ)
    env.pop('MADEIRA_XP_TNAMES', None)
    out = subprocess.run([str(exe)], env=env, check=True, capture_output=True, text=True).stdout
    assert out.startswith('on ok'), out
    env['MADEIRA_XP_TNAMES'] = '0'
    out = subprocess.run([str(exe), 'off'], env=env, check=True, capture_output=True, text=True).stdout
    assert out.startswith('off ok'), out
print('PASS: ios_xp_mname: ~main, kernel names made safe and cut at 24, ~pri<n> unnamed, nothing for a gone thread or a small buffer, nothing with MADEIRA_XP_TNAMES=0')
