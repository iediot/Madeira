#!/usr/bin/env python3
"""Exercise bounded JIT environment inheritance and early child socket ownership."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / 'build/ntdll-unix/process_ios.c').read_text()
assert 'free(jit_environment);' in source
assert 'jit_params = *params;' in source and 'params = &jit_params;' in source
assert 'args->socketfd = -1;' in source
failure = source[source.index('if (ret) {\n        ERR("spawn_process: pthread_create failed:'):]
failure = failure[:failure.index('pthread_detach(')]
assert 'close(args->socketfd)' in failure and 'close(args->unixdir)' in failure
harness = r'''
#include <assert.h>
#include <stdio.h>
#include <sys/socket.h>
#include <unistd.h>
#include "process_startup_helpers.h"
static char *lookup(const char *key) {
    if (!strcmp(key, "WINE_IOS_JIT_RX")) return "0x1000";
    if (!strcmp(key, "WINE_IOS_JIT_RW")) return "0x2000";
    if (!strcmp(key, "WINE_IOS_JIT_SIZE")) return "896";
    return NULL;
}
static size_t utf16(const char *data, size_t bytes, uint16_t *out) {
    for (size_t i = 0; i < bytes; ++i) out[i] = (unsigned char)data[i];
    return bytes;
}
static int contains(uint16_t *env, const char *entry) {
    for (size_t at = 0; env[at]; ) {
        size_t n = 0, j;
        while (env[at + n]) ++n;
        if (n == strlen(entry)) {
            for (j = 0; j < n && env[at + j] == (unsigned char)entry[j]; ++j) {}
            if (j == n) return 1;
        }
        at += n + 1;
    }
    return 0;
}
int main(void) {
    uint16_t env[256], saved[256]; size_t size, output; unsigned added;
    const char empty[] = "";
    size = utf16(empty, sizeof(empty), env);
    uint16_t *out = ios_jit_environment(env, size, lookup, &output, &added);
    assert(out && added == 7 && out[output - 1] == 0 && contains(out, "WINE_IOS_JIT_RX=0x1000"));
    free(out);
    const char partial[] = "foo=bar\0wine_ios_jit_rx=custom\0WINE_IOS_JIT_RW=\0\0";
    size = utf16(partial, sizeof(partial), env); memcpy(saved, env, size * 2);
    for (int i = 0; i < 10000; ++i) {
        out = ios_jit_environment(env, size, lookup, &output, &added);
        assert(out && added == 2 && contains(out, "wine_ios_jit_rx=custom"));
        assert(contains(out, "WINE_IOS_JIT_RW=") && contains(out, "WINE_IOS_JIT_SIZE=896"));
        assert(!memcmp(env, saved, size * 2)); free(out);
    }
    const char complete[] = "WINE_IOS_JIT_RX=x\0WINE_IOS_JIT_SIZE=1\0WINE_IOS_JIT_RW=y\0\0";
    size = utf16(complete, sizeof(complete), env);
    assert(!ios_jit_environment(env, size, lookup, &output, &added) && !added);
    env[0] = 'x'; env[1] = 0;
    assert(!ios_jit_environment(env, 1, lookup, &output, &added));
    assert(!ios_jit_environment(env, 2, lookup, &output, &added)); /* no final terminator */
    assert(!ios_jit_environment(NULL, 0, lookup, &output, &added));
    assert(ios_child_socket_is_unclaimed("entry"));
    assert(ios_child_socket_is_unclaimed("window-bind"));
    assert(!ios_child_socket_is_unclaimed("server_init_process_child"));
    assert(!ios_child_socket_is_unclaimed("init_thread_stack"));
    assert(!ios_child_socket_is_unclaimed(NULL));
    int pair[2]; assert(!socketpair(AF_UNIX, SOCK_STREAM, 0, pair));
    int owned = dup(pair[1]); assert(owned >= 0); close(pair[1]);
    if (ios_child_socket_is_unclaimed("peb-mmap")) { close(owned); owned = -1; }
    char byte; assert(read(pair[0], &byte, 1) == 0 && owned == -1); close(pair[0]);
    puts("PASS: inherited overrides preserved, bounded UTF16, 10000 owned copies freed, early-boot socket EOF");
}
'''
with tempfile.TemporaryDirectory(prefix='madeira-startup-test-') as directory:
    path = Path(directory)
    c = path / 'startup.c'; c.write_text(harness)
    exe = path / 'startup'
    subprocess.run(['xcrun', 'clang', '-std=c11', '-O1', '-g', '-fsanitize=address,undefined',
                    '-I', str(root / 'build/ntdll-unix'), str(c), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
