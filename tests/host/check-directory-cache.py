#!/usr/bin/env python3
"""Compile the native directory-cache code with a per-process server model.

Exercise colliding server indexes, scan continuation on another thread,
process-local invalidations, mask changes, and process teardown/PEB reuse.
The pre-fix shared-cache variant must fail the first isolation assertion.
No Wine/device runtime is needed; directory discovery is modeled separately.
"""
from pathlib import Path
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / 'wine/dlls/ntdll/unix/file.c').read_text()


def function(signature):
    start = source.index(signature)
    return source[start:source.index('\n}', start) + 2] + '\n'


struct_start = source.index('struct ios_dir_cache\n')
struct_end = source.index('#else', struct_start)
parts = source[struct_start:struct_end]
parts += function('int ios_set_child_unix_cwd(')
parts += function('static struct ios_dir_cache *ios_get_dir_cache(')
parts += function('void ios_dir_cache_release(')
parts += function('static BOOLEAN ustring_equal(')
parts += function('static unsigned int get_cached_dir_data(')
parts += '\n#undef dir_data_cache\n#undef dir_data_cache_size\n'

prefix = r'''
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <unistd.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define WINE_IOS 1
#define TRUE 1
#define FALSE 0
#define STATUS_NO_MEMORY 12
#define STATUS_NO_SUCH_FILE 2
#define STATUS_NO_MORE_FILES 18
#define STATUS_SHARING_VIOLATION 32
#define max(a,b) ((a) > (b) ? (a) : (b))
#define TRACE(...) ((void)0)
#define FIXME(...) ((void)0)
typedef int BOOLEAN;
typedef unsigned short USHORT;
typedef uintptr_t HANDLE;
typedef struct { USHORT Length, MaximumLength; char *Buffer; } UNICODE_STRING;
struct dir_data { unsigned count, pos; int directory; UNICODE_STRING mask; };
static const unsigned dir_data_cache_initial_size = 256;
static pthread_mutex_t dir_mutex = PTHREAD_MUTEX_INITIALIZER;
#define mutex_lock pthread_mutex_lock
#define mutex_unlock pthread_mutex_unlock
static _Thread_local struct { void *Peb; } teb;
#define NtCurrentTeb() (&teb)
static unsigned freed, initialized;
static void free_dir_data(struct dir_data *d)
{
    if (!d) return;
    freed++;
    free(d->mask.Buffer);
    free(d);
}
static unsigned init_cached_dir_data(struct dir_data **out, int fd, const UNICODE_STRING *mask)
{
    struct dir_data *d = calloc(1, sizeof(*d));
    assert(d);
    initialized++;
    d->directory = fd; d->count = 3;
    if (mask) {
        d->mask = *mask; d->mask.Buffer = malloc(mask->Length);
        memcpy(d->mask.Buffer, mask->Buffer, mask->Length);
    }
    *out = d;
    return 0;
}
/* Each process's server can return index zero; free notices are also local. */
static int server_entry, server_free = -1;
static size_t reply_size;
#define SERVER_START_REQ(name) do { \
    struct { HANDLE handle; } request, *req = &request; \
    struct { int entry; } response, *reply = &response;
#define SERVER_END_REQ } while(0)
#define wine_server_obj_handle(h) (h)
#define wine_server_set_reply(req, buffer, size) do { \
    reply->entry = server_entry; reply_size = 0; \
    if (server_free >= 0) { buffer[0] = server_free; reply_size = sizeof(int); } \
} while (0)
#define wine_server_call(req) 0
#define wine_server_reply_size(reply) reply_size
'''
body = r'''
static int parent, child;
static UNICODE_STRING xnb = {5, 5, "*.xnb"}, all = {1, 1, "*"};
static struct dir_data *query(void *owner, int directory, UNICODE_STRING *mask, int restart)
{
    struct dir_data *data = NULL;
    teb.Peb = owner;
    mutex_lock(&dir_mutex);
    assert(!get_cached_dir_data(4, &data, directory, mask, restart));
    mutex_unlock(&dir_mutex);
    assert(data && data->directory == directory);
    return data;
}
static void *worker(void *unused)
{
    (void)unused;
    assert(query(&child, 200, NULL, 0)->pos == 2);
    return NULL;
}
int main(void)
{
    pthread_t thread;
    char path[4096];
    int original = open(".", O_RDONLY), temporary = open("/private/tmp", O_RDONLY);
    assert(original >= 0 && temporary >= 0);
    assert(!ios_set_child_unix_cwd(temporary));
    assert(getcwd(path, sizeof(path)) && !strcmp(path, "/private/tmp"));
    assert(ios_set_child_unix_cwd(-1) == -1 && errno == EBADF);
    assert(!ios_set_child_unix_cwd(original));
    close(original); close(temporary);
    struct dir_data *a = query(&parent, 100, &xnb, 0);
    a->pos = 3; /* parent's scan is exhausted */
    struct dir_data *b = query(&child, 200, &xnb, 0);
    assert(b != a && b->pos == 0);
    b->pos = 2;
    assert(!pthread_create(&thread, NULL, worker, NULL));
    assert(!pthread_join(thread, NULL));
    assert(query(&parent, 100, NULL, 0) == a && a->pos == 3);
    /* Closing/reusing child's slot zero must leave parent's slot alive. */
    server_free = 0;
    b = query(&child, 201, &all, 0);
    server_free = -1;
    assert(freed == 1 && b->pos == 0);
    assert(query(&parent, 100, NULL, 0) == a && a->pos == 3);
    b = query(&child, 201, &xnb, 1);
    assert(freed == 2 && b->mask.Length == 5 && b->pos == 0);
    ios_dir_cache_release(&child);
    assert(freed == 3);
    assert(query(&parent, 100, NULL, 0) == a && a->pos == 3);
    b = query(&child, 202, &xnb, 0); /* reused PEB starts empty */
    assert(b->pos == 0);
    ios_dir_cache_release(&child);
    ios_dir_cache_release(&child); /* repeated teardown is harmless */
    ios_dir_cache_release(&parent);
    assert(!ios_dir_caches && freed == initialized);
    puts("directory cache isolation, continuation, invalidation and teardown passed");
}
'''
# Keep the actual function, but replace its owner lookup with the old shared
# entries/size. This reproduces the bug without depending on git history.
old = parts.replace('struct ios_dir_cache *cache = ios_get_dir_cache();',
                    'static struct ios_dir_cache shared; struct ios_dir_cache *cache = &shared;')
with tempfile.TemporaryDirectory(prefix='madeira-dir-cache-') as tmp:
    tmp = Path(tmp)
    for name, code in [('fixed', parts), ('shared', old)]:
        c = tmp / f'{name}.c'
        exe = tmp / name
        c.write_text(prefix + code + body)
        subprocess.run([os.environ.get('CC', 'cc'), '-std=c11', '-g',
                        '-fsanitize=address,undefined', '-pthread', str(c), '-o', str(exe)], check=True)
        run = subprocess.run([str(exe)], text=True, capture_output=True)
        if name == 'fixed':
            assert run.returncode == 0, run.stdout + run.stderr
            print(run.stdout.strip())
        else:
            assert run.returncode != 0 and 'data->directory == directory' in run.stderr, run.stderr
            print('pre-fix shared cache reproduces wrong-directory failure')
