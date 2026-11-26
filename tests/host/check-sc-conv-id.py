#!/usr/bin/env python3
"""madeira-bcd sc-conv-id, run from production source.

The PE shader cache's build directory (madeira_d3d12.c mad_sc_init) is keyed on
this DLL's converter stamp plus the identity of the converter linked into the
app, which the bridge (WineProcessBridge.m madeira_export_image_uuid) reads
from the app image's C strings and exports as MADEIRA_IR_CONVERTER_BUILD. The
app image's LC_UUID, which changes with every Xcode build, is only the
fallback. Checks:

1. The bridge's scan, compiled as it is in the app and run against test
   images: one "converter <16 hex>" literal is exported, none or two different
   ones leave the variable unset, malformed ones are ignored.
2. The PE identity block, compiled with stubs: with a converter identity the
   directory hash does not depend on the app image UUID (it survives an app
   rebuild) but changes with the converter identity and with this DLL's stamp;
   without one (an older app) it is byte-for-byte the previous formula, so
   that app keeps its cache; malformed or oversized values fall back.
3. The per-entry key still carries the DLL stamp and the runtime switch set,
   and the build still passes MAD_SC_CONVERTER_ID.
4. Provenance: the native archive the app links holds exactly one converter
   identity, and the built/staged DLL's stamp names the same one.
"""
from pathlib import Path
import re
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
pe = (root / 'madeira-d3d12/src/pe/madeira_d3d12.c').read_text()
bridge = (root / 'app/Madeira/WineProcessBridge.m').read_text()
fails = []


def check(name, ok):
    print(('ok   ' if ok else 'FAIL ') + name)
    if not ok:
        fails.append(name)


def cut(src, begin, end):
    b = src.index(begin)
    return src[b:src.index(end, b) + len(end)]


def run_c(code, name, extra=()):
    with tempfile.TemporaryDirectory() as td:
        c = Path(td) / (name + '.c')
        exe = Path(td) / name
        c.write_text(code)
        r = subprocess.run(['clang', '-std=gnu11', '-O1', '-Wall', '-Werror', '-Wno-unused-function',
                            *extra, '-o', str(exe), str(c)], capture_output=True, text=True)
        if r.returncode:
            print(r.stderr)
            return None
        r = subprocess.run([str(exe)], capture_output=True, text=True)
        return (r.returncode, r.stdout + r.stderr)


# ---------------------------------------------------------------- 1. bridge
fn = cut(bridge, 'static void madeira_export_image_uuid(void)\n{', '\n}\n')
check('bridge reads the converter identity from __TEXT,__cstring',
      'getsectiondata(mh, "__TEXT", "__cstring", &size)' in fn and 'MADEIRA_IR_CONVERTER_BUILD' in fn)
check('bridge still exports the app image UUID (the fallback)', 'setenv("MADEIRA_APP_IMAGE_UUID", hex, 1);' in fn)
check('bridge includes <mach-o/getsect.h>', '#include <mach-o/getsect.h>' in bridge)
start = bridge.index('madeira_export_image_uuid();')
check('bridge exports before the Wine processes start (beside MADEIRA_WIN32U)',
      'setenv("MADEIRA_WIN32U", "1", 1);' in bridge[start - 200:start])

bridge_harness = r'''
#include <dlfcn.h>
#include <mach-o/loader.h>
#include <mach-o/getsect.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
@@LITS@@
''' + fn + r'''
int main(void) {
    int i;
    for (i = 0; i < (int)(sizeof lits / sizeof lits[0]); i++) sink[i] = lits[i];   /* C strings in __cstring, as MAD_SC_BUILD is */
    unsetenv("MADEIRA_IR_CONVERTER_BUILD");
    madeira_export_image_uuid();
    const char *v = getenv("MADEIRA_IR_CONVERTER_BUILD");
    const char *u = getenv("MADEIRA_APP_IMAGE_UUID");
    printf("conv=%s uuid=%d\n", v ? v : "(unset)", u && strlen(u) == 32);
    return 0;
}
'''
cases = [
    ('one identity', ['"converter 0123456789abcdef"', '"unrelated"'], 'conv=converter 0123456789abcdef'),
    ('the same identity twice', ['"converter 0123456789abcdef"', '"[x] converter 0123456789abcdef"'],
     'conv=converter 0123456789abcdef'),
    ('two different identities', ['"converter 0123456789abcdef"', '"converter fedcba9876543210"'], 'conv=(unset)'),
    ('no identity (a build-time stamp instead)', ['"Nov 27 2026 01:00:00"', '"converter "'], 'conv=(unset)'),
    ('malformed ones ignored', ['"converter 0123456789ABCDEF"', '"converter 0123456789abcde"',
                                '"converter 0123456789abcdef0"', '"converter 00112233445566aa"'],
     'conv=converter 00112233445566aa'),
]
for name, lits, want in cases:
    lit_src = ('static const char *const lits[] = { %s };\n'
               'const char *volatile sink[%d];' % (', '.join(lits), len(lits)))
    r = run_c(bridge_harness.replace('@@LITS@@', lit_src), 'bridge_scan')
    check('bridge scan: %s -> %s' % (name, want), r is not None and r[0] == 0 and want in r[1] and 'uuid=1' in r[1])
    if r and want not in r[1]:
        print('   got: ' + r[1].strip())

# ---------------------------------------------------------------- 2. PE side
feed = cut(pe, 'static void mad_sc_feed(struct mad_sc_hash *h, const void *p, SIZE_T n) {', '\n}\n')
ident = cut(pe, '                char uuid[40] = "", conv[64] = "", ident[80];',
            '                mad_sc_feed(&h, ident, strlen(ident) + 1);')
check('PE identity block reads MADEIRA_IR_CONVERTER_BUILD before the UUID',
      ident.index('"MADEIRA_IR_CONVERTER_BUILD"') < ident.index('"MADEIRA_APP_IMAGE_UUID"'))
check('PE keys the directory on the DLL stamp first', 'mad_sc_feed(&h, g_sc_stamp, strlen(g_sc_stamp) + 1);' in ident)

pe_harness = r'''
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint64_t UINT64; typedef size_t SIZE_T; typedef uint32_t DWORD;
struct mad_sc_hash { UINT64 a, b; };
''' + feed + r'''
static const char *env_conv, *env_uuid;
static DWORD GetEnvironmentVariableA(const char *n, char *buf, DWORD cap) {
    const char *v = !strcmp(n, "MADEIRA_IR_CONVERTER_BUILD") ? env_conv : !strcmp(n, "MADEIRA_APP_IMAGE_UUID") ? env_uuid : 0;
    size_t l;
    if (!v) return 0;
    l = strlen(v);
    if (l + 1 > cap) return (DWORD)(l + 1);   /* as Windows: the size needed */
    memcpy(buf, v, l + 1);
    return (DWORD)l;
}
static char g_sc_stamp[96];
static UINT64 now(const char *stamp, const char *conv, const char *uuid) {
    struct mad_sc_hash h = { 0xcbf29ce484222325ull, 0x84222325cbf29ce4ull };
    snprintf(g_sc_stamp, sizeof g_sc_stamp, "%s", stamp); env_conv = conv; env_uuid = uuid;
    {
''' + ident + r'''
    }
    return h.a;
}
static UINT64 before(const char *stamp, const char *uuid_env) {   /* the formula this change replaced */
    struct mad_sc_hash h = { 0xcbf29ce484222325ull, 0x84222325cbf29ce4ull };
    char uuid[40] = "";
    DWORD ul;
    snprintf(g_sc_stamp, sizeof g_sc_stamp, "%s", stamp); env_conv = 0; env_uuid = uuid_env;
    ul = GetEnvironmentVariableA("MADEIRA_APP_IMAGE_UUID", uuid, sizeof uuid);
    if (!ul || ul >= sizeof uuid) uuid[0] = 0;
    mad_sc_feed(&h, g_sc_stamp, strlen(g_sc_stamp) + 1);
    mad_sc_feed(&h, uuid, strlen(uuid) + 1);
    return h.a;
}
#define S "madeira_d3d12 bc1 converter 71d7ea9b5dd2f88c"
#define C "converter 71d7ea9b5dd2f88c"
#define U1 "0123456789abcdef0123456789abcdef"
#define U2 "fedcba9876543210fedcba9876543210"
int main(void) {
    char longv[200]; memset(longv, 'a', sizeof longv - 1); longv[sizeof longv - 1] = 0; memcpy(longv, "converter ", 10);
    printf("survives_rebuild=%d\n", now(S, C, U1) == now(S, C, U2));
    printf("conv_changes=%d\n", now(S, C, U1) != now(S, "converter 0000000000000001", U1));
    printf("stamp_changes=%d\n", now(S, C, U1) != now("madeira_d3d12 bc1 converter 0000000000000001", C, U1));
    printf("legacy_same=%d\n", now(S, 0, U1) == before(S, U1) && now(S, 0, 0) == before(S, 0));
    printf("legacy_uuid_changes=%d\n", now(S, 0, U1) != now(S, 0, U2));
    printf("conv_not_legacy=%d\n", now(S, C, U1) != before(S, U1) && now(S, C, 0) != before(S, 0));
    printf("malformed_fallback=%d\n", now(S, "abc", U1) == before(S, U1) && now(S, "converter ", U1) == before(S, U1));
    printf("oversized_fallback=%d\n", now(S, longv, U1) == before(S, U1));
    return 0;
}
'''
r = run_c(pe_harness, 'pe_ident')
check('PE identity block compiles in the harness', r is not None and r[0] == 0)
out = r[1] if r else ''
for k, what in [('survives_rebuild', 'with a converter identity the cache survives an app rebuild (UUID ignored)'),
                ('conv_changes', 'a different converter identity is a different cache'),
                ('stamp_changes', "a different DLL converter stamp is a different cache"),
                ('legacy_same', 'without an identity the directory is the previous formula (an older app keeps its cache)'),
                ('legacy_uuid_changes', 'without an identity the UUID still separates app builds'),
                ('conv_not_legacy', 'the identity-keyed directory never equals a UUID-keyed one'),
                ('malformed_fallback', 'a malformed identity falls back to the UUID'),
                ('oversized_fallback', 'an oversized identity falls back to the UUID')]:
    check('PE: ' + what, (k + '=1') in out)

# ---------------------------------------------------------------- 3. keys and build
key = cut(pe, 'static void mad_sc_key(const struct madeira_ir_convert_args *a, UINT64 key[2]) {', '\n}\n')
check('entry key still carries the DLL stamp and the switch set',
      'mad_sc_feed(&h, g_sc_stamp, strlen(g_sc_stamp) + 1);' in key and 'mad_sc_feed(&h, g_sc_rt, sizeof g_sc_rt);' in key)
bpe = (root / 'build/madeira-d3d12/build-pe.sh').read_text()
check('build-pe.sh passes MAD_SC_CONVERTER_ID', '-DMAD_SC_CONVERTER_ID=' in bpe)
bios = (root / 'build/dxmt-ios/build.sh').read_text()
check('build/dxmt-ios/build.sh passes MADEIRA_IR_CONVERTER_ID to the converter',
      '-DMADEIRA_IR_CONVERTER_ID=' in bios)
mm = (root / 'madeira-d3d12/src/unix/madeira_ir_unix.mm').read_text()
check('the converter still holds its identity as the literal "converter " MADEIRA_IR_CONVERTER_ID',
      '#define MAD_SC_BUILD "converter " MADEIRA_IR_CONVERTER_ID' in mm)

# ---------------------------------------------------------------- 4. provenance
pat = re.compile(rb'converter ([0-9a-f]{16})\x00')
arch = root / 'build/dxmt-ios/libdxmt_combined.a'
if arch.exists():
    ids = sorted(set(m.group(1) for m in pat.finditer(arch.read_bytes())))
    check('libdxmt_combined.a holds exactly one converter identity (%s)' % b', '.join(ids).decode(), len(ids) == 1)
    for dll in ['build/madeira-d3d12/out-pe/madeira_d3d12.dll', 'app/Madeira/arm64ec-windows/madeira_d3d12.dll',
                'app/Madeira/arm64ec-windows/d3d12.dll']:
        p = root / dll
        if not p.exists():
            print('skip %s (absent)' % dll)
            continue
        m = re.search(rb'madeira_d3d12 bc1 converter ([0-9a-f]{16})\x00', p.read_bytes())
        check('%s stamp matches the app archive' % dll, bool(m) and len(ids) == 1 and m.group(1) == ids[0])
else:
    print('skip provenance: build/dxmt-ios/libdxmt_combined.a absent')

if fails:
    print('%d failure(s)' % len(fails))
    sys.exit(1)
print('all ok')
