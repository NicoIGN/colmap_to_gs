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

MAX_ITER=6000
STOP_SPLIT_AT=5200
TRAIN_RAYS_PER_BATCH=768

NUM_NERF_SAMPLES_PER_RAY=16
NUM_PROPOSAL_SAMPLES_PER_RAY="32 16"

########################################
# GAUSSIAN SPLATTING
# DENSIFICATION MODEREE
########################################

# Intermédiaire entre :
#   fast     = 0.00003
#   balanced = 0.00045
#
# Plus la valeur est élevée, moins il y a de Gaussians candidats.
DENSIFY_GRAD_THRESH=0.00015

# Intermédiaire entre :
#   fast     = 0.008
#   balanced = 0.02
SPLIT_SCREEN_SIZE=0.015

# Densification toutes les 100 itérations au lieu de 50.
# Cela laisse aux Gaussians le temps de converger entre deux raffinements.
REFINE_EVERY=100

########################################
# CULLING / CLEANING
########################################

# Plus strict que fast, mais beaucoup moins brutal que balanced=0.12.
CULL_ALPHA_THRESH=0.03

# Supprime les Gaussians excessivement grands.
# Valeur intermédiaire entre fast=0.05 et balanced=0.5.
CULL_SCALE_THRESH=0.20

# Nettoyage screen-space modéré.
CULL_SCREEN_SIZE=0.12

# Le compteur est lié aux cycles de raffinement.
# Avec REFINE_EVERY=100, 50 correspond à un reset vers 5000 steps.
RESET_ALPHA_EVERY=50

########################################
# QUALITY / REGULARIZATION
########################################

USE_BILATERAL_GRID=true
USE_SCALE_REGULARIZATION=false

# Limite plus basse que fast=8, mais moins restrictive que balanced=4.
MAX_GAUSS_RATIO=5.0

SSIM_LAMBDA=0.22

########################################
# EXPORT
########################################

EXPORT_NUM_POINTS=800000
EXPORT_DOWNSAMPLE=1
