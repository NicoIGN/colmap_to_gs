
%%bash
set -euo pipefail

cd /content/colmap_to_gs

# Création du fichier
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
    mesa-vulkan-drivers

test -f /usr/include/vulkan/vulkan.h

ldconfig -p | grep -q 'libvulkan.so.1'

echo "[VULKAN] Headers et loader disponibles."

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
