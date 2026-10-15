#ifndef MADEIRA_AUDIO_STREAM_LIFETIME_H
#define MADEIRA_AUDIO_STREAM_LIFETIME_H
#include <stddef.h>
#include <stdatomic.h>

#define IOS_AUDIO_MIX_SLOTS 16
struct ios_stream;
struct ios_audio_readers {
    _Atomic(struct ios_stream *) hazards[IOS_AUDIO_MIX_SLOTS];
    atomic_flag rendering;
};

/* Nonblocking: overlapping callbacks produce silence instead of overwriting
 * another callback's hazard. The normal endpoint has one render thread. */
static inline int ios_audio_reader_begin(struct ios_audio_readers *r)
{
    return !atomic_flag_test_and_set_explicit(&r->rendering, memory_order_acquire);
}

static inline struct ios_stream *ios_audio_reader_acquire(struct ios_audio_readers *r,
    _Atomic(struct ios_stream *) *slots, unsigned slot)
{
    struct ios_stream *s = atomic_load_explicit(&slots[slot], memory_order_seq_cst);
    if (!s) return NULL;
    atomic_store_explicit(&r->hazards[slot], s, memory_order_seq_cst);
    /* A detach between the first load and publication may already have freed
     * s. Do not dereference it unless the published slot still agrees. The SC
     * order pairs this validation with detach and its hazard scan. */
    if (s != atomic_load_explicit(&slots[slot], memory_order_seq_cst)) {
        atomic_store_explicit(&r->hazards[slot], NULL, memory_order_seq_cst);
        return NULL;
    }
    return s;
}

static inline void ios_audio_reader_release(struct ios_audio_readers *r, unsigned slot)
{
    atomic_store_explicit(&r->hazards[slot], NULL, memory_order_seq_cst);
}

static inline void ios_audio_reader_end(struct ios_audio_readers *r)
{
    atomic_flag_clear_explicit(&r->rendering, memory_order_release);
}

static inline int ios_audio_stream_busy(struct ios_audio_readers *r, struct ios_stream *s)
{
    unsigned i;
    for (i = 0; i < IOS_AUDIO_MIX_SLOTS; ++i)
        if (atomic_load_explicit(&r->hazards[i], memory_order_seq_cst) == s) return 1;
    return 0;
}
#endif
