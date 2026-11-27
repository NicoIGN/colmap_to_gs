#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
grid_py="$repo_root/tools/grid/grid_tiles.py"
source "$repo_root/tools/libs/scripts/shell_helpers.sh"
source "$repo_root/tools/libs/scripts/pipeline_config.sh"
load_pipeline_defaults "$repo_root" libs

usage() {
  cat <<'EOF'
Usage:
  bash scripts/grid_to_3dgs.sh /path/to/config.sh [--plan-only] [--redo]

Mode GRID : chaque dalle est une exécution indépendante du pipeline (Panoramax ou images
géoréférencées), calculée avec une marge puis clippée à l'emprise de la dalle.
Sorties : GRID_OUTPUT_DIR/splats/<dalle>.splat (pas d'export fusionné), manifest.json.

Variables : GRID_SOURCE (panoramax|images), GRID_BBOX (xmin,ymin,xmax,ymax), GRID_CRS (EPSG),
GRID_TILE_SIZE, GRID_OVERLAP, GRID_MARGIN (mètres), GRID_IMAGES_DIR (mode images),
GRID_OUTPUT_DIR (défaut : $OUTPUT_DIR/grid).
Contraintes : SPLIT_NUM_BLOCKS=1 et NUM_CPUS x NUM_GPUS < 64.
Parallélisme : NUM_GPUS workers, worker w utilise CUDA_VISIBLE_DEVICES=w et les dalles d'indice i avec i % NUM_GPUS = w.
EOF
}

[[ "${1:-}" != --help && "${1:-}" != -h ]] || { usage; exit 0; }
[[ $# -ge 1 ]] || { usage >&2; exit 1; }
config="$(resolve_path "$1")"
shift
plan_only=false
redo=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --plan-only) plan_only=true ;;
    --redo) redo=true ;;
    *) die "Option inconnue : $1" ;;
  esac
  shift
done

[[ -f "$config" ]] || die "Configuration introuvable : $config"
source "$config"

[[ "$SPLIT_NUM_BLOCKS" == 1 ]] || die "Mode GRID : SPLIT_NUM_BLOCKS doit valoir 1 (actuel : $SPLIT_NUM_BLOCKS)."
is_positive_integer "$NUM_CPUS" || die "NUM_CPUS doit être un entier >= 1."
is_positive_integer "$NUM_GPUS" || die "NUM_GPUS doit être un entier >= 1."
(( NUM_CPUS * NUM_GPUS < 64 )) || die "Mode GRID : NUM_CPUS x NUM_GPUS doit être < 64 (actuel : $((NUM_CPUS * NUM_GPUS)))."
case "$GRID_SOURCE" in
  panoramax)
    [[ -z "${GRID_POSITIONS:-}" ]] || die "Mode GRID panoramax : les positions viennent de l'EXIF ; GRID_POSITIONS n'est pas utilisé."
    [[ -z "${SFM_SP_METRIC_POSITIONS:-}" ]] || die "Mode GRID panoramax : SFM_SP_METRIC_POSITIONS global n'est pas compatible avec les dalles ; les positions viennent de l'EXIF."
    ;;
  images) [[ -d "${GRID_IMAGES_DIR:-}" ]] || die "Mode GRID images : GRID_IMAGES_DIR doit être un dossier existant."
    [[ -z "${GRID_POSITIONS:-}" || -f "$GRID_POSITIONS" ]] || die "GRID_POSITIONS doit être un fichier existant." ;;
  *) die "GRID_SOURCE doit valoir panoramax ou images." ;;
esac
[[ -n "${GRID_BBOX:-}" ]] || die "GRID_BBOX est requis (xmin,ymin,xmax,ymax)."
is_positive_integer "$GRID_CRS" || die "GRID_CRS doit être un code EPSG numérique."
[[ -n "${OUTPUT_DIR:-}" ]] || die "OUTPUT_DIR est requis."

mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR="$(cd -- "$OUTPUT_DIR" && pwd)"
GRID_OUTPUT_DIR="${GRID_OUTPUT_DIR:-$OUTPUT_DIR/grid}"
mkdir -p "$GRID_OUTPUT_DIR"
GRID_OUTPUT_DIR="$(cd -- "$GRID_OUTPUT_DIR" && pwd)"
work_dir="$GRID_OUTPUT_DIR/work"
log_dir="$GRID_OUTPUT_DIR/logs"
splat_dir="$GRID_OUTPUT_DIR/splats"
mkdir -p "$work_dir" "$log_dir" "$splat_dir"
manifest="$GRID_OUTPUT_DIR/manifest.json"

python3 "$grid_py" plan --bbox "$GRID_BBOX" --epsg "$GRID_CRS" --tile-size "$GRID_TILE_SIZE" \
  --overlap "$GRID_OVERLAP" --margin "$GRID_MARGIN" --output "$manifest"
mapfile -t tile_lines < <(python3 "$grid_py" tiles --manifest "$manifest")
echo "[GRID] ${#tile_lines[@]} dalle(s), marge ${GRID_MARGIN} m, recouvrement ${GRID_OVERLAP} m, $NUM_GPUS GPU(s), $NUM_CPUS CPU(s) par dalle."

if [[ "$plan_only" == true ]]; then
  printf '%s\n' "${tile_lines[@]}"
  exit 0
fi

if [[ "$GRID_SOURCE" == images ]]; then
  python3 "$grid_py" select-images --manifest "$manifest" --images "$GRID_IMAGES_DIR" --output-dir "$work_dir" \
    ${GRID_POSITIONS:+--positions "$GRID_POSITIONS"} ${SFM_SP_METRIC_POSITIONS:+--global-metric "$SFM_SP_METRIC_POSITIONS"}
fi

redo_args=()
[[ "$redo" != true ]] || redo_args=(--redo)

run_tile() {
  local id="$1" x0="$2" y0="$3" x1="$4" y1="$5" cx0="$6" cy0="$7" cx1="$8" cy1="$9"
  local tile_dir="$work_dir/$id" run_dir splat_out tile_conf
  run_dir="$tile_dir/run"
  splat_out="$splat_dir/$id.splat"
  tile_conf="$tile_dir/tile.conf"

  if [[ -f "$splat_out" && "$redo" != true ]]; then
    echo "[SKIP] $id : déjà exporté"
    return 0
  fi
  if [[ "$GRID_SOURCE" == images && -z "$(ls -A "$tile_dir/images" 2>/dev/null)" ]]; then
    echo "[WARN] $id : aucune image dans l'emprise de calcul, dalle ignorée"
    return 0
  fi

  mkdir -p "$tile_dir"
  # Fichier métrique propre à la dalle (images seulement) : utilisé par Spirula et par le géoréférencement.
  local metric_line=""
  if [[ "$GRID_SOURCE" == images && -f "$tile_dir/metric_positions.txt" ]]; then
    metric_line="SFM_SP_METRIC_POSITIONS=\"$tile_dir/metric_positions.txt\""
  fi
  cat > "$tile_conf" <<EOF
# Généré par grid_to_3dgs.sh : configuration utilisateur puis surcharges de la dalle.
source "$config"
OUTPUT_DIR="$run_dir"
EXPORT_SPLAT_NAME="$id.splat"
SPLIT_NUM_BLOCKS=1
NUM_GPUS=1
PANORAMAX_BBOX="$cx0,$cy0,$cx1,$cy1"
PANORAMAX_BBOX_EPSG="$GRID_CRS"
$metric_line
EOF

  if [[ "$GRID_SOURCE" == panoramax ]]; then
    bash "$repo_root/scripts/panoramax_to_3dgs.sh" "$tile_conf" all "${redo_args[@]}" || return 1
  else
    bash "$repo_root/scripts/images_to_3dgs.sh" "$tile_conf" --images "$tile_dir/images" "${redo_args[@]}" || return 1
  fi

  python3 "$repo_root/tools/export/filter_splat_by_bbox.py" \
    --input "$run_dir/$id.splat" --output "$splat_out" --format splat \
    --georef "$run_dir/$id.metadata.json" --epsg "$GRID_CRS" \
    --bbox "$x0,$y0,$x1,$y1" --overwrite || return 1
  echo "[OK] $id -> $splat_out"
}

rm -f "$log_dir/failed.txt"
pids=()
for worker in $(seq 0 $((NUM_GPUS - 1))); do
  (
    export CUDA_VISIBLE_DEVICES="$worker"
    for index in "${!tile_lines[@]}"; do
      (( index % NUM_GPUS == worker )) || continue
      IFS=$'\t' read -r id x0 y0 x1 y1 cx0 cy0 cx1 cy1 <<< "${tile_lines[$index]}"
      if ! run_tile "$id" "$x0" "$y0" "$x1" "$y1" "$cx0" "$cy0" "$cx1" "$cy1" > "$log_dir/$id.log" 2>&1; then
        echo "$id" >> "$log_dir/failed.txt"
        echo "[ERREUR] $id : voir $log_dir/$id.log" >&2
      fi
    done
  ) &
  pids+=("$!")
done
for pid in "${pids[@]}"; do
  wait "$pid" || true
done

[[ ! -f "$log_dir/failed.txt" ]] || die "Mode GRID : dalles en échec : $(tr '\n' ' ' < "$log_dir/failed.txt") (logs dans $log_dir)."
echo "[TERMINE] $(ls "$splat_dir" | wc -l) dalle(s) exportée(s) dans $splat_dir"
