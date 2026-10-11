#!/bin/bash
# Arm MoltenVK's one-frame dump on the device: copies an empty Documents/mvk-dump-now
# into Madeira's container. MoltenVK checks for it every 30 presents, removes it, and
# dumps the next whole frame to Documents/mvk-dump-<n>/ (see mvkMadeiraFrameDumpTick in
# research/MoltenVK/.../MVKCommandBuffer.mm; [mvk-dump] lines in the log). Fetch and
# convert the result with tools/mvk-dump-pull.sh.
#
#   tools/mvk-dump-fire.sh [device-udid]
set -euo pipefail
DEVICE="${1:-00008142-000A38AE0147801C}"
BUNDLE=com.iediot.madeora
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
: > "$TMP/mvk-dump-now"
xcrun devicectl device copy to --device "$DEVICE" \
    --domain-type appDataContainer --domain-identifier "$BUNDLE" \
    --source "$TMP/mvk-dump-now" --destination Documents/mvk-dump-now --quiet --timeout 120
echo "trigger placed on $DEVICE; the dump starts within 30 presents"
