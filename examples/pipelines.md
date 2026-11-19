# Pipelines principaux

[Retour aux exemples](README.md) · [Guide Bash / Slurm](../doc/lancement_bash_slurm.md)

Les exemples ci-dessous utilisent `GIT_ROOT` initialisé comme dans
[l'index](README.md). Choisir le pipeline selon les données d'entrée, puis
adapter sa configuration et choisir un mode de lancement.

## Images locales → 3DGS

À partir d'un dossier de photos, ce pipeline sélectionne les images,
reconstruit les caméras et les points 3D, découpe la scène en blocs, entraîne
les gaussiennes, puis fusionne et exporte un fichier `.splat`. Les masques et
le géoréférencement sont optionnels.

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

Deux modes de lancement sont disponibles :

```bash
# Directement sur la machine courante.
bash "$GIT_ROOT/scripts/images_to_3dgs.sh" config-images.sh

# Sur un serveur Slurm : soumission et attente de la fin.
bash "$GIT_ROOT/scripts/launch_images_to_3dgs.sh" config-images.sh --wait
```

## Vidéo → images → 3DGS

À partir d'une vidéo, ce pipeline extrait des images nettes réparties sur sa
durée, puis lance le pipeline images ci-dessus pour produire un `.splat`.

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
OpenCV, NumPy et tqdm sont nécessaires pour l'extraction.

Deux modes de lancement sont disponibles. Le lanceur Slurm lit `VIDEO`,
`NUM_FRAMES` et `SFM_IMAGES_DIR` dans la configuration ; le script direct
prend la vidéo et le nombre d'images en arguments :

```bash
# Direct : extraction puis pipeline sur la machine courante.
bash "$GIT_ROOT/scripts/videos_to_3dgs.sh" config-video.sh \
  --video "$GIT_ROOT/data/video.mp4" --num-frames 200 \
  --frames-dir "$GIT_ROOT/results/video-frames"

# Slurm : extraction sur le cluster, puis soumission du pipeline images.
bash "$GIT_ROOT/scripts/launch_videos_to_3dgs.sh" config-video.sh --wait
```

## Panoramax → 3DGS

À partir d'une emprise géographique, ce pipeline télécharge les photos
Panoramax et leurs métadonnées, reconstruit la scène, puis entraîne et exporte
un `.splat`. La préparation nécessite un accès réseau à Panoramax.

Créer `config-panoramax.sh` et adapter l'emprise Lambert-93 et les chemins :

```bash
OUTPUT_DIR="$GIT_ROOT/results/panoramax"
PANORAMAX_BBOX="651000,6861000,652000,6862000"
PANORAMAX_BBOX_EPSG="2154"
SFM_MASKS="off"
TRAIN_MASKS="off"
SLURM_PARTITION="partition_du_cluster"
```

Les masques sont désactivés ici ; les outils SfM et d'entraînement restent
nécessaires. Deux modes de lancement sont disponibles :

```bash
# Direct : téléchargement et calcul sur la machine courante.
bash "$GIT_ROOT/scripts/panoramax_to_3dgs.sh" config-panoramax.sh

# Slurm : téléchargement et calcul sur les nœuds du cluster.
bash "$GIT_ROOT/scripts/launch_panoramax_to_3dgs.sh" config-panoramax.sh --wait
```

## Choisir le mode de lancement

Le script sans `launch_` travaille directement sur la machine courante, sans
réserver de ressources Slurm. Le script `launch_` se lance avec `bash` depuis
un serveur Slurm ; il soumet les étapes au cluster, qui réserve les ressources
configurées. Ne pas lancer `launch_` avec `sbatch`.

Sans `--wait`, un retour réussi confirme la soumission, pas la fin du calcul.
Avec `--wait`, le lanceur attend le résultat de la fusion. Le lanceur vidéo
attend toujours la fin de l'extraction avant de soumettre le pipeline images.
Les lanceurs images et vidéo acceptent `--prepare-only` pour arrêter après la
préparation, sans entraînement ni fusion. Les journaux sont dans
`OUTPUT_DIR/logs/`.

Sur Slurm, le dépôt, les données, la configuration et les sorties doivent être
accessibles aux mêmes chemins depuis les nœuds de calcul. Adapter la partition
et les ressources au cluster, ne pas modifier la configuration pendant les
jobs et choisir une seule commande par workspace. Pour les ressources, les
environnements et le suivi, consulter le [guide Bash / Slurm](../doc/lancement_bash_slurm.md).

## Consulter les paramètres avec le helper

Le helper `monparam` décrit les paramètres et leurs valeurs par défaut à partir
des fichiers de configuration du projet. Il ne lit pas les valeurs d'un
fichier utilisateur. Charger le helper, puis demander un paramètre ou une phase :

```bash
source "$GIT_ROOT/scripts/helper.sh"
monparam COLMAP_METHOD HLOC_CAMERA_MODEL
monparam --phase slurm
```

Sans argument, `monparam` liste les phases disponibles. Les paramètres propres
au lanceur vidéo `VIDEO` et `NUM_FRAMES` sont à renseigner comme indiqué dans
l'exemple vidéo ci-dessus.
