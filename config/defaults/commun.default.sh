#!/usr/bin/env bash
# Défauts communs à tous les pipelines. Chargés avant tools/<pipeline>/config.default.sh
# puis avant la configuration utilisateur. Les chemins propres aux données sont vides ici.
# Tous les chemins de données/modèles doivent être absolus et visibles des nœuds.

# ============================================================================
# 0. Entrées et outils
# ============================================================================
# Dossier contenant les images, sans téléchargement Panoramax.
# --images /chemin peut remplacer cette valeur pour les deux lanceurs.
SFM_IMAGES_DIR=""

# true : scanner aussi les sous-dossiers ; false : fichiers de premier niveau.
# Les chemins relatifs sont conservés (pas d'aplatissement ni de collision de noms).
# Les liens vers des fichiers sont acceptés ; les dossiers symboliques ne sont
# pas parcourus pour éviter les boucles.
SFM_IMAGES_RECURSIVE="true"

# Workspace distinct et EXTÉRIEUR au dossier d'images. Les images sont liées,
# jamais déplacées. Choisir un nouveau workspace si images/SfM/masques changent.
OUTPUT_DIR=""
SPIRULA_BIN=""
PYTHON_BIN="python3"
# Reprise explicite après interruption, sans supprimer les artefacts.
# true : accepter une signature absente/ancienne avant blocs/train, vérifier
# chaque masque PNG existant et relancer le SfM si son sparse est incomplet.
# Les changements connus d'images/paramètres restent refusés.
# Laisser false normalement ; même variable pour le local et Slurm.
PIPELINE_RESUME="${PIPELINE_RESUME:-false}"
REMOVE_LOCKS="${REMOVE_LOCKS:-false}" # Forcer seulement après arrêt des anciens calculs.

# auto, indice Vulkan, nom du GPU (ex. NVIDIA A40), uuid:...
SPIRULA_DEVICE="auto"
# Logs Spirula : 1 masque les temps SAM et les messages [pcs], conserve
# avertissements/erreurs et la progression du pipeline. 0 = silencieux,
# 2 = informations et temps (défaut Spirula), 3 = détaillé.
# S'applique aux processus Spirula, pas seulement aux masques.
export SS_NN_LOG=1

# Parallélisme CPU : threads SfM, masques, entraînement et jobs de fusion.
NUM_CPUS="2"

# Un GPU PAR bloc actif. 1 : successifs ; 4 : jusqu'à 4 GPU sur 4 tâches.
NUM_GPUS="1"

# Moteur d'entraînement, indépendant du producteur COLMAP.
TRAIN_BACKEND="spirula"              # spirula | nerfstudio

# true UNIQUEMENT après lecture et acceptation des conditions SAM 3 Meta.
# false par défaut : aucune acceptation automatique implicite.
MASKS_ACCEPT_LICENSE="false"

# Niveau de log MoltenVK (macOS). 0 = silencieux, 1 = erreurs, 2 = avertissements, 3 = informations, 4 = debug.
export MVK_CONFIG_LOG_LEVEL="1"

# macOS : évite l'abandon OMP « libomp.dylib already initialized » quand plusieurs
# copies de libomp sont chargées (pycolmap pip + torch conda). Contournement
# non sûr ; à remplacer par une seule libomp si possible (voir tools/colmap/install-hloc.sh).
export KMP_DUPLICATE_LIB_OK=TRUE
