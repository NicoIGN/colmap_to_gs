#!/usr/bin/env bash
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/../.." && pwd)"
source "$repo_root/tools/libs/scripts/pipeline_slurm.sh"
usage() {
  echo "Usage : bash tools/images_to_splat/launch.sh CONFIG [--images DIR] [--prepare-only] [--wait]"
}
[[ "${1:-}" != --help && "${1:-}" != -h ]] || { usage; exit 0; }
[[ $# -ge 1 ]] || { usage >&2; exit 1; }
config="$(realpath -e -- "$1")"; shift
images=""; wait_args=(); prepare_only=false
while (($#)); do
  case "$1" in
    --images)
      [[ $# -ge 2 && -n "$2" ]] || { usage >&2; exit 1; }
      images="$2"; shift 2 ;;
    --prepare-only) prepare_only=true; shift ;;
    --wait) wait_args=(--wait); shift ;;
    *) usage >&2; exit 1 ;;
  esac
done
source "$script_dir/common.sh"
load_config "$config" "$images"
command -v sbatch >/dev/null || die "sbatch introuvable."
command -v scancel >/dev/null || die "scancel introuvable."
mkdir -p "$OUTPUT_DIR/logs"
exec > >(tee -a "$OUTPUT_DIR/logs/submit.log") 2>&1
gres="gpu:1"
[[ -z "$SLURM_GPU_TYPE" ]] || gres="gpu:$SLURM_GPU_TYPE:1"
args=(--parsable --nodes=1 --ntasks=1 --export=ALL
      "--partition=$SLURM_PARTITION" "--cpus-per-task=$SLURM_CPUS_PER_TASK"
      "--mem=$SLURM_MEMORY" "--time=$SLURM_TIME_LIMIT" "--chdir=$PWD")
[[ -z "${SLURM_ACCOUNT:-}" ]] || args+=("--account=$SLURM_ACCOUNT")
[[ -z "${SLURM_QOS:-}" ]] || args+=("--qos=$SLURM_QOS")
PIPELINE_SUBMITTED_JOBS=()
submit() {
  local phase="$1"
  shift
  pipeline_submit_job "$phase" "${args[@]}" "$@" "$script_dir/launch.slurm" \
    "$repo_root" "$config" "$phase" "$SFM_IMAGES_DIR"
  job="$PIPELINE_JOB_ID"
}
mask_jobs=()
parallel_masks=false
prepare_wait_args=()
if [[ "$prepare_only" == true ]]; then
  prepare_wait_args=("${wait_args[@]}")
fi
if [[ "$SFM_MASKS" == on && "$TRAIN_MASKS" == on &&
      -z "$SFM_MASKS_DIR" && -z "$TRAIN_MASKS_DIR" ]]; then
  policies_equal="$("$PYTHON_BIN" "$script_dir/prepare_masks.py" --policies-equal)" ||
    die "Impossible de comparer les politiques de masques."
  [[ "$policies_equal" == true || "$policies_equal" == false ]] ||
    die "Résultat invalide de comparaison des politiques de masques."
  [[ "$policies_equal" != false ]] || parallel_masks=true
fi
if [[ "$parallel_masks" == true ]]; then
  submit mask-setup --job-name=images-mask-setup --gres=none \
    "--output=$OUTPUT_DIR/logs/mask-setup-%j.out" \
    "--error=$OUTPUT_DIR/logs/mask-setup-%j.err"
  setup_job="$job"
  for policy in sfm train; do
    submit "mask-$policy" "--job-name=images-mask-$policy" "--gres=$gres" \
      "--dependency=afterok:$setup_job" --kill-on-invalid-dep=yes \
      "--output=$OUTPUT_DIR/logs/mask-$policy-%j.out" \
      "--error=$OUTPUT_DIR/logs/mask-$policy-%j.err"
    mask_jobs+=("$job")
  done
  submit prepare --job-name=images-prepare "--gres=$gres" \
    "--dependency=afterok:${mask_jobs[0]}:${mask_jobs[1]}" --kill-on-invalid-dep=yes \
    "--output=$OUTPUT_DIR/logs/prepare-%j.out" "--error=$OUTPUT_DIR/logs/prepare-%j.err" \
    "${prepare_wait_args[@]}"
  prepare_job="$job"
else
  submit prepare --job-name=images-prepare "--gres=$gres" \
    "--output=$OUTPUT_DIR/logs/prepare-%j.out" "--error=$OUTPUT_DIR/logs/prepare-%j.err" \
    "${prepare_wait_args[@]}"
  prepare_job="$job"
fi
if [[ "$prepare_only" == true ]]; then
  echo "[slurm] Préparation SfM terminée ou soumise : job $prepare_job."
  echo "[slurm] Suivi : squeue -j ${PIPELINE_SUBMITTED_JOBS[*]// /,}"
  echo "[slurm] Annulation : scancel ${PIPELINE_SUBMITTED_JOBS[*]}"
  exit 0
fi
submit train-block --job-name=images-block "--gres=$gres" \
  "--array=0-$((SPLIT_NUM_BLOCKS - 1))%$SLURM_BLOCK_PARALLELISM" \
  "--dependency=afterok:$prepare_job" --kill-on-invalid-dep=yes \
  "--output=$OUTPUT_DIR/logs/block-%A_%a.out" "--error=$OUTPUT_DIR/logs/block-%A_%a.err"
blocks_job="$job"
submit merge --job-name=images-merge --gres=none \
  "--dependency=afterok:$blocks_job" --kill-on-invalid-dep=yes \
  "--output=$OUTPUT_DIR/logs/merge-%j.out" "--error=$OUTPUT_DIR/logs/merge-%j.err" \
  "${wait_args[@]}"
echo "[slurm] Suivi : squeue -j ${PIPELINE_SUBMITTED_JOBS[*]// /,}"
echo "[slurm] Annulation : scancel ${PIPELINE_SUBMITTED_JOBS[*]}"
if ((${#wait_args[@]})); then
  echo "[slurm] Pipeline terminé."
else
  echo "[slurm] Jobs soumis ; calcul en attente ou en cours."
fi
