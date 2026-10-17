#!/usr/bin/env python3
"""Exercise the production attachment-store learning state machine under sanitizers."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
objects = root / 'research/MoltenVK/MoltenVK/MoltenVK/GPUObjects'
harness = r'''
#include "MVKMadeiraStoreElision.h"
#include <cassert>
#include <cstdio>

static MVKMadeiraStoreSignature sig(unsigned attachment) {
 return { 0x1234, 2, attachment, 1, 1920, 1080 };
}
static MVKMadeiraStoreMetadata meta() { return { 1920ull * 1080 * 4, 37, 1920, 1080, "RGBA8" }; }

int main() {
 unsetenv("MVK_MADEIRA_STORE_ELISION");
 unsetenv("MVK_MADEIRA_STORE_ELISION_FRAMES");
 MVKMadeiraStoreElisionTracker off;
 assert(!off.enabled() && off.mode() == 0);

 MVKMadeiraStoreElisionTracker learn(1, 3);
 for (uint64_t frame = 1; frame <= 4; ++frame) {
   assert(!learn.shouldElide(sig(0), meta(), false));
   learn.stored(7, 1, sig(0), meta(), false, frame);
   learn.overwrite(7, frame + 1);
 }
 auto learned = learn.snapshot();
 assert(learned.tracked == 1 && learned.eligible == 1 && learned.elided == 0);

 MVKMadeiraStoreElisionTracker apply(2, 3);
 for (uint64_t frame = 1; frame <= 3; ++frame) {
   bool elide = apply.shouldElide(sig(0), meta(), false);
   assert(!elide);
   apply.stored(8, 1, sig(0), meta(), elide, frame);
   apply.overwrite(8, frame + 1);
 }
 assert(apply.shouldElide(sig(0), meta(), false));
 apply.stored(8, 1, sig(0), meta(), true, 4);
 apply.consume(8, 5, "rare-read");
 auto reverted = apply.snapshot();
 assert(reverted.reverted == 1 && reverted.eligible == 0 && reverted.elided == 0);
 assert(!apply.shouldElide(sig(0), meta(), true)); // permanent ineligibility beats static proof

 MVKMadeiraStoreElisionTracker consumed(2, 2);
 consumed.stored(9, 1, sig(1), meta(), false, 1);
 consumed.consume(9, 2, "sampled");
 consumed.stored(9, 1, sig(1), meta(), false, 2);
 consumed.overwrite(9, 3);
 consumed.stored(9, 1, sig(1), meta(), false, 3);
 consumed.overwrite(9, 4);
 assert(!consumed.shouldElide(sig(1), meta(), false));

 MVKMadeiraStoreElisionTracker staticSafe(1, 240);
 assert(staticSafe.shouldElide(sig(2), meta(), true));
 staticSafe.stored(10, 1, sig(2), meta(), true, 1);
 staticSafe.overwrite(10, 2);
 auto snapshot = staticSafe.snapshot();
 assert(snapshot.elided == 1 && snapshot.estimatedBytesPerFrame == meta().bytes);

 MVKMadeiraStoreElisionTracker sameFrame(2, 2);
 sameFrame.stored(11, 1, sig(3), meta(), false, 1);
 sameFrame.overwrite(11, 2);
 sameFrame.stored(11, 1, sig(3), meta(), false, 2);
 sameFrame.overwrite(11, 2);
 assert(!sameFrame.shouldElide(sig(3), meta(), false));
 puts("PASS: store learning, learn-only, threshold, static proof, revert and permanent disqualification");
}
'''

with tempfile.TemporaryDirectory(prefix='mvk-store-elision-') as directory:
    exe = Path(directory) / 'test'
    subprocess.run(['xcrun', 'clang++', '-std=c++17', '-x', 'c++', '-pthread',
                    '-fsanitize=address,undefined', '-I', str(objects), '-o', str(exe), '-'],
                   input=harness, text=True, check=True)
    subprocess.run([str(exe)], check=True, timeout=20)
