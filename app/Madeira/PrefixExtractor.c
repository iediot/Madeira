// Minimal ustar/GNU tar extractor over gzip or xz. Handles the subset of tar
// produced by /usr/bin/tar on macOS and by GNU tar: regular files (type '0'),
// directories (type '5'), GNU long names (type 'L', applied to the next entry),
// pax extended headers (type 'x': `path=` applied to the next entry, the rest
// ignored) and global pax headers (type 'g', ignored).

#include "PrefixExtractor.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/stat.h>
#include <zlib.h>
#include <compression.h>

#define BLOCK 512
#define NAME_MAX_LEN 4096

static long long parse_octal(const char *s, size_t n) {
    long long v = 0;
    for (size_t i = 0; i < n && s[i]; i++) {
        if (s[i] == ' ' || s[i] == 0) continue;
        if (s[i] < '0' || s[i] > '7') return -1;
        v = (v << 3) | (s[i] - '0');
    }
    return v;
}

static int mkdir_p(const char *path) {
    char buf[NAME_MAX_LEN + 1200];
    strncpy(buf, path, sizeof(buf) - 1);
    buf[sizeof(buf) - 1] = 0;
    for (char *p = buf + 1; *p; p++) {
        if (*p == '/') {
            *p = 0;
            if (mkdir(buf, 0755) != 0 && errno != EEXIST) return -1;
            *p = '/';
        }
    }
    if (mkdir(buf, 0755) != 0 && errno != EEXIST) return -1;
    return 0;
}

// ---- byte sources -------------------------------------------------------

typedef struct reader {
    int kind;                       // 0 = gzip, 1 = xz
    gzFile gz;
    int fd;
    compression_stream cs;
    int cs_live, in_eof, out_eof;
    uint8_t in[64 * 1024];
    uint8_t out[256 * 1024];
    size_t out_pos, out_len;
} reader;

// Read exactly n bytes; returns n, or fewer at the end of the stream / on error.
static size_t read_exact(reader *r, void *dst, size_t n) {
    if (r->kind == 0) {
        size_t done = 0;
        while (done < n) {
            int got = gzread(r->gz, (char *)dst + done, (unsigned)(n - done));
            if (got <= 0) break;
            done += (size_t)got;
        }
        return done;
    }
    size_t done = 0;
    while (done < n) {
        if (r->out_pos < r->out_len) {
            size_t take = r->out_len - r->out_pos;
            if (take > n - done) take = n - done;
            memcpy((char *)dst + done, r->out + r->out_pos, take);
            r->out_pos += take; done += take;
            continue;
        }
        if (r->out_eof) break;
        // Refill the decoder's output.
        r->cs.dst_ptr = r->out;
        r->cs.dst_size = sizeof(r->out);
        for (;;) {
            if (r->cs.src_size == 0 && !r->in_eof) {
                ssize_t got = read(r->fd, r->in, sizeof(r->in));
                if (got < 0) return done;
                if (got == 0) r->in_eof = 1;
                r->cs.src_ptr = r->in;
                r->cs.src_size = (size_t)got;
            }
            compression_status st = compression_stream_process(&r->cs, r->in_eof ? COMPRESSION_STREAM_FINALIZE : 0);
            if (st == COMPRESSION_STATUS_ERROR) { r->out_eof = 1; break; }
            if (st == COMPRESSION_STATUS_END) r->out_eof = 1;
            if (r->cs.dst_size < sizeof(r->out) || r->out_eof) break;
        }
        r->out_pos = 0;
        r->out_len = sizeof(r->out) - r->cs.dst_size;
        if (r->out_len == 0 && r->out_eof) break;
    }
    return done;
}

static int skip_blocks(reader *r, long long size) {
    char buf[BLOCK];
    long long pad = (size + BLOCK - 1) / BLOCK * BLOCK;
    while (pad > 0) {
        if (read_exact(r, buf, BLOCK) != BLOCK) return -1;
        pad -= BLOCK;
    }
    return 0;
}

// Read a header's payload (a long name or pax records) into out (NUL-terminated).
static int read_payload(reader *r, long long size, char *out, size_t cap) {
    char buf[BLOCK];
    long long pad = (size + BLOCK - 1) / BLOCK * BLOCK;
    size_t len = 0;
    while (pad > 0) {
        if (read_exact(r, buf, BLOCK) != BLOCK) return -1;
        size_t want = size > BLOCK ? BLOCK : (size_t)size;
        if (size > 0 && len + want < cap) { memcpy(out + len, buf, want); len += want; }
        size -= (long long)want; pad -= BLOCK;
    }
    out[len < cap ? len : cap - 1] = 0;
    return 0;
}

// ---- the archive walk ---------------------------------------------------

// strip: a leading path component to drop (the prefix template's "prefix"), or NULL.
static int extract_tar(reader *r, const char *dest_dir, const char *strip, const char *tag) {
    if (mkdir_p(dest_dir) != 0) {
        fprintf(stderr, "[%s] mkdir_p dest failed: %s\n", tag, dest_dir);
        return -1;
    }

    char header[BLOCK];
    char buf[BLOCK];
    static char longname[NAME_MAX_LEN];
    int have_longname = 0;
    int files = 0, dirs = 0;
    size_t strip_len = strip ? strlen(strip) : 0;

    for (;;) {
        size_t n = read_exact(r, header, BLOCK);
        if (n == 0) break;
        if (n != BLOCK) {
            fprintf(stderr, "[%s] short header read: %zu\n", tag, n);
            return -1;
        }
        // End-of-archive: two zero blocks. Bail on any all-zero block.
        int all_zero = 1;
        for (int i = 0; i < BLOCK; i++) if (header[i]) { all_zero = 0; break; }
        if (all_zero) break;

        long long size = parse_octal(header + 124, 12);
        char type = header[156];
        if (size < 0) { fprintf(stderr, "[%s] bad size field\n", tag); return -1; }

        if (type == 'L') {                       // GNU long name for the next entry
            if (read_payload(r, size, longname, sizeof(longname)) != 0) return -1;
            have_longname = 1;
            continue;
        }
        if (type == 'x') {                       // pax: only path= matters here
            static char pax[NAME_MAX_LEN * 2];
            if (read_payload(r, size, pax, sizeof(pax)) != 0) return -1;
            for (char *p = pax; *p; ) {
                char *nl = strchr(p, '\n');
                char *sp = strchr(p, ' ');
                if (!nl || !sp || sp > nl) break;
                if (!strncmp(sp + 1, "path=", 5)) {
                    size_t l = (size_t)(nl - (sp + 6));
                    if (l < sizeof(longname)) { memcpy(longname, sp + 6, l); longname[l] = 0; have_longname = 1; }
                }
                p = nl + 1;
            }
            continue;
        }
        if (type == 'g' || type == 'K') {        // global pax / GNU long link name
            if (skip_blocks(r, size) != 0) return -1;
            continue;
        }

        // ustar stores a path longer than 100 bytes as prefix (offset 345, up to
        // 155 bytes) + '/' + name. Without the prefix every leading directory of
        // such a path is lost and the file lands in the wrong place. Only POSIX
        // headers ("ustar\0") have the field; old GNU ones ("ustar  ") keep
        // other data there, and use 'L' records instead (handled above).
        char name[NAME_MAX_LEN] = {0};
        if (have_longname) {
            strncpy(name, longname, sizeof(name) - 1);
            have_longname = 0;
        } else {
            memcpy(name, header, 100);
            if (memcmp(header + 257, "ustar", 6) == 0 && header[345]) {
                char pfx[156] = {0}, base[101] = {0};
                memcpy(pfx, header + 345, 155);
                memcpy(base, header, 100);
                snprintf(name, sizeof(name), "%s/%s", pfx, base);
            }
        }
        if (strstr(name, "..")) {                // never write outside dest_dir
            fprintf(stderr, "[%s] refusing path with '..': %s\n", tag, name);
            if (skip_blocks(r, size) != 0) return -1;
            continue;
        }

        const char *relname = name;
        while (relname[0] == '.' && relname[1] == '/') relname += 2;
        if (strip && !strncmp(relname, strip, strip_len) && (relname[strip_len] == '/' || !relname[strip_len]))
            relname += strip_len + (relname[strip_len] == '/');

        char outpath[NAME_MAX_LEN + 1200];
        if (*relname) snprintf(outpath, sizeof(outpath), "%s/%s", dest_dir, relname);
        else snprintf(outpath, sizeof(outpath), "%s", dest_dir);

        size_t nl = strlen(name);
        if (type == '5' || (type == 0 && nl && name[nl - 1] == '/')) {
            if (*relname) {
                if (mkdir_p(outpath) != 0) {
                    fprintf(stderr, "[%s] mkdir %s: %s\n", tag, outpath, strerror(errno));
                    return -1;
                }
                dirs++;
            }
        } else if (type == '0' || type == 0) {
            // Regular file: ensure parent dir, then write size bytes.
            char parent[sizeof(outpath)];
            strncpy(parent, outpath, sizeof(parent) - 1);
            parent[sizeof(parent) - 1] = 0;
            char *slash = strrchr(parent, '/');
            if (slash) { *slash = 0; mkdir_p(parent); }

            int fd = open(outpath, O_WRONLY | O_CREAT | O_TRUNC, 0644);
            if (fd < 0) {
                fprintf(stderr, "[%s] open %s: %s\n", tag, outpath, strerror(errno));
                return -1;
            }
            long long remaining = size;
            while (remaining > 0) {
                size_t want = remaining < BLOCK ? (size_t)remaining : BLOCK;
                if (read_exact(r, buf, BLOCK) != BLOCK) {
                    fprintf(stderr, "[%s] short data read for %s\n", tag, relname);
                    close(fd);
                    return -1;
                }
                if (write(fd, buf, want) != (ssize_t)want) {
                    fprintf(stderr, "[%s] write %s: %s\n", tag, outpath, strerror(errno));
                    close(fd);
                    return -1;
                }
                remaining -= (long long)want;
            }
            close(fd);
            files++;
        } else {
            // Unknown type (links, devices): skip its data blocks.
            if (skip_blocks(r, size) != 0) break;
        }
    }

    fprintf(stderr, "[%s] extracted %d files, %d dirs to %s\n", tag, files, dirs, dest_dir);
    return 0;
}

int madeira_extract_prefix_tgz(const char *tgz_path, const char *dest_dir) {
    static reader r;
    memset(&r, 0, sizeof(r));
    r.kind = 0;
    r.gz = gzopen(tgz_path, "rb");
    if (!r.gz) {
        fprintf(stderr, "[prefix-extract] gzopen failed: %s\n", tgz_path);
        return -1;
    }
    // Strip leading "prefix/" so files land directly under dest_dir.
    int ret = extract_tar(&r, dest_dir, "prefix", "prefix-extract");
    gzclose(r.gz);
    return ret;
}

int madeira_extract_tar_xz(const char *path, const char *dest_dir) {
    reader *r = calloc(1, sizeof(*r));
    if (!r) return -1;
    r->kind = 1;
    r->fd = open(path, O_RDONLY);
    if (r->fd < 0) {
        fprintf(stderr, "[xz-extract] open %s: %s\n", path, strerror(errno));
        free(r);
        return -1;
    }
    if (compression_stream_init(&r->cs, COMPRESSION_STREAM_DECODE, COMPRESSION_LZMA) != COMPRESSION_STATUS_OK) {
        fprintf(stderr, "[xz-extract] compression_stream_init failed\n");
        close(r->fd);
        free(r);
        return -1;
    }
    r->cs_live = 1;
    int ret = extract_tar(r, dest_dir, NULL, "xz-extract");
    compression_stream_destroy(&r->cs);
    close(r->fd);
    free(r);
    return ret;
}
