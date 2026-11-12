
#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

echo "[VULKAN] Installation des dépendances"
echo "[VULKAN] Projet : ${PROJECT_ROOT}"

# ===============================================================
# Contrôle de l'environnement
# ===============================================================

if ! command -v apt-get >/dev/null 2>&1; then
    echo "[ERROR] apt-get requis."
    exit 1
fi

if [[ "${EUID}" -ne 0 ]]; then
    echo "[ERROR] Exécution root obligatoire."
    exit 1
fi

export DEBIAN_FRONTEND=noninteractive

# ===============================================================
# Installation des dépendances
# ===============================================================

echo "[VULKAN] Mise à jour des dépôts"

apt-get update

echo "[VULKAN] Installation Vulkan, X11, Wayland et OpenGL"

apt-get install -y --no-install-recommends \
    build-essential \
    pkg-config \
    cmake \
    ninja-build \
    \
    # Vulkan
    libvulkan1 \
    libvulkan-dev \
    mesa-vulkan-drivers \
    vulkan-tools \
    \
    # X11 / Xorg
    xorgproto \
    libx11-dev \
    libxext-dev \
    libxrender-dev \
    libxinerama-dev \
    libxcursor-dev \
    libxi-dev \
    libxrandr-dev \
    libxfixes-dev \
    \
    # Wayland
    libwayland-dev \
    libwayland-egl1 \
    wayland-protocols \
    libxkbcommon-dev \
    \
    # OpenGL / GLX / EGL
    libgl1-mesa-dev \
    libglx-dev \
    libopengl-dev \
    libegl-dev \
    libgl-dev \
    libglu1-mesa-dev

# ===============================================================
# Vérification des headers
# ===============================================================

echo "[VULKAN] Vérification des headers"

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
        echo "[ERROR] Header manquant : ${header}"
        exit 1
    fi

    echo "[OK] ${header}"
done

# ===============================================================
# Vérification pkg-config
# ===============================================================

echo "[VULKAN] Vérification pkg-config"

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
        echo "[OK] ${package}: $(pkg-config --modversion "${package}")"
    else
        echo "[ERROR] Package pkg-config manquant : ${package}"
        exit 1
    fi
done

# ===============================================================
# Vérification des bibliothèques
# ===============================================================

echo "[VULKAN] Vérification des bibliothèques système"

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
        echo "[OK] ${library}"
    else
        echo "[ERROR] Bibliothèque absente : ${library}"
        exit 1
    fi
done

# ===============================================================
# ICD Vulkan
# ===============================================================

echo "[VULKAN] Vérification des ICD"

if compgen -G "/usr/share/vulkan/icd.d/*.json" > /dev/null; then
    ls -l /usr/share/vulkan/icd.d/*.json
else
    echo "[WARNING] Aucun ICD Vulkan détecté."
fi

# ===============================================================
# Runtime Vulkan
# ===============================================================

echo "[VULKAN] Vérification du runtime Vulkan"

if command -v vulkaninfo >/dev/null 2>&1; then
    vulkaninfo --summary || \
        echo "[WARNING] Runtime Vulkan non opérationnel."
else
    echo "[WARNING] vulkaninfo indisponible."
fi

echo "[VULKAN] Installation terminée."
