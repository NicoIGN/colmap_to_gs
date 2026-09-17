########################################
# PROFILE: fast_moderate
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
MAX_RES=1024
NUM_DOWNSCALES=1
SKIP_IMAGE_PROCESSING=true

########################################
# TRAINING
########################################

MAX_ITER=3000
STOP_SPLIT_AT=2000
TRAIN_RAYS_PER_BATCH=768

NUM_NERF_SAMPLES_PER_RAY=16
NUM_PROPOSAL_SAMPLES_PER_RAY="32 16"

########################################
# GAUSSIAN SPLATTING
# DENSIFICATION CONTROLEE
########################################

# Ancien fast : 0.00003
# Valeur 10x plus stricte : moins de Gaussians dépassent le seuil gradient.
DENSIFY_GRAD_THRESH=0.00030

# Ancien fast : 0.008
# Augmenté : évite les splits de petits Gaussians à l'écran.
SPLIT_SCREEN_SIZE=0.020

# Ancien fast : 50
# Seulement un raffinement toutes les 150 itérations.
REFINE_EVERY=150

########################################
# CULLING / PRUNING
########################################

# Élimine plus tôt les Gaussians quasi transparents.
CULL_ALPHA_THRESH=0.04

# Seuil modéré pour éliminer les splats devenus anormalement étendus.
CULL_SCALE_THRESH=0.20

# Culling en espace écran plus strict que fast.
CULL_SCREEN_SIZE=0.15

# Reset alpha plus fréquent pour éviter l'accumulation de splats inutiles.
RESET_ALPHA_EVERY=50

########################################
# QUALITY / REGULARIZATION
########################################

USE_BILATERAL_GRID=true
USE_SCALE_REGULARIZATION=false

# Plafond de croissance essentiel.
# Avec 306075 points initiaux, objectif théorique <= ~918225 GS.
MAX_GAUSS_RATIO=3.0

SSIM_LAMBDA=0.22

########################################
# EXPORT
########################################

EXPORT_NUM_POINTS=600000
EXPORT_DOWNSAMPLE=1
