#!/usr/bin/env python3
"""Execute the production admission wait with concurrent producers, ASan/UBSan."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
objects = root / 'research/MoltenVK/MoltenVK/MoltenVK/GPUObjects'
device = (objects / 'MVKDevice.mm').read_text()
start = device.index('\tvoid waitForAdmission(')
method = device[start:device.index('\n\tMVKDevice* _device;', start)]
harness = r'''
#include "MVKMadeiraAdmission.h"
#include <atomic>
#include <cassert>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <mutex>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>
static std::atomic<bool> pressure{false};
static bool mvkMadeiraMemoryPressureActive() { return pressure; }
static uint64_t mvkTimeNow() { return std::chrono::steady_clock::now().time_since_epoch().count(); }
struct Scheduler {
 std::mutex _lock;
 std::condition_variable _cv;
 std::unordered_map<std::string, int> _jobs;
''' + method + r'''
 void add(std::string key) {
   std::unique_lock<std::mutex> lock(_lock);
   waitForAdmission(lock,key);
   ++_jobs[key];
   assert(_jobs.size() <= mvkMadeiraAdmissionCapacity(false));
 }
 void remove(std::string key) {
   { std::lock_guard<std::mutex> lock(_lock); _jobs.erase(key); }
   _cv.notify_all();
 }
};
int main(int argc, char**) {
 if (argc > 2) {
   unsetenv("MVK_MADEIRA_ASYNC_ADMISSION");
   assert(!mvkMadeiraAdmissionEnabled());
   puts("PASS: admission is off unless MVK_MADEIRA_ASYNC_ADMISSION=1");
   return 0;
 }
 if (argc > 1) {
   setenv("MVK_MADEIRA_ASYNC_ADMISSION","0",1);
   setenv("MVK_MADEIRA_ASYNC_MAX_JOBS","1",1);
   assert(!mvkMadeiraAdmissionEnabled());
   Scheduler s;
   s._jobs.emplace("existing", 1);
   std::unique_lock<std::mutex> lock(s._lock);
   s.waitForAdmission(lock,"new");
   pressure=true;
   s.waitForAdmission(lock,"new");
   puts("PASS: explicit off switch never blocks admission, including under pressure");
   return 0;
 }
 setenv("MVK_MADEIRA_ASYNC_ADMISSION","1",1);
 assert(mvkMadeiraAdmissionLimit(nullptr,256)==256);
 for (auto s : {"", "0", "-1", " 1", "4097", "2x", "9999999999999999999999"})
   assert(mvkMadeiraAdmissionLimit(s,256)==256);
 assert(mvkMadeiraAdmissionLimit("12",256)==12);
 setenv("MVK_MADEIRA_ASYNC_MAX_JOBS","4",1);
 setenv("MVK_MADEIRA_ASYNC_PRESSURE_JOBS","2",1);
 Scheduler s;
 for(int i=0;i<4;i++) s.add(std::to_string(i));
 s.add("0"); // dedupe cannot wait behind its own full queue
 std::atomic<bool> entered{false}, done{false};
 std::thread producer([&]{ entered=true; s.add("new"); done=true; });
 while(!entered) std::this_thread::yield();
 std::this_thread::sleep_for(std::chrono::milliseconds(20));
 assert(!done);
 pressure=true;
 s.remove("0"); s.remove("1");
 std::this_thread::sleep_for(std::chrono::milliseconds(20));
 assert(!done); // downshift: existing jobs drain, never canceled
 s.remove("2"); producer.join(); assert(done);
 assert(s._jobs.size()==2);
 s.add("new"); assert(s._jobs.size()==2);
 s.remove("3"); s.remove("new");
 pressure=false;
 std::vector<std::thread> producers;
 for(int i=0;i<32;i++) producers.emplace_back([&,i]{ auto k=std::to_string(i); s.add(k); s.remove(k); });
 for(auto& p:producers)p.join();
 assert(s._jobs.empty());
 puts("PASS: bounded concurrent admission, dedupe at capacity, pressure drain and no lost jobs");
}
'''
with tempfile.TemporaryDirectory(prefix='mvk-admission-') as directory:
    exe = Path(directory) / 'test'
    subprocess.run(['xcrun', 'clang++', '-std=c++17', '-x', 'c++', '-pthread',
                    '-fsanitize=address,undefined', '-I', str(objects), '-o', str(exe), '-'],
                   input=harness, text=True, check=True)
    subprocess.run([str(exe)], check=True, timeout=20)
    subprocess.run([str(exe), 'default-off'], check=True, timeout=20)
    subprocess.run([str(exe), 'default-is-off', 'check'], check=True, timeout=20)
for signature in ('MTLRenderPipelineDescriptor* descriptor,', 'MTLComputePipelineDescriptor* descriptor, ComputeCompletion'):
    section = device[device.index('bool enqueue(const std::string& key, ' + signature):]
    assert section.index('waitForAdmission(lock, key)') < section.index('[descriptor copy]')
