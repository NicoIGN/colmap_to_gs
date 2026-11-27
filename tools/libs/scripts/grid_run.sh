#!/usr/bin/env bash
# Mode GRID des lanceurs launch_images_to_3dgs.sh et launch_panoramax_to_3dgs.sh (--grid).
# Sourcé, pas exécuté. Local par défaut ; --slurm soumet le même lanceur (--grid) en job Slurm.
#
# Chaque dalle est une exécution indépendante du pipeline (images ou Panoramax), calculée avec
# une marge puis clippée à l'emprise de la dalle. Sorties : GRID_OUTPUT_DIR/splats/<dalle>.splat,
# manifest.json, logs/grid.log (progression) et work/<dalle>/tile.log (log par dalle).

source "$(dirname -- "${BASH_SOURCE[0]}")/shell_helpers.sh"
source "$(dirname -- "${BASH_SOURCE[0]}")/pipeline_config.sh"
source "$(dirname -- "${BASH_SOURCE[0]}")/pipeline_slurm.sh"

# grid_run REPO_ROOT SOURCE CONFIG [--plan-only] [--redo] [--slurm]
# SOURCE : panoramax | images.
grid_run() {
  local repo_root="$1" grid_source="$2" config plan_only=false redo=false slurm=false
  shift 2
  [[ $# -ge 1 ]] || die "CONFIG requis avec --grid."
  config="$(resolve_path "$1")"
  shift
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --plan-only) plan_only=true ;;
      --redo) redo=true ;;
      --slurm) slurm=true ;;
      *) die "Option inconnue avec --grid : $1" ;;
    esac
    shift
  done
  [[ -f "$config" ]] || die "Configuration introuvable : $config"

  load_pipeline_defaults "$repo_root" libs
  source "$config"
  [[ "$SPLIT_NUM_BLOCKS" == 1 ]] || die "Mode GRID : SPLIT_NUM_BLOCKS doit valoir 1 (actuel : $SPLIT_NUM_BLOCKS)."
  is_positive_integer "$NUM_CPUS" || die "NUM_CPUS doit être un entier >= 1."
  is_positive_integer "$NUM_GPUS" || die "NUM_GPUS doit être un entier >= 1."
  (( NUM_CPUS * NUM_GPUS < 64 )) || die "Mode GRID : NUM_CPUS x NUM_GPUS doit être < 64 (actuel : $((NUM_CPUS * NUM_GPUS)))."
  case "$grid_source" in
    panoramax)
      [[ -z "${GRID_POSITIONS:-}" ]] || die "Mode GRID panoramax : les positions viennent de l'EXIF ; GRID_POSITIONS n'est pas utilisé."
      [[ -z "${SFM_SP_METRIC_POSITIONS:-}" ]] || die "Mode GRID panoramax : SFM_SP_METRIC_POSITIONS global n'est pas compatible avec les dalles ; les positions viennent de l'EXIF."
      ;;
    images) [[ -d "${SFM_IMAGES_DIR:-}" ]] || die "Mode GRID images : SFM_IMAGES_DIR doit être un dossier existant."
      [[ -z "${GRID_POSITIONS:-}" || -f "$GRID_POSITIONS" ]] || die "GRID_POSITIONS doit être un fichier existant." ;;
    *) die "Source GRID inconnue : $grid_source" ;;
  esac
  [[ -n "${GRID_BBOX:-}" ]] || die "GRID_BBOX est requis (xmin,ymin,xmax,ymax)."
  is_positive_integer "$GRID_CRS" || die "GRID_CRS doit être un code EPSG numérique."
  [[ -n "${OUTPUT_DIR:-}" ]] || die "OUTPUT_DIR est requis."
  if [[ "$slurm" == true ]]; then
    [[ -n "${SLURM_PARTITION:-}" ]] || die "--slurm : SLURM_PARTITION est vide ; le renseigner dans la configuration."
    [[ -n "${SLURM_MEMORY:-}" ]] || die "--slurm : SLURM_MEMORY est vide ; le renseigner dans la configuration."
    [[ -n "${SLURM_TIME_LIMIT:-}" ]] || die "--slurm : SLURM_TIME_LIMIT est vide ; le renseigner dans la configuration."
  fi

  local grid_py="$repo_root/tools/grid/grid_tiles.py"
  mkdir -p "$OUTPUT_DIR"
  OUTPUT_DIR="$(cd -- "$OUTPUT_DIR" && pwd)"
  GRID_OUTPUT_DIR="${GRID_OUTPUT_DIR:-$OUTPUT_DIR/grid}"
  mkdir -p "$GRID_OUTPUT_DIR"
  GRID_OUTPUT_DIR="$(cd -- "$GRID_OUTPUT_DIR" && pwd)"
  local work_dir="$GRID_OUTPUT_DIR/work" log_dir="$GRID_OUTPUT_DIR/logs"
  local splat_dir="$GRID_OUTPUT_DIR/splats" manifest="$GRID_OUTPUT_DIR/manifest.json"
  mkdir -p "$work_dir" "$log_dir" "$splat_dir"

  python3 "$grid_py" plan --bbox "$GRID_BBOX" --epsg "$GRID_CRS" --tile-size "$GRID_TILE_SIZE" \
    --overlap "$GRID_OVERLAP" --margin "$GRID_MARGIN" --output "$manifest"
  local tile_lines=()
  mapfile -t tile_lines < <(python3 "$grid_py" tiles --manifest "$manifest")
  echo "[GRID] ${#tile_lines[@]} dalle(s), marge ${GRID_MARGIN} m, recouvrement ${GRID_OVERLAP} m, $NUM_GPUS GPU(s), $NUM_CPUS CPU(s) par dalle."

  if [[ "$plan_only" == true ]]; then
    printf '%s\n' "${tile_lines[@]}"
    return 0
  fi

  if [[ "$slurm" == true ]]; then
    grid_submit "$repo_root" "$grid_source" "$config" "$log_dir" "$redo" "${#tile_lines[@]}"
    return 0
  fi

  if [[ "$grid_source" == images ]]; then
    python3 "$grid_py" select-images --manifest "$manifest" --images "$SFM_IMAGES_DIR" --output-dir "$work_dir" \
      ${GRID_POSITIONS:+--positions "$GRID_POSITIONS"} ${SFM_SP_METRIC_POSITIONS:+--global-metric "$SFM_SP_METRIC_POSITIONS"}
  fi

  local redo_args=()
  [[ "$redo" != true ]] || redo_args=(--redo)

  grid_run_tile() {
    local id="$1" x0="$2" y0="$3" x1="$4" y1="$5" cx0="$6" cy0="$7" cx1="$8" cy1="$9"
    local tile_dir="$work_dir/$id" run_dir splat_out tile_conf
    run_dir="$tile_dir/run"
    splat_out="$splat_dir/$id.splat"
    tile_conf="$tile_dir/tile.conf"

    if [[ -f "$splat_out" && "$redo" != true ]]; then
      echo "[SKIP] $id : déjà exporté"
      return 0
    fi
    if [[ "$grid_source" == images && -z "$(ls -A "$tile_dir/images" 2>/dev/null)" ]]; then
      echo "[WARN] $id : aucune image dans l'emprise de calcul, dalle ignorée"
      return 0
    fi

    mkdir -p "$tile_dir"
    # Fichier métrique propre à la dalle (images seulement) : utilisé par Spirula et par le géoréférencement.
    local metric_line=""
    if [[ "$grid_source" == images && -f "$tile_dir/metric_positions.txt" ]]; then
      metric_line="SFM_SP_METRIC_POSITIONS=\"$tile_dir/metric_positions.txt\""
    fi
    cat > "$tile_conf" <<EOF
# Généré par grid_run.sh : configuration utilisateur puis surcharges de la dalle.
source "$config"
OUTPUT_DIR="$run_dir"
EXPORT_SPLAT_NAME="$id.splat"
SPLIT_NUM_BLOCKS=1
NUM_GPUS=1
PANORAMAX_BBOX="$cx0,$cy0,$cx1,$cy1"
PANORAMAX_BBOX_EPSG="$GRID_CRS"
$metric_line
EOF

    if [[ "$grid_source" == panoramax ]]; then
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
  local pids=() worker index id x0 y0 x1 y1 cx0 cy0 cx1 cy1
  for worker in $(seq 0 $((NUM_GPUS - 1))); do
    (
      export CUDA_VISIBLE_DEVICES="$worker"
      for index in "${!tile_lines[@]}"; do
        (( index % NUM_GPUS == worker )) || continue
        IFS=$'\t' read -r id x0 y0 x1 y1 cx0 cy0 cx1 cy1 <<< "${tile_lines[$index]}"
        mkdir -p "$work_dir/$id"
        echo "[START] $id (GPU $worker) : log $work_dir/$id/tile.log" | tee -a "$log_dir/grid.log"
        if grid_run_tile "$id" "$x0" "$y0" "$x1" "$y1" "$cx0" "$cy0" "$cx1" "$cy1" > "$work_dir/$id/tile.log" 2>&1; then
          echo "[END] $id : OK" | tee -a "$log_dir/grid.log"
        else
          echo "$id" >> "$log_dir/failed.txt"
          echo "[END] $id : ÉCHEC, voir $work_dir/$id/tile.log" | tee -a "$log_dir/grid.log" >&2
        fi
      done
    ) &
    pids+=("$!")
  done
  for pid in "${pids[@]}"; do
    wait "$pid" || true
  done

  [[ ! -f "$log_dir/failed.txt" ]] || die "Mode GRID : dalles en échec : $(tr '\n' ' ' < "$log_dir/failed.txt") (logs : $log_dir/grid.log et $work_dir/<dalle>/tile.log)."
  echo "[TERMINE] $(ls "$splat_dir" | wc -l) dalle(s) exportée(s) dans $splat_dir"
}

# Job Slurm : relance le lanceur --grid en local dans l'allocation (un GPU par worker).
grid_submit() {
  local repo_root="$1" grid_source="$2" config="$3" log_dir="$4" redo="$5" tiles="$6"
  local launcher="$repo_root/scripts/launch_${grid_source}_to_3dgs.sh" gres="gpu:$NUM_GPUS" cmd
  command -v sbatch >/dev/null || die "sbatch introuvable."
  [[ -z "${SLURM_GPU_TYPE:-}" ]] || gres="gpu:$SLURM_GPU_TYPE:$NUM_GPUS"

  printf -v cmd 'export OMP_NUM_THREADS=%q PYTHONUNBUFFERED=1; ' "$NUM_CPUS"
  [[ -z "${SLURM_ENV_SETUP:-}" ]] || printf -v cmd '%s. %q; ' "$cmd" "$SLURM_ENV_SETUP"
  printf -v cmd '%sbash %q %q --grid' "$cmd" "$launcher" "$config"
  [[ "$redo" != true ]] || cmd+=" --redo"

  local sbatch_args=(--parsable --nodes=1 --ntasks=1 --export=ALL "--job-name=grid-$grid_source"
    "--partition=$SLURM_PARTITION" "--cpus-per-task=$((NUM_CPUS * NUM_GPUS))" "--gres=$gres"
    "--mem=$SLURM_MEMORY" "--time=$SLURM_TIME_LIMIT" "--chdir=$PWD"
    "--output=$log_dir/slurm-%j.out" "--error=$log_dir/slurm-%j.out")
  [[ -z "${SLURM_ACCOUNT:-}" ]] || sbatch_args+=("--account=$SLURM_ACCOUNT")
  [[ -z "${SLURM_QOS:-}" ]] || sbatch_args+=("--qos=$SLURM_QOS")

  PIPELINE_SUBMITTED_JOBS=()
  pipeline_submit_job "grid-$grid_source" "${sbatch_args[@]}" "--wrap=$cmd" || die "Soumission GRID échouée."
  echo "[slurm] GRID $grid_source : job $PIPELINE_JOB_ID, $tiles dalle(s)."
  echo "[slurm] Progression par dalle : $log_dir/grid.log ; logs dalle : $(dirname -- "$log_dir")/work/<dalle>/tile.log"
  echo "[slurm] Suivi : squeue -j $PIPELINE_JOB_ID ; annulation : scancel $PIPELINE_JOB_ID"
}
