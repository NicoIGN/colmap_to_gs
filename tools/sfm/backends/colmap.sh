#!/usr/bin/env bash
# Backend COLMAP natif : reconstruct.py (COLMAP_METHOD=colmap).
set -Eeuo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
# Étapes recover et refine : sans objet pour cette méthode.
[[ "${SFM_BACKEND_STEP:-reconstruct}" == reconstruct ]] || exit 0
"$PYTHON_BIN" "$repo_root/tools/sfm/colmap/reconstruct.py"
