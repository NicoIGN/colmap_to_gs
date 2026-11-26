#!/usr/bin/env bash
# Backend HLOC : extraction, appariement et triangulation pycolmap (COLMAP_METHOD=hloc).
set -Eeuo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
mask_args=()
[[ -z "$SFM_BACKEND_MASK_DIR" ]] || mask_args=(--feature-masks "$SFM_BACKEND_MASK_DIR")
projection_args=()
if [[ "$HLOC_CAMERA_MODEL" == EQUIRECTANGULAR ]]; then
  projection_args=(--perspective-view-size "$SFM_EQUIRECTANGULAR_VIEW_SIZE")
fi
"$PYTHON_BIN" "$repo_root/tools/sfm/colmap/hloc_reconstruct.py" \
  --images "$SFM_BACKEND_IMAGES" --output "$SFM_BACKEND_DIR/sparse/0" --work "$SFM_BACKEND_DIR/hloc" \
  --features "$HLOC_FEATURES" --matcher "$HLOC_MATCHER" --pairs "$HLOC_PAIRS" \
  --retrieval "$HLOC_RETRIEVAL" --num-neighbors "$HLOC_NUM_NEIGHBORS" \
  --camera-model "$HLOC_CAMERA_MODEL" --camera-mode "$SFM_BACKEND_CAMERA_MODE" \
  ${mask_args[@]+"${mask_args[@]}"} ${projection_args[@]+"${projection_args[@]}"}
