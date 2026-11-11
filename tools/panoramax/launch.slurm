#!/usr/bin/env bash
set -euo pipefail

die() {
  printf '[ERREUR] %s\n' "$*" >&2
  exit 1
}

[[ $# -eq 2 || $# -eq 3 ]] ||
  die "Usage : launch.slurm REPO_ROOT CONFIG_SH [prepare|train-block|merge|all] (via launch.sh)"
repo_root="$1"
config_path="$2"
phase="${3:-all}"
case "$phase" in
  all|prepare|merge) ;;
  train-block)
    [[ "${SLURM_ARRAY_TASK_ID:-}" =~ ^(0|[1-9][0-9]*)$ ]] ||
      die "SLURM_ARRAY_TASK_ID requis pour entraîner un bloc." ;;
  *) die "Étape inconnue : $phase" ;;
esac
: "${SLURM_JOB_ID:?Ce script doit être lancé dans une allocation Slurm}"
[[ -f "$config_path" ]] || die "Configuration inaccessible sur le nœud : $config_path"
[[ -f "$repo_root/script/panoramax_to_3dgs.sh" ]] ||
  die "Dépôt inaccessible sur le nœud : $repo_root"
source "$config_path"
source "$repo_root/tools/panoramax/validate_config.sh"
reject_legacy_config || exit 1
: "${OUTPUT_DIR:?OUTPUT_DIR requis}"
export POSTPROCESS_CLEANER="${POSTPROCESS_CLEANER:-contextual}"

# Site-specific modules (Vulkan, drivers, Conda) must be loaded on the node.
if [[ -n "${SLURM_ENV_SETUP:-}" ]]; then
  [[ -f "$SLURM_ENV_SETUP" ]] || die "Script d'environnement absent : $SLURM_ENV_SETUP"
  source "$SLURM_ENV_SETUP"
fi
if [[ -n "${SLURM_CONDA_ENV:-}" ]]; then
  if [[ -z "${SLURM_CONDA_BASE:-}" ]]; then
    command -v conda >/dev/null 2>&1 ||
      die "Conda introuvable ; définir SLURM_CONDA_BASE ou SLURM_ENV_SETUP."
    SLURM_CONDA_BASE="$(conda info --base)"
  fi
  [[ -f "$SLURM_CONDA_BASE/etc/profile.d/conda.sh" ]] ||
    die "Initialisation Conda absente : $SLURM_CONDA_BASE"
  source "$SLURM_CONDA_BASE/etc/profile.d/conda.sh"
  set +u
  conda activate "$SLURM_CONDA_ENV"
  set -u
fi

PYTHON_BIN="${PYTHON_BIN:-python3}"
command -v "$PYTHON_BIN" >/dev/null 2>&1 || die "Python introuvable : $PYTHON_BIN"
command -v srun >/dev/null 2>&1 || die "srun introuvable."
export PYTHONUNBUFFERED=1
export OMP_NUM_THREADS="${PIPELINE_STEP_CPUS:-${SLURM_CPUS_PER_TASK:-1}}"
# Set by script/panoramax_to_3dgs.slurm to bind concurrent block steps to one GPU each.
read -r -a step_opts <<< "${PIPELINE_SRUN_OPTIONS:-}"
runtime_parent="${SLURM_TMPDIR:-${TMPDIR:-/tmp}}"
XDG_RUNTIME_DIR="$(mktemp -d "$runtime_parent/panoramax-${SLURM_JOB_ID}-XXXXXXXX")"
export XDG_RUNTIME_DIR
cleanup() {
  rmdir -- "$XDG_RUNTIME_DIR" ||
    printf '[AVERTISSEMENT] Dossier runtime conservé : %s\n' "$XDG_RUNTIME_DIR" >&2
}
trap cleanup EXIT

printf '[slurm] Job=%s hôte=%s dossier=%s\n' "$SLURM_JOB_ID" "$(hostname)" "$PWD"
printf '[slurm] Python=%s\n' "$(command -v "$PYTHON_BIN")"
"$PYTHON_BIN" -c 'import numpy, scipy, tqdm, pyproj, plyfile'

printf '[slurm] Étape=%s bloc=%s\n' "$phase" "${SLURM_ARRAY_TASK_ID:-sans objet}"
# Each array task inherits its own GPU allocation and device cgroup.
exec_status=0
srun ${step_opts[@]+"${step_opts[@]}"} --unbuffered --export=ALL bash -c '
  set -euo pipefail
  if [[ "$3" != merge || "${POSTPROCESS_CLEANER:-contextual}" == cleangs ]]; then
    command -v nvidia-smi >/dev/null 2>&1 || {
      echo "[ERREUR] nvidia-smi introuvable ; GPU NVIDIA requis." >&2
      exit 1
    }
    nvidia-smi -L
  fi
  args=("$2" "$3")
  [[ "$3" != train-block ]] || args+=("$SLURM_ARRAY_TASK_ID")
  bash "$1/script/panoramax_to_3dgs.sh" "${args[@]}"
' bash "$repo_root" "$config_path" "$phase" || exec_status=$?
if [[ "$exec_status" -ne 0 ]]; then
  printf '[ERREUR] Pipeline GPU en échec (job %s, hôte %s, code %s).\n' \
    "$SLURM_JOB_ID" "$(hostname)" "$exec_status" >&2
  printf '[ERREUR] srun relaie un arrêt du pipeline ou du job ; consulter la cause et l’étape indiquées plus haut.\n' >&2
  log_name="panoramax-$phase-$SLURM_JOB_ID"
  if [[ "$phase" == train-block ]]; then
    log_name="panoramax-block-${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID}"
  fi
  printf '[ERREUR] Étape : %s ; journaux : %s/logs/%s.{out,err}\n' \
    "$phase" "$OUTPUT_DIR" "$log_name" >&2
  exit "$exec_status"
fi
printf '[slurm] Étape %s terminée.\n' "$phase"
