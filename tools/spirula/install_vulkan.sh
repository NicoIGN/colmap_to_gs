
#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

log() {
    echo "[VULKAN] $*"
}

die() {
    echo "[ERROR] $*" >&2
    exit 1
}

log "Installation des dépendances"
log "Projet : ${PROJECT_ROOT}"

# ===============================================================
# Contrôle de l'environnement
# ===============================================================

if ! command -v apt-get >/dev/null 2>&1; then
    die "apt-get requis."
fi

if [[ "${EUID}" -ne 0 ]]; then
    die "Exécution root obligatoire."
fi

export DEBIAN_FRONTEND=noninteractive

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

    # Vulkan
    libvulkan1
    libvulkan-dev
    mesa-vulkan-drivers
    vulkan-tools

    # X11 / Xorg
    xorgproto
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

ldconfig

# ===============================================================
# Vérification des headers
# ===============================================================

log "Vérification des headers"

REQUIRED_HEADERS=(
    /usr/include/vulkan/vulkan.h

    /usr/include/X11/X.h
    /usr/include/X11/Xlib.h
    /usr/include/X11/extensions/Xinerama.h
    /usr/include/X11/extensions/Xrandr.h
    /usr/include/X11/extensions/XInput2.h

    /usr/include/GL/gl.h
    /usr/include/GL/glx.h

    /usr/include/EGL/egl.h

    /usr/include/wayland-client.h
    /usr/include/xkbcommon/xkbcommon.h
)

for header in "${REQUIRED_HEADERS[@]}"; do
    if [[ ! -f "${header}" ]]; then
        die "Header manquant : ${header}"
    fi

    log "Header OK : ${header}"
done

# ===============================================================
# Vérification pkg-config
# ===============================================================

log "Vérification pkg-config"

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

REQUIRED_LIBRARIES=(
    libvulkan.so.1
    libvulkan.so
    libX11.so
    libXinerama.so
    libXcursor.so
    libXi.so
    libXrandr.so
    libGL.so
    libGLX.so
    libOpenGL.so
    libEGL.so
    libwayland-client.so
    libwayland-egl.so
)

for library in "${REQUIRED_LIBRARIES[@]}"; do
    if ldconfig -p | grep -Fq "${library}"; then
        log "Bibliothèque OK : ${library}"
    else
        die "Bibliothèque absente : ${library}"
    fi
done

# ===============================================================
# ICD Vulkan
# ===============================================================

log "Vérification des ICD Vulkan"

ICD_FILES=(/usr/share/vulkan/icd.d/*.json)

if [[ -f "${ICD_FILES[0]}" ]]; then
    ls -l "${ICD_FILES[@]}"
else
    log "WARNING : aucun ICD Vulkan détecté."
fi

# ===============================================================
# Runtime Vulkan
# ===============================================================

log "Vérification du runtime Vulkan"

if command -v vulkaninfo >/dev/null 2>&1; then
    if ! vulkaninfo --summary; then
        log "WARNING : runtime Vulkan non opérationnel."
    fi
else
    log "WARNING : vulkaninfo indisponible."
fi


# ===============================================================
# Validation finale des dépendances Vulkan
# ===============================================================

log "Validation finale des dépendances Vulkan"

# Headers Vulkan indispensables à FindVulkan.cmake
REQUIRED_VULKAN_HEADERS=(
    /usr/include/vulkan/vulkan.h
    /usr/include/vulkan/vulkan_core.h
    /usr/include/vulkan/vk_platform.h
)

for header in "${REQUIRED_VULKAN_HEADERS[@]}"; do
    [[ -r "${header}" ]] || die "Header Vulkan absent ou illisible : ${header}"
    log "Header Vulkan OK : ${header}"
done

# pkg-config
pkg-config --exists vulkan || die "Package pkg-config vulkan introuvable"

VULKAN_PC_INCLUDEDIR="$(pkg-config --variable=includedir vulkan)"

[[ -r "${VULKAN_PC_INCLUDEDIR}/vulkan/vulkan.h" ]] ||
    die "Header Vulkan absent du chemin pkg-config : ${VULKAN_PC_INCLUDEDIR}"

log "Vulkan pkg-config OK : $(pkg-config --modversion vulkan)"
log "Vulkan include directory : ${VULKAN_PC_INCLUDEDIR}"

# Bibliothèque de développement
VULKAN_LIBRARY="/usr/lib/x86_64-linux-gnu/libvulkan.so"

if [[ ! -e "${VULKAN_LIBRARY}" ]]; then
    VULKAN_LIBRARY="$(ldconfig -p | awk '/libvulkan\.so \(/{print $NF; exit}')"
fi

[[ -n "${VULKAN_LIBRARY}" && -e "${VULKAN_LIBRARY}" ]] ||
    die "Bibliothèque de développement libvulkan.so introuvable"

log "Vulkan library OK : ${VULKAN_LIBRARY}"

# ICD
ICD_FILES=(/usr/share/vulkan/icd.d/*.json)

if [[ ! -f "${ICD_FILES[0]}" ]]; then
    die "Aucun ICD Vulkan installé dans /usr/share/vulkan/icd.d/"
fi

log "ICD Vulkan détectés :"
printf '  %s\n' "${ICD_FILES[@]}"

log "Toutes les dépendances Vulkan sont installées et vérifiées."




log "Installation et vérifications terminées."
