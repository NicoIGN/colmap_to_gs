#!/usr/bin/env bash
# Mode GRID Panoramax autour de la place de la Nation (Paris), économe en ressources.
# Emprise de 400 m x 400 m, arrondie aux 100 m, autour de 655661,6861087 (Lambert 93) ; 4 dalles de 200 m.

GRID_BBOX="655500,6860900,655900,6861300"
GRID_CRS="2154"
GRID_TILE_SIZE="200"
GRID_OVERLAP="0"
GRID_MARGIN="10"

OUTPUT_DIR="/chemin/sortie/nation"

NUM_CPUS="2"
NUM_GPUS="1"
SPLIT_NUM_BLOCKS="1"
TRAIN_NUM_ITER="7000"
