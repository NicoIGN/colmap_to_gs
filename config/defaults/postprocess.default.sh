#!/usr/bin/env bash
# Défauts du recadrage et du nettoyage des splats, par bloc puis après fusion.

# ============================================================================
# 5. Recadrage AVANT fusion
# ============================================================================
# apply|diagnostic|off. apply retire les centres hors bbox COLMAP globale ;
# diagnostic écrit le diagnostic mais ne retire rien ; off ne calcule pas la bbox.
# Bbox orientée robuste (quantiles 0.5/99.5 %), pas bbox de chaque sous-bloc.
POSTPROCESS_SPARSE_BBOX_MODE="apply"
POSTPROCESS_SPARSE_BBOX_MARGIN="0.02"  # fraction du plus grand côté (plani : resserrée)
# Quantile de chaque côté de la bbox sparse (0.005 = 0.5 %). Plus petit = bbox plus large,
# moins de risque de couper le haut d'un objet (ex. statue) avec peu de points sparse.
POSTPROCESS_SPARSE_BBOX_QUANTILE="0.02"
# planar : nettoyage seulement en plani (verticale = normale du plan des centres
# caméra, altitude jamais coupée). full : boîte 3D complète (coupe le haut d'un objet).
POSTPROCESS_SPARSE_BBOX_SCOPE="planar"
# Filtre de support (floaters isolés) : un splat est retiré s'il est loin de tout point
# sparse ET de tout autre splat (8e voisin). diagnostic = écrit .filter.json sans retirer ;
# apply = retire. DISTANCE_FACTOR : en densités sparse locales. MIN_OPACITY : splats
# moins opaques ne sont pas testés (0 : tous).
POSTPROCESS_SPARSE_SUPPORT_MODE="apply"
POSTPROCESS_SPARSE_SUPPORT_DISTANCE_FACTOR="3.0"
# Points sparse isolés retirés avant le filtre : distance au 8e voisin sparse > ISOLATION_FACTOR
# fois la médiane.
POSTPROCESS_SPARSE_SUPPORT_ISOLATION_FACTOR="3.0"
POSTPROCESS_SPARSE_SUPPORT_MIN_OPACITY="0"
# Sécurité PAR BLOC : au-delà, le filtre est ignoré (entrée conservée, avertissement).
# Examiner les diagnostics avant d'augmenter cette limite [0,1].
POSTPROCESS_MAX_REMOVAL_FRACTION="0.20"
# Les bruts sont conservés. Pas de filtre contextuel additionnel dans cet outil.
