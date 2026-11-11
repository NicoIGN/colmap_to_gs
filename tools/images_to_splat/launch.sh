#!/usr/bin/env bash
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/../.." && pwd)"
source "$repo_root/tools/libs/scripts/pipeline_slurm.sh"
usage() {
  echo "Usage : bash tools/images_to_splat/launch.sh CONFIG [--images DIR] [--wait]"
}
[[ "${1:-}" != --help && "${1:-}" != -h ]] || { usage; exit 0; }
[[ $# -ge 1 ]] || { usage >&2; exit 1; }
config="$(realpath -e -- "$1")"; shift
images=""; wait_args=()
while (($#)); do
  case "$1" in
    --images)
      [[ $# -ge 2 && -n "$2" ]] || { usage >&2; exit 1; }
      images="$2"; shift 2 ;;
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
submit prepare --job-name=images-prepare "--gres=$gres" \
  "--output=$OUTPUT_DIR/logs/prepare-%j.out" "--error=$OUTPUT_DIR/logs/prepare-%j.err"
prepare_job="$job"
submit train-block --job-name=images-block "--gres=$gres" \
  "--array=0-$((SPLIT_NUM_BLOCKS - 1))%$SLURM_BLOCK_PARALLELISM" \
  "--dependency=afterok:$prepare_job" --kill-on-invalid-dep=yes \
  "--output=$OUTPUT_DIR/logs/block-%A_%a.out" "--error=$OUTPUT_DIR/logs/block-%A_%a.err"
blocks_job="$job"
submit merge --job-name=images-merge --gres=none \
  "--dependency=afterok:$blocks_job" --kill-on-invalid-dep=yes \
  "--output=$OUTPUT_DIR/logs/merge-%j.out" "--error=$OUTPUT_DIR/logs/merge-%j.err" \
  "${wait_args[@]}"
echo "[slurm] Suivi : squeue -j $prepare_job,$blocks_job,$job"
echo "[slurm] Annulation : scancel $prepare_job $blocks_job $job"
if ((${#wait_args[@]})); then
  echo "[slurm] Pipeline terminé."
else
  echo "[slurm] Jobs soumis ; calcul en attente ou en cours."
fi
