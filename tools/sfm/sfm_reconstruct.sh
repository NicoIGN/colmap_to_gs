#!/usr/bin/env bash
# Dispatch de la reconstruction vers tools/sfm/backends/$COLMAP_METHOD.sh.
# Le backend lit la configuration exportée et les SFM_BACKEND_* ci-dessous.
set -Eeuo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"

case "$COLMAP_METHOD" in
  spirula|colmap|nerfstudio|hloc|orientations) ;;
  *) echo "COLMAP_METHOD inconnu : $COLMAP_METHOD" >&2; exit 1 ;;
esac

export SFM_BACKEND_IMAGES="${SFM_RECONSTRUCT_IMAGES:-$OUTPUT_DIR/images}"
export SFM_BACKEND_DIR="${SFM_RECONSTRUCT_DIR:-$OUTPUT_DIR/sfm}"
export SFM_BACKEND_CAMERA_MODE="${SFM_RECONSTRUCT_CAMERA_MODE:-$SFM_CAMERA_MODE}"
export SFM_BACKEND_MASK_DIR=""
if [[ "$SFM_MASKS" == on ]]; then
  SFM_BACKEND_MASK_DIR="${SFM_RECONSTRUCT_MASKS:-$OUTPUT_DIR/feature_masks}"
  export SFM_BACKEND_MASK_DIR
fi

exec bash "$repo_root/tools/sfm/backends/$COLMAP_METHOD.sh"
