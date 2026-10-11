#!/bin/bash
# Copy a MoltenVK frame dump (Documents/mvk-dump-<n>, made after tools/mvk-dump-fire.sh)
# off the device and convert it with tools/mvk-dump-to-png.py: PNGs next to the raw files
# and an index.html contact sheet of every pass in submission order.
#
#   tools/mvk-dump-pull.sh [device-udid] [mvk-dump-<n>] [local-parent-dir]
#
# Defaults: the iPad, the highest-numbered dump on the device, ./mvk-dumps. Extra
# converter options go in MVK_DUMP_PNG_ARGS (e.g. "--float clamp --gamma 1").
set -euo pipefail
DEVICE="${1:-00008142-000A38AE0147801C}"
NAME="${2:-}"
DEST="${3:-mvk-dumps}"
BUNDLE=com.iediot.madeora
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

if [ -z "$NAME" ]; then
    xcrun devicectl device info files --device "$DEVICE" \
        --domain-type appDataContainer --domain-identifier "$BUNDLE" \
        --subdirectory Documents --no-recurse --quiet --timeout 120 \
        --json-output "$TMP/files.json" > /dev/null
    NAME="$(python3 - "$TMP/files.json" <<'PY'
import json, re, sys
files = json.load(open(sys.argv[1])).get('result', {}).get('files', [])
best = None
for f in files:
    name = f.get('name') or f.get('relativePath', '').rstrip('/').split('/')[-1] if isinstance(f, dict) else f
    m = re.fullmatch(r'mvk-dump-(\d+)', name or '')
    if m and (best is None or int(m.group(1)) > best[0]):
        best = (int(m.group(1)), name)
print(best[1] if best else '')
PY
)"
    if [ -z "$NAME" ]; then
        echo "no Documents/mvk-dump-<n> on $DEVICE (run tools/mvk-dump-fire.sh while the game renders)" >&2
        exit 1
    fi
fi

mkdir -p "$DEST"
OUT="$DEST/$NAME"
rm -rf "$OUT"
echo "copying Documents/$NAME from $DEVICE to $OUT"
# devicectl copies directories recursively.
xcrun devicectl device copy from --device "$DEVICE" \
    --domain-type appDataContainer --domain-identifier "$BUNDLE" \
    --source "Documents/$NAME" --destination "$OUT" --quiet --timeout 3600
if [ ! -f "$OUT/index.json" ]; then
    echo "note: no index.json yet (frame still writing, or the app was killed); converting from index.jsonl" >&2
fi
# shellcheck disable=SC2086
python3 "$HERE/mvk-dump-to-png.py" ${MVK_DUMP_PNG_ARGS:-} "$OUT"
echo "open $OUT/index.html"
