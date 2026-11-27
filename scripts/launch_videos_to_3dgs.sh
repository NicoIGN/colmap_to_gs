#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "${1:-}" == --help || "${1:-}" == -h || $# -eq 0 ]]; then
  echo "Usage : bash scripts/launch_videos_to_3dgs.sh CONFIG [--prepare-only] [--wait]"
  echo "Configuration : VIDEO, NUM_FRAMES, SFM_IMAGES_DIR et paramètres images/Slurm."
  [[ $# -gt 0 ]] && exit 0 || exit 1
fi
config="$(realpath -e -- "$1")"; shift
source "$config"
: "${VIDEO:?Définir VIDEO dans la configuration}"
: "${NUM_FRAMES:?Définir NUM_FRAMES dans la configuration}"
: "${SFM_IMAGES_DIR:?Définir SFM_IMAGES_DIR dans la configuration}"
[[ -f "$VIDEO" && "$VIDEO" == /* ]] || { echo "[ERREUR] VIDEO doit être un fichier absolu existant." >&2; exit 1; }
[[ "$NUM_FRAMES" =~ ^[1-9][0-9]*$ ]] || { echo "[ERREUR] NUM_FRAMES doit être positif." >&2; exit 1; }
[[ "$SFM_IMAGES_DIR" == /* ]] || { echo "[ERREUR] SFM_IMAGES_DIR doit être absolu." >&2; exit 1; }
for argument in "$@"; do
  case "$argument" in
    --prepare-only|--wait) ;;
    *) echo "[ERREUR] Option inconnue : $argument" >&2; exit 1 ;;
  esac
done
command -v sbatch >/dev/null || { echo "[ERREUR] sbatch introuvable." >&2; exit 1; }
command -v scancel >/dev/null || { echo "[ERREUR] scancel introuvable." >&2; exit 1; }
mkdir -p "$SFM_IMAGES_DIR"
source "$repo_root/tools/libs/common.sh"
load_config "$config" "$SFM_IMAGES_DIR"
mkdir -p "$OUTPUT_DIR/logs"
source "$repo_root/tools/libs/scripts/pipeline_slurm.sh"
PIPELINE_SUBMITTED_JOBS=()
args=(--parsable --wait --nodes=1 --ntasks=1 --gres=none --export=ALL
      "--partition=$SLURM_PARTITION" "--cpus-per-task=$NUM_CPUS"
      "--mem=$SLURM_MEMORY" "--time=$SLURM_TIME_LIMIT" "--chdir=$PWD"
      --job-name=video-frames "--output=$OUTPUT_DIR/logs/video-frames-%j.out"
      "--error=$OUTPUT_DIR/logs/video-frames-%j.err")
[[ -z "${SLURM_ACCOUNT:-}" ]] || args+=("--account=$SLURM_ACCOUNT")
[[ -z "${SLURM_QOS:-}" ]] || args+=("--qos=$SLURM_QOS")
echo "[vidéo] Extraction sur Slurm ; attente de sa réussite avant soumission du pipeline images."
pipeline_submit_job video-frames "${args[@]}" "$repo_root/tools/prepare/extract_video_frames.slurm" \
  "$repo_root" "$config" "$VIDEO" "$SFM_IMAGES_DIR"
exec bash "$repo_root/scripts/launch_images_to_3dgs.sh" "$config" --images "$SFM_IMAGES_DIR" "$@"
