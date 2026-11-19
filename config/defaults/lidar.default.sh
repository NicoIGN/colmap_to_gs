#!/usr/bin/env bash
# Défauts LiDAR optionnels (images, vidéos et Slurm) ; détails : tools/lidar/PIPELINES.md.

LIDAR_COLMAP_ALIGN=false
LIDAR_COLMAP_ALIGNMENT_MODE=all # trajectory-ground : sol proche, hauteur/inclinaison seules.
LIDAR_COLMAP_GROUND_CORRIDOR=10
LIDAR_COLMAP_INJECT=false
LIDAR_CLEAN=false
LIDAR_CLEAN_MODE=diagnostic
LIDAR_MARGIN=20
LIDAR_CLEAN_DISTANCE=0.5
LIDAR_CLEAN_MIN_OPACITY=0.05
LIDAR_CLEAN_EVIDENCE_MODE=aerial
LIDAR_CLEAN_GROUND_CLEARANCE=2
