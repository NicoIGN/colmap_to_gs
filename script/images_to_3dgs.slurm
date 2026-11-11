#!/usr/bin/env bash
#SBATCH --job-name=images-to-3dgs
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gres=gpu:4
#SBATCH --cpus-per-task=32
#SBATCH --mem=128G
#SBATCH --time=48:00:00
#SBATCH --output=images-to-3dgs-%j.out
#SBATCH --error=images-to-3dgs-%j.err
# Pipeline images -> 3DGS complet dans une seule allocation Slurm.
# Usage (depuis la racine du dépôt) :
#   sbatch [-p PARTITION] [options] script/images_to_3dgs.slurm CONFIG [--images DIR]
# Les options passées à sbatch remplacent les directives ci-dessus.
# Les blocs sont entraînés en parallèle, un GPU par bloc : --gres=gpu:N fixe le
# parallélisme ; CPU et RAM de l'allocation sont répartis entre les blocs actifs.
set -euo pipefail

die() { printf '[ERREUR] %s\n' "$*" >&2; exit 1; }
usage="sbatch script/images_to_3dgs.slurm CONFIG [--images DIR]"
[[ $# -eq 1 || ($# -eq 3 && "$2" == --images && -n "$3") ]] || die "Usage : $usage"
: "${SLURM_JOB_ID:?Ce script doit être soumis avec sbatch}"

# sbatch copies the script to its spool directory: locate the repository.
repo_root="${COLMAP_TO_GS_ROOT:-}"
if [[ -z "$repo_root" ]]; then
  for candidate in "${SLURM_SUBMIT_DIR:-}" "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"; do
    if [[ -n "$candidate" && -f "$candidate/script/images_to_3dgs.sh" ]]; then
      repo_root="$candidate"; break
    fi
  done
fi
[[ -n "$repo_root" && -f "$repo_root/script/images_to_3dgs.sh" ]] ||
  die "Dépôt introuvable : soumettre depuis la racine du dépôt ou définir COLMAP_TO_GS_ROOT."
config="$1"
[[ "$config" == /* ]] || config="${SLURM_SUBMIT_DIR:-$PWD}/$config"
[[ -f "$config" ]] || die "Configuration inaccessible sur le nœud : $config"
images="${3:-}"
[[ -z "$images" || "$images" == /* ]] || images="${SLURM_SUBMIT_DIR:-$PWD}/$images"


# Read the effective defaults without polluting the job environment.
read -r num_blocks output_dir < <(
  source "$repo_root/tools/images_to_splat/common.sh"
  load_config "$config" "$images" >&2
  printf '%s %s\n' "$SPLIT_NUM_BLOCKS" "$OUTPUT_DIR"
) || die "Configuration invalide : $config"
[[ -n "${output_dir:-}" && "${num_blocks:-}" =~ ^[1-9][0-9]*$ ]] || die "Configuration invalide : $config"

source "$repo_root/tools/libs/scripts/pipeline_sbatch.sh"
pipeline_sbatch_run "$repo_root/tools/images_to_splat/launch.slurm" "$output_dir" "$num_blocks" \
  -- "$repo_root" "$config" -- "$images"
