#!/usr/bin/env python3
"""Compile real runtime helpers on the host, then compile their collected MSL."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import shutil
import sys
from unittest.mock import patch
sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('precompile', ROOT / 'tools/precompile-msl.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

shader = '''#include <metal_stdlib>
using namespace metal;
kernel void madeira_test(device float* out [[buffer(0)]], uint i [[thread_position_in_grid]]) {
    out[i] = float(SIGNED_VALUE) + float(UNSIGNED_VALUE & 255ul) + FLOAT_VALUE;
}
'''

with tempfile.TemporaryDirectory(prefix='madeira-precomp-') as scratch:
    tmp = Path(scratch)
    source = (ROOT / 'research/MoltenVK/MoltenVK/MoltenVK/GPUObjects/MVKShaderModule.mm').read_text()
    key_function = source[source.index('uint64_t mvkLibCacheKey('):source.index('id<MTLLibrary> mvkLibCacheGet(')]
    helpers = source[source.index('// Disk I/O is serialized'):source.index('\n}\n\nid<MTLLibrary> MVKShaderLibraryCompiler::newMTLLibrary')]
    harness = r'''
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <atomic>
#include <algorithm>
#include <cassert>
#include <string>
#include <vector>
using namespace std;
#define MVK_XCODE_16 1
struct MSLSpecializationMacroInfo { string name; };
struct MVKShaderMacroValue { union { uint64_t ui64; } value; size_t size; };
NSString* mvkMadeiraGameCacheDirectory(id<MTLDevice> device) { return @"/unused"; }
''' + key_function + helpers + r'''
int main(int argc, char** argv) { @autoreleasepool {
    NSString* root = @(argv[1]);
    NSString* source = [NSString stringWithContentsOfFile:[root stringByAppendingPathComponent:@"input.metal"] encoding:NSUTF8StringEncoding error:nil];
    vector<pair<MSLSpecializationMacroInfo, MVKShaderMacroValue>> macros;
    size_t length;
    uint64_t key = mvkLibCacheKey(source, macros, 0, false, &length);
    assert(length == strlen(source.UTF8String));
    assert(key == mvkLibCacheKey(source, macros, 0, false, &length));
    assert(key != mvkLibCacheKey(source, macros, 1, false, &length));
    assert(key != mvkLibCacheKey(source, macros, 0, true, &length));
    macros.push_back({{"SIGNED_VALUE"}, {{42}, 8}});
    assert(key != mvkLibCacheKey(source, macros, 0, false, &length));
    MTLCompileOptions* options = [MTLCompileOptions new];
    options.languageVersion = MTLLanguageVersion3_1;
    options.mathMode = MTLMathModeRelaxed;
    options.mathFloatingPointFunctions = MTLMathFloatingPointFunctionsPrecise;
    options.preserveInvariance = YES;
    options.optimizationLevel = MTLLibraryOptimizationLevelDefault;
    options.preprocessorMacros = @{@"SIGNED_VALUE": @(-17), @"UNSIGNED_VALUE": @(UINT64_MAX), @"FLOAT_VALUE": @(1.25f)};
    NSDictionary* metadata = mvkPrecompOptions(options, key);
    dispatch_apply(64, dispatch_get_global_queue(QOS_CLASS_DEFAULT, 0), ^(size_t index) {
        mvkPrecompCollect(root, key, source, metadata);
    });
    // Joining the collector queue via its pending-byte counter avoids a fixed sleep.
    for (unsigned i = 0; g_mvkPrecompPending.load() && i < 10000; ++i) { usleep(1000); }
    assert(g_mvkPrecompPending.load() == 0);
    assert(g_mvkPrecompCollected.load() == (mvkPrecompCollectCap() ? 1 : 0));
    if (mvkPrecompCollectCap()) {
        NSString* full = [root stringByAppendingPathComponent:@"full"];
        NSString* fullSrc = [full stringByAppendingPathComponent:@"src"];
        [NSFileManager.defaultManager createDirectoryAtPath:fullSrc withIntermediateDirectories:YES attributes:nil error:nil];
        [[NSMutableData dataWithLength:1024 * 1024] writeToFile:[fullSrc stringByAppendingPathComponent:@"existing.metal"] atomically:YES];
        mvkPrecompCollect(full, key, source, metadata);
        mvkPrecompCollect([root stringByAppendingPathComponent:@"input.metal"], key, source, metadata);
        for (unsigned i = 0; g_mvkPrecompPending.load() && i < 10000; ++i) { usleep(1000); }
        assert(g_mvkPrecompPending.load() == 0);
        assert(g_mvkPrecompCollected.load() == 1);  // Full quota and unwritable path are harmless.
    }
    printf("%s\n", mvkPrecompKeyString(key).UTF8String);
    [options release];
} }
'''
    (tmp / 'input.metal').write_text(shader)
    (tmp / 'check.mm').write_text(harness)
    module.run(['xcrun', 'clang++', '-std=c++17', '-Wno-deprecated-declarations', '-framework', 'Foundation', '-framework', 'Metal', str(tmp / 'check.mm'), '-o', str(tmp / 'check')])
    key = module.run([str(tmp / 'check'), str(tmp)], env={**os.environ, 'MVK_MADEIRA_MSL_COLLECT_MB': '1'}).decode().strip()
    assert key == '67534f80cafcb457'  # Pin the existing runtime hash and 16-hex formatting.
    assert module.KEY.fullmatch(key)
    src = tmp / 'src' / (key + '.metal')
    assert src.read_text() == shader
    options_path = src.with_suffix('.json')
    options = json.loads(options_path.read_text())
    assert options['key'] == key
    assert options['preprocessorMacros'] == {'SIGNED_VALUE': '-17', 'UNSIGNED_VALUE': '18446744073709551615', 'FLOAT_VALUE': '1.25'}
    module.run([str(tmp / 'check'), str(tmp)], env={**os.environ, 'MVK_MADEIRA_MSL_COLLECT_MB': '0'})
    assert module.compile_one(src, 'test-toolchain')
    lib = tmp / 'lib' / (key + '.metallib')
    assert lib.stat().st_size > 0
    before = lib.stat().st_mtime_ns
    assert not module.compile_one(src, 'test-toolchain')
    assert lib.stat().st_mtime_ns == before
    assert module.compile_one(src, 'changed-toolchain')
    # Exercise every math-mode mapping and the older fastMathEnabled fallback.
    for mode in (0, 1, 2, None):
        variant = dict(options)
        if mode is None:
            variant.pop('mathMode')
            variant.pop('mathFloatingPointFunctions')
            variant['fastMathEnabled'] = False
        else:
            variant['mathMode'] = mode
            variant['mathFloatingPointFunctions'] = mode % 2
        options_path.write_text(json.dumps(variant))
        assert module.compile_one(src, 'test-toolchain')
    variant['fastMathEnabled'] = True
    variant['preserveInvariance'] = False
    options_path.write_text(json.dumps(variant))
    assert module.compile_one(src, 'test-toolchain')
    # Never publish a successful freshness stamp for a compiler failure.
    saved_source = src.read_text()
    src.write_text('this is not valid Metal')
    try:
        module.compile_one(src, 'test-toolchain')
        raise AssertionError('invalid MSL unexpectedly compiled')
    except RuntimeError:
        pass
    assert lib.with_suffix('.stamp').read_text() != module.fingerprint(src, 'test-toolchain')
    assert module.file_names({'result': {'files': [{'name': key}, {'relativePath': 'src/a.metal'}]}}) == {key, 'a.metal'}
    src.write_text(saved_source)
    # Exercise discovery, directory pull, xargs workers, delta push and repair with
    # a fake device filesystem. Compilation still uses the real iPhoneOS toolchain.
    remote = tmp / 'device'
    game = '0123456789abcdef'
    remote_src = remote / module.ROOT / game / 'msl/src'
    remote_src.mkdir(parents=True)
    shutil.copy2(src, remote_src / src.name)
    shutil.copy2(options_path, remote_src / options_path.name)
    mirror_home = tmp / 'home'
    mirror_home.mkdir()
    real_run = module.run
    transfers = []
    def simulated_run(command, **kwargs):
        if command[:3] != ['xcrun', 'devicectl', 'device']:
            return real_run(command, **kwargs)
        def arg(name):
            return command[command.index(name) + 1]
        assert arg('--domain-identifier') == 'com.iediot.madeora'
        assert arg('--domain-type') == 'appDataContainer'
        if command[3:5] == ['info', 'files']:
            directory = remote / arg('--subdirectory')
            Path(arg('--json-output')).write_text(json.dumps({'result': {'files': [{'name': p.name} for p in directory.iterdir()]}}))
        elif command[3:5] == ['copy', 'from']:
            shutil.copytree(remote / arg('--source'), arg('--destination'))
        elif command[3:5] == ['copy', 'to']:
            directory = Path(arg('--source'))
            transfers.extend(p.name for p in directory.iterdir())
            shutil.copytree(directory, remote / arg('--destination'), dirs_exist_ok=True)
        else:
            raise AssertionError(command)
        return b''
    module.run = simulated_run
    old_argv = sys.argv
    try:
        sys.argv = ['precompile-msl.py', 'test-device']
        with patch.object(Path, 'home', return_value=mirror_home):
            assert module.main() == 0
            assert transfers == [key + '.metallib']
            assert module.main() == 0
            assert len(transfers) == 1
            (remote_src.parent / 'lib' / (key + '.metallib')).unlink()
            assert module.main() == 0
            assert len(transfers) == 2
    finally:
        sys.argv = old_argv
        module.run = real_run
    print(f'PASS: concurrent runtime collection, quota disable, exact metadata, filename key {key}, iOS metallib, math flags, freshness, failure recovery, simulated device sync and eviction repair')
