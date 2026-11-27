#!/usr/bin/env bash
# Défauts du pipeline Panoramax : seulement les clés propres et le différentiel
# par rapport à config/defaults/*.default.sh. Chargé après ces défauts communs.
# Exécution locale : bash scripts/local/panoramax_to_3dgs.sh config.sh
# Slurm : bash tools/panoramax/launch.sh config.sh [--wait]
# Validation sans calcul : bash tools/panoramax/validate_config.sh config.sh slurm
#
# "" signifie une option non forcée ; le commentaire indique alors le repli.
# OUTPUT_DIR et PANORAMAX_BBOX n'ont aucun défaut : à définir dans la configuration utilisateur.

# --- Propre à Panoramax : recherche et téléchargement ---
# Obligatoire, aucun défaut. Ordre xmin,ymin,xmax,ymax dans le système indiqué
# ci-dessous ; quatre nombres finis, xmin<xmax et ymin<ymax.
PANORAMAX_BBOX=""

# Défaut : 2154 (Lambert-93). Code EPSG des coordonnées de PANORAMAX_BBOX.
PANORAMAX_BBOX_EPSG="2154"

# Défaut : https://explore.panoramax.fr/api.
# URL HTTP(S) racine de l'API STAC, sans /search ni espaces.
PANORAMAX_API_URL="https://explore.panoramax.fr/api"

# Défaut : hd. Valeurs : hd (haute définition), sd, thumb (vignette).
PANORAMAX_QUALITY="hd"

# Défaut : "". Vide = toutes dates ; sinon filtre datetime STAC ISO 8601
# (instant ou intervalle début/fin, ex. "2024-01-01T00:00:00Z/..").
PANORAMAX_DATETIME=""

# Défaut : "". Vide = aucune limite utilisateur (la recherche découpe la zone
# au-delà de 10000 résultats par requête) ; sinon entier > 0, nombre maximal de photos.
PANORAMAX_IMAGE_MAX=""

# Défaut : all. Valeurs : all (toutes), normal (photos non 360), 360 (équirectangulaires).
# Le type vient de la collection ; s'il n'y est pas, il est lu dans le XMP GPano de l'image.
PANORAMAX_PICTURE_TYPE="all"

# Défaut : "". Vide = tous les providers ; sinon nom exact du provider STAC
# (properties.providers[].name), insensible à la casse.
PANORAMAX_PROVIDER=""

# Défaut : "". Vide = aucun filtre. Syntaxe : "KEY=V1,V2;KEY2=V3" ; ';' sépare les
# propriétés (ET), ',' sépare les valeurs d'une propriété (OU), insensible à la casse.
# KEY : propriété STAC (ex. geovisio:producer) ou tag EXIF (ex. Exif.Image.Software).
PANORAMAX_FILTERS=""

# Défaut : false. true = SfM multi-caméras : toutes les tailles d'images sont
# gardées et chaque taille devient une caméra (sous-dossier LxH/). La focale
# n'est pas transmise à Spirula ; il estime chaque caméra.
PANORAMAX_MULTI_CAM="false"

# Défaut : false. Valeurs : true|false (alias 1|0, yes|no, on|off).
# false = arrêt si la requête diffère de celle du dataset existant.
# true = archive panoramax/ dans panoramax.replaced-DATE, puis retélécharge.
# L'archive conserve photos, SfM et blocs ; aucune suppression automatique.
PANORAMAX_REPLACE_REQUEST="false"

# --- Propre à Panoramax : SfM Spirula ---
# Défaut : "". Vide = filtre GPS des paires désactivé ; sinon nombre fini > 0
# en mètres : garder les paires à distance horizontale strictement < au rayon.
# Les paires sans GPS sont gardées ; ne rajoute aucune paire.
SFM_SP_PAIR_MAX_DISTANCE=""

# Défaut : lightglue. Valeurs : bruteforce|lightglue|loma-b128|loma-b|loma-r|loma-l|loma-g.
# bruteforce contourne LightGlue si celui-ci plante (par exemple erreur gemm).
SFM_SP_MATCHER="lightglue"

# --- Propre à Panoramax : entraînement Spirula 360 ---
# Spirula, 360 uniquement (equirectangular) :
#   true  (défaut Spirula) : la photo 360 est découpée en vues pinhole (faces),
#          sans couture et sans distorsion équirectangulaire, mais plus de
#          caméras à entraîner (plus de VRAM et de temps).
#   false : entraînement direct sur l'équirectangulaire, sans découpage.
TRAIN_SP_WARP_SPHERICAL="true"

# --- Propre à Panoramax : nettoyage des splats ---
# Défaut : contextual. Valeurs : contextual|cleangs.
# contextual = filtres géométriques ; cleangs = nettoyage ciel multivues (CUDA,
# modèle et venv propres à Clean-GS).
POSTPROCESS_CLEANER="contextual"

# Défaut : diagnostic. Valeurs : diagnostic|apply|off.
# diagnostic = garde tout et exporte les candidats dans <nom>.rejected.splat ;
# apply = retire les candidats ; off = désactive le test contextuel.
POSTPROCESS_FILTER_MODE="apply"

# Filtre de forme/isolement (export_ply.py). Vide = désactivé ; les deux vont ensemble.
# Rejet si splat allongé (rapport d'axes > MAX_ASPECT_RATIO) ET isolé
# (distance moyenne à 8 voisins > ISOLATION_FACTOR x médiane). Ex. 20 et 5.
POSTPROCESS_SHAPE_MAX_ASPECT_RATIO=""
POSTPROCESS_SHAPE_ISOLATION_FACTOR=""

# Défaut : 10. Nombre fini > 1, sans unité.
# Grand axe / médiane des grands axes voisins ; le dépassement seul ne retire
# rien : il faut aussi une position périphérique et un faible support.
POSTPROCESS_FILTER_SIZE_FACTOR="10"

# --- Propre à Panoramax : nettoyage cleangs (ignoré si POSTPROCESS_CLEANER=contextual) ---
# Défaut : "" (repli PYTHON_BIN). Commande/chemin du Python du venv Clean-GS,
# affiché par bash tools/libs/legacy/clean_gs/install.sh.
CLEANGS_PYTHON=""
# Défaut : "". Dossier local du modèle de segmentation obligatoire si
# POSTPROCESS_CLEANER=cleangs ; affiché par l'installateur Clean-GS.
CLEANGS_MODEL=""
# Défaut : diagnostic. Valeurs : diagnostic|apply.
CLEANGS_MODE="diagnostic"
# Défaut : 2. Entier > 0 : nombre minimal de vues ciel concordantes.
CLEANGS_MIN_VIEWS="2"
# Défaut : 0.9. Nombre dans ]0.5,1] : confiance minimale du label ciel.
CLEANGS_SKY_CONFIDENCE="0.9"
# Défaut : 0.2. Nombre dans [0,1[ : fraction maximale de splats supprimables.
CLEANGS_MAX_REMOVAL_FRACTION="0.2"

# --- Différentiel avec images_to_3dgs (config/defaults/*.default.sh) ---
SFM_MASKS="off"
SFM_ALIKED_MAX_FEATURES="4096"
TRAIN_MASKS="off"
TRAIN_MASKS_BACKEND="segmentation"
TRAIN_MASKS_DILATE="auto"
TRAIN_MASKS_MAX_FRACTION="0.8"
TRAIN_MASKS_MODE="auto"
TRAIN_MASKS_PYTHON=""
SPLIT_OVERLAP_MAX_FRACTION="0.05"
TRAIN_NUM_ITER="30000"
TRAIN_SP_CAP_MAX="1500000"
TRAIN_SP_FLOATER_SUPPRESSION="off"
TRAIN_SP_QUALITY=""
TRAIN_SP_REFINE_STOP_ITER=""
TRAIN_SP_REFINE_STOP_NUM_ITER=""
EXPORT_SPLAT_NAME="panoramax.splat"
SLURM_PARTITION="jean-zellou"

# --- Propre à Panoramax : Slurm Conda ---
# Défaut : "". Nom/chemin d'un environnement Conda ; vide = environnement hérité.
SLURM_CONDA_ENV=""
# Défaut : "". Racine Conda ; vide = conda info --base. Utilisé seulement si SLURM_CONDA_ENV est renseigné.
SLURM_CONDA_BASE=""
