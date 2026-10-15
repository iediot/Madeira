#!/usr/bin/env python3
"""Exercise production no-skip waiting, failure and lock handoff."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / 'research/MoltenVK/MoltenVK/MoltenVK/GPUObjects/MVKPipeline.mm').read_text()
start = source.index('\tunique_lock<mutex> lock(_asyncPipelineLock);', source.index('bool MVKGraphicsPipeline::shouldSkipDraw'))
end = source.index('\n\tif (_asyncPipelinePending) {', start + 100)
prefix = source[start:end]
harness = r'''
#include <mutex>
#include <condition_variable>
#include <thread>
#include <atomic>
#include <vector>
#include <string>
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <chrono>
using namespace std;
uint64_t mvkTimeNow() { return chrono::steady_clock::now().time_since_epoch().count(); }
struct Device {
 atomic<int> waits{0};
 uint64_t beginAsyncPipelineWait(const vector<string>&) { ++waits; return 1; }
 void endAsyncPipelineWait(uint64_t) { --waits; }
};
struct Pipeline {
 mutex _asyncPipelineLock;
 condition_variable _asyncPipelineCV;
 unsigned _asyncPipelinePending=1;
 bool _asyncPipelineFailed=false;
 vector<string> _asyncPipelineKeys{"shader"};
 Device device;
 Device* getDevice() { return &device; }
 bool test(uint32_t drawCount=1) {
''' + prefix + r'''
 return _asyncPipelinePending || _asyncPipelineFailed;
 }
 void finish(bool failed) {
   { lock_guard<mutex> lock(_asyncPipelineLock); _asyncPipelinePending=0; _asyncPipelineFailed=failed; }
   _asyncPipelineCV.notify_all();
 }
};
int main(int argc,char**) {
 setenv("MVK_MADEIRA_ASYNC_NO_SKIP",argc>1?"0":"1",1);
 Pipeline p;
 if(argc>1) { assert(p.test()); puts("PASS: default path remains nonblocking"); return 0; }
 atomic<bool> returned{false};
 thread draw([&]{ assert(!p.test()); returned=true; });
 while(!p.device.waits) this_thread::yield();
 assert(!returned); p.finish(false); draw.join(); assert(returned && !p.device.waits);
 Pipeline bad;
 thread failed([&]{ assert(bad.test()); });
 while(!bad.device.waits) this_thread::yield();
 bad.finish(true); failed.join(); assert(!bad.device.waits);
 puts("PASS: no-skip waits release mutex, completion unblocks, failure remains failure");
}
'''
with tempfile.TemporaryDirectory(prefix='mvk-no-skip-') as directory:
    exe = Path(directory) / 'test'
    subprocess.run(['xcrun', 'clang++', '-std=c++17', '-x', 'c++', '-pthread',
                    '-fsanitize=address,undefined', '-o', str(exe), '-'],
                   input=harness, text=True, check=True)
    subprocess.run([str(exe)], check=True, timeout=20)
    subprocess.run([str(exe), 'off'], check=True, timeout=20)
