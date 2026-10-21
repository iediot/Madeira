#!/usr/bin/env python3
"""Execute production swap rotation/queue code on real mappings and a sparse file.

The punch worker is deliberately blocked until after recommit writes. This
checks offset quarantine and the zero contract, not device APFS latency.
"""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / 'build/ntdll-unix/virtual_ios.c').read_text()

def section(start, end):
    return source[source.index(start):source.index(end)]

code = r'''
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>
#ifdef __linux__
#include <linux/falloc.h>
struct fpunchhole { off_t fp_offset, fp_length; };
#define F_PUNCHHOLE 99
#define MS_KILLPAGES 0
#define pthread_setname_np(name) pthread_setname_np(pthread_self(), name)
#endif
static pthread_mutex_t virtual_mutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t gate = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t gate_cond = PTHREAD_COND_INITIALIZER;
static int blocked, entered, fail_punch, fail_map;
static unsigned punch_calls;
static int test_punch(int fd, int cmd, struct fpunchhole *ph) {
    pthread_mutex_lock(&gate);
    entered = 1;
    punch_calls++;
    pthread_cond_broadcast(&gate_cond);
    while (blocked) pthread_cond_wait(&gate_cond, &gate);
    int fail = fail_punch;
    pthread_mutex_unlock(&gate);
    if (fail) { errno = EIO; return -1; }
#ifdef __linux__
    return fallocate(fd, FALLOC_FL_PUNCH_HOLE | FALLOC_FL_KEEP_SIZE, ph->fp_offset, ph->fp_length);
#else
    return fcntl(fd, cmd, ph);
#endif
}
static void *test_map(void *p, size_t n, int prot, int flags, int fd, off_t off) {
    if (fail_map) { errno = ENOMEM; return MAP_FAILED; }
    return mmap(p, n, prot, flags, fd, off);
}
#define fcntl test_punch
#define mmap test_map
static int ios_swap_v2 = 1;
static uint64_t ios_swap_logical;
static unsigned long long ios_swap_merges, ios_swap_bump_back, ios_swap_free_drop, ios_swap_holes;
'''
code += section('static int      ios_swap_fd = -1;', '/* COVERAGE, FREE-SPACE REUSE')
code += section('static uint64_t ios_swap_take(', '/* ml1258, broad only:')
code += section('static int ios_swap_rotate_resv(', 'static int ios_swap_overlaps(')
code += r'''
static void wait_entered(void) {
    struct timespec deadline; clock_gettime(CLOCK_REALTIME, &deadline); deadline.tv_sec += 5;
    pthread_mutex_lock(&gate);
    while (!entered) assert(pthread_cond_timedwait(&gate_cond, &gate, &deadline) == 0);
    pthread_mutex_unlock(&gate);
}
static void finish(unsigned long long target) {
    pthread_mutex_lock(&gate);
    blocked = 0; pthread_cond_broadcast(&gate_cond);
    pthread_mutex_unlock(&gate);
    for (int tries = 0; tries < 5000; tries++) {
        pthread_mutex_lock(&virtual_mutex);
        int done = ios_swap_pq_done == target;
        pthread_mutex_unlock(&virtual_mutex);
        if (done) return;
        usleep(1000);
    }
    assert(!"punch worker timed out");
}
static void pattern(const char *p, size_t n, unsigned char value) {
    for (size_t j = 0; j < n; j++) assert((unsigned char)p[j] == value);
}
static void run_case(size_t start, size_t len, int fail) {
    const size_t total = 0x10000;
    ios_swap_n = ios_swap_nfree = 0; ios_swap_bump = 0;
    assert(ftruncate(ios_swap_fd, 0) == 0);
    assert(ftruncate(ios_swap_fd, ios_swap_logical) == 0);
    uint64_t old = ios_swap_take(total);
    char *p = mmap(NULL, total, PROT_READ | PROT_WRITE, MAP_SHARED, ios_swap_fd, old);
    assert(p != MAP_FAILED);
    memset(p, 0x57, total);
    assert(msync(p, total, MS_SYNC) == 0);
    ios_swap_n = 1;
    ios_swap_ext[0].va = p; ios_swap_ext[0].off = old;
    ios_swap_ext[0].len = total; ios_swap_ext[0].resv = 1;
    ios_swap_ext[0].key = total; ios_swap_ext[0].born = 123;
    ios_swap_bytes = total;
    pthread_mutex_lock(&gate);
    blocked = 1; entered = 0; fail_punch = fail;
    pthread_mutex_unlock(&gate);
    unsigned long long done = ios_swap_pq_done, leaks = ios_swap_punch_leaks;
    pthread_mutex_lock(&virtual_mutex);
    assert(ios_swap_punch_resv(p + start, len));
    assert(ios_swap_bytes == total);
    assert(ios_swap_n == 1 + (start != 0) + (start + len != total));
    unsigned center = ios_swap_n - 1;
    assert(ios_swap_ext[center].off != old + start);
    assert(ios_swap_ext[center].va == p + start && ios_swap_ext[center].len == len);
    assert(ios_swap_nfree == 0); /* old offsets quarantined, worker not finished */
    for (unsigned k = 0; k < ios_swap_n; k++) {
        assert(ios_swap_ext[k].resv == 1 && ios_swap_ext[k].key == total);
        assert(ios_swap_ext[k].born == 123);
        for (unsigned j = k + 1; j < ios_swap_n; j++)
            assert(ios_swap_ext[k].va + ios_swap_ext[k].len <= ios_swap_ext[j].va ||
                   ios_swap_ext[j].va + ios_swap_ext[j].len <= ios_swap_ext[k].va);
    }
    pthread_mutex_unlock(&virtual_mutex);
    wait_entered();
    assert(pthread_mutex_trylock(&virtual_mutex) == 0); /* blocked punch does not hold VM lock */
    pthread_mutex_unlock(&virtual_mutex);
    /* Worker is blocked inside fcntl: it cannot consume this synthetic full queue. */
    pthread_mutex_lock(&ios_swap_pq_lock);
    unsigned queued = ios_swap_pq_n;
    ios_swap_pq_n = IOS_SWAP_PQ;
    pthread_mutex_unlock(&ios_swap_pq_lock);
    assert(!ios_swap_rotate_resv(center, p + start, len));
    pthread_mutex_lock(&ios_swap_pq_lock);
    ios_swap_pq_n = queued;
    pthread_mutex_unlock(&ios_swap_pq_lock);
    pattern(p, start, 0x57);
    pattern(p + start, len, 0);
    pattern(p + start + len, total - start - len, 0x57);
    memset(p + start, 0xa9, len); /* immediate recommit/write, BEFORE old punch */
    finish(done + 1);
    pattern(p + start, len, 0xa9); /* delayed cleanup must never erase new data */
    pattern(p, start, 0x57);
    pattern(p + start + len, total - start - len, 0x57);
    if (fail) {
        assert(ios_swap_punch_leaks == leaks + 1 && ios_swap_nfree == 0);
    } else {
        assert(ios_swap_nfree == 1 && ios_swap_free[0].off == old + start);
        assert(ios_swap_free[0].len == len);
        uint64_t reused = ios_swap_take(len);
        assert(reused == old + start);
        char *q = mmap(NULL, len, PROT_READ, MAP_SHARED, ios_swap_fd, reused);
        assert(q != MAP_FAILED); pattern(q, len, 0); munmap(q, len);
    }
    /* Failure paths return before changing any mapping or metadata. */
    unsigned n = ios_swap_n;
    uint64_t off = ios_swap_ext[center].off, bump = ios_swap_bump;
    unsigned nfree = ios_swap_nfree;
    unsigned long long rotations = ios_swap_rotations;
    assert(msync(p + start, len, MS_SYNC) == 0);
    fail_map = 1;
    assert(!ios_swap_rotate_resv(center, p + start, len));
    fail_map = 0;
    assert(ios_swap_n == n && ios_swap_ext[center].off == off);
    assert(ios_swap_bump == bump && ios_swap_nfree == nfree);
    pattern(p + start, len, 0xa9);
    uint64_t logical = ios_swap_logical;
    ios_swap_logical = ios_swap_bump;
    assert(!ios_swap_rotate_resv(center, p + start, len)); /* no spare offsets */
    ios_swap_logical = logical;
    ios_swap_n = 16384;
    assert(!ios_swap_rotate_resv(center, p + start + 0x4000, 0x4000));
    ios_swap_n = n;
    /* Explicit disabled mode must preserve the old backing. */
    ios_swap_pq_state = -1;
    assert(!ios_swap_rotate_resv(center, p + start, len));
    ios_swap_pq_state = 1;
    assert(ios_swap_rotations == rotations);
    /* A larger decommit must span the previously split reservation without
     * becoming anonymous. Validate coverage before changing any one piece. */
    ios_swap_ext[center].resv = 0;
    assert(!ios_swap_punch_resv(p, total));
    assert(ios_swap_rotations == rotations);
    ios_swap_ext[center].resv = 1;
    pthread_mutex_lock(&gate);
    blocked = 1; entered = 0; fail_punch = 0;
    pthread_mutex_unlock(&gate);
    done = ios_swap_pq_done;
    pthread_mutex_lock(&virtual_mutex);
    assert(ios_swap_punch_resv(p, total));
    assert(ios_swap_n == 1 && ios_swap_bytes == total);
    pthread_mutex_unlock(&virtual_mutex);
    wait_entered();
    pattern(p, total, 0);
    memset(p, 0x38, total);
    finish(done + n);
    pattern(p, total, 0x38);
    /* Disabled queue follows the synchronous correctness fallback. */
    pthread_mutex_lock(&virtual_mutex);
    ios_swap_pq_state = -1;
    assert(ios_swap_punch_resv(p, total));
    ios_swap_pq_state = 1;
    pthread_mutex_unlock(&virtual_mutex);
    pattern(p, total, 0);
    munmap(p, total);
}
static void stress_splits(void) {
    const size_t page = 0x4000, total = 16 * page;
    unsigned char expected[16]; memset(expected, 0x57, sizeof(expected));
    ios_swap_n = ios_swap_nfree = 0; ios_swap_bump = 0;
    assert(ftruncate(ios_swap_fd, 0) == 0);
    assert(ftruncate(ios_swap_fd, ios_swap_logical) == 0);
    uint64_t off = ios_swap_take(total);
    char *p = mmap(NULL, total, PROT_READ | PROT_WRITE, MAP_SHARED, ios_swap_fd, off);
    assert(p != MAP_FAILED); memset(p, 0x57, total);
    ios_swap_n = 1;
    ios_swap_ext[0].va = p; ios_swap_ext[0].len = total;
    ios_swap_ext[0].off = off; ios_swap_ext[0].resv = 1;
    ios_swap_bytes = total;
    unsigned seed = 42;
    for (unsigned step = 0; step < 128; step++) {
        seed = seed * 1664525 + 1013904223;
        unsigned first = (seed >> 16) % 16;
        seed = seed * 1664525 + 1013904223;
        unsigned count = 1 + (seed >> 16) % (16 - first);
        pthread_mutex_lock(&virtual_mutex);
        unsigned long long done = ios_swap_pq_done, rotations = ios_swap_rotations + ios_swap_rotate_merged;
        assert(ios_swap_punch_resv(p + first * page, count * page));
        rotations = ios_swap_rotations + ios_swap_rotate_merged - rotations;
        assert(ios_swap_bytes == total && ios_swap_n <= 16);
        size_t covered = 0;
        for (unsigned k = 0; k < ios_swap_n; k++) {
            covered += ios_swap_ext[k].len;
            for (unsigned j = k + 1; j < ios_swap_n; j++)
                assert(ios_swap_ext[k].off + ios_swap_ext[k].len <= ios_swap_ext[j].off ||
                       ios_swap_ext[j].off + ios_swap_ext[j].len <= ios_swap_ext[k].off);
            for (unsigned j = 0; j < ios_swap_nfree; j++)
                assert(ios_swap_ext[k].off + ios_swap_ext[k].len <= ios_swap_free[j].off ||
                       ios_swap_free[j].off + ios_swap_free[j].len <= ios_swap_ext[k].off);
        }
        assert(covered == total);
        pthread_mutex_unlock(&virtual_mutex);
        for (unsigned k = 0; k < 16; k++)
            pattern(p + k * page, page, k >= first && k < first + count ? 0 : expected[k]);
        unsigned char value = 1 + step;
        memset(p + first * page, value, count * page);
        for (unsigned k = first; k < first + count; k++) expected[k] = value;
        finish(done + rotations);
        for (unsigned k = 0; k < 16; k++) pattern(p + k * page, page, expected[k]);
    }
    munmap(p, total);
}
int main(void) {
    char file[] = "/tmp/madeira-swap-rotate-XXXXXX";
    ios_swap_fd = mkstemp(file); assert(ios_swap_fd >= 0); unlink(file);
    ios_swap_logical = 16 << 20;
    unsetenv("MADEIRA_SWAP_PUNCH_DEFER");
    run_case(0, 0x10000, 0); /* whole */
    run_case(0, 0x8000, 0); /* prefix */
    run_case(0x8000, 0x8000, 0); /* suffix */
    run_case(0x4000, 0x8000, 0); /* middle */
    run_case(0x4000, 0x8000, 1); /* failed deferred punch: no reuse */
    assert(punch_calls == 21);
    stress_splits();
    puts("PASS: zeroed file-backed decommit, neighbours, delayed-punch isolation, reuse, split coverage and fallbacks");
    return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='madeira-swap-test-') as directory:
    path = Path(directory)
    (path / 'test.c').write_text(code)
    subprocess.run(['cc', '-g', '-O1', '-fsanitize=address,undefined', '-pthread',
                    '-Wno-unused-function', str(path / 'test.c'), '-o', str(path / 'test')], check=True)
    subprocess.run([str(path / 'test')], check=True, timeout=30)
