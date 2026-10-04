# Local FEX and DXMT changes

The FEX and DXMT submodules point at upstream's pinned commits; this fork's
changes to them are kept here and applied on top (`git -C FEX apply --3way
../patches/fex-iediot.patch`, same for `dxmt`), then the DLLs are rebuilt with
`build/fex-arm64ec/build.sh`, `build/fex-wow64/build.sh`,
`build/dxmt-pe/build.sh d3d11` and `build/dxmt-ios/build.sh`.

- `fex-iediot.patch`: folded L1 lookup index (ml2111), `[lookup-stats]` (ml2110,
  `env.MADEIRA_FEX_LOOKUP_STATS = 1`), iOS host build guards.
- `dxmt-iediot.patch`: single-level texture halving for BC1/BC3, RGBA8 and R8/A8
  at any size (ml2100/2101/2113), texture census, Metal 4 atomic fix.

Regenerate both after editing a submodule; `dxmt_bcn_downscale.hpp` is a new
file, so add it with `git -C dxmt add -N` before `git -C dxmt diff`.
