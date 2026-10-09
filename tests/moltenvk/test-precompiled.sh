#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/../.."
exec python3 tests/moltenvk/test-precompiled.py
