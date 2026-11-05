#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/../.." && pwd)"
repo_dir="$(dirname -- "$repo_root")/spirula-studio"
repo_url="https://github.com/harry7557558/spirula-studio.git"
cmake_args=()

die() {
  printf 'Erreur : %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage :
  bash install_spirula.sh [options] [-- options CMake]

Clone ou met à jour Spirula Studio, puis compile le backend Vulkan.
  --repo-dir PATH   Sources (défaut : <colmap_to_gs>/../spirula-studio)
  --repo-url URL    Dépôt à cloner (défaut : dépôt officiel)
  -h, --help        Afficher cette aide

Un dépôt existant conserve sa branche et son remote de suivi.
La mise à jour est fast-forward uniquement, sans écrasement des modifications.
Les prérequis doivent déjà être installés : Git, CMake, Ninja, compilateur C++.
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
    -h|--help) usage; exit 0 ;;
    *) die "Argument inconnu : $1" ;;
  esac
done

case "$(uname -s)" in
  Darwin) build_dir=build ;;
  Linux) build_dir=build_vulkan ;;
  *) die "Ce lanceur prend en charge macOS et Linux uniquement" ;;
esac

for tool in git cmake ninja; do
  command -v "$tool" >/dev/null 2>&1 || die "Prérequis manquant : $tool"
done
if [[ ${#cmake_args[@]} -gt 0 ]]; then
  for arg in "${cmake_args[@]}"; do
    case "$arg" in
      -B*|--build*|-DSS_BACKEND*|-DSSPLAT_BACKEND*)
        die "Le dossier de build et le backend Vulkan sont fixés par le lanceur" ;;
    esac
  done
fi

if [[ -e "$repo_dir" ]]; then
  [[ -d "$repo_dir/.git" || -f "$repo_dir/.git" ]] ||
    die "Le dossier existe mais n'est pas un checkout Git : $repo_dir"
  [[ -z "$(git -C "$repo_dir" status --porcelain)" ]] ||
    die "Modifications locales dans $repo_dir : les conserver ou les committer avant la mise à jour"
  printf '[Spirula] Mise à jour : %s\n' "$repo_dir"
  git -C "$repo_dir" pull --ff-only
else
  printf '[Spirula] Clonage : %s\n' "$repo_dir"
  git clone --recurse-submodules -- "$repo_url" "$repo_dir"
fi

cd -- "$repo_dir"
git submodule update --init --recursive
[[ -f build_develop.bash ]] || die "Script officiel build_develop.bash introuvable"

printf '[Spirula] Compilation Vulkan dans %s/%s\n' "$PWD" "$build_dir"
# Le script officiel gère la génération, MoltenVK et le parallélisme selon la RAM.
build_cmd=(bash build_develop.bash -DSS_BACKEND=vulkan -DCMAKE_BUILD_TYPE=Release)
if [[ ${#cmake_args[@]} -gt 0 ]]; then
  build_cmd+=("${cmake_args[@]}")
fi
"${build_cmd[@]}"

binary="$PWD/$build_dir/spirula"
[[ -x "$binary" ]] || die "Compilation terminée mais exécutable introuvable : $binary"
"$binary" --version
printf '[Spirula] Prêt : %s\n' "$binary"
printf 'export SPIRULA_BIN=%q\n' "$binary"
