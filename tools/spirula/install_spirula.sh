
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
    log "VERIFICATION DES DEPENDANCES LINUX"
    log "==============================================================="

    diagnose_environment

    command -v apt-get >/dev/null 2>&1 ||
        die "apt-get indisponible : installation automatique impossible."

    [[ "$(id -u)" -eq 0 ]] ||
        die "Les dépendances système doivent être installées avec les droits root."

    local multiarch
    multiarch="$(dpkg-architecture -qDEB_HOST_MULTIARCH 2>/dev/null || true)"

    log "Architecture Debian : ${multiarch:-inconnue}"

    local opengl_lib
    local glx_lib

    opengl_lib="$(find_system_library libOpenGL.so)"
    glx_lib="$(find_system_library libGLX.so)"

    log "OpenGL avant installation : ${opengl_lib:-INTROUVABLE}"
    log "GLX avant installation : ${glx_lib:-INTROUVABLE}"

    # -----------------------------------------------------------
    # Contrôle réel des bibliothèques de développement.
    # Les fichiers .so.0 ne suffisent pas.
    # -----------------------------------------------------------

    local need_install=false

    [[ -n "$opengl_lib" && -n "$glx_lib" ]] ||
        need_install=true

    [[ -f /usr/include/X11/X.h ]] ||
        need_install=true

    [[ -f /usr/include/GL/gl.h ]] ||
        need_install=true

    if [[ "$need_install" == true ]]; then

        log "Installation des dépendances de développement X11/OpenGL..."

        apt-get update

        apt-get install -y \
            xorgproto \
            libx11-dev \
            libxext-dev \
            libxrandr-dev \
            libxinerama-dev \
            libxcursor-dev \
            libxi-dev \
            libgl-dev \
            libglx-dev \
            libopengl-dev \
            libgl1-mesa-dev \
            libegl1-mesa-dev \
            libwayland-dev \
            libxkbcommon-dev \
            pkg-config

    else

        log "Dépendances de développement déjà présentes."

    fi

    # -----------------------------------------------------------
    # Nouvelle détection
    # -----------------------------------------------------------

    opengl_lib="$(find_system_library libOpenGL.so)"
    glx_lib="$(find_system_library libGLX.so)"

    log "OpenGL après installation : ${opengl_lib:-INTROUVABLE}"
    log "GLX après installation : ${glx_lib:-INTROUVABLE}"

    [[ -n "$opengl_lib" ]] ||
        die "libOpenGL.so de développement introuvable."

    [[ -n "$glx_lib" ]] ||
        die "libGLX.so de développement introuvable."

    [[ -f /usr/include/GL/gl.h ]] ||
        die "GL/gl.h introuvable."

    [[ -f /usr/include/X11/X.h ]] ||
        die "X11/X.h introuvable."

    # -----------------------------------------------------------
    # Informations sur les fichiers trouvés
    # -----------------------------------------------------------

    log "Détails OpenGL :"
    ls -l "$opengl_lib"

    log "Détails GLX :"
    ls -l "$glx_lib"

    log "Headers OpenGL :"
    ls -l /usr/include/GL/gl.h

    log "Headers X11 :"
    ls -l /usr/include/X11/X.h

    log "Bibliothèques runtime :"

    ldconfig -p 2>/dev/null |
        grep -E 'lib(OpenGL|GLX|GL|EGL)\.so' || true

    # -----------------------------------------------------------
    # Variables CMake explicites
    # -----------------------------------------------------------

    OPENGL_CMAKE_ARGS=(
        "-DOPENGL_opengl_LIBRARY=$opengl_lib"
        "-DOPENGL_glx_LIBRARY=$glx_lib"
        "-DOPENGL_INCLUDE_DIR=/usr/include"
        "-DOpenGL_GL_PREFERENCE=GLVND"
    )

    log "Arguments OpenGL transmis à CMake :"

    printf '  %s\n' "${OPENGL_CMAKE_ARGS[@]}"

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
