#!/usr/bin/env bash
# Défauts Slurm (ignorés pour l'exécution locale).

# ============================================================================
# 7. Slurm uniquement
# ============================================================================
SLURM_PARTITION=""
SLURM_MEMORY="64G"
SLURM_TIME_LIMIT="48:00:00"   # limite PAR job, pas durée garantie
SLURM_GPU_TYPE=""           # vide : gpu:1 ; sinon type Slurm, ex. a40
# Si SFM_MASKS et TRAIN_MASKS sont actives avec des politiques différentes,
# Slurm soumet un job par politique (un GPU chacun) ; ils tournent en parallèle
# si deux GPU sont disponibles. La préparation SfM attend les deux résultats.
SLURM_ACCOUNT=""
SLURM_QOS=""
# Script optionnel sourcé sur chaque nœud (modules, activation mamba/conda).
# Vide : environnement hérité du terminal de soumission (--export=ALL).
SLURM_ENV_SETUP=""
