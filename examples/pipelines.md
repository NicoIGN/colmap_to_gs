# Pipelines principaux : exécution directe et Slurm

[Retour aux exemples](README.md) · [Guide Bash / Slurm](../doc/lancement_bash_slurm.md)

Les scripts sans `launch_` exécutent le pipeline directement sur la machine
courante, sans réserver de ressources Slurm. Les scripts `launch_` se lancent
avec `bash` depuis le cluster : ils soumettent les étapes à Slurm, qui réserve
les ressources configurées. Ne pas les lancer avec `sbatch`.

Sans `--wait`, leur succès indique que les jobs ont été soumis, pas qu'ils ont
terminé. Avec `--wait`, le lanceur attend le résultat de la fusion. Le lanceur
vidéo attend toujours la fin de l'extraction avant de soumettre les étapes
images. Les journaux sont dans `OUTPUT_DIR/logs/`.

Les exemples ci-dessous utilisent `GIT_ROOT` initialisé comme dans
[l'index](README.md). Adapter les chemins, la partition, les ressources et
les outils installés à la machine. Sur Slurm, le dépôt, les données, la
configuration et les sorties doivent être accessibles aux nœuds de calcul
aux mêmes chemins ; ne pas modifier la configuration pendant les jobs.
Choisir **une seule** des deux commandes pour un workspace donné.

## Images locales → 3DGS

Créer `config-images.sh` et adapter les valeurs :

```bash
SFM_IMAGES_DIR="$GIT_ROOT/data/images"
OUTPUT_DIR="$GIT_ROOT/results/images"
SFM_MASKS="off"
TRAIN_MASKS="off"
SLURM_PARTITION="partition_du_cluster"
```

Le dossier d'images doit exister ; le workspace doit être distinct du dossier
d'images. Les masques sont désactivés dans cet exemple. Le SfM et l'entraînement
utilisent Spirula par défaut ; installer et configurer les outils requis.

```bash
# Direct : exécution sur la machine courante.
bash "$GIT_ROOT/scripts/images_to_3dgs.sh" config-images.sh

# Slurm : soumission et attente de la fin.
bash "$GIT_ROOT/scripts/launch_images_to_3dgs.sh" config-images.sh --wait
```

## Vidéo → images → 3DGS

Créer `config-video.sh` et adapter les valeurs :

```bash
VIDEO="$GIT_ROOT/data/video.mp4"
NUM_FRAMES="200"
SFM_IMAGES_DIR="$GIT_ROOT/results/video-frames"
OUTPUT_DIR="$GIT_ROOT/results/video"
SFM_MASKS="off"
TRAIN_MASKS="off"
SLURM_PARTITION="partition_du_cluster"
```

La vidéo doit exister et le dossier d'images doit être distinct du workspace.
OpenCV, NumPy et tqdm sont nécessaires pour l'extraction. Le lanceur Slurm lit
`VIDEO`, `NUM_FRAMES` et `SFM_IMAGES_DIR` dans la configuration ; le script
direct prend la vidéo et le nombre d'images en arguments :

```bash
# Direct : extraction puis pipeline sur la machine courante.
bash "$GIT_ROOT/scripts/videos_to_3dgs.sh" config-video.sh \
  --video "$GIT_ROOT/data/video.mp4" --num-frames 200 \
  --frames-dir "$GIT_ROOT/results/video-frames"

# Slurm : extraction sur le cluster, puis soumission du pipeline images.
bash "$GIT_ROOT/scripts/launch_videos_to_3dgs.sh" config-video.sh --wait
```

## Panoramax → 3DGS

Créer `config-panoramax.sh` et adapter l'emprise Lambert-93 et les chemins :

```bash
OUTPUT_DIR="$GIT_ROOT/results/panoramax"
PANORAMAX_BBOX="651000,6861000,652000,6862000"
PANORAMAX_BBOX_EPSG="2154"
SFM_MASKS="off"
TRAIN_MASKS="off"
SLURM_PARTITION="partition_du_cluster"
```

La préparation nécessite un accès réseau à Panoramax. Les masques sont
désactivés ici ; les outils SfM et d'entraînement restent nécessaires.

```bash
# Direct : téléchargement et calcul sur la machine courante.
bash "$GIT_ROOT/scripts/panoramax_to_3dgs.sh" config-panoramax.sh

# Slurm : téléchargement et calcul sur les nœuds du cluster.
bash "$GIT_ROOT/scripts/launch_panoramax_to_3dgs.sh" config-panoramax.sh --wait
```

Pour les réglages de ressources, les environnements, les phases et le suivi,
consulter le [guide Bash / Slurm](../doc/lancement_bash_slurm.md). Les
lanceurs images et vidéo acceptent aussi `--prepare-only` pour s'arrêter
après la préparation, sans entraînement ni fusion.
