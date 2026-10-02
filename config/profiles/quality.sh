########################################
# PERFORMANCE PROFILE
########################################

TRAINING_PROFILE="gpu/quality"

DEVICE="gpu"
MODEL="splatfacto"
MODEL_IMPLEMENTATION="torch"
TRAIN_VIS_MODE="tensorboard"

########################################
# RÉSOLUTION
########################################

# Conserver la résolution des images pour les détails.
CAMERA_RES_SCALE_FACTOR=1
MAX_RES=8192
NUM_DOWNSCALES=0

########################################
# ENTRAÎNEMENT
########################################

MAX_ITER=30000
STEPS_PER_SAVE=5000
STEPS_PER_EVAL_ALL_IMAGES=2000

# Densification pendant la première moitié de l'entraînement,
# puis optimisation des Gaussians existants.
STOP_SPLIT_AT=15000
REFINE_EVERY=100

########################################
# DENSIFICATION ET CULLING
########################################

# Valeur par défaut du Nerfstudio actuel avec use_absgrad=True.
# À vérifier si ta version utilise les gradients classiques.
DENSIFY_GRAD_THRESH=0.0008

# Préserver davantage de Gaussians de faible opacité.
CULL_ALPHA_THRESH=0.005

CULL_SCREEN_SIZE=0.15
SPLIT_SCREEN_SIZE=0.05
CULL_SCALE_THRESH=0.5

# Nombre de cycles de raffinement entre deux resets :
# 30 × 100 = 3000 étapes.
RESET_ALPHA_EVERY=30

########################################
# QUALITÉ / RÉGULARISATION
########################################

SSIM_LAMBDA=0.2
USE_SCALE_REGULARIZATION=False
MAX_GAUSS_RATIO=10.0

# Première référence sans correction d'exposition par image.
USE_BILATERAL_GRID=False

MIXED_PRECISION=False
USE_GRAD_SCALER=False

########################################
# PARAMÈTRES NeRF — NON PERTINENTS POUR SPLATFACTO
########################################

# Définis vides car run.sh les référence.
# train.sh doit éviter de les transmettre à Splatfacto.
TRAIN_RAYS_PER_BATCH=""
NUM_NERF_SAMPLES_PER_RAY=""
NUM_PROPOSAL_SAMPLES_PER_RAY=""
