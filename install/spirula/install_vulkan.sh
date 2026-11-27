
#!/usr/bin/env bash

set -Eeuo pipefail

# ===============================================================
# Configuration
# ===============================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

export DEBIAN_FRONTEND=noninteractive

# ===============================================================
# Logging
# ===============================================================

log() {
    echo "[VULKAN] $*"
}

die() {
    echo "[VULKAN][ERROR] $*" >&2
    exit 1
}

on_error() {
    local status=$?
    local line="${1:-?}"

    echo
    log "[ERROR] Échec ligne ${line} (code ${status})."
    exit "${status}"
}

trap 'on_error $LINENO' ERR

log "Installation des dépendances"
log "Projet : ${PROJECT_ROOT}"

# ===============================================================
# Contrôle de l'environnement
# ===============================================================

command -v apt-get >/dev/null 2>&1 ||
    die "apt-get requis."

[[ "${EUID}" -eq 0 ]] ||
    die "Exécution root obligatoire."

# ===============================================================
# Installation des dépendances
# ===============================================================

log "Mise à jour des dépôts"

apt-get update

log "Installation Vulkan, X11, Wayland et OpenGL"

PACKAGES=(
    # Build tools
    build-essential
    pkg-config
    cmake
    ninja-build
    git
    curl
    wget

    # Vulkan
    libvulkan1
    libvulkan-dev
    mesa-vulkan-drivers
    vulkan-tools

    # X11
    libx11-dev
    libxext-dev
    libxrender-dev
    libxinerama-dev
    libxcursor-dev
    libxi-dev
    libxrandr-dev
    libxfixes-dev

    # Wayland
    libwayland-dev
    libwayland-egl1
    wayland-protocols
    libxkbcommon-dev

    # OpenGL / GLX / EGL
    libgl1-mesa-dev
    libglx-dev
    libopengl-dev
    libegl-dev
    libgl-dev
    libglu1-mesa-dev
)

apt-get install -y --no-install-recommends "${PACKAGES[@]}"

# ===============================================================
# Actualisation du cache des bibliothèques
# ===============================================================

log "Actualisation du cache des bibliothèques"

ldconfig

# ===============================================================
# Vérification des headers
# ===============================================================

log "Vérification des headers"

REQUIRED_HEADERS=(
    # Vulkan
    /usr/include/vulkan/vulkan.h
    /usr/include/vulkan/vulkan_core.h
    /usr/include/vulkan/vk_platform.h

    # X11
    /usr/include/X11/X.h
    /usr/include/X11/Xlib.h
    /usr/include/X11/Xatom.h
    /usr/include/X11/Xresource.h
    /usr/include/X11/keysym.h
    /usr/include/X11/extensions/Xrandr.h
    /usr/include/X11/extensions/Xinerama.h
    /usr/include/X11/extensions/XInput2.h
    /usr/include/X11/Xcursor/Xcursor.h

    # OpenGL
    /usr/include/GL/gl.h
    /usr/include/GL/glx.h

    # EGL
    /usr/include/EGL/egl.h

    # Wayland
    /usr/include/wayland-client.h
    /usr/include/wayland-egl.h

    # XKB
    /usr/include/xkbcommon/xkbcommon.h
)

for header in "${REQUIRED_HEADERS[@]}"; do
    [[ -r "${header}" ]] ||
        die "Header manquant ou illisible : ${header}"

    log "Header OK : ${header}"
done

# ===============================================================
# Vérification pkg-config
# ===============================================================

log "Vérification pkg-config"

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
    egl
    wayland-client
    wayland-egl
    xkbcommon
)

for package in "${REQUIRED_PC_PACKAGES[@]}"; do
    if pkg-config --exists "${package}"; then
        log "pkg-config OK : ${package} ($(pkg-config --modversion "${package}"))"
    else
        die "Package pkg-config manquant : ${package}"
    fi
done

# ===============================================================
# Vérification des bibliothèques
# ===============================================================

log "Vérification des bibliothèques système"

# Les bibliothèques de développement (.so) et les bibliothèques
# runtime (.so.N) sont vérifiées séparément.
#
# ldconfig -p ne référence pas nécessairement les liens
# de développement .so.

find_library() {
    local library="$1"

    # Cache dynamique
    local found

    found="$(ldconfig -p | awk -v lib="${library}" \
        '$1 == lib { print $NF; exit }')"

    if [[ -n "${found}" && -e "${found}" ]]; then
        echo "${found}"
        return 0
    fi

    # Emplacements standards
    local paths=(
        "/usr/lib/x86_64-linux-gnu/${library}"
        "/usr/lib/${library}"
        "/lib/x86_64-linux-gnu/${library}"
        "/lib/${library}"
        "/usr/local/lib/${library}"
    )

    for path in "${paths[@]}"; do
        if [[ -e "${path}" ]]; then
            echo "${path}"
            return 0
        fi
    done

    return 1
}

REQUIRED_LIBRARIES=(
    libvulkan.so
    libvulkan.so.1
    libX11.so
    libXext.so
    libXrandr.so
    libXinerama.so
    libXcursor.so
    libXi.so
    libGL.so
    libGLX.so
    libOpenGL.so
    libEGL.so
    libwayland-client.so
    libwayland-egl.so
)

for library in "${REQUIRED_LIBRARIES[@]}"; do
    if library_path="$(find_library "${library}")"; then
        log "Bibliothèque OK : ${library} (${library_path})"
    else
        die "Bibliothèque absente : ${library}"
    fi
done

# ===============================================================
# Validation spécifique de Vulkan pour CMake
# ===============================================================

log "Validation de Vulkan pour CMake"

VULKAN_INCLUDE_DIR="/usr/include"
VULKAN_HEADER="${VULKAN_INCLUDE_DIR}/vulkan/vulkan.h"

[[ -r "${VULKAN_HEADER}" ]] ||
    die "Vulkan_INCLUDE_DIR invalide : ${VULKAN_HEADER}"

VULKAN_LIBRARY="$(find_library libvulkan.so)" ||
    die "Bibliothèque de développement libvulkan.so introuvable."

log "Vulkan_INCLUDE_DIR : ${VULKAN_INCLUDE_DIR}"
log "Vulkan_LIBRARY : ${VULKAN_LIBRARY}"

# ===============================================================
# Vérification de compilation Vulkan
# ===============================================================

if command -v c++ >/dev/null 2>&1; then

    printf '#include <vulkan/vulkan.h>\nint main() { return 0; }\n' |
        c++ -x c++ -fsyntax-only - ||
        die "Impossible de compiler un programme incluant vulkan/vulkan.h."

    log "Compilation test des headers Vulkan OK"

else
    die "Compilateur C++ introuvable."
fi

# ===============================================================
# ICD Vulkan
# ===============================================================

log "Vérification des ICD Vulkan"

shopt -s nullglob

ICD_FILES=(/usr/share/vulkan/icd.d/*.json)

if (( ${#ICD_FILES[@]} > 0 )); then
    for icd in "${ICD_FILES[@]}"; do
        log "ICD Vulkan : ${icd}"
    done
else
    log "WARNING : aucun ICD Vulkan détecté."
fi

# ===============================================================
# Runtime Vulkan
# ===============================================================

log "Vérification du runtime Vulkan"

if command -v vulkaninfo >/dev/null 2>&1; then

    if vulkaninfo --summary; then
        log "Runtime Vulkan accessible."
    else
        log "WARNING : runtime Vulkan non opérationnel."
        log "Les dépendances de compilation sont néanmoins installées."
    fi

else
    log "WARNING : vulkaninfo indisponible."
fi

# ===============================================================
# Résumé
# ===============================================================

log "==============================================================="
log "Installation Vulkan terminée et validée"
log "==============================================================="

log "Headers Vulkan : ${VULKAN_INCLUDE_DIR}"
log "Bibliothèque Vulkan : ${VULKAN_LIBRARY}"
log "X11 : OK"
log "OpenGL : OK"
log "EGL : OK"
log "Wayland : OK"
log "pkg-config : OK"

log "==============================================================="
