#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/.." && pwd)"
# shellcheck source=spirula_common.sh
source "$script_dir/spirula_common.sh"

DIR="${DIR:-}"
SPIRULA_BIN="${SPIRULA_BIN:-$(default_spirula_bin)}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
NUM_ITER="${NUM_ITER:-30000}"
NUM_BLOCKS="${NUM_BLOCKS:-4}"
CAP_MAX="${CAP_MAX:-1500000}"
SPLATS_PER_IMAGE="${SPLATS_PER_IMAGE:-}"
GROWTH_FACTOR="${GROWTH_FACTOR:-}"
REFINE_STOP_ITER="${REFINE_STOP_ITER:-}"
REFINE_STOP_NUM_ITER="${REFINE_STOP_NUM_ITER:-}"
FLOATER_SUPPRESSION="${FLOATER_SUPPRESSION:-}"
TRAIN_PRESET="${TRAIN_PRESET:-3dgs}"
SFM_MAX_IMAGE_SIZE="${SFM_MAX_IMAGE_SIZE:-4096}"
SFM_ALIKED_MAX_FEATURES="${SFM_ALIKED_MAX_FEATURES:-4096}"
SFM_PAIRS="${SFM_PAIRS:-prefilter}"
SFM_FEATURES="${SFM_FEATURES:-aliked-n16rot}"
SFM_MATCHER="${SFM_MATCHER:-lightglue}"
SFM_THREADS="${SFM_THREADS:-}"
SFM_PAIR_MAX_DISTANCE="${SFM_PAIR_MAX_DISTANCE:-}"
OVERLAP_MAX_FRACTION="${OVERLAP_MAX_FRACTION:-0.05}"
CLEAN_GS="${CLEAN_GS:-false}"
mode=all
block_index=""

usage() {
  cat <<'EOF'
Usage:
  DIR=/path/to/panoramax [NUM_BLOCKS=4] bash run_spirula_blocks.sh [options]

Options:
  --prepare-only            Run/reuse SfM and partition, without training
  --block INDEX             Train only this zero-based block; requires preparation

Variables:
  DIR                       Panoramax dataset; contains images/ and metadata/
  SPIRULA_BIN               Spirula executable (default: shared launcher logic)
  PYTHON_BIN                Python with numpy, scipy and tqdm (default: python3)
  NUM_ITER                  SfM workspace suffix and training iterations (30000)
  NUM_BLOCKS                Number of COLMAP blocks (4)
  CAP_MAX                   Maximum splats per block (1500000)
  SPLATS_PER_IMAGE          Optional budget: block cap = min(CAP_MAX, images × value)
  GROWTH_FACTOR             Optional Spirula growth per refine round (> 1)
  REFINE_STOP_ITER          Spirula refine_stop_iter (empty: Spirula 14000)
  REFINE_STOP_NUM_ITER      Spirula refine_stop_num_iter (empty: Spirula 2500)
                            Growth stops at max(REFINE_STOP_ITER, NUM_ITER - REFINE_STOP_NUM_ITER)
  FLOATER_SUPPRESSION       Optional Spirula floater suppression: off, mild, strong
  TRAIN_PRESET              Spirula training preset (3dgs)
  SFM_MAX_IMAGE_SIZE        SfM extraction resolution (4096)
  SFM_ALIKED_MAX_FEATURES   ALIKED feature budget (4096)
  SFM_PAIRS                 Spirula pair selection (prefilter)
  SFM_FEATURES              Feature extractor (aliked-n16rot)
  SFM_MATCHER               Matcher (lightglue; bruteforce bypasses LightGlue)
  SFM_THREADS               SfM CPU threads (empty: Spirula default)
  SFM_PAIR_MAX_DISTANCE     Optional exclusive GPS radius before mapping (metres)
  OVERLAP_MAX_FRACTION      Split overlap fraction (0.05)
  CLEAN_GS=true             Re-train blocks from scratch (destructive to GS outputs)
  MVK_CONFIG_LOG_LEVEL      MoltenVK logging level

Existing SfM and a complete matching block split are reused. Incomplete or
mismatched split folders are never deleted automatically.
EOF
}

die() {
  printf '[ERREUR] %s\n' "$*" >&2
  exit 1
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

case "${1:-}" in
  "") [[ $# -eq 0 ]] || die "Argument vide inattendu." ;;
  --prepare-only)
    [[ $# -eq 1 ]] || die "--prepare-only ne prend pas d'autre argument."
    mode=prepare ;;
  --block)
    [[ $# -eq 2 && "$2" =~ ^(0|[1-9][0-9]*)$ ]] ||
      die "--block attend un indice entier positif ou nul."
    mode=block
    block_index="$2" ;;
  *) die "Argument inattendu : $1" ;;
esac
[[ -n "$DIR" ]] || { usage >&2; die "Définir DIR avec le chemin du dataset Panoramax."; }
[[ -d "$DIR" ]] || die "Dataset introuvable : $DIR"
[[ -d "$DIR/images" ]] || die "Dossier d'images introuvable : $DIR/images"

for value_name in NUM_ITER NUM_BLOCKS CAP_MAX SFM_MAX_IMAGE_SIZE SFM_ALIKED_MAX_FEATURES; do
  value="${!value_name}"
  [[ "$value" =~ ^[1-9][0-9]*$ ]] || die "$value_name doit être un entier positif : $value"
done
[[ "$NUM_BLOCKS" -ge 1 ]] || die "NUM_BLOCKS doit être supérieur ou égal à 1."
[[ "$CAP_MAX" -ge 1 ]] || die "CAP_MAX doit être supérieur ou égal à 1."
[[ -z "$SPLATS_PER_IMAGE" || "$SPLATS_PER_IMAGE" =~ ^[1-9][0-9]*$ ]] ||
  die "SPLATS_PER_IMAGE doit être vide ou un entier positif."
[[ -z "$GROWTH_FACTOR" ]] || {
  [[ "$GROWTH_FACTOR" =~ ^[0-9]+([.][0-9]+)?$ ]] &&
    awk -v x="$GROWTH_FACTOR" 'BEGIN { exit !(x > 1) }'
} || die "GROWTH_FACTOR doit être vide ou un nombre > 1."
for value_name in REFINE_STOP_ITER REFINE_STOP_NUM_ITER; do
  value="${!value_name}"
  [[ -z "$value" || "$value" =~ ^[0-9]+$ ]] ||
    die "$value_name doit être vide ou un entier >= 0 : $value"
done
growth_stop=$(( ${REFINE_STOP_ITER:-14000} > NUM_ITER - ${REFINE_STOP_NUM_ITER:-2500} ?
                ${REFINE_STOP_ITER:-14000} : NUM_ITER - ${REFINE_STOP_NUM_ITER:-2500} ))
if ((growth_stop >= NUM_ITER)); then
  printf '[AVERTISSEMENT] Croissance des splats jusqu’à la fin (pas %s >= NUM_ITER=%s) : aucune phase d’affinage seul.\n' \
    "$growth_stop" "$NUM_ITER" >&2
fi
case "$FLOATER_SUPPRESSION" in
  ""|off|mild|strong) ;;
  *) die "FLOATER_SUPPRESSION doit être vide, off, mild ou strong." ;;
esac
[[ -z "$block_index" || "$block_index" -lt "$NUM_BLOCKS" ]] ||
  die "Indice de bloc $block_index hors de [0,$((NUM_BLOCKS - 1))]."
[[ -z "$SFM_THREADS" || "$SFM_THREADS" =~ ^[1-9][0-9]*$ ]] ||
  die "SFM_THREADS doit être vide ou un entier positif."
if [[ -n "$SFM_PAIR_MAX_DISTANCE" ]]; then
  [[ "$SFM_PAIR_MAX_DISTANCE" =~ ^[0-9]+([.][0-9]+)?$ ]] &&
    awk -v radius="$SFM_PAIR_MAX_DISTANCE" 'BEGIN { exit !(radius > 0) }' ||
    die "SFM_PAIR_MAX_DISTANCE doit être vide ou un nombre positif."
fi
[[ "$OVERLAP_MAX_FRACTION" =~ ^(0([.][0-9]+)?|1([.]0+)?)$ ]] ||
  die "OVERLAP_MAX_FRACTION doit être dans [0,1] : $OVERLAP_MAX_FRACTION"
case "$CLEAN_GS" in
  true|1|yes|on) CLEAN_GS=true ;;
  false|0|no|off) CLEAN_GS=false ;;
  *) die "CLEAN_GS invalide : $CLEAN_GS" ;;
esac

command -v "$PYTHON_BIN" >/dev/null 2>&1 || die "Python introuvable : $PYTHON_BIN"
if [[ "$SPIRULA_BIN" == */* ]]; then
  [[ -x "$SPIRULA_BIN" ]] || die "Spirula introuvable ou non exécutable : $SPIRULA_BIN"
else
  SPIRULA_BIN="$(command -v "$SPIRULA_BIN")" || die "Spirula introuvable dans le PATH."
fi

DIR="$(cd -- "$DIR" && pwd)"
SFM_DIR="$DIR/sfm-$NUM_ITER"
SPARSE_DIR="$SFM_DIR/sparse/0"
BLOCKS_DIR="$SFM_DIR/blocks-$NUM_BLOCKS"
GS_ROOT="$DIR/gs-${NUM_ITER}-blocks-${NUM_BLOCKS}"
SPLITTER="$repo_root/tools/preprocess/colmap_split_blocks.py"

export MVK_CONFIG_LOG_LEVEL="${MVK_CONFIG_LOG_LEVEL:-1}"

prepare_blocks() {
  local block_count block_id block_sparse file index
  local sfm_args
  if [[ -n "$SFM_PAIR_MAX_DISTANCE" ]]; then
    if [[ -d "$GS_ROOT" && ( ! -s "$SPARSE_DIR/cameras.bin" ||
          ! -s "$SPARSE_DIR/images.bin" || ! -s "$SPARSE_DIR/points3D.bin" ) ]]; then
      die "Recalcul du SfM filtré avec d'anciens entraînements : déplacer $GS_ROOT en sauvegarde pour éviter leur réutilisation."
    fi
    sfm_args=(
      "$repo_root/tools/panoramax/run_sfm_filtered.py"
      --dataset "$DIR" --output "$SFM_DIR" --spirula-bin "$SPIRULA_BIN"
      --max-distance "$SFM_PAIR_MAX_DISTANCE" --pairs "$SFM_PAIRS"
      --features "$SFM_FEATURES" --matcher "$SFM_MATCHER"
      --max-image-size "$SFM_MAX_IMAGE_SIZE"
      --aliked-max-features "$SFM_ALIKED_MAX_FEATURES"
    )
    [[ -z "$SFM_THREADS" ]] || sfm_args+=(--threads "$SFM_THREADS")
    "$PYTHON_BIN" "${sfm_args[@]}"
  elif [[ -f "$SFM_DIR/gps_filter_run.json" ]]; then
    die "Ce SfM a été filtré par GPS ; conserver SFM_PAIR_MAX_DISTANCE ou déplacer $SFM_DIR en sauvegarde pour désactiver le filtre."
  elif [[ -s "$SPARSE_DIR/cameras.bin" &&
        -s "$SPARSE_DIR/images.bin" &&
        -s "$SPARSE_DIR/points3D.bin" ]]; then
    printf '[pipeline] SfM existant : %s (ignoré).\n' "$SPARSE_DIR"
  else
    printf '[pipeline] Lancement du SfM.\n'
    sfm_args=(
      --spirula-bin "$SPIRULA_BIN"
      --dataset "$DIR"
      --output-dir "$SFM_DIR"
      --preset panoramax
      --pairs "$SFM_PAIRS"
      --max-image-size "$SFM_MAX_IMAGE_SIZE"
      --aliked-max-features "$SFM_ALIKED_MAX_FEATURES"
      --features "$SFM_FEATURES"
      --matcher "$SFM_MATCHER"
    )
    [[ -z "$SFM_THREADS" ]] || sfm_args+=(--threads "$SFM_THREADS")
    bash "$script_dir/run_spirula_sfm.sh" "${sfm_args[@]}"
  fi

  for file in cameras.bin images.bin points3D.bin; do
    [[ -s "$SPARSE_DIR/$file" ]] || die "Artefact SfM absent ou vide : $SPARSE_DIR/$file"
  done
  [[ -d "$SFM_DIR/images" ]] || die "Images préparées par SfM absentes : $SFM_DIR/images"

  if [[ -s "$BLOCKS_DIR/manifest.json" ]]; then
    block_count="$("$PYTHON_BIN" - "$BLOCKS_DIR/manifest.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as manifest:
    data = json.load(manifest)
print(len(data.get("blocks", [])))
PY
)" || die "Manifest de blocs invalide : $BLOCKS_DIR/manifest.json"
    [[ "$block_count" == "$NUM_BLOCKS" ]] ||
      die "$BLOCKS_DIR contient $block_count blocs, mais NUM_BLOCKS=$NUM_BLOCKS. Choisir un autre nombre ou un autre workspace."
    for ((index = 0; index < NUM_BLOCKS; index++)); do
      block_id="$(printf '%03d' "$index")"
      block_sparse="$BLOCKS_DIR/block_$block_id"
      for file in cameras.bin images.bin points3D.bin; do
        [[ -s "$block_sparse/$file" ]] ||
          die "Découpage existant incomplet : $block_sparse/$file. Il n'a pas été modifié."
      done
    done
    printf '[pipeline] Découpage existant : %s (%s blocs).\n' "$BLOCKS_DIR" "$NUM_BLOCKS"
  else
    if [[ -e "$BLOCKS_DIR" ]] && [[ -n "$(ls -A "$BLOCKS_DIR" 2>/dev/null)" ]]; then
      die "Sortie de découpage non vide sans manifest valide : $BLOCKS_DIR. Déplacer ou renommer ce dossier avant de relancer."
    fi
    printf '[pipeline] Découpage de %s en %s blocs.\n' "$SPARSE_DIR" "$NUM_BLOCKS"
    "$PYTHON_BIN" "$SPLITTER" \
      --input "$SPARSE_DIR" \
      --output "$BLOCKS_DIR" \
      --num-blocks "$NUM_BLOCKS" \
      --export-binary \
      --overlap-max-fraction "$OVERLAP_MAX_FRACTION"
    [[ -s "$BLOCKS_DIR/manifest.json" ]] || die "Le découpeur n'a pas produit manifest.json."
  fi
}
if [[ "$mode" != block ]]; then
  prepare_blocks
fi

[[ "$mode" != prepare ]] || {
  printf '[pipeline] Préparation terminée : %s\n' "$BLOCKS_DIR"
  exit 0
}

if [[ "$mode" == block ]]; then
  [[ -s "$BLOCKS_DIR/manifest.json" ]] ||
    die "Découpage absent : lancer --prepare-only avant --block."
  for file in cameras.bin images.bin points3D.bin; do
    [[ -s "$BLOCKS_DIR/block_$(printf '%03d' "$block_index")/$file" ]] ||
      die "Bloc $block_index incomplet : $file absent."
  done
  [[ -d "$SFM_DIR/images" ]] || die "Images préparées absentes : $SFM_DIR/images"
fi

mkdir -p "$GS_ROOT"
first_index="${block_index:-0}"
last_index="$NUM_BLOCKS"
[[ "$mode" != block ]] || last_index=$((block_index + 1))
for ((index = first_index; index < last_index; index++)); do
  block_id="$(printf '%03d' "$index")"
  block_sparse="$BLOCKS_DIR/block_$block_id"
  block_output="$GS_ROOT/block_$block_id"
  splat_name="Paris-${NUM_ITER}-block-${block_id}.splat"
  printf '\n[pipeline] Entraînement du bloc %s/%s.\n' "$((index + 1))" "$NUM_BLOCKS"

  block_cap="$CAP_MAX"
  if [[ -n "$SPLATS_PER_IMAGE" ]]; then
    block_images="$(od -An -t u8 -N 8 "$block_sparse/images.bin" | tr -d '[:space:]')"
    [[ "$block_images" =~ ^[1-9][0-9]*$ ]] ||
      die "Nombre d'images illisible : $block_sparse/images.bin"
    block_cap=$((block_images * SPLATS_PER_IMAGE))
    ((block_cap <= CAP_MAX)) || block_cap="$CAP_MAX"
    printf '[pipeline] Budget du bloc : %s images × %s = %s splats (plafond %s).\n' \
      "$block_images" "$SPLATS_PER_IMAGE" "$block_cap" "$CAP_MAX"
  fi
  block_args=(
    --spirula-bin "$SPIRULA_BIN"
    --sparse-dir "$block_sparse"
    --images-dir "$SFM_DIR/images"
    --output-dir "$block_output"
    --train-preset "$TRAIN_PRESET"
    --cap-max "$block_cap"
    --num-iterations "$NUM_ITER"
    --name "$splat_name"
    --python-bin "$PYTHON_BIN"
    --export-filter-mode off
  )
  [[ -z "$GROWTH_FACTOR" ]] || block_args+=(--growth-factor "$GROWTH_FACTOR")
  [[ -z "$FLOATER_SUPPRESSION" ]] || block_args+=(--floater-suppression "$FLOATER_SUPPRESSION")
  [[ -z "$REFINE_STOP_ITER" ]] || block_args+=(--refine-stop-iter "$REFINE_STOP_ITER")
  [[ -z "$REFINE_STOP_NUM_ITER" ]] || block_args+=(--refine-stop-num-iter "$REFINE_STOP_NUM_ITER")
  printf '[pipeline] Ajout de splats jusqu’au pas %s sur %s.\n' "$((growth_stop < NUM_ITER ? growth_stop : NUM_ITER))" "$NUM_ITER"
  [[ "$CLEAN_GS" != true ]] || block_args+=(--clean)
  bash "$script_dir/run_spirula_block.sh" "${block_args[@]}"
done

printf '\n[pipeline] Terminé. Modèles par bloc : %s/block_*/splats/*.splat\n' "$GS_ROOT"
