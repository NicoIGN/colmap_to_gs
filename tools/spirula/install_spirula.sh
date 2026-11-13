
#!/usr/bin/env bash

set -Eeuo pipefail

# ===============================================================
# Configuration
# ===============================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

SPIRULA_URL="https://github.com/harry7557558/spirula-studio.git"
REPO_DIR="${SPIRULA_REPO_DIR:-$(dirname "${PROJECT_ROOT}")/spirula-studio}"

SAM3_MODEL_URL="https://huggingface.co/PABannier/sam3.cpp/resolve/main/sam3-q4_0.ggml"
SAM3_MODEL_PATH="${SPIRULA_SAM3_MODEL:-${REPO_DIR}/models/sam3-q4_0.ggml}"

# ===============================================================
# Logging et gestion des erreurs
# ===============================================================

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
LOG_FILE="/tmp/spirula_install_${TIMESTAMP}.log"

mkdir -p "$(dirname "${LOG_FILE}")"
exec > >(tee -a "${LOG_FILE}") 2>&1

log() {
    echo "[Spirula] $*"
}

die() {
    log "[ERREUR] $*"
    exit 1
}

on_error() {
    local status=$?
    local line="${1:-?}"

    echo
    log "[ERREUR] Échec ligne ${line} (code ${status})."
    log "[ERREUR] Log : ${LOG_FILE}"

    exit "${status}"
}

trap 'on_error $LINENO' ERR

# ===============================================================
# Arguments
# ===============================================================

CLEAN=0

usage() {
    cat <<EOF
Usage : $0 [OPTIONS]

Options :
  --clean    Supprime le répertoire de compilation avant le build
  --help     Affiche cette aide

Variables :
  SPIRULA_JOBS       Nombre de jobs de compilation
  SPIRULA_REPO_DIR   Répertoire du dépôt Spirula
  SPIRULA_SAM3_MODEL Chemin du modèle SAM3
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --clean)
            CLEAN=1
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            die "Argument inconnu : $1"
            ;;
    esac
    shift
done

# ===============================================================
# Informations système
# ===============================================================

log "Projet : ${PROJECT_ROOT}"
log "Dépôt Spirula : ${REPO_DIR}"
log "Log : ${LOG_FILE}"

if [[ -z "${CONDA_PREFIX:-}" ]]; then
    die "CONDA_PREFIX absent. Lancer ce script dans l'environnement gsplat."
fi

if [[ ! -d "${CONDA_PREFIX}" ]]; then
    die "Environnement Conda introuvable : ${CONDA_PREFIX}"
fi

log "Environnement Conda : ${CONDA_PREFIX}"

OS="$(uname -s)"

case "${OS}" in
    Linux)
        BUILD_DIR="build_vulkan"
        ;;
    Darwin)
        BUILD_DIR="build"
        ;;
    *)
        die "Système non supporté : ${OS}"
        ;;
esac

log "Système : ${OS}"
log "Répertoire de compilation : ${BUILD_DIR}"

# ===============================================================
# Parallélisme
# ===============================================================

if [[ "${OS}" == "Linux" ]]; then
    CPU_COUNT="$(nproc)"
else
    CPU_COUNT="$(sysctl -n hw.ncpu)"
fi

JOBS="${SPIRULA_JOBS:-${CPU_COUNT}}"

[[ "${JOBS}" =~ ^[1-9][0-9]*$ ]] ||
    die "SPIRULA_JOBS doit être un entier positif."

export CMAKE_BUILD_PARALLEL_LEVEL="${JOBS}"

log "CPU disponibles : ${CPU_COUNT}"
log "Jobs de compilation : ${JOBS}"

# ===============================================================
# Recherche des headers
# ===============================================================

HEADER_DIRS=(
    "${CONDA_PREFIX}/include"
    /usr/include
)

find_header() {
    local relative_path="$1"
    local directory

    for directory in "${HEADER_DIRS[@]}"; do
        if [[ -r "${directory}/${relative_path}" ]]; then
            printf '%s\n' "${directory}/${relative_path}"
            return 0
        fi
    done

    return 1
}

check_header() {
    local relative_path="$1"
    local result

    if ! result="$(find_header "${relative_path}")"; then
        die "Header manquant : ${relative_path}"
    fi

    log "Header OK : ${result}"
}

# ===============================================================
# Recherche des bibliothèques
# ===============================================================

find_library() {
    local name="$1"
    local directory
    local candidate

    local directories=(
        "${CONDA_PREFIX}/lib"
        /usr/lib
        /usr/lib64
        /usr/lib/x86_64-linux-gnu
        /usr/lib/aarch64-linux-gnu
        /lib
        /lib64
        /lib/x86_64-linux-gnu
        /lib/aarch64-linux-gnu
    )

    for directory in "${directories[@]}"; do
        candidate="${directory}/${name}"

        if [[ -e "${candidate}" ]]; then
            printf '%s\n' "${candidate}"
            return 0
        fi
    done

    if command -v ldconfig >/dev/null 2>&1; then
        candidate="$(ldconfig -p 2>/dev/null |
            awk -v name="${name}" '$1 == name {print $NF; exit}')"

        if [[ -n "${candidate}" && -e "${candidate}" ]]; then
            printf '%s\n' "${candidate}"
            return 0
        fi
    fi

    return 1
}

check_library() {
    local name="$1"
    local result

    if ! result="$(find_library "${name}")"; then
        die "Bibliothèque manquante : ${name}"
    fi

    log "Bibliothèque OK : ${name} (${result})"
}

# ===============================================================
# Vérification des dépendances Linux
# ===============================================================

if [[ "${OS}" == "Linux" ]]; then

    log "Vérification des dépendances Linux"

    # -----------------------------------------------------------
    # Headers X11 / OpenGL / Vulkan
    # -----------------------------------------------------------

    REQUIRED_HEADERS=(
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
        GL/glx.h
        vulkan/vulkan.h
        vulkan/vulkan_core.h
        vulkan/vk_platform.h
    )

    for header in "${REQUIRED_HEADERS[@]}"; do
        check_header "${header}"
    done

    # -----------------------------------------------------------
    # Détermination du répertoire des headers Vulkan
    # -----------------------------------------------------------

    VULKAN_HEADER="$(find_header vulkan/vulkan.h)"
    VULKAN_INCLUDE_DIR="${VULKAN_HEADER%/vulkan/vulkan.h}"

    log "Vulkan_INCLUDE_DIR : ${VULKAN_INCLUDE_DIR}"

    # -----------------------------------------------------------
    # Bibliothèques
    # -----------------------------------------------------------

    REQUIRED_LIBRARIES=(
        libX11.so
        libXext.so
        libXrandr.so
        libXi.so
        libXcursor.so
        libXinerama.so
        libGL.so
        libGLX.so
        libOpenGL.so
        libvulkan.so
    )

    for library in "${REQUIRED_LIBRARIES[@]}"; do
        check_library "${library}"
    done

    # -----------------------------------------------------------
    # Bibliothèque Vulkan pour CMake
    # -----------------------------------------------------------

    VULKAN_LIBRARY="$(find_library libvulkan.so)" ||
        die "Bibliothèque de développement libvulkan.so introuvable."

    log "Vulkan_LIBRARY : ${VULKAN_LIBRARY}"

    # -----------------------------------------------------------
    # pkg-config
    # -----------------------------------------------------------

    command -v pkg-config >/dev/null 2>&1 ||
        die "pkg-config introuvable."

    REQUIRED_PC_PACKAGES=(
        vulkan
        x11
        xinerama
        xcursor
        xi
        xrandr
        gl
    )

    for package in "${REQUIRED_PC_PACKAGES[@]}"; do
        if pkg-config --exists "${package}"; then
            log "pkg-config OK : ${package} ($(pkg-config --modversion "${package}"))"
        else
            die "Package pkg-config manquant : ${package}"
        fi
    done

    # -----------------------------------------------------------
    # Vérification ICD
    # -----------------------------------------------------------

    ICD_FILES=(/usr/share/vulkan/icd.d/*.json)

    if [[ -f "${ICD_FILES[0]}" ]]; then
        log "ICD Vulkan détectés :"
        printf '  %s\n' "${ICD_FILES[@]}"
    else
        log "WARNING : aucun ICD Vulkan système détecté."
    fi

    # -----------------------------------------------------------
    # Vérification runtime
    # -----------------------------------------------------------

    if command -v vulkaninfo >/dev/null 2>&1; then
        if vulkaninfo --summary; then
            log "Runtime Vulkan accessible."
        else
            log "WARNING : vulkaninfo ne peut pas initialiser Vulkan."
        fi
    else
        log "WARNING : vulkaninfo indisponible."
    fi

    # -----------------------------------------------------------
    # Flags compilateur
    # -----------------------------------------------------------

    export CFLAGS="${CFLAGS:-} -I${CONDA_PREFIX}/include -I/usr/include"
    export CXXFLAGS="${CXXFLAGS:-} -I${CONDA_PREFIX}/include -I/usr/include"

    log "CFLAGS : ${CFLAGS}"
    log "CXXFLAGS : ${CXXFLAGS}"

    log "Vérification des dépendances Linux terminée."

fi

# ===============================================================
# Mise à jour du dépôt Spirula
# ===============================================================

if [[ -d "${REPO_DIR}/.git" ]]; then

    log "Mise à jour du dépôt Spirula"

    git -C "${REPO_DIR}" pull --ff-only

else

    if [[ -e "${REPO_DIR}" ]]; then
        die "Le chemin existe mais n'est pas un dépôt Git : ${REPO_DIR}"
    fi

    log "Clonage de Spirula"

    git clone "${SPIRULA_URL}" "${REPO_DIR}"

fi

# ===============================================================
# Sous-modules
# ===============================================================

log "Mise à jour des sous-modules"

git -C "${REPO_DIR}" submodule sync --recursive
git -C "${REPO_DIR}" submodule update --init --recursive

# ===============================================================
# Vérification du script de compilation
# ===============================================================

BUILD_SCRIPT="${REPO_DIR}/build_develop.bash"

[[ -f "${BUILD_SCRIPT}" ]] ||
    die "Script de compilation introuvable : ${BUILD_SCRIPT}"

# ===============================================================
# Préparation de la compilation
# ===============================================================

BUILD_PATH="${REPO_DIR}/${BUILD_DIR}"

if [[ "${CLEAN}" -eq 1 && -d "${BUILD_PATH}" ]]; then
    log "Suppression du répertoire de compilation : ${BUILD_PATH}"
    rm -rf "${BUILD_PATH}"
fi

# ===============================================================
# Vérification Vulkan avant CMake
# ===============================================================

if [[ "${OS}" == "Linux" ]]; then

    log "Contrôle final des ressources Vulkan"

    [[ -r "${VULKAN_INCLUDE_DIR}/vulkan/vulkan.h" ]] ||
        die "Header Vulkan inaccessible : ${VULKAN_INCLUDE_DIR}/vulkan/vulkan.h"

    [[ -r "${VULKAN_INCLUDE_DIR}/vulkan/vulkan_core.h" ]] ||
        die "Header vulkan_core.h inaccessible."

    [[ -e "${VULKAN_LIBRARY}" ]] ||
        die "Bibliothèque Vulkan inaccessible : ${VULKAN_LIBRARY}"

    log "Headers Vulkan : OK"
    log "Bibliothèque Vulkan : OK"

fi

# ===============================================================
# Compilation de Spirula
# ===============================================================

log "Compilation de Spirula"

cd "${REPO_DIR}"

BUILD_CMD=(
    bash build_develop.bash
    -DSS_BACKEND=vulkan
    -DSS_BUILD_SAM=ON
    -DCMAKE_BUILD_TYPE=Release
)

if [[ "${OS}" == "Linux" ]]; then
    BUILD_CMD+=(
        "-DVulkan_INCLUDE_DIR:PATH=${VULKAN_INCLUDE_DIR}"
        "-DVulkan_LIBRARY:FILEPATH=${VULKAN_LIBRARY}"
    )
fi

log "Commande de compilation :"
printf '  %q' "${BUILD_CMD[@]}"
echo

log "Parallélisme CMake : ${CMAKE_BUILD_PARALLEL_LEVEL}"

"${BUILD_CMD[@]}"

# ===============================================================
# Vérification de l'exécutable
# ===============================================================

SPIRULA_BIN="${BUILD_PATH}/spirula"

if [[ ! -x "${SPIRULA_BIN}" ]]; then
    die "Exécutable Spirula introuvable ou non exécutable : ${SPIRULA_BIN}"
fi

log "Exécutable Spirula : ${SPIRULA_BIN}"

"${SPIRULA_BIN}" --version

# ===============================================================
# Vérification et téléchargement du modèle SAM3
# ===============================================================

log "Vérification du modèle SAM3"

MODEL_DIR="$(dirname "${SAM3_MODEL_PATH}")"
mkdir -p "${MODEL_DIR}"

if [[ -s "${SAM3_MODEL_PATH}" ]]; then

    log "Modèle SAM3 déjà présent : ${SAM3_MODEL_PATH}"

else

    log "Téléchargement du modèle SAM3"

    if command -v curl >/dev/null 2>&1; then

        curl -fL \
            --retry 3 \
            --retry-delay 2 \
            "${SAM3_MODEL_URL}" \
            -o "${SAM3_MODEL_PATH}"

    elif command -v wget >/dev/null 2>&1; then

        wget \
            --tries=3 \
            "${SAM3_MODEL_URL}" \
            -O "${SAM3_MODEL_PATH}"

    else
        die "curl ou wget est nécessaire pour télécharger le modèle SAM3."
    fi

fi

[[ -s "${SAM3_MODEL_PATH}" ]] ||
    die "Modèle SAM3 absent ou vide : ${SAM3_MODEL_PATH}"

log "Modèle SAM3 OK : ${SAM3_MODEL_PATH}"
log "Taille : $(du -h "${SAM3_MODEL_PATH}" | awk '{print $1}')"

# ===============================================================
# Variables d'environnement
# ===============================================================

log "Configuration des variables d'environnement"

cat <<EOF

===============================================================
Installation Spirula terminée
===============================================================

SPIRULA_ROOT="${REPO_DIR}"
SPIRULA_BUILD="${BUILD_PATH}"
SPIRULA_BIN="${SPIRULA_BIN}"
SPIRULA_SAM3_MODEL="${SAM3_MODEL_PATH}"

CMAKE_BUILD_PARALLEL_LEVEL="${CMAKE_BUILD_PARALLEL_LEVEL}"

Pour utiliser ces chemins dans ton environnement :

export SPIRULA_ROOT="${REPO_DIR}"
export SPIRULA_BUILD="${BUILD_PATH}"
export SPIRULA_BIN="${SPIRULA_BIN}"
export SPIRULA_SAM3_MODEL="${SAM3_MODEL_PATH}"

===============================================================

EOF

log "Installation terminée avec succès."
log "Log complet : ${LOG_FILE}"
