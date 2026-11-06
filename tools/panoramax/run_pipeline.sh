#!/usr/bin/env bash
set -Eeuo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/../.." && pwd)"

usage() {
  cat <<'EOF'
Usage:
  bash run_pipeline.sh /path/to/config.sh [all|prepare|train-block INDEX|merge]

The config is a trusted Bash file. Start from tools/panoramax/config.sh.example.
It defines the Panoramax bbox, output directory, SfM/block/training parameters,
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
: "${BBOX_LAMBERT93:?BBOX_LAMBERT93 doit être défini dans la configuration}"
: "${NUM_BLOCKS:?NUM_BLOCKS doit être défini dans la configuration}"
: "${NUM_ITER:?NUM_ITER doit être défini dans la configuration}"
: "${CAP_MAX:?CAP_MAX doit être défini dans la configuration}"
: "${TRAIN_PRESET:?TRAIN_PRESET doit être défini dans la configuration}"

PANORAMAX_API_URL="${PANORAMAX_API_URL:-https://explore.panoramax.fr/api}"
PANORAMAX_QUALITY="${PANORAMAX_QUALITY:-hd}"
PANORAMAX_DATETIME="${PANORAMAX_DATETIME:-}"
PANORAMAX_IMAGE_MAX="${PANORAMAX_IMAGE_MAX:-}"
SPIRULA_BIN="${SPIRULA_BIN:-}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
SFM_MAX_IMAGE_SIZE="${SFM_MAX_IMAGE_SIZE:-4096}"
SFM_ALIKED_MAX_FEATURES="${SFM_ALIKED_MAX_FEATURES:-4096}"
SFM_PAIRS="${SFM_PAIRS:-prefilter}"
SFM_FEATURES="${SFM_FEATURES:-aliked-n16rot}"
SFM_MATCHER="${SFM_MATCHER:-lightglue}"
SFM_THREADS="${SFM_THREADS:-${SLURM_CPUS_PER_TASK:-}}"
SFM_PAIR_MAX_DISTANCE="${SFM_PAIR_MAX_DISTANCE:-}"
OVERLAP_MAX_FRACTION="${OVERLAP_MAX_FRACTION:-0.05}"
CLEAN_GS="${CLEAN_GS:-false}"
SPLAT_NAME="${SPLAT_NAME:-panoramax.splat}"
SPLAT_MERGE_STRENGTH="${SPLAT_MERGE_STRENGTH:-1.0}"
SPLAT_FILTER_MODE="${SPLAT_FILTER_MODE:-diagnostic}"
SPLAT_FILTER_SIZE_FACTOR="${SPLAT_FILTER_SIZE_FACTOR:-10}"
SPLAT_SPARSE_BBOX_MODE="${SPLAT_SPARSE_BBOX_MODE:-apply}"
SPLAT_SPARSE_BBOX_MARGIN="${SPLAT_SPARSE_BBOX_MARGIN:-0.05}"
SPLATS_PER_IMAGE="${SPLATS_PER_IMAGE:-}"
GROWTH_FACTOR="${GROWTH_FACTOR:-}"
REFINE_STOP_ITER="${REFINE_STOP_ITER:-}"
REFINE_STOP_NUM_ITER="${REFINE_STOP_NUM_ITER:-}"
FLOATER_SUPPRESSION="${FLOATER_SUPPRESSION:-off}"

[[ "$NUM_BLOCKS" =~ ^[1-9][0-9]*$ ]] || die "NUM_BLOCKS doit être un entier positif."
[[ "$NUM_ITER" =~ ^[1-9][0-9]*$ ]] || die "NUM_ITER doit être un entier positif."
[[ "$CAP_MAX" =~ ^[1-9][0-9]*$ ]] || die "CAP_MAX doit être un entier positif."
[[ "$SFM_MAX_IMAGE_SIZE" =~ ^[1-9][0-9]*$ ]] || die "SFM_MAX_IMAGE_SIZE doit être un entier positif."
[[ "$SFM_ALIKED_MAX_FEATURES" =~ ^[1-9][0-9]*$ ]] || die "SFM_ALIKED_MAX_FEATURES doit être un entier positif."
[[ -z "$PANORAMAX_IMAGE_MAX" || "$PANORAMAX_IMAGE_MAX" =~ ^[1-9][0-9]*$ ]] ||
  die "PANORAMAX_IMAGE_MAX doit être vide ou un entier positif."
[[ "$PANORAMAX_QUALITY" == hd || "$PANORAMAX_QUALITY" == sd || "$PANORAMAX_QUALITY" == thumb ]] ||
  die "PANORAMAX_QUALITY doit être hd, sd ou thumb."
[[ "$SPLAT_NAME" == *.splat && "$SPLAT_NAME" != */* && "$SPLAT_NAME" != .splat ]] ||
  die "SPLAT_NAME doit être un nom de fichier terminé par .splat."
[[ "$SPLAT_MERGE_STRENGTH" =~ ^[0-9]+([.][0-9]+)?$ ]] &&
  awk -v value="$SPLAT_MERGE_STRENGTH" 'BEGIN { exit !(value > 0) }' ||
  die "SPLAT_MERGE_STRENGTH doit être un nombre positif."

command -v "$PYTHON_BIN" >/dev/null 2>&1 || die "Python introuvable : $PYTHON_BIN"

mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR="$(cd -- "$OUTPUT_DIR" && pwd)"
DATASET_DIR="$OUTPUT_DIR/panoramax"
SFM_DIR="$DATASET_DIR/sfm-$NUM_ITER"
BLOCKS_DIR="$SFM_DIR/blocks-$NUM_BLOCKS"
GS_ROOT="$DATASET_DIR/gs-${NUM_ITER}-blocks-${NUM_BLOCKS}"
prepare_images() {
  local bbox_args
  [[ ! -e "$DATASET_DIR/request.json" ]] || "$PYTHON_BIN" - \
    "$DATASET_DIR/request.json" "$BBOX_LAMBERT93" "$PANORAMAX_API_URL" \
    "$PANORAMAX_DATETIME" "$PANORAMAX_QUALITY" "$PANORAMAX_IMAGE_MAX" <<'PY'
import json
import sys

path, bbox, api_url, datetime, quality, image_max = sys.argv[1:]
with open(path, encoding="utf-8") as stream:
    previous = json.load(stream)
expected = {
    "bbox": [float(value) for value in bbox.split(",")],
    "api_url": api_url,
    "datetime": datetime or None,
    "quality": quality,
    "image_max": int(image_max) if image_max else None,
}
for key, value in expected.items():
    actual = previous.get(key)
    if key == "api_url":
        actual = actual.rstrip("/")
        value = value.rstrip("/")
    if actual != value:
        raise SystemExit(
            f"Le dossier {path} appartient à une autre configuration "
            f"({key}: {actual!r} != {value!r}). Choisir un OUTPUT_DIR neuf."
        )
PY

  if [[ ! -e "$DATASET_DIR/request.json" && -d "$DATASET_DIR/images" ]] &&
     [[ -n "$(ls -A "$DATASET_DIR/images" 2>/dev/null)" ]]; then
    die "$DATASET_DIR/images contient des photos sans request.json ; choisir un OUTPUT_DIR neuf."
  fi

  bbox_args=(--bbox "$BBOX_LAMBERT93" --output-dir "$DATASET_DIR" \
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
  NUM_ITER="$NUM_ITER" \
  NUM_BLOCKS="$NUM_BLOCKS" \
  CAP_MAX="$CAP_MAX" \
  SPLATS_PER_IMAGE="$SPLATS_PER_IMAGE" \
  GROWTH_FACTOR="$GROWTH_FACTOR" \
  REFINE_STOP_ITER="$REFINE_STOP_ITER" \
  REFINE_STOP_NUM_ITER="$REFINE_STOP_NUM_ITER" \
  FLOATER_SUPPRESSION="$FLOATER_SUPPRESSION" \
  TRAIN_PRESET="$TRAIN_PRESET" \
  SFM_MAX_IMAGE_SIZE="$SFM_MAX_IMAGE_SIZE" \
  SFM_ALIKED_MAX_FEATURES="$SFM_ALIKED_MAX_FEATURES" \
  SFM_PAIRS="$SFM_PAIRS" \
  SFM_FEATURES="$SFM_FEATURES" \
  SFM_MATCHER="$SFM_MATCHER" \
  SFM_THREADS="$SFM_THREADS" \
  SFM_PAIR_MAX_DISTANCE="$SFM_PAIR_MAX_DISTANCE" \
  OVERLAP_MAX_FRACTION="$OVERLAP_MAX_FRACTION" \
  CLEAN_GS="$CLEAN_GS" \
  bash "$repo_root/scripts/run_spirula_blocks.sh" "${phase_args[@]}"
fi
if [[ "$phase" == prepare || "$phase" == train-block ]]; then
  printf '[pipeline] Étape %s terminée.\n' "$phase"
  exit 0
fi

pipeline_stage="vérification des splats avant fusion"
MERGE_INPUTS=()
for ((index = 0; index < NUM_BLOCKS; index++)); do
  block_id="$(printf '%03d' "$index")"
  splat="$GS_ROOT/block_$block_id/splats/Paris-${NUM_ITER}-block-${block_id}.splat"
  [[ -s "$splat" ]] || die "Splat du bloc absent ou vide : $splat"
  MERGE_INPUTS+=("$splat")
done

FINAL_SPLAT="$OUTPUT_DIR/$SPLAT_NAME"
UNFILTERED_SPLAT="$OUTPUT_DIR/${SPLAT_NAME%.splat}.unfiltered.splat"
printf '[pipeline] Fusion des blocs vers %s\n' "$UNFILTERED_SPLAT"
pipeline_stage="fusion des splats des blocs"
"$PYTHON_BIN" "$repo_root/tools/postprocess/merge_splats.py" \
  "${MERGE_INPUTS[@]}" --output "$UNFILTERED_SPLAT" --strength "$SPLAT_MERGE_STRENGTH"
[[ -s "$UNFILTERED_SPLAT" ]] || die "Splat fusionné absent ou vide : $UNFILTERED_SPLAT"
pipeline_stage="filtre contextuel global des splats"
"$PYTHON_BIN" "$repo_root/tools/postprocess/filter_splats.py" \
  --input "$UNFILTERED_SPLAT" --output "$FINAL_SPLAT" \
  --mode "$SPLAT_FILTER_MODE" --size-factor "$SPLAT_FILTER_SIZE_FACTOR" \
  --sparse-points "$SFM_DIR/sparse/0/points3D.bin" \
  --sparse-bbox-mode "$SPLAT_SPARSE_BBOX_MODE" \
  --sparse-bbox-margin "$SPLAT_SPARSE_BBOX_MARGIN" --overwrite
[[ -s "$FINAL_SPLAT" ]] || die "Splat final absent ou vide : $FINAL_SPLAT"

# Keep the georeferencing evidence alongside the final world-coordinate splat.
GEOREF_DIR="$OUTPUT_DIR/${SPLAT_NAME%.splat}.georef"
pipeline_stage="copie des références de géoréférencement"
mkdir -p "$GEOREF_DIR"
cp "$DATASET_DIR/request.json" "$DATASET_DIR/summary.json" \
  "$DATASET_DIR/manifest.jsonl" "$GEOREF_DIR/"
cp -R "$DATASET_DIR/metadata" "$GEOREF_DIR/"
mkdir -p "$GEOREF_DIR/sparse"
cp -R "$SFM_DIR/sparse/0/." "$GEOREF_DIR/sparse/"
cp "$BLOCKS_DIR/manifest.json" "$GEOREF_DIR/blocks_manifest.json"
cp "$OUTPUT_DIR/${SPLAT_NAME%.splat}.filter.json" "$GEOREF_DIR/splat_filter_summary.json"
if [[ -f "$SFM_DIR/gps_filter_summary.json" ]]; then
  cp "$SFM_DIR/gps_filter_summary.json" "$GEOREF_DIR/"
fi
for ((index = 0; index < NUM_BLOCKS; index++)); do
  block_id="$(printf '%03d' "$index")"
  transform="$GS_ROOT/block_$block_id/splats/scene_transform.json"
  [[ -s "$transform" ]] || die "Transformation d'entraînement absente : $transform"
  mkdir -p "$GEOREF_DIR/scene_transforms"
  cp "$transform" "$GEOREF_DIR/scene_transforms/block_${block_id}.json"
done
"$PYTHON_BIN" - "$GEOREF_DIR/georeference.json" "$FINAL_SPLAT" \
  "$SPLAT_NAME" "$NUM_BLOCKS" "$NUM_ITER" <<'PY'
import json
import sys
from pathlib import Path

output, splat_path, splat_name, blocks, iterations = sys.argv[1:]
data = {
    "splat": Path(splat_path).name,
    "format": "splat",
    "coordinate_frame": "Spirula SfM metric frame aligned from Panoramax EXIF GPS",
    "crs": "local ENU; not projected EPSG coordinates",
    "source_gps": "Panoramax image metadata and JPEG EXIF; WGS84 longitude/latitude and EXIF altitude",
    "source_bbox_crs": "EPSG:2154",
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
