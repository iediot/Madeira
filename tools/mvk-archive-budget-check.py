from pathlib import Path
import subprocess
import tempfile

# Compile the real archive class, with profiling stubs, against host Metal.
# The test never opens a game's archive directory or needs an iPad.
source=(Path(__file__).resolve().parents[1] / 'research/MoltenVK/MoltenVK/MoltenVK/GPUObjects/MVKDevice.mm').read_text()
start=source.index('class MVKBinaryArchiveCache {')
end=source.index('\nvoid MVKDevice::logBinaryArchiveMemory()',start)
prefix=r'''
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <objc/runtime.h>
#include <vector>
#include <mutex>
#include <atomic>
#include <algorithm>
#include <thread>
#include <cassert>
#include "MVKMadeiraMemoryPressure.h"
static bool testPressure;
bool mvkMadeiraMemoryPressureActive() { return testPressure; }
enum MVKArchivePipelineKind { kTest };
static const char kMVKArchiveShaderKey=0;
static NSString* testDirectory;
static NSString* mvkMadeiraGameCacheDirectory(id<MTLDevice>) { return testDirectory; }
static uint64_t mvkTimeNow() { return 0; }
static void mvkTimeAdd(int,uint64_t) {}
static void mvkTimeAddElapsed(int,uint64_t) {}
static void mvkTimeArchiveResult(bool,bool,uint64_t,MVKArchivePipelineKind) {}
static void mvkTimeArchiveSyncMiss(MVKArchivePipelineKind,uint64_t) {}
template<class D> std::string mvkPSOKey(id<MTLDevice>,D*) { return {}; }
#define private public
'''
suffix=r'''
#undef private
int main() { @autoreleasepool {
  testDirectory=[NSTemporaryDirectory() stringByAppendingPathComponent:NSUUID.UUID.UUIDString];
  setenv("MVK_MADEIRA_ARCHIVE_MB","1",1);
  MVKBinaryArchiveCache cache(MTLCreateSystemDefaultDevice());
  cache._budget=1024;
  std::atomic<bool> stop{false};
  std::thread reader([&] { while(!stop) {
    for (auto& shard:cache._shards) { std::lock_guard<std::mutex> lock(shard.lock);
      for(auto& page:shard.frozen) assert(page.archive!=nil);
    }
  }});
  for (int i=0;i<128;++i) {
    auto& shard=cache._shards[i%16];
    NSString* path=[testDirectory stringByAppendingPathComponent:[NSString stringWithFormat:@"test-%d",i]];
    [[NSMutableData dataWithLength:256] writeToFile:path atomically:YES];
    auto archive=(id<MTLBinaryArchive>)[NSObject new];
    { std::lock_guard<std::mutex> lock(shard.lock);
      cache.freeze(shard,archive,[NSURL fileURLWithPath:path]); }
    [archive release];
    cache.trimFrozenPages();
    assert(cache._frozenBytes<=1024);
    assert([NSFileManager.defaultManager fileExistsAtPath:path]);
  }
  assert(cache._frozenCount==4 && cache._evictions==124);
  /* Lower configured budgets are never raised by pressure mode. */
  testPressure=true;
  cache.trimFrozenPages();
  assert(cache._frozenCount==4);
  testPressure=false;
  cache._budget=512ull<<20;
  for(auto& shard:cache._shards) for(auto& page:shard.frozen) page.bytes=128ull<<20;
  cache._frozenBytes=512ull<<20;
  testPressure=true;
  cache.trimFrozenPages();
  assert(cache._frozenCount==2 && cache._frozenBytes==(256ull<<20));
  testPressure=false;
  cache._budget=0;
  cache.trimFrozenPages();
  assert(cache._frozenCount==2);
  stop=true; reader.join();
  cache.logMemory();
  NSLog(@"PASS actual archive class: cap, cross-shard eviction, concurrent readers, disk preserved, unlimited mode");
  [NSFileManager.defaultManager removeItemAtPath:testDirectory error:nil];
} }
'''
with tempfile.TemporaryDirectory(prefix='madeira-archive-test-') as scratch:
    binary=str(Path(scratch) / 'archive-check')
    include=Path(__file__).resolve().parents[1] / 'research/MoltenVK/MoltenVK/MoltenVK/GPUObjects'
    subprocess.run(['xcrun','clang++','-std=c++17','-I',str(include),'-x','objective-c++','-fblocks','-fsanitize=address,undefined','-framework','Foundation','-framework','Metal','-o',binary,'-'],input=prefix+source[start:end]+suffix,text=True,check=True)
    subprocess.run([binary],check=True)
