# Pipelines principaux

[Retour aux exemples](../examples/README.md) · [Guide Bash / Slurm](../doc/lancement_bash_slurm.md)

Les exemples ci-dessous utilisent `GIT_ROOT` et `DATA_ROOT` initialisés comme dans
[l'index](../examples/README.md). Choisir le pipeline selon les données d'entrée, puis
adapter sa configuration et choisir un mode de lancement.

## Flux des pipelines

Les trois entrées (vidéo, image, Panoramax) passent par des scripts de préparation
spécifiques, puis convergent vers les scripts communs (SfM, entraînement, export).

```text
Entrées        videos_to_3dgs.sh          images_to_3dgs.sh          panoramax_to_3dgs.sh
               │                          │                          │
               ▼                          ▼                          ▼
Préparation    prepare_video_frames.py    prepare_images.py          download.py
               │                          │                          │
               │                          │                          ▼
               │                          │                          prepare_panoramax.py
               │                          │                          │
               │                          │                          ▼
               │                          │                          prepare_equirect_views.py
               │                          │                          │
               ├──────────────────────────┘                          ▼
               ▼                                                     run_panoramax.sh
Pilotes        run_standard_images.sh                                │
               │                                                     │
               ├─────────────────────────────────────────────────────┘
               ▼
              =========================================================
               │                                                     
               ▼                
SfM            sfm_run.sh 
               │  lance le backend choisi par COLMAP_METHOD
               │
               │  backends/$COLMAP_METHOD.sh
               │    │
               │    ├─ spirula.sh      → spirula/sfm_spirula.sh
               │    ├─ colmap.sh       → colmap/reconstruct.py
               │    ├─ nerfstudio.sh   → colmap/reconstruct.py (nerfstudio)
               │    ├─ hloc.sh         → colmap/hloc_reconstruct.py
               │    └─ orientations.sh → colmap/orientations_to_colmap.py (importe des orientations existantes)
               │
               ▼
Découpage      colmap_split_blocks.py
               │
               ▼
Entraînement   train_block.sh   (dispatch selon TRAIN_BACKEND, dans training/)
               │    │
               │    ├─ spirula    → training/backends/train_block_spirula.sh
               │    └─ nerfstudio → training/backends/train_block_nerfstudio.py
               │
               ▼
Géoréférence   georef.sh
               │
               ▼
Export         export.sh
```

## Images locales → 3DGS

À partir d'un dossier de photos, ce pipeline sélectionne les images,
reconstruit les caméras et les points 3D, découpe la scène en blocs, entraîne
les gaussiennes, puis fusionne et exporte un fichier `.splat`. Les masques et
le géoréférencement sont optionnels.

Créer `config-images.sh` dans `DATA_ROOT` et adapter les valeurs :

```bash
SFM_IMAGES_DIR="$DATA_ROOT/images"
OUTPUT_DIR="$DATA_ROOT/results/images"
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
bash "$GIT_ROOT/scripts/images_to_3dgs.sh" "$DATA_ROOT/config-images.sh"

# Sur un serveur Slurm : soumission et attente de la fin.
bash "$GIT_ROOT/scripts/launch_images_to_3dgs.sh" "$DATA_ROOT/config-images.sh" --wait
```

Configuration avec des paramètres supplémentaires :

```bash
SFM_IMAGES_DIR="$DATA_ROOT/images"
OUTPUT_DIR="$DATA_ROOT/results/images-complet"
SFM_MAX_IMAGE_SIZE="2048"
SFM_ALIKED_MAX_FEATURES="2048"
SFM_MASKS="off"
TRAIN_MASKS="off"
NUM_CPUS="2"
```

- `SFM_IMAGES_DIR` : dossier source des photos.
- `OUTPUT_DIR` : dossier de sortie, distinct de `SFM_IMAGES_DIR`.
- `SFM_MAX_IMAGE_SIZE="2048"` : résolution maximale des images pour le SfM (défaut `4096`
  dans ce pipeline). Réduire cette valeur diminue fortement la mémoire GPU.
- `SFM_ALIKED_MAX_FEATURES="2048"` : nombre maximal de points ALIKED par image (défaut `8192`).
- `SFM_MASKS` et `TRAIN_MASKS="off"` : désactivent les masques, qui demandent des outils supplémentaires.
- `NUM_CPUS="2"` : nombre de CPU utilisés par le SfM, les masques et les processus parallèles.

## Vidéo → images → 3DGS

À partir d'une vidéo, ce pipeline extrait des images nettes réparties sur sa
durée, puis lance le pipeline images ci-dessus pour produire un `.splat`.

Créer `config-video.sh` dans `DATA_ROOT` et adapter les valeurs :

```bash
VIDEO="$DATA_ROOT/video.mp4"
NUM_FRAMES="200"
SFM_IMAGES_DIR="$DATA_ROOT/results/video-frames"
OUTPUT_DIR="$DATA_ROOT/results/video"
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
bash "$GIT_ROOT/scripts/videos_to_3dgs.sh" "$DATA_ROOT/config-video.sh" \
  --video "$DATA_ROOT/video.mp4" --num-frames 200 \
  --frames-dir "$DATA_ROOT/results/video-frames"

# Slurm : extraction sur le cluster, puis soumission du pipeline images.
bash "$GIT_ROOT/scripts/launch_videos_to_3dgs.sh" "$DATA_ROOT/config-video.sh" --wait
```

Configuration avec des paramètres supplémentaires :

```bash
VIDEO="$DATA_ROOT/video.mp4"
NUM_FRAMES="200"
SFM_IMAGES_DIR="$DATA_ROOT/results/video-frames-complet"
OUTPUT_DIR="$DATA_ROOT/results/video-complet"
SFM_MAX_IMAGE_SIZE="2048"
SFM_ALIKED_MAX_FEATURES="2048"
SFM_MASKS="off"
TRAIN_MASKS="off"
NUM_CPUS="2"
```

- `VIDEO` : vidéo source.
- `NUM_FRAMES="200"` : nombre d'images nettes extraites, réparties sur la durée de la vidéo.
- `SFM_IMAGES_DIR` : dossier où sont écrites les images extraites, distinct de `OUTPUT_DIR`.
- `OUTPUT_DIR` : dossier de sortie du pipeline images.
- `SFM_MAX_IMAGE_SIZE`, `SFM_ALIKED_MAX_FEATURES`, `SFM_MASKS`, `TRAIN_MASKS`, `NUM_CPUS` :
  mêmes effets que dans le pipeline images ci-dessus.

## Panoramax → 3DGS

À partir d'une emprise géographique, ce pipeline télécharge les photos
Panoramax et leurs métadonnées, reconstruit la scène, puis entraîne et exporte
un `.splat`. La préparation nécessite un accès réseau à Panoramax.

Créer `config-panoramax.sh` dans `DATA_ROOT`. Une configuration minimale suffit : l'emprise
Lambert-93 (`PANORAMAX_BBOX_EPSG` vaut `2154` par défaut) et le dossier de sortie ; les autres
paramètres prennent leurs valeurs par défaut.

```bash
OUTPUT_DIR="$DATA_ROOT/results/panoramax-test"
PANORAMAX_BBOX="655500,6861900,655700,6862100"
```

Les outils SfM et d'entraînement restent nécessaires. Deux modes de lancement sont disponibles :

```bash
# Direct : téléchargement et calcul sur la machine courante.
bash "$GIT_ROOT/scripts/panoramax_to_3dgs.sh" "$DATA_ROOT/config-panoramax.sh"

# Slurm : téléchargement et calcul sur les nœuds du cluster.
bash "$GIT_ROOT/scripts/launch_panoramax_to_3dgs.sh" "$DATA_ROOT/config-panoramax.sh" --wait
```

Configuration avec des paramètres supplémentaires :

```bash
OUTPUT_DIR="$DATA_ROOT/results/panoramax-complet"
PANORAMAX_BBOX="655500,6861900,655700,6862100"
PANORAMAX_PICTURE_TYPE="360"
PANORAMAX_FILTERS="geovisio:producer=gaelle.gabriel@paris.fr"
PANORAMAX_REPLACE_REQUEST="true"
SFM_MAX_IMAGE_SIZE="2048"
SFM_ALIKED_MAX_FEATURES="2048"
```

- `OUTPUT_DIR` : dossier de sortie (photos, SfM, blocs et `.splat`).
- `PANORAMAX_BBOX` : emprise en Lambert-93 (`PANORAMAX_BBOX_EPSG="2154"` par défaut).
- `PANORAMAX_PICTURE_TYPE="360"` : ne garde que les photos équirectangulaires. Avec Spirula,
  `SFM_CAMERA_MODEL` passe automatiquement à `equirectangular`.
- `PANORAMAX_FILTERS` : ne garde que les photos dont `geovisio:producer` vaut `gaelle.gabriel@paris.fr`.
- `PANORAMAX_REPLACE_REQUEST="true"` : si `OUTPUT_DIR` contient une autre requête, elle est
  archivée (`panoramax.replaced-DATE`) puis retéléchargée. Avec `false`, le lancement s'arrête.
- `SFM_MAX_IMAGE_SIZE="2048"` : résolution maximale des images pour le SfM (défaut `4096`).
  Réduire cette valeur diminue fortement la mémoire GPU.
- `SFM_ALIKED_MAX_FEATURES="2048"` : nombre maximal de points ALIKED par image (défaut `4096`).

Modifier ces deux paramètres SfM sur un workspace existant impose de le reconstruire
(voir `tools/panoramax/README.md`).

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

Le script `helper.sh` décrit les paramètres et leurs valeurs par défaut à partir
des fichiers de configuration du projet. Il ne lit pas les valeurs d'un
fichier utilisateur. Lui passer un paramètre ou une phase :

```bash
bash "$GIT_ROOT/scripts/helper.sh" COLMAP_METHOD HLOC_CAMERA_MODEL
bash "$GIT_ROOT/scripts/helper.sh" --phase slurm
```

Sans argument, le script liste les phases disponibles. Les paramètres propres
au lanceur vidéo `VIDEO` et `NUM_FRAMES` sont à renseigner comme indiqué dans
l'exemple vidéo ci-dessus.
