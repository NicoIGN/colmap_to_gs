#!/usr/bin/env bash
# Cœur SfM partagé par run_standard_images.sh (images, vidéo) et run_panoramax.sh.
# Gère la reprise et la vérification des sorties. Chaque méthode est un backend
# tools/sfm/backends/$COLMAP_METHOD.sh appelé selon trois étapes (SFM_BACKEND_STEP) :
#   recover     réparation d'une interruption précédente (sans objet pour la plupart)
#   reconstruct calcul du SfM
#   refine      étape après vérification (ex. séparation par caméra)
# Les différences de source passent par l'environnement.
# Usage : sfm_run.sh --sfm-dir DIR --images-dir DIR [--force]
#   --force : relance toujours la reconstruction (sinon, un sparse complet est réutilisé).
set -Eeuo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
sfm=""
images=""
force=false
while (($#)); do
  case "$1" in
    --sfm-dir) sfm="$2"; shift 2 ;;
    --images-dir) images="$2"; shift 2 ;;
    --force) force=true; shift ;;
    *) echo "Usage : sfm_run.sh --sfm-dir DIR --images-dir DIR [--force]" >&2; exit 2 ;;
  esac
done
[[ -n "$sfm" && -n "$images" ]] || { echo "--sfm-dir et --images-dir sont obligatoires." >&2; exit 2; }
die() { echo "[ERREUR] $*" >&2; exit 1; }

PYTHON_BIN="${PYTHON_BIN:-python3}"
COLMAP_METHOD="${COLMAP_METHOD:-spirula}"
PIPELINE_RESUME="${PIPELINE_RESUME:-false}"
export SFM_CAMERA_SPLIT="${SFM_CAMERA_SPLIT:-false}"
export SFM_CAMERA_SPLIT_MIN_IMAGES="${SFM_CAMERA_SPLIT_MIN_IMAGES:-50}"
SFM_MASKS="${SFM_MASKS:-off}"
sparse="$sfm/sparse/0"

case "$COLMAP_METHOD" in
  spirula|colmap|nerfstudio|hloc|orientations) ;;
  *) die "COLMAP_METHOD inconnu : $COLMAP_METHOD" ;;
esac

sparse_ready() {
  if [[ "$PIPELINE_RESUME" == true ]]; then
    local status=0
    "$PYTHON_BIN" "$repo_root/tools/sfm/resume.py" "$sparse" || status=$?
    ((status < 2)) || die "Impossible de valider le sparse ; aucun recalcul lancé."
    return "$status"
  else
    [[ -s "$sparse/cameras.bin" && -s "$sparse/images.bin" && -s "$sparse/points3D.bin" ]]
  fi
}

# Arguments : étape, images, dossier SfM, dossier de masques, mode caméra.
run_step() {
  local mask_dir=""
  [[ "$SFM_MASKS" != on ]] || mask_dir="$4"
  SFM_BACKEND_STEP="$1" SFM_BACKEND_IMAGES="$2" SFM_BACKEND_DIR="$3" \
    SFM_BACKEND_CAMERA_MODE="$5" SFM_BACKEND_MASK_DIR="$mask_dir" \
    bash "$repo_root/tools/sfm/backends/$COLMAP_METHOD.sh"
}

reconstruct() {
  echo "[étape $(date '+%H:%M:%S')] reconstruction COLMAP ($COLMAP_METHOD)"
  run_step reconstruct "$images" "$sfm" "$OUTPUT_DIR/feature_masks" "$SFM_CAMERA_MODE"
}

run_step recover "$images" "$sfm" "$OUTPUT_DIR/feature_masks" "$SFM_CAMERA_MODE"
if [[ "$force" == true ]]; then
  reconstruct
elif ! sparse_ready; then
  if [[ "$PIPELINE_RESUME" == true ]]; then
    for downstream in georef sparse_filtered sparse_clean sparse_rejected blocks gs gs-nerfstudio; do
      [[ ! -e "$OUTPUT_DIR/$downstream" ]] ||
        die "Sparse incomplet mais sorties $downstream déjà présentes : choisir un nouvel OUTPUT_DIR."
    done
    "$PYTHON_BIN" "$repo_root/tools/sfm/resume.py" "$sparse" --archive-sparse
  fi
  reconstruct
fi
for name in cameras images points3D; do
  [[ -s "$sparse/$name.bin" ]] || die "SfM incomplet : $name.bin"
done
run_step refine "$images" "$sfm" "$OUTPUT_DIR/feature_masks" "$SFM_CAMERA_MODE"
"$PYTHON_BIN" "$repo_root/tools/sfm/resume.py" "$sparse"
