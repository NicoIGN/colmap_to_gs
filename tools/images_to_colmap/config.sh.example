#!/usr/bin/env bash
# Configuration Bash de confiance ; chemins absolus.
SFM_IMAGES_DIR="/chemin/images"
OUTPUT_DIR="/chemin/dataset-colmap"
PYTHON_BIN="python3"
COLMAP_METHOD="spirula" # spirula | colmap | nerfstudio | orientations
SPIRULA_BIN="/chemin/spirula"
# --- COLMAP_METHOD=colmap uniquement ---
SFM_COLMAP_BIN="colmap"
SFM_COLMAP_USE_GPU="false"
# --- Matching COLMAP direct / ns-process-data ---
SFM_COLMAP_MATCHING="exhaustive"
# --- COLMAP_METHOD=nerfstudio uniquement ---
SFM_NS_PROCESS_DATA_BIN="ns-process-data"
SFM_CAMERA_MODEL="opencv"
SFM_CAMERA_MODE="single" # ns-process-data : single obligatoire
SFM_MAX_IMAGE_SIZE="2048"
SFM_ALIKED_MAX_FEATURES="4096"
SFM_THREADS="4"
# Pas d'entrainement ici ; sa preparation peut etre anticipee si souhaite.
SFM_MASKS="off"
TRAIN_MASKS="off"
# Pour activer les masques SfM Spirula/COLMAP direct, voir l'exemple images_to_splat.
# Logs Spirula : 1 masque les temps SAM et les messages [pcs], conserve
# avertissements/erreurs et la progression du pipeline. 0 = silencieux,
# 2 = informations et temps (defaut Spirula), 3 = detaille.
# S'applique aux processus Spirula, pas seulement aux masques.
export SS_NN_LOG=1
ORIENTATIONS_JSON="/chemin/orientations.json"
LIDAR_PLY="/chemin/points.ply"
GEOREF_GPX_FILE="" # prioritaire sur EXIF ; noms _fXXXXX requis par le recalage GPX
