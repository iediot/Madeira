#!/bin/bash
# Pull collected MSL, compile on the Mac, and install persistent iOS libraries.
set -euo pipefail
exec python3 "$(cd "$(dirname "$0")" && pwd)/precompile-msl.py" "$@"
