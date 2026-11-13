########################################
# PERFORMANCE PROFILE - QUALITY++ REAL
########################################

TRAINING_PROFILE="gpu/quality_pp_real"

DEVICE="gpu"
MODEL="splatfacto"
TRAIN_NS_MODEL_IMPLEMENTATION="tcnn"
TRAIN_VIS_MODE="tensorboard"

########################################
# IMAGE / PREPROCESSING
########################################

TRAIN_NS_CAMERA_RES_SCALE_FACTOR=1.0
TRAIN_NS_MAX_RES=1536

TRAIN_NS_NUM_DOWNSCALES=0
SKIP_IMAGE_PROCESSING=true
TRAIN_NS_MAX_JOBS=2

########################################
# TRAINING
########################################

TRAIN_NS_MAX_ITER=18000

# ⚡ clé : ton 400 est beaucoup trop lent
TRAIN_NS_REFINE_EVERY=180

TRAIN_RAYS_PER_BATCH=768

TRAIN_NS_NUM_NERF_SAMPLES_PER_RAY=80
TRAIN_NS_NUM_PROPOSAL_SAMPLES_PER_RAY="160 80"

########################################
# GAUSSIAN SPLATTING (DETAIL DRIVEN)
########################################

# 🔥 énorme correction
TRAIN_NS_DENSIFY_GRAD_THRESH=0.0011

# garde le détail vivant
TRAIN_NS_CULL_ALPHA_THRESH=0.08

# évite blobs sans tuer micro-structure
TRAIN_NS_CULL_SCREEN_SIZE=0.24

# split adapté à 1536px
TRAIN_NS_SPLIT_SCREEN_SIZE=0.016

# laisse le modèle apprendre avant de figer
TRAIN_NS_STOP_SPLIT_AT=15000

########################################
# STABILITY CONTROL
########################################

TRAIN_NS_RESET_ALPHA_EVERY=45
TRAIN_NS_CULL_SCALE_THRESH=0.45
TRAIN_NS_MAX_GAUSS_RATIO=4.0

########################################
# QUALITY
########################################

TRAIN_NS_SSIM_LAMBDA=0.28
TRAIN_NS_USE_SCALE_REGULARIZATION=False

########################################
# EXPORT
########################################

EXPORT_NUM_POINTS=2500000
EXPORT_DOWNSAMPLE=1
