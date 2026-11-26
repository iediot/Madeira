#!/usr/bin/env python3
"""Pool pressure fixes for RDR2 (2026-11-27): image trim, the other-process big
code-buffer allowance once the game runs, and the SMC fast decline.

1. [image-trim]: ios_image_trim_size / ios_image_trim_relocs_ok on the staged
   arm64ec Wine DLLs and on synthetic headers; the copy path and every reader
   of a mapping's relocation table are wired for a copy shorter than the image.
2. MADEIRA_POOL_OTHER_BIG_GAME_MB is an allowance ON TOP of what the other
   processes held when the game was first seen running.
3. [smc-fast]: ios_smc_fast_decline's gates, with Mach calls mocked.

Host compiles of production functions only; no device behaviour is claimed.
"""
from pathlib import Path
import os
import re
import struct
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
virtual = (root / 'build/ntdll-unix/virtual_ios.c').read_text()
signal = (root / 'build/ntdll-unix/signal_arm64_ios.c').read_text()
dlls = root / 'app/Madeira/arm64ec-windows'


def function(source, signature):
    start = source.index(signature)
    return source[start:source.index('\n}', start) + 2] + '\n'


def compile_run(code, name):
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, name + '.c')
        exe = os.path.join(tmp, name)
        Path(src).write_text(code)
        subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wno-unused-function', '-O1', src, '-o', exe, '-lpthread'],
                       check=True)
        out = subprocess.run([exe], check=True, capture_output=True, text=True).stdout
    return out


# ---- 1. wiring ---------------------------------------------------------------
copy = virtual[virtual.index('size_t image_full_size = image_size;'):]
copy = copy[:copy.index('static void ios_jit_mark_ec_ranges_for_copy')]
assert copy.index('ios_image_trim_size(') < copy.index('size_t image_alloc = (image_size + page_size - 1)'), \
    'trim before the allocation is sized'
assert 'ios_pool_image_trim_enabled() && !ios_image_is_fex( image_base, image_size )' in copy
assert 'ios_image_trim_relocs_ok( (const unsigned char *)image_base, image_size, keep )' in copy
assert copy.index('ios_jit_add_mapping(image_base, (char *)jit_rx_base + offset, image_size);') \
    < copy.index('ios_jit_set_full_size( image_base, (char *)jit_rx_base + offset, image_full_size );')
assert 'memcpy((char *)jit_rw_base + offset, image_base, image_size);' in copy
assert '((size_t)reloc_rva + reloc_sz <= image_size ? rw_image : (char *)image_base)' in copy, \
    'parent relocation walk reads a trimmed table at the PE VA'
assert 'if ((size_t)block_rva + off + 8 > image_size) continue;' in copy

early = virtual[virtual.index('/* Check if this image was already copied to JIT pool */'):]
early = early[:early.index('/* Scan backward to find MZ header')]
assert 'a < mb + (ios_jit_mappings[i].full_size ? ios_jit_mappings[i].full_size' in early, \
    'a page of the untrimmed tail belongs to the copied image'
assert '(ios_jit_mappings[i].full_size && imgsz == ios_jit_mappings[i].full_size)' in early, \
    'ml352 accepts a trimmed entry'

add = function(virtual, 'void ios_jit_add_mapping(void *pe_base, void *jit_base, size_t size)')
assert add.index('ios_jit_mappings[slot].full_size = 0;') < add.index('ios_jit_mappings[slot].pe_base = pe_base;')
assert virtual.count('ios_jit_mapping_reloc_src(') == 4, 'definition + child copy + two NtProtect-sync walks'
child = virtual[virtual.index('/* DIR64 relocations with the child\'s delta.'):][:1600]
assert 'ios_jit_mapping_reloc_src( m, rw_dest ) + m->reloc_rva' in child
assert '(size_t)block_rva + (entries[j] & 0xFFF) + 8 > m->size' in child, 'child never writes past its copy'

# ---- 1. behaviour --------------------------------------------------------------
trim_src = ''.join(function(virtual, sig) for sig in (
    'static uint32_t ios_trim_rd32(',
    'static size_t ios_image_trim_size(',
    'static int ios_image_trim_relocs_ok(',
))
trim_min = re.search(r'#define IOS_IMAGE_TRIM_MIN .*', virtual).group(0)

real = []
for name in ('shell32.dll', 'kernelbase.dll', 'user32.dll', 'ntdll.dll', 'ucrtbase.dll', 'xtajit64.dll'):
    p = dlls / name
    if p.exists():
        real.append((name, p))

harness = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define CHECK(c) do { if (!(c)) { fprintf(stderr, "failed line %d: %s\n", __LINE__, #c); exit(1); } } while (0)
''' + trim_min + '\n' + trim_src + r'''
/* Map a PE file into an image-layout buffer (sections at their RVAs). */
static unsigned char *map_pe( const char *path, size_t *image_size )
{
    FILE *f = fopen( path, "rb" );
    long n; unsigned char *file, *img; uint32_t lfanew, soi, hdrs; unsigned nsec, opt, i;
    if (!f) return NULL;
    fseek( f, 0, SEEK_END ); n = ftell( f ); fseek( f, 0, SEEK_SET );
    file = malloc( n ); CHECK( fread( file, 1, n, f ) == (size_t)n ); fclose( f );
    lfanew = ios_trim_rd32( file + 0x3c );
    nsec = file[lfanew + 6] | file[lfanew + 7] << 8;
    opt = file[lfanew + 0x14] | file[lfanew + 0x15] << 8;
    soi = ios_trim_rd32( file + lfanew + 0x18 + 0x38 );
    hdrs = ios_trim_rd32( file + lfanew + 0x18 + 0x3c );
    img = calloc( 1, soi );
    memcpy( img, file, hdrs );
    for (i = 0; i < nsec; i++)
    {
        const unsigned char *s = file + lfanew + 0x18 + opt + i * 40;
        uint32_t vs = ios_trim_rd32( s + 8 ), va = ios_trim_rd32( s + 12 ), rs = ios_trim_rd32( s + 16 ), rp = ios_trim_rd32( s + 20 );
        uint32_t len = rs < vs ? rs : vs;
        if (rp && len && va + len <= soi && rp + len <= (uint32_t)n) memcpy( img + va, file + rp, len );
    }
    free( file );
    *image_size = soi;
    return img;
}

/* A synthetic Wine builtin: headers, .text, .data, .rsrc, .reloc. */
static unsigned char img[0x200000];
static void synth( void )
{
    unsigned char *nt, *opt, *sec;
    memset( img, 0, sizeof(img) );
    img[0] = 'M'; img[1] = 'Z';
    memcpy( img + 0x40, "Wine builtin DLL", 16 );
    *(uint32_t *)(img + 0x3c) = 0x80;
    nt = img + 0x80; memcpy( nt, "PE\0\0", 4 );
    nt[6] = 4; nt[0x14] = 0xf0;
    opt = nt + 0x18; opt[0] = 0x0b; opt[1] = 0x02;
    *(uint32_t *)(opt + 0x10) = 0x1000;                 /* entry */
    *(uint64_t *)(opt + 0x18) = (uint64_t)(uintptr_t)img; /* ImageBase = where it is */
    *(uint32_t *)(opt + 0x38) = sizeof(img);            /* SizeOfImage */
    *(uint32_t *)(opt + 0x3c) = 0x400;                  /* SizeOfHeaders */
    *(uint32_t *)(opt + 0x6c) = 16;
    *(uint32_t *)(opt + 0x70 + 0 * 8) = 0x30000; *(uint32_t *)(opt + 0x74 + 0 * 8) = 0x100;  /* exports in .data */
    *(uint32_t *)(opt + 0x70 + 2 * 8) = 0x40000; *(uint32_t *)(opt + 0x74 + 2 * 8) = 0x100;  /* resources */
    *(uint32_t *)(opt + 0x70 + 5 * 8) = 0x1f0000; *(uint32_t *)(opt + 0x74 + 5 * 8) = 0x10;  /* relocs */
    sec = opt + 0xf0;
    memcpy( sec + 0 * 40, ".text", 5 ); *(uint32_t *)(sec + 0 * 40 + 8) = 0x2000; *(uint32_t *)(sec + 0 * 40 + 12) = 0x1000; *(uint32_t *)(sec + 0 * 40 + 36) = 0x60000020;
    memcpy( sec + 1 * 40, ".data", 5 ); *(uint32_t *)(sec + 1 * 40 + 8) = 0x1000; *(uint32_t *)(sec + 1 * 40 + 12) = 0x30000; *(uint32_t *)(sec + 1 * 40 + 36) = 0xc0000040;
    memcpy( sec + 2 * 40, ".rsrc", 5 ); *(uint32_t *)(sec + 2 * 40 + 8) = 0x1a0000; *(uint32_t *)(sec + 2 * 40 + 12) = 0x40000; *(uint32_t *)(sec + 2 * 40 + 36) = 0x40000040;
    memcpy( sec + 3 * 40, ".reloc", 6 ); *(uint32_t *)(sec + 3 * 40 + 8) = 0x10; *(uint32_t *)(sec + 3 * 40 + 12) = 0x1f0000; *(uint32_t *)(sec + 3 * 40 + 36) = 0x42000040;
    /* one reloc block: page 0x30000, a DIR64 at +0x10 pointing into .text, padding */
    *(uint32_t *)(img + 0x1f0000) = 0x30000; *(uint32_t *)(img + 0x1f0004) = 0x10;
    *(uint16_t *)(img + 0x1f0008) = 0xa010; *(uint16_t *)(img + 0x1f000a) = 0;
    *(uint64_t *)(img + 0x30010) = (uint64_t)(uintptr_t)img + 0x1100;
}

int main( int argc, char **argv )
{
    int i;
    size_t keep;

    synth();
    keep = ios_image_trim_size( img, 0x1000, sizeof(img) );
    CHECK( keep == 0x40000 );
    CHECK( ios_image_trim_relocs_ok( img, sizeof(img), keep ) );
    /* a pointer into the resources from kept data refuses the trim */
    *(uint64_t *)(img + 0x30010) = (uint64_t)(uintptr_t)img + 0x50000;
    CHECK( !ios_image_trim_relocs_ok( img, sizeof(img), keep ) );
    synth();
    *(uint16_t *)(img + 0x1f000a) = 0x3020;              /* HIGHLOW: not understood */
    CHECK( !ios_image_trim_relocs_ok( img, sizeof(img), keep ) );
    synth(); memcpy( img + 0x40, "Not a builtin DLL", 16 );
    CHECK( ios_image_trim_size( img, 0x1000, sizeof(img) ) == sizeof(img) );
    synth(); *(uint32_t *)(img + 0x80 + 0x18 + 0x70) = 0x40010;   /* exports in the resources */
    CHECK( ios_image_trim_size( img, 0x1000, sizeof(img) ) == sizeof(img) );
    synth(); *(uint32_t *)(img + 0x80 + 0x18 + 0x10) = 0x41000;   /* entry point in the resources */
    CHECK( ios_image_trim_size( img, 0x1000, sizeof(img) ) == sizeof(img) );
    synth(); *(uint32_t *)(img + 0x80 + 0x18 + 0xf0 + 2 * 40 + 36) = 0xc0000040;   /* writable .rsrc */
    keep = ios_image_trim_size( img, 0x1000, sizeof(img) );
    CHECK( keep == sizeof(img) || keep >= 0x1f0000 );     /* never cuts a writable section */
    synth(); *(uint32_t *)(img + 0x80 + 0x18 + 0xf0 + 1 * 40 + 8) = 0x11000;   /* .data runs into .rsrc */
    CHECK( ios_image_trim_size( img, 0x1000, sizeof(img) ) == sizeof(img) );
    synth(); *(uint32_t *)(img + 0x80 + 0x18 + 0xf0 + 2 * 40 + 8) = 0x10000;   /* small resources */
    *(uint32_t *)(img + 0x80 + 0x18 + 0xf0 + 3 * 40 + 12) = 0x50000;          /* relocs right behind */
    *(uint32_t *)(img + 0x80 + 0x18 + 0x70 + 5 * 8) = 0x50000;
    CHECK( ios_image_trim_size( img, 0x1000, 0x60000 ) == 0x60000 );          /* under IOS_IMAGE_TRIM_MIN */
    CHECK( ios_image_trim_size( img, 0x100, sizeof(img) ) == sizeof(img) );   /* header bytes missing */

    for (i = 1; i + 1 < argc; i += 2)
    {
        size_t size, k;
        unsigned char *m = map_pe( argv[i + 1], &size );
        CHECK( m );
        k = ios_image_trim_size( m, 0x1000, size );
        printf( "%s %zx %zx %d\n", argv[i], size, k, k < size ? ios_image_trim_relocs_ok( m, size, k ) : -1 );
        free( m );
    }
    return 0;
}
'''
args = []
for name, p in real:
    args += [name, str(p)]
with tempfile.TemporaryDirectory() as tmp:
    src = os.path.join(tmp, 'trim.c')
    exe = os.path.join(tmp, 'trim')
    Path(src).write_text(harness)
    subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wno-unused-function', '-O1', src, '-o', exe], check=True)
    out = subprocess.run([exe] + args, check=True, capture_output=True, text=True).stdout
results = {}
for line in out.split('\n'):
    if line:
        n, size, keep, ok = line.split()
        results[n] = (int(size, 16), int(keep, 16), int(ok))
if 'shell32.dll' in results:
    size, keep, ok = results['shell32.dll']
    assert keep < size and size - keep >= 8 << 20 and ok == 1, results['shell32.dll']
if 'kernelbase.dll' in results:
    size, keep, ok = results['kernelbase.dll']
    assert keep < size and size - keep >= 1 << 20 and ok == 1, results['kernelbase.dll']
if 'user32.dll' in results:
    size, keep, ok = results['user32.dll']
    assert keep < size and ok == 1, results['user32.dll']
for small in ('ntdll.dll', 'ucrtbase.dll'):
    if small in results:
        assert results[small][1] == results[small][0], (small, 'saves under IOS_IMAGE_TRIM_MIN: whole copy')
for n, (size, keep, ok) in sorted(results.items()):
    print('  %-16s SizeOfImage 0x%07x copy 0x%07x (%s)' % (n, size, keep,
          'whole' if keep == size else '%.1f MB not copied, relocations %s' % ((size - keep) / 1048576.0,
                                                                                 'ok' if ok == 1 else 'REFUSED')))
print('ok: image trim (synthetic + %d staged DLLs)' % len(results))

# ---- 2. MADEIRA_POOL_OTHER_BIG_GAME_MB --------------------------------------
big = function(virtual, 'static int ios_pool_big_code_allowed( size_t want )')
listfn = function(virtual, 'static int ios_peb_in_big_code_list( PEB *peb, const char *list )')
fixture = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <pthread.h>
#define CHECK(c) do { if (!(c)) { fprintf(stderr, "failed line %d: %s\n", __LINE__, #c); exit(1); } } while (0)
#define MB ((size_t)1 << 20)
typedef unsigned short WCHAR;
typedef struct { unsigned short Length, MaximumLength; WCHAR *Buffer; } UNICODE_STRING;
typedef struct { UNICODE_STRING ImagePathName; } RTL_USER_PROCESS_PARAMETERS;
typedef struct { RTL_USER_PROCESS_PARAMETERS *ProcessParameters; } PEB;
struct ios_tail_carve { size_t off, size; int free; time_t freed_at; void *peb; int retired; };
static struct ios_tail_carve ios_tail_carves[64];
static unsigned ios_tail_carve_n;
static pthread_mutex_t ios_tail_carve_lock = PTHREAD_MUTEX_INITIALIZER;
static PEB *current;
static PEB *ios_jit_current_peb(void) { return current; }
static WCHAR game_path[] = { 'C',':','\\','R','D','R','2','.','e','x','e' };
static WCHAR sc_path[] = { 'S','o','c','i','a','l','C','l','u','b','H','e','l','p','e','r','.','e','x','e' };
static RTL_USER_PROCESS_PARAMETERS game_pp = { { sizeof(game_path), sizeof(game_path), game_path } };
static RTL_USER_PROCESS_PARAMETERS sc_pp = { { sizeof(sc_path), sizeof(sc_path), sc_path } };
static PEB game = { &game_pp }, sc = { &sc_pp };
static void carve( PEB *peb, size_t size ) { ios_tail_carves[ios_tail_carve_n].size = size; ios_tail_carves[ios_tail_carve_n].peb = peb; ios_tail_carve_n++; }
''' + listfn + big + r'''
int main(void)
{
    setenv( "MADEIRA_POOL_BIG_CODE_EXES", "RDR2.exe", 1 );
    setenv( "MADEIRA_POOL_OTHER_BIG_GAME_MB", "64", 1 );
    current = &sc;
    carve( &sc, 64 * MB ); carve( &sc, 64 * MB );
    CHECK( ios_pool_big_code_allowed( 64 * MB ) );     /* 192 MB share before the game */
    carve( &sc, 64 * MB );
    CHECK( !ios_pool_big_code_allowed( 32 * MB ) );    /* share spent */
    carve( &game, 128 * MB );                          /* the game runs: held = 192 */
    CHECK( ios_pool_big_code_allowed( 64 * MB ) );     /* 192 + 64 allowed on top */
    CHECK( !ios_pool_big_code_allowed( 128 * MB ) );
    carve( &sc, 64 * MB );
    CHECK( !ios_pool_big_code_allowed( 32 * MB ) );    /* the 64 MB on top is spent */
    ios_tail_carves[0].free = 1;                       /* one 64 MB buffer released */
    CHECK( ios_pool_big_code_allowed( 64 * MB ) );
    current = &game;
    CHECK( ios_pool_big_code_allowed( 128 * MB ) );    /* the game itself is never held back */
    unsetenv( "MADEIRA_POOL_OTHER_BIG_GAME_MB" );
    current = &sc;
    CHECK( !ios_pool_big_code_allowed( 32 * MB ) );    /* default: keep what they had, no more */
    printf( "ok\n" );
    return 0;
}
'''
assert compile_run(fixture, 'otherbig').strip() == 'ok'
print('ok: other processes add MADEIRA_POOL_OTHER_BIG_GAME_MB on top of what they held when the game started')

# ---- 3. [smc-fast] -----------------------------------------------------------
fast = function(signal, 'static int ios_smc_fast_decline(')
assert signal.count('goto ios_exc_reply;') == 1 and signal.count('ios_exc_reply: ;') == 1
mach = signal[signal.index('static void *ios_mach_exception_thread( void *arg )'):]
assert mach.index('int srcwatch_hit = ios_srcwatch_handle( &state, fault_addr );') \
    < mach.index('ios_smc_fast_decline( req, thread, &state, fault_addr )') \
    < mach.index('/* 1. Redirect user_shared_data accesses (0x7FFE0000) */'), 'after srcwatch, before every service'
assert mach.index('goto ios_exc_reply;') < mach.index('ios_exc_reply: ;') < mach.index('reply.ret_code = handled ? KERN_SUCCESS : KERN_FAILURE;')
bus = function(signal, 'static void bus_handler( int signal, siginfo_t *siginfo, void *sigcontext )')
assert bus.index('ios_decline_foreign_fault(') < bus.index('ios_bsd_bus_seen = 1;') < bus.index('ios_make_wine_logging_safe();')
trap = function(virtual, 'int ios_smc_trap_write_fault( uintptr_t addr )')
assert 'pthread_mutex_trylock( &virtual_mutex )' in trap and 'pthread_mutex_lock' not in trap, 'the Mach thread never waits'
assert '(VPROT_X64DATA | SEC_IMAGE)) == (VPROT_X64DATA | SEC_IMAGE)' in trap
assert '!(vprot & (VPROT_WRITE | VPROT_WRITECOPY | VPROT_GUARD | VPROT_WRITEWATCH))' in trap
assert 'ios_jit_anon_alias_overlaps(' in trap

fixture = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define CHECK(c) do { if (!(c)) { fprintf(stderr, "failed line %d: %s\n", __LINE__, #c); exit(1); } } while (0)
#define dprintf(fd, ...) ((void)0)
typedef int kern_return_t; typedef unsigned thread_t; typedef unsigned mach_msg_type_number_t; typedef void *thread_state_t;
#define KERN_SUCCESS 0
#define KERN_PROTECTION_FAILURE 2
#define EXC_BAD_ACCESS 1
#define ARM_EXCEPTION_STATE64 7
#define ARM_EXCEPTION_STATE64_COUNT 4
typedef struct { uint64_t __far; uint32_t __esr, __exception; } arm_exception_state64_t;
typedef struct { uint64_t pc; } arm_thread_state64_t;
#define arm_thread_state64_get_pc(s) ((s).pc)
typedef struct { int exception; unsigned code_count; int64_t code[2]; } ios_exc_request_t;
void *ios_jit_rx_base_global = (void *)0x148000000, *ios_jit_rw_base_global = (void *)0x7900000000;
size_t ios_jit_pool_size_global = 0x38000000;
static int ios_jit_low_contains( uintptr_t a ) { return a - 0x134400000 < 0xbc00000; }
static int ios_jit_low_rw_contains( uintptr_t a ) { (void)a; return 0; }
static uint32_t esr_now; static int trap_answer, trap_calls;
static kern_return_t thread_get_state( thread_t t, int f, thread_state_t s, mach_msg_type_number_t *n )
{ (void)t; (void)f; (void)n; ((arm_exception_state64_t *)s)->__esr = esr_now; return KERN_SUCCESS; }
int ios_smc_trap_write_fault( uintptr_t a ) { (void)a; trap_calls++; return trap_answer; }
static volatile int ios_bsd_bus_seen;
''' + fast + r'''
int main(void)
{
    ios_exc_request_t req = { EXC_BAD_ACCESS, 2, { KERN_PROTECTION_FAILURE, 0x12e2ba165 } };
    arm_thread_state64_t st = { 0x13661814c };                     /* FEX code in region C */
    const uint32_t write_perm = (0x24u << 26) | 0x40 | 0x0f;        /* data abort, WnR, permission L3 */
    esr_now = write_perm; trap_answer = 1;
    CHECK( !ios_smc_fast_decline( &req, 1, &st, 0x12e2ba165 ) );   /* no BSD signal seen yet */
    CHECK( trap_calls == 0 );
    ios_bsd_bus_seen = 1;
    CHECK( ios_smc_fast_decline( &req, 1, &st, 0x12e2ba165 ) );
    esr_now = (0x24u << 26) | 0x0f;                                /* a read */
    CHECK( !ios_smc_fast_decline( &req, 1, &st, 0x12e2ba165 ) );
    esr_now = (0x24u << 26) | 0x40 | 0x07;                         /* translation fault, not permission */
    CHECK( !ios_smc_fast_decline( &req, 1, &st, 0x12e2ba165 ) );
    esr_now = (0x24u << 26) | 0x40 | 0x21;                         /* alignment fault */
    CHECK( !ios_smc_fast_decline( &req, 1, &st, 0x12e2ba165 ) );
    esr_now = write_perm;
    CHECK( !ios_smc_fast_decline( &req, 1, &st, 0x148010000 ) );   /* a pool target: the store emulator's */
    CHECK( !ios_smc_fast_decline( &req, 1, &st, 0x134500000 ) );   /* region C target */
    st.pc = 0x1a0000000;                                           /* host code, not translated code */
    CHECK( !ios_smc_fast_decline( &req, 1, &st, 0x12e2ba165 ) );
    st.pc = 0x150000000;                                           /* pool tail code */
    CHECK( ios_smc_fast_decline( &req, 1, &st, 0x12e2ba165 ) );
    req.code[0] = 1;                                               /* KERN_INVALID_ADDRESS */
    CHECK( !ios_smc_fast_decline( &req, 1, &st, 0x12e2ba165 ) );
    req.code[0] = KERN_PROTECTION_FAILURE;
    trap_answer = 0;                                               /* Wine's tables say no */
    CHECK( !ios_smc_fast_decline( &req, 1, &st, 0x12e2ba165 ) );
    trap_answer = 1;
    setenv( "MADEIRA_SMC_FAST_DECLINE", "0", 1 );
    printf( "ok\n" );
    return 0;
}
'''
assert compile_run(fixture, 'smcfast').strip() == 'ok'
print('ok: smc fast decline gates (BSD seen, write permission fault, translated pc, non-pool target, Wine says armed)')
