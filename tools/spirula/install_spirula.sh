
#!/usr/bin/env bash
set -Eeuo pipefail

###############################################################################
# PATHS AND DEFAULTS
###############################################################################

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
REPO_DIR="$(dirname -- "${REPO_ROOT}")/spirula-studio"

REPO_URL="https://github.com/harry7557558/spirula-studio.git"
MODEL_URL="https://huggingface.co/PABannier/sam3.cpp/resolve/main/sam3-q4_0.ggml"
MODEL_NAME="sam3-q4_0.ggml"

CMAKE_ARGS=()
CLEAN=false
TMP_MODEL=""

###############################################################################
# LOGGING AND ERRORS
###############################################################################

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
LOG_FILE="/tmp/spirula_install_${TIMESTAMP}.log"

exec > >(tee -a "${LOG_FILE}") 2>&1

log() {
    echo "[Spirula] $*"
}

die() {
    echo "[Spirula][ERREUR] $*" >&2
    exit 1
}

cleanup() {
    [[ -n "${TMP_MODEL}" ]] && rm -f -- "${TMP_MODEL}"
}

trap cleanup EXIT
trap 'status=$?; echo "[Spirula][ERREUR] Échec ligne ${LINENO} (code ${status}). Log : ${LOG_FILE}" >&2; exit "${status}"' ERR

###############################################################################
# HELP
###############################################################################

usage() {
    cat <<EOF
Usage:
  bash install_spirula.sh [options] [-- arguments CMake]

Options:
  --repo-dir PATH    Répertoire du dépôt Spirula
  --repo-url URL     URL Git du dépôt Spirula
  --clean            Supprime et reconstruit le répertoire de compilation
  --help, -h         Affiche cette aide

Arguments après -- :
  Arguments supplémentaires transmis à build_develop.bash

Exemple :
  bash install_spirula.sh --clean
  bash install_spirula.sh -- -DSS_BUILD_SAM=ON
EOF
}

###############################################################################
# ARGUMENT PARSING
###############################################################################

while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo-dir)
            [[ $# -ge 2 ]] || die "Argument manquant pour --repo-dir"
            REPO_DIR="$2"
            shift 2
            ;;
        --repo-url)
            [[ $# -ge 2 ]] || die "Argument manquant pour --repo-url"
            REPO_URL="$2"
            shift 2
            ;;
        --clean)
            CLEAN=true
            shift
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        --)
            shift
            CMAKE_ARGS+=("$@")
            break
            ;;
        *)
            die "Option inconnue : $1"
            ;;
    esac
done

###############################################################################
# PLATFORM
###############################################################################

OS="$(uname -s)"

case "${OS}" in
    Linux)
        BUILD_DIR="build_vulkan"
        ;;
    Darwin)
        BUILD_DIR="build"
        ;;
    *)
        die "Système non pris en charge : ${OS}"
        ;;
esac

BINARY="${REPO_DIR}/${BUILD_DIR}/spirula"

log "Projet : ${REPO_ROOT}"
log "Dépôt Spirula : ${REPO_DIR}"
log "Système : ${OS}"
log "Répertoire de compilation : ${BUILD_DIR}"
log "Log : ${LOG_FILE}"

###############################################################################
# BUILD PARALLELISM
###############################################################################

if [[ "${OS}" == "Linux" ]]; then
    CPU_COUNT="$(nproc)"
else
    CPU_COUNT="$(sysctl -n hw.ncpu)"
fi

# Limitation facultative par variable d'environnement.
JOBS="${SPIRULA_JOBS:-${CPU_COUNT}}"

[[ "${JOBS}" =~ ^[1-9][0-9]*$ ]] ||
    die "SPIRULA_JOBS doit être un entier positif"

log "CPU disponibles : ${CPU_COUNT}"
log "Jobs de compilation : ${JOBS}"

###############################################################################
# CMAKE ARGUMENT VALIDATION
###############################################################################

for arg in "${CMAKE_ARGS[@]}"; do
    case "${arg}" in
        -B*|--build*|-DSS_BACKEND*|-DSSPLAT_BACKEND*)
            die "Argument CMake interdit : ${arg}"
            ;;
    esac
done

###############################################################################
# DEPENDENCIES
###############################################################################

check_header() {
    local header="$1"

    for dir in \
        "${CONDA_PREFIX:-}/include" \
        /usr/include \
        /usr/local/include
    do
        [[ -f "${dir}/${header}" ]] || continue
        log "Header OK : ${header} (${dir})"
        return 0
    done

    echo "[Spirula][ERREUR] Header manquant : ${header}"
    return 1
}

check_library() {
    local library="$1"
    local prefix="${CONDA_PREFIX:-}"

    # Conda
    if [[ -n "${prefix}" ]] &&
       compgen -G "${prefix}/lib/${library}" >/dev/null; then
        log "Bibliothèque OK : ${library} (Conda)"
        return 0
    fi

    # Système
    if command -v ldconfig >/dev/null 2>&1 &&
       ldconfig -p 2>/dev/null | grep -Fq "${library%.so}"; then
        log "Bibliothèque OK : ${library} (système)"
        return 0
    fi

    # Emplacements usuels
    for dir in \
        /usr/lib \
        /usr/lib64 \
        /usr/lib/x86_64-linux-gnu \
        /lib \
        /lib64 \
        /lib/x86_64-linux-gnu
    do
        if compgen -G "${dir}/${library}" >/dev/null; then
            log "Bibliothèque OK : ${library} (${dir})"
            return 0
        fi
    done

    echo "[Spirula][ERREUR] Bibliothèque manquante : ${library}"
    return 1
}

check_linux_dependencies() {
    log "Vérification des dépendances Linux"

    [[ -n "${CONDA_PREFIX:-}" ]] &&
        log "Environnement Conda : ${CONDA_PREFIX}"

    local failed=0

    local headers=(
        X11/X.h
        X11/Xlib.h
        X11/Xatom.h
        X11/Xresource.h
        X11/keysym.h
        X11/extensions/Xrandr.h
        X11/extensions/Xinerama.h
        X11/extensions/XInput2.h
        X11/Xcursor/Xcursor.h
        GL/gl.h
    )

    local libraries=(
        libX11.so
        libXext.so
        libXrandr.so
        libXi.so
        libXcursor.so
        libOpenGL.so
        libGLX.so
        libvulkan.so
    )

    local header library

    for header in "${headers[@]}"; do
        check_header "${header}" || failed=1
    done

    for library in "${libraries[@]}"; do
        check_library "${library}" || failed=1
    done

    # Xinerama : bibliothèque optionnelle, header requis par GLFW.
    if check_library libXinerama.so; then
        log "Xinerama Xlib disponible"
    else
        log "libXinerama.so absente : fonctionnalité optionnelle"
    fi

    [[ "${failed}" -eq 0 ]] ||
        die "Certaines dépendances Linux obligatoires sont absentes."

    log "Vérification des dépendances terminée"
}

if [[ "${OS}" == "Linux" ]]; then
    check_linux_dependencies

    ###########################################################################
    # IMPORTANT : CONDA GCC / SYSTEM HEADERS
    ###########################################################################

    # Le compilateur Conda ne recherche pas toujours /usr/include.
    # GLFW a notamment besoin de X11/extensions/Xinerama.h.

    CFLAGS="${CFLAGS:-} -I/usr/include"
    CXXFLAGS="${CXXFLAGS:-} -I/usr/include"

    export CFLAGS CXXFLAGS
fi

###############################################################################
# BUILD TOOLS
###############################################################################

for tool in git cmake ninja; do
    command -v "${tool}" >/dev/null 2>&1 ||
        die "Commande indispensable introuvable : ${tool}"
done

###############################################################################
# EXISTING BINARY
###############################################################################

if [[ -x "${BINARY}" && "${CLEAN}" == false ]]; then

    log "Binaire déjà présent : ${BINARY}"
    log "Utilisation de la compilation existante"

else

    ###########################################################################
    # REPOSITORY
    ###########################################################################

    if [[ -d "${REPO_DIR}/.git" ]]; then

        log "Dépôt Git existant"

        if ! git -C "${REPO_DIR}" diff --quiet ||
           ! git -C "${REPO_DIR}" diff --cached --quiet ||
           [[ -n "$(git -C "${REPO_DIR}" ls-files --others --exclude-standard -- ':!models' ':!models/**')" ]]; then

            die "Le dépôt Spirula contient des modifications locales. Nettoie-les ou utilise un autre --repo-dir."
        fi

        log "Mise à jour du dépôt"
        git -C "${REPO_DIR}" pull --ff-only

    else

        log "Clonage de Spirula"

        mkdir -p "$(dirname -- "${REPO_DIR}")"

        git clone --recurse-submodules \
            "${REPO_URL}" \
            "${REPO_DIR}"
    fi

    log "Mise à jour des sous-modules"

    git -C "${REPO_DIR}" submodule update --init --recursive

    [[ -f "${REPO_DIR}/build_develop.bash" ]] ||
        die "Script build_develop.bash introuvable dans ${REPO_DIR}"

    ###########################################################################
    # CLEAN BUILD
    ###########################################################################

    if [[ "${CLEAN}" == true ]]; then
        log "Suppression du répertoire de compilation"
        rm -rf -- "${REPO_DIR:?}/${BUILD_DIR}"
    fi

    ###########################################################################
    # BUILD
    ###########################################################################

    log "Compilation de Spirula"

    export CMAKE_BUILD_PARALLEL_LEVEL="${JOBS}"

    BUILD_CMD=(
        bash build_develop.bash
        -DSS_BACKEND=vulkan
        -DSS_BUILD_SAM=ON
        -DCMAKE_BUILD_TYPE=Release
    )

    # Force les chemins d'inclusion dans les variables CMake.
    # Cela fonctionne également si le cache CMake existe déjà.

    if [[ "${OS}" == "Linux" ]]; then

        BUILD_CMD+=(
            "-DCMAKE_C_FLAGS=${CFLAGS}"
            "-DCMAKE_CXX_FLAGS=${CXXFLAGS}"
        )

    fi

    BUILD_CMD+=("${CMAKE_ARGS[@]}")

    (
        cd "${REPO_DIR}"
        "${BUILD_CMD[@]}"
    )

    [[ -x "${BINARY}" ]] ||
        die "Compilation terminée mais exécutable introuvable : ${BINARY}"

fi

###############################################################################
# BINARY VALIDATION
###############################################################################

[[ -x "${BINARY}" ]] ||
    die "Exécutable Spirula introuvable : ${BINARY}"

log "Exécutable : ${BINARY}"
log "Version de Spirula :"

"${BINARY}" --version

###############################################################################
# SAM3 MODEL
###############################################################################

MODEL_DIR="${REPO_DIR}/models"
MODEL_FILE="${MODEL_DIR}/${MODEL_NAME}"

mkdir -p "${MODEL_DIR}"

validate_model() {
    local file="$1"

    [[ -f "${file}" ]] || return 1

    local size
    size="$(stat -c '%s' "${file}" 2>/dev/null ||
            stat -f '%z' "${file}" 2>/dev/null)" || return 1

    # Taille minimale : 600 MiB
    [[ "${size}" -ge 629145600 ]] || return 1

    # Magic bytes SAM3
    local magic
    magic="$(od -An -tx1 -N4 "${file}" | tr -d ' \n')"

    [[ "${magic}" == "336d6173" ]]
}

if [[ -f "${MODEL_FILE}" ]]; then

    log "Modèle SAM3 existant : ${MODEL_FILE}"

    validate_model "${MODEL_FILE}" ||
        die "Le modèle SAM3 existant est invalide : ${MODEL_FILE}"

else

    log "Téléchargement du modèle SAM3"
    log "URL : ${MODEL_URL}"

    command -v curl >/dev/null 2>&1 ||
        die "curl est nécessaire pour télécharger le modèle SAM3"

    TMP_MODEL="$(mktemp "${MODEL_DIR}/.sam3-download.XXXXXX")"

    curl --fail --location --retry 3 \
        --output "${TMP_MODEL}" \
        "${MODEL_URL}"

    validate_model "${TMP_MODEL}" ||
        die "Le modèle téléchargé est invalide (taille ou magic bytes incorrects)"

    mv -- "${TMP_MODEL}" "${MODEL_FILE}"
    TMP_MODEL=""

    log "Modèle SAM3 installé : ${MODEL_FILE}"

fi

###############################################################################
# EXPORTS
###############################################################################

log "Installation terminée"

echo
echo "Pour utiliser Spirula, configure les variables suivantes :"
echo

printf 'export SPIRULA_BIN="%s"\n' "${BINARY}"
printf 'export TRAIN_MASKS_SPIRULA_MODEL="%s"\n' "${MODEL_FILE}"

echo

log "Log complet : ${LOG_FILE}"
