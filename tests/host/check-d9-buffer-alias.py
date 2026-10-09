#!/usr/bin/env python3
"""Exercise production alias Lock transitions with a deterministic GPU fence.

No Wine/Metal runtime is needed. The two production prepareAliasLock bodies
run against a small allocation/fence harness, checking observable bytes and
pointer lifetime across draws, nested locks, DISCARD, completion, and OOM.
This does not validate Metal coherence or the host's translated-store faults.
"""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / 'dxmt/src/d3d9/d3d9_buffer.cpp').read_text()


def method(kind):
    start = source.index(f'HRESULT\nMTLD3D9{kind}Buffer::prepareAliasLock(')
    end = source.index('\nHRESULT STDMETHODCALLTYPE', start)
    return source[start:end].replace(f'MTLD3D9{kind}Buffer', 'TestBuffer')


harness = r'''
#include <atomic>
#include <cassert>
#include <cstdint>
#include <cstring>
#include <cstdlib>
#include <deque>
#include <memory>
#include <vector>
using HRESULT = int;
using DWORD = unsigned;
constexpr int D3D_OK = 0, D3DERR_DEVICELOST = -1, D3DERR_OUTOFVIDEOMEMORY = -2;
constexpr unsigned D3DLOCK_NOOVERWRITE = 1, D3DLOCK_READONLY = 2, D3DLOCK_DISCARD = 4;
namespace dxmt {
template<class T> struct Rc : std::shared_ptr<T> {
  using std::shared_ptr<T>::shared_ptr;
  Rc() = default;
  Rc(T *p) : std::shared_ptr<T>(p) {}
  T *ptr() const { return this->get(); }
};
struct Allocation {
  std::vector<unsigned char> bytes = std::vector<unsigned char>(64);
  bool buffer() const { return true; }
  void *mappedMemory(unsigned) { return bytes.data(); }
  size_t length() const { return bytes.size(); }
};
struct Buffer {
  Rc<Allocation> current = new Allocation;
  void rename(Rc<Allocation> value) { current = std::move(value); }
};
enum class BufferAllocationFlag { CpuWriteCombined };
struct DynamicBuffer {
  Rc<Allocation> name;
  std::deque<std::pair<uint64_t, Rc<Allocation>>> retired;
  bool fail = false;
  unsigned allocations = 0;
  DynamicBuffer(Buffer *b, BufferAllocationFlag) : name(b->current) {}
  Rc<Allocation> allocate(uint64_t done, bool *minted) {
    ++allocations;
    if (fail) return {};
    if (!retired.empty() && retired.front().first <= done) {
      auto result = retired.front().second;
      retired.pop_front();
      *minted = false;
      return result;
    }
    *minted = true;
    return new Allocation;
  }
  void updateImmediateName(uint64_t seq, Rc<Allocation> value, unsigned, bool) {
    retired.emplace_back(seq, name);
    name = std::move(value);
  }
  Rc<Allocation> immediateName() { return name; }
  void *immediateMappedMemory() { return name->mappedMemory(0); }
};
}
using namespace dxmt;
struct Device {
  std::atomic<uint64_t> m_cachedSignaled{0};
  uint64_t m_currentCmdSeq = 1;
  int m_metalDevice = 0;
  unsigned waits = 0, commits = 0;
  bool fail_wait = false;
  void forceFlushAndCommit() { ++commits; ++m_currentCmdSeq; }
  bool waitForGpuOrDeviceError(uint64_t seq) {
    ++waits;
    if (fail_wait) return false;
    m_cachedSignaled = seq;
    return true;
  }
  void noteDynamicRenameBytes(size_t) {}
};
bool fail_fallback = false;
bool allocateD3D9BufferStorage(int, unsigned size, int, Rc<Buffer> &out, void *&mirror, bool &watch, bool alias) {
  assert(!alias);
  if (fail_fallback) return false;
  out = new Buffer;
  mirror = std::malloc(size);
  watch = true;
  return mirror != nullptr;
}
unsigned copies = 0, discards = 0;
void recordAlias(bool, bool, bool discard, bool copied) { discards += discard; copies += copied; }
struct TestBuffer {
  Device device;
  Device *m_device = &device;
  Rc<Buffer> m_dxmtBuffer = new Buffer;
  Rc<DynamicBuffer> m_dynamic = new DynamicBuffer(m_dxmtBuffer.ptr(), BufferAllocationFlag::CpuWriteCombined);
  void *m_hostPtr = m_dynamic->immediateMappedMemory();
  unsigned m_size = 64;
  int m_pool = 0;
  bool m_writeWatch = false, m_aliased = true;
  struct { unsigned count = 0; } m_lockCount;
  struct { bool whole = true; } m_refreshState;
  uint64_t m_lastUseSeq = 0;
  ~TestBuffer() { if (!m_aliased) std::free(m_hostPtr); }
  HRESULT prepareAliasLock(DWORD flags);
};
'''
checks = r'''
int main() {
  TestBuffer b;
  std::memset(b.m_hostPtr, 0x31, b.m_size);
  auto draw = b.m_dynamic->immediateName();
  b.m_lastUseSeq = 1; // Frozen draw, not yet encoded or completed.
  auto initial = b.m_hostPtr;
  assert(b.prepareAliasLock(D3DLOCK_NOOVERWRITE) == D3D_OK);
  assert(b.prepareAliasLock(D3DLOCK_READONLY) == D3D_OK);
  assert(b.m_hostPtr == initial && b.m_dynamic->allocations == 0 && b.device.waits == 0);
  assert(b.prepareAliasLock(0) == D3D_OK);
  assert(b.m_hostPtr != initial && std::memcmp(b.m_hostPtr, draw->bytes.data(), b.m_size) == 0);
  std::memset(b.m_hostPtr, 0x42, b.m_size);
  assert(draw->bytes.front() == 0x31 && draw->bytes.back() == 0x31 && copies == 1);
  auto idle = b.m_hostPtr;
  assert(b.prepareAliasLock(0) == D3D_OK && b.m_hostPtr == idle);
  b.m_lastUseSeq = 1;
  auto second_draw = b.m_dynamic->immediateName();
  assert(b.prepareAliasLock(D3DLOCK_DISCARD) == D3D_OK && b.m_hostPtr != idle);
  std::memset(b.m_hostPtr, 0x53, b.m_size);
  assert(second_draw->bytes.front() == 0x42 && discards == 1 && copies == 1);
  // Completion makes the first generation reusable, despite our test's Rc.
  b.device.m_cachedSignaled = 1;
  b.device.m_currentCmdSeq = 2;
  assert(b.prepareAliasLock(D3DLOCK_DISCARD) == D3D_OK && b.m_hostPtr == initial);
  assert(b.m_dxmtBuffer->current.ptr() == b.m_dynamic->immediateName().ptr());
  // A held pointer survives unsafe nested locks; NOOVERWRITE never waits.
  b.m_lockCount.count = 1;
  b.m_lastUseSeq = 2;
  assert(b.prepareAliasLock(D3DLOCK_NOOVERWRITE) == D3D_OK && b.device.waits == 0);
  assert(b.prepareAliasLock(D3DLOCK_DISCARD) == D3D_OK && b.m_hostPtr == initial && b.device.waits == 1);
  b.m_lastUseSeq = 3;
  b.device.fail_wait = true;
  assert(b.prepareAliasLock(0) == D3DERR_DEVICELOST && b.m_hostPtr == initial);
  b.m_lockCount.count = 0;
  b.m_dynamic->fail = true;
  fail_fallback = true;
  assert(b.prepareAliasLock(0) == D3DERR_OUTOFVIDEOMEMORY && b.m_aliased && b.m_hostPtr == initial);
  fail_fallback = false;
  std::memset(b.m_hostPtr, 0x64, b.m_size);
  auto pinned = b.m_dynamic->immediateName();
  assert(b.prepareAliasLock(0) == D3D_OK && !b.m_aliased && b.m_writeWatch);
  assert(b.m_hostPtr != initial && std::memcmp(b.m_hostPtr, pinned->bytes.data(), b.m_size) == 0);
  std::memset(b.m_hostPtr, 0x75, b.m_size);
  assert(pinned->bytes.front() == 0x64); // Fallback cannot mutate a frozen draw.
}
'''
with tempfile.TemporaryDirectory(prefix='d9-alias-') as tmp:
    for kind in ('Vertex', 'Index'):
        cpp = Path(tmp) / f'{kind}.cpp'
        binary = Path(tmp) / kind
        cpp.write_text(harness + method(kind) + checks)
        subprocess.run(['c++', '-std=c++20', '-Wall', '-Wextra', str(cpp), '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True)
        print(f'PASS: {kind} alias bytes, draw isolation, completion reuse, nested pointers, and OOM fallback')
