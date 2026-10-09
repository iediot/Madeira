# MoltenVK fragment output regression check

Run from the repository root after `build/moltenvk-ios/build.sh` has applied the
SPIRV-Cross patch:

```sh
PATH=/usr/bin:/opt/homebrew/bin:$PATH TEST_METAL=1 bash tests/moltenvk/test-fragment-output.sh
```

Requires a host C++ compiler, `glslangValidator`, and (with `TEST_METAL=1`) Apple's
Metal compiler. The test builds the vendored SPIRV-Cross sources with MoltenVK's
`MVK_spirv_cross` namespace. It checks all float/uint/int conversion directions,
matching and unspecified types, unaffected locations, scalar array padding,
component-packed outputs, and early returns. `TEST_METAL=1` also compiles every
result for iOS.

The remap preserves SPIRV-Cross's existing four-component fragment output
padding. Metal permits a uint4 output for R32Uint and stores its red component.
Conversions happen at return, after the shader has finished using its original
output types. No numeric conversion is emitted for matching locations.

SPIRV-Cross is an ignored nested repository. Its changes are saved separately in
`patches/spirv-cross-moltenvk-output-types.patch`; the iOS build applies this patch
and rebuilds the dependency framework before building MoltenVK.
