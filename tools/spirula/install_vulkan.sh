
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

echo "[VULKAN] Installation Vulkan et dépendances GLFW/X11"

apt-get update

apt-get install -y --no-install-recommends \
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
    wayland-protocols

echo "[VULKAN] Vérification des dépendances GLFW"

if ! pkg-config --exists xinerama; then
    echo "[ERROR] Xinerama non détecté par pkg-config."
    exit 1
fi

test -f /usr/include/X11/extensions/Xinerama.h
test -f /usr/include/vulkan/vulkan.h

echo "[VULKAN] Dépendances Vulkan et GLFW disponibles."

echo "=== ICD Vulkan ==="

if compgen -G "/usr/share/vulkan/icd.d/*.json" > /dev/null; then
    ls -l /usr/share/vulkan/icd.d/*.json
else
    echo "[WARNING] Aucun ICD détecté."
fi

echo "=== Runtime ==="

vulkaninfo --summary || \
    echo "[WARNING] Runtime Vulkan non opérationnel."

echo "[VULKAN] Installation terminée."
