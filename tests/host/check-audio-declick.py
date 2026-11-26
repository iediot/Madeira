#!/usr/bin/env python3
"""ml1240 underrun de-click and padding cushion of the iOS audio driver
(build/ntdll-unix/audio_null_ios.c); no Wine runs.

Compiles the production struct ios_stream, the de-click block, ios_mix_stream and the
cushion helpers against stubs, then checks:
  - a ring that keeps up is mixed sample for sample (no gain anywhere);
  - a pass that runs dry fades its last frames out (monotonic, reaching ~0 at the gap)
    and the next audio fades in over IOS_DECLICK_FRAMES; MADEIRA_AUDIO_DECLICK=0
    (g_audio_declick = 0) mixes the raw samples;
  - gains stay within [0, 1], also for a resampled stream and a dry pass of 1 frame;
  - ring indexing wraps at ring_frames (the cushion's extra room), not buffer_frames;
  - the cushion grows one 10 ms step per look with new short passes (at most every
    250 ms) up to the ring's extra room, only for a started polled stream, shrinks a
    step after 20 s without one, and the reported padding is the real fill less the
    cushion, clamped to the client's buffer.
"""
from pathlib import Path
import subprocess, tempfile

root = Path(__file__).resolve().parents[2]
src = (root / "build/ntdll-unix/audio_null_ios.c").read_text()

def cut(start, end):
    a = src.index(start)
    return src[a:src.index(end, a)]

stream = cut("struct ios_stream {", "/* ml739: one stream object per client")
assert "g_audio_declick" in stream and "IOS_DECLICK_FRAMES" in stream
mix = cut("static void ios_mix_stream(", "/* Core Audio real-time thread. See ios_mix_stream")
cushion = cut("static void ios_cushion_update(", "/* ml1240: the client side of every stream")
assert "ring_frames" in mix and "rt_resume" in mix

harness = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stdatomic.h>
#include <math.h>
typedef unsigned char BYTE; typedef uint16_t WORD; typedef uint32_t UINT32, DWORD, UInt32;
typedef void *HANDLE;
""" + stream + mix + cushion + r"""
static int fails;
#define CHECK(c, ...) do { if (!(c)) { printf("FAIL " __VA_ARGS__); printf("\n"); fails++; } } while (0)

static float ring[8192 * 2];

static void setup(struct ios_stream *s, UINT32 buffer, UINT32 ringf, UINT32 rate)
{
    memset(s, 0, sizeof(*s));
    s->started = 1; s->mixable = 1; s->is_float = 1; s->sample_bits = 32;
    s->channels = 2; s->frame_bytes = 8; s->sample_rate = rate;
    s->buffer_frames = buffer; s->ring_frames = ringf; s->ring = (BYTE *)ring;
    s->mix_gain[0][0] = 1.0f; s->mix_gain[1][1] = 1.0f;
}

/* write n frames of a constant 0.5 / -0.5 at the current write position */
static void produce(struct ios_stream *s, UINT32 n)
{
    uint64_t wr = atomic_load(&s->write_pos);
    for (UINT32 i = 0; i < n; i++) {
        UINT32 idx = (UINT32)((wr + i) % s->ring_frames);
        ring[idx * 2] = 0.5f; ring[idx * 2 + 1] = -0.5f;
    }
    atomic_store(&s->write_pos, wr + n);
}

int main(void)
{
    struct ios_stream s;
    static float out[4096 * 2];
    UINT32 f;

    /* 1. keeping up: exact */
    setup(&s, 4800, 4800, 48000);
    produce(&s, 480);
    for (int pass = 0; pass < 2; pass++) {
        memset(out, 0, sizeof(out));
        ios_mix_stream(&s, out, 240, 2, 48000);
        for (f = 0; f < 240; f++)
            CHECK(out[f * 2] == 0.5f && out[f * 2 + 1] == -0.5f, "steady pass %d frame %u: %f %f", pass, f, out[f * 2], out[f * 2 + 1]);
    }
    CHECK(!s.rt_resume, "steady stream marked as resuming");

    /* 2. a dry pass fades out, the next audio fades in */
    produce(&s, 100);
    memset(out, 0, sizeof(out));
    ios_mix_stream(&s, out, 240, 2, 48000);
    CHECK(out[0] == 0.5f, "frames well before the gap keep full level: %f", out[0]);
    for (f = 1; f < 100; f++) CHECK(out[f * 2] <= out[(f - 1) * 2] + 1e-7f, "fade-out not monotonic at %u", f);
    CHECK(out[99 * 2] > 0.0f && out[99 * 2] < 0.5f * 0.02f, "last frame before the gap not near silence: %f", out[99 * 2]);
    CHECK(fabsf(out[99 * 2 + 1] + out[99 * 2]) < 1e-6f, "channels faded differently");
    for (f = 100; f < 240; f++) CHECK(out[f * 2] == 0.0f, "gap not silent at %u", f);
    CHECK(s.rt_resume, "dry pass not remembered");
    CHECK(atomic_load(&s.st_short_cbs) == 1, "short pass not counted");
    produce(&s, 480);
    memset(out, 0, sizeof(out));
    ios_mix_stream(&s, out, 240, 2, 48000);
    CHECK(out[0] > 0.0f && out[0] < 0.5f * 0.02f, "first frame after the gap not faded in: %f", out[0]);
    for (f = 1; f < IOS_DECLICK_FRAMES; f++) CHECK(out[f * 2] >= out[(f - 1) * 2], "fade-in not monotonic at %u", f);
    for (f = IOS_DECLICK_FRAMES; f < 240; f++) CHECK(out[f * 2] == 0.5f, "fade-in longer than %u frames (frame %u: %f)", IOS_DECLICK_FRAMES, f, out[f * 2]);
    CHECK(!s.rt_resume, "resume flag kept after a full pass");

    /* 3. de-click off: raw samples */
    g_audio_declick = 0;
    setup(&s, 4800, 4800, 48000);
    produce(&s, 100);
    memset(out, 0, sizeof(out));
    ios_mix_stream(&s, out, 240, 2, 48000);
    for (f = 0; f < 100; f++) CHECK(out[f * 2] == 0.5f, "declick=0 changed frame %u: %f", f, out[f * 2]);
    produce(&s, 240);
    memset(out, 0, sizeof(out));
    ios_mix_stream(&s, out, 240, 2, 48000);
    CHECK(out[0] == 0.5f, "declick=0 faded in: %f", out[0]);
    g_audio_declick = 1;

    /* 4. gains within [0, 1]: resampled stream, 1-frame and empty passes */
    setup(&s, 4410, 4410, 44100);
    for (int round = 0; round < 50; round++) {
        produce(&s, (UINT32)(round * 37 % 300));
        memset(out, 0, sizeof(out));
        ios_mix_stream(&s, out, 256, 2, 48000);
        for (f = 0; f < 256; f++)
            CHECK(out[f * 2] >= 0.0f && out[f * 2] <= 0.5f && out[f * 2 + 1] <= 0.0f && out[f * 2 + 1] >= -0.5f,
                  "resampled round %d frame %u out of range: %f %f", round, f, out[f * 2], out[f * 2 + 1]);
    }
    setup(&s, 4800, 4800, 48000);
    produce(&s, 1);
    memset(out, 0, sizeof(out));
    ios_mix_stream(&s, out, 240, 2, 48000);
    CHECK(out[0] >= 0.0f && out[0] <= 0.5f, "1-frame pass out of range: %f", out[0]);

    /* 5. ring wraps at ring_frames */
    setup(&s, 100, 140, 48000);
    atomic_store(&s.write_pos, 130); atomic_store(&s.play_pos, 130);
    for (f = 0; f < 140; f++) { ring[f * 2] = 0; ring[f * 2 + 1] = 0; }
    produce(&s, 30);   /* frames 130..139 then 0..19 of the ring */
    CHECK(ring[139 * 2] == 0.5f && ring[19 * 2] == 0.5f && ring[20 * 2] == 0.0f, "producer did not wrap at ring_frames");
    memset(out, 0, sizeof(out));
    ios_mix_stream(&s, out, 30, 2, 48000);
    for (f = 0; f < 30; f++) CHECK(out[f * 2] == 0.5f, "mixer did not wrap at ring_frames (frame %u: %f)", f, out[f * 2]);
    {   /* a stream set up without ring_frames (older callers) still mixes on buffer_frames */
        struct ios_stream t;
        setup(&t, 240, 0, 48000);
        for (f = 0; f < 240; f++) { ring[f * 2] = 0.5f; ring[f * 2 + 1] = -0.5f; }
        atomic_store(&t.write_pos, 240);
        memset(out, 0, sizeof(out));
        ios_mix_stream(&t, out, 240, 2, 48000);
        CHECK(out[239 * 2] == 0.5f, "ring_frames 0 does not fall back to buffer_frames");
    }

    /* 6. cushion */
    {
        const uint64_t ms = 1000000ull;
        UINT32 step = 480, room = 1920;   /* 40 ms at 48 kHz */
        uint64_t t = 1000 * ms;
        setup(&s, 4800, 4800 + room, 48000);
        CHECK(ios_reported_padding(&s, 1000) == 1000, "no cushion changes the padding");
        CHECK(ios_reported_padding(&s, 9000) == 4800, "padding not clamped to the buffer");
        s.started = 0;
        atomic_store(&s.st_short_cbs, 5);
        ios_cushion_update(&s, t);
        CHECK(atomic_load(&s.cushion) == 0, "cushion grew while stopped");
        s.started = 1; s.event = (HANDLE)1;
        ios_cushion_update(&s, t);
        CHECK(atomic_load(&s.cushion) == 0, "cushion grew for an event-driven stream");
        s.event = NULL;
        ios_cushion_update(&s, t);
        CHECK(atomic_load(&s.cushion) == step, "first short look: %u", atomic_load(&s.cushion));
        atomic_store(&s.st_short_cbs, 6);
        ios_cushion_update(&s, t + 100 * ms);
        CHECK(atomic_load(&s.cushion) == step, "grew within 250 ms");
        for (int k = 1; k <= 10; k++) {
            atomic_store(&s.st_short_cbs, 6 + k);
            ios_cushion_update(&s, t + (uint64_t)k * 300 * ms);
        }
        CHECK(atomic_load(&s.cushion) == room, "cushion not capped at the ring's room: %u", atomic_load(&s.cushion));
        CHECK(ios_reported_padding(&s, 3000) == 3000 - room, "reported padding not fill less cushion");
        CHECK(ios_reported_padding(&s, 1000) == 0, "reported padding below zero");
        CHECK(ios_reported_padding(&s, 4800 + room) == 4800, "full ring reported past the buffer");
        t += 3300 * ms;
        ios_cushion_update(&s, t + 19000 * ms);
        CHECK(atomic_load(&s.cushion) == room, "shrank before 20 s");
        ios_cushion_update(&s, t + 20500 * ms);
        CHECK(atomic_load(&s.cushion) == room - step, "did not shrink a step after 20 s: %u", atomic_load(&s.cushion));
        ios_cushion_update(&s, t + 30000 * ms);
        CHECK(atomic_load(&s.cushion) == room - step, "shrank again within 20 s");
        for (int k = 2; k <= 6; k++) ios_cushion_update(&s, t + 20500 * ms + (uint64_t)k * 20500 * ms);
        CHECK(atomic_load(&s.cushion) == 0, "did not shrink back to 0: %u", atomic_load(&s.cushion));
        setup(&s, 4800, 4800, 48000);   /* MADEIRA_AUDIO_CUSHION_MS unset: no room, never grows */
        atomic_store(&s.st_short_cbs, 9);
        ios_cushion_update(&s, t);
        CHECK(atomic_load(&s.cushion) == 0, "cushion without room");
    }

    if (fails) return 1;
    puts("ok");
    return 0;
}
"""

with tempfile.TemporaryDirectory() as d:
    c = Path(d) / "t.c"
    exe = Path(d) / "t"
    c.write_text(harness)
    subprocess.run(["cc", "-std=c11", "-O1", "-Wall", "-Wno-unused-function", "-Wno-unused-variable",
                    "-fsanitize=address,undefined", "-o", str(exe), str(c), "-lm"], check=True)
    r = subprocess.run([str(exe)], capture_output=True, text=True)
    print(r.stdout, end="")
    if r.returncode or r.stdout.strip() != "ok":
        raise SystemExit("check-audio-declick: FAILED")
print("PASS: steady mix untouched; dry pass fades out, next audio fades in; declick=0 raw; gains in range; "
      "ring wraps at ring_frames; cushion grows/caps/decays and only for started polled streams")
