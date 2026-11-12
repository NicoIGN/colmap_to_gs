
#!/usr/bin/env bash
set -Eeuo pipefail

# ===============================================================
# CONFIGURATION
# ===============================================================

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/../.." && pwd)"
repo_dir="$(dirname -- "$repo_root")/spirula-studio"

repo_url="https://github.com/harry7557558/spirula-studio.git"
model_url="https://huggingface.co/PABannier/sam3.cpp/resolve/main/sam3-q4_0.ggml"

cmake_args=()
clean=false

die() {
    printf '[Spirula][ERREUR] %s\n' "$*" >&2
    exit 1
}

log() {
    printf '[Spirula] %s\n' "$*"
}

usage() {
    cat <<'EOF'
Usage :
  bash install_spirula.sh [options] [-- options CMake]

Options :
  --repo-dir PATH   Sources Spirula
  --repo-url URL    Dépôt Git
  --clean           Supprimer le build et recompiler proprement
  -h, --help        Afficher cette aide

Un exécutable existant est réutilisé sans mise à jour ni compilation.

Le modèle SAM 3 est téléchargé dans :
  <repo-dir>/models/sam3-q4_0.ggml

Linux nécessite les dépendances Vulkan, X11 et OpenGL dans Conda.
macOS utilise MoltenVK via CMake.

Avec --clean, le dépôt conserve sa branche et son remote de suivi.
Les modifications locales empêchent la mise à jour.
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
    Darwin) build_dir=build ;;
    Linux)  build_dir=build_vulkan ;;
    *) die "Plateforme non prise en charge" ;;
esac

binary="$repo_dir/$build_dir/spirula"

# ===============================================================
# VALIDATION CMAKE
# ===============================================================

for arg in "${cmake_args[@]}"; do
    case "$arg" in
        -B*|--build*|-DSS_BACKEND*|-DSSPLAT_BACKEND*)
            die "Le dossier de build et le backend sont fixés par le lanceur"
            ;;
    esac
done

# ===============================================================
# DEPENDANCES LINUX
# ===============================================================

check_linux_dependencies() {
    [[ "$(uname -s)" == Linux ]] || return 0

    local prefix="${CONDA_PREFIX:-}"
    [[ -n "$prefix" ]] ||
        die "CONDA_PREFIX absent : utiliser l'environnement Conda gsplat."

    log "Vérification des dépendances Linux : $prefix"

    local headers=(
        X11/X.h
        X11/Xlib.h
        X11/keysym.h
        X11/Xatom.h
        X11/Xresource.h
        X11/extensions/Xrandr.h
        X11/extensions/Xinerama.h
        X11/extensions/XInput2.h
        X11/Xcursor/Xcursor.h
        GL/gl.h
        vulkan/vulkan.h
    )

    local libraries=(
        libX11.so
        libXext.so
        libXrandr.so
        libXinerama.so
        libXcursor.so
        libXi.so
        libOpenGL.so
        libGLX.so
        libvulkan.so
    )

    local item
    local missing=0

    for item in "${headers[@]}"; do
        if [[ ! -f "$prefix/include/$item" ]]; then
            log "HEADER MANQUANT : $item"
            missing=1
        fi
    done

    for item in "${libraries[@]}"; do
        if [[ ! -e "$prefix/lib/$item" ]]; then
            log "BIBLIOTHEQUE MANQUANTE : $item"
            missing=1
        fi
    done

    [[ "$missing" -eq 0 ]] ||
        die "Dépendances Linux incomplètes dans l'environnement Conda."

    log "Dépendances Linux vérifiées."
}

# ===============================================================
# INSTALLATION / COMPILATION
# ===============================================================

if [[ -f "$binary" && "$clean" == false ]]; then

    [[ -x "$binary" ]] ||
        die "Exécutable existant mais non exécutable : $binary"

    [[ ${#cmake_args[@]} -eq 0 ]] ||
        die "Options CMake ignorées avec un exécutable existant : utiliser --clean."

    log "Exécutable existant réutilisé : $binary"

else

    for tool in git cmake ninja; do
        command -v "$tool" >/dev/null 2>&1 ||
            die "Prérequis manquant : $tool"
    done

    check_linux_dependencies

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
        )" || die "Impossible de vérifier l'état Git."

        [[ -z "$status" ]] ||
            die "Modifications locales dans $repo_dir."

        log "Mise à jour : $repo_dir"
        git -C "$repo_dir" pull --ff-only

    else
        log "Clonage : $repo_dir"
        git clone --recurse-submodules -- "$repo_url" "$repo_dir"
    fi

    repo_dir="$(cd -- "$repo_dir" && pwd)"
    cd -- "$repo_dir"

    git submodule update --init --recursive

    [[ -f build_develop.bash ]] ||
        die "Script officiel build_develop.bash introuvable."

    # -----------------------------------------------------------
    # NETTOYAGE
    # -----------------------------------------------------------

    if [[ "$clean" == true ]]; then
        log "Nettoyage complet : $PWD/$build_dir"
        rm -rf -- "$build_dir"
    fi

    # -----------------------------------------------------------
    # COMPILATION
    # -----------------------------------------------------------

    log "Compilation Vulkan : $build_dir"

    build_cmd=(
        bash build_develop.bash
        -DSS_BACKEND=vulkan
        -DSS_BUILD_SAM=ON
        -DCMAKE_BUILD_TYPE=Release
        "${cmake_args[@]}"
    )

    "${build_cmd[@]}"

fi

# ===============================================================
# VERIFICATION EXECUTABLE
# ===============================================================

repo_dir="$(cd -- "$repo_dir" && pwd)"
binary="$repo_dir/$build_dir/spirula"

[[ -x "$binary" ]] ||
    die "Exécutable introuvable : $binary"

log "Vérification de l'exécutable..."
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
        die "Modèle existant invalide ou incomplet : $model"

    log "Modèle SAM 3 existant réutilisé : $model"

else

    command -v curl >/dev/null 2>&1 ||
        die "Prérequis manquant : curl"

    log "Téléchargement SAM 3 q4_0 : $model_url"
    log "Licence Meta : https://huggingface.co/PABannier/sam3.cpp"

    tmp_model="$(mktemp "$model_dir/.sam3-q4_0.XXXXXXXX")"
    trap 'rm -f -- "$tmp_model"' EXIT

    curl --fail --location --progress-bar \
        --output "$tmp_model" "$model_url" ||
        die "Téléchargement SAM 3 échoué."

    valid_model "$tmp_model" ||
        die "Modèle téléchargé invalide ou incomplet."

    ln -- "$tmp_model" "$model" ||
        die "Impossible de publier le modèle sans écraser $model."

    rm -f -- "$tmp_model"
    trap - EXIT

fi

# ===============================================================
# FIN
# ===============================================================

log "Installation terminée."
log "Binaire : $binary"
log "Modèle : $model"

printf '\nexport SPIRULA_BIN=%q\n' "$binary"
printf 'export TRAIN_MASKS_SPIRULA_MODEL=%q\n' "$model"
