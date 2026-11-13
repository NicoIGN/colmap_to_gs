########################################
# PERFORMANCE PROFILE
########################################

TRAINING_PROFILE="gpu/balanced"

DEVICE="gpu"
MODEL="splatfacto"
TRAIN_NS_MODEL_IMPLEMENTATION="tcnn"
TRAIN_VIS_MODE="tensorboard"

########################################
# IMAGE / PREPROCESSING
########################################

# ⚠️ CRITIQUE pour débloquer la densification
TRAIN_NS_CAMERA_RES_SCALE_FACTOR=0.75
TRAIN_NS_MAX_RES=1024

TRAIN_NS_NUM_DOWNSCALES=1
SKIP_IMAGE_PROCESSING=true

########################################
# TRAINING
########################################

TRAIN_NS_MAX_ITER=6000

TRAIN_NS_STOP_SPLIT_AT=6000

# ⚠️ CRITIQUE (qualité du gradient)
TRAIN_RAYS_PER_BATCH=512

# 🧠 Meilleur signal pour split
TRAIN_NS_NUM_NERF_SAMPLES_PER_RAY=32
TRAIN_NS_NUM_PROPOSAL_SAMPLES_PER_RAY="64 32"

########################################
# GAUSSIAN SPLATTING (REDUCED SPLATS)
########################################

# 🔥 DENSIFICATION (moins agressif)
TRAIN_NS_DENSIFY_GRAD_THRESH=0.00045   # ↑ moins de split

# 🧹 CLEANING (plus strict)
TRAIN_NS_CULL_ALPHA_THRESH=0.12        # ↑ supprime plus tôt les splats faibles

# 📏 SPATIAL CONTROL (réduction explosion)
TRAIN_NS_CULL_SCREEN_SIZE=0.25         # ↑ plus agressif en screen-space
TRAIN_NS_SPLIT_SCREEN_SIZE=0.02        # ↑ moins de split fin

# ⚡ DENSIFICATION FREQUENCY (moins de croissance)
TRAIN_NS_REFINE_EVERY=300              # ↑ réduit création de nouveaux splats


# 🧠 STABILISATION (évite accumulation de bruit)
TRAIN_NS_RESET_ALPHA_EVERY=40          # ↑ nettoyage plus fréquent
TRAIN_NS_CULL_SCALE_THRESH=0.5         # ↓ supprime petits clusters instables

########################################
# QUALITY / REGULARIZATION
########################################


TRAIN_NS_USE_BILATERAL_GRID=true
TRAIN_NS_USE_SCALE_REGULARIZATION=False

TRAIN_NS_MAX_GAUSS_RATIO=4.0          # ↓ limite taille splats
TRAIN_NS_SSIM_LAMBDA=0.25             # léger boost stabilité image (optionnel)

########################################
# EXPORT BALANCED
########################################

# adapté au nouveau volume
EXPORT_NUM_POINTS=600000
EXPORT_DOWNSAMPLE=1
