#!/bin/bash
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
scratch=$(mktemp -d)
trap 'rm -f "$scratch/prefetch-test"; rmdir "$scratch"' EXIT
xcrun clang++ -std=c++17 -fblocks -fsanitize=address,undefined \
  -I"$root/research/MoltenVK/MoltenVK/MoltenVK/GPUObjects" \
  -framework Foundation -framework Metal "$root/tests/moltenvk/shader-prefetch.mm" \
  -o "$scratch/prefetch-test"
"$scratch/prefetch-test"
