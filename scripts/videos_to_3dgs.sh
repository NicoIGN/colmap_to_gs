#!/usr/bin/env bash
set -Eeuo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
  echo "Usage : bash scripts/videos_to_3dgs.sh CONFIG --video VIDEO [--video VIDEO ...] --num-frames N [--width W --height H] [--frames-dir DIR] [--phase all|prepare|mask-setup|mask-sfm|mask-train|train-block|merge] [--block INDEX]"
}
die() { printf '[ERREUR] %s\n' "$*" >&2; exit 1; }
# realpath -e/-m n'existe pas sur macOS : Python est portable.
resolve_path() { python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$1"; }
[[ "${1:-}" != --help && "${1:-}" != -h ]] || { usage; exit 0; }
[[ $# -ge 1 ]] || { usage >&2; exit 1; }
config="$1"; shift
videos=(); num_frames=""; width=0; height=0; frames_dir=""; pipeline_args=()
while (($#)); do
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

extract_args=(--out "$frames_dir" --num-frames "$num_frames" --reset --video "${videos[@]}")
if ((width > 0)); then extract_args+=(--width "$width" --height "$height"); fi

echo "[vidéos] extraction de $num_frames image(s) par vidéo dans $frames_dir"
"$python_bin" "$repo_root/tools/preprocess/extract_sharp_frames.py" "${extract_args[@]}"

echo "[3dgs] lancement du pipeline images_to_3dgs"
exec bash "$repo_root/scripts/images_to_3dgs.sh" "$config" --images "$frames_dir" "${pipeline_args[@]}"
