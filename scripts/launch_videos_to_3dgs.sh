#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "${1:-}" == --help || "${1:-}" == -h || $# -eq 0 ]]; then
  echo "Usage : bash scripts/launch_videos_to_3dgs.sh CONFIG [--slurm] [--width W --height H] [--frames-dir DIR] [--prepare-only] [--wait]"
  echo "Configuration : VIDEO, NUM_FRAMES, SFM_IMAGES_DIR et paramètres images/Slurm."
  echo "  --width W --height H  redimensionne les frames extraites (0 ou absent = taille source)."
  echo "  --frames-dir DIR      dossier des frames extraites (défaut : SFM_IMAGES_DIR)."
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
slurm=false
rest=()
width=0
height=0
frames_dir=""
while (($#)); do
  case "$1" in
    --slurm) slurm=true ;;
    --prepare-only|--wait) rest+=("$1") ;;
    --width|--height|--frames-dir)
      [[ $# -ge 2 && -n "$2" ]] || { echo "[ERREUR] $1 attend une valeur." >&2; exit 1; }
      case "$1" in
        --width) width="$2" ;;
        --height) height="$2" ;;
        --frames-dir) frames_dir="$2" ;;
      esac
      shift
      ;;
    *) echo "[ERREUR] Option inconnue : $1" >&2; exit 1 ;;
  esac
  shift
done
[[ "$width" =~ ^[0-9]+$ && "$height" =~ ^[0-9]+$ ]] || { echo "[ERREUR] --width et --height doivent être des entiers." >&2; exit 1; }
[[ $((width > 0)) == "$((height > 0))" ]] || { echo "[ERREUR] --width et --height doivent être fournis ensemble." >&2; exit 1; }
if [[ -n "$frames_dir" ]]; then
  [[ "$frames_dir" == /* ]] || { echo "[ERREUR] --frames-dir doit être absolu." >&2; exit 1; }
else
  frames_dir="$SFM_IMAGES_DIR"
fi
if [[ "$slurm" != true ]]; then
  for argument in ${rest[@]+"${rest[@]}"}; do
    echo "[ERREUR] $argument s'utilise avec --slurm." >&2; exit 1
  done
  local_args=(--video "$VIDEO" --num-frames "$NUM_FRAMES" --frames-dir "$frames_dir")
  if ((width > 0)); then local_args+=(--width "$width" --height "$height"); fi
  exec bash "$repo_root/scripts/local/videos_to_3dgs.sh" "$config" "${local_args[@]}"
fi
command -v sbatch >/dev/null || { echo "[ERREUR] sbatch introuvable." >&2; exit 1; }
command -v scancel >/dev/null || { echo "[ERREUR] scancel introuvable." >&2; exit 1; }
mkdir -p "$frames_dir"
source "$repo_root/tools/libs/common.sh"
load_config "$config" "$frames_dir"
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
export VIDEO_WIDTH="$width" VIDEO_HEIGHT="$height"
pipeline_submit_job video-frames "${args[@]}" "$repo_root/tools/prepare/extract_video_frames.slurm" \
  "$repo_root" "$config" "$VIDEO" "$frames_dir"
exec bash "$repo_root/scripts/launch_images_to_3dgs.sh" "$config" --images "$frames_dir" --slurm ${rest[@]+"${rest[@]}"}
