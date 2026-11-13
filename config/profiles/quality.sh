########################################
# PERFORMANCE PROFILE
########################################

TRAINING_PROFILE="gpu/quality"

DEVICE="gpu"
MODEL="splatfacto"
TRAIN_NS_MODEL_IMPLEMENTATION="tcnn"
TRAIN_VIS_MODE="tensorboard"

########################################
# RÉSOLUTION
########################################

# Conserver la résolution des images pour les détails.
TRAIN_NS_CAMERA_RES_SCALE_FACTOR=1
TRAIN_NS_MAX_RES=8192
TRAIN_NS_NUM_DOWNSCALES=0

########################################
# ENTRAÎNEMENT
########################################

TRAIN_NS_MAX_ITER=30000
TRAIN_NS_STEPS_PER_SAVE=5000
TRAIN_NS_STEPS_PER_EVAL_ALL_IMAGES=2000

# Densification pendant un peu plus de la première moitié de l'entraînement,
# puis optimisation des Gaussians existants.
TRAIN_NS_STOP_SPLIT_AT=22000
TRAIN_NS_REFINE_EVERY=80

########################################
# DENSIFICATION ET CULLING
########################################

# Seuil légèrement abaissé pour déclencher un peu plus de splits.
TRAIN_NS_DENSIFY_GRAD_THRESH=0.0003
TRAIN_NS_DENSIFY_SIZE_THRESH=0.015

# Préserver davantage de Gaussians de faible opacité.
TRAIN_NS_CULL_ALPHA_THRESH=0.005

TRAIN_NS_CULL_SCREEN_SIZE=0.15
TRAIN_NS_SPLIT_SCREEN_SIZE=0.04
TRAIN_NS_CULL_SCALE_THRESH=0.5

# Nombre de cycles de raffinement entre deux resets :
# 30 × 80 = 2400 étapes.
TRAIN_NS_RESET_ALPHA_EVERY=30

########################################
# QUALITÉ / RÉGULARISATION
########################################

TRAIN_NS_SSIM_LAMBDA=0.2
TRAIN_NS_USE_SCALE_REGULARIZATION=False
TRAIN_NS_MAX_GAUSS_RATIO=10.0

# Première référence sans correction d'exposition par image.
TRAIN_NS_USE_BILATERAL_GRID=False

TRAIN_NS_MIXED_PRECISION=False
TRAIN_NS_USE_GRAD_SCALER=False

########################################
# PARAMÈTRES NeRF — NON PERTINENTS POUR SPLATFACTO
########################################

TRAIN_RAYS_PER_BATCH=""
TRAIN_NS_NUM_NERF_SAMPLES_PER_RAY=""
TRAIN_NS_NUM_PROPOSAL_SAMPLES_PER_RAY=""
