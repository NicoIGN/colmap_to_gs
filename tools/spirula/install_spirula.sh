#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/../.." && pwd)"
repo_dir="$(dirname -- "$repo_root")/spirula-studio"
repo_url="https://github.com/harry7557558/spirula-studio.git"
cmake_args=()
clean=false
model_url="https://huggingface.co/PABannier/sam3.cpp/resolve/main/sam3-q4_0.ggml"

die() {
  printf 'Erreur : %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage :
  bash install_spirula.sh [options] [-- options CMake]

Installe Spirula Studio (Vulkan/SAM) et le checkpoint SAM 3 GGML.
Un exécutable existant est réutilisé sans mise à jour ni compilation.
  --repo-dir PATH   Sources (défaut : <colmap_to_gs>/../spirula-studio)
  --repo-url URL    Dépôt à cloner (défaut : dépôt officiel)
  --clean           Mettre à jour et recompiler proprement même si l'exécutable existe
  -h, --help        Afficher cette aide

Le modèle est téléchargé dans <repo-dir>/models/sam3-q4_0.ggml (licence Meta).
Vérifier sa licence avant utilisation ; aucune acceptation n'est enregistrée ici.
Avec --clean, un dépôt existant conserve sa branche et son remote de suivi.
La mise à jour est fast-forward uniquement, sans écrasement des modifications.
La compilation exige Git, CMake, Ninja et un compilateur C++ ; curl est requis
uniquement si le modèle n'est pas déjà présent.
Linux nécessite aussi le SDK Vulkan ; macOS utilise MoltenVK via CMake.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-dir|--repo-url)
      [[ $# -ge 2 && -n "$2" && "$2" != --* ]] || die "Valeur manquante pour $1"
      case "$1" in
        --repo-dir) repo_dir="$2" ;;
        --repo-url) repo_url="$2" ;;
      esac
      shift 2
      ;;
    --) shift; cmake_args=("$@"); break ;;
    --clean) clean=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "Argument inconnu : $1" ;;
  esac
done

case "$(uname -s)" in
  Darwin) build_dir=build ;;
  Linux) build_dir=build_vulkan ;;
  *) die "Ce lanceur prend en charge macOS et Linux uniquement" ;;
esac

if [[ ${#cmake_args[@]} -gt 0 ]]; then
  for arg in "${cmake_args[@]}"; do
    case "$arg" in
      -B*|--build*|-DSS_BACKEND*|-DSSPLAT_BACKEND*)
        die "Le dossier de build et le backend Vulkan sont fixés par le lanceur" ;;
    esac
  done
fi

binary="$repo_dir/$build_dir/spirula"
if [[ -f "$binary" && "$clean" == false ]]; then
  [[ -x "$binary" ]] || die "Exécutable existant mais non exécutable : $binary"
  [[ ${#cmake_args[@]} -eq 0 ]] ||
    die "Options CMake ignorées avec un exécutable existant : utiliser --clean pour les appliquer."
  printf '[Spirula] Exécutable existant réutilisé, sans mise à jour ni compilation : %s\n' "$binary"
else
  for tool in git cmake ninja; do
    command -v "$tool" >/dev/null 2>&1 || die "Prérequis manquant : $tool"
  done
  if [[ -e "$repo_dir" ]]; then
    [[ -d "$repo_dir/.git" || -f "$repo_dir/.git" ]] ||
      die "Le dossier existe mais n'est pas un checkout Git : $repo_dir"
    status="$(git -C "$repo_dir" status --porcelain --untracked-files=all -- . ':(top,exclude)models/')" ||
      die "Impossible de vérifier l'état Git de $repo_dir"
    [[ -z "$status" ]] ||
      die "Modifications locales dans $repo_dir : les conserver ou les committer avant la mise à jour"
    printf '[Spirula] Mise à jour : %s\n' "$repo_dir"
    git -C "$repo_dir" pull --ff-only
  else
    printf '[Spirula] Clonage : %s\n' "$repo_dir"
    git clone --recurse-submodules -- "$repo_url" "$repo_dir"
  fi

  repo_dir="$(cd -- "$repo_dir" && pwd)"
  cd -- "$repo_dir"
  git submodule update --init --recursive
  [[ -f build_develop.bash ]] || die "Script officiel build_develop.bash introuvable"
  if [[ "$clean" == true && -f "$build_dir/CMakeCache.txt" ]]; then
    printf '[Spirula] Nettoyage des artefacts de compilation : %s/%s\n' "$PWD" "$build_dir"
    cmake --build "$build_dir" --target clean
  fi

  printf '[Spirula] Compilation Vulkan dans %s/%s\n' "$PWD" "$build_dir"
  # Le script officiel gère la génération, MoltenVK et le parallélisme selon la RAM.
  build_cmd=(bash build_develop.bash -DSS_BACKEND=vulkan -DSS_BUILD_SAM=ON -DCMAKE_BUILD_TYPE=Release)
  if [[ ${#cmake_args[@]} -gt 0 ]]; then
    build_cmd+=("${cmake_args[@]}")
  fi
  "${build_cmd[@]}"
fi

repo_dir="$(cd -- "$repo_dir" && pwd)"
binary="$repo_dir/$build_dir/spirula"
[[ -x "$binary" ]] || die "Compilation terminée mais exécutable introuvable : $binary"
"$binary" --version

model_dir="$repo_dir/models"
model="$model_dir/sam3-q4_0.ggml"
valid_model() {
  local magic size
  [[ -f "$1" ]] || return 1
  magic="$(LC_ALL=C od -An -tx1 -N4 "$1" | tr -d '[:space:]')"
  size="$(wc -c < "$1" | tr -d '[:space:]')"
  [[ "$magic" == 336d6173 && "$size" -ge 629145600 ]]
}
mkdir -p "$model_dir"
if [[ -e "$model" ]]; then
  valid_model "$model" ||
    die "Modèle existant invalide ou incomplet : $model ; le déplacer avant de relancer."
  printf '[Spirula] Modèle SAM 3 existant réutilisé : %s\n' "$model"
else
  command -v curl >/dev/null 2>&1 || die "Prérequis manquant pour le téléchargement du modèle : curl"
  printf '[Spirula] Téléchargement SAM 3 q4_0 (~707 Mio) : %s\n' "$model_url"
  printf '[Spirula] Licence Meta SAM 3 : https://huggingface.co/PABannier/sam3.cpp ; aucune acceptation automatique.\n'
  tmp_model="$(mktemp "$model_dir/.sam3-q4_0.XXXXXXXX")"
  trap 'rm -f -- "$tmp_model"' EXIT
  curl --fail --location --progress-bar --output "$tmp_model" "$model_url" ||
    die "Téléchargement SAM 3 échoué."
  valid_model "$tmp_model" || die "Modèle téléchargé invalide ou incomplet ; aucun fichier final écrit."
  # Do not replace a model created concurrently during the download.
  ln -- "$tmp_model" "$model" || die "Impossible de publier le modèle sans écraser $model."
  rm -f -- "$tmp_model"
  trap - EXIT
fi
printf '[Spirula] Prêt : %s\n' "$binary"
printf 'export SPIRULA_BIN=%q\n' "$binary"
printf 'export TRAIN_MASKS_SPIRULA_MODEL=%q\n' "$model"
