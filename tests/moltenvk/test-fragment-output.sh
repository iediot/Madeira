#!/bin/bash
set -eu
R="$(cd "$(dirname "$0")/../.." && pwd)"
CROSS="$R/research/MoltenVK/External/SPIRV-Cross"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
c++ -std=c++14 -O0 -DSPIRV_CROSS_NAMESPACE_OVERRIDE=MVK_spirv_cross -I"$CROSS" \
    "$R/tests/moltenvk/fragment-output.cpp" \
    "$CROSS/spirv_msl.cpp" "$CROSS/spirv_glsl.cpp" "$CROSS/spirv_cross.cpp" \
    "$CROSS/spirv_parser.cpp" "$CROSS/spirv_cross_parsed_ir.cpp" "$CROSS/spirv_cfg.cpp" \
    -o "$TMP/check"
for source in float uint int; do
    case "$source" in float) vec=vec4;; uint) vec=uvec4;; int) vec=ivec4;; esac
    cat > "$TMP/input.frag" <<GLSL
#version 450
layout(location=0) out $vec color;
layout(location=1) out vec4 untouched;
void main() { color = $vec(3); untouched = vec4(1); }
GLSL
    glslangValidator -V "$TMP/input.frag" -o "$TMP/input.spv" > /dev/null
    for target in none float uint int; do
        "$TMP/check" "$TMP/input.spv" "$target" > "$TMP/output.metal"
        python3 - "$TMP/output.metal" "$source" "$target" <<'PY'
import sys
msl = open(sys.argv[1]).read()
source, target = sys.argv[2:]
if target == 'none' or source == target:
    assert '_attachment' not in msl, msl
else:
    assert f'{target}4 color [[color(0)]];' in msl, msl
    assert f'color = as_type<{target}4>(value.color);' in msl, msl
assert 'float4 untouched [[color(1)]];' in msl, msl
PY
        if [ "${TEST_METAL:-0}" = 1 ]; then
            xcrun -sdk iphoneos metal -fmodules-cache-path="$TMP/modules" -c "$TMP/output.metal" -o "$TMP/output.air"
        fi
    done
done
# Padding and flattened arrays use the same conversion boundary.
cat > "$TMP/input.frag" <<'GLSL'
#version 450
layout(location=0) out float color[2];
void main() { color[0] = 3.0; color[1] = 4.0; }
GLSL
glslangValidator -V "$TMP/input.frag" -o "$TMP/input.spv" > /dev/null
"$TMP/check" "$TMP/input.spv" uint > "$TMP/output.metal"
python3 - "$TMP/output.metal" <<'PY'
import sys
msl = open(sys.argv[1]).read()
assert 'uint4 color_0 [[color(0)]];' in msl, msl
assert 'color_0 = as_type<uint4>(value.color_0);' in msl, msl
assert 'float4 color_1 [[color(1)]];' in msl, msl
PY
if [ "${TEST_METAL:-0}" = 1 ]; then
    xcrun -sdk iphoneos metal -fmodules-cache-path="$TMP/modules" -c "$TMP/output.metal" -o "$TMP/output.air"
fi
# Packed components and early returns must convert only the final value.
cat > "$TMP/input.frag" <<'GLSL'
#version 450
layout(location=0, component=0) out vec2 rg;
layout(location=0, component=2) out vec2 ba;
layout(location=1) out vec4 untouched;
layout(location=0) in vec4 value;
void main() {
    rg = value.xy;
    ba = value.zw;
    untouched = value;
    if (value.x > 0.0) { return; }
    rg += vec2(0.5);
}
GLSL
glslangValidator -V "$TMP/input.frag" -o "$TMP/input.spv" > /dev/null
"$TMP/check" "$TMP/input.spv" uint > "$TMP/output.metal"
python3 - "$TMP/output.metal" <<'PYCODE'
import sys
msl = open(sys.argv[1]).read()
assert 'uint4 m_location_0 [[color(0)]];' in msl, msl
assert 'm_location_0 = as_type<uint4>(value.m_location_0);' in msl, msl
assert 'float4 untouched [[color(1)]];' in msl, msl
PYCODE
if [ "${TEST_METAL:-0}" = 1 ]; then
    xcrun -sdk iphoneos metal -fmodules-cache-path="$TMP/modules" -c "$TMP/output.metal" -o "$TMP/output.air"
fi
echo 'Fragment output numeric conversion tests passed.'
