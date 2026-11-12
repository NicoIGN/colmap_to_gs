
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

for library in \
    libvulkan.so.1 \
    libX11.so \
    libXinerama.so \
    libXcursor.so \
    libXi.so \
    libXrandr.so \
    libGL.so \
    libGLX.so \
    libOpenGL.so \
    libEGL.so
do
    if ! ldconfig -p | grep -Fq "${library}"; then
        echo "[ERROR] Bibliothèque manquante : ${library}"
        exit 1
    fi

    echo "[OK] ${library}"
done

echo "[VULKAN] Toutes les dépendances de compilation sont disponibles."

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
