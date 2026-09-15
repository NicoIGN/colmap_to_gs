########################################
# PROFILE: force_split (agressif)
########################################

TRAINING_PROFILE="gpu/fast"

DEVICE="gpu"
MODEL="splatfacto"
MODEL_IMPLEMENTATION="tcnn"
TRAIN_VIS_MODE="tensorboard"

########################################
# IMAGE / PREPROCESSING
########################################

CAMERA_RES_SCALE_FACTOR=0.75
MAX_RES=1024
NUM_DOWNSCALES=1
SKIP_IMAGE_PROCESSING=true

########################################
# TRAINING
########################################

MAX_ITER=6000
STOP_SPLIT_AT=5500
TRAIN_RAYS_PER_BATCH=768

NUM_NERF_SAMPLES_PER_RAY=32
NUM_PROPOSAL_SAMPLES_PER_RAY="64 32"

########################################
# GAUSSIAN SPLATTING (FORCE DENSIFY)
########################################

# déclenche split très facilement
DENSIFY_GRAD_THRESH=0.00003
SPLIT_SCREEN_SIZE=0.008
REFINE_EVERY=50

# évite de tuer les splats trop tôt
CULL_ALPHA_THRESH=0.005
CULL_SCALE_THRESH=0.05
CULL_SCREEN_SIZE=0.05
RESET_ALPHA_EVERY=200

########################################
# QUALITY / REGULARIZATION
########################################

USE_BILATERAL_GRID=true
USE_SCALE_REGULARIZATION=false
MAX_GAUSS_RATIO=8.0
SSIM_LAMBDA=0.20

########################################
# EXPORT
########################################

EXPORT_NUM_POINTS=1200000
EXPORT_DOWNSAMPLE=1
