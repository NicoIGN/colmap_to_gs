#!/bin/bash
set -e

LOG_FILE="torch_stack_check.log"
echo "🧪 Torch stack validation starting..." | tee "$LOG_FILE"

run_check () {
  echo "→ $1"
  eval "$1" >> "$LOG_FILE" 2>&1 || {
    echo "❌ FAILED: $1" | tee -a "$LOG_FILE"
    exit 1
  }
}

# ======================
# CORE CHECKS
# ======================
run_check "python -c \"import torch; print('torch', torch.__version__, torch.version.cuda, torch.cuda.is_available())\""
run_check "python -c \"import torchvision; print('torchvision', torchvision.__version__)\""
run_check "python -c \"import torchaudio; print('torchaudio', torchaudio.__version__)\""

# ======================
# TORCHVISION OP CHECK (IMPORTANT BUG DETECTION)
# ======================
run_check "python -c \"import torch; import torchvision; from torchvision.ops import nms; print('torchvision.ops.nms OK')\""

# ======================
# OPTIONAL LIBS
# ======================
run_check "python -c \"import torchmetrics; print('torchmetrics', torchmetrics.__version__)\""
run_check "python -c \"import nerfstudio; print('nerfstudio', nerfstudio.__version__)\""
run_check "python -c \"import gsplat; print('gsplat', gsplat.__version__)\""

echo "✅ Torch stack OK" | tee -a "$LOG_FILE"
exit 0
