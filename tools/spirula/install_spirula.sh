
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
  --clean           Mettre à jour et recompiler proprement
  -h, --help        Afficher cette aide

Le modèle est téléchargé dans :
  <repo-dir>/models/sam3-q4_0.ggml

Avec --clean, un dépôt existant conserve sa branche et son remote
de suivi. La mise à jour est fast-forward uniquement.

Linux nécessite le SDK Vulkan et les dépendances X11/Wayland.
macOS utilise MoltenVK via CMake.

EOF
}

# ===============================================================
# ARGUMENTS
# ===============================================================

while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo-dir|--repo-url)
            [[ $# -ge 2 && -n "$2" && "$2" != --* ]] ||
                die "Valeur manquante pour $1"

            case "$1" in
                --repo-dir) repo_dir="$2" ;;
                --repo-url) repo_url="$2" ;;
            esac

            shift 2
            ;;

        --)
            shift
            cmake_args=("$@")
            break
            ;;

        --clean)
            clean=true
            shift
            ;;

        -h|--help)
            usage
            exit 0
            ;;

        *)
            die "Argument inconnu : $1"
            ;;
    esac
done

# ===============================================================
# PLATEFORME
# ===============================================================

case "$(uname -s)" in
    Darwin)
        build_dir=build
        ;;

    Linux)
        build_dir=build_vulkan
        ;;

    *)
        die "Ce lanceur prend en charge macOS et Linux uniquement"
        ;;
esac

# ===============================================================
# VALIDATION OPTIONS CMAKE
# ===============================================================

if [[ ${#cmake_args[@]} -gt 0 ]]; then
    for arg in "${cmake_args[@]}"; do
        case "$arg" in
            -B*|--build*|-DSS_BACKEND*|-DSSPLAT_BACKEND*)
                die "Le dossier de build et le backend Vulkan sont fixés par le lanceur"
                ;;
        esac
    done
fi

# ===============================================================
# DEPENDANCES LINUX : X11 / WAYLAND
# ===============================================================

install_linux_dependencies() {

    [[ "$(uname -s)" == Linux ]] || return 0

    printf '[Spirula] Vérification des dépendances Linux...\n'

    local conda_prefix="${CONDA_PREFIX:-}"
    local x11_header=""
    local installed=false

    if [[ -n "$conda_prefix" ]]; then
        x11_header="$conda_prefix/include/X11/X.h"
    fi

    # X.h peut aussi être fourni par le système.
    if [[ -f "$x11_header" || -f /usr/include/X11/X.h ]]; then
        printf '[Spirula] En-têtes X11 présents.\n'
        return 0
    fi

    printf '[Spirula] En-tête X11/X.h manquant.\n'

    # -----------------------------------------------------------
    # Priorité à l'environnement Conda/Micromamba actif.
    # xorg-xorgproto fournit notamment X11/X.h.
    # -----------------------------------------------------------

    if [[ -n "$conda_prefix" ]]; then

        if command -v micromamba >/dev/null 2>&1; then

            printf '[Spirula] Installation xorg-xorgproto via Micromamba...\n'

            micromamba install -y \
                -p "$conda_prefix" \
                -c conda-forge \
                xorg-xorgproto

            installed=true

        elif command -v conda >/dev/null 2>&1; then

            printf '[Spirula] Installation xorg-xorgproto via Conda...\n'

            conda install -y \
                -p "$conda_prefix" \
                -c conda-forge \
                xorg-xorgproto

            installed=true
        fi
    fi

    # -----------------------------------------------------------
    # Fallback système Debian / Ubuntu (Colab).
    # -----------------------------------------------------------

    if [[ "$installed" == false ]]; then

        if command -v apt-get >/dev/null 2>&1 &&
           [[ "$(id -u)" -eq 0 ]]; then

            printf '[Spirula] Installation des en-têtes X11 système...\n'

            apt-get update
            apt-get install -y xorgproto

            installed=true
        fi
    fi

    # -----------------------------------------------------------
    # Vérification finale.
    # -----------------------------------------------------------

    if [[ -n "$conda_prefix" ]]; then
        x11_header="$conda_prefix/include/X11/X.h"
    fi

    if [[ -f "$x11_header" || -f /usr/include/X11/X.h ]]; then
        printf '[Spirula] Dépendances X11 correctement installées.\n'
    else
        die "X11/X.h introuvable. Installer xorg-xorgproto dans l'environnement actif."
    fi
}

install_linux_dependencies

# ===============================================================
# EXECUTABLE
# ===============================================================

binary="$repo_dir/$build_dir/spirula"

if [[ -f "$binary" && "$clean" == false ]]; then

    [[ -x "$binary" ]] ||
        die "Exécutable existant mais non exécutable : $binary"

    [[ ${#cmake_args[@]} -eq 0 ]] ||
        die "Options CMake ignorées avec un exécutable existant : utiliser --clean."

    printf '[Spirula] Exécutable existant réutilisé, sans mise à jour ni compilation : %s\n' "$binary"

else

    # -----------------------------------------------------------
    # OUTILS DE BUILD
    # -----------------------------------------------------------

    for tool in git cmake ninja; do
        command -v "$tool" >/dev/null 2>&1 ||
            die "Prérequis manquant : $tool"
    done

    # -----------------------------------------------------------
    # DEPOT GIT
    # -----------------------------------------------------------

    if [[ -e "$repo_dir" ]]; then

        [[ -d "$repo_dir/.git" || -f "$repo_dir/.git" ]] ||
            die "Le dossier existe mais n'est pas un checkout Git : $repo_dir"

        status="$(
            git -C "$repo_dir" status \
                --porcelain \
                --untracked-files=all \
                -- . ':(top,exclude)models/'
        )" || die "Impossible de vérifier l'état Git de $repo_dir"

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

    # -----------------------------------------------------------
    # SUBMODULES
    # -----------------------------------------------------------

    printf '[Spirula] Vérification des sous-modules...\n'

    git submodule update --init --recursive

    [[ -f build_develop.bash ]] ||
        die "Script officiel build_develop.bash introuvable"

    # -----------------------------------------------------------
    # NETTOYAGE
    # -----------------------------------------------------------

    if [[ "$clean" == true ]]; then

        printf '[Spirula] Nettoyage complet : %s/%s\n' "$PWD" "$build_dir"

        rm -rf -- "$build_dir"

    fi

    # -----------------------------------------------------------
    # COMPILATION VULKAN
    # -----------------------------------------------------------

    printf '[Spirula] Compilation Vulkan dans %s/%s\n' "$PWD" "$build_dir"

    build_cmd=(
        bash build_develop.bash
        -DSS_BACKEND=vulkan
        -DSS_BUILD_SAM=ON
        -DCMAKE_BUILD_TYPE=Release
    )

    if [[ ${#cmake_args[@]} -gt 0 ]]; then
        build_cmd+=("${cmake_args[@]}")
    fi

    "${build_cmd[@]}"

fi

# ===============================================================
# VERIFICATION EXECUTABLE
# ===============================================================

repo_dir="$(cd -- "$repo_dir" && pwd)"
binary="$repo_dir/$build_dir/spirula"

[[ -x "$binary" ]] ||
    die "Compilation terminée mais exécutable introuvable : $binary"

printf '[Spirula] Vérification de l'exécutable...\n'

"$binary" --version

# ===============================================================
# MODELE SAM 3
# ===============================================================

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

    command -v curl >/dev/null 2>&1 ||
        die "Prérequis manquant pour le téléchargement du modèle : curl"

    printf '[Spirula] Téléchargement SAM 3 q4_0 (~707 Mio) : %s\n' "$model_url"

    printf '[Spirula] Licence Meta SAM 3 : https://huggingface.co/PABannier/sam3.cpp\n'
    printf '[Spirula] Aucune acceptation automatique de licence.\n'

    tmp_model="$(mktemp "$model_dir/.sam3-q4_0.XXXXXXXX")"

    trap 'rm -f -- "$tmp_model"' EXIT

    curl --fail --location --progress-bar \
        --output "$tmp_model" "$model_url" ||
        die "Téléchargement SAM 3 échoué."

    valid_model "$tmp_model" ||
        die "Modèle téléchargé invalide ou incomplet ; aucun fichier final écrit."

    # Ne pas écraser un modèle créé pendant le téléchargement.
    ln -- "$tmp_model" "$model" ||
        die "Impossible de publier le modèle sans écraser $model."

    rm -f -- "$tmp_model"

    trap - EXIT

fi

# ===============================================================
# FIN
# ===============================================================

printf '[Spirula] Prêt : %s\n' "$binary"

printf 'export SPIRULA_BIN=%q\n' "$binary"
printf 'export TRAIN_MASKS_SPIRULA_MODEL=%q\n' "$model"
