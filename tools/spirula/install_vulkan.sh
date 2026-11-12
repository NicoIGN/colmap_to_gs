
%%bash
set -Eeuo pipefail

cd /content/colmap_to_gs

# Création du script d'installation Vulkan
cat > tools/spirula/install_vulkan.sh <<'EOF'
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
    libxrandr-dev

# Vérification des headers Vulkan
test -f /usr/include/vulkan/vulkan.h

# Vérification des headers X11 requis par GLFW
for header in \
    X11/Xlib.h \
    X11/extensions/Xinerama.h \
    X11/extensions/Xrandr.h \
    X11/extensions/XInput2.h \
    X11/extensions/Xfixes.h
do
    if [[ ! -f "/usr/include/${header}" ]]; then
        echo "[ERROR] Header manquant : /usr/include/${header}"
        exit 1
    fi
done

# Vérification du loader Vulkan
ldconfig -p | grep -q 'libvulkan.so.1'

echo "[VULKAN] Headers Vulkan et X11 disponibles."
echo "[VULKAN] Loader Vulkan disponible."

echo "=== ICD Vulkan ==="

if compgen -G "/usr/share/vulkan/icd.d/*.json" > /dev/null; then
    ls -l /usr/share/vulkan/icd.d/*.json
else
    echo "[WARNING] Aucun ICD détecté."
fi

echo "=== Runtime ==="
vulkaninfo --summary || echo "[WARNING] Runtime Vulkan non opérationnel."

echo "[VULKAN] Installation terminée."
EOF

chmod +x tools/spirula/install_vulkan.sh

# Exécution
bash tools/spirula/install_vulkan.sh
