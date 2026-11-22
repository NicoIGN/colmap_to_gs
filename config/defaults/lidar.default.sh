#!/usr/bin/env bash
# Défauts LiDAR optionnels (images, vidéos et Slurm) ; détails : tools/lidar/PIPELINES.md.

LIDAR_COLMAP_ALIGN=false       # true|false : aligne le SfM COLMAP sur le LiDAR
# all : alignement complet ; trajectory-ground : sol proche, hauteur/inclinaison seules.
LIDAR_COLMAP_ALIGNMENT_MODE=all
# Rayon autour de la trajectoire retenu comme sol lors de l'alignement (valeur numérique).
LIDAR_COLMAP_GROUND_CORRIDOR=10
LIDAR_COLMAP_INJECT=false      # true|false : injecte des points LiDAR (exige LIDAR_COLMAP_ALIGN=true)
LIDAR_CLEAN=false              # true|false : active le nettoyage des splats par LiDAR
# diagnostic : écrit le rapport sans retirer de splats ; apply : retire les splats.
LIDAR_CLEAN_MODE=diagnostic
# Marge autour de l'emprise LiDAR avant nettoyage (valeur numérique).
LIDAR_MARGIN=20
# Distance de rattachement d'un splat au LiDAR (valeur numérique).
LIDAR_CLEAN_DISTANCE=0.5
# Opacité minimale d'un splat pour être testé, dans [0,1].
LIDAR_CLEAN_MIN_OPACITY=0.05
# aerial : preuve vue du dessus ; proximity : preuve vue au sol.
LIDAR_CLEAN_EVIDENCE_MODE=aerial
# Hauteur (valeur numérique) au-dessus du sol LiDAR protégée du nettoyage.
LIDAR_CLEAN_GROUND_CLEARANCE=2
