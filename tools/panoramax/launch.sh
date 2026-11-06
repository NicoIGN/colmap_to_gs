#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/../.." && pwd)"

die() {
  printf '[ERREUR] %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage:
  bash tools/panoramax/launch.sh /path/to/config.sh [--wait]

Submit the complete Panoramax pipeline to one Slurm GPU job.
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

: "${OUTPUT_DIR:?OUTPUT_DIR doit être défini dans la configuration}"
: "${BBOX_LAMBERT93:?BBOX_LAMBERT93 doit être défini dans la configuration}"
: "${NUM_BLOCKS:?NUM_BLOCKS doit être défini dans la configuration}"
: "${NUM_ITER:?NUM_ITER doit être défini dans la configuration}"
: "${CAP_MAX:?CAP_MAX doit être défini dans la configuration}"
: "${TRAIN_PRESET:?TRAIN_PRESET doit être défini dans la configuration}"
SLURM_PARTITION="${SLURM_PARTITION:-jean-zellou}"
SLURM_CPUS_PER_TASK="${SLURM_CPUS_PER_TASK:-16}"
SLURM_MEMORY="${SLURM_MEMORY:-64G}"
SLURM_TIME_LIMIT="${SLURM_TIME_LIMIT:-48:00:00}"
SLURM_GPU_TYPE="${SLURM_GPU_TYPE:-}"
SLURM_ACCOUNT="${SLURM_ACCOUNT:-}"
SLURM_QOS="${SLURM_QOS:-}"
[[ "$SLURM_CPUS_PER_TASK" =~ ^[1-9][0-9]*$ ]] ||
  die "SLURM_CPUS_PER_TASK doit être un entier positif."
[[ -z "$SLURM_GPU_TYPE" || "$SLURM_GPU_TYPE" =~ ^[a-zA-Z0-9_-]+$ ]] ||
  die "SLURM_GPU_TYPE doit être un type GPU Slurm, sans séparateur ':'."
command -v sbatch >/dev/null 2>&1 || die "sbatch introuvable ; lancer depuis le cluster."
[[ -f "$script_dir/launch.slurm" ]] || die "launch.slurm introuvable."
[[ -f "$script_dir/run_pipeline.sh" ]] || die "run_pipeline.sh introuvable."

mkdir -p "$OUTPUT_DIR/logs"
log_dir="$(cd -- "$OUTPUT_DIR/logs" && pwd)"
exec > >(tee -a "$log_dir/submit.log") 2>&1
gres="gpu:1"
[[ -z "$SLURM_GPU_TYPE" ]] || gres="gpu:$SLURM_GPU_TYPE:1"
sbatch_args=(
  --parsable
  --job-name=panoramax
  --nodes=1
  --ntasks=1
  "--partition=$SLURM_PARTITION"
  "--cpus-per-task=$SLURM_CPUS_PER_TASK"
  "--mem=$SLURM_MEMORY"
  "--time=$SLURM_TIME_LIMIT"
  "--gres=$gres"
  "--chdir=$submit_dir"
  --export=ALL
  "--output=$log_dir/panoramax-%j.out"
  "--error=$log_dir/panoramax-%j.err"
)
[[ -z "$SLURM_ACCOUNT" ]] || sbatch_args+=("--account=$SLURM_ACCOUNT")
[[ -z "$SLURM_QOS" ]] || sbatch_args+=("--qos=$SLURM_QOS")
[[ "${2:-}" != --wait ]] || sbatch_args+=(--wait)

printf '[slurm] Configuration : %s\n' "$config_path"
printf '[slurm] Partition=%s CPU=%s RAM=%s GPU=%s durée=%s\n' \
  "$SLURM_PARTITION" "$SLURM_CPUS_PER_TASK" "$SLURM_MEMORY" "$gres" "$SLURM_TIME_LIMIT"
status=0
submission="$(sbatch "${sbatch_args[@]}" "$script_dir/launch.slurm" \
  "$repo_root" "$config_path")" || status=$?
job_id="${submission%%;*}"
if [[ "$job_id" =~ ^[0-9]+$ ]]; then
  printf '[slurm] Job : %s\n' "$submission"
  printf '[slurm] stdout : %s/panoramax-%s.out\n' "$log_dir" "$job_id"
  printf '[slurm] stderr : %s/panoramax-%s.err\n' "$log_dir" "$job_id"
  printf '[slurm] Suivi : squeue -j %s\n' "$job_id"
  printf '[slurm] Bilan : sacct -j %s --format=JobID,State,ExitCode\n' "$job_id"
elif [[ "$status" -eq 0 ]]; then
  die "Réponse sbatch invalide : $submission"
fi
if [[ "$status" -ne 0 ]]; then
  printf '[ERREUR] Échec Slurm (soumission ou exécution), code %s.\n' "$status" >&2
  exit "$status"
fi
if [[ "${2:-}" == --wait ]]; then
  printf '[slurm] Job terminé avec succès.\n'
else
  printf '[slurm] Job soumis ; calcul en attente ou en cours.\n'
fi
