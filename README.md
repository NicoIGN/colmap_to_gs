# Images / COLMAP → Gaussian Splats (`.ply`) avec Nerfstudio

Ce dépôt entraîne un modèle Gaussian Splatting avec Nerfstudio / Splatfacto à partir d'un modèle COLMAP, l'exporte en `.ply`, puis le nettoie.

Exemple :

```bash
./scripts/run.sh \
  --dataset-dir /abs/path/to/dataset \
  --output-dir /abs/path/to/output \
  --gsplat-profile balanced
```

**Sommaire**

- [Objectif](#1-objectif)
- [Entrées et sorties](#2-entrées-et-sorties)
- [Configuration et utilisation](#3-configuration-et-utilisation)
- [Fonctionnement internes](#4-fonctionnement-internes)
- [Notes et informations complémentaires](#5-notes-et-informations-complémentaires)

## 1. Objectif

Le pipeline transforme un modèle COLMAP en Gaussian Splat entraîné avec Splatfacto, exporté en `.ply`, puis nettoyé. Il peut aussi créer un dataset COLMAP à partir d'un dossier d'images et lancer un serveur local de visualisation.

Flux principal :

```text
dossier d'images
   │  images_to_colmap.py (optionnel)
   ▼
<dataset-dir>/ : images/ + colmap/sparse/0/*.bin + sparse_pc.ply
   │
   │  scripts/run.sh
   ├─► colmap_to_transforms.py ─► transforms.json
   ├─► estimate_planes.py      ─► near / far
   ├─► train.sh (Splatfacto)   ─► model3d/splatfacto/<run>/
   ├─► export_splat_to_ply.sh  ─► model3d/splat.ply
   └─► clean_ply.sh / clean_ply.py
           (splat.ply + points3D.bin + dataparser_transforms.json)
                 ▼
       model3d/cleaned/<BASENAME>.ply
```

Étapes :

1. *(Optionnel)* `tools/preprocess/images_to_colmap.py` convertit un dossier d'images en dataset COLMAP.
2. `scripts/run.sh` génère `transforms.json`, estime `near` / `far`, entraîne Splatfacto, exporte le `.ply` et le nettoie.
3. *(Optionnel)* `tools/viewer/server.py` sert le résultat pour une visualisation locale.

## 2. Entrées et sorties

### Dataset d'entrée

Structure attendue :

```text
<dataset-dir>/
├── colmap/
│   └── sparse/
│       └── 0/
│           ├── cameras.bin
│           ├── images.bin
│           └── points3D.bin
├── images/
│   └── *.jpg / *.jpeg / *.png     (au moins 2)
└── sparse_pc.ply
```

- `sparse_pc.ply` peut provenir de COLMAP (par exemple via `images_to_colmap.py`) ou être enrichi avec des données externes, comme un nuage LAZ.
- `sparse_pc.ply` doit être exprimé dans le repère mondial COLMAP.
- `sparse_pc.ply` initialise les Gaussians ; sa densité, sa répartition, ses couleurs et sa cohérence avec les poses influencent fortement le résultat.

Rôle des fichiers :

- `colmap/sparse/0/cameras.bin` et `images.bin` : utilisés par `colmap_to_transforms.py`.
- `colmap/sparse/0/` (modèle complet) : utilisé par `estimate_planes.py`.
- `colmap/sparse/0/points3D.bin` : utilisé par `clean_ply.py`.
- `images/` : utilisé par Nerfstudio via `transforms.json`.
- `sparse_pc.ply` : utilisé par Nerfstudio pour initialiser les Gaussians via `ply_file_path`.
- `transforms.json` (généré) : utilisé par le dataparser Nerfstudio.

### Sorties

```text
<output-dir>/
└── model3d/
    ├── splatfacto/
    │   └── <timestamp>/
    │       ├── config.yml
    │       ├── dataparser_transforms.json
    │       └── nerfstudio_models/
    ├── splat.ply                  # export brut
    └── cleaned/
        └── <BASENAME>.ply         # livrable final

<dataset-dir>/transforms.json      # généré
```

`run.sh` renomme le résultat nettoyé en `<output-dir>/model3d/cleaned/<BASENAME>.ply`. Ce fichier est le livrable final.

Les journaux SLURM sont écrits dans :

```text
<OUTPUT_DIR>/logs/
├── submit.log
├── gsplat-<jobid>.out
└── gsplat-<jobid>.err
```

## 3. Configuration et utilisation

### Prérequis

- GPU NVIDIA : `nvidia-smi` doit répondre, sinon le pipeline s'arrête.
- Python 3.10 ou 3.11.
- Environnement Conda nommé `gsplat`, contenant Nerfstudio (`ns-train` et `ns-export`).
- Définition de l'environnement : `environment/ign.slurm/conda_env.yml`.

### Créer un dataset depuis des images

Cette étape est optionnelle. Le dossier produit peut être passé directement à `scripts/run.sh --dataset-dir`.

Commande :

```bash
python "$GIT_ROOT/tools/preprocess/images_to_colmap.py" \
  --image_dir /path/to/images \
  --output_dir /path/to/dataset \
  --camera_model OPENCV \
  --matcher sequential \
  --verbose \
  --no-gpu
```

Options (`option` : défaut ou valeurs possibles — description) :

- `--image_dir` : requis — dossier des images.
- `--output_dir` : `<image_dir>_colmap` — dossier de sortie.
- `--colmap` : `colmap` — exécutable COLMAP.
- `--matcher` : `exhaustive`, `sequential`, `spatial` ou `vocab_tree` (défaut : `exhaustive`) — méthode d'appariement.
- `--camera_model` : `SIMPLE_PINHOLE`, `PINHOLE`, `SIMPLE_RADIAL`, `RADIAL`, `OPENCV`, `FULL_OPENCV`, `SIMPLE_RADIAL_FISHEYE`, `RADIAL_FISHEYE`, `OPENCV_FISHEYE` ou `THIN_PRISM_FISHEYE` (défaut : `SIMPLE_RADIAL`) — modèle caméra COLMAP.
- `--separate_cameras` : désactivé — utilise une caméra par image au lieu d'une caméra partagée.
- `--max_image_size` : `3200` — taille maximale des images pour SIFT.
- `--no_gpu` : désactivé — force le mode CPU même si un environnement GPU est détecté.
- `--bundle_adjustment` : désactivé — effectue un bundle adjustment supplémentaire.
- `--verbose` : désactivé — affiche la sortie de COLMAP.

Dataset produit :

```text
<output_dir>/                      # défaut : <image_dir>_colmap
├── images/                        # copie des images source
├── colmap/
│   ├── database.db
│   ├── sparse/0/{cameras,images,points3D}.bin
│   └── sparse_txt/0/{cameras,images,points3D}.txt
└── sparse_pc.ply                  # PLY ASCII xyz + rgb issu de points3D
```

### Lancer `scripts/run.sh`

Options (`option` : défaut ou valeurs possibles — description) :

- `--dataset-dir` : requis — racine du dataset.
- `--output-dir`, `-o` : `runs/default` — dossier de sortie.
- `--name` : `gsplat_<timestamp>` — nom du `.ply` final.
- `--gsplat-profile` : aucun — profil d'entraînement.
- `--max-jobs` : `2` — nombre de tâches parallèles.
- `--two-stages` : désactivé — entraîne en deux passes (voir « Mode deux passes »).
- `--skip-conda` : désactivé — n'active pas l'environnement conda `gsplat`.
- `--skip-training` : désactivé — saute l'entraînement.
- `--skip-export` : désactivé — saute l'export et le nettoyage.
- `--no-proxy` : désactivé — désactive le proxy IGN (`proxy.ign.fr:3128`), activé par défaut.

### Configuration du pipeline

`config/config.sh` fixe notamment ces valeurs :

- `CONDA_ENV_NAME` : `gsplat`.
- `EXPERIMENT_NAME` : `model3d`.
- `MODEL_IMPLEMENTATION` : `torch` (`tcnn` possible si la stack le permet).
- `MAX_ITER` : `3000`.
- `REFINE_EVERY` : `100`.
- `STEPS_PER_SAVE` : `5000`.
- `STEPS_PER_EVAL_ALL_IMAGES` : `2000`.
- `CAMERA_RES_SCALE_FACTOR` : `0.5`.
- `NUM_DOWNSCALES` : `0`.
- `TRAIN_VIS_MODE` : `tensorboard`.

Les paramètres de densification et de culling sont vides par défaut et sont définis par les profils :

- `DENSIFY_GRAD_THRESH`
- `CULL_ALPHA_THRESH`
- `CULL_SCREEN_SIZE`
- `SPLIT_SCREEN_SIZE`
- `MAX_GAUSS_RATIO`
- `STOP_SPLIT_AT`
- `RESET_ALPHA_EVERY`
- `CULL_SCALE_THRESH`
- `USE_SCALE_REGULARIZATION`
- `USE_BILATERAL_GRID`
- `SSIM_LAMBDA`
- `MAX_RES`
- etc.

Profils fournis dans `config/profiles/` : `balanced`, `best`, `fast` et `quality`.

Pour utiliser des profils hors du dépôt :

```bash
export GSPLAT_PROFILE_PATH=/path/to/custom/profiles
```

Le premier fichier trouvé est utilisé :

```text
1. $GSPLAT_PROFILE_PATH/<profile>
2. $GSPLAT_PROFILE_PATH/<profile>.sh
3. <repo>/config/profiles/<profile>
4. <repo>/config/profiles/<profile>.sh
```

`estimate_planes.py` analyse le modèle COLMAP avant l'entraînement. Cette étape est toujours exécutée et transmet à l'entraînement `COLLIDER_NEAR`, `COLLIDER_FAR` et `ENABLE_COLLIDER=True`.

Mode deux passes (`--two-stages`) :

- Première passe : `CAMERA_RES_SCALE_FACTOR=0.5`, `NUM_DOWNSCALES=2`, `MAX_RES=8192` et densification active.
- Deuxième passe : reprise depuis le checkpoint, résolution du profil, `MAX_ITER + 1000` itérations et densification désactivée (`REFINE_EVERY=999999`, `STOP_SPLIT_AT=0`).

### Exécuter sur SLURM

Avec un profil fourni :

```bash
cd /path/to/data      # dossier contenant éventuellement config.sh

VERBOSE=1 \
GIT_ROOT=/path/to/repo \
OUTPUT_DIR=/path/to/output \
DATASET_DIR=/path/to/dataset \
GSPLAT_PROFILE=balanced \
BASENAME=my_scene \
bash "$GIT_ROOT/environment/ign.slurm/launch.sh" [partition]
```

`[partition]` est un argument optionnel à remplacer par le nom de la partition, ou à omettre.

Avec un profil personnalisé :

```bash
VERBOSE=1 \
GIT_ROOT=/path/to/repo \
OUTPUT_DIR=/path/to/output \
DATASET_DIR=/path/to/dataset \
GSPLAT_PROFILE_PATH=/path/to/custom/profiles \
GSPLAT_PROFILE=fast_compact \
BASENAME=my_scene \
bash "$GIT_ROOT/environment/ign.slurm/launch.sh"
```

Variables requises et optionnelles (`nom` : statut/défaut — description) :

- `GIT_ROOT` : requis — racine du dépôt.
- `OUTPUT_DIR` : requis — dossier de sortie (logs inclus).
- `DATASET_DIR` : requis — dataset passé à `run.sh`.
- `GSPLAT_PROFILE` : requis — profil d'entraînement.
- `BASENAME` : requis — nom du `.ply` final.
- `CONFIG_SH` : optionnel, défaut `./config.sh` relatif au dossier courant — fichier sourcé avant soumission et dans le job.
- `PARTITION` : optionnel, défaut `jean-zellou` ou premier argument — partition SLURM.
- `GSPLAT_PROFILE_PATH` : optionnel — dossier de profils personnalisés.
- `SKIP_TRAINING`, `SKIP_EXPORT` : optionnels — `true` active `--skip-training` / `--skip-export`.
- `VERBOSE` : optionnel — `1`/`true` affiche les logs complets et l'état CPU/GPU toutes les 120 s ; sinon les logs sont filtrés.

Les variables requises peuvent être passées dans l'environnement ou définies dans `CONFIG_SH`. Toutes les variables d'environnement sont transmises au job avec `--export=ALL`.

Ressources définies dans `launch.slurm` :

- Nœuds : `1`.
- GPU : `1`.
- CPU : `8`.
- Mémoire : `16 Go`.
- Durée maximale : `48 h`.

### Exemple complet depuis un dossier d'images

L'exemple utilise le dossier `poster/images` du dataset Nerfstudio. Télécharger les images depuis [Hugging Face](https://huggingface.co/datasets/nerfstudioteam/datasets/tree/main/poster/images), puis les placer dans `/tmp/nerfstudio_poster/images`.

Préparer le dossier :

```bash
mkdir -p /tmp/nerfstudio_poster
cd /tmp/nerfstudio_poster
```

Générer le dataset COLMAP compatible avec `scripts/run.sh` :

```bash
python "$GIT_ROOT/tools/preprocess/images_to_colmap.py" \
  --image_dir /tmp/nerfstudio_poster/images \
  --output_dir /tmp/nerfstudio_poster/poster_colmap \
  --camera_model OPENCV \
  --matcher sequential \
  --verbose
```

Le dossier généré peut être utilisé comme `--dataset-dir` :

```text
/tmp/nerfstudio_poster/poster_colmap/
├── images/
├── colmap/
│   ├── database.db
│   ├── sparse/0/
│   │   ├── cameras.bin
│   │   ├── images.bin
│   │   └── points3D.bin
│   └── sparse_txt/0/
│       ├── cameras.txt
│       ├── images.txt
│       └── points3D.txt
└── sparse_pc.ply
```

Créer `config.sh` dans le dossier de travail :

```bash
cat > /tmp/nerfstudio_poster/config.sh <<'EOF'
#!/usr/bin/env bash

GIT_ROOT=/path/to/repo
OUTPUT_DIR=/tmp/nerfstudio_poster/output
DATASET_DIR=/tmp/nerfstudio_poster/poster_colmap

GSPLAT_PROFILE=balanced
BASENAME=poster
VERBOSE=1
EOF
```

Adapter `GIT_ROOT` à l'emplacement local du dépôt.

Lancer le job SLURM depuis le dossier contenant `config.sh` :

```bash
cd /tmp/nerfstudio_poster

CONFIG_SH=/tmp/nerfstudio_poster/config.sh \
GIT_ROOT=/path/to/repo \
OUTPUT_DIR=/tmp/nerfstudio_poster/output \
bash "$GIT_ROOT/environment/ign.slurm/launch.sh"
```

Le script charge `config.sh`, soumet `environment/ign.slurm/launch.slurm` avec `sbatch`, transmet les variables au job, puis suit les logs jusqu'à la fin de l'exécution.

Le pipeline exécuté sur SLURM génère `/tmp/nerfstudio_poster/poster_colmap/transforms.json`, estime `near` / `far` depuis le modèle COLMAP, entraîne Splatfacto, exporte le Gaussian Splat en `.ply`, puis nettoie le `.ply` final.

Livrable final :

```text
/tmp/nerfstudio_poster/output/model3d/cleaned/poster.ply
```

Logs SLURM :

```text
/tmp/nerfstudio_poster/output/logs/
├── submit.log
├── gsplat-<jobid>.out
└── gsplat-<jobid>.err
```

## 4. Fonctionnement internes

### Création du dataset COLMAP

`images_to_colmap.py` :

1. Copie les images. Seul le premier niveau du dossier est lu ; extensions acceptées : jpg, jpeg, png, tif, tiff, webp. Il faut au moins 2 images.
2. Supprime les résultats d'un précédent lancement : `database.db`, `sparse/`, `sparse_txt/` et `sparse_pc.ply`.
3. Extrait les points caractéristiques SIFT.
4. Met en correspondance les images.
5. Reconstruit le modèle (`mapper`). Si COLMAP produit plusieurs modèles, celui qui contient le plus d'images est conservé dans `sparse/0`.
6. *(Avec `--bundle_adjustment`)* Effectue un bundle adjustment supplémentaire. Un échec à cette étape ne bloque pas la suite.
7. Convertit le modèle en texte, puis écrit `sparse_pc.ply`.

### Génération de `transforms.json`

Commande appelée par `run.sh` :

```bash
python3 python/colmap_to_transforms.py \
  --colmap-model <dataset-dir>/colmap/sparse/0 \
  --images-dir   <dataset-dir>/images \
  --output       <dataset-dir>/transforms.json \
  --image-prefix images \
  --ply-file-path sparse_pc.ply
```

Options :

- `--no-validate-images` : désactive la vérification de l'existence des images.
- `--ply-file-path ""` : omet le champ `ply_file_path`.

Conversion des poses et repères :

- COLMAP stocke des poses **world-to-camera** : X vers la droite, Y vers le bas et Z vers l'avant.
- `transforms.json` contient des poses **camera-to-world** au format OpenGL/Nerfstudio : X vers la droite, Y vers le haut et Z vers l'arrière.
- Le repère mondial COLMAP est conservé. Seuls les axes Y et Z locaux de chaque caméra sont inversés ; aucun recentrage ni changement d'échelle n'est appliqué.

Exemple schématique de contenu :

```json
{
  "w": 7952,
  "h": 5304,
  "camera_model": "OPENCV",
  "fl_x": 4737.2, "fl_y": 4737.2,
  "cx": 3956.9, "cy": 2611.9,
  "k1": 0.0, "k2": 0.0, "k3": 0.0, "k4": 0.0,
  "p1": 0.0, "p2": 0.0,
  "camera_angle_x": 1.39,
  "camera_angle_y": 1.02,
  "applied_transform": [[1,0,0,0],[0,1,0,0],[0,0,1,0]],
  "applied_scale": 1.0,
  "ply_file_path": "sparse_pc.ply",
  "frames": [
    {
      "file_path": "./images/image.jpg",
      "transform_matrix": [[...], [...], [...], [0, 0, 0, 1]],
      "colmap_im_id": 1
    }
  ]
}
```

Les `...` représentent les valeurs de la matrice caméra ; ils ne constituent pas du JSON valide. Si toutes les images partagent les mêmes paramètres caméra, les intrinsèques sont écrits à la racine ; sinon, ils sont écrits dans chaque `frame`.

Modèles caméra :

- Le champ `camera_model` vaut toujours `OPENCV` ou `OPENCV_FISHEYE`.
- Modèles COLMAP `SIMPLE_PINHOLE`, `PINHOLE`, `SIMPLE_RADIAL`, `RADIAL`, `OPENCV` : `OPENCV`.
- Modèles COLMAP `OPENCV_FISHEYE`, `SIMPLE_RADIAL_FISHEYE`, `RADIAL_FISHEYE` : `OPENCV_FISHEYE`.
- Modèles COLMAP `FULL_OPENCV`, `FOV`, `THIN_PRISM_FISHEYE` : refusés.

Champs de transformation globale :

- `applied_transform` : toujours l'identité.
- `applied_scale` : toujours `1.0`.
- Ces champs ne doivent pas être confondus avec `dataparser_transforms.json`, produit par Nerfstudio pendant l'entraînement.

### Exécution et traitement

`scripts/run.sh` effectue les opérations suivantes :

1. Vérifie le dataset.
2. Génère `transforms.json` avec `colmap_to_transforms.py`.
3. Charge `config/config.sh`, puis le profil.
4. Vérifie le GPU, active Conda si demandé et vérifie la version de Python.
5. Estime `near` / `far` avec `estimate_planes.py`.
6. Entraîne Splatfacto avec `scripts/train.sh`.
7. Exporte le `.ply` avec `tools/postprocess/export_splat_to_ply.sh`.
8. Nettoie le `.ply` avec `tools/postprocess/clean_ply.sh` et `tools/postprocess/clean_ply.py`.

Export :

- `tools/postprocess/export_splat_to_ply.sh` prend le run le plus récent dans `<output-dir>/model3d/splatfacto/`.
- Il lance `ns-export gaussian-splat --load-config <run>/config.yml`.
- Il écrit le `.ply` dans `<output-dir>/model3d/`.
- Le script peut archiver le run en ZIP : `ZIP_RUN=1` par défaut ; `run.sh` désactive cette option avec `ZIP_RUN=0`.

Nettoyage : `tools/postprocess/clean_ply.sh` appelle `tools/postprocess/clean_ply.py` :

```bash
tools/postprocess/clean_ply.sh \
  --transform <run>/dataparser_transforms.json \
  --points <dataset-dir>/colmap/sparse/0/points3D.bin \
  --in-ply <output-dir>/model3d/splat.ply \
  --out-ply <output-dir>/model3d/cleaned/scene.ply
```

- `--in-ply` : chemin du `.ply` exporté à nettoyer.
- `--points` : `<dataset-dir>/colmap/sparse/0/points3D.bin`.
- `--transform` : `dataparser_transforms.json` le plus récent sous `<out>`.
- `--out-ply` : chemin du `.ply` nettoyé à créer.

Ces quatre chemins sont fournis explicitement au lanceur ; `run.sh` les prépare automatiquement. Le script utilise `dataparser_transforms.json` — la transformation globale appliquée par Nerfstudio pendant l'entraînement — et non `transforms.json`.

### Fonctionnement de SLURM

`environment/ign.slurm/launch.sh` soumet `launch.slurm` avec `sbatch`, affiche les logs en direct jusqu'à la fin du job et renvoie `0` si l'état final est `COMPLETED`, `1` sinon. Le job active l'environnement conda `gsplat`, puis lance `run.sh` avec `--skip-conda`.

## 5. Notes et informations complémentaires

### Compatibilité des modèles caméra

> ⚠️ `FULL_OPENCV` et `THIN_PRISM_FISHEYE` sont acceptés par `images_to_colmap.py`, mais refusés par `colmap_to_transforms.py`. Avec ces modèles, `run.sh` échouera. Utilise `PINHOLE`, `SIMPLE_RADIAL`, `RADIAL`, `OPENCV` ou un modèle fisheye pris en charge.

> ℹ️ Seules les images que COLMAP a réussi à placer apparaissent dans le modèle, donc dans `transforms.json`.

### Fichier `transforms.json`

Le fichier `transforms.json` requis par Nerfstudio est généré dans `<dataset-dir>/transforms.json` s'il n'existe pas déjà. Un fichier existant est conservé ; supprime-le explicitement pour le régénérer depuis le modèle COLMAP.

### Chemins des scripts

Tous les scripts peuvent être lancés depuis n'importe quel dossier : les chemins relatifs passés en argument sont résolus depuis le dossier courant, et les scripts du dépôt sont retrouvés à partir de leur propre emplacement.

> ⚠️ Utilise des **chemins absolus** avec `run.sh`. Le script se place dans `scripts/` avant de résoudre les chemins : un chemin relatif est donc interprété depuis `scripts/`. C'est aussi le cas de la valeur par défaut `runs/default`.

L'exécutable Spirula est choisi dans cet ordre de priorité : `--spirula-bin`, `SPIRULA_BIN`, `spirula` dans le `PATH`, puis `<dépôt>/../spirula-studio/build/spirula`.

### Visualisation locale

Lancer le serveur :

```bash
python tools/viewer/server.py
```

- Sert le dossier `tools/viewer/html/` sur **`http://127.0.0.1:8000/`**.
- Tente d'ouvrir le navigateur automatiquement.
- Accepte un fichier local optionnel et `--data-directory` pour exposer un dossier de données.
- Accepte `--port` pour changer le port et `--no-browser` pour ne pas ouvrir le navigateur.
- N'écoute qu'en local.

Arrêt : `Ctrl+C`. Voir le [guide des visualiseurs](tools/viewer/README.md).

### Autres guides et outils

- [Guide utilisateur](doc/guide_utilisateur.md).
- Télécharger des images géolocalisées : [extraction Panoramax par bbox Lambert-93](tools/panoramax/README.md).
- Images locales (sous-dossiers optionnels) : [pipeline images vers splat, Bash et Slurm](tools/images_to_splat/README.md).
- Pour convertir un `.splat` géoréférencé en tileset 3D Tiles, voir le [pipeline `.splat` vers 3D Tiles](tools/splat_to_3dtiles/README.md).

### Organisation du dépôt

```text
├── config
│   ├── config.sh                  # paramètres par défaut du pipeline
│   └── profiles/                  # profils : balanced, best, fast, quality
├── environment
│   └── ign.slurm
│       ├── conda_env.yml          # environnement conda
│       ├── launch.sh              # soumission SLURM + suivi des logs
│       └── launch.slurm           # job SLURM (appelle scripts/run.sh)
├── python
│   ├── colmap_to_transforms.py    # modèle COLMAP → transforms.json
│   └── estimate_planes.py        # estimation near / far
├── scripts
│   ├── run.sh                     # point d'entrée du pipeline
│   ├── run_spirula*.sh            # pipelines Spirula
│   ├── run_spirula_blocks.sh      # découpe SfM et entraînement par blocs
│   ├── spirula_common.sh          # exécutable Spirula par défaut
│   ├── train.sh
│   └── torch_stack_check.sh       # validation PyTorch / CUDA
└── tools
    ├── preprocess/                # images, vidéo, géoréférencement, blocs COLMAP
    ├── postprocess/               # nettoyage, export, fusion, GeoPackage
    ├── viewer/
    │   ├── server.py              # visualiseur PLY / SPLAT
    │   ├── html/                  # interface et modules JavaScript
    │   └── 3dtiles/               # visualiseur CesiumJS + OpenStreetMap
    ├── panoramax/                 # récupération d'images par bbox Lambert-93
    ├── images_to_splat/           # images locales → splat, Bash et Slurm
    ├── spirula/                   # installation et compilation de Spirula Studio
    └── splat_to_3dtiles/           # conversion SPLAT → 3D Tiles
```
