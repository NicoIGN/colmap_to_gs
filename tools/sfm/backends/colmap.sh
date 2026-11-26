#!/usr/bin/env bash
# Backend COLMAP natif : reconstruct.py (COLMAP_METHOD=colmap).
set -Eeuo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
"$PYTHON_BIN" "$repo_root/tools/sfm/colmap/reconstruct.py"
