#!/usr/bin/env bash
# Défauts de la phase SfM (reconstruction globale des poses et du nuage épars).

# Producteur du modèle : spirula | colmap | nerfstudio | orientations | hloc.
# Le mode nerfstudio appelle ns-process-data (COLMAP/SIFT) et exige NVIDIA/CUDA.
# hloc : SfM complet via Hierarchical-Localization + pycolmap (voir HLOC_*).
COLMAP_METHOD="spirula"

# --- COLMAP_METHOD=hloc uniquement ---
# Extraction de points : superpoint_aachen | superpoint_max | superpoint_inloc | r2d2 |
# d2net-ss | sift | sosnet | disk | dir | netvlad | openibl | cosplace (globaux, non utilisables ici).
HLOC_FEATURES="superpoint_max"
# superpoint+lightglue | disk+lightglue | superglue | superglue-fast | NN-superpoint | NN-ratio | NN-mutual | adalam
HLOC_MATCHER="superpoint+lightglue"
# retrieval : paires des N voisins les plus proches (HLOC_RETRIEVAL) ; exhaustive : toutes les paires (coûteux).
HLOC_PAIRS="retrieval"
HLOC_RETRIEVAL="netvlad"    # descripteur global pour retrieval : netvlad | openibl | cosplace
HLOC_NUM_NEIGHBORS=10       # entier > 0, paires par image en mode retrieval
# Modèle HLOC (majuscules) : OPENCV, SIMPLE_RADIAL, PINHOLE ou EQUIRECTANGULAR.
# EQUIRECTANGULAR projette les panoramas 2:1 en six vues pinhole de 110 degrés.
HLOC_CAMERA_MODEL="OPENCV"
# Les vues servent aussi à l'entraînement, avec focale pinhole fixée.
SFM_EQUIRECTANGULAR_VIEW_SIZE="1024"

# --- COLMAP_METHOD=colmap uniquement ---
SFM_COLMAP_BIN="colmap"
# true | false : GPU pour l'extraction et le matching COLMAP (exige un build CUDA).
SFM_COLMAP_USE_GPU="true"
# --- Matching COLMAP direct / ns-process-data ---
SFM_COLMAP_MATCHING="exhaustive"          # exhaustive | sequential
# --- COLMAP_METHOD=nerfstudio uniquement ---
SFM_NS_PROCESS_DATA_BIN="ns-process-data"
# Mode orientations : poses caméra JSON explicites et PLY coloré dans les mêmes coordonnées.
ORIENTATIONS_JSON=""
# Chemin d'un PLY LiDAR aligné sur le SfM ; vide = non utilisé.
LIDAR_PLY=""

# ============================================================================
# 2. Reconstruction SfM globale
# ============================================================================
# Ces paramètres communs sont utilisés par les producteurs compatibles
# (Spirula ou COLMAP direct). Les options spécifiques sont dans les groupes
# SFM_SP_*, SFM_COLMAP_* et SFM_NS_*.
SFM_MAX_IMAGE_SIZE="4096"
# Nombre maximal de points extraits par image (entier > 0), ALIKED.
SFM_ALIKED_MAX_FEATURES="8192"
# Seuils géométriques partagés avec COLMAP direct ; le SfM Nerfstudio garde
# ses propres seuils. Plus strict = moins de parasites, mais couverture réduite.
SFM_MAX_ERROR="2"         # [0.1,100], pixels d'extraction : vérification + mapping
SFM_MIN_INLIERS="30"      # entier [4,1000000] par paire, pas par point 3D
SFM_MIN_TRI_ANGLE="6"     # ]0,90] degrés ; augmente = moins de faible parallaxe

# --- COLMAP_METHOD=spirula uniquement ---
# Extracteur de points : sift | aliked-n16rot | aliked-n32 | loma-b128 | loma-b.
SFM_SP_FEATURES="aliked-n16rot"
# Appariement : bruteforce | lightglue | loma-b128 | loma-b | loma-r | loma-l | loma-g.
SFM_SP_MATCHER="bruteforce"
# prefilter|auto|exhaustive|sequential ; exhaustive est coûteux.
# sequential : noms triés dans l'ordre de la vidéo, SFM_CAMERA_SPLIT=false.
SFM_SP_PAIRS="prefilter"
# prefilter : voisins retenus par image (plus petit = appariement plus court).
SFM_SP_PREFILTER_NEIGHBORS="16"
# low|medium|high|extreme : préréglage Spirula ; high plafonne les points max à 8192
# et écrase SFM_ALIKED_MAX_FEATURES. Utiliser extreme pour aller au-delà.
SFM_SP_QUALITY="high"
# video|panoramax : préréglage Spirula. panoramax prépare les images équirectangulaires.
SFM_SP_PRESET="video"
SFM_SP_BA_LOSS="huber"       # huber|cauchy|trivial
SFM_SP_BA_LOSS_PARAM="2"     # ]0,1000], pixels d'extraction : Huber delta / Cauchy c
# Spirula uniquement : exposition pour l'analyse SfM, pas la correction d'entraînement.
# Vide = option omise ; sinon auto ou correction en stops (ex. 2 ou -1).
SFM_SP_IMAGE_EXPOSURE=""
# Spirula uniquement : fichier texte « nom_image X Y Z » (X, Y, Z en mètres, 4 champs par ligne),
# une ligne par image. Le nom doit correspondre à celui du modèle SfM (avec ou sans extension).
# Remplace le GPS EXIF ; incompatible avec SFM_SP_PAIR_MAX_DISTANCE. Vide = désactivé.
# Format décrit dans tools/sfm/README.md ; produit par tools/sfm/spirula/gps_to_metric_positions.py.
SFM_SP_METRIC_POSITIONS=""
# Nouveau OUTPUT_DIR nécessaire pour recalculer avec d'autres seuils.
# folder : intrinsèques partagés par sous-dossier ; single : une caméra ;
# image : intrinsèques indépendants par image.
SFM_CAMERA_MODE="folder"
# true : si Spirula produit plusieurs modèles d'au moins SFM_CAMERA_SPLIT_MIN_IMAGES
# images (ex. vidéo avec changement d'objectif), relance le SfM avec une caméra
# par modèle dans OUTPUT_DIR/sfm_cameras ; ancien SfM dans sfm/sparse-before-camera-split.
# Avec PIPELINE_RESUME=true, les sorties dérivées de l'ancien SfM sont archivées
# dans camera-split-backups/. Spirula uniquement, pas avec SFM_CAMERA_MODE=image.
SFM_CAMERA_SPLIT="false"
# Entier > 0 : taille minimale d'un modèle caméra pour déclencher le découpage.
SFM_CAMERA_SPLIT_MIN_IMAGES=50
# Modèles Spirula multiples (sparse/N), code 3 accepté si sparse/0 est valide.
# Un modèle est représentatif s'il a >= SFM_PARTIAL_MIN_IMAGES images ET
# >= SFM_PARTIAL_MIN_FRACTION du total ; un bloc et un géoréférencement par modèle.
# Exige un mode de géoréférencement (GPX, positions ou EXIF) ; sinon modèle unique.
SFM_PARTIAL_MIN_FRACTION=0.05
SFM_PARTIAL_MIN_IMAGES=30
# SFM_CAMERA_MODEL non défini : détection automatique (360° -> equirectangular, sinon opencv).
# Le définir explicitement force le modèle.
# Filtre expérimental des points isolés/lointains avant découpage (CPU).
# Sorties COLMAP binaires sparse_clean et sparse_rejected ; original intact.
# Activer avant préparation ; changer après découpage exige un nouveau workspace.
SFM_FILTER_DENSITY="${SFM_FILTER_DENSITY:-false}"
# Seuils du filtre : augmenter far-support-rate ou baisser vote-fraction = plus strict.
SFM_DENSITY_VOTE_FRACTION="${SFM_DENSITY_VOTE_FRACTION:-0.75}"   # ]0,1[
SFM_DENSITY_FAR_SUPPORT_RATE="${SFM_DENSITY_FAR_SUPPORT_RATE:-0.25}"  # ]0,1]
# Densification MVS COLMAP (patch_match_stereo + stereo_fusion), optionnelle.
# Nécessite un COLMAP compilé avec CUDA (usage prévu : Slurm). Les points denses
# remplacent le sparse en entrée du 3DGS ; incompatible avec GEOREF_GPX_FILE.
COLMAP_USE_MVS="${COLMAP_USE_MVS:-false}"
# Entier > 0 : plus grand côté (px) des images passées à la densification MVS.
MVS_MAX_IMAGE_SIZE="${MVS_MAX_IMAGE_SIZE:-2000}"
# Entier >= 0 : index du GPU CUDA utilisé par MVS (0 = premier GPU).
MVS_GPU_INDEX="${MVS_GPU_INDEX:-0}"
# Photos individuelles, sans séquence.
# Sans GPX, les GPS EXIF de plusieurs images géoréférencent automatiquement le SfM.
# Un GPX configuré ci-dessous reste prioritaire sur les coordonnées EXIF.

# ============================================================================
# 2b. Géoréférencement GPX OPTIONNEL, avant découpage
# ============================================================================
# Vide/absent : GPS EXIF utilisé automatiquement s'il est présent sur plusieurs images.
# Sinon chemin absolu de la trace ; le SfM brut est conservé dans sfm/sparse/0.
# Le GPX est prioritaire sur l'EXIF ; le modèle transformé est dans georef/0.
GEOREF_GPX_FILE=""
# Dossier de vues pour construire la trajectoire, ex. h00_y0 ; vide = auto.
# Images ordonnées par _fXXXXX (ex. paris9_f002160.jpg), sans FPS ni horodatage.
# Nécessite >= 6 indices de frame distincts dans une trajectoire exploitable.
GEOREF_VIEW_PREFIX=""
# EPSG de l'origine projetée dans le rapport, PAS celui des coordonnées splat.
# Les splats restent en ENU local, pas en grandes coordonnées Lambert-93.
GEOREF_EPSG="2154"
# false : bloquer si ambigu ou erreur normalisée > 0.08.
# true : continuer APRÈS contrôle visuel des rapports et acceptation explicite.
# Toute solution reste approximative et doit être contrôlée sur la trace.
GEOREF_ALLOW_UNCERTAIN="false"
# Dépendances supplémentaires seulement si GPX : fiona et pyproj.
# Altitudes GPX brutes, sans correction géoïde. Rigs/frames COLMAP non supportés.
# Changer de GPX implique un nouveau OUTPUT_DIR, même avec TRAIN_CLEAN=true.
