#!/bin/bash
# Build DXMT's ARM64EC PE modules (d3d11.dll, dxgi.dll, winemetal.dll, ...)
# and stage them in app/Madeira/arm64ec-windows/.
#
# Needs the llvm-mingw toolchain in toolchains/ and a configured
# wine/build-arm64ec with these four targets built (docs/BUILDING.md step 3):
#   make -C wine/build-arm64ec tools/winebuild/winebuild \
#     libs/winecrt0/arm64ec-windows/libwinecrt0.a \
#     dlls/ntdll/arm64ec-windows/libntdll.a dlls/dbghelp/arm64ec-windows/libdbghelp.a
#
# dxmt/build-arm64ec-win.txt points at dxmt/toolchains/, which only exists in
# the old research/dxmt layout, so the cross file is generated here.
#
# Usage: build/dxmt-pe/build.sh [module...]   (default: d3d11)
set -eu
R="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TC="$R/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin"
B="$R/dxmt/build-arm64ec"
CROSS="$R/build/dxmt-pe/arm64ec-cross.txt"
export PATH="/usr/bin:$PATH"   # Apple ar/xxd first; Homebrew binutils breaks Mach-O archives

cat > "$CROSS" <<EOF
[binaries]
c = '$TC/arm64ec-w64-mingw32-clang'
cpp = '$TC/arm64ec-w64-mingw32-clang++'
ar = '$TC/arm64ec-w64-mingw32-ar'
strip = '$TC/arm64ec-w64-mingw32-strip'
windres = '$TC/arm64ec-w64-mingw32-windres'

[properties]
needs_exe_wrapper = true

[host_machine]
system = 'windows'
cpu_family = 'aarch64'
cpu = 'aarch64'
endian = 'little'
EOF

if [ ! -f "$B/build.ninja" ]; then
    meson setup "$B" "$R/dxmt" -Dbuildtype=release \
        -Dwine_build_path="$R/wine/build-arm64ec" --cross-file="$CROSS"
fi

MODULES=("$@")
[ ${#MODULES[@]} -gt 0 ] || MODULES=(d3d11)
for m in "${MODULES[@]}"; do
    out=$(cd "$B" && ninja -t targets all | grep -oE "^src/[a-z0-9_]+/$m\.dll" | head -1)
    [ -n "$out" ] || { echo "no target for $m.dll" >&2; exit 1; }
    ninja -C "$B" "$out"
    cp "$B/$out" "$R/app/Madeira/arm64ec-windows/$m.dll"
    ls -l "$R/app/Madeira/arm64ec-windows/$m.dll"
done
