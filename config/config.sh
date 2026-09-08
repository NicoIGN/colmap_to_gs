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

CONDA_ENV_FILE="environment/conda_colab.yml"
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
if [ -z "${MODEL_IMPLEMENTATION+x}" ]; then
  MODEL_IMPLEMENTATION="torch"
  # torch -> fonctionne partout (fallback sûr)
  # tcnn  -> GPU only (tiny-cuda-nn), plus rapide si stack compatible
fi

############################
# TRAINING PARAMETERS
############################

if [ -z "${STEPS_PER_SAVE+x}" ]; then
  STEPS_PER_SAVE=5000
fi

if [ -z "${STEPS_PER_EVAL_ALL_IMAGES+x}" ]; then
  STEPS_PER_EVAL_ALL_IMAGES=2000
fi

if [ -z "${REFINE_EVERY+x}" ]; then
  REFINE_EVERY=100
fi

if [ -z "${MAX_ITER+x}" ]; then
  MAX_ITER=3000
fi

if [ -z "${MAX_JOBS+x}" ]; then
  MAX_JOBS=2
fi

############################
# IMAGE / DATA RESOLUTION
############################

if [ -z "${CAMERA_RES_SCALE_FACTOR+x}" ]; then
  CAMERA_RES_SCALE_FACTOR=0.5
fi

if [ -z "${NUM_DOWNSCALES+x}" ]; then
  NUM_DOWNSCALES=0
fi

if [ -z "${MAX_RES+x}" ]; then
  unset MAX_RES
fi

############################
# RAYS / SAMPLING
############################

if [ -z "${TRAIN_RAYS_PER_BATCH+x}" ]; then
  unset TRAIN_RAYS_PER_BATCH
fi

if [ -z "${NUM_NERF_SAMPLES_PER_RAY+x}" ]; then
  unset NUM_NERF_SAMPLES_PER_RAY
fi

if [ -z "${NUM_PROPOSAL_SAMPLES_PER_RAY+x}" ]; then
  unset NUM_PROPOSAL_SAMPLES_PER_RAY
fi

############################
# GAUSSIAN SPLATTING TUNING
############################

if [ -z "${DENSIFY_GRAD_THRESH+x}" ]; then
  unset DENSIFY_GRAD_THRESH
fi

if [ -z "${CULL_ALPHA_THRESH+x}" ]; then
  unset CULL_ALPHA_THRESH
fi

if [ -z "${CULL_SCREEN_SIZE+x}" ]; then
  unset CULL_SCREEN_SIZE
fi

if [ -z "${SPLIT_SCREEN_SIZE+x}" ]; then
  unset SPLIT_SCREEN_SIZE
fi

if [ -z "${MAX_GAUSS_RATIO+x}" ]; then
  unset MAX_GAUSS_RATIO
fi

if [ -z "${STOP_SPLIT_AT+x}" ]; then
  unset STOP_SPLIT_AT
fi

if [ -z "${RESET_ALPHA_EVERY+x}" ]; then
  unset RESET_ALPHA_EVERY
fi

if [ -z "${USE_SCALE_REGULARIZATION+x}" ]; then
  unset USE_SCALE_REGULARIZATION
fi

if [ -z "${USE_BILATERAL_GRID+x}" ]; then
  unset USE_BILATERAL_GRID
fi

if [ -z "${CULL_SCALE_THRESH+x}" ]; then
  unset CULL_SCALE_THRESH
fi

if [ -z "${SSIM_LAMBDA+x}" ]; then
  unset SSIM_LAMBDA
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
MIXED_PRECISION=False
USE_GRAD_SCALER=False
