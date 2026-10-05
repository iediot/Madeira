#!/bin/bash
# Fetch Wine Mono (the .NET runtime Wine runs .NET programs with -- Terraria and
# other XNA/.NET games) into the app bundle's resources, verified by SHA-256.
#
# The 42 MB .tar.xz ships in the app as is; Madeira unpacks it (232 MB) into
# Application Support the first time a .NET program is started (WineMono.swift)
# and links it in as C:\windows\mono\mono-2.0, where mscoree looks.
#
# The version must match wine/dlls/appwiz.cpl/addons.c MONO_VERSION.
# Wine Mono is MIT-licensed with bundled MIT/BSD/MS-PL components; its notices
# are inside the archive (support/, and lib/mono's license files).
set -eu
R="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VER=11.0.0
FILE="wine-mono-$VER-x86.tar.xz"
SHA=0cd723aa28897f7d7d2702eed6c72e4262255980e212bc2b3c94bfa234abe5fd
DEST="$R/app/Madeira/$FILE"

if [ -f "$DEST" ] && [ "$(shasum -a 256 "$DEST" | cut -d' ' -f1)" = "$SHA" ]; then
    echo "$FILE already present and verified"; exit 0
fi
curl -fL -o "$DEST.part" "https://dl.winehq.org/wine/wine-mono/$VER/$FILE"
GOT="$(shasum -a 256 "$DEST.part" | cut -d' ' -f1)"
[ "$GOT" = "$SHA" ] || { echo "SHA-256 mismatch for $FILE: $GOT" >&2; rm -f "$DEST.part"; exit 1; }
mv "$DEST.part" "$DEST"
echo "fetched $FILE ($(du -h "$DEST" | cut -f1))"
