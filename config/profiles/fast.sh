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

# plus agressif que balanced pour accélérer
CAMERA_RES_SCALE_FACTOR=0.5
MAX_RES=768

NUM_DOWNSCALES=2
SKIP_IMAGE_PROCESSING=true

########################################
# TRAINING
########################################

MAX_ITER=3500
STOP_SPLIT_AT=3000

# batch réduit pour accélérer / réduire VRAM
TRAIN_RAYS_PER_BATCH=384

# moins d'échantillons = plus rapide
NUM_NERF_SAMPLES_PER_RAY=24
NUM_PROPOSAL_SAMPLES_PER_RAY="48 24"

########################################
# GAUSSIAN SPLATTING (FAST / FEWER SPLATS)
########################################

# densification encore moins agressive
DENSIFY_GRAD_THRESH=0.00065

# nettoyage plus strict
CULL_ALPHA_THRESH=0.15

# contrôle spatial plus agressif
CULL_SCREEN_SIZE=0.30
SPLIT_SCREEN_SIZE=0.03

# moins d'étapes de raffinement
REFINE_EVERY=400

# stabilisation / nettoyage fréquent
RESET_ALPHA_EVERY=30
CULL_SCALE_THRESH=0.45

########################################
# QUALITY / REGULARIZATION
########################################

USE_BILATERAL_GRID=true
USE_SCALE_REGULARIZATION=false

MAX_GAUSS_RATIO=3.5
SSIM_LAMBDA=0.20

########################################
# EXPORT FAST
########################################

EXPORT_NUM_POINTS=300000
EXPORT_DOWNSAMPLE=2
