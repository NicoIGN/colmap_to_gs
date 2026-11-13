########################################
# PROFILE: fast_moderate
########################################

TRAINING_PROFILE="gpu/fast"

DEVICE="gpu"
MODEL="splatfacto"
TRAIN_NS_MODEL_IMPLEMENTATION="tcnn"
TRAIN_VIS_MODE="tensorboard"

########################################
# IMAGE / PREPROCESSING
########################################

TRAIN_NS_CAMERA_RES_SCALE_FACTOR=0.5
TRAIN_NS_MAX_RES=1024
TRAIN_NS_NUM_DOWNSCALES=1
SKIP_IMAGE_PROCESSING=true

########################################
# TRAINING
########################################

TRAIN_NS_MAX_ITER=3000
TRAIN_NS_STOP_SPLIT_AT=2000
TRAIN_RAYS_PER_BATCH=768

TRAIN_NS_NUM_NERF_SAMPLES_PER_RAY=16
TRAIN_NS_NUM_PROPOSAL_SAMPLES_PER_RAY="32 16"

########################################
# GAUSSIAN SPLATTING
# DENSIFICATION CONTROLEE
########################################

# Ancien fast : 0.00003
# Valeur 10x plus stricte : moins de Gaussians dépassent le seuil gradient.
TRAIN_NS_DENSIFY_GRAD_THRESH=0.00030

# Ancien fast : 0.008
# Augmenté : évite les splits de petits Gaussians à l'écran.
TRAIN_NS_SPLIT_SCREEN_SIZE=0.020

# Ancien fast : 50
# Seulement un raffinement toutes les 150 itérations.
TRAIN_NS_REFINE_EVERY=150

########################################
# CULLING / PRUNING
########################################

# Élimine plus tôt les Gaussians quasi transparents.
TRAIN_NS_CULL_ALPHA_THRESH=0.04

# Seuil modéré pour éliminer les splats devenus anormalement étendus.
TRAIN_NS_CULL_SCALE_THRESH=0.20

# Culling en espace écran plus strict que fast.
TRAIN_NS_CULL_SCREEN_SIZE=0.15

# Reset alpha plus fréquent pour éviter l'accumulation de splats inutiles.
TRAIN_NS_RESET_ALPHA_EVERY=50

########################################
# QUALITY / REGULARIZATION
########################################

TRAIN_NS_USE_BILATERAL_GRID=true
TRAIN_NS_USE_SCALE_REGULARIZATION=false

# Plafond de croissance essentiel.
# Avec 306075 points initiaux, objectif théorique <= ~918225 GS.
TRAIN_NS_MAX_GAUSS_RATIO=3.0

TRAIN_NS_SSIM_LAMBDA=0.22

########################################
# EXPORT
########################################

EXPORT_NUM_POINTS=600000
EXPORT_DOWNSAMPLE=1
