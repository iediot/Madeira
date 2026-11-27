#!/usr/bin/env python3
"""[sc-hide] / [sc-park] / [sc-cpu]: Social Club's helper goes quiet while a game runs.

Log 1127-0105-wave1: SocialClubHelper.exe's Chrome_InProcRendererThread was the
busiest thread of the session during play with the 50 ms wait floor on; the
launcher hides the overlay but the off-screen browser is never told. Checked
here without a Wine run and without the owner's helper binary:

  1. tests/host/sc-hide.S assembles (GNU as for x86-64, PE or ELF) to exactly
     ios_sch302_hide_blob; the host-address immediates sit at
     ios_sch302_hide_imm; the stub entry points match the hook destinations;
     every rip-relative / rel32 target is the IAT slot or helper RVA it names.
  2. The visibility stub and the three counter stubs run (x86-64, natively or
     under Rosetta) inside a fake image laid out at the 2.4.0.302 RVAs, against
     fake CEF structs: WasHidden(want) reaches every browser found, each
     reference taken is released, `applied` follows `want`, nothing is called
     when they match, the original cef_post_delayed_task call gets its three
     arguments, callee-saved registers survive, the counter stubs fall through
     to the original function bodies.
  3. ios_sc_hide_mismatch / ios_sc_hide_patch (virtual_ios.c) compiled natively:
     they patch a synthetic image carrying the guarded bytes, write the host
     address into every immediate and the four hooks, and refuse (writing
     nothing) without the [sc-rph] loop, with other entry bytes, a non-zero tail
     or another build.
  4. The policy (process_ios.c ios_sc_poll / ios_sc_wait and the thread-name
     table) compiled natively with a fake TEB and clock: nothing before the
     arming; then want = 1, park only once WasHidden was applied (or after
     10 s); waits of 1 ms .. 5 s become 5 s, longer ones untouched; a wait
     ended early by a signal and a SendMessage counted by the wineserver lift
     the park for 5 s; no park without moving Presents; the WATCHDOG ends it
     after 3 s without Presents and re-arms after 60 s of Presents; a helper
     Show() makes the pages visible for the yield time; the game gone ends both.
  5. Wiring: the hook sites in sync.c's wait functions call ios_wait_floor; the
     wineserver's send_message counts only cross-process messages that wait for
     a reply; the RDR2 profile enables MADEIRA_SC_HIDE / MADEIRA_SC_PARK.
Parts needing a tool that is missing print SKIP and are not counted as passing.
"""
from pathlib import Path
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
virtual = (root / 'build/ntdll-unix/virtual_ios.c').read_text()
process = (root / 'build/ntdll-unix/process_ios.c').read_text()
server = (root / 'build/ntdll-unix/server_ios.c').read_text()
queue = (root / 'build/wineserver/queue_ios.c').read_text()
sync = (root / 'wine/dlls/ntdll/unix/sync.c').read_text()
library = (root / 'app/Madeira/Library.swift').read_text()
asm_src = root / 'tests/host/sc-hide.S'

failures = []
skips = []


def check(cond, what):
    if not cond:
        failures.append(what)
        print('FAIL', what)


def define(src, name):
    m = re.search(r'#define %s\s+\(?(0x[0-9a-fA-F]+|[0-9]+)u?' % name, src)
    return int(m.group(1), 0)


def array_bytes(src, name):
    start = src.index('static const unsigned char ' + name + '[')
    body = src[start:src.index('};', start) + 2]
    return bytes(int(x, 16) for x in re.findall(r'0x([\da-fA-F]{2})', body[body.index('{'):]))


def section(src, start_marker, end_marker):
    a = src.index(start_marker)
    b = src.index(end_marker, a)
    return src[a:b]


def tool(*names):
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


blob = array_bytes(virtual, 'ios_sch302_hide_blob')
imm = [int(x, 0) for x in re.search(r'ios_sch302_hide_imm\[4\] = \{([^}]*)\}', virtual).group(1).split(',')]
BLOB = define(virtual, 'IOS_SCH302_HIDE_BLOB')
SHOW_STUB = define(virtual, 'IOS_SCH302_HIDE_SHOW_STUB')
HIDE_STUB = define(virtual, 'IOS_SCH302_HIDE_HIDE_STUB')
PAINT_STUB = define(virtual, 'IOS_SCH302_HIDE_PAINT_STUB')
LOOP_INIT = define(virtual, 'IOS_SCH302_LOOP_INIT')
SHOW_FN = define(virtual, 'IOS_SCH302_SHOW_FN')
HIDE_FN = define(virtual, 'IOS_SCH302_HIDE_FN')
ON_PAINT = define(virtual, 'IOS_SCH302_ON_PAINT')
PUMP_CALL = LOOP_INIT + 276
IAT_GET_BY_ID, IAT_POST = 0x1a5b28, 0x1a5b10
PLACEHOLDER = (0x5ac0de5ac0de5ac0).to_bytes(8, 'little')

# ---- 1. bytes and displacements -------------------------------------------------
for off in imm:
    check(blob[off:off + 8] == PLACEHOLDER, 'host-address immediate at blob+0x%x' % off)
check(SHOW_STUB - BLOB == 0xa0 and HIDE_STUB - BLOB == 0xc0 and PAINT_STUB - BLOB == 0xe0, 'stub entry offsets')
check(BLOB + len(blob) <= BLOB + define(virtual, 'IOS_SCH302_HIDE_ZERO') <= 0x1a5000, 'blob inside the guarded zero tail')
check(BLOB >= define(virtual, 'IOS_SCH302_IPC_DIRECT') + 13, 'blob after the [sc-rph] IPC blobs')


def rel32(at):
    return int.from_bytes(blob[at:at + 4], 'little', signed=True)


check(blob[0x33:0x35] == b'\xff\x15' and BLOB + 0x39 + rel32(0x35) == IAT_GET_BY_ID, 'vis calls the get_browser_by_identifier IAT slot')
check(blob[0x99:0x9b] == b'\xff\x25' and BLOB + 0x9f + rel32(0x9b) == IAT_POST, 'vis tail-jumps through the cef_post_delayed_task IAT slot')
check(blob[0x65:0x67] == b'\xff\x90' and int.from_bytes(blob[0x67:0x6b], 'little') == 0x138, 'was_hidden at +0x138')
check(blob[0x4a:0x4d] == b'\xff\x57\x30', 'get_host at +0x30')
check(blob[0xb5] == 0xe9 and BLOB + 0xba + rel32(0xb6) == SHOW_FN + 6, 'show stub returns to Show()+6')
check(blob[0xd5] == 0xe9 and BLOB + 0xda + rel32(0xd6) == HIDE_FN + 6, 'hide stub returns to Hide()+6')
check(blob[0xf4] == 0xe9 and BLOB + 0xf9 + rel32(0xf5) == ON_PAINT + 5, 'paint stub returns to on_paint+5')
loop = array_bytes(virtual, 'ios_sch302_loop_thunk')
check(loop[276:282] == b'\xff\x15\x26\x16\x00\x00' and LOOP_INIT + 282 + 0x1626 == IAT_POST,
      'the pump call is the [sc-rph] loop re-post through the cef_post_delayed_task IAT slot')

gas = tool('x86_64-w64-mingw32-as', 'x86_64-linux-gnu-as') or (tool('as') if platform.machine() == 'x86_64' and sys.platform.startswith('linux') else None)
objcopy = tool('x86_64-w64-mingw32-objcopy', 'x86_64-linux-gnu-objcopy', 'objcopy', 'gobjcopy')
tmp = Path(tempfile.mkdtemp(prefix='sc-hide-'))
if gas and objcopy:
    subprocess.run([gas, '-o', str(tmp / 'h.o'), str(asm_src)], check=True)
    subprocess.run([objcopy, '-O', 'binary', '-j', '.text', str(tmp / 'h.o'), str(tmp / 'h.bin')], check=True)
    built = (tmp / 'h.bin').read_bytes()[:len(blob)]
    check(built == blob, 'sc-hide.S assembles to ios_sch302_hide_blob')
else:
    skips.append('assembling sc-hide.S (no x86-64 GNU as/objcopy)')

# ---- 2. execute the stubs (x86-64) ----------------------------------------------
harness = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <sys/mman.h>
#define MS __attribute__((ms_abi))
typedef struct { uint64_t size; void *add_ref; void *release; void *one; void *any; } base_t;
struct vis { volatile int want, applied, browsers, passes, show, hide, pumps, paints; } V;
static int refs, hidden_calls, last_hidden[40], post_calls, post_tid, post_delay; static void *post_task;
typedef struct { base_t b; void *is_valid; void *get_host; char pad[0x60]; int id; } browser_t;
typedef struct { base_t b; char pad[0x200]; int id; } host_t;
static browser_t br[40]; static host_t ho[40];
MS static int release(void *self) { refs--; return 1; }
MS static void *get_host(browser_t *b) { refs++; return &ho[b->id]; }
MS static void was_hidden(host_t *h, int hidden) { hidden_calls++; last_hidden[h->id] = hidden; }
MS static void *get_by_id(int id) { if (id == 1 || id == 3 || id == 32) { refs++; return &br[id]; } if (id == 33) abort(); return 0; }
MS static int post(int tid, void *task, long long delay) { post_calls++; post_tid = tid; post_task = task; post_delay = (int)delay; return 1; }
typedef MS int (*vis_fn)(int, void *, int);
typedef MS int (*hook_fn)(void);
int main(int argc, char **argv)
{
    FILE *f = fopen(argv[1], "rb"); unsigned char blob[512]; size_t n = fread(blob, 1, sizeof(blob), f); fclose(f);
    size_t size = 0x1a6000; unsigned char *img = mmap(0, size, PROT_READ|PROT_WRITE|PROT_EXEC, MAP_PRIVATE|MAP_ANON, -1, 0);
    uint64_t host = (uint64_t)(uintptr_t)&V; int i, k, fails = 0;
    if (img == MAP_FAILED) { printf("SKIP no RWX mapping\n"); return 77; }
    memcpy(img + BLOB, blob, n);
    { unsigned offs[4] = { IMM0, IMM1, IMM2, IMM3 }; for (k = 0; k < 4; k++) memcpy(img + BLOB + offs[k], &host, 8); }
    *(void **)(img + IAT_GET) = (void *)get_by_id; *(void **)(img + IAT_POST) = (void *)post;
    /* original bodies after the overwritten prologues */
    { unsigned char e[] = { 0x48,0x83,0xc4,0x30, 0x5b, 0xb8,0x34,0x12,0,0, 0xc3 };           /* add rsp,30h; pop rbx; mov eax,1234h; ret */
      memcpy(img + SHOW_FN + 6, e, sizeof(e)); memcpy(img + HIDE_FN + 6, e, sizeof(e)); }
    { unsigned char e[] = { 0x41,0x5e, 0x5d, 0x5b, 0xb8,0x78,0x56,0,0, 0xc3 };               /* pop r14; pop rbp; pop rbx; mov eax,5678h; ret */
      memcpy(img + ON_PAINT + 5, e, sizeof(e)); }
    for (i = 0; i < 40; i++) { br[i].b.size = sizeof(browser_t); br[i].b.release = (void *)release; br[i].get_host = (void *)get_host; br[i].id = i;
                               ho[i].b.size = sizeof(host_t); ho[i].b.release = (void *)release; ho[i].id = i; last_hidden[i] = -1;
                               *(void **)((char *)&ho[i] + 0x138) = (void *)was_hidden; }
    vis_fn vis = (vis_fn)(img + BLOB);
#define T(c, m) do { if (!(c)) { printf("FAIL %s\n", m); fails++; } } while (0)
    vis(6, (void *)0x1234, 10);
    T(V.pumps == 1 && hidden_calls == 0 && post_calls == 1 && post_tid == 6 && post_task == (void *)0x1234 && post_delay == 10, "idle pump: no CEF call, original post");
    V.want = 1; vis(6, (void *)0x5678, 10);
    T(hidden_calls == 3 && last_hidden[1] == 1 && last_hidden[3] == 1 && last_hidden[32] == 1, "WasHidden(1) on browsers 1, 3, 32");
    T(V.applied == 1 && V.browsers == 3 && V.passes == 1 && refs == 0, "applied, browsers counted, every reference released");
    T(post_calls == 2 && post_tid == 6 && post_task == (void *)0x5678 && post_delay == 10, "original post after a pass");
    vis(6, (void *)0x5678, 10); T(hidden_calls == 3 && V.passes == 1, "no second pass while applied == want");
    br[3].b.size = 0x30; ho[1].b.size = 0x138;
    V.want = 0; vis(6, (void *)0x9, 10);
    T(hidden_calls == 4 && last_hidden[32] == 0 && last_hidden[1] == 1 && last_hidden[3] == 1 && refs == 0, "size checks skip short structs, references still released");
    T(V.applied == 0 && V.browsers == 1 && V.passes == 2, "WasHidden(0) pass");
    T(((hook_fn)(img + SHOW_STUB))() == 0x1234 && V.show == 1, "show counter falls through to Show()");
    T(((hook_fn)(img + HIDE_STUB))() == 0x1234 && V.hide == 1, "hide counter falls through to Hide()");
    T(((hook_fn)(img + PAINT_STUB))() == 0x5678 && V.paints == 1, "paint counter falls through to on_paint");
    printf(fails ? "harness: %d failures\n" : "harness: ok\n", fails);
    return fails ? 1 : 0;
}
'''
cc_x64 = None
if platform.machine() == 'x86_64':
    cc_x64 = [tool('cc', 'clang', 'gcc')]
elif sys.platform == 'darwin' and tool('clang') and subprocess.run(['arch', '-x86_64', '/usr/bin/true']).returncode == 0:
    cc_x64 = ['clang', '-arch', 'x86_64']
if cc_x64 and cc_x64[0]:
    (tmp / 'blob.bin').write_bytes(blob)
    (tmp / 'h.c').write_text(harness)
    defs = ['-DBLOB=0x%x' % BLOB, '-DIAT_GET=0x%x' % IAT_GET_BY_ID, '-DIAT_POST=0x%x' % IAT_POST,
            '-DSHOW_FN=0x%x' % SHOW_FN, '-DHIDE_FN=0x%x' % HIDE_FN, '-DON_PAINT=0x%x' % ON_PAINT,
            '-DSHOW_STUB=0x%x' % (SHOW_STUB - BLOB + BLOB), '-DHIDE_STUB=0x%x' % HIDE_STUB, '-DPAINT_STUB=0x%x' % PAINT_STUB] + \
           ['-DIMM%d=0x%x' % (k, v) for k, v in enumerate(imm)]
    subprocess.run(cc_x64 + ['-O1', '-o', str(tmp / 'h')] + defs + [str(tmp / 'h.c')], check=True)
    run = (['arch', '-x86_64'] if platform.machine() != 'x86_64' else []) + [str(tmp / 'h'), str(tmp / 'blob.bin')]
    r = subprocess.run(run, capture_output=True, text=True)
    print(r.stdout.strip())
    if r.returncode == 77:
        skips.append('executing the stubs (no RWX mapping)')
    else:
        check(r.returncode == 0, 'stubs execute correctly against fake CEF structs')
else:
    skips.append('executing the stubs (no x86-64 compiler or Rosetta)')

# ---- 3. the patch, compiled natively ----------------------------------------------
patch_src = section(virtual, '#define IOS_SCH302_HIDE_BLOB', '/* Does the calling pseudo-process have socialclub.dll mapped?')
patch_harness = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
typedef unsigned short WCHAR; typedef size_t SIZE_T; typedef uint64_t ULONG64; typedef uintptr_t ULONG_PTR;
typedef struct { unsigned short Length, MaximumLength; WCHAR *Buffer; } UNICODE_STRING;
typedef struct { struct { unsigned short Machine; unsigned int TimeDateStamp; } FileHeader;
                 struct { unsigned int SizeOfImage; } OptionalHeader; } IMAGE_NT_HEADERS;
#define IMAGE_FILE_MACHINE_AMD64 0x8664
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define dprintf(fd, ...) (printf(__VA_ARGS__))
static int ios_sc_path_is_helper(const WCHAR *p, size_t n) { return 1; }
static int host_var;
void *ios_sc_vis_address(void) { return &host_var; }
#define IOS_SCH302_STAMP 0x%(stamp)xu
#define IOS_SCH302_SIZE 0x%(size)xu
#define IOS_SCH302_LOOP_INIT 0x%(loop_init)xu
#define IOS_SCH302_IPC_DIRECT 0x%(ipc_direct)xu
static const unsigned char ios_sch302_loop_thunk[%(loop_len)d] = { %(loop)s };
%(patch)s
static unsigned char img[0x26e000], ref[0x26e000];
static WCHAR nm[] = { 'x', 0 };
static UNICODE_STRING name = { 2, 4, nm };
static void build(IMAGE_NT_HEADERS *nt)
{
    memset(img, 0, sizeof(img));
    memcpy(img + IOS_SCH302_LOOP_INIT, ios_sch302_loop_thunk, sizeof(ios_sch302_loop_thunk));
    memcpy(img + IOS_SCH302_SHOW_FN, ios_sch302_window_entry, sizeof(ios_sch302_window_entry));
    memcpy(img + IOS_SCH302_HIDE_FN, ios_sch302_window_entry, sizeof(ios_sch302_window_entry));
    memcpy(img + IOS_SCH302_ON_PAINT, ios_sch302_paint_entry, sizeof(ios_sch302_paint_entry));
    nt->FileHeader.Machine = IMAGE_FILE_MACHINE_AMD64; nt->FileHeader.TimeDateStamp = IOS_SCH302_STAMP;
    nt->OptionalHeader.SizeOfImage = IOS_SCH302_SIZE;
}
static int unchanged(void) { return !memcmp(img, ref, sizeof(img)); }
int main(void)
{
    IMAGE_NT_HEADERS nt; int fails = 0, k; uint64_t host = (uint64_t)(uintptr_t)&host_var;
#define T(c, m) do { if (!(c)) { printf("FAIL %%s\n", m); fails++; } } while (0)
    setenv("MADEIRA_SC_HIDE", "1", 1);
    build(&nt); ios_sc_hide_patch((char *)img, sizeof(img), &nt, &name);
    T(!memcmp(img + IOS_SCH302_HIDE_BLOB + 0x10 + 0x10, ios_sch302_hide_blob + 0x20, 0x10), "blob written");
    for (k = 0; k < 4; k++) T(!memcmp(img + IOS_SCH302_HIDE_BLOB + ios_sch302_hide_imm[k], &host, 8), "host address written");
    T(img[IOS_SCH302_PUMP_CALL] == 0xe8 && *(int32_t *)(img + IOS_SCH302_PUMP_CALL + 1) == (int32_t)(IOS_SCH302_HIDE_BLOB - IOS_SCH302_PUMP_CALL - 5) && img[IOS_SCH302_PUMP_CALL + 5] == 0x90, "pump call -> vis");
    T(img[IOS_SCH302_SHOW_FN] == 0xe9 && *(int32_t *)(img + IOS_SCH302_SHOW_FN + 1) == (int32_t)(IOS_SCH302_HIDE_SHOW_STUB - IOS_SCH302_SHOW_FN - 5) && img[IOS_SCH302_SHOW_FN + 5] == 0x90, "Show() -> stub");
    T(img[IOS_SCH302_HIDE_FN] == 0xe9 && *(int32_t *)(img + IOS_SCH302_HIDE_FN + 1) == (int32_t)(IOS_SCH302_HIDE_HIDE_STUB - IOS_SCH302_HIDE_FN - 5), "Hide() -> stub");
    T(img[IOS_SCH302_ON_PAINT] == 0xe9 && *(int32_t *)(img + IOS_SCH302_ON_PAINT + 1) == (int32_t)(IOS_SCH302_HIDE_PAINT_STUB - IOS_SCH302_ON_PAINT - 5) && img[IOS_SCH302_ON_PAINT + 5] == 0x48, "on_paint -> stub, 5 bytes");
    /* refusals write nothing */
    build(&nt); img[IOS_SCH302_LOOP_INIT + 3] ^= 1; memcpy(ref, img, sizeof(img)); ios_sc_hide_patch((char *)img, sizeof(img), &nt, &name); T(unchanged(), "no [sc-rph] loop: untouched");
    build(&nt); img[IOS_SCH302_SHOW_FN + 9] ^= 1; memcpy(ref, img, sizeof(img)); ios_sc_hide_patch((char *)img, sizeof(img), &nt, &name); T(unchanged(), "Show() differs: untouched");
    build(&nt); img[IOS_SCH302_ON_PAINT + 7] ^= 1; memcpy(ref, img, sizeof(img)); ios_sc_hide_patch((char *)img, sizeof(img), &nt, &name); T(unchanged(), "on_paint differs: untouched");
    build(&nt); img[IOS_SCH302_HIDE_BLOB + 0xff] = 1; memcpy(ref, img, sizeof(img)); ios_sc_hide_patch((char *)img, sizeof(img), &nt, &name); T(unchanged(), "tail not zero: untouched");
    build(&nt); nt.FileHeader.TimeDateStamp++; memcpy(ref, img, sizeof(img)); ios_sc_hide_patch((char *)img, sizeof(img), &nt, &name); T(unchanged(), "other build: untouched");
    printf(fails ? "patch: %%d failures\n" : "patch: ok\n", fails);
    return fails ? 1 : 0;
}
'''
cc = tool('cc', 'clang', 'gcc')
if cc:
    src = patch_harness % dict(stamp=define(virtual, 'IOS_SCH302_STAMP'), size=define(virtual, 'IOS_SCH302_SIZE'),
                               loop_init=LOOP_INIT, ipc_direct=define(virtual, 'IOS_SCH302_IPC_DIRECT'),
                               loop_len=len(loop), loop=','.join('0x%02x' % b for b in loop), patch=patch_src)
    (tmp / 'p.c').write_text(src)
    subprocess.run([cc, '-O1', '-Wno-unused', '-o', str(tmp / 'p'), str(tmp / 'p.c')], check=True)
    r = subprocess.run([str(tmp / 'p')], capture_output=True, text=True)
    print('\n'.join(l for l in r.stdout.splitlines() if not l.startswith('[sc-hide]')))
    check(r.returncode == 0, 'ios_sc_hide_patch patches and refuses as designed')
else:
    skips.append('compiling the patch (no C compiler)')

# ---- 4. the policy, compiled natively ---------------------------------------------
policy_src = section(process, '/* madeira-bcd thread names (2026-11-27)', 'const LARGE_INTEGER *ios_wait_floor(')
policy_harness = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <pthread.h>
typedef unsigned short WCHAR; typedef int64_t LONGLONG;
typedef union { struct { uint32_t LowPart; int32_t HighPart; } u; LONGLONG QuadPart; } LARGE_INTEGER;
typedef struct { unsigned short Length, MaximumLength; WCHAR *Buffer; } UNICODE_STRING;
typedef struct { UNICODE_STRING ImagePathName; } RTL_USER_PROCESS_PARAMETERS;
typedef struct { RTL_USER_PROCESS_PARAMETERS *ProcessParameters; } PEB;
typedef struct { PEB *Peb; struct { void *UniqueProcess, *UniqueThread; } ClientId; } TEB;
volatile uint64_t ios_xp_pe_block, ios_xp_pe_len;
static TEB teb; static PEB peb; static RTL_USER_PROCESS_PARAMETERS pp;
static TEB *NtCurrentTeb(void) { return &teb; }
#define HandleToULong(h) ((unsigned int)(uintptr_t)(h))
static int lines;
#define dprintf(fd, ...) (lines++, printf(__VA_ARGS__))
static char ios_wf_after_name[64] = "rdr2.exe";
static long long ios_wf_after_ns = 180000000000ll, now_ns;
static int game_running;
static int ios_image_name_is(const WCHAR *image, int len, const char *name) { return 1; }
static int ios_wf_game_active_after(long long now, long long delay) { return game_running && now >= delay; }
static long long ios_wf_now_ns(void) { return now_ns; }
static int ios_sc_features;
#define pthread_create(t, a, f, x) (0)
#define usleep(x) (0)
%(policy)s
static LARGE_INTEGER t(double ms) { LARGE_INTEGER l; l.QuadPart = -(LONGLONG)(ms * 10000); return l; }
static long long ms_of(const LARGE_INTEGER *p) { return -p->QuadPart / 10000; }
static const LARGE_INTEGER *wait_ms(double ms, LARGE_INTEGER *store) { static LARGE_INTEGER l; l = t(ms); const LARGE_INTEGER *r = ios_sc_wait(&l, store); return r ? r : &l; }
int main(void)
{
    LARGE_INTEGER store; int fails = 0; char nm[40];
    static WCHAR path[] = { 'h', 0 };
    long long pres = 100;
#define T(c, m) do { if (!(c)) { printf("FAIL %%s\n", m); fails++; } } while (0)
#define AT(s) (now_ns = (long long)((s) * 1e9))
#define FRAMES(s0, s1) do { double x; for (x = (s0); x <= (s1) + 1e-9; x += 0.25) ios_sc_watchdog_tick((long long)(x * 1e9), pres += 15); } while (0)
#define STALL(s0, s1) do { double x; for (x = (s0); x <= (s1) + 1e-9; x += 0.25) ios_sc_watchdog_tick((long long)(x * 1e9), pres); } while (0)
    pp.ImagePathName.Buffer = path; pp.ImagePathName.Length = 2; peb.ProcessParameters = &pp; teb.Peb = &peb;
    teb.ClientId.UniqueProcess = (void *)0x268; teb.ClientId.UniqueThread = (void *)0x34c;
    T(!ios_thread_name_get(0x34c, nm, sizeof(nm)), "no names before");
    ios_thread_name_note(0x34c, "Chrome_InProcRendererThread"); ios_thread_name_note(0x350, "Compositor");
    T(ios_thread_name_get(0x34c, nm, sizeof(nm)) && !strcmp(nm, "Chrome_InProcRendererThread"), "thread name table");
    T(ios_thread_name_get(0x350, nm, sizeof(nm)) && !strcmp(nm, "Compositor") && !ios_thread_name_get(0x354, nm, sizeof(nm)), "thread name table, other tids");
    /* all switches off: nothing happens */
    ios_sc_quiet_init();
    T(ios_sc_features == 0, "off by default");
    setenv("MADEIRA_SC_HIDE", "1", 1); setenv("MADEIRA_SC_PARK", "1", 1);
    ios_sc_quiet_init();
    T(ios_sc_features == 3 && ios_sc_slice_ns == 5000000000ll && ios_sc_after_ns == 180000000000ll && ios_sc_lift_on_signal, "env parsed");
    T(ios_sc_is_helper_thread(), "helper thread");
    game_running = 1;
    AT(100); T(ms_of(wait_ms(16, &store)) == 16 && !ios_sc_vis.want && !madeira_sc_park_pid, "before the arming: untouched");
    FRAMES(170, 181);
    AT(181); T(ms_of(wait_ms(16, &store)) == 16, "armed, pages not hidden yet: no park");
    T(ios_sc_vis.want == 1 && madeira_sc_park_pid == 0x268 && ios_sc_watchdog_started, "want = 1, the server knows the pid, watchdog started");
    ios_sc_vis.applied = 1; ios_sc_vis.browsers = 2; ios_sc_vis.passes = 1;   /* the guest stub ran */
    FRAMES(181.25, 181.5);
    AT(181.5); T(ms_of(wait_ms(16, &store)) == 5000, "hidden: a 16 ms wait lasts 5 s");
    AT(186.6); T(ms_of(wait_ms(1, &store)) == 5000 && ios_sc_early == 0, "re-entry after the full slice: not early, stretched");
    AT(187.0); T(ms_of(wait_ms(16, &store)) == 16 && ios_sc_early == 1 && ios_sc_lifts == 1, "woken early by a signal: lifted");
    AT(190.0); T(ms_of(wait_ms(16, &store)) == 16, "still lifted");
    FRAMES(181.75, 192.5);
    AT(192.5); T(ms_of(wait_ms(16, &store)) == 5000, "parked again after 5 s");
    AT(192.6); T(ms_of(wait_ms(45000, &store)) == 45000, "waits of 5 s or more untouched");
    madeira_sc_park_demand_info[0] = 0x46; madeira_sc_park_demands = 1;   /* the wineserver saw a SendMessage */
    FRAMES(192.75, 193.0);
    AT(193.0); T(ms_of(wait_ms(16, &store)) == 16, "SendMessage lifts the park");
    FRAMES(193.25, 198.5);
    AT(198.5); T(ms_of(wait_ms(16, &store)) == 5000, "parked again after 5 s");
    /* watchdog: presents stop while parked */
    STALL(198.75, 202.0);
    T(!ios_sc_watchdog_tripped && ios_sc_presents_ok == 0 || ios_sc_watchdog_tripped, "stall seen");
    STALL(202.25, 202.5);
    T(ios_sc_watchdog_tripped, "WATCHDOG after 3 s without Presents");
    AT(202.6); T(ms_of(wait_ms(16, &store)) == 16, "WATCHDOG: unparked");
    FRAMES(202.75, 250.0);
    AT(250.0); T(ms_of(wait_ms(16, &store)) == 16 && ios_sc_watchdog_tripped, "held off while Presents ran < 60 s");
    FRAMES(250.25, 263.0);
    T(!ios_sc_watchdog_tripped, "watchdog re-arms after 60 s of Presents");
    AT(263.5); T(ms_of(wait_ms(16, &store)) == 5000, "parked again");
    /* helper Show(): pages visible for the yield */
    ios_sc_vis.show_calls = 1;
    FRAMES(263.0, 264.5);
    AT(264.5); wait_ms(16, &store); T(ios_sc_vis.want == 0, "helper Show(): pages visible");
    ios_sc_vis.applied = 0;
    FRAMES(264.75, 330.0);
    AT(330.0); wait_ms(16, &store); T(ios_sc_vis.want == 0, "visible during the yield");
    FRAMES(330.25, 385.0);
    AT(385.0); wait_ms(16, &store); T(ios_sc_vis.want == 1, "hidden again after the yield");
    game_running = 0;
    AT(390.0); T(ms_of(wait_ms(16, &store)) == 16 && !ios_sc_park_on && ios_sc_vis.want == 0 && !madeira_sc_park_pid, "game gone: unparked, WasHidden(0) asked");
    /* no Presents at all (another renderer): never parked */
    ios_sc_features = 2; game_running = 1; ios_sc_active_since = 0;
    STALL(390.25, 400.0);
    AT(400.0); T(ms_of(wait_ms(16, &store)) == 16, "Presents not moving: no park");
    FRAMES(400.25, 401.0);
    AT(401.0); T(ms_of(wait_ms(16, &store)) == 5000, "park without sc-hide parks once Presents move");
    /* WasHidden never applied: park after 10 s */
    ios_sc_features = 3; game_running = 0; AT(402.0); wait_ms(16, &store);
    ios_sc_vis.applied = 0; game_running = 1;
    FRAMES(401.25, 420.5);
    AT(410.0); T(ms_of(wait_ms(16, &store)) == 16, "WasHidden pending: no park yet");
    AT(420.5); T(ms_of(wait_ms(16, &store)) == 5000, "WasHidden never applied: park after 10 s");
    /* lifts off */
    setenv("MADEIRA_SC_PARK_LIFT", "0", 1); ios_sc_quiet_init();
    { long long lifts = ios_sc_lifts, early = ios_sc_early;
      AT(421.0); T(ms_of(wait_ms(16, &store)) == 5000 && ios_sc_lifts == lifts && ios_sc_early == early + 1,
                   "MADEIRA_SC_PARK_LIFT=0: an early wake is counted but does not lift"); }
    printf(fails ? "policy: %%d failures\n" : "policy: ok\n", fails);
    return fails ? 1 : 0;
}
'''
if cc:
    (tmp / 'q.c').write_text(policy_harness % dict(policy=policy_src))
    subprocess.run([cc, '-O1', '-Wno-unused', '-o', str(tmp / 'q'), str(tmp / 'q.c')], check=True)
    r = subprocess.run([str(tmp / 'q')], capture_output=True, text=True)
    print('\n'.join(l for l in r.stdout.splitlines() if l.startswith(('FAIL', 'policy'))))
    check(r.returncode == 0, 'park / hide policy')
else:
    skips.append('compiling the policy (no C compiler)')

# ---- 5. wiring ------------------------------------------------------------------
for fn in ('NtWaitForMultipleObjects', 'NtWaitForSingleObject', 'NtDelayExecution', 'NtRemoveIoCompletion(',
           'NtRemoveIoCompletionEx', 'NtWaitForAlertByThreadId'):
    start = sync.index('NTSTATUS WINAPI ' + fn)
    body = sync[start:sync.index('\n}', start)]
    check('ios_wait_floor( timeout' in body, 'sync.c %s enters ios_wait_floor' % fn)
check('ios_sc_wait( timeout, store )' in section(process, 'const LARGE_INTEGER *ios_wait_floor(', '\n}'),
      'ios_wait_floor hands helper threads to ios_sc_wait')
check(virtual.count('ios_sc_hide_patch( ptr, total_size, nt, nt_name );') == 1 and
      virtual.index('ios_sc_render_handler_patch( ptr, total_size, nt, sec, nt_name );') <
      virtual.index('ios_sc_hide_patch( ptr, total_size, nt, nt_name );'), 'patched after [sc-rph], at map time')
sm = section(queue, 'DECL_HANDLER(send_message)', 'if (!(recv_queue = thread->queue))')
check('madeira_sc_park_demands' in sm and 'current->process != thread->process' in sm and 'MSG_POSTED' not in sm
      and 'MSG_NOTIFY' not in sm, 'wineserver counts cross-process messages that wait for a reply')
check('ios_sc_xp_row( th[k]' in server and 'ios_sc_xp_window( dt_ms )' in server, '[sc-cpu] wired into the xprobe loop')
check('ios_thread_name_note(' in (root / 'build/ntdll-unix/thread_ios.c').read_text(), 'thread names published')
rdr2 = section(library, 'case "rdr2.exe", "playrdr2.exe":', 'case "gta5_enhanced.exe"')
for line in ('env.MADEIRA_SC_HIDE = 1', 'env.MADEIRA_SC_PARK = 2', 'env.MADEIRA_SPIN_PC_NAMES = Chrome_InProcRendererThread',
             'env.MADEIRA_WAIT_FLOOR_AFTER = RDR2.exe:180'):
    check(line in rdr2, 'RDR2 profile: ' + line)
check(len(re.findall(r'^\s*env\.MADEIRA_SC_PARK = 2$', library, re.M)) == 1 and
      len(re.findall(r'^\s*env\.MADEIRA_SC_HIDE = 1$', library, re.M)) == 1, 'enabled only in the RDR2 profile')

shutil.rmtree(tmp, ignore_errors=True)
for s in skips:
    print('SKIP', s)
if failures:
    print('%d failures' % len(failures))
    sys.exit(1)
print('ok' + (' (with %d skipped parts)' % len(skips) if skips else ''))
