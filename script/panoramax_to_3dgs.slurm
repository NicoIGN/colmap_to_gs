#!/usr/bin/env bash
#SBATCH --job-name=panoramax-to-3dgs
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gres=gpu:4
#SBATCH --cpus-per-task=32
#SBATCH --mem=128G
#SBATCH --time=48:00:00
#SBATCH --output=panoramax-to-3dgs-%j.out
#SBATCH --error=panoramax-to-3dgs-%j.err
# Pipeline Panoramax -> 3DGS complet dans une seule allocation Slurm.
# Usage (depuis la racine du dépôt) :
#   sbatch [-p PARTITION] [options] script/panoramax_to_3dgs.slurm CONFIG
# Les options passées à sbatch remplacent les directives ci-dessus.
# Les blocs sont entraînés en parallèle, un GPU par bloc : --gres=gpu:N fixe le
# parallélisme ; CPU et RAM de l'allocation sont répartis entre les blocs actifs.
set -euo pipefail

die() { printf '[ERREUR] %s\n' "$*" >&2; exit 1; }
[[ $# -eq 1 ]] || die "Usage : sbatch script/panoramax_to_3dgs.slurm CONFIG"
: "${SLURM_JOB_ID:?Ce script doit être soumis avec sbatch}"

# sbatch copies the script to its spool directory: locate the repository.
repo_root="${COLMAP_TO_GS_ROOT:-}"
if [[ -z "$repo_root" ]]; then
  for candidate in "${SLURM_SUBMIT_DIR:-}" "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"; do
    if [[ -n "$candidate" && -f "$candidate/script/panoramax_to_3dgs.sh" ]]; then
      repo_root="$candidate"; break
    fi
  done
fi
[[ -n "$repo_root" && -f "$repo_root/script/panoramax_to_3dgs.sh" ]] ||
  die "Dépôt introuvable : soumettre depuis la racine du dépôt ou définir COLMAP_TO_GS_ROOT."
config="$1"
[[ "$config" == /* ]] || config="${SLURM_SUBMIT_DIR:-$PWD}/$config"

[[ -f "$config" ]] || die "Configuration inaccessible sur le nœud : $config"

read -r num_blocks output_dir < <(
  source "$config"
  printf '%s %s\n' "${SPLIT_NUM_BLOCKS:-}" "${OUTPUT_DIR:-}"
) || die "Configuration invalide : $config"
[[ "${num_blocks:-}" =~ ^[1-9][0-9]*$ ]] || die "SPLIT_NUM_BLOCKS doit être un entier positif."
[[ -n "${output_dir:-}" ]] || die "OUTPUT_DIR requis."

source "$repo_root/tools/libs/scripts/pipeline_sbatch.sh"
pipeline_sbatch_run "$repo_root/tools/panoramax/launch.slurm" "$output_dir" "$num_blocks" \
  -- "$repo_root" "$config" --
