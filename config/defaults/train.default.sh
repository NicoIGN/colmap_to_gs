#!/usr/bin/env bash
# Défauts de l'entraînement 3DGS de chaque bloc.

# Nerfstudio exige PyTorch + GPU NVIDIA/CUDA ; aucun repli CPU/Metal.
# Avec TRAIN_MASKS=on, définir explicitement TRAIN_MASKS_MODE=ignore.
# Nerfstudio : TRAIN_MASKS_BACKEND=segmentation, modèle local Mask2Former/SegFormer,
# labels sky (pas cloud). Exemple complet : param.nerfstudio.default.sh.
# Les options de cap/croissance/qualité Spirula ne sont pas transposées.

# ============================================================================
# 4. Entraînement de chaque bloc
# ============================================================================
# Nombre d'itérations commun aux backends Spirula et Nerfstudio.
TRAIN_NUM_ITER="20000"   # PAR BLOC, pas réparti entre les blocs

# --- TRAIN_BACKEND=spirula uniquement ---
TRAIN_SP_PRESET="3dgs"
# Macro Spirula (--quality) : règle le budget de splats et la durée, pas la résolution.
# low : 200 k splats, 15 000 itérations ; medium : 1 M, 30 000 ;
# high : 3 M, 50 000 ; ultra : 10 M, 80 000. Spirula seul applique le défaut "medium".
# Les budgets explicites (TRAIN_SP_CAP_MAX, TRAIN_NUM_ITER) priment sur la macro.
TRAIN_SP_QUALITY="ultra"    # low|medium|high|ultra
# Ces budgets explicites priment sur TRAIN_SP_QUALITY.
TRAIN_SP_CAP_MAX="2000000"  # PAR BLOC : 4 x 2 M = jusqu'à 8 M avant fusion
# Vide : plafond TRAIN_SP_CAP_MAX ; sinon limite par bloc à
# min(TRAIN_SP_CAP_MAX, nombre d'images du bloc x cette valeur).
TRAIN_SP_SPLATS_PER_IMAGE=""
# Vide/absent : estimation [1.001,1.05] pour atteindre le cap à l'arrêt.
# Valeur explicite > 1 : prioritaire, non bornée automatiquement.
TRAIN_SP_GROWTH_FACTOR=""
# Itération minimale d'arrêt de la densification (ajout/division de splats),
# pas de l'entraînement : les splats existants restent optimisés ensuite.
TRAIN_SP_REFINE_STOP_ITER="14000"
# Nombre d'itérations finales souhaitées sans densification, sous réserve
# du minimum ci-dessus. Arrêt = max(REFINE_STOP_ITER, TRAIN_NUM_ITER - REFINE_STOP_NUM_ITER).
# Ici : max(14000, 20000-4000) = 16000, puis 4000 itérations de stabilisation.
# Si REFINE_STOP_ITER >= TRAIN_NUM_ITER, aucune phase finale sans densification.
TRAIN_SP_REFINE_STOP_NUM_ITER="4000"
# Sauvegarde du checkpoint toutes les N itérations (Spirula --steps-per-save).
# Seul le dernier checkpoint est conservé (--save-only-latest-checkpoint 1).
TRAIN_SP_STEPS_PER_SAVE="2000"
TRAIN_SP_FLOATER_SUPPRESSION="mild"       # off|mild|strong
TRAIN_SP_DISTRACTION_ROBUSTNESS="off"     # off|mild|strong
# Corrections photométriques Spirula uniquement ; vide = défauts Spirula observés :
# true, no_crf_no_vig, false, true, ppisp. Valeurs PPISP : original|rqs|no_crf|
# no_crf_clamp|no_crf_no_vig|no_crf_no_vig_clamp.
TRAIN_SP_USE_PPISP=""
TRAIN_SP_PPISP_PARAM_TYPE=""
# Initialiser la correction d'exposition depuis les EXIF, s'ils sont disponibles.
TRAIN_SP_PPISP_EXPOSURE_FROM_EXIF=""
# PPISP ajuste exposition/couleur ; no_crf inclut le vignettage, no_crf_no_vig l'omet.
# Booléens : true|false. La grille bilatérale affine aussi les couleurs.
TRAIN_SP_USE_BILATERAL_GRID=""
# Type de grille : affine|ppisp|loglinear.
TRAIN_SP_BILAGRID_TYPE=""

# --- TRAIN_BACKEND=nerfstudio uniquement ---
# Ces paramètres sont ignorés par Spirula ; Nerfstudio requiert NVIDIA/CUDA.
# Commandes Nerfstudio (chemin absolu ou nom dans le PATH).
TRAIN_NS_TRAIN_BIN="ns-train"
TRAIN_NS_EXPORT_BIN="ns-export"
# Entier >= 1 : facteur de réduction des images d'entraînement (1 = résolution native).
TRAIN_NS_DOWNSCALE_FACTOR="1"
# false : réutiliser un checkpoint final ; une sortie partielle est refusée.
# true : supprimer/recalculer les sorties 3DGS des blocs, PAS le SfM.
# Sauvegarder les anciens entraînements avant de l'activer.
TRAIN_CLEAN="false"
