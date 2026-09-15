########################################
# PERFORMANCE PROFILE
########################################

TRAINING_PROFILE="gpu/fast"

DEVICE="gpu"
MODEL="splatfacto"
MODEL_IMPLEMENTATION="tcnn"
TRAIN_VIS_MODE="tensorboard"

########################################
# IMAGE / PREPROCESSING
########################################

CAMERA_RES_SCALE_FACTOR=0.5
MAX_RES=768

NUM_DOWNSCALES=2
SKIP_IMAGE_PROCESSING=true

########################################
# TRAINING
########################################

MAX_ITER=3500
STOP_SPLIT_AT=3500

TRAIN_RAYS_PER_BATCH=384

NUM_NERF_SAMPLES_PER_RAY=24
NUM_PROPOSAL_SAMPLES_PER_RAY="48 24"

########################################
# GAUSSIAN SPLATTING (FAST SAFE)
########################################

# plus permissif pour éviter le collapse à 0 GS
DENSIFY_GRAD_THRESH=0.00025

# culling moins agressif en début de train
CULL_ALPHA_THRESH=0.02
CULL_SCALE_THRESH=0.10
CULL_SCREEN_SIZE=0.15
SPLIT_SCREEN_SIZE=0.03

# raffinement plus fréquent mais plus stable
REFINE_EVERY=200
RESET_ALPHA_EVERY=100

########################################
# QUALITY / REGULARIZATION
########################################

USE_BILATERAL_GRID=true
USE_SCALE_REGULARIZATION=false

MAX_GAUSS_RATIO=4.0
SSIM_LAMBDA=0.20

########################################
# EXPORT FAST
########################################

EXPORT_NUM_POINTS=300000
EXPORT_DOWNSAMPLE=2
