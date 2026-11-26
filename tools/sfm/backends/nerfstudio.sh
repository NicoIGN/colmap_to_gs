#!/usr/bin/env bash
# Backend nerfstudio : reconstruct.py lance ns-process-data (COLMAP_METHOD=nerfstudio).
set -Eeuo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
"$PYTHON_BIN" "$repo_root/tools/sfm/colmap/reconstruct.py"
