#!/usr/bin/env python3
"""Execute the production hazard and deferred-disposal paths with ASan/UBSan."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
src = (root / 'build/ntdll-unix/audio_null_ios.c').read_text()
retire = src[src.index('/* Reader-safe stream retirement.'):src.index('static NTSTATUS ios_process_attach(')]
detach = src[src.index('static void ios_dev_detach('):src.index('/* Start/stop the shared endpoint.')]
assert 'cb_epoch' not in detach and 'usleep' not in detach
assert src.count('ios_audio_dispose_stream(s);') == 2
assert 'ios_audio_reader_begin(&g_audio_readers)' in src
assert 'ios_audio_reader_release(&g_audio_readers, i)' in src
assert 'ios_audio_reader_end(&g_audio_readers)' in src
harness = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <pthread.h>
#include <sched.h>
#include "audio_stream_lifetime.h"
struct ios_stream {
    struct ios_stream *retire_next;
    void *render_scratch, *ring;
    int scratch_guest;
    _Atomic unsigned value;
};
#define IOS_MAX_STREAMS IOS_AUDIO_MIX_SLOTS
static _Atomic(struct ios_stream *) g_mix[IOS_MAX_STREAMS];
static struct ios_audio_readers g_audio_readers = { .rendering = ATOMIC_FLAG_INIT };
static pthread_mutex_t g_dev_lock = PTHREAD_MUTEX_INITIALIZER;
static unsigned destroyed, scheduled;
static void ios_audio_free_scratch(void *p, int guest) { assert(!guest); free(p); ++destroyed; }
/* A deterministic queued tick: no block runs until the test chooses to run it. */
#define DISPATCH_TIME_NOW 0
#define NSEC_PER_MSEC 1000000
#define QOS_CLASS_UTILITY 0
#define dispatch_time(a,b) (b)
#define dispatch_get_global_queue(a,b) 0
#define dispatch_after(when,queue,block) ((void)(when), ++scheduled)
''' + detach + retire + r'''
static struct ios_stream *make_stream(void) {
    struct ios_stream *s = calloc(1, sizeof(*s));
    s->ring = malloc(64); s->render_scratch = malloc(64);
    atomic_store(&s->value, 123);
    return s;
}
static _Atomic int stop;
static void *reader(void *arg) {
    (void)arg;
    while (!atomic_load(&stop)) {
        if (!ios_audio_reader_begin(&g_audio_readers)) { sched_yield(); continue; }
        struct ios_stream *s = ios_audio_reader_acquire(&g_audio_readers, g_mix, 0);
        if (s) {
            assert(atomic_load(&s->value) == 123);
            ((char *)s->ring)[0] = 7;
            ios_audio_reader_release(&g_audio_readers, 0);
        }
        ios_audio_reader_end(&g_audio_readers);
    }
    return NULL;
}
int main(void) {
    struct ios_stream *s = make_stream();
    atomic_store(&g_mix[0], s);
    /* No hardware callbacks: detach and free at once, no future epoch needed. */
    ios_dev_detach(s); ios_audio_dispose_stream(s);
    assert(destroyed == 1 && !g_audio_retired && scheduled == 0);
    s = make_stream(); atomic_store(&g_mix[0], s);
    assert(ios_audio_reader_begin(&g_audio_readers));
    assert(!ios_audio_reader_begin(&g_audio_readers)); /* overlapping callback fails open to silence */
    assert(ios_audio_reader_acquire(&g_audio_readers, g_mix, 0) == s);
    ios_dev_detach(s); ios_audio_dispose_stream(s);
    assert(destroyed == 1 && g_audio_retired == s && scheduled == 1);
    /* Hundreds of releases while that one callback is stalled must not retain
     * unrelated streams or allocate hundreds of worker blocks. */
    for (int i = 0; i < 1000; ++i) {
        struct ios_stream *other = make_stream();
        atomic_store(&g_mix[1], other); ios_dev_detach(other); ios_audio_dispose_stream(other);
        assert(g_audio_retired == s && !s->retire_next && scheduled == 1);
    }
    ios_audio_retire_tick(); assert(g_audio_retired == s && scheduled == 2);
    assert(atomic_load(&s->value) == 123); /* held stream is still valid */
    ios_audio_reader_release(&g_audio_readers, 0); ios_audio_reader_end(&g_audio_readers);
    ios_audio_retire_tick(); assert(!g_audio_retired && !g_audio_retire_scheduled && destroyed == 1002);
    pthread_t thread;
    assert(!pthread_create(&thread, NULL, reader, NULL));
    for (int i = 0; i < 100000; ++i) {
        s = make_stream(); atomic_store_explicit(&g_mix[0], s, memory_order_seq_cst);
        if (!(i % 17)) sched_yield();
        ios_dev_detach(s); ios_audio_dispose_stream(s);
    }
    atomic_store(&stop, 1); pthread_join(thread, NULL); ios_audio_retire_tick();
    assert(!g_audio_retired && destroyed == 101002);
    puts("PASS: idle endpoint, paused/overlapping callback, bounded retirement, 100000 publication/free races");
}
'''
with tempfile.TemporaryDirectory(prefix='madeira-audio-lifetime-') as directory:
    path = Path(directory)
    c = path / 'lifetime.c'
    c.write_text(harness)
    exe = path / 'lifetime'
    subprocess.run(['xcrun', 'clang', '-std=c11', '-fblocks', '-O1', '-g',
                    '-fsanitize=address,undefined', '-pthread', '-I', str(root / 'build/ntdll-unix'),
                    str(c), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
