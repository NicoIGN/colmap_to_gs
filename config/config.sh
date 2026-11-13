############################
# PIPELINE CONTROL (SKIP FLAGS)
############################

SKIP_CONDA_UPDATE=true
SKIP_TRAINING=false
SKIP_EXPORT=false

# NO_PROXY=true

############################
# CONDA
############################

CONDA_ENV_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/environment/ign.slurm/conda_env.yml"
CONDA_ENV_NAME="gsplat"

############################
# PROXY
############################

if [ "$NO_PROXY" != true ]; then
  export HTTP_PROXY="http://proxy.ign.fr:3128"
  export HTTPS_PROXY="http://proxy.ign.fr:3128"
  export http_proxy="$HTTP_PROXY"
  export https_proxy="$HTTPS_PROXY"
  echo "🌐 Proxy enabled"
else
  echo "🚫 Proxy disabled (NO_PROXY=true)"
  unset HTTP_PROXY
  unset HTTPS_PROXY
  unset http_proxy
  unset https_proxy
fi

############################
# EXECUTION ENV
############################

if [ -z "${OUTPUT_ROOT+x}" ]; then
  OUTPUT_ROOT="runs/default"
fi

export SCENE_NAME="scene3d"

############################
# FIXED RUNTIME (downstream splatfacto)
############################

# Forcé dans run_downstream.sh, rappelé ici pour cohérence config:
if [ -z "${DEVICE+x}" ]; then
  DEVICE="gpu"
fi
export DEVICE

EXPERIMENT_NAME="model3d"

if [ -z "${MODEL+x}" ]; then
  MODEL="splatfacto"
fi

# Backend nerfstudio
if [ -z "${TRAIN_NS_MODEL_IMPLEMENTATION+x}" ]; then
  TRAIN_NS_MODEL_IMPLEMENTATION="torch"
  # torch -> fonctionne partout (fallback sûr)
  # tcnn  -> GPU only (tiny-cuda-nn), plus rapide si stack compatible
fi

if [ -z "${BASENAME+x}" ]; then
  BASENAME="model3d"
fi

############################
# TRAINING PARAMETERS
############################

if [ -z "${TRAIN_NS_STEPS_PER_SAVE+x}" ]; then
  TRAIN_NS_STEPS_PER_SAVE=5000
fi

if [ -z "${TRAIN_NS_STEPS_PER_EVAL_ALL_IMAGES+x}" ]; then
  TRAIN_NS_STEPS_PER_EVAL_ALL_IMAGES=2000
fi

if [ -z "${TRAIN_NS_REFINE_EVERY+x}" ]; then
  TRAIN_NS_REFINE_EVERY=100
fi

if [ -z "${TRAIN_NS_MAX_ITER+x}" ]; then
  TRAIN_NS_MAX_ITER=3000
fi

if [ -z "${TRAIN_NS_MAX_JOBS+x}" ]; then
  TRAIN_NS_MAX_JOBS=2
fi

############################
# IMAGE / DATA RESOLUTION
############################

if [ -z "${TRAIN_NS_CAMERA_RES_SCALE_FACTOR+x}" ]; then
  TRAIN_NS_CAMERA_RES_SCALE_FACTOR=0.5
fi

if [ -z "${TRAIN_NS_NUM_DOWNSCALES+x}" ]; then
  TRAIN_NS_NUM_DOWNSCALES=0
fi

if [ -z "${TRAIN_NS_MAX_RES+x}" ]; then
  unset TRAIN_NS_MAX_RES
fi

############################
# RAYS / SAMPLING
############################

if [ -z "${TRAIN_RAYS_PER_BATCH+x}" ]; then
  unset TRAIN_RAYS_PER_BATCH
fi

if [ -z "${TRAIN_NS_NUM_NERF_SAMPLES_PER_RAY+x}" ]; then
  unset TRAIN_NS_NUM_NERF_SAMPLES_PER_RAY
fi

if [ -z "${TRAIN_NS_NUM_PROPOSAL_SAMPLES_PER_RAY+x}" ]; then
  unset TRAIN_NS_NUM_PROPOSAL_SAMPLES_PER_RAY
fi

############################
# GAUSSIAN SPLATTING TUNING
############################

if [ -z "${TRAIN_NS_DENSIFY_GRAD_THRESH+x}" ]; then
  unset TRAIN_NS_DENSIFY_GRAD_THRESH
fi

if [ -z "${TRAIN_NS_CULL_ALPHA_THRESH+x}" ]; then
  unset TRAIN_NS_CULL_ALPHA_THRESH
fi

if [ -z "${TRAIN_NS_CULL_SCREEN_SIZE+x}" ]; then
  unset TRAIN_NS_CULL_SCREEN_SIZE
fi

if [ -z "${TRAIN_NS_SPLIT_SCREEN_SIZE+x}" ]; then
  unset TRAIN_NS_SPLIT_SCREEN_SIZE
fi

if [ -z "${TRAIN_NS_MAX_GAUSS_RATIO+x}" ]; then
  unset TRAIN_NS_MAX_GAUSS_RATIO
fi

if [ -z "${TRAIN_NS_STOP_SPLIT_AT+x}" ]; then
  unset TRAIN_NS_STOP_SPLIT_AT
fi

if [ -z "${TRAIN_NS_RESET_ALPHA_EVERY+x}" ]; then
  unset TRAIN_NS_RESET_ALPHA_EVERY
fi

if [ -z "${TRAIN_NS_USE_SCALE_REGULARIZATION+x}" ]; then
  unset TRAIN_NS_USE_SCALE_REGULARIZATION
fi

if [ -z "${TRAIN_NS_USE_BILATERAL_GRID+x}" ]; then
  unset TRAIN_NS_USE_BILATERAL_GRID
fi

if [ -z "${TRAIN_NS_CULL_SCALE_THRESH+x}" ]; then
  unset TRAIN_NS_CULL_SCALE_THRESH
fi

if [ -z "${TRAIN_NS_SSIM_LAMBDA+x}" ]; then
  unset TRAIN_NS_SSIM_LAMBDA
fi

############################
# TRAINING VIS MODE
############################

if [ -z "${TRAIN_VIS_MODE+x}" ]; then
  TRAIN_VIS_MODE="tensorboard"
fi

############################
# EXPORT CONFIG
############################

NORMAL_METHOD="open3d"
REMOVE_OUTLIERS=True
TRAIN_NS_MIXED_PRECISION=False
TRAIN_NS_USE_GRAD_SCALER=False
