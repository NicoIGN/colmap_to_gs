#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/../.." && pwd)"
source "$repo_root/tools/libs/scripts/pipeline_slurm.sh"

die() {
  printf '[ERREUR] %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage:
  bash tools/panoramax/launch.sh /path/to/config.sh [--wait]

Submit preparation, a GPU job array (one block per task), then merge.
Start from tools/panoramax/config.sh.example (trusted Bash configuration).
--wait waits for completion and returns a failure if the job fails.
Without --wait, success means submission only.
EOF
}

if [[ "${1:-}" == -h || "${1:-}" == --help ]]; then
  usage
  exit 0
fi
[[ $# -ge 1 && $# -le 2 ]] || { usage >&2; exit 1; }
[[ $# -eq 1 || "$2" == --wait ]] || die "Argument inattendu : $2"
[[ -f "$1" ]] || die "Configuration introuvable : $1"
config_path="$(cd -- "$(dirname -- "$1")" && pwd)/$(basename -- "$1")"
submit_dir="$PWD"
source "$config_path"
source "$script_dir/validate_config.sh"
validate_config slurm || exit 1

: "${OUTPUT_DIR:?OUTPUT_DIR doit être défini dans la configuration}"
: "${PANORAMAX_BBOX:?PANORAMAX_BBOX doit être défini dans la configuration}"
: "${SPLIT_NUM_BLOCKS:?SPLIT_NUM_BLOCKS doit être défini dans la configuration}"
: "${TRAIN_NUM_ITER:?TRAIN_NUM_ITER doit être défini dans la configuration}"
: "${TRAIN_CAP_MAX:?TRAIN_CAP_MAX doit être défini dans la configuration}"
: "${TRAIN_PRESET:?TRAIN_PRESET doit être défini dans la configuration}"
SLURM_PARTITION="${SLURM_PARTITION:-jean-zellou}"
SLURM_CPUS_PER_TASK="${SLURM_CPUS_PER_TASK:-16}"
SLURM_MEMORY="${SLURM_MEMORY:-64G}"
SLURM_TIME_LIMIT="${SLURM_TIME_LIMIT:-48:00:00}"
SLURM_GPU_TYPE="${SLURM_GPU_TYPE:-}"
SLURM_ACCOUNT="${SLURM_ACCOUNT:-}"
SLURM_QOS="${SLURM_QOS:-}"
SLURM_BLOCK_PARALLELISM="${SLURM_BLOCK_PARALLELISM:-$SPLIT_NUM_BLOCKS}"
[[ "$SPLIT_NUM_BLOCKS" =~ ^[1-9][0-9]*$ ]] || die "SPLIT_NUM_BLOCKS doit être un entier positif."
[[ "$SLURM_BLOCK_PARALLELISM" =~ ^[1-9][0-9]*$ ]] ||
  die "SLURM_BLOCK_PARALLELISM doit être un entier positif."
[[ "$SLURM_CPUS_PER_TASK" =~ ^[1-9][0-9]*$ ]] ||
  die "SLURM_CPUS_PER_TASK doit être un entier positif."
[[ -z "$SLURM_GPU_TYPE" || "$SLURM_GPU_TYPE" =~ ^[a-zA-Z0-9_-]+$ ]] ||
  die "SLURM_GPU_TYPE doit être un type GPU Slurm, sans séparateur ':'."
command -v sbatch >/dev/null 2>&1 || die "sbatch introuvable ; lancer depuis le cluster."
command -v scancel >/dev/null 2>&1 || die "scancel introuvable."
[[ -f "$script_dir/launch.slurm" ]] || die "launch.slurm introuvable."
[[ -f "$repo_root/script/panoramax_to_3dgs.sh" ]] || die "script/panoramax_to_3dgs.sh introuvable."

mkdir -p "$OUTPUT_DIR/logs"
log_dir="$(cd -- "$OUTPUT_DIR/logs" && pwd)"
exec > >(tee -a "$log_dir/submit.log") 2>&1
gres="gpu:1"
[[ -z "$SLURM_GPU_TYPE" ]] || gres="gpu:$SLURM_GPU_TYPE:1"
sbatch_args=(
  --parsable
  --nodes=1
  --ntasks=1
  "--partition=$SLURM_PARTITION"
  "--cpus-per-task=$SLURM_CPUS_PER_TASK"
  "--mem=$SLURM_MEMORY"
  "--time=$SLURM_TIME_LIMIT"
  "--chdir=$submit_dir"
  --export=ALL
)
[[ -z "$SLURM_ACCOUNT" ]] || sbatch_args+=("--account=$SLURM_ACCOUNT")
[[ -z "$SLURM_QOS" ]] || sbatch_args+=("--qos=$SLURM_QOS")

printf '[slurm] Configuration : %s\n' "$config_path"
printf '[slurm] Partition=%s CPU/job=%s RAM/job=%s GPU/bloc=%s durée/job=%s parallélisme=%s\n' \
  "$SLURM_PARTITION" "$SLURM_CPUS_PER_TASK" "$SLURM_MEMORY" "$gres" \
  "$SLURM_TIME_LIMIT" "$SLURM_BLOCK_PARALLELISM"
PIPELINE_SUBMITTED_JOBS=()
submit_job() {
  local stage="$1"
  shift
  pipeline_submit_job "$stage" "${sbatch_args[@]}" "$@" "$script_dir/launch.slurm" \
    "$repo_root" "$config_path" "$stage"
  job_id="$PIPELINE_JOB_ID"
}

submit_job prepare --job-name=panoramax-prepare "--gres=$gres" \
  "--output=$log_dir/panoramax-prepare-%j.out" \
  "--error=$log_dir/panoramax-prepare-%j.err"
prepare_job="$job_id"
submit_job train-block --job-name=panoramax-block "--gres=$gres" \
  "--array=0-$((SPLIT_NUM_BLOCKS - 1))%$SLURM_BLOCK_PARALLELISM" \
  "--dependency=afterok:$prepare_job" --kill-on-invalid-dep=yes \
  "--output=$log_dir/panoramax-block-%A_%a.out" \
  "--error=$log_dir/panoramax-block-%A_%a.err"
blocks_job="$job_id"
printf '[slurm] Journaux préparation : %s/panoramax-prepare-%s.{out,err}\n' "$log_dir" "$prepare_job"
printf '[slurm] Journaux blocs : %s/panoramax-block-%s_INDICE.{out,err}\n' "$log_dir" "$blocks_job"
merge_args=()
[[ "${2:-}" != --wait ]] || merge_args+=(--wait)
merge_gres=none
[[ "${POSTPROCESS_CLEANER:-contextual}" != cleangs ]] || merge_gres="$gres"
submit_job merge --job-name=panoramax-merge "--gres=$merge_gres" \
  "--dependency=afterok:$blocks_job" --kill-on-invalid-dep=yes \
  "--output=$log_dir/panoramax-merge-%j.out" \
  "--error=$log_dir/panoramax-merge-%j.err" "${merge_args[@]}"
printf '[slurm] Journaux fusion : %s/panoramax-merge-%s.{out,err}\n' "$log_dir" "$job_id"
printf '[slurm] Suivi : squeue -j %s,%s,%s\n' "$prepare_job" "$blocks_job" "$job_id"
printf '[slurm] Bilan : sacct -j %s,%s,%s --format=JobID,State,ExitCode\n' \
  "$prepare_job" "$blocks_job" "$job_id"
printf '[slurm] Annulation : scancel %s %s %s\n' "$prepare_job" "$blocks_job" "$job_id"
if [[ "${2:-}" == --wait ]]; then
  printf '[slurm] Pipeline terminé avec succès (préparation, tous les blocs, fusion).\n'
else
  printf '[slurm] Jobs soumis ; calcul en attente ou en cours.\n'
fi
