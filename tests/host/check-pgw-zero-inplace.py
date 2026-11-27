#!/usr/bin/env python3
"""Page-wait attribution ([pgw]) and hot-decommit zeroing in place (zero-inplace); no Wine runs.

Compiles the production block from build/ntdll-unix/virtual_ios.c (from
"madeira-bcd zero-inplace and pgw" to the log-volume switch) against stubs for
the swap tier's extent table, the page protections and the virtual lock, on a
real MAP_SHARED file mapping, and checks:
  - a decommit is zeroed in place only when its base was decommitted and then
    committed again within the hot window, lies in one reservation extent, is
    small enough, every 4 KB page is committed read-write (no guard, write watch,
    exec, write-copy) and every host page is resident; otherwise 0 is returned and
    the memory is untouched (the caller rotates as before), with the reason counted;
  - zeroing in place leaves zeros in the file at the same offsets and the
    neighbours untouched;
  - env.MADEIRA_SWAP_ZERO_INPLACE=0 never zeroes, and with env.MADEIRA_PGW=0 as well
    the decommit/recommit table is not touched at all (today's behaviour);
  - recommit latencies land in the right buckets;
  - the sampler's classifier names a thread blocked in read() as a syscall (svc
    + x16), and its [pgw] line is printed and bounded;
  - decommit_pages tries zero-inplace before the rotation, verifies the zero
    contract, and the commit path starts the sampler and notes recommits;
  - the prefetch is off by default, checks the extent under the virtual lock, is
    never issued for image extents, and is guarded by os_proc_available_memory;
  - the sampler never suspends a thread;
  - disk-cache: the broad tier's disk-cap check uses the size the [pgw] thread
    read off the lock while it is under 10 s old, and fstats otherwise or with
    env.MADEIRA_SWAP_DISK_CACHE=0 (every time, as before).
Needs python3 and a C compiler on Darwin/arm64 for the executed part (Mach calls,
mincore, svc decoding); elsewhere only the source checks run.
"""
from pathlib import Path
import os
import platform
import re
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
virt = (root / 'build/ntdll-unix/virtual_ios.c').read_text()
failures = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        failures.append(what)


dc_begin = virt.index('/* madeira-bcd disk-cache:')
disk = virt[dc_begin:virt.index('/* ml1257: madeira.cfg swap-mode = 2 selects')]
begin = virt.index('/* madeira-bcd zero-inplace and pgw:')
end = virt.index('/* madeira-bcd log-volume: env.MADEIRA_DECOMMIT_LOG_ALL')
block = virt[begin:end]

# ---- source checks ----
dec = virt[virt.index('static NTSTATUS decommit_pages( struct file_view *view, char *base, size_t size )'):]
dec = dec[:dec.index('\n}\n')]
zi = dec.index('ios_swap_zero_inplace( host_start, host_end - host_start )')
check(zi < dec.index('ios_swap_punch_resv( host_start, host_end - host_start )'),
      'decommit_pages tries zero-inplace before the rotation')
check('dc_branch = "swap-inplace";' in dec[zi:zi + 400] and 'dc_verify = host_start;' in dec[zi:zi + 400],
      'an in-place zeroing is read back like mmap-over')
commit = virt[virt.index('else  /* commit the pages */'):]
commit = commit[:commit.index('ios_verify_commit_zero( base, size, protect, was_committed );')]
sp = commit.index('else if (!(status = set_protection( view, base, size, protect )))')
check(sp < commit.index('ios_pgw_start();') and sp < commit.index('ios_swap_cyc_recommit( base );'),
      'the commit path starts the sampler and notes a recommit only after set_protection succeeded')
check('getenv( "MADEIRA_SWAP_ZERO_INPLACE" ), 1, 0, 1' in block, 'zero-inplace defaults on, 0 turns it off')
check('getenv( "MADEIRA_PGW" ), 1, 0, 1' in block, '[pgw] defaults on, 0 turns it off')
check('getenv( "MADEIRA_PGW_PREFETCH_KB" ), 0, 0, 4096' in block, 'prefetch defaults off')
pf = block[block.index('static void ios_pgw_prefetch( uint64_t a )'):]
pf = pf[:pf.index('\n}\n')]
check(pf.index('os_proc_available_memory()') < pf.index('pthread_mutex_trylock( &virtual_mutex )')
      < pf.index('pthread_mutex_unlock( &virtual_mutex )') < pf.index('madvise('),
      'prefetch: memory guard, then the extent under the lock, madvise after unlocking')
check('!ios_swap_ext[i].img' in pf, 'prefetch never touches image extents')
check('if (ios_pgw_pf_kb) ios_pgw_prefetch( ea );' in block, 'prefetch runs only when its switch is on')
code_only = re.sub(r'/\*.*?\*/', '', block, flags=re.S)
check(not re.search(r'\bthread_(suspend|resume|abort)\s*\(', code_only), 'the sampler never suspends a thread')
check('pthread_sigmask( SIG_BLOCK, &all, NULL );' in block, 'the sampler thread blocks every signal')
zbody = block[block.index('static int ios_swap_zero_inplace( char *lo, size_t len )'):]
zbody = zbody[:zbody.index('\n}\n')]
check(zbody.index('mincore(') < zbody.index('memset( lo, 0, len );'), 'residency is checked before the memset')
check(zbody.index('if (!ios_swap_zip) return 0;') < zbody.index('memset( lo, 0, len );'),
      'zero-inplace off returns before any write')
rot = virt[virt.index('static int ios_swap_rotate_resv('):virt.index('static int ios_swap_punch_resv(')]
check('ios_swap_rotate_bytes += len;' in rot, 'rotation bytes are counted for [pgw]')

check('ios_swap_disk_refresh();' in block and 'next_disk = now + 250000000ull;' in block,
      'the [pgw] thread refreshes the swap file size off the lock every 250 ms')
check('getenv( "MADEIRA_SWAP_DISK_CACHE" )' in disk and 'ios_swap_disk_cache = !(e && e[0] == \'0\');' in disk,
      'disk-cache defaults on, 0 restores the fstat under the lock')
if platform.system() != 'Darwin' or platform.machine() not in ('arm64', 'aarch64'):
    print('SKIP executed part: needs Darwin/arm64 (Mach, mincore, svc)')
    sys.exit(1 if failures else 0)

prelude = r'''
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <sys/mman.h>
#include <sys/time.h>
#include <sys/stat.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
typedef unsigned char BYTE;
#define VPROT_READ       0x01
#define VPROT_WRITE      0x02
#define VPROT_EXEC       0x04
#define VPROT_WRITECOPY  0x08
#define VPROT_GUARD      0x10
#define VPROT_COMMITTED  0x20
#define VPROT_WRITEWATCH 0x40
#define page_size 0x1000
static uintptr_t host_page_mask = 0x3fff;
static pthread_mutex_t virtual_mutex = PTHREAD_MUTEX_INITIALIZER;
static int ios_swap_fd = -1;
static struct { char *va; size_t len; uint64_t off; int resv; size_t key; uint64_t born; int img; } ios_swap_ext[16];
static unsigned ios_swap_n;
static unsigned long long ios_swap_rotations, ios_swap_rotate_bytes, ios_swap_holes, ios_swap_punch_calls, ios_swap_punch_ns,
                          ios_swap_pq_done, ios_swap_punch_slow, ios_swap_backs, ios_swap_releases, ios_swap_bytes;
static uint64_t fake_ns = 1000000000ull;
static int real_clock;
static uint64_t ios_swap_now_ns( void )
{
    struct timespec ts;
    if (!real_clock) return fake_ns;
    clock_gettime( CLOCK_MONOTONIC, &ts );
    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}
static BYTE vprots[64];
static char *vbase;
static BYTE get_page_vprot( const void *p ) { return vprots[((const char *)p - vbase) / 0x1000]; }
int ios_thread_registry_count( void ) { return 0; }
uintptr_t ios_thread_registry_teb( int i ) { (void)i; return 0; }
thread_t ios_thread_registry_mach( int i ) { (void)i; return 0; }
size_t os_proc_available_memory( void ) { return 4ull << 30; }
static int fstat_calls;
static int counted_fstat( int fd, struct stat *st ) { fstat_calls++; return fstat( fd, st ); }
#define fstat counted_fstat
'''

tests = r'''
static int pipefd[2];
static volatile int reader_in;
static void *reader( void *arg ) { char c; (void)arg; reader_in = 1; (void)!read( pipefd[0], &c, 1 ); return NULL; }

static void all( const char *p, size_t n, unsigned char v ) { for (size_t i = 0; i < n; i++) assert( (unsigned char)p[i] == v ); }

int main( int argc, char **argv )
{
    const size_t total = 0x40000, pg = 0x4000;
    char path[] = "/tmp/pgw-zi-XXXXXX";
    int mode = argc > 1 ? atoi( argv[1] ) : 0;
    ios_swap_fd = mkstemp( path );
    assert( ios_swap_fd >= 0 );
    unlink( path );
    assert( !ftruncate( ios_swap_fd, total * 2 ) );
    char *p = mmap( NULL, total, PROT_READ | PROT_WRITE, MAP_SHARED, ios_swap_fd, total );
    assert( p != MAP_FAILED && !((uintptr_t)p & host_page_mask) );
    vbase = p;
    memset( vprots, VPROT_COMMITTED | VPROT_READ | VPROT_WRITE, sizeof(vprots) );
    ios_swap_n = 1;
    ios_swap_ext[0].va = p; ios_swap_ext[0].len = total; ios_swap_ext[0].off = total; ios_swap_ext[0].resv = 1;
    memset( p, 0x5a, total );
    char *r = p + 4 * pg;              /* the range: 2 host pages */
    size_t len = 2 * pg;

    if (mode == 1)   /* env.MADEIRA_SWAP_ZERO_INPLACE=0 MADEIRA_PGW=0: today's behaviour, no table */
    {
        for (int i = 0; i < 3; i++) { assert( !ios_swap_zero_inplace( r, len ) ); ios_swap_cyc_recommit( r ); fake_ns += 1000000; }
        all( p, total, 0x5a );
        assert( ios_swap_cyc_decs == 0 && ios_swap_zip_n == 0 );
        for (int i = 0; i < IOS_SWAP_CYC_N; i++) assert( !ios_swap_cyc[i].base );
        puts( "PASS zero-inplace off + pgw off: never zeroes, table untouched" );
        return 0;
    }
    if (mode == 3)   /* the real sampler thread for ~1.2 s while a thread faults file pages in */
    {
        size_t big = 256u << 20;
        char *f;
        real_clock = 1;
        ios_swap_n = 1;
        assert( !ftruncate( ios_swap_fd, big ) );
        f = mmap( NULL, big, PROT_READ | PROT_WRITE, MAP_SHARED, ios_swap_fd, 0 );
        assert( f != MAP_FAILED );
        ios_swap_ext[0].va = f; ios_swap_ext[0].len = big; ios_swap_ext[0].off = 0;
        pthread_mutex_lock( &virtual_mutex );
        ios_pgw_start();
        ios_pgw_start();   /* once */
        pthread_mutex_unlock( &virtual_mutex );
        for (int round = 0; round < 3; round++)
        {
            for (size_t o = 0; o < big; o += 0x4000) f[o] = (char)o;
            msync( f, big, MS_KILLPAGES );
        }
        usleep( 700000 );
        puts( "PASS sampler thread ran" );
        return 0;
    }
    if (mode == 4 || mode == 5)   /* disk-cache on (4) / env.MADEIRA_SWAP_DISK_CACHE=0 (5) */
    {
        unsigned long long real, got;
        struct stat st;
        fstat( ios_swap_fd, &st ); real = (unsigned long long)st.st_blocks * 512; fstat_calls = 0;
        got = ios_swap_disk_used();                    /* no refresh yet: fstat, as before */
        assert( got == real && fstat_calls == 1 );
        ios_swap_disk_refresh();                       /* the [pgw] thread's off-lock read */
        assert( fstat_calls == 2 );
        got = ios_swap_disk_used();
        if (mode == 4) assert( got == real && fstat_calls == 2 && ios_swap_disk_cache_hits == 1 );
        else assert( got == real && fstat_calls == 3 && ios_swap_disk_cache_hits == 0 );
        fake_ns += 11000000000ull;                     /* older than 10 s: fstat again */
        got = ios_swap_disk_used();
        assert( got == real && fstat_calls == (mode == 4 ? 3 : 4) );
        puts( mode == 4 ? "PASS disk-cache: fresh off-lock size used, stale or missing -> fstat" : "PASS disk-cache off: fstat every time" );
        return 0;
    }
    if (mode == 2)   /* env.MADEIRA_SWAP_ZERO_INPLACE=0 alone: no zeroing, statistics still kept */
    {
        assert( !ios_swap_zero_inplace( r, len ) ); fake_ns += 1000000; ios_swap_cyc_recommit( r );
        fake_ns += 1000000; assert( !ios_swap_zero_inplace( r, len ) );
        all( p, total, 0x5a );
        assert( ios_swap_cyc_decs == 2 && ios_swap_zip_n == 0 && ios_swap_cyc_rec[1] == 1 );
        puts( "PASS zero-inplace off: never zeroes, recommit statistics kept" );
        return 0;
    }

    /* cold first decommit: rotate as before */
    assert( !ios_swap_zero_inplace( r, len ) && ios_swap_zip_skip[IOS_ZIP_COLD] == 1 );
    all( p, total, 0x5a );
    /* two decommits without a recommit in between: still cold */
    fake_ns += 1000000;
    assert( !ios_swap_zero_inplace( r, len ) && ios_swap_zip_skip[IOS_ZIP_COLD] == 2 );
    /* recommit 5 ms later: bucket <10 ms */
    fake_ns += 5000000;
    ios_swap_cyc_recommit( r );
    assert( ios_swap_cyc_rec[1] == 1 );
    ios_swap_cyc_recommit( r );            /* a second commit of the same base is not a new recommit */
    assert( ios_swap_cyc_rec[1] == 1 );
    /* hot decommit, all resident and RW: zeroed in place */
    fake_ns += 1000000;
    assert( ios_swap_zero_inplace( r, len ) == 1 && ios_swap_zip_n == 1 && ios_swap_zip_bytes == len );
    all( r, len, 0 );
    all( p, 4 * pg, 0x5a );
    all( r + len, total - 4 * pg - len, 0x5a );
    assert( !msync( p, total, MS_SYNC ) );
    {
        char buf[0x8000];
        assert( pread( ios_swap_fd, buf, len, total + 4 * pg ) == (ssize_t)len );
        all( buf, len, 0 );
        assert( pread( ios_swap_fd, buf, pg, total + 3 * pg ) == (ssize_t)pg );
        all( buf, pg, 0x5a );
    }
    /* recommit after 2.5 s: hot window (2000 ms) has run out -> cold */
    memset( r, 0x11, len );
    fake_ns += 2500000000ull; ios_swap_cyc_recommit( r ); assert( ios_swap_cyc_rec[4] == 1 );
    fake_ns += 1000000;
    assert( !ios_swap_zero_inplace( r, len ) && ios_swap_zip_skip[IOS_ZIP_COLD] == 3 );
    all( r, len, 0x11 );
    /* each refusal leaves memory alone and counts its reason */
#define HOT() do { fake_ns += 1000000; ios_swap_cyc_recommit( r ); fake_ns += 1000000; } while (0)
    HOT(); vprots[(r - p) / 0x1000 + 1] = VPROT_COMMITTED | VPROT_READ;   /* read-only guest page */
    assert( !ios_swap_zero_inplace( r, len ) && ios_swap_zip_skip[IOS_ZIP_PROT] == 1 ); all( r, len, 0x11 );
    vprots[(r - p) / 0x1000 + 1] = VPROT_COMMITTED | VPROT_READ | VPROT_WRITE;
    HOT(); vprots[(r - p) / 0x1000 + 5] |= VPROT_GUARD;
    assert( !ios_swap_zero_inplace( r, len ) && ios_swap_zip_skip[IOS_ZIP_PROT] == 2 ); all( r, len, 0x11 );
    vprots[(r - p) / 0x1000 + 5] &= ~VPROT_GUARD;
    HOT(); vprots[(r - p) / 0x1000 + 2] = VPROT_READ | VPROT_WRITE;    /* uncommitted guest page */
    assert( !ios_swap_zero_inplace( r, len ) && ios_swap_zip_skip[IOS_ZIP_PROT] == 3 ); all( r, len, 0x11 );
    vprots[(r - p) / 0x1000 + 2] = VPROT_COMMITTED | VPROT_READ | VPROT_WRITE;
    HOT(); ios_swap_ext[0].resv = 0;
    assert( !ios_swap_zero_inplace( r, len ) && ios_swap_zip_skip[IOS_ZIP_EXTENT] == 1 ); all( r, len, 0x11 );
    ios_swap_ext[0].resv = 1;
    HOT(); ios_swap_ext[0].len = 5 * pg;   /* the range runs past the extent */
    assert( !ios_swap_zero_inplace( r, len ) && ios_swap_zip_skip[IOS_ZIP_EXTENT] == 2 ); all( r, len, 0x11 );
    ios_swap_ext[0].len = total;
    HOT(); ios_swap_zip_max = pg;
    assert( !ios_swap_zero_inplace( r, len ) && ios_swap_zip_skip[IOS_ZIP_BIG] == 1 ); all( r, len, 0x11 );
    ios_swap_zip_max = 1024 << 10;
    /* a host page that is not resident: written back, then MS_INVALIDATE drops it */
    assert( !msync( r, len, MS_SYNC ) );
    assert( !msync( r + pg, pg, MS_INVALIDATE ) );
    HOT();
    {
        char vec[2];
        assert( !mincore( r, len, vec ) );
        if (!(vec[1] & MINCORE_INCORE))
        {
            assert( !ios_swap_zero_inplace( r, len ) && ios_swap_zip_skip[IOS_ZIP_NOTRES] == 1 );
            all( r, len, 0x11 );   /* reading pages it back in: the data is still there */
            puts( "ok   a non-resident host page is refused" );
        }
        else puts( "note: MS_INVALIDATE left the page resident on this host; residency refusal not exercised" );
    }
    assert( ios_swap_zip_n == 1 );

    /* the classifier on a thread blocked in read(): a syscall, number in x16 */
    {
        pthread_t t;
        thread_act_t port;
        uint64_t ea = 0;
        long nr = -1;
        int cls;
        assert( !pipe( pipefd ) );
        assert( !pthread_create( &t, NULL, reader, NULL ) );
        while (!reader_in) usleep( 1000 );
        usleep( 50000 );
        port = pthread_mach_thread_np( t );
        cls = ios_pgw_classify( port, &ea, &nr );
        printf( "classify(blocked read) = %s nr=%ld\n", ios_pgw_cls_name[cls], nr );
        assert( cls == PGW_SYS && (nr == 3 || nr == 396) );
        ios_pgw_note( port, cls, ea, nr, 0 );
        assert( ios_pgw_w.unint == 1 && ios_pgw_w.nthr == 1 && ios_pgw_w.nsys == 1 );
        (void)!write( pipefd[1], "x", 1 );
        pthread_join( t, NULL );
    }
    /* the window line: printed, newline-terminated, bounded */
    {
        struct ios_pgw_snap a, b;
        mach_timebase_info_data_t tb;
        int out[2], saved;
        char buf[8192];
        ssize_t got;
        mach_timebase_info( &tb );
        ios_pgw_sample_ms = 20;
        ios_pgw_take( &a );
        b = a; b.ns += 2000000000ull; b.rot += 7; b.zip += 3; b.rec[2] += 5; b.decs += 9;
        for (int i = 0; i < IOS_PGW_THR + 4; i++)
        {
            int k = i < IOS_PGW_THR ? i : IOS_PGW_THR;
            if (i < IOS_PGW_THR) { ios_pgw_w.thr[i].tid = 1000 + i; ios_pgw_w.thr[i].n = i; ios_pgw_w.thr[i].cls[PGW_SWAP_ZF] = i; ios_pgw_w.nthr = i + 1; }
            else ios_pgw_w.thr_lost++;
            (void)k;
        }
        for (int i = 0; i < IOS_PGW_SITE; i++) { ios_pgw_w.site[i].cls = PGW_SWAP_RD; ios_pgw_w.site[i].mb = 0x77000 + i; ios_pgw_w.site[i].n = i + 1; }
        ios_pgw_w.nsite = IOS_PGW_SITE;
        assert( !pipe( out ) );
        saved = dup( 2 ); dup2( out[1], 2 );
        ios_pgw_emit( &a, &b, &tb );
        dup2( saved, 2 ); close( out[1] );
        got = read( out[0], buf, sizeof(buf) - 1 );
        assert( got > 0 && got <= 2600 && buf[got - 1] == '\n' );
        buf[got] = 0;
        printf( "%s", buf );
        assert( strstr( buf, "[pgw] " ) == buf && strstr( buf, " rot=7/" ) && strstr( buf, " inplace=3/" ) &&
                strstr( buf, "recommit<1ms/10/100/1s/10s/more=0/0/5/0/0/0 of 9" ) && strstr( buf, " | thr " ) &&
                strstr( buf, "swap-rd@0x7701f00000:32" ) && strstr( buf, "+4 more" ) );
    }
    puts( "PASS zero-inplace: hot ranges zeroed in place, every refusal untouched; classifier and [pgw] line" );
    return 0;
}
'''

with tempfile.TemporaryDirectory() as td:
    src = Path(td) / 'pgw.c'
    exe = Path(td) / 'pgw'
    src.write_text(prelude + disk + block + tests)
    cmd = ['cc', '-O1', '-g', '-Wall', '-Wno-unused-function', '-Wno-unused-variable', '-pthread',
           '-I' + str(root / 'build/ntdll-unix'), '-o', str(exe), str(src)]
    san = cmd[:1] + ['-fsanitize=address,undefined'] + cmd[1:]
    r = subprocess.run(san, capture_output=True, text=True)
    if r.returncode:
        r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        print(r.stderr[-4000:])
        check(False, 'the production block compiles against the stubs')
    else:
        for mode, env in ((0, {}), (1, {'MADEIRA_SWAP_ZERO_INPLACE': '0', 'MADEIRA_PGW': '0'}),
                          (2, {'MADEIRA_SWAP_ZERO_INPLACE': '0'}),
                          (3, {'MADEIRA_PGW_WINDOW_MS': '300', 'MADEIRA_PGW_SAMPLE_MS': '5'}),
                          (4, {}), (5, {'MADEIRA_SWAP_DISK_CACHE': '0'})):
            e = dict(os.environ)
            for k in ('MADEIRA_SWAP_ZERO_INPLACE', 'MADEIRA_PGW', 'MADEIRA_SWAP_ZERO_INPLACE_KB', 'MADEIRA_SWAP_ZERO_HOT_MS',
                      'MADEIRA_PGW_WINDOW_MS', 'MADEIRA_PGW_SAMPLE_MS', 'MADEIRA_PGW_PREFETCH_KB', 'MADEIRA_SWAP_DISK_CACHE'):
                e.pop(k, None)
            e.update(env)
            r = subprocess.run([str(exe), str(mode)], capture_output=True, text=True, env=e, timeout=60)
            sys.stdout.write(r.stdout)
            if r.returncode:
                sys.stdout.write(r.stderr[-3000:])
            check(r.returncode == 0, 'executed: mode %d %s' % (mode, env or 'defaults'))
            if mode == 3:
                lines = [l for l in r.stderr.splitlines() if l.startswith('[pgw] ')]
                print('\n'.join(lines[:4]))
                check(sum(' | samp ' in l for l in lines) >= 2 and lines[0].startswith('[pgw] madeira-bcd pgw on'),
                      'the sampler thread prints its config line and [pgw] windows')
                check(all(len(l) < 2600 for l in lines), '[pgw] lines stay bounded')

if failures:
    print('FAILED: ' + '; '.join(failures))
    sys.exit(1)
print('PASS check-pgw-zero-inplace')
