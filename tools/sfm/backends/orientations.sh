#!/usr/bin/env bash
# Backend orientations : poses JSON explicites et PLY LiDAR, sans SfM (COLMAP_METHOD=orientations).
set -Eeuo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
"$PYTHON_BIN" "$repo_root/tools/sfm/colmap/orientations_to_colmap.py" \
  --orientations "$ORIENTATIONS_JSON" --points "$LIDAR_PLY" \
  --images "$SFM_BACKEND_IMAGES" --output "$SFM_BACKEND_DIR/sparse/0"
