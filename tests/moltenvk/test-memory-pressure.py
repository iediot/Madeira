#!/usr/bin/env python3
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
objects = root / 'research/MoltenVK/MoltenVK/MoltenVK/GPUObjects'
device = (objects / 'MVKDevice.mm').read_text()
shader = (objects / 'MVKShaderModule.mm').read_text()
# Execute the actual PSO cache class against reference-counted Foundation states.
cache = device[device.index('class MVKPipelineStateCache {'):device.index('\nMVKPipelineStateCache& mvkPipelineStateCache()')]
library = shader[shader.index('struct MVKLibCacheEntry'):shader.index('uint64_t mvkLibCacheKey(')]
library += shader[shader.index('id<MTLLibrary> mvkLibCacheGet('):shader.index('void mvkLibCacheNote(')]
library += shader[shader.index('void mvkMadeiraTrimLibraryCache()'):shader.index('MVKMadeiraLibraryStats mvkMadeiraLibraryStats()')]
harness = r'''
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <list>
#include <mutex>
#include <string>
#include <unordered_map>
#include <cassert>
#include <atomic>
#include "MVKMadeiraMemoryPressure.h"
#include "MVKMadeiraConversionCache.h"
static bool pressure;
bool mvkMadeiraMemoryPressureActive() { return pressure; }
static void mvkTimePSOCacheResult(bool) {}
template<class D> std::string mvkPSOKey(id<MTLDevice>, D *desc) { return std::to_string([desc unsignedLongLongValue]); }
struct TestConfig {
    int value;
    bool matches(const TestConfig& other) const { return value == other.value; }
    void alignWith(const TestConfig& other) { value = other.value; }
};
struct TestResult { std::vector<int> payload; };
''' + cache + library + r'''
int main() { @autoreleasepool {
    MVKMadeiraMemoryPressurePolicy policy;
    assert(!policy.update(0, 1000));
    assert(policy.update(511ull<<20, 2000) && policy.active);
    assert(!policy.update(1100ull<<20, 3000));
    assert(!policy.update(1100ull<<20, 32000) && policy.active);
    assert(policy.update(1100ull<<20, 33000) && !policy.active);
    assert(policy.update(100ull<<20, 34000));
    policy.update(1100ull<<20, 35000); policy.update(800ull<<20, 60000);
    assert(!policy.update(1100ull<<20, 66000) && policy.active);
    assert(policy.update(1100ull<<20, 96000) && !policy.active);
    pressure = true;
    assert(mvkMadeiraPressureCapacity(0, 128) == 0);
    assert(mvkMadeiraPressureCapacity(20, 128) == 20);
    assert(mvkMadeiraPressureCapacity(1024, 128) == 256);
    assert(mvkMadeiraPressureCapacity(1536, 128, 2) == 768);
    pressure = false;
    setenv("MVK_MADEIRA_PSO_CACHE", "1024", 1);
    MVKPipelineStateCache cache;
    for (unsigned i = 0; i < 1024; ++i) cache.put(nil, @(i), [NSObject new]);
    assert(cache.occupancy().first == 1024);
    id held = cache.get<NSNumber, id>(nil, @0);
    assert(held);
    for (unsigned i = 1; i < 1024; ++i) {
        id other = cache.get<NSNumber, id>(nil, @(i)); assert(other); [other release];
    }
    pressure = true; cache.trim();
    assert(cache.occupancy().first == 256 && cache.occupancy().second == 256);
    assert((!cache.get<NSNumber, id>(nil, @0))); /* held state really was evicted */
    assert([held isKindOfClass:NSObject.class]); [held release];
    pressure = false;
    assert(cache.occupancy().second == 1024);
    for (unsigned i = 0; i < 256; ++i) {
        id library = [NSObject new]; mvkLibCachePut(i, 32, (id<MTLLibrary>)library); [library release];
    }
    id heldLibrary = mvkLibCacheGet(0, 32);
    for (unsigned i = 1; i < 256; ++i) { id other = mvkLibCacheGet(i, 32); [other release]; }
    pressure = true; mvkMadeiraTrimLibraryCache();
    assert(g_mvkLibCache->size() == 64 && !mvkLibCacheGet(0, 32));
    assert([heldLibrary isKindOfClass:NSObject.class]); [heldLibrary release];
    pressure = false; assert(mvkLibCacheCap() == 256);
    MVKMadeiraConversionCache<TestConfig, TestResult> conversions(1024, 8);
    for (uint64_t i = 0; i < 6; ++i) {
        std::vector<uint32_t> spirv = {(uint32_t)i};
        conversions.put(i, spirv, TestConfig{(int)i}, TestResult{{(int)i}}, 128);
    }
    assert(conversions.snapshot().bytes == 768);
    conversions.trim(256);
    assert(conversions.snapshot().entries == 2 && conversions.snapshot().bytes == 256);
    assert(conversions.snapshot().evictions == 4);
    for (auto& entry : *g_mvkLibCache) [entry.second.lib release];
    delete g_mvkLibCache; delete g_mvkLibLRU;
    puts("PASS: pressure hysteresis, actual PSO/library/conversion trims and evicted held references");
} }
'''
# Fix test-owned state references: cache.put retains its own reference.
harness = harness.replace('cache.put(nil, @(i), [NSObject new]);',
                          '{ id state = [NSObject new]; cache.put(nil, @(i), state); [state release]; }')
with tempfile.TemporaryDirectory(prefix='madeira-pressure-test-') as directory:
    exe = Path(directory) / 'pressure'
    subprocess.run(['xcrun','clang++','-std=c++17','-x','objective-c++','-fsanitize=address,undefined',
                    '-I',str(objects),'-framework','Foundation','-framework','Metal','-o',str(exe),'-'],
                   input=harness,text=True,check=True)
    subprocess.run([str(exe)],check=True)
assert 'while (g_mvkLibCache->size() > mvkLibCacheCap())' in shader
assert 'if (!_memoryLogThread.joinable())' in device
assert '#if MVK_IOS\n' in device and 'os_proc_available_memory()' in device
assert '[mem-breakdown]' in device and 'malloc_zone_pressure_relief(nullptr, 0)' in device
