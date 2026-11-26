#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
script_dir="$repo_root/tools/panoramax"
source "$repo_root/tools/libs/scripts/export.sh"
source "$repo_root/tools/libs/scripts/redo.sh"

usage() {
  cat <<'EOF'
Usage:
  bash scripts/panoramax_to_3dgs.sh /path/to/config.sh [all|prepare|train-block INDEX|merge] [--redo]

The config is a trusted Bash file. It overrides config/defaults/*.default.sh and
tools/panoramax/config.default.sh, which are always loaded first; only set the variables you want to change.
It defines the Panoramax bbox and its EPSG code, output directory, SfM/block/training parameters,
and final .splat name.
Default: all. prepare downloads, reconstructs and partitions; train-block trains
only one prepared zero-based block; merge requires every block to be exported.
EOF
}

source "$repo_root/tools/libs/scripts/spirula_common.sh"

# Résout SPIRULA_DEVICE=auto une seule fois : premier GPU « ok » discret, puis intégré.
# Colonnes de `spirula sam devices` : no, nom (espaces possibles), type, VRAM (2 mots), uuid, état.
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
redo=false
args=()
while (($#)); do
  if [[ "$1" == --redo ]]; then redo=true; else args+=("$1"); fi
  shift
done
argc="${#args[@]}"
[[ $argc -ge 1 && $argc -le 3 ]] || { usage >&2; die "Un fichier de configuration est requis."; }
config_path="${args[0]}"
phase="${args[1]:-all}"
phase_args=()
case "$phase" in
  all|prepare|merge)
    [[ $argc -le 2 ]] || die "Trop d'arguments pour $phase." ;;
  train-block)
    [[ $argc -eq 3 && "${args[2]}" =~ ^(0|[1-9][0-9]*)$ ]] ||
      die "train-block attend un indice de bloc entier positif ou nul."
    phase_args=(--block "${args[2]}") ;;
  *) die "Étape inconnue : $phase" ;;
esac
[[ "$redo" != true ]] || redo_check_phase "$phase"
[[ -f "$config_path" ]] || die "Configuration introuvable : $config_path"
source "$repo_root/tools/libs/scripts/pipeline_config.sh"
load_pipeline_defaults "$repo_root" panoramax
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
PANORAMAX_PICTURE_TYPE="${PANORAMAX_PICTURE_TYPE:-all}"
PANORAMAX_PROVIDER="${PANORAMAX_PROVIDER:-}"
# Filtres : "KEY=V1,V2;KEY2=V3" ; ';' sépare les clés (ET), ',' sépare les valeurs (OU).
PANORAMAX_FILTERS="${PANORAMAX_FILTERS:-}"
export PANORAMAX_MULTI_CAM="${PANORAMAX_MULTI_CAM:-false}"
PANORAMAX_REPLACE_REQUEST="${PANORAMAX_REPLACE_REQUEST:-false}"
SPIRULA_BIN="${SPIRULA_BIN:-}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
source "$repo_root/tools/libs/scripts/georef.sh"
source "$repo_root/tools/libs/scripts/pipeline_lidar.sh"
load_lidar_config
export LIDAR_WORKSPACE="$OUTPUT_DIR"
lidar_pipeline_hook preflight
source "$repo_root/tools/libs/scripts/pipeline_report.sh"
pipeline_report_start "$OUTPUT_DIR" "panoramax_to_3dgs:$phase"
pipeline_exit() {
  local status=$?
  trap - EXIT
  pipeline_report_finish "$status" || { [[ "$status" != 0 ]] || status=1; }
  exit "$status"
}
trap pipeline_exit EXIT
set_pipeline_stage() { pipeline_stage="$1"; pipeline_report_phase "$pipeline_stage"; }
set_pipeline_stage "initialisation"
export TRAIN_BACKEND="${TRAIN_BACKEND:-spirula}"
if [[ "$TRAIN_BACKEND" == spirula ]]; then
  : "${TRAIN_SP_CAP_MAX:?TRAIN_SP_CAP_MAX doit être défini pour TRAIN_BACKEND=spirula}"
  : "${TRAIN_SP_PRESET:?TRAIN_SP_PRESET doit être défini pour TRAIN_BACKEND=spirula}"
fi
export TRAIN_NS_TRAIN_BIN="${TRAIN_NS_TRAIN_BIN:-ns-train}"
export TRAIN_NS_EXPORT_BIN="${TRAIN_NS_EXPORT_BIN:-ns-export}"
export TRAIN_NS_DOWNSCALE_FACTOR="${TRAIN_NS_DOWNSCALE_FACTOR:-1}"
# Child scripts (run_panoramax.sh, masks.py) only see exported variables.
export SFM_MASKS_DIR="${SFM_MASKS_DIR:-}" TRAIN_MASKS_DIR="${TRAIN_MASKS_DIR:-}"
if [[ "$TRAIN_BACKEND" == nerfstudio && ( "$phase" == all || "$phase" == train-block ) ]]; then
  "$PYTHON_BIN" "$repo_root/tools/libs/python/nerfstudio_runtime.py"
fi
SFM_MAX_IMAGE_SIZE="${SFM_MAX_IMAGE_SIZE:-4096}"
SFM_ALIKED_MAX_FEATURES="${SFM_ALIKED_MAX_FEATURES:-4096}"
SFM_SP_PAIRS="${SFM_SP_PAIRS:-prefilter}"
SFM_SP_PREFILTER_NEIGHBORS="${SFM_SP_PREFILTER_NEIGHBORS:-16}"
SFM_SP_FEATURES="${SFM_SP_FEATURES:-aliked-n16rot}"
SFM_SP_MATCHER="${SFM_SP_MATCHER:-lightglue}"
NUM_CPUS="${NUM_CPUS:-2}"
init_sfm_geometry_config
SFM_SP_PAIR_MAX_DISTANCE="${SFM_SP_PAIR_MAX_DISTANCE:-}"
SPLIT_OVERLAP_MAX_FRACTION="${SPLIT_OVERLAP_MAX_FRACTION:-0.05}"
TRAIN_CLEAN="${TRAIN_CLEAN:-false}"
EXPORT_SPLAT_NAME="${EXPORT_SPLAT_NAME:-panoramax.splat}"
MERGE_STRENGTH="${MERGE_STRENGTH:-1.0}"
export POSTPROCESS_FILTER_MODE="${POSTPROCESS_FILTER_MODE:-apply}"
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
SFM_SP_METRIC_POSITIONS="${SFM_SP_METRIC_POSITIONS:-}"
TRAIN_MASKS="${TRAIN_MASKS:-off}"
TRAIN_MASKS_BACKEND="${TRAIN_MASKS_BACKEND:-segmentation}"
TRAIN_MASKS_SPIRULA_MODEL="${TRAIN_MASKS_SPIRULA_MODEL:-}"
SPIRULA_DEVICE="${SPIRULA_DEVICE:-auto}"
MASKS_ACCEPT_LICENSE="${MASKS_ACCEPT_LICENSE:-false}"
TRAIN_MASKS_PYTHON="${TRAIN_MASKS_PYTHON:-$PYTHON_BIN}"
TRAIN_MASKS_MODEL="${TRAIN_MASKS_MODEL:-}"
TRAIN_MASKS_LABELS="${TRAIN_MASKS_LABELS-sky}"
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
[[ "$PANORAMAX_PICTURE_TYPE" == all || "$PANORAMAX_PICTURE_TYPE" == normal || "$PANORAMAX_PICTURE_TYPE" == 360 ]] ||
  die "PANORAMAX_PICTURE_TYPE doit être all, normal ou 360."
if [[ "$PANORAMAX_PICTURE_TYPE" == 360 && "${COLMAP_METHOD:-spirula}" == spirula && "${SFM_CAMERA_MODEL:-opencv}" != equirectangular ]]; then
  echo "[config] PANORAMAX_PICTURE_TYPE=360 : SFM_CAMERA_MODEL=equirectangular (au lieu de ${SFM_CAMERA_MODEL:-opencv})." >&2
  SFM_CAMERA_MODEL=equirectangular
  export SFM_CAMERA_MODEL
fi
[[ "$EXPORT_SPLAT_NAME" == *.splat && "$EXPORT_SPLAT_NAME" != */* && "$EXPORT_SPLAT_NAME" != .splat ]] ||
  die "EXPORT_SPLAT_NAME doit être un nom de fichier terminé par .splat."
[[ "$MERGE_STRENGTH" =~ ^[0-9]+([.][0-9]+)?$ ]] &&
  awk -v value="$MERGE_STRENGTH" 'BEGIN { exit !(value > 0) }' ||
  die "MERGE_STRENGTH doit être un nombre positif."

command -v "$PYTHON_BIN" >/dev/null 2>&1 || die "Python introuvable : $PYTHON_BIN"

mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR="$(cd -- "$OUTPUT_DIR" && pwd)"
DATASET_DIR="$OUTPUT_DIR/panoramax"
SFM_DIR="$OUTPUT_DIR/sfm"
IMAGES_DIR="$OUTPUT_DIR/images"
BLOCKS_DIR="$OUTPUT_DIR/blocks"
GS_ROOT="$OUTPUT_DIR/gs"
[[ "$TRAIN_BACKEND" != nerfstudio ]] || GS_ROOT="$OUTPUT_DIR/gs-nerfstudio"
download_done="$DATASET_DIR/.download_done"
download_lock="$DATASET_DIR/.download.lock"
download_lock_helper="$repo_root/tools/libs/python/pipeline_lock.py"
prepare_images() {
  local bbox_args request_status=0 archive download_status=0
  if [[ -e "$DATASET_DIR/request.json" ]]; then
    "$PYTHON_BIN" - \
    "$DATASET_DIR/request.json" "$PANORAMAX_BBOX" "$PANORAMAX_BBOX_EPSG" "$PANORAMAX_API_URL" \
    "$PANORAMAX_DATETIME" "$PANORAMAX_QUALITY" "$PANORAMAX_IMAGE_MAX" "$PANORAMAX_PICTURE_TYPE" \
    "$PANORAMAX_PROVIDER" "$PANORAMAX_FILTERS" <<'PY' || request_status=$?
import json
import sys

path, bbox, bbox_epsg, api_url, datetime, quality, image_max, picture_type, provider, filters = sys.argv[1:]
stac_types = {"all": "all", "normal": "normal", "360": "360"}


def parse_filters(spec):
    parsed = []
    for item in spec.split(";"):
        if not item.strip():
            continue
        key, _, values = item.partition("=")
        parsed.append({"key": key.strip(), "values": [v.strip() for v in values.split(",") if v.strip()]})
    return parsed or None


with open(path, encoding="utf-8") as stream:
    previous = json.load(stream)
expected = {
    "bbox": [float(value) for value in bbox.split(",")],
    "bbox_epsg": int(bbox_epsg),
    "api_url": api_url,
    "datetime": datetime or None,
    "quality": quality,
    "image_max": int(image_max) if image_max else None,
    "picture_type": stac_types[picture_type],
    "provider": provider or None,
    "filters": parse_filters(filters),
}
for key, value in expected.items():
    actual = previous.get(key, 2154 if key == "bbox_epsg" else None)
    if key == "picture_type" and actual is None:
        actual = "all"
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

  stray_photos=""
  for images_dir in "$DATASET_DIR/images" "$DATASET_DIR"/*/images; do
    [[ -d "$images_dir" ]] || continue
    [[ "$images_dir" == "$DATASET_DIR/images" || -d "${images_dir%/images}/metadata" ]] || continue
    stray_photos="$(find "$images_dir" -type f -print -quit 2>/dev/null)"
    [[ -z "$stray_photos" ]] || break
  done
  if [[ ! -e "$DATASET_DIR/request.json" && -n "$stray_photos" ]]; then
    die "$DATASET_DIR contient des photos sans request.json ; choisir un OUTPUT_DIR neuf."
  fi

  bbox_args=(--bbox "$PANORAMAX_BBOX" --crs "EPSG:$PANORAMAX_BBOX_EPSG" \
    --output-dir "$DATASET_DIR" \
    --api-url "$PANORAMAX_API_URL" --quality "$PANORAMAX_QUALITY")
  [[ -z "$PANORAMAX_DATETIME" ]] || bbox_args+=(--datetime "$PANORAMAX_DATETIME")
  [[ -z "$PANORAMAX_IMAGE_MAX" ]] || bbox_args+=(--image-max "$PANORAMAX_IMAGE_MAX")
  [[ -z "$PANORAMAX_PROVIDER" ]] || bbox_args+=(--provider "$PANORAMAX_PROVIDER")
  bbox_args+=(--picture-type "$PANORAMAX_PICTURE_TYPE")
  if [[ -n "$PANORAMAX_FILTERS" ]]; then
    IFS=';' read -r -a panoramax_filter_items <<< "$PANORAMAX_FILTERS"
    for panoramax_filter_item in "${panoramax_filter_items[@]}"; do
      [[ -z "${panoramax_filter_item//[[:space:]]/}" ]] || bbox_args+=(--filter "$panoramax_filter_item")
    done
  fi
  if [[ "${PIPELINE_RESUME:-false}" == true && -f "$download_done" ]]; then
    printf '[pipeline] Téléchargement déjà terminé (%s) : étape ignorée.\n' "$download_done"
  else
    rm -f -- "$download_done"
    mkdir -p -- "$DATASET_DIR"
    "$PYTHON_BIN" "$download_lock_helper" "$download_lock" --pid "$$" \
      --resume "${PIPELINE_RESUME:-false}" --ignore "${REMOVE_LOCKS:-false}" --workspace "$OUTPUT_DIR" \
      || die "Téléchargement Panoramax verrouillé : $download_lock"
    printf '[pipeline] Téléchargement Panoramax dans %s\n' "$DATASET_DIR"
    set_pipeline_stage "téléchargement des photos Panoramax"
    download_status=0
    "$PYTHON_BIN" "$script_dir/download.py" "${bbox_args[@]}" || download_status=$?
    "$PYTHON_BIN" "$download_lock_helper" "$download_lock" --pid "$$" --release
    ((download_status == 0)) || {
      printf '[ERREUR] échec du téléchargement des photos Panoramax (code %s).\n' "$download_status" >&2
      printf '[ERREUR] Diagnostic téléchargement : %s/summary.json et %s/manifest.jsonl\n' \
        "$DATASET_DIR" "$DATASET_DIR" >&2
      exit "$download_status"
    }
    touch -- "$download_done"
  fi

  # The downloader retains old files for manual resume. The dedicated pipeline
  # directory must contain exactly this run's successful/reused manifest images.
  set_pipeline_stage "validation des photos téléchargées"
  "$PYTHON_BIN" - "$DATASET_DIR" "$script_dir" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
sys.path.insert(0, sys.argv[2])
from dataset_layout import image_files

manifest = root / "manifest.jsonl"
if not manifest.is_file():
    raise SystemExit(f"Manifeste absent après téléchargement : {manifest}")
expected = set()
for line in manifest.read_text(encoding="utf-8").splitlines():
    item = json.loads(line)
    if item.get("status") in ("downloaded", "reused"):
        expected.add(item["image"])
stale = sorted(
    path.relative_to(root).as_posix()
    for path in image_files(root)
    if path.relative_to(root).as_posix() not in expected
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

if [[ "$phase" == merge ]]; then
  redo_check_blocks "$GS_ROOT" "$SPLIT_NUM_BLOCKS"
fi
if [[ "$redo" == true ]]; then
  set_pipeline_stage "nettoyage des sorties de fusion à refaire ($phase)"
  redo_clean_merge_outputs "$OUTPUT_DIR" "$GS_ROOT" "$EXPORT_SPLAT_NAME" "$SPLIT_NUM_BLOCKS"
fi

train_clean="$TRAIN_CLEAN"
[[ "$redo" != true ]] || train_clean=true
if [[ "$phase" != merge ]]; then
  if [[ "$phase" == prepare ]]; then
    phase_args=(--prepare-only)
    set_pipeline_stage "reconstruction SfM et découpage COLMAP"
  elif [[ "$phase" == train-block ]]; then
    set_pipeline_stage "entraînement du bloc ${args[2]}"
  else
    set_pipeline_stage "reconstruction SfM, découpage COLMAP et entraînement des blocs"
  fi
  printf '[pipeline] Étape : %s\n' "$pipeline_stage"
  if [[ "$SPIRULA_DEVICE" == auto && -n "$SPIRULA_BIN" &&
        ( "${COLMAP_METHOD:-spirula}" == spirula || "$TRAIN_BACKEND" == spirula ||
          ( "${SFM_MASKS:-off}" == on && "${SFM_MASKS_BACKEND:-$TRAIN_MASKS_BACKEND}" == spirula ) ||
          ( "$TRAIN_MASKS" == on && "$TRAIN_MASKS_BACKEND" == spirula ) ) ]]; then
    SPIRULA_DEVICE="$(resolve_spirula_device "$SPIRULA_BIN" "$SPIRULA_DEVICE")"
    [[ "$SPIRULA_DEVICE" != auto ]] ||
      die "Aucun GPU Vulkan prêt (discrete/integrated) ; définir SPIRULA_DEVICE explicitement (ex. index du CPU)"
    printf '[pipeline] SPIRULA_DEVICE résolu : %s\n' "$SPIRULA_DEVICE"
  fi
  DIR="$DATASET_DIR" OUTPUT_DIR="$OUTPUT_DIR" \
  COLMAP_METHOD="${COLMAP_METHOD:-spirula}" \
  HLOC_CAMERA_MODEL="${HLOC_CAMERA_MODEL:-OPENCV}" \
  SFM_EQUIRECTANGULAR_VIEW_SIZE="${SFM_EQUIRECTANGULAR_VIEW_SIZE:-1024}" \
  HLOC_FEATURES="${HLOC_FEATURES:-superpoint_max}" \
  HLOC_MATCHER="${HLOC_MATCHER:-superpoint+lightglue}" \
  HLOC_PAIRS="${HLOC_PAIRS:-retrieval}" \
  HLOC_RETRIEVAL="${HLOC_RETRIEVAL:-netvlad}" \
  HLOC_NUM_NEIGHBORS="${HLOC_NUM_NEIGHBORS:-20}" \
  GEOREF_EPSG="$PANORAMAX_BBOX_EPSG" \
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
  SFM_SP_METRIC_POSITIONS="$SFM_SP_METRIC_POSITIONS" \
  SFM_MAX_IMAGE_SIZE="$SFM_MAX_IMAGE_SIZE" \
  SFM_FILTER_DENSITY="$SFM_FILTER_DENSITY" \
  SFM_DENSITY_VOTE_FRACTION="$SFM_DENSITY_VOTE_FRACTION" \
  SFM_DENSITY_FAR_SUPPORT_RATE="$SFM_DENSITY_FAR_SUPPORT_RATE" \
  SFM_ALIKED_MAX_FEATURES="$SFM_ALIKED_MAX_FEATURES" \
  SFM_SP_PAIRS="$SFM_SP_PAIRS" \
  SFM_SP_PREFILTER_NEIGHBORS="$SFM_SP_PREFILTER_NEIGHBORS" \
  SFM_SP_FEATURES="$SFM_SP_FEATURES" \
  SFM_SP_MATCHER="$SFM_SP_MATCHER" \
  NUM_CPUS="$NUM_CPUS" \
  SFM_MAX_ERROR="$SFM_MAX_ERROR" \
  SFM_MIN_INLIERS="$SFM_MIN_INLIERS" \
  SFM_MIN_TRI_ANGLE="$SFM_MIN_TRI_ANGLE" \
  SFM_SP_BA_LOSS="$SFM_SP_BA_LOSS" \
  SFM_SP_BA_LOSS_PARAM="$SFM_SP_BA_LOSS_PARAM" \
  SFM_SP_PAIR_MAX_DISTANCE="$SFM_SP_PAIR_MAX_DISTANCE" \
  SPLIT_OVERLAP_MAX_FRACTION="$SPLIT_OVERLAP_MAX_FRACTION" \
  TRAIN_CLEAN="$train_clean" \
  SPIRULA_DEVICE="$SPIRULA_DEVICE" \
  SFM_MASKS="${SFM_MASKS:-off}" \
  SFM_MASKS_BACKEND="${SFM_MASKS_BACKEND:-$TRAIN_MASKS_BACKEND}" \
  SFM_MASKS_PYTHON="${SFM_MASKS_PYTHON:-$TRAIN_MASKS_PYTHON}" \
  SFM_MASKS_MODEL="${SFM_MASKS_MODEL:-$TRAIN_MASKS_MODEL}" \
  SFM_MASKS_SPIRULA_MODEL="${SFM_MASKS_SPIRULA_MODEL:-$TRAIN_MASKS_SPIRULA_MODEL}" \
  SFM_MASKS_LABELS="${SFM_MASKS_LABELS:-$TRAIN_MASKS_LABELS}" \
  SFM_MASKS_CONFIDENCE="${SFM_MASKS_CONFIDENCE:-$TRAIN_MASKS_CONFIDENCE}" \
  SFM_MASKS_DILATE="${SFM_MASKS_DILATE:-0.002}" \
  SFM_MASKS_MAX_FRACTION="${SFM_MASKS_MAX_FRACTION:-$TRAIN_MASKS_MAX_FRACTION}" \
  SFM_MASKS_DEVICE="${SFM_MASKS_DEVICE:-$TRAIN_MASKS_DEVICE}" \
  TRAIN_MASKS="$TRAIN_MASKS" \
  TRAIN_MASKS_BACKEND="$TRAIN_MASKS_BACKEND" \
  TRAIN_MASKS_SPIRULA_MODEL="$TRAIN_MASKS_SPIRULA_MODEL" \
  MASKS_ACCEPT_LICENSE="$MASKS_ACCEPT_LICENSE" \
  TRAIN_MASKS_PYTHON="$TRAIN_MASKS_PYTHON" \
  TRAIN_MASKS_MODEL="$TRAIN_MASKS_MODEL" \
  TRAIN_MASKS_LABELS="$TRAIN_MASKS_LABELS" \
  TRAIN_MASKS_MODE="$TRAIN_MASKS_MODE" \
  TRAIN_MASKS_CONFIDENCE="$TRAIN_MASKS_CONFIDENCE" \
  TRAIN_MASKS_DILATE="$TRAIN_MASKS_DILATE" \
  TRAIN_MASKS_MAX_FRACTION="$TRAIN_MASKS_MAX_FRACTION" \
  TRAIN_MASKS_DEVICE="$TRAIN_MASKS_DEVICE" \
  NUM_CPUS="$NUM_CPUS" \
  bash "$repo_root/tools/libs/scripts/run_panoramax.sh" "${phase_args[@]}"
fi
if [[ "$phase" == prepare || "$phase" == train-block ]]; then
  printf '[pipeline] Étape %s terminée.\n' "$phase"
  exit 0
fi
LIDAR_SPARSE="$SFM_DIR/sparse/0"
[[ "$LIDAR_COLMAP_ALIGN" != true ]] || LIDAR_SPARSE="$OUTPUT_DIR/colmap_lidar"
lidar_pipeline_hook check --workspace "$OUTPUT_DIR" --model "$SFM_DIR/sparse/0" --images "$IMAGES_DIR"

FINAL_SPLAT="$OUTPUT_DIR/$EXPORT_SPLAT_NAME"
# Keep the georeferencing evidence alongside the final world-coordinate splat.
GEOREF_DIR="$OUTPUT_DIR/${EXPORT_SPLAT_NAME%.splat}.georef"
set_pipeline_stage "copie des références de géoréférencement"
mkdir -p "$GEOREF_DIR"
cp "$DATASET_DIR/request.json" "$DATASET_DIR/summary.json" \
  "$DATASET_DIR/manifest.jsonl" "$GEOREF_DIR/"
mkdir -p "$GEOREF_DIR/metadata"
for metadata_dir in "$DATASET_DIR/metadata" "$DATASET_DIR"/*/metadata; do
  [[ -d "$metadata_dir" ]] || continue
  cp -R "$metadata_dir/." "$GEOREF_DIR/metadata/"
done
mkdir -p "$GEOREF_DIR/sparse"
cp -R "$LIDAR_SPARSE/." "$GEOREF_DIR/sparse/"
cp "$BLOCKS_DIR/manifest.json" "$GEOREF_DIR/blocks_manifest.json"
if [[ -f "$SFM_DIR/.gps_filter_done" ]]; then
  cp "$SFM_DIR/.gps_filter_done" "$GEOREF_DIR/gps_filter_signature.txt"
fi
if [[ -f "$SFM_DIR/projection.json" ]]; then
  cp "$SFM_DIR/projection.json" "$GEOREF_DIR/"
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
    },
    "num_blocks": int(blocks),
    "num_iterations_per_block": int(iterations),
}
reference_path = Path(output).parent / "sparse/georef_stats.json"
if reference_path.is_file():
    stats = json.loads(reference_path.read_text())
    reference = stats["output_reference"]
    data["output_reference"] = reference
    data["coordinate_frame"] = ("COLMAP GPS-aligned local ENU frame"
                                if stats.get("status") == "georeferenced"
                                else "COLMAP LiDAR-aligned local ENU frame")
Path(output).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY

finish_block_splats "$GS_ROOT" "$SPLIT_NUM_BLOCKS" "$FINAL_SPLAT" panoramax_to_3dgs \
  "$GEOREF_DIR/sparse/georef_stats.json"
printf '[pipeline] Terminé.\n'
printf '[pipeline] Splat final : %s\n' "$FINAL_SPLAT"
printf '[pipeline] Géoréférencement et références : %s\n' "$GEOREF_DIR"
