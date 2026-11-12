
#!/usr/bin/env bash
set -Eeuo pipefail

# ===============================================================
# CONFIGURATION
# ===============================================================

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/../.." && pwd)"

repo_dir="$(dirname -- "$repo_root")/spirula-studio"
repo_url="https://github.com/harry7557558/spirula-studio.git"

cmake_args=()
clean=false

model_url="https://huggingface.co/PABannier/sam3.cpp/resolve/main/sam3-q4_0.ggml"

build_dir=""
binary=""
log_file=""

# ===============================================================
# LOGGING
# ===============================================================

timestamp="$(date +%Y%m%d_%H%M%S)"
log_file="${TMPDIR:-/tmp}/spirula_install_${timestamp}.log"

exec > >(tee -a "$log_file") 2>&1

log() {
    printf '[Spirula] %s\n' "$*"
}

die() {
    printf '\n[Spirula][ERREUR] %s\n' "$*" >&2
    printf '[Spirula] Journal complet : %s\n' "$log_file" >&2
    exit 1
}

trap 'rc=$?; printf "\n[Spirula][ERREUR] Ligne %s, code %s\n" "$LINENO" "$rc" >&2; printf "[Spirula] Journal : %s\n" "$log_file" >&2; exit "$rc"' ERR

usage() {
    cat <<'EOF'
Usage :
  bash install_spirula.sh [options] [-- options CMake]

Installe Spirula Studio (Vulkan/SAM) et le checkpoint SAM 3 GGML.

  --repo-dir PATH   Sources (défaut : <colmap_to_gs>/../spirula-studio)
  --repo-url URL    Dépôt Git
  --clean           Nettoyage complet et recompilation
  -h, --help        Afficher cette aide

Un exécutable existant est réutilisé sans mise à jour ni compilation.

Le modèle est téléchargé dans :
  <repo-dir>/models/sam3-q4_0.ggml

Linux nécessite le SDK Vulkan et les dépendances X11/OpenGL.
macOS utilise MoltenVK via CMake.

Un journal détaillé est créé dans /tmp.
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
        die "Plateforme non prise en charge"
        ;;

esac

binary="$repo_dir/$build_dir/spirula"

# ===============================================================
# VALIDATION CMAKE
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
# DIAGNOSTIC ENVIRONNEMENT
# ===============================================================

diagnose_environment() {

    log "==============================================================="
    log "ENVIRONNEMENT"
    log "==============================================================="

    log "OS : $(uname -a)"
    log "Utilisateur : $(id)"
    log "CONDA_PREFIX : ${CONDA_PREFIX:-non défini}"
    log "CC : ${CC:-non défini}"
    log "CXX : ${CXX:-non défini}"
    log "CMAKE_PREFIX_PATH : ${CMAKE_PREFIX_PATH:-non défini}"
    log "LD_LIBRARY_PATH : ${LD_LIBRARY_PATH:-non défini}"
    log "PKG_CONFIG_PATH : ${PKG_CONFIG_PATH:-non défini}"

    for tool in cmake ninja gcc g++ pkg-config; do
        if command -v "$tool" >/dev/null 2>&1; then
            log "$tool : $(command -v "$tool")"
        else
            log "$tool : absent"
        fi
    done

    if [[ -n "${CONDA_PREFIX:-}" ]]; then
        log "Bibliothèques Conda :"
        ls -ld "$CONDA_PREFIX/lib" || true
    fi

    log "==============================================================="
}

# ===============================================================
# LOCALISATION DES BIBLIOTHEQUES
# ===============================================================

find_system_library() {

    local name="$1"
    local multiarch=""

    if command -v dpkg-architecture >/dev/null 2>&1; then
        multiarch="$(dpkg-architecture -qDEB_HOST_MULTIARCH 2>/dev/null || true)"
    fi

    local candidates=()

    if [[ -n "$multiarch" ]]; then
        candidates+=(
            "/usr/lib/$multiarch/$name"
            "/lib/$multiarch/$name"
        )
    fi

    candidates+=(
        "/usr/lib/$name"
        "/lib/$name"
    )

    local path

    for path in "${candidates[@]}"; do
        if [[ -e "$path" ]]; then
            printf '%s\n' "$path"
            return 0
        fi
    done

    # Recherche complémentaire.
    find /usr/lib /lib \
        -name "$name" \
        -print -quit 2>/dev/null || true
}

# ===============================================================
# DEPENDANCES LINUX
# ===============================================================


install_linux_dependencies() {

    [[ "$(uname -s)" == Linux ]] || return 0

    log "==============================================================="
    log "DEPENDANCES LINUX / CONDA"
    log "==============================================================="

    local prefix="${CONDA_PREFIX:-}"

    [[ -n "$prefix" ]] ||
        die "CONDA_PREFIX absent : lancer le script dans l'environnement gsplat."

    log "Environnement actif : $prefix"

    local x11_header="$prefix/include/X11/X.h"
    local xlib_header="$prefix/include/X11/Xlib.h"

    # -----------------------------------------------------------
    # Diagnostic initial
    # -----------------------------------------------------------

    log "Vérification des headers X11..."

    for header in \
        "$prefix/include/X11/X.h" \
        "$prefix/include/X11/Xlib.h" \
        "$prefix/include/X11/keysym.h" \
        "$prefix/include/X11/extensions/Xrandr.h" \
        "$prefix/include/GL/gl.h"
    do
        if [[ -f "$header" ]]; then
            log "OK      $header"
        else
            log "ABSENT  $header"
        fi
    done

    # -----------------------------------------------------------
    # Installation dans le même environnement que le compilateur
    # -----------------------------------------------------------

    if [[ ! -f "$x11_header" ]]; then

        log "Installation de xorg-xorgproto dans gsplat..."

        local mamba="${MAMBA_EXE:-}"

        if [[ -z "$mamba" ]]; then
            mamba="$(command -v micromamba || true)"
        fi

        [[ -n "$mamba" ]] ||
            die "Micromamba introuvable pour installer les headers X11."

        "$mamba" install -y \
            -p "$prefix" \
            -c conda-forge \
            xorg-xorgproto \
            xorg-libx11 \
            xorg-libxext

    fi

    # -----------------------------------------------------------
    # Vérification stricte
    # -----------------------------------------------------------

    [[ -f "$x11_header" ]] ||
        die "Header manquant dans Conda : $x11_header"

    [[ -f "$xlib_header" ]] ||
        die "Header manquant dans Conda : $xlib_header"

    log "Headers X11 Conda correctement installés."

    # -----------------------------------------------------------
    # Vérification OpenGL
    # -----------------------------------------------------------

    local opengl="$prefix/lib/libOpenGL.so"
    local glx="$prefix/lib/libGLX.so"

    if [[ ! -f "$opengl" || ! -f "$glx" ]]; then

        log "Vérification des bibliothèques OpenGL Conda..."

        local mamba="${MAMBA_EXE:-$(command -v micromamba || true)}"

        [[ -n "$mamba" ]] ||
            die "Micromamba introuvable."

        "$mamba" install -y \
            -p "$prefix" \
            -c conda-forge \
            libgl-devel \
            libglx-devel \
            libopengl-devel

    fi

    log "Bibliothèques OpenGL :"

    find "$prefix/lib" -maxdepth 1 \
        \( -name 'libOpenGL.so*' -o -name 'libGLX.so*' \) \
        -print

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

    install_linux_dependencies

    # -----------------------------------------------------------
    # DEPOT
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

    # -----------------------------------------------------------
    # SUBMODULES
    # -----------------------------------------------------------

    log "Vérification des sous-modules..."

    git submodule update --init --recursive

    [[ -f build_develop.bash ]] ||
        die "Script officiel build_develop.bash introuvable."

    # -----------------------------------------------------------
    # NETTOYAGE COMPLET
    # -----------------------------------------------------------

    if [[ "$clean" == true ]]; then

        log "Nettoyage complet : $PWD/$build_dir"

        rm -rf -- "$build_dir"

    fi

    # -----------------------------------------------------------
    # CONFIGURATION CMAKE
    # -----------------------------------------------------------

    log "==============================================================="
    log "COMPILATION SPIRULA"
    log "==============================================================="

    log "Backend : Vulkan"
    log "Build : $build_dir"
    log "SAM : activé"

    build_cmd=(
        bash build_develop.bash
        -DSS_BACKEND=vulkan
        -DSS_BUILD_SAM=ON
        -DCMAKE_BUILD_TYPE=Release
    )

    if [[ "$(uname -s)" == Linux ]]; then
        build_cmd+=("${OPENGL_CMAKE_ARGS[@]}")
    fi

    if [[ ${#cmake_args[@]} -gt 0 ]]; then
        build_cmd+=("${cmake_args[@]}")
    fi

    log "Commande de compilation :"
    printf '  %q' "${build_cmd[@]}"
    printf '\n'

    # -----------------------------------------------------------
    # BUILD ET CAPTURE DU CODE RETOUR
    # -----------------------------------------------------------

    set +e

    "${build_cmd[@]}" 2>&1 | tee -a "$log_file"

    build_status=${PIPESTATUS[0]}

    set -e

    if [[ "$build_status" -ne 0 ]]; then

        log "==============================================================="
        log "ECHEC DE COMPILATION : DIAGNOSTIC CMAKE"
        log "==============================================================="

        cache="$repo_dir/$build_dir/CMakeCache.txt"

        if [[ -f "$cache" ]]; then

            log "Variables OpenGL dans CMakeCache :"

            grep -Ei \
                'OPENGL_|OpenGL_|CMAKE_PREFIX_PATH|CMAKE_LIBRARY_PATH' \
                "$cache" || true

        else

            log "CMakeCache.txt absent."

        fi

        log "Bibliothèques OpenGL disponibles :"

        find /usr/lib /lib \
            \( -name 'libOpenGL.so*' -o -name 'libGLX.so*' \) \
            -ls 2>/dev/null || true

        log "Fin du diagnostic."

        die "Compilation échouée (code $build_status). Voir $log_file"

    fi

fi

# ===============================================================
# VERIFICATION EXECUTABLE
# ===============================================================

repo_dir="$(cd -- "$repo_dir" && pwd)"
binary="$repo_dir/$build_dir/spirula"

[[ -x "$binary" ]] ||
    die "Compilation terminée mais exécutable introuvable : $binary"

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

    log "Téléchargement SAM 3 q4_0 (~707 Mio) : $model_url"

    log "Licence Meta SAM 3 : https://huggingface.co/PABannier/sam3.cpp"

    tmp_model="$(mktemp "$model_dir/.sam3-q4_0.XXXXXXXX")"

    trap 'rm -f -- "$tmp_model"; printf "\n[Spirula] Journal : %s\n" "$log_file"' EXIT

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
log "Journal : $log_file"

printf '\nexport SPIRULA_BIN=%q\n' "$binary"
printf 'export TRAIN_MASKS_SPIRULA_MODEL=%q\n' "$model"
