#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
script_dir="$repo_root/tools/panoramax"
source "$repo_root/tools/libs/scripts/pipeline_postprocess.sh"

usage() {
  cat <<'EOF'
Usage:
  bash scripts/panoramax_to_3dgs.sh /path/to/config.sh [all|prepare|train-block INDEX|merge]

The config is a trusted Bash file. Start from tools/panoramax/config.sh.example.
It defines the Panoramax bbox and its EPSG code, output directory, SfM/block/training parameters,
and final .splat name.
Default: all. prepare downloads, reconstructs and partitions; train-block trains
only one prepared zero-based block; merge requires every block to be exported.
EOF
}

die() {
  printf '[ERREUR] %s\n' "$*" >&2
  exit 1
}

pipeline_stage="configuration et validation des paramètres"
pipeline_error() {
  local status="$1" line="$2"
  printf '[ERREUR] Pipeline arrêté pendant : %s (code %s, ligne %s).\n' \
    "$pipeline_stage" "$status" "$line" >&2
  if [[ "$pipeline_stage" == *"photos"* && -n "${DATASET_DIR:-}" ]]; then
    printf '[ERREUR] Diagnostic téléchargement : %s/summary.json et %s/manifest.jsonl\n' \
      "$DATASET_DIR" "$DATASET_DIR" >&2
  elif [[ -n "${SFM_DIR:-}" ]]; then
    printf '[ERREUR] Workspace SfM : %s ; modèles des blocs : %s\n' \
      "$SFM_DIR" "$GS_ROOT" >&2
  fi
  exit "$status"
}
trap 'pipeline_error "$?" "$LINENO"' ERR

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi
[[ $# -ge 1 && $# -le 3 ]] || { usage >&2; die "Un fichier de configuration est requis."; }
config_path="$1"
phase="${2:-all}"
phase_args=()
case "$phase" in
  all|prepare|merge)
    [[ $# -le 2 ]] || die "Trop d'arguments pour $phase." ;;
  train-block)
    [[ $# -eq 3 && "$3" =~ ^(0|[1-9][0-9]*)$ ]] ||
      die "train-block attend un indice de bloc entier positif ou nul."
    phase_args=(--block "$3") ;;
  *) die "Étape inconnue : $phase" ;;
esac
[[ -f "$config_path" ]] || die "Configuration introuvable : $config_path"
# The config is an explicitly selected Bash file, equivalent to sourcing env vars.
source "$config_path"
source "$script_dir/validate_config.sh"
validate_config || exit 1

: "${OUTPUT_DIR:?OUTPUT_DIR doit être défini dans la configuration}"
: "${PANORAMAX_BBOX:?PANORAMAX_BBOX doit être défini dans la configuration}"
: "${SPLIT_NUM_BLOCKS:?SPLIT_NUM_BLOCKS doit être défini dans la configuration}"
: "${TRAIN_NUM_ITER:?TRAIN_NUM_ITER doit être défini dans la configuration}"

PANORAMAX_API_URL="${PANORAMAX_API_URL:-https://explore.panoramax.fr/api}"
PANORAMAX_BBOX_EPSG="${PANORAMAX_BBOX_EPSG:-2154}"
PANORAMAX_QUALITY="${PANORAMAX_QUALITY:-hd}"
PANORAMAX_DATETIME="${PANORAMAX_DATETIME:-}"
PANORAMAX_IMAGE_MAX="${PANORAMAX_IMAGE_MAX:-}"
PANORAMAX_REPLACE_REQUEST="${PANORAMAX_REPLACE_REQUEST:-false}"
SPIRULA_BIN="${SPIRULA_BIN:-}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
export TRAIN_BACKEND="${TRAIN_BACKEND:-spirula}"
if [[ "$TRAIN_BACKEND" == spirula ]]; then
  : "${TRAIN_SP_CAP_MAX:?TRAIN_SP_CAP_MAX doit être défini pour TRAIN_BACKEND=spirula}"
  : "${TRAIN_SP_PRESET:?TRAIN_SP_PRESET doit être défini pour TRAIN_BACKEND=spirula}"
fi
export TRAIN_NS_TRAIN_BIN="${TRAIN_NS_TRAIN_BIN:-ns-train}"
export TRAIN_NS_EXPORT_BIN="${TRAIN_NS_EXPORT_BIN:-ns-export}"
export TRAIN_NS_DOWNSCALE_FACTOR="${TRAIN_NS_DOWNSCALE_FACTOR:-1}"
if [[ "$TRAIN_BACKEND" == nerfstudio && ( "$phase" == all || "$phase" == train-block ) ]]; then
  "$PYTHON_BIN" "$repo_root/tools/libs/python/nerfstudio_runtime.py"
fi
SFM_MAX_IMAGE_SIZE="${SFM_MAX_IMAGE_SIZE:-4096}"
SFM_ALIKED_MAX_FEATURES="${SFM_ALIKED_MAX_FEATURES:-4096}"
SFM_SP_PAIRS="${SFM_SP_PAIRS:-prefilter}"
SFM_SP_FEATURES="${SFM_SP_FEATURES:-aliked-n16rot}"
SFM_SP_MATCHER="${SFM_SP_MATCHER:-lightglue}"
SFM_THREADS="${SFM_THREADS:-${SLURM_CPUS_PER_TASK:-}}"
init_sfm_geometry_config
SFM_SP_PAIR_MAX_DISTANCE="${SFM_SP_PAIR_MAX_DISTANCE:-}"
SPLIT_OVERLAP_MAX_FRACTION="${SPLIT_OVERLAP_MAX_FRACTION:-0.05}"
TRAIN_CLEAN="${TRAIN_CLEAN:-false}"
EXPORT_SPLAT_NAME="${EXPORT_SPLAT_NAME:-panoramax.splat}"
MERGE_STRENGTH="${MERGE_STRENGTH:-1.0}"
POSTPROCESS_FILTER_MODE="${POSTPROCESS_FILTER_MODE:-diagnostic}"
POSTPROCESS_FILTER_SIZE_FACTOR="${POSTPROCESS_FILTER_SIZE_FACTOR:-10}"
POSTPROCESS_CLEANER="${POSTPROCESS_CLEANER:-contextual}"
CLEANGS_PYTHON="${CLEANGS_PYTHON:-$PYTHON_BIN}"
CLEANGS_MODEL="${CLEANGS_MODEL:-}"
CLEANGS_MODE="${CLEANGS_MODE:-diagnostic}"
CLEANGS_MIN_VIEWS="${CLEANGS_MIN_VIEWS:-2}"
CLEANGS_SKY_CONFIDENCE="${CLEANGS_SKY_CONFIDENCE:-0.9}"
CLEANGS_MAX_REMOVAL_FRACTION="${CLEANGS_MAX_REMOVAL_FRACTION:-0.2}"
POSTPROCESS_SPARSE_BBOX_MODE="${POSTPROCESS_SPARSE_BBOX_MODE:-apply}"
POSTPROCESS_SPARSE_BBOX_MARGIN="${POSTPROCESS_SPARSE_BBOX_MARGIN:-0.05}"
TRAIN_SP_SPLATS_PER_IMAGE="${TRAIN_SP_SPLATS_PER_IMAGE:-}"
TRAIN_SP_GROWTH_FACTOR="${TRAIN_SP_GROWTH_FACTOR:-}"
TRAIN_SP_REFINE_STOP_ITER="${TRAIN_SP_REFINE_STOP_ITER:-}"
TRAIN_SP_REFINE_STOP_NUM_ITER="${TRAIN_SP_REFINE_STOP_NUM_ITER:-}"
TRAIN_SP_FLOATER_SUPPRESSION="${TRAIN_SP_FLOATER_SUPPRESSION:-off}"
TRAIN_SP_QUALITY="${TRAIN_SP_QUALITY:-}"
TRAIN_SP_DISTRACTION_ROBUSTNESS="${TRAIN_SP_DISTRACTION_ROBUSTNESS:-off}"
TRAIN_SP_USE_PPISP="${TRAIN_SP_USE_PPISP:-}"
TRAIN_SP_PPISP_PARAM_TYPE="${TRAIN_SP_PPISP_PARAM_TYPE:-}"
TRAIN_SP_PPISP_EXPOSURE_FROM_EXIF="${TRAIN_SP_PPISP_EXPOSURE_FROM_EXIF:-}"
TRAIN_SP_USE_BILATERAL_GRID="${TRAIN_SP_USE_BILATERAL_GRID:-}"
TRAIN_SP_BILAGRID_TYPE="${TRAIN_SP_BILAGRID_TYPE:-}"
SFM_SP_IMAGE_EXPOSURE="${SFM_SP_IMAGE_EXPOSURE:-}"
TRAIN_MASKS="${TRAIN_MASKS:-off}"
TRAIN_MASKS_BACKEND="${TRAIN_MASKS_BACKEND:-segmentation}"
TRAIN_MASKS_SPIRULA_MODEL="${TRAIN_MASKS_SPIRULA_MODEL:-}"
TRAIN_MASKS_SPIRULA_DEVICE="${TRAIN_MASKS_SPIRULA_DEVICE:-auto}"
MASKS_ACCEPT_LICENSE="${MASKS_ACCEPT_LICENSE:-false}"
TRAIN_MASKS_PYTHON="${TRAIN_MASKS_PYTHON:-$PYTHON_BIN}"
TRAIN_MASKS_MODEL="${TRAIN_MASKS_MODEL:-}"
TRAIN_MASKS_LABELS="${TRAIN_MASKS_LABELS:-sky}"
TRAIN_MASKS_MODE="${TRAIN_MASKS_MODE:-auto}"
TRAIN_MASKS_CONFIDENCE="${TRAIN_MASKS_CONFIDENCE:-0.5}"
TRAIN_MASKS_DILATE="${TRAIN_MASKS_DILATE:-auto}"
TRAIN_MASKS_MAX_FRACTION="${TRAIN_MASKS_MAX_FRACTION:-0.8}"
TRAIN_MASKS_DEVICE="${TRAIN_MASKS_DEVICE:-auto}"

[[ "$SPLIT_NUM_BLOCKS" =~ ^[1-9][0-9]*$ ]] || die "SPLIT_NUM_BLOCKS doit être un entier positif."
[[ "$TRAIN_NUM_ITER" =~ ^[1-9][0-9]*$ ]] || die "TRAIN_NUM_ITER doit être un entier positif."
if [[ "$TRAIN_BACKEND" == spirula ]]; then
  [[ "$TRAIN_SP_CAP_MAX" =~ ^[1-9][0-9]*$ ]] || die "TRAIN_SP_CAP_MAX doit être un entier positif."
fi
[[ "$SFM_MAX_IMAGE_SIZE" =~ ^[1-9][0-9]*$ ]] || die "SFM_MAX_IMAGE_SIZE doit être un entier positif."
[[ "$SFM_ALIKED_MAX_FEATURES" =~ ^[1-9][0-9]*$ ]] || die "SFM_ALIKED_MAX_FEATURES doit être un entier positif."
[[ -z "$PANORAMAX_IMAGE_MAX" || "$PANORAMAX_IMAGE_MAX" =~ ^[1-9][0-9]*$ ]] ||
  die "PANORAMAX_IMAGE_MAX doit être vide ou un entier positif."
[[ "$PANORAMAX_QUALITY" == hd || "$PANORAMAX_QUALITY" == sd || "$PANORAMAX_QUALITY" == thumb ]] ||
  die "PANORAMAX_QUALITY doit être hd, sd ou thumb."
[[ "$EXPORT_SPLAT_NAME" == *.splat && "$EXPORT_SPLAT_NAME" != */* && "$EXPORT_SPLAT_NAME" != .splat ]] ||
  die "EXPORT_SPLAT_NAME doit être un nom de fichier terminé par .splat."
[[ "$MERGE_STRENGTH" =~ ^[0-9]+([.][0-9]+)?$ ]] &&
  awk -v value="$MERGE_STRENGTH" 'BEGIN { exit !(value > 0) }' ||
  die "MERGE_STRENGTH doit être un nombre positif."

command -v "$PYTHON_BIN" >/dev/null 2>&1 || die "Python introuvable : $PYTHON_BIN"

mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR="$(cd -- "$OUTPUT_DIR" && pwd)"
DATASET_DIR="$OUTPUT_DIR/panoramax"
SFM_DIR="$DATASET_DIR/sfm-$TRAIN_NUM_ITER"
BLOCKS_DIR="$SFM_DIR/blocks-$SPLIT_NUM_BLOCKS"
GS_ROOT="$DATASET_DIR/gs-${TRAIN_NUM_ITER}-blocks-${SPLIT_NUM_BLOCKS}"
[[ "$TRAIN_BACKEND" != nerfstudio ]] || GS_ROOT="$GS_ROOT-nerfstudio"
prepare_images() {
  local bbox_args request_status=0 archive
  if [[ -e "$DATASET_DIR/request.json" ]]; then
    "$PYTHON_BIN" - \
    "$DATASET_DIR/request.json" "$PANORAMAX_BBOX" "$PANORAMAX_BBOX_EPSG" "$PANORAMAX_API_URL" \
    "$PANORAMAX_DATETIME" "$PANORAMAX_QUALITY" "$PANORAMAX_IMAGE_MAX" <<'PY' || request_status=$?
import json
import sys

path, bbox, bbox_epsg, api_url, datetime, quality, image_max = sys.argv[1:]
with open(path, encoding="utf-8") as stream:
    previous = json.load(stream)
expected = {
    "bbox": [float(value) for value in bbox.split(",")],
    "bbox_epsg": int(bbox_epsg),
    "api_url": api_url,
    "datetime": datetime or None,
    "quality": quality,
    "image_max": int(image_max) if image_max else None,
}
for key, value in expected.items():
    actual = previous.get(key, 2154 if key == "bbox_epsg" else None)
    if key == "api_url":
        actual = actual.rstrip("/")
        value = value.rstrip("/")
    if actual != value:
        print(
            f"[pipeline] {path} appartient à une autre requête "
            f"({key}: {actual!r} != {value!r}).", file=sys.stderr,
        )
        raise SystemExit(3)
PY
  fi
  if ((request_status == 3)); then
    case "$PANORAMAX_REPLACE_REQUEST" in
      true|1|yes|on) ;;
      *) die "Choisir un OUTPUT_DIR neuf, ou PANORAMAX_REPLACE_REQUEST=\"true\" pour archiver $DATASET_DIR et retélécharger." ;;
    esac
    # Archive rather than delete: old photos, SfM and blocks stay recoverable.
    archive="$DATASET_DIR.replaced-$(date +%Y%m%d-%H%M%S)"
    [[ ! -e "$archive" ]] || die "Archive déjà présente : $archive"
    mv -- "$DATASET_DIR" "$archive"
    printf '[pipeline] Ancienne requête archivée dans %s (à supprimer manuellement si inutile).\n' "$archive"
  elif ((request_status != 0)); then
    die "Lecture impossible de $DATASET_DIR/request.json (code $request_status)."
  fi

  if [[ ! -e "$DATASET_DIR/request.json" && -d "$DATASET_DIR/images" ]] &&
     [[ -n "$(ls -A "$DATASET_DIR/images" 2>/dev/null)" ]]; then
    die "$DATASET_DIR/images contient des photos sans request.json ; choisir un OUTPUT_DIR neuf."
  fi

  bbox_args=(--bbox "$PANORAMAX_BBOX" --bbox-epsg "$PANORAMAX_BBOX_EPSG" \
    --output-dir "$DATASET_DIR" \
    --api-url "$PANORAMAX_API_URL" --quality "$PANORAMAX_QUALITY")
  [[ -z "$PANORAMAX_DATETIME" ]] || bbox_args+=(--datetime "$PANORAMAX_DATETIME")
  [[ -z "$PANORAMAX_IMAGE_MAX" ]] || bbox_args+=(--image-max "$PANORAMAX_IMAGE_MAX")
  printf '[pipeline] Téléchargement Panoramax dans %s\n' "$DATASET_DIR"
  pipeline_stage="téléchargement des photos Panoramax"
  "$PYTHON_BIN" "$script_dir/download.py" "${bbox_args[@]}"

  # The downloader retains old files for manual resume. The dedicated pipeline
  # directory must contain exactly this run's successful/reused manifest images.
  pipeline_stage="validation des photos téléchargées"
  "$PYTHON_BIN" - "$DATASET_DIR" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
manifest = root / "manifest.jsonl"
if not manifest.is_file():
    raise SystemExit(f"Manifeste absent après téléchargement : {manifest}")
expected = set()
for line in manifest.read_text(encoding="utf-8").splitlines():
    item = json.loads(line)
    if item.get("status") in ("downloaded", "reused"):
        expected.add(item["image"])
images = root / "images"
stale = sorted(
    str(path.relative_to(root))
    for path in images.glob("*.jpg")
    if str(path.relative_to(root)) not in expected
)
if stale:
    raise SystemExit(
        "Des images non sélectionnées restent dans le dossier dédié : "
        + ", ".join(stale[:5])
        + ". Choisir un OUTPUT_DIR neuf pour éviter de les inclure dans le SfM."
    )
if not expected:
    raise SystemExit("Aucune photo téléchargée ou réutilisable dans le manifeste.")
PY
}

if [[ "$phase" == all || "$phase" == prepare ]]; then
  prepare_images
fi

if [[ "$phase" != merge ]]; then
  if [[ "$phase" == prepare ]]; then
    phase_args=(--prepare-only)
    pipeline_stage="reconstruction SfM et découpage COLMAP"
  elif [[ "$phase" == train-block ]]; then
    pipeline_stage="entraînement du bloc $3"
  else
    pipeline_stage="reconstruction SfM, découpage COLMAP et entraînement des blocs"
  fi
  printf '[pipeline] Étape : %s\n' "$pipeline_stage"
  DIR="$DATASET_DIR" \
  SPIRULA_BIN="$SPIRULA_BIN" \
  PYTHON_BIN="$PYTHON_BIN" \
  TRAIN_NUM_ITER="$TRAIN_NUM_ITER" \
  SPLIT_NUM_BLOCKS="$SPLIT_NUM_BLOCKS" \
  TRAIN_SP_CAP_MAX="$TRAIN_SP_CAP_MAX" \
  TRAIN_SP_SPLATS_PER_IMAGE="$TRAIN_SP_SPLATS_PER_IMAGE" \
  TRAIN_SP_GROWTH_FACTOR="$TRAIN_SP_GROWTH_FACTOR" \
  TRAIN_SP_REFINE_STOP_ITER="$TRAIN_SP_REFINE_STOP_ITER" \
  TRAIN_SP_REFINE_STOP_NUM_ITER="$TRAIN_SP_REFINE_STOP_NUM_ITER" \
  TRAIN_SP_FLOATER_SUPPRESSION="$TRAIN_SP_FLOATER_SUPPRESSION" \
  TRAIN_SP_PRESET="$TRAIN_SP_PRESET" \
  TRAIN_SP_QUALITY="$TRAIN_SP_QUALITY" \
  TRAIN_SP_DISTRACTION_ROBUSTNESS="$TRAIN_SP_DISTRACTION_ROBUSTNESS" \
  TRAIN_SP_USE_PPISP="$TRAIN_SP_USE_PPISP" \
  TRAIN_SP_PPISP_PARAM_TYPE="$TRAIN_SP_PPISP_PARAM_TYPE" \
  TRAIN_SP_PPISP_EXPOSURE_FROM_EXIF="$TRAIN_SP_PPISP_EXPOSURE_FROM_EXIF" \
  TRAIN_SP_USE_BILATERAL_GRID="$TRAIN_SP_USE_BILATERAL_GRID" \
  TRAIN_SP_BILAGRID_TYPE="$TRAIN_SP_BILAGRID_TYPE" \
  SFM_SP_IMAGE_EXPOSURE="$SFM_SP_IMAGE_EXPOSURE" \
  SFM_MAX_IMAGE_SIZE="$SFM_MAX_IMAGE_SIZE" \
  SFM_ALIKED_MAX_FEATURES="$SFM_ALIKED_MAX_FEATURES" \
  SFM_SP_PAIRS="$SFM_SP_PAIRS" \
  SFM_SP_FEATURES="$SFM_SP_FEATURES" \
  SFM_SP_MATCHER="$SFM_SP_MATCHER" \
  SFM_THREADS="$SFM_THREADS" \
  SFM_MAX_ERROR="$SFM_MAX_ERROR" \
  SFM_MIN_INLIERS="$SFM_MIN_INLIERS" \
  SFM_MIN_TRI_ANGLE="$SFM_MIN_TRI_ANGLE" \
  SFM_SP_BA_LOSS="$SFM_SP_BA_LOSS" \
  SFM_SP_BA_LOSS_PARAM="$SFM_SP_BA_LOSS_PARAM" \
  SFM_SP_PAIR_MAX_DISTANCE="$SFM_SP_PAIR_MAX_DISTANCE" \
  SPLIT_OVERLAP_MAX_FRACTION="$SPLIT_OVERLAP_MAX_FRACTION" \
  TRAIN_CLEAN="$TRAIN_CLEAN" \
  SFM_MASKS="${SFM_MASKS:-off}" \
  SFM_MASKS_BACKEND="${SFM_MASKS_BACKEND:-$TRAIN_MASKS_BACKEND}" \
  SFM_MASKS_PYTHON="${SFM_MASKS_PYTHON:-$TRAIN_MASKS_PYTHON}" \
  SFM_MASKS_MODEL="${SFM_MASKS_MODEL:-$TRAIN_MASKS_MODEL}" \
  SFM_MASKS_SPIRULA_MODEL="${SFM_MASKS_SPIRULA_MODEL:-$TRAIN_MASKS_SPIRULA_MODEL}" \
  SFM_MASKS_SPIRULA_DEVICE="${SFM_MASKS_SPIRULA_DEVICE:-$TRAIN_MASKS_SPIRULA_DEVICE}" \
  SFM_MASKS_LABELS="${SFM_MASKS_LABELS:-$TRAIN_MASKS_LABELS}" \
  SFM_MASKS_CONFIDENCE="${SFM_MASKS_CONFIDENCE:-$TRAIN_MASKS_CONFIDENCE}" \
  SFM_MASKS_DILATE="${SFM_MASKS_DILATE:-0.002}" \
  SFM_MASKS_MAX_FRACTION="${SFM_MASKS_MAX_FRACTION:-$TRAIN_MASKS_MAX_FRACTION}" \
  SFM_MASKS_DEVICE="${SFM_MASKS_DEVICE:-$TRAIN_MASKS_DEVICE}" \
  TRAIN_MASKS="$TRAIN_MASKS" \
  TRAIN_MASKS_BACKEND="$TRAIN_MASKS_BACKEND" \
  TRAIN_MASKS_SPIRULA_MODEL="$TRAIN_MASKS_SPIRULA_MODEL" \
  TRAIN_MASKS_SPIRULA_DEVICE="$TRAIN_MASKS_SPIRULA_DEVICE" \
  MASKS_ACCEPT_LICENSE="$MASKS_ACCEPT_LICENSE" \
  TRAIN_MASKS_PYTHON="$TRAIN_MASKS_PYTHON" \
  TRAIN_MASKS_MODEL="$TRAIN_MASKS_MODEL" \
  TRAIN_MASKS_LABELS="$TRAIN_MASKS_LABELS" \
  TRAIN_MASKS_MODE="$TRAIN_MASKS_MODE" \
  TRAIN_MASKS_CONFIDENCE="$TRAIN_MASKS_CONFIDENCE" \
  TRAIN_MASKS_DILATE="$TRAIN_MASKS_DILATE" \
  TRAIN_MASKS_MAX_FRACTION="$TRAIN_MASKS_MAX_FRACTION" \
  TRAIN_MASKS_DEVICE="$TRAIN_MASKS_DEVICE" \
  bash "$repo_root/tools/libs/scripts/run_spirula_blocks.sh" "${phase_args[@]}"
fi
if [[ "$phase" == prepare || "$phase" == train-block ]]; then
  printf '[pipeline] Étape %s terminée.\n' "$phase"
  exit 0
fi

pipeline_stage="vérification des splats avant fusion"
MERGE_INPUTS=()
for ((index = 0; index < SPLIT_NUM_BLOCKS; index++)); do
  block_id="$(printf '%03d' "$index")"
  splat="$GS_ROOT/block_$block_id/splats/Paris-${TRAIN_NUM_ITER}-block-${block_id}.splat"
  [[ -s "$splat" ]] || die "Splat du bloc absent ou vide : $splat"
  if [[ "$POSTPROCESS_SPARSE_BBOX_MODE" == apply ]]; then
    pipeline_stage="recadrage bbox COLMAP du bloc $block_id avant fusion"
    cropped="$GS_ROOT/block_$block_id/splats/Paris-${TRAIN_NUM_ITER}-block-${block_id}.bbox.splat"
    crop_splat_to_sparse "$splat" "$cropped" "$SFM_DIR/sparse/0" \
      apply "$POSTPROCESS_SPARSE_BBOX_MARGIN"
    splat="$cropped"
  fi
  MERGE_INPUTS+=("$splat")
done

FINAL_SPLAT="$OUTPUT_DIR/$EXPORT_SPLAT_NAME"
UNFILTERED_SPLAT="$OUTPUT_DIR/${EXPORT_SPLAT_NAME%.splat}.unfiltered.splat"
printf '[pipeline] Fusion des blocs vers %s\n' "$UNFILTERED_SPLAT"
pipeline_stage="fusion des splats des blocs"
merge_pipeline_splats "$UNFILTERED_SPLAT" "$MERGE_STRENGTH" "${MERGE_INPUTS[@]}"
if [[ "$POSTPROCESS_CLEANER" == cleangs ]]; then
  pipeline_stage="filtre Clean-GS multivues"
  "$CLEANGS_PYTHON" "$repo_root/tools/clean_gs/clean_sky.py" \
    --input "$UNFILTERED_SPLAT" --output "$FINAL_SPLAT" \
    --scene "$SFM_DIR" --model "$CLEANGS_MODEL" --mode "$CLEANGS_MODE" \
    --min-views "$CLEANGS_MIN_VIEWS" --sky-confidence "$CLEANGS_SKY_CONFIDENCE" \
    --max-removal-fraction "$CLEANGS_MAX_REMOVAL_FRACTION" --overwrite
else
  pipeline_stage="filtre contextuel global des splats"
  "$PYTHON_BIN" "$repo_root/tools/postprocess/filter_splats.py" \
    --input "$UNFILTERED_SPLAT" --output "$FINAL_SPLAT" \
    --mode "$POSTPROCESS_FILTER_MODE" --size-factor "$POSTPROCESS_FILTER_SIZE_FACTOR" \
    --sparse-points "$SFM_DIR/sparse/0/points3D.bin" \
    --sparse-bbox-mode "$POSTPROCESS_SPARSE_BBOX_MODE" \
    --sparse-bbox-margin "$POSTPROCESS_SPARSE_BBOX_MARGIN" --overwrite
fi
[[ -s "$FINAL_SPLAT" ]] || die "Splat final absent ou vide : $FINAL_SPLAT"

# Keep the georeferencing evidence alongside the final world-coordinate splat.
GEOREF_DIR="$OUTPUT_DIR/${EXPORT_SPLAT_NAME%.splat}.georef"
pipeline_stage="copie des références de géoréférencement"
mkdir -p "$GEOREF_DIR"
cp "$DATASET_DIR/request.json" "$DATASET_DIR/summary.json" \
  "$DATASET_DIR/manifest.jsonl" "$GEOREF_DIR/"
cp -R "$DATASET_DIR/metadata" "$GEOREF_DIR/"
mkdir -p "$GEOREF_DIR/sparse"
cp -R "$SFM_DIR/sparse/0/." "$GEOREF_DIR/sparse/"
cp "$BLOCKS_DIR/manifest.json" "$GEOREF_DIR/blocks_manifest.json"
cp "$OUTPUT_DIR/${EXPORT_SPLAT_NAME%.splat}.filter.json" "$GEOREF_DIR/splat_filter_summary.json"
if [[ -f "$SFM_DIR/gps_filter_summary.json" ]]; then
  cp "$SFM_DIR/gps_filter_summary.json" "$GEOREF_DIR/"
fi
for ((index = 0; index < SPLIT_NUM_BLOCKS; index++)); do
  block_id="$(printf '%03d' "$index")"
  transform="$GS_ROOT/block_$block_id/splats/scene_transform.json"
  [[ -s "$transform" ]] || die "Transformation d'entraînement absente : $transform"
  mkdir -p "$GEOREF_DIR/scene_transforms"
  cp "$transform" "$GEOREF_DIR/scene_transforms/block_${block_id}.json"
done
"$PYTHON_BIN" - "$GEOREF_DIR/georeference.json" "$FINAL_SPLAT" \
  "$EXPORT_SPLAT_NAME" "$SPLIT_NUM_BLOCKS" "$TRAIN_NUM_ITER" \
  "$PANORAMAX_BBOX_EPSG" <<'PY'
import json
import sys
from pathlib import Path

output, splat_path, splat_name, blocks, iterations, bbox_epsg = sys.argv[1:]
data = {
    "splat": Path(splat_path).name,
    "format": "splat",
    "coordinate_frame": "Spirula SfM metric frame aligned from Panoramax EXIF GPS",
    "crs": "local ENU; not projected EPSG coordinates",
    "source_gps": "Panoramax image metadata and JPEG EXIF; WGS84 longitude/latitude and EXIF altitude",
    "source_bbox_crs": f"EPSG:{bbox_epsg}",
    "references": {
        "request": "request.json",
        "download_summary": "summary.json",
        "photo_manifest": "manifest.jsonl",
        "photo_metadata": "metadata/",
        "colmap_sparse_model": "sparse/",
        "block_partition": "blocks_manifest.json",
        "training_transforms": "scene_transforms/",
        "splat_filter_summary": "splat_filter_summary.json",
    },
    "num_blocks": int(blocks),
    "num_iterations_per_block": int(iterations),
}
Path(output).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY

printf '[pipeline] Terminé.\n'
printf '[pipeline] Splat final : %s\n' "$FINAL_SPLAT"
printf '[pipeline] Géoréférencement et références : %s\n' "$GEOREF_DIR"
