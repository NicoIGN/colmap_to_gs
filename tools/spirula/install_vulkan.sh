
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

echo "[VULKAN] Installation Vulkan, GLFW, X11, Wayland et OpenGL"

apt-get install -y --no-install-recommends \
    build-essential \
    pkg-config \
    cmake \
    libvulkan-dev \
    libvulkan1 \
    vulkan-tools \
    mesa-vulkan-drivers \
    libx11-dev \
    libxext-dev \
    libxrender-dev \
    libxinerama-dev \
    libxcursor-dev \
    libxi-dev \
    libxrandr-dev \
    libxfixes-dev \
    libwayland-dev \
    libwayland-egl1 \
    wayland-protocols \
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
    /usr/include/X11/Xlib.h
    /usr/include/X11/extensions/Xinerama.h
    /usr/include/X11/extensions/Xrandr.h
    /usr/include/X11/extensions/XInput2.h
    /usr/include/GL/gl.h
    /usr/include/GL/glx.h
    /usr/include/EGL/egl.h
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

for package in xinerama x11 gl glx egl; do
    if pkg-config --exists "${package}"; then
        echo "[OK] ${package}"
    else
        echo "[ERROR] Package pkg-config manquant : ${package}"
        exit 1
    fi
done

# ===============================================================
# Vérification des bibliothèques
# ===============================================================

echo "[VULKAN] Vérification des bibliothèques"

REQUIRED_PACKAGES=(
    vulkan
    x11
    xinerama
    xcursor
    xi
    xrandr
    gl
    glx
    egl
)

for package in "${REQUIRED_PACKAGES[@]}"; do
    if ! pkg-config --exists "${package}"; then
        echo "[ERROR] Dépendance manquante : ${package}"
        exit 1
    fi

    echo "[OK] ${package}: $(pkg-config --libs "${package}")"
done

# Vérification du loader Vulkan
if ! dpkg -L libvulkan1 | grep -Eq '/libvulkan\.so\.1$'; then
    echo "[ERROR] Loader Vulkan libvulkan.so.1 absent."
    exit 1
fi

# Vérification de la bibliothèque de développement
if ! dpkg -L libvulkan-dev | grep -Eq '/libvulkan\.so$'; then
    echo "[ERROR] Bibliothèque de développement Vulkan absente."
    exit 1
fi

echo "[OK] Loader et bibliothèque de développement Vulkan présents."
# ===============================================================
# ICD Vulkan
# ===============================================================

echo "=== ICD Vulkan ==="

if compgen -G "/usr/share/vulkan/icd.d/*.json" > /dev/null; then
    ls -l /usr/share/vulkan/icd.d/*.json
else
    echo "[WARNING] Aucun ICD détecté."
fi

# ===============================================================
# Runtime Vulkan
# ===============================================================

echo "=== Runtime Vulkan ==="

if command -v vulkaninfo >/dev/null 2>&1; then
    vulkaninfo --summary || \
        echo "[WARNING] Runtime Vulkan non opérationnel."
else
    echo "[WARNING] vulkaninfo indisponible."
fi

echo "[VULKAN] Installation terminée."
