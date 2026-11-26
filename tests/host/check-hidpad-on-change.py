#!/usr/bin/env python3
"""ml2108: the HID pad's reports on change (env.MADEIRA_HIDPAD_ON_CHANGE).

Compiles the production pacing (build/hidpad/hidpad_pace.h) and report builders
(build/hidpad/hidpad_reports.h) on the host and drives them with a model of the
wineserver's device clock (build/wineserver/hidpad_ios.c: a 4 ms tick while a
read is pending, a new read checked at once) and of Wine's dinput/xinput HID
readers (a new read queued as soon as one completes).

Checks:
  1. Switch off: hidpad_pace_due is the clock rule from git HEAD's
     hidpad_report_due, extracted from the committed file and compiled beside
     it, over a dense grid of times and packets; and every due slot sends.
  2. Switch on, readers re-reading at once: the reports sent are exactly the
     switch-off reports whose bytes differ from the reader's last report, plus
     keep-alives; after every tick the reader holds the same bytes in both modes
     (no added latency, nothing lost); the first report is immediate.
  3. Switch on, readers re-reading 0-3 ms late: every change reaches the reader
     within one tick of being due; a repeat of the same bytes is only ever a
     keep-alive at least keep_alive - tick/8 after the previous report; with a
     read pending, reports are never further apart than keep_alive + one tick.
  4. Exact bytes: a one-unit move of any stick axis or trigger, any button, the
     hat, changes the generic report (no deadzone or rounding); a DualSense
     report differs every time (counter, sensor clock), which is why the
     wineserver keeps that identity on the clock.
  5. Wiring: the wineserver uses these functions, keeps the switch off by
     default and off for the DualSense identity, logs [hid-pad] ml2108, and the
     RDR2 profile turns it on; the settings catalog lists both switches.

No device, SDK, Wine build or controller; needs a C compiler (CC).
"""
from pathlib import Path
import os
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
srv = (root / 'build/wineserver/hidpad_ios.c').read_text()

# ---- 5. wiring (source checks) ---------------------------------------------
assert '#include "../hidpad/hidpad_pace.h"' in srv
assert 'hidpad_pace_due( monotonic_time, file->last_report, file->last_packet, pad.packet,' in srv
assert 'hidpad_pace_send( 1, monotonic_time' in srv
assert 'static int hidpad_report_due' not in srv, 'old rule must live only in hidpad_pace.h now'
assert re.search(r'madeira_cfg_get\( "env\.MADEIRA_HIDPAD_ON_CHANGE"', srv)
assert re.search(r'getenv\( "MADEIRA_HIDPAD_ON_CHANGE" \)', srv)
assert "device->on_change = on && on[0] == '1' && id->kind != HIDPAD_KIND_DUALSENSE;" in srv, \
    'on-change must default off and never apply to the DualSense identity'
assert 'if (ms < 4 || ms > 1000) ms = HIDPAD_KEEPALIVE_MS;' in srv
assert '#define HIDPAD_KEEPALIVE_MS 200' in srv
assert '[hid-pad] ml2108 %.1f s: %u clock ticks, %u reports sent' in srv
# The off path builds and sends exactly as before: one report per due slot.
assert re.search(r'else if \(!device->on_change\)\s*\{\s*send = HIDPAD_SEND_CLOCK;\s*len = hidpad_input_report\(', srv)
# Held reports leave the read pending and the clock running.
assert 'if (pending) hidpad_start_clock( device );' in srv
assert 'if (hidpad_file_pending( file )) hidpad_start_clock( device );' in srv

lib = (root / 'app/Madeira/Library.swift').read_text()
m = re.search(r'case "rdr2\.exe", "playrdr2\.exe":(.*?)\n        case ', lib, re.S)
assert m, 'RDR2 profile not found'
assert re.search(r'^\s*env\.MADEIRA_HIDPAD_ON_CHANGE = 1$', m.group(1), re.M), 'RDR2 profile must turn ml2108 on'
assert lib.count('env.MADEIRA_HIDPAD_ON_CHANGE = 1') == 1, 'ml2108 is on for RDR2 only'

cat = (root / 'app/Madeira/ConfigCatalog.generated.swift').read_text()
assert 'key: "env.MADEIRA_HIDPAD_ON_CHANGE"' in cat, 'regenerate: python3 build/tools/gen-config-catalog.py'
assert 'key: "env.MADEIRA_HIDPAD_KEEPALIVE_MS"' in cat, 'regenerate: python3 build/tools/gen-config-catalog.py'

# ---- 1. the clock rule from git HEAD ----------------------------------------
old_rule = None
try:
    head = subprocess.run(['git', '-C', str(root), 'show', 'HEAD:build/wineserver/hidpad_ios.c'],
                          capture_output=True, text=True, check=True).stdout
    mm = re.search(r'static int hidpad_report_due\(.*?\n}\n', head, re.S)
    if mm:
        old_rule = mm.group(0)
except (OSError, subprocess.CalledProcessError):
    pass
if old_rule is None:
    # HEAD already has the new code (after a commit): the rule as it stood before ml2108.
    old_rule = '''static int hidpad_report_due( const struct hidpad_file *file, const struct winios_hidpad *pad )
{
    timeout_t since = monotonic_time - file->last_report;

    if (!file->last_report || since >= HIDPAD_TICK - HIDPAD_TICK / 8) return 1;
    return pad->packet != file->last_packet && since >= HIDPAD_MIN_GAP;
}
'''
    print('note: HEAD has no hidpad_report_due; using the pre-ml2108 rule kept in this test')

test = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "hidpad_reports.h"
#include "hidpad_pace.h"

#define TICKS_PER_SEC 10000000LL
typedef int64_t timeout_t;
#define HIDPAD_TICK      (TICKS_PER_SEC / 250)
#define HIDPAD_MIN_GAP   (TICKS_PER_SEC / 1000)
#define MS (TICKS_PER_SEC / 1000)
static timeout_t monotonic_time;
struct hidpad_file { timeout_t last_report; unsigned int last_packet; };

OLD_RULE

static uint64_t rng = 0x9e3779b97f4a7c15ull;
static unsigned int rnd( unsigned int n )
{
    rng ^= rng << 13; rng ^= rng >> 7; rng ^= rng << 17;
    return (unsigned int)(rng % n);
}

static const struct hidpad_identity *xbox, *generic, *dualsense;

/* The app's snapshot over time: idle stretches, stick drifts of one unit,
 * taps, trigger pulls, a disconnect -- changes at random sub-tick times. */
struct timeline { int count; timeout_t at[4096]; struct winios_hidpad pad[4096]; };

static void make_timeline( struct timeline *t, timeout_t end )
{
    struct winios_hidpad pad = {0};
    timeout_t now = 1;
    pad.connected = 1;
    t->count = 0;
    while (now < end && t->count < 4096)
    {
        switch (rnd( 10 ))
        {
        case 0: now += (timeout_t)(200 + rnd( 1500 )) * MS; break;            /* idle */
        case 1: pad.lx += rnd( 2 ) ? 1 : -1; now += rnd( 6 * MS ) + 1; break;   /* one unit */
        case 2: pad.ry = (int16_t)(rnd( 65536 ) - 32768); now += rnd( 3 * MS ) + 1; break;
        case 3: pad.buttons ^= 1u << rnd( 16 ); now += rnd( 40 * MS ) + 1; break;
        case 4: pad.left_trigger = rnd( 256 ); now += rnd( 2 * MS ) + 1; break;
        case 5: pad.right_trigger += 1; now += rnd( 9 * MS ) + 1; break;
        case 6: pad.battery = rnd( 100 ); now += rnd( 50 * MS ) + 1; break;     /* not in the report */
        case 7: pad.connected = !pad.connected; now += rnd( 300 * MS ) + 1; break;
        case 8: pad.rx += 1; pad.rx -= 1; now += rnd( 5 * MS ) + 1; break;      /* published, same */
        default: pad.ly += 3; now += rnd( 500 ) + 1; break;                       /* faster than the clock */
        }
        t->at[t->count] = now;
        t->pad[t->count] = pad;
        t->pad[t->count].packet = t->count + 1;
        t->count++;
    }
}

/* What winios_hidpad_get_state returns at `now`. */
static void state_at( const struct timeline *t, timeout_t now, struct winios_hidpad *out )
{
    int lo = 0, hi = t->count;
    while (lo < hi) { int mid = (lo + hi) / 2; if (t->at[mid] <= now) lo = mid + 1; else hi = mid; }
    if (!lo) { memset( out, 0, sizeof(*out) ); return; }
    *out = t->pad[lo - 1];
    if (!out->connected) { uint32_t p = out->packet; memset( out, 0, sizeof(*out) ); out->packet = p; }
}

/* One handle, one reader, the wineserver's hidpad_file_deliver over it. */
struct handle
{
    timeout_t last_report; uint32_t last_packet;
    unsigned char last[64]; unsigned int last_len;
    int pending; timeout_t reissue_at;
    unsigned char held_by_reader[64]; unsigned int reader_len;   /* what the game last parsed */
    int sent, first, changed, keepalive, held;
    timeout_t prev_sent, max_gap_pending;
};

static int deliver( struct handle *h, const struct hidpad_identity *id, const struct timeline *t,
                    int on_change, timeout_t keep_alive, timeout_t now, struct hidpad_report_state *st )
{
    struct winios_hidpad pad;
    unsigned char report[64];
    unsigned int len = 0;
    enum hidpad_send send;

    if (!h->pending) return 0;
    state_at( t, now, &pad );
    if (!hidpad_pace_due( now, h->last_report, h->last_packet, pad.packet, HIDPAD_TICK, HIDPAD_MIN_GAP ))
        send = HIDPAD_SEND_NOT_DUE;
    else
    {
        len = hidpad_input_report( id, &pad, st, now / 10, report );
        send = hidpad_pace_send( on_change, now, h->last_report, h->last_packet, pad.packet,
                                 h->last, h->last_len, report, len, HIDPAD_TICK, HIDPAD_MIN_GAP, keep_alive );
    }
    if (send == HIDPAD_SEND_HELD) h->held++;
    if (send < HIDPAD_SEND_CLOCK) return 0;
    if (send == HIDPAD_SEND_FIRST) h->first++;
    if (send == HIDPAD_SEND_CHANGED) h->changed++;
    if (send == HIDPAD_SEND_KEEPALIVE) h->keepalive++;
    if (send == HIDPAD_SEND_KEEPALIVE || (on_change && send != HIDPAD_SEND_FIRST && send != HIDPAD_SEND_CHANGED))
        assert( h->last_len == len && !memcmp( h->last, report, len ) );
    if (send == HIDPAD_SEND_CHANGED) assert( h->last_len != len || memcmp( h->last, report, len ) );
    if (h->sent && h->pending && now - h->prev_sent > h->max_gap_pending) h->max_gap_pending = now - h->prev_sent;
    h->prev_sent = now;
    h->last_report = now; h->last_packet = pad.packet;
    memcpy( h->last, report, len ); h->last_len = len;
    memcpy( h->held_by_reader, report, len ); h->reader_len = len;
    h->sent++;
    h->pending = 0;
    return 1;
}

/* 2: readers that re-read at once. Both modes see the same clock phase, so
 * they can be compared tick by tick. */
static void lockstep( const struct hidpad_identity *id, unsigned int seed, timeout_t keep_alive )
{
    static struct timeline t;
    struct handle off = {0}, on = {0};
    struct hidpad_report_state st_off = {0}, st_on = {0};
    timeout_t end = 120LL * TICKS_PER_SEC, now, phase;
    int ticks = 0;

    rng = 0x9e3779b97f4a7c15ull ^ (seed * 0x100000001b3ull);
    make_timeline( &t, end );
    phase = 1 + rnd( HIDPAD_TICK );
    off.pending = on.pending = 1;

    /* The first read: due at once in both modes. */
    now = phase;
    assert( deliver( &off, id, &t, 0, keep_alive, now, &st_off ) );
    assert( deliver( &on, id, &t, 1, keep_alive, now, &st_on ) );
    assert( on.first == 1 );
    off.pending = on.pending = 1;
    /* The reader's re-read reaches the server at once: not due (since 0). */
    assert( !deliver( &off, id, &t, 0, keep_alive, now, &st_off ) );
    assert( !deliver( &on, id, &t, 1, keep_alive, now, &st_on ) );

    for (now = phase + HIDPAD_TICK; now < end; now += HIDPAD_TICK)
    {
        unsigned char prev_on[64];
        unsigned int prev_len = on.reader_len;
        int s_off, s_on;

        memcpy( prev_on, on.held_by_reader, prev_len );
        s_off = deliver( &off, id, &t, 0, keep_alive, now, &st_off );
        s_on = deliver( &on, id, &t, 1, keep_alive, now, &st_on );
        ticks++;
        /* Off mode: a report every tick, as before. */
        assert( s_off );
        /* On mode sends only on slots off mode sends on... */
        if (s_on) assert( s_off );
        /* ...and on every one whose bytes are new to the reader. */
        if (s_off && (off.reader_len != prev_len || memcmp( off.held_by_reader, prev_on, prev_len ))) assert( s_on );
        /* The reader holds the same bytes in both modes after every tick. */
        assert( on.reader_len == off.reader_len && !memcmp( on.held_by_reader, off.held_by_reader, on.reader_len ) );
        off.pending = on.pending = 1;
        assert( !deliver( &off, id, &t, 0, keep_alive, now, &st_off ) );
        assert( !deliver( &on, id, &t, 1, keep_alive, now, &st_on ) );
    }
    assert( on.max_gap_pending <= keep_alive + HIDPAD_TICK );
    assert( on.sent < off.sent / 4 );
    if (seed < 3)
        printf( "  lockstep %s keep-alive %lld ms seed %u: %d ticks, off %d reports, on %d (first %d, changed %d, "
                "keep-alive %d, held %d)\n", id->env, (long long)(keep_alive / MS), seed, ticks, off.sent, on.sent,
                on.first, on.changed, on.keepalive, on.held );
}

/* 3: readers that take 0-3 ms to queue the next read; reads also land between
 * ticks, where the server checks them at once. */
static unsigned long jitter_checks;
static void jittered( const struct hidpad_identity *id, unsigned int seed, timeout_t keep_alive )
{
    static struct timeline t;
    struct handle h[3] = {{0}};
    struct hidpad_report_state st = {0};
    timeout_t end = 90LL * TICKS_PER_SEC, next_tick, now;
    unsigned int i;

    rng = 0x51ed27ull ^ (seed * 0x2545f4914f6cdd1dull);
    make_timeline( &t, end );
    for (i = 0; i < 3; i++) { h[i].pending = 0; h[i].reissue_at = 1 + rnd( 5 * MS ); }
    next_tick = 1 + rnd( HIDPAD_TICK );
    now = 0;
    while (now < end)
    {
        timeout_t step = next_tick;
        for (i = 0; i < 3; i++) if (!h[i].pending && h[i].reissue_at < step) step = h[i].reissue_at;
        now = step;
        for (i = 0; i < 3; i++)
            if (!h[i].pending && h[i].reissue_at == now)
            {
                h[i].pending = 1;                      /* hidpad_file_read: queue, deliver */
                if (deliver( &h[i], id, &t, 1, keep_alive, now, &st )) h[i].reissue_at = now + rnd( 3 * MS + 1 );
            }
        if (now == next_tick)
        {
            for (i = 0; i < 3; i++)
                if (deliver( &h[i], id, &t, 1, keep_alive, now, &st )) h[i].reissue_at = now + rnd( 3 * MS + 1 );
            next_tick += HIDPAD_TICK;
        }
        /* Whatever the snapshot shows, a reader with a read pending has it in
         * hand by one tick after the change became due (MIN_GAP after its
         * last report, which itself is at most a tick). */
        for (i = 0; i < 3; i++)
        {
            struct winios_hidpad pad;
            unsigned char cur[64];
            unsigned int len;
            timeout_t changed_at;
            int k;

            if (!h[i].pending || !h[i].sent) continue;
            state_at( &t, now - HIDPAD_TICK - HIDPAD_MIN_GAP, &pad );
            len = hidpad_input_report( id, &pad, &st, 0, cur );
            /* find when the snapshot last changed before that point */
            for (k = t.count - 1, changed_at = 0; k >= 0; k--)
                if (t.at[k] <= now - HIDPAD_TICK - HIDPAD_MIN_GAP) { changed_at = t.at[k]; break; }
            /* the state must have been stable since and the read pending throughout */
            if (k + 1 < t.count && t.at[k + 1] <= now) continue;
            if (h[i].reissue_at > changed_at) continue;
            assert( h[i].reader_len == len && !memcmp( h[i].held_by_reader, cur, len ) );
            jitter_checks++;
        }
    }
    for (i = 0; i < 3; i++) assert( h[i].max_gap_pending <= keep_alive + HIDPAD_TICK + 3 * MS + 1 );
}

static void exact_bytes( void )
{
    struct hidpad_report_state st = {0};
    struct winios_hidpad base = {0}, p;
    unsigned char a[64], b[64];
    unsigned int la, lb, bit;
    int16_t *axis[4];
    int i;

    base.connected = 1;
    base.lx = 1234; base.ly = -77; base.rx = 0; base.ry = 32767;
    base.left_trigger = 0; base.right_trigger = 254;
    la = hidpad_input_report( xbox, &base, &st, 0, a );
    assert( la == HIDPAD_GENERIC_INPUT_LEN );
    for (i = 0; i < 4; i++)
    {
        int d;
        for (d = -1; d <= 1; d += 2)
        {
            p = base;
            axis[0] = &p.lx; axis[1] = &p.ly; axis[2] = &p.rx; axis[3] = &p.ry;
            if ((*axis[i] == 32767 && d > 0) || (*axis[i] == -32768 && d < 0)) continue;
            *axis[i] += d;
            lb = hidpad_input_report( xbox, &p, &st, 0, b );
            assert( lb == la && memcmp( a, b, la ) );
            assert( hidpad_pace_send( 1, 100 * MS, 1, 1, 2, a, la, b, lb, HIDPAD_TICK, HIDPAD_MIN_GAP, 200 * MS )
                    == HIDPAD_SEND_CHANGED );
        }
    }
    p = base; p.left_trigger = 1;
    lb = hidpad_input_report( xbox, &p, &st, 0, b ); assert( memcmp( a, b, la ) );
    p = base; p.right_trigger = 255;
    lb = hidpad_input_report( xbox, &p, &st, 0, b ); assert( memcmp( a, b, la ) );
    for (bit = 0; bit < 16; bit++)
    {
        if ((1u << bit) == 0x0800) continue;   /* XInput's unused bit */
        p = base; p.buttons = 1u << bit;
        lb = hidpad_input_report( xbox, &p, &st, 0, b );
        assert( memcmp( a, b, la ) );
    }
    /* Not in the report: the same bytes, held (the reader has them already). */
    p = base; p.battery = 42; p.packet = 99;
    lb = hidpad_input_report( xbox, &p, &st, 0, b ); assert( !memcmp( a, b, la ) );
    assert( hidpad_pace_send( 1, 100 * MS, 1, 1, 99, a, la, b, lb, HIDPAD_TICK, HIDPAD_MIN_GAP, 200 * MS )
            == HIDPAD_SEND_HELD );
    /* Keep-alive edge: 7/8 of a tick early on the clock is a keep-alive. */
    assert( hidpad_pace_send( 1, 1 + 200 * MS - HIDPAD_TICK / 8, 1, 1, 1, a, la, a, la, HIDPAD_TICK,
                              HIDPAD_MIN_GAP, 200 * MS ) == HIDPAD_SEND_KEEPALIVE );
    assert( hidpad_pace_send( 1, 200 * MS - HIDPAD_TICK / 8 - 1, 1, 1, 1, a, la, a, la, HIDPAD_TICK,
                              HIDPAD_MIN_GAP, 200 * MS ) == HIDPAD_SEND_HELD );
    /* No report kept yet: first. Not due: nothing, even with new bytes. */
    assert( hidpad_pace_send( 1, 5, 0, 0, 0, a, 0, a, la, HIDPAD_TICK, HIDPAD_MIN_GAP, 200 * MS ) == HIDPAD_SEND_FIRST );
    assert( hidpad_pace_send( 1, 10 + HIDPAD_MIN_GAP - 1, 10, 1, 2, a, la, b, la, HIDPAD_TICK, HIDPAD_MIN_GAP,
                              200 * MS ) == HIDPAD_SEND_NOT_DUE );
    /* Switch off: every due slot is a clock report, same bytes or not. */
    assert( hidpad_pace_send( 0, 100 * MS, 1, 1, 1, a, la, a, la, HIDPAD_TICK, HIDPAD_MIN_GAP, 200 * MS )
            == HIDPAD_SEND_CLOCK );
    /* The generic identity uses the same builder as the Xbox one. */
    lb = hidpad_input_report( generic, &base, &st, 0, b ); assert( lb == la && !memcmp( a, b, la ) );
    /* DualSense: two reports of one state differ (counter, sensor clock). */
    la = hidpad_input_report( dualsense, &base, &st, 1000, a );
    lb = hidpad_input_report( dualsense, &base, &st, 1000, b );
    assert( la == HIDPAD_DUALSENSE_INPUT_LEN && memcmp( a, b, la ) );
}

static void old_rule_equal( void )
{
    static const timeout_t lasts[] = { 0, 1, 7, HIDPAD_TICK, 123456789 };
    unsigned int i, lp, pk;
    timeout_t d;

    for (i = 0; i < sizeof(lasts) / sizeof(lasts[0]); i++)
        for (d = -2 * HIDPAD_TICK; d <= 3 * HIDPAD_TICK; d += 997)
            for (lp = 0; lp < 3; lp++)
                for (pk = 0; pk < 3; pk++)
                {
                    struct hidpad_file f = { lasts[i], lp };
                    struct winios_hidpad pad = {0};
                    timeout_t now = lasts[i] + d;
                    int edges[] = { 0, 1, -1 };
                    unsigned int e;

                    pad.packet = pk;
                    for (e = 0; e < 3; e++)
                    {
                        timeout_t n2 = now + edges[e];
                        monotonic_time = n2;
                        assert( hidpad_report_due( &f, &pad ) ==
                                hidpad_pace_due( n2, f.last_report, f.last_packet, pad.packet, HIDPAD_TICK, HIDPAD_MIN_GAP ) );
                    }
                    /* the exact boundaries */
                    {
                        timeout_t b[] = { lasts[i] + HIDPAD_TICK - HIDPAD_TICK / 8, lasts[i] + HIDPAD_MIN_GAP };
                        for (e = 0; e < 2; e++)
                        {
                            timeout_t n3;
                            for (n3 = b[e] - 1; n3 <= b[e] + 1; n3++)
                            {
                                monotonic_time = n3;
                                assert( hidpad_report_due( &f, &pad ) ==
                                        hidpad_pace_due( n3, f.last_report, f.last_packet, pad.packet, HIDPAD_TICK, HIDPAD_MIN_GAP ) );
                            }
                        }
                    }
                }
}

int main( void )
{
    unsigned int seed;

    xbox = hidpad_identity_from_env( "xbox" );
    generic = hidpad_identity_from_env( "generic" );
    dualsense = hidpad_identity_from_env( "dualsense" );
    assert( xbox && generic && dualsense );
    assert( xbox->kind != HIDPAD_KIND_DUALSENSE && generic->kind != HIDPAD_KIND_DUALSENSE );

    old_rule_equal();
    printf( "ok 1: switch off is the committed clock rule, every due slot sends\n" );
    for (seed = 0; seed < 40; seed++)
    {
        lockstep( xbox, seed, 200 * MS );
        lockstep( generic, seed, 100 * MS );
        lockstep( xbox, seed, 250 * MS );
    }
    printf( "ok 2: on-change sends a subset of the clock's reports, same reader state every tick\n" );
    for (seed = 0; seed < 25; seed++)
    {
        jittered( xbox, seed, 200 * MS );
        jittered( generic, seed, 4 * MS );
    }
    assert( jitter_checks > 100000 );
    printf( "ok 3: late re-reads: changes within a tick, keep-alive gap bounded (%lu state checks)\n", jitter_checks );
    exact_bytes();
    printf( "ok 4: exact bytes, no deadzone; DualSense reports always differ\n" );
    return 0;
}
'''.replace('OLD_RULE', old_rule)

cc = os.environ.get('CC', 'cc')
with tempfile.TemporaryDirectory() as d:
    src = Path(d) / 't.c'
    exe = Path(d) / 't'
    src.write_text(test)
    subprocess.run([cc, '-std=gnu99', '-O1', '-Wall', '-Wno-unused-function', '-Werror=implicit-function-declaration',
                    '-I', os.environ.get('HIDPAD_INC', str(root / 'build/hidpad')),
                    '-I', str(root / 'build/hidpad'), str(src), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
print('ok 5: wiring (wineserver, RDR2 profile, catalog)')
print('check-hidpad-on-change: all passed')
