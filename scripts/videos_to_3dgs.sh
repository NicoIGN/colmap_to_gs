#!/usr/bin/env bash
set -Eeuo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source "$repo_root/tools/libs/scripts/shell_helpers.sh"

usage() {
  echo "Usage : bash scripts/videos_to_3dgs.sh CONFIG --video VIDEO [--video VIDEO ...] --num-frames N [--width W --height H] [--frames-dir DIR] [--phase all|prepare|mask-setup|mask-sfm|mask-train|train-block|merge] [--block INDEX] [--redo]"
}
[[ "${1:-}" != --help && "${1:-}" != -h ]] || { usage; exit 0; }
[[ $# -ge 1 ]] || { usage >&2; exit 1; }
config="$1"; shift
videos=(); num_frames=""; width=0; height=0; frames_dir=""; redo=false; pipeline_args=()
while (($#)); do
  if [[ "$1" == --redo ]]; then
    redo=true; shift; continue
  fi
  [[ $# -ge 2 && -n "$2" ]] || { usage >&2; exit 1; }
  case "$1" in
    --video) videos+=("$2") ;;
    --num-frames) num_frames="$2" ;;
    --width) width="$2" ;;
    --height) height="$2" ;;
    --frames-dir) frames_dir="$2" ;;
    --phase|--block) pipeline_args+=("$1" "$2") ;;
    *) usage >&2; exit 1 ;;
  esac
  shift 2
done
[[ "$redo" != true ]] || pipeline_args+=(--redo)

[[ -f "$config" ]] || die "Configuration introuvable : $config"
((${#videos[@]} > 0)) || die "Indiquer au moins une vidéo avec --video."
for video in "${videos[@]}"; do
  [[ -f "$video" ]] || die "Vidéo introuvable : $video"
done
[[ "$num_frames" =~ ^[1-9][0-9]*$ ]] || die "--num-frames doit être un entier positif."
[[ "$width" =~ ^[0-9]+$ && "$height" =~ ^[0-9]+$ ]] || die "--width et --height doivent être des entiers."
[[ $((width > 0)) == "$((height > 0))" ]] || die "--width et --height doivent être fournis ensemble."

if [[ -z "$frames_dir" ]]; then
  frames_dir="$(dirname -- "$(resolve_path "${videos[0]}")")/frames_3dgs"
fi
frames_dir="$(resolve_path "$frames_dir")"

python_bin="$(bash -c 'set -a; source "$1" >/dev/null 2>&1; echo "${PYTHON_BIN:-python3}"' _ "$config")"
command -v "$python_bin" >/dev/null || die "Python introuvable : $python_bin"
for module in cv2 numpy tqdm; do
  "$python_bin" -c "import $module" 2>/dev/null ||
    die "Module Python manquant pour l'extraction vidéo : $module (Python : $python_bin). Installer avec : $python_bin -m pip install opencv-python-headless numpy tqdm"
done

mkdir -p "$frames_dir"
echo "[prévalidation] vérification de la configuration et des ressources des masques avant extraction."
bash "$repo_root/scripts/images_to_3dgs.sh" "$config" --images "$frames_dir" --validate-only
report_workspace="$(bash -c 'source "$1" >/dev/null; printf "%s" "$OUTPUT_DIR"' _ "$config")"
mkdir -p "$report_workspace/logs"
frames_lock="$report_workspace/.frames.lock"
frames_done="$report_workspace/.frames_done"
lock_helper="$repo_root/tools/libs/python/pipeline_lock.py"
pipeline_resume="$(bash -c 'source "$1" >/dev/null 2>&1; printf "%s" "${PIPELINE_RESUME:-false}"' _ "$config")"
remove_locks="$(bash -c 'source "$1" >/dev/null 2>&1; printf "%s" "${REMOVE_LOCKS:-false}"' _ "$config")"
"$python_bin" "$lock_helper" "$frames_lock" --pid "$$" --resume "$pipeline_resume" --ignore "$remove_locks" --workspace "$report_workspace"
source "$repo_root/tools/libs/scripts/pipeline_report.sh"
pipeline_report_start "$report_workspace" "videos_to_3dgs"
video_exit() {
  local status=$?
  trap - EXIT
  pipeline_report_finish "$status" || { [[ "$status" != 0 ]] || status=1; }
  "$python_bin" "$lock_helper" "$frames_lock" --pid "$$" --release ||
    echo "[AVERTISSEMENT] Verrou conservé : $frames_lock" >&2
  exit "$status"
}
trap video_exit EXIT
pipeline_report_phase "extraction des images vidéo"

extract_args=(--out "$frames_dir" --num-frames "$num_frames" --reset --video "${videos[@]}")
if ((width > 0)); then extract_args+=(--width "$width" --height "$height"); fi

# Une vidéo : frames à la racine ; plusieurs vidéos : un sous-dossier par vidéo (même règle que l'extracteur).
frames_complete=true
for video in "${videos[@]}"; do
  out_dir="$frames_dir"
  if ((${#videos[@]} > 1)); then
    name="$(basename -- "$video")"
    out_dir="$frames_dir/${name%.*}"
  fi
  shopt -s nullglob
  existing_frames=("$out_dir"/frame_*.jpg)
  shopt -u nullglob
  if ((${#existing_frames[@]} != num_frames)); then
    frames_complete=false
    break
  fi
done

frames_key="$frames_dir|$num_frames"
if [[ "$frames_complete" == true && -f "$frames_done" ]] && grep -qxF -- "$frames_key" "$frames_done"; then
  echo "[vidéos] $num_frames image(s) par vidéo déjà extraites dans $frames_dir : extraction ignorée."
else
  rm -f -- "$frames_done"
  echo "[vidéos] extraction de $num_frames image(s) par vidéo dans $frames_dir"
  "$python_bin" "$repo_root/tools/preprocess/extract_sharp_frames.py" "${extract_args[@]}"
  printf '%s\n' "$frames_key" > "$frames_done"
fi

echo "[3dgs] lancement du pipeline images_to_3dgs"
pipeline_report_phase "pipeline images_to_3dgs"
bash "$repo_root/scripts/images_to_3dgs.sh" "$config" --images "$frames_dir" ${pipeline_args[@]+"${pipeline_args[@]}"}
