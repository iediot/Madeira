"""Compile the actual profiler aggregation/report functions with host fixtures."""
from pathlib import Path
import subprocess
import tempfile
import os
root = Path(__file__).resolve().parents[2]
source = (root / 'build/ntdll-unix/perf_ios.c').read_text()
named = source[source.index('struct class_named_bucket'):source.index('static void class_report_fex')]
report = source[source.index('static void class_report_game(void)'):source.index('static void *perf_classifier')]
prefix = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#define CLASS_GAME_PCS 2048
#define CLASS_ROWS 8
struct class_game_bucket { uint64_t id,block,teb; uintptr_t pc; unsigned tid,hits; };
static struct class_game_bucket class_game_pcs[CLASS_GAME_PCS];
static unsigned class_game_dropped;
uint64_t ios_native_rip_from_hostpc(uint64_t b,uint64_t pc,const char **n) { return b ? pc : 0; }
const char *ios_guest_module_from_rip(uint64_t t,uint64_t rip,uint64_t *rva) {
  if (rip>=0x140000000ull && rip<0x150000000ull) { *rva=rip-0x140000000ull; return "DetroitBecomeHuman.exe"; }
  return NULL;
}
'''
suffix = r'''
int main(void) {
  class_game_pcs[0]=(struct class_game_bucket){1,1,0,0x14064d8b4ull,0x1a0,5};
  class_game_pcs[1]=(struct class_game_bucket){1,1,0,0x14064d8b8ull,0x1a0,7};
  class_game_pcs[2]=(struct class_game_bucket){1,1,0,0x14064d0f6ull,0x1a0,8};
  class_game_pcs[3]=(struct class_game_bucket){2,1,0,0x39112345ull,0xac,3};
  class_report_game();
  assert(class_game_pcs[0].hits==0);
}
'''
with tempfile.TemporaryDirectory(prefix='madeira-guest-pc-test-') as scratch:
    binary = str(Path(scratch) / 'guest-detail')
    subprocess.run(['xcrun','clang','-x','c','-fsanitize=address,undefined',
                    '-I'+str(root/'build/ntdll-unix'),'-o',binary,'-'],
                   input=prefix+named+report+suffix,text=True,check=True)
    for enabled in ('1','0'):
        out = subprocess.run([binary],env={**os.environ,'MADEIRA_PERF_CLASSIFY_FINE':enabled},
                             text=True,capture_output=True,check=True).stderr
        assert '[perf-classify-game] tid=01a0 DetroitBecomeHuman.exe+0x64d000 samples=20 pct=100.0' in out
        if enabled=='1':
            assert 'DetroitBecomeHuman.exe+0x64d8b0 samples=12 pct=60.0 bucket=16B' in out
            assert 'guest+0x39112340 samples=3 pct=100.0 bucket=16B' in out
        else:
            assert '[perf-classify-game-detail]' not in out
print('PASS actual guest-PC reports: page aggregation, 16B aggregation, denominator, unknown module, disable, reset')
