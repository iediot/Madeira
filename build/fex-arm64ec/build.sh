#!/bin/bash
# Configure (first time) and build the ARM64EC FEX module (libarm64ecfex.dll,
# shipped as xtajit64.dll), the CPU backend for 64-bit x86 programs.
# Same iOS host options as build/fex-wow64/build.sh: FEX_IOS_HOST selects the
# iOS-specific code paths (the shipped DLL carries them); the guest window is
# never enabled for ARM64EC (FEX's CMakeLists refuses it). The
# DISABLE_FIND_PACKAGE options keep CMake from picking up host (Homebrew)
# copies of fmt and friends, which cannot link into this Windows DLL.
set -eu
R="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PATH="$R/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin:$PATH"
B="$R/FEX/build-arm64ec-ios"
if [ ! -f "$B/CMakeCache.txt" ]; then
    cmake -S "$R/FEX" -B "$B" -G Ninja -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_TOOLCHAIN_FILE="$R/FEX/Data/CMake/toolchain_mingw.cmake" \
        -DMINGW_TRIPLE=arm64ec-w64-mingw32 \
        -DFEX_IOS_HOST_BUILD=ON -DCMAKE_C_FLAGS=-DFEX_IOS_HOST \
        -DCMAKE_CXX_FLAGS=-DFEX_IOS_HOST -DCMAKE_ASM_FLAGS=-DFEX_IOS_HOST \
        -DENABLE_LTO=OFF -DENABLE_ASSERTIONS=OFF -DENABLE_JEMALLOC_GLIBC_ALLOC=OFF \
        -DBUILD_TESTING=OFF -DBUILD_FEXCONFIG=OFF -DTUNE_ARCH=generic -DTUNE_CPU=none \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
        -DCMAKE_DISABLE_FIND_PACKAGE_fmt=ON -DCMAKE_DISABLE_FIND_PACKAGE_unordered_dense=ON \
        -DCMAKE_DISABLE_FIND_PACKAGE_range-v3=ON -DCMAKE_DISABLE_FIND_PACKAGE_xxhash=ON \
        -DCMAKE_DISABLE_FIND_PACKAGE_Zydis=ON -DCMAKE_DISABLE_FIND_PACKAGE_Zycore=ON
fi
cmake --build "$B" --target arm64ecfex
cp "$B/Bin/libarm64ecfex.dll" "$R/app/Madeira/arm64ec-windows/xtajit64.dll" && ls -l "$R/app/Madeira/arm64ec-windows/xtajit64.dll"
