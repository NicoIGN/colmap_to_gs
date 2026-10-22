#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

spirula_bin="${SPIRULA_BIN:-./build/spirula}"
sparse_dir="${SPARSE_DIR:-}"
images_dir="${IMAGES_DIR:-}"
output_dir="${OUTPUT_DIR:-}"
train_preset="${TRAIN_PRESET:-3dgs}"
clean="${CLEAN:-false}"
export_name="block.splat"

python_bin="${PYTHON_BIN:-python}"
export_script="${EXPORT_SCRIPT:-$script_dir/../python/export_ply.py}"

quality="${QUALITY:-}"
cap_max="${CAP_MAX:-}"
densify_loss_map_mode="${DENSIFY_LOSS_MAP_MODE:-}"
densify_score_mode="${DENSIFY_SCORE_MODE:-}"
min_init_fraction="${MIN_INIT_FRACTION:-}"
num_iterations="${NUM_ITERATIONS:-50000}"
train_resolution_divisor="${TRAIN_RESOLUTION_DIVISOR:-}"

long_axis_split_opacity_k=()
if [[ -n "${LONG_AXIS_SPLIT_OPACITY_K:-}" ]]; then
  values="${LONG_AXIS_SPLIT_OPACITY_K//,/ }"
  read -r -a long_axis_split_opacity_k <<< "$values"
fi

usage() {
  cat <<'EOF'
Usage:
  bash run_spirula_block.sh --sparse-dir <dir> --images-dir <dir> --output-dir <dir> [options]

Important:
  - La visionneuse est désactivée.
  - Le checkpoint final attendu dépend de --num-iterations:
      {output_dir}/splats/step-XXXXXXXXX.ckpt/splat.ply
  - Export post-train:
      conversion du PLY final vers splats/block_000.splat (repère monde)
      via python/export_ply.py et splats/scene_transform.json
  - Marqueurs:
      .train_done = train terminé
      .export_done = export terminé

Options:
  --spirula-bin PATH
  --sparse-dir PATH
  --images-dir PATH
  --output-dir PATH
  --train-preset NAME
  --quality LEVEL
  --cap-max N
  --densify-loss-map-mode MODE
  --densify-score-mode MODE
  --long-axis-split-opacity-k A B C
  --min-init-fraction N
  --num-iterations N
  --train-resolution-divisor N
  --python-bin PATH
  --export-script PATH
  --name FILENAME           Nom de sortie (défaut: block.splat)
  --clean
EOF
}

die(){ echo "[ERREUR] $*" >&2; exit 1; }
log(){ echo "[run_spirula_block] $*"; }
need_arg(){ [[ $# -ge 2 && -n "${2:-}" ]] || die "Valeur manquante pour $1"; }
is_positive_integer(){ [[ "$1" =~ ^[1-9][0-9]*$ ]]; }
is_nonnegative_integer(){ [[ "$1" =~ ^[0-9]+$ ]]; }
is_number(){ [[ "$1" =~ ^[0-9]+([.][0-9]+)?$ ]]; }

canonical_path() {
  python3 - "$1" <<'PY'
from pathlib import Path
import sys
print(Path(sys.argv[1]).resolve())
PY
}

while [[ $# -gt 0 ]]; do
  if [[ "$1" == --*=* ]]; then
    option="${1%%=*}"
    value="${1#*=}"
    shift
    if [[ "$option" == "--long-axis-split-opacity-k" ]]; then
      value="${value//,/ }"
      read -r -a long_axis_split_opacity_k <<< "$value"
      continue
    fi
    set -- "$option" "$value" "$@"
  fi

  case "$1" in
    --spirula-bin) need_arg "$@"; spirula_bin="$2"; shift 2 ;;
    --sparse-dir) need_arg "$@"; sparse_dir="$2"; shift 2 ;;
    --images-dir) need_arg "$@"; images_dir="$2"; shift 2 ;;
    --output-dir) need_arg "$@"; output_dir="$2"; shift 2 ;;
    --train-preset) need_arg "$@"; train_preset="$2"; shift 2 ;;
    --quality) need_arg "$@"; quality="$2"; shift 2 ;;
    --cap-max) need_arg "$@"; cap_max="$2"; shift 2 ;;
    --densify-loss-map-mode) need_arg "$@"; densify_loss_map_mode="$2"; shift 2 ;;
    --densify-score-mode) need_arg "$@"; densify_score_mode="$2"; shift 2 ;;
    --long-axis-split-opacity-k)
      [[ $# -ge 4 ]] || die "--long-axis-split-opacity-k attend A B C"
      long_axis_split_opacity_k=("$2" "$3" "$4")
      shift 4
      ;;
    --min-init-fraction) need_arg "$@"; min_init_fraction="$2"; shift 2 ;;
    --num-iterations) need_arg "$@"; num_iterations="$2"; shift 2 ;;
    --train-resolution-divisor) need_arg "$@"; train_resolution_divisor="$2"; shift 2 ;;
    --python-bin) need_arg "$@"; python_bin="$2"; shift 2 ;;
    --export-script) need_arg "$@"; export_script="$2"; shift 2 ;;
    --name) need_arg "$@"; export_name="$2"; shift 2 ;;
    --clean)
      if [[ "${2:-}" == "true" || "${2:-}" == "false" ]]; then
        clean="$2"; shift 2
      else
        clean=true; shift
      fi
      ;;
    -h|--help) usage; exit 0 ;;
    *) die "Argument inconnu : $1" ;;
  esac
done

[[ -n "$sparse_dir" ]] || die "--sparse-dir requis"
[[ -n "$images_dir" ]] || die "--images-dir requis"
[[ -n "$output_dir" ]] || die "--output-dir requis"
[[ -d "$sparse_dir" ]] || die "Bloc introuvable : $sparse_dir"
[[ -d "$images_dir" ]] || die "Images introuvables : $images_dir"

# Le fichier doit rester directement dans le dossier splats.
[[ -n "$export_name" && "$export_name" != */* ]] ||  die "--name doit être un nom de fichier, sans chemin"
[[ "$export_name" == *.splat ]] ||  die "--name doit se terminer par .splat"


case "$clean" in
  true|1|yes|on) clean=true ;;
  false|0|no|off) clean=false ;;
  *) die "CLEAN invalide : $clean" ;;
esac

is_positive_integer "$num_iterations" ||
  die "--num-iterations doit être un entier > 0"

if [[ -n "$cap_max" ]]; then
  is_positive_integer "$cap_max" || die "--cap-max doit être un entier > 0"
fi

if [[ -n "$train_resolution_divisor" ]]; then
  is_nonnegative_integer "$train_resolution_divisor" ||
    die "--train-resolution-divisor doit être un entier >= 0"
fi

if [[ -n "$min_init_fraction" ]]; then
  is_number "$min_init_fraction" || die "--min-init-fraction invalide"
fi

if [[ ${#long_axis_split_opacity_k[@]} -gt 0 ]]; then
  [[ ${#long_axis_split_opacity_k[@]} -eq 3 ]] ||
    die "--long-axis-split-opacity-k attend exactement 3 valeurs"
  for value in "${long_axis_split_opacity_k[@]}"; do
    is_number "$value" || die "Valeur opacity invalide : $value"
  done
fi

sparse_dir="$(canonical_path "$sparse_dir")"
images_dir="$(canonical_path "$images_dir")"
output_dir="$(canonical_path "$output_dir")"
export_script="$(canonical_path "$export_script")"

if [[ "$spirula_bin" == */* ]]; then
  spirula_bin="$(canonical_path "$spirula_bin")"
  [[ -x "$spirula_bin" ]] || die "Exécutable introuvable : $spirula_bin"
else
  spirula_bin="$(command -v "$spirula_bin")" || die "Spirula introuvable"
fi

command -v "$python_bin" >/dev/null 2>&1 ||
  die "Python introuvable : $python_bin"

[[ -f "$export_script" ]] || die "Script d'export introuvable : $export_script"

model_ext=""
for ext in bin txt; do
  if [[ -f "$sparse_dir/cameras.$ext" &&
        -f "$sparse_dir/images.$ext" &&
        -f "$sparse_dir/points3D.$ext" ]]; then
    model_ext="$ext"
    break
  fi
done
[[ -n "$model_ext" ]] || die "Modèle COLMAP incomplet dans $sparse_dir"

mkdir -p "$output_dir"

lock_dir="$output_dir/.training_lock"
mkdir "$lock_dir" 2>/dev/null || die "Sortie verrouillée : $lock_dir"
trap 'rmdir "$lock_dir" 2>/dev/null || true' EXIT

splats_dir="$output_dir/splats"
train_done_file="$output_dir/.train_done"
export_done_file="$output_dir/.export_done"

export_transform="$splats_dir/scene_transform.json"
export_points="$output_dir/sparse/0/points3D.bin"
world_splat="$splats_dir/$export_name"

iteration_count=$((10#$num_iterations))
printf -v checkpoint_name 'step-%09d.ckpt' "$iteration_count"
final_ckpt="$splats_dir/$checkpoint_name/splat.ply"

mark_train_done() {
  {
    printf 'finished_at=%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    printf 'reason=%s\n' "$1"
    printf 'iterations=%s\n' "$iteration_count"
  } > "$train_done_file"
}

mark_export_done() {
  {
    printf 'finished_at=%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    printf 'reason=%s\n' "$1"
    printf 'output=%s\n' "$world_splat"
  } > "$export_done_file"
}

export_world_splat() {
  [[ -s "$final_ckpt" ]] || die "Checkpoint final absent ou vide : $final_ckpt"
  [[ -s "$export_transform" ]] || die "Transformation entraînement absente : $export_transform"
  [[ -s "$export_points" ]] || die "Points COLMAP binaires absents : $export_points"

  local export_cmd=(
    "$python_bin" "$export_script"
    --in-ply "$final_ckpt"
    --points "$export_points"
    --transform "$export_transform"
    --out-ply "$world_splat"
    --format splat
    --radius-factor 10
    --overwrite
  )

  log "Nettoyage + conversion monde -> .splat"
  printf '%q ' "${export_cmd[@]}" > "$output_dir/export_command.sh"
  printf '\n' >> "$output_dir/export_command.sh"

  rm -f -- "$export_done_file"
  "${export_cmd[@]}" 2>&1 | tee "$output_dir/export.log"

  [[ -s "$world_splat" ]] || die "Export absent ou vide : $world_splat"
  mark_export_done "completed_export"
  log "Export terminé : $world_splat"
  log "Marqueur export : $export_done_file"
}

if [[ "$clean" == false ]]; then
  if [[ -s "$final_ckpt" ]]; then
    log "Checkpoint final déjà présent. Entraînement ignoré."
    mark_train_done "found_final_ckpt"
    export_world_splat
    exit 0
  fi

  if [[ -f "$train_done_file" ]]; then
    die ".train_done présent mais checkpoint final absent : $final_ckpt"
  fi

  if [[ -d "$splats_dir" ]]; then
    die "Sortie partielle détectée ($splats_dir). Utiliser --clean pour recalculer."
  fi
fi

if [[ "$clean" == true ]]; then
  rm -rf -- "$splats_dir"
  rm -f -- "$train_done_file" "$export_done_file"
fi

[[ ! -L "$output_dir/sparse" ]] || die "$output_dir/sparse ne doit pas être un lien symbolique"
[[ ! -L "$output_dir/sparse/0" ]] || die "$output_dir/sparse/0 ne doit pas être un lien symbolique"
mkdir -p "$output_dir/sparse/0"

for name in cameras images points3D rigs frames; do
  rm -f -- "$output_dir/sparse/0/$name.bin" "$output_dir/sparse/0/$name.txt"
done

for name in cameras images points3D; do
  cp "$sparse_dir/$name.$model_ext" "$output_dir/sparse/0/$name.$model_ext"
done

for name in rigs frames; do
  if [[ -f "$sparse_dir/$name.$model_ext" ]]; then
    cp "$sparse_dir/$name.$model_ext" "$output_dir/sparse/0/$name.$model_ext"
  fi
done

if [[ -f "$sparse_dir/image_membership.json" ]]; then
  cp "$sparse_dir/image_membership.json" "$output_dir/image_membership.json"
fi

images_link="$output_dir/images"
if [[ -L "$images_link" ]]; then
  existing="$(canonical_path "$images_link")"
  [[ "$existing" == "$images_dir" ]] ||
    die "$images_link pointe vers une autre source : $existing"
elif [[ -e "$images_link" ]]; then
  die "$images_link existe et n'est pas un lien symbolique"
else
  ln -s "$images_dir" "$images_link"
fi

cmd=(
  "$spirula_bin" train "$train_preset"
  --data "$output_dir"
  --data-format colmap
  --colmap-recon-dir sparse/0
  --image-dir images
  --output-dir-prefix "$output_dir"
  --output-dir-name splats
  --lang fr
  --disable-viewer 1
  --keep-viewer-alive 0
  --save-full-checkpoint 1
)

[[ -z "$quality" ]] || cmd+=(--quality "$quality")
[[ -z "$cap_max" ]] || cmd+=(--cap-max "$cap_max")
[[ -z "$densify_loss_map_mode" ]] || cmd+=(--densify-loss-map-mode "$densify_loss_map_mode")
[[ -z "$densify_score_mode" ]] || cmd+=(--densify-score-mode "$densify_score_mode")
[[ -z "$min_init_fraction" ]] || cmd+=(--min-init-fraction "$min_init_fraction")
[[ -z "$num_iterations" ]] || cmd+=(--num-iterations "$num_iterations")
[[ -z "$train_resolution_divisor" ]] || cmd+=(--train-resolution-divisor "$train_resolution_divisor")

if [[ ${#long_axis_split_opacity_k[@]} -gt 0 ]]; then
  cmd+=(--long-axis-split-opacity-k "${long_axis_split_opacity_k[@]}")
fi

log "Spirula : $spirula_bin"
log "Bloc    : $sparse_dir"
log "Images  : $images_dir"
log "Sortie  : $output_dir"
log "Preset  : $train_preset"

printf '%q ' "${cmd[@]}" > "$output_dir/train_command.sh"
printf '\n' >> "$output_dir/train_command.sh"

rm -f -- "$train_done_file" "$export_done_file"

if command -v stdbuf >/dev/null 2>&1; then
  stdbuf -oL -eL "${cmd[@]}" 2>&1 | tee "$output_dir/train.log"
else
  "${cmd[@]}" 2>&1 | tee "$output_dir/train.log"
fi

[[ -s "$final_ckpt" ]] ||
  die "Entraînement terminé mais checkpoint final introuvable : $final_ckpt"

mark_train_done "completed_training"
log "Entraînement terminé."
log "Marqueur train : $train_done_file"

export_world_splat
