#!/usr/bin/env bash
# Défauts de images_to_3dgs : valeurs propres à ce pipeline, chargées après config/defaults/*.
# Les chemins Paris9 sont ici pour référence ; remplacer-les dans la configuration utilisateur.

SFM_IMAGES_DIR="/shared/home/NBellaiche/data/Paris9/images"
OUTPUT_DIR="/shared/home/NBellaiche/data/Paris9/reconstruction-20k-8M-4blocs"
SPIRULA_BIN="/shared/home/NBellaiche/sources/spirula-studio/build_vulkan/spirula"
TRAIN_MASKS_SPIRULA_MODEL="/shared/home/NBellaiche/sources/spirula-studio/models/sam3-q4_0.ggml"
EXPORT_SPLAT_NAME="Paris9-20000-8M.splat"
SLURM_PARTITION="jz"
