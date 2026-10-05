#!/usr/bin/env python3
"""Compile production stroke math and layout types; no UIKit/device required."""
from pathlib import Path
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / 'app/Madeira/TouchGamepad.swift').read_text()
motion = source.split('// MARK: - Mouse look motion (host-testable)', 1)[1].split('// MARK: - Mouse look touch lifetime', 1)[0]
content = (root / 'app/Madeira/ContentView.swift').read_text()
models = content[content.index('enum ControlAction:'):content.index('final class TouchControlsModel:')]
tests = r'''
var motion = TouchMouseLookMotion()
assert(motion.move(to: CGPoint(x: 100, y: 100), sensitivity: 2) == (0, 0))
motion.begin(at: CGPoint(x: 100, y: 100))
assert(motion.move(to: CGPoint(x: 104, y: 97), sensitivity: 2) == (8, -6))
assert(motion.move(to: CGPoint(x: 104, y: 97), sensitivity: 2) == (0, 0))
// Tiny motions accumulate equally in both directions instead of disappearing.
motion.begin(at: .zero)
var totalX: Int32 = 0, totalY: Int32 = 0
for i in 1...40 {
    let (x, y) = motion.move(to: CGPoint(x: Double(i) / 4, y: -Double(i) / 4), sensitivity: 0.5)
    totalX += x; totalY += y
}
assert(totalX == 5 && totalY == -5)
// A new stroke at a distant position must never snap the camera.
motion.begin(at: CGPoint(x: 900, y: 900))
assert(motion.move(to: CGPoint(x: 900, y: 900), sensitivity: 2) == (0, 0))
assert(motion.move(to: CGPoint(x: 901, y: 899), sensitivity: 2) == (2, -2))
motion.reset()
assert(motion.move(to: CGPoint(x: 1000, y: 1000), sensitivity: 2) == (0, 0))
motion.begin(at: .zero)
_ = motion.move(to: CGPoint(x: 0.75, y: 0.75), sensitivity: 1)
motion.reset(); motion.begin(at: .zero)
assert(motion.move(to: CGPoint(x: 0.5, y: 0.5), sensitivity: 1) == (0, 0))
// Bad/oversized input cannot trap or leave a backlog of motion.
motion.begin(at: .zero)
assert(motion.move(to: CGPoint(x: 100000, y: -100000), sensitivity: 8) == (30000, -30000))
assert(motion.move(to: CGPoint(x: 100000, y: -100000), sensitivity: 8) == (0, 0))
assert(motion.move(to: .zero, sensitivity: .nan) == (0, 0))
motion.begin(at: CGPoint(x: CGFloat.nan, y: 0))
assert(motion.move(to: .zero, sensitivity: 2) == (0, 0))
// New actions round-trip with existing controls; old JSON needs no migration.
let decoder = JSONDecoder(), encoder = JSONEncoder()
let legacy = Data("{\"id\":\"00000000-0000-0000-0000-000000000001\",\"nx\":0.2,\"ny\":0.7,\"scale\":1,\"action\":{\"mouseLeft\":{}}}".utf8)
let old = try decoder.decode(TouchControl.self, from: legacy)
assert(old.action == .mouseLeft && old.padBinding == nil)
var pad = old; pad.id = UUID(); pad.action = .mouseLook; pad.scale = 2
let controls = [old, pad]
assert(try decoder.decode([TouchControl].self, from: encoder.encode(controls)) == controls)
let size = pad.action.controlSize(diameter: 64)
assert(size.width > 150 && size.height == 128)
assert(!pad.action.isPad && pad.action.stickKeys == nil)
print("Touch mouse look: motion, resets, low sensitivity and layout compatibility passed")
'''
# assert's autoclosure cannot throw.
tests = tests.replace('assert(try decoder.decode([TouchControl].self, from: encoder.encode(controls)) == controls)',
                      'let restored = try decoder.decode([TouchControl].self, from: encoder.encode(controls))\nassert(restored == controls)')
with tempfile.TemporaryDirectory(prefix='madeira-touch-look-') as tmp:
    swift = Path(tmp) / 'main.swift'
    swift.write_text('import Foundation\n#if canImport(CoreGraphics)\nimport CoreGraphics\n#endif\n' + motion + models + tests)
    binary = Path(tmp) / 'check'
    subprocess.run([os.environ.get('SWIFTC', 'swiftc'), '-module-cache-path', str(Path(tmp) / 'cache'), str(swift), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
