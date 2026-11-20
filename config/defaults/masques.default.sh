#!/usr/bin/env bash
# Défauts des masques SfM et d'entraînement (SAM Spirula ou segmentation locale).
# Mêmes variables que tools/panoramax.

# on|off pour chaque étape. Politiques identiques : un seul calcul réutilisé.
SFM_MASKS="on"
TRAIN_MASKS="on"
# Dossiers optionnels de masques PNG déjà calculés. Chemin vide = génération
# normale ; renseigner un dossier complet pour le réutiliser sans modèle/GPU.
TRAIN_MASKS_DIR=""
TRAIN_MASKS_BACKEND="spirula" # spirula|segmentation ; défaut local : spirula
# Chemin du modèle SAM Spirula (.ggml).
TRAIN_MASKS_SPIRULA_MODEL=""
# Prompts SAM en anglais ; conserver voitures et passants.
TRAIN_MASKS_LABELS="sky"
TRAIN_MASKS_CONFIDENCE="0.5"     # [0,1]
TRAIN_MASKS_DILATE="0.002"      # auto ou [0,0.1[, fraction de la diagonale
# 1.0 permet les vues vers le haut presque entièrement masquées.
# En dessous, un masque dépassant cette fraction est désactivé pour cette image.
TRAIN_MASKS_MAX_FRACTION="1.0"
# Backend segmentation : modèle local Transformers et interpréteur associé.
TRAIN_MASKS_MODEL=""
TRAIN_MASKS_PYTHON="python3"
# auto | cuda | cuda:N | mps | cpu (backend segmentation).
TRAIN_MASKS_DEVICE="auto"
# Vide : hériter du TRAIN_MASKS_* correspondant, comme dans Panoramax.
# SFM_MASKS_DILATE a son propre défaut 0.002.
SFM_MASKS_BACKEND=""   # vide : hérite de TRAIN_MASKS_BACKEND ; spirula|segmentation
SFM_MASKS_DIR=""
SFM_MASKS_SPIRULA_MODEL=""
SFM_MASKS_LABELS=""    # vide : hérite de TRAIN_MASKS_LABELS
SFM_MASKS_CONFIDENCE=""  # [0,1] ; vide : hérite de TRAIN_MASKS_CONFIDENCE
SFM_MASKS_DILATE="0.002"
SFM_MASKS_MAX_FRACTION=""  # [0,1] ; vide : hérite de TRAIN_MASKS_MAX_FRACTION
SFM_MASKS_MODEL=""
SFM_MASKS_PYTHON=""
# auto | cuda | cuda:N | mps | cpu ; vide : hérite de TRAIN_MASKS_DEVICE.
SFM_MASKS_DEVICE=""
# auto|empty|ignore : mêmes modes que Panoramax.
# empty : apprendre le ciel comme vide ; ignore : exclure les pixels de la perte.
TRAIN_MASKS_MODE="empty"
