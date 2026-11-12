
#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

echo "[VULKAN] Installation des dépendances"
echo "[VULKAN] Projet : ${PROJECT_ROOT}"

if ! command -v apt-get >/dev/null 2>&1; then
    echo "[ERROR] apt-get requis."
    exit 1
fi

if [[ "${EUID}" -ne 0 ]]; then
    echo "[ERROR] Exécution root obligatoire."
    exit 1
fi

export DEBIAN_FRONTEND=noninteractive

echo "[VULKAN] Mise à jour des dépôts"
apt-get update

echo "[VULKAN] Installation Vulkan et dépendances GLFW/X11"

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
    wayland-protocols

echo "[VULKAN] Vérification des headers"

REQUIRED_HEADERS=(
    /usr/include/vulkan/vulkan.h
    /usr/include/X11/Xlib.h
    /usr/include/X11/extensions/Xinerama.h
    /usr/include/X11/extensions/Xrandr.h
    /usr/include/X11/extensions/XInput2.h
    /usr/include/X11/extensions/Xfixes.h
)

for header in "${REQUIRED_HEADERS[@]}"; do
    if [[ ! -f "${header}" ]]; then
        echo "[ERROR] Header manquant : ${header}"
        exit 1
    fi
    echo "[OK] ${header}"
done

echo "[VULKAN] Vérification des bibliothèques"

for library in \
    libvulkan.so \
    libX11.so \
    libXinerama.so \
    libXcursor.so \
    libXi.so \
    libXrandr.so \
    libXfixes.so
do
    if ! ldconfig -p | grep -q "${library}"; then
        echo "[ERROR] Bibliothèque manquante : ${library}"
        exit 1
    fi
    echo "[OK] ${library}"
done

echo "[VULKAN] Headers et bibliothèques disponibles."

echo "=== ICD Vulkan ==="

if compgen -G "/usr/share/vulkan/icd.d/*.json" > /dev/null; then
    ls -l /usr/share/vulkan/icd.d/*.json
else
    echo "[WARNING] Aucun ICD détecté."
fi

echo "=== Runtime ==="

if command -v vulkaninfo >/dev/null 2>&1; then
    vulkaninfo --summary || \
        echo "[WARNING] Runtime Vulkan non opérationnel."
fi

echo "[VULKAN] Installation terminée."
