#ifndef MADEIRA_PROCESS_STARTUP_HELPERS_H
#define MADEIRA_PROCESS_STARTUP_HELPERS_H
#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>

static int ios_child_socket_is_unclaimed(const char *stage)
{
    static const char *const early[] = { "entry", "guest-window-reserve", "virtual_alloc_teb",
                                       "windowed-peb-alloc", "peb-mmap", "window-bind" };
    unsigned i;
    if (!stage) return 0;
    for (i = 0; i < sizeof(early) / sizeof(early[0]); ++i)
        if (!strcmp(stage, early[i])) return 1;
    return 0;
}

/* Copy a bounded UTF-16 environment and fill only absent pool variables. The
 * caller owns the result and keeps the original process parameters untouched. */
static uint16_t *ios_jit_environment(const uint16_t *env, size_t units,
    char *(*lookup)(const char *), size_t *result_units, unsigned *added)
{
    static const char *const keys[] = { "WINE_IOS_JIT_RX", "WINE_IOS_JIT_SIZE", "WINE_IOS_JIT_RW" };
    const char *values[3];
    size_t used = 0, extra = 0, i, j, end, out_at;
    uint16_t *out;
    *added = 0;
    *result_units = 0;
    if (!env || !units || units > 4 * 1024 * 1024) return NULL;
    while (used < units && env[used]) {
        end = used;
        while (end < units && env[end]) ++end;
        if (end == units) return NULL;
        used = end + 1;
    }
    if (used == units) return NULL;
    for (i = 0; i < 3; ++i) {
        int present = 0;
        size_t n = strlen(keys[i]), at = 0;
        values[i] = lookup(keys[i]);
        if (!values[i]) continue;
        for (end = 0; end < used; end = at + 1) {
            at = end;
            while (env[at]) ++at;
            if (at - end < n + 1) continue;
            for (j = 0; j < n; ++j) {
                uint16_t c = env[end + j];
                if (c >= 'a' && c <= 'z') c -= 'a' - 'A';
                if (c != (unsigned char)keys[i][j]) break;
            }
            if (j == n && env[end + n] == '=') { present = 1; break; }
        }
        if (present) continue;
        if (strlen(values[i]) > 128) { *added = 0; return NULL; }
        *added |= 1u << i;
        extra += n + 1 + strlen(values[i]) + 1;
    }
    if (!*added) return NULL;
    out = malloc((used + extra + 1) * sizeof(*out));
    if (!out) { *added = 0; return NULL; }
    memcpy(out, env, used * sizeof(*out));
    out_at = used;
    for (i = 0; i < 3; ++i) {
        const char *p;
        if (!(*added & (1u << i))) continue;
        for (p = keys[i]; *p; ++p) out[out_at++] = (unsigned char)*p;
        out[out_at++] = '=';
        for (p = values[i]; *p; ++p) out[out_at++] = (unsigned char)*p;
        out[out_at++] = 0;
    }
    out[out_at++] = 0;
    *result_units = out_at;
    return out;
}
#endif
