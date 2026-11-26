/*
 * ml2108: when the HID controller's device clock completes a pending read.
 *
 * GPL-3.0-or-later WITH the Madeira Converter Exception, version 1; see
 * LICENSE-EXCEPTION.md.
 *
 * Pure C, no wineserver types, so tests/host/check-hidpad-on-change.py runs the
 * same decisions the wineserver makes (build/wineserver/hidpad_ios.c). Times are
 * in the caller's ticks (the wineserver's monotonic_time, 100 ns).
 *
 * hidpad_pace_due is the report clock as it has always been: a handle with no
 * report yet, or none for 7/8 of a tick, gets one; a new snapshot goes out once
 * 1 ms has passed. Every report goes out on a due slot.
 *
 * hidpad_pace_send adds env.MADEIRA_HIDPAD_ON_CHANGE: on a due slot, a report
 * whose bytes equal the last one this handle got stays back and the read stays
 * pending, unless keep_alive has passed since that report. A changed report is
 * never held, so it leaves on the same slot as with the switch off -- the
 * reports sent are a subset of the switch-off reports, minus only repeats of
 * the bytes the reader already has. Bytes are compared exactly: no deadzone,
 * no rounding, any stick change is a change. With the switch off every due
 * slot sends, as before.
 */
#ifndef MADEIRA_HIDPAD_PACE_H
#define MADEIRA_HIDPAD_PACE_H

#include <stdint.h>
#include <string.h>

enum hidpad_send
{
    HIDPAD_SEND_NOT_DUE,      /* no report this slot (switch off or on) */
    HIDPAD_SEND_HELD,         /* on-change: due, same bytes, keep-alive not reached */
    HIDPAD_SEND_CLOCK,        /* switch off: every due slot */
    HIDPAD_SEND_FIRST,        /* on-change: the handle's first report */
    HIDPAD_SEND_CHANGED,      /* on-change: the bytes differ from the handle's last report */
    HIDPAD_SEND_KEEPALIVE,    /* on-change: same bytes, keep_alive reached */
};

static inline int hidpad_pace_due( int64_t now, int64_t last_report, uint32_t last_packet, uint32_t packet,
                                   int64_t tick, int64_t min_gap )
{
    int64_t since = now - last_report;

    if (!last_report || since >= tick - tick / 8) return 1;
    return packet != last_packet && since >= min_gap;
}

/* `last`/`last_len`: the bytes of the handle's last report (last_len 0: none
 * kept). `report`/`len`: the report this slot would send. */
static inline enum hidpad_send hidpad_pace_send( int on_change, int64_t now, int64_t last_report,
                                                 uint32_t last_packet, uint32_t packet,
                                                 const unsigned char *last, unsigned int last_len,
                                                 const unsigned char *report, unsigned int len,
                                                 int64_t tick, int64_t min_gap, int64_t keep_alive )
{
    if (!hidpad_pace_due( now, last_report, last_packet, packet, tick, min_gap )) return HIDPAD_SEND_NOT_DUE;
    if (!on_change) return HIDPAD_SEND_CLOCK;
    if (!last_report || !last_len) return HIDPAD_SEND_FIRST;
    if (len != last_len || memcmp( report, last, len )) return HIDPAD_SEND_CHANGED;
    /* Keep-alive slots fall on the 4 ms clock: the same 1/8-tick slack as a due slot. */
    if (now - last_report >= keep_alive - tick / 8) return HIDPAD_SEND_KEEPALIVE;
    return HIDPAD_SEND_HELD;
}

#endif /* MADEIRA_HIDPAD_PACE_H */
