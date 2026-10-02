# Images / COLMAP → Gaussian Splats (`.ply`) avec Nerfstudio

Ce dépôt entraîne un modèle **Gaussian Splatting** avec **Nerfstudio / Splatfacto** à partir d'un modèle COLMAP, l'exporte en `.ply`, puis le nettoie.

## Sommaire

1. [Présentation du projet](#1-présentation-du-projet)
2. [Installation et prérequis](#2-installation-et-prérequis)
3. [Préparation du dataset](#3-préparation-du-dataset)
4. [Exécution et configuration du pipeline](#4-exécution-et-configuration-du-pipeline)
5. [Export, nettoyage et visualisation](#5-export-nettoyage-et-visualisation)
6. [Exécution sur SLURM](#6-exécution-sur-slurm)
7. [Exemple complet depuis un dossier d'images](#7-exemple-complet-depuis-un-dossier-dimages)

---

## 1. Présentation du projet

### 1.1. Étapes du pipeline

Le pipeline se déroule en plusieurs étapes :

1. *(optionnel)* `python/images_to_colmap.py` : dossier d'images → dataset COLMAP prêt à l'emploi ;
2. `scripts/run.sh` :
   - génération de `transforms.json` depuis COLMAP ;
   - estimation de `near` / `far` ;
   - entraînement Splatfacto ;
   - export du `.ply` ;
   - nettoyage du `.ply` ;
3. *(optionnel)* `python/server.py` : serveur web local de visualisation du résultat.

### 1.2. Organisation du dépôt

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
│   ├── images_to_colmap.py        # images → modèle COLMAP + sparse_pc.ply
│   ├── colmap_to_transforms.py    # modèle COLMAP → transforms.json
│   ├── estimate_planes.py        # estimation near / far
│   ├── clean_ply.py               # nettoyage du .ply exporté
│   ├── server.py                  # serveur de visualisation
│   ├── html/index.html            # page de visualisation
│   └── torch_stack_check.py
└── scripts
    ├── run.sh                     # point d'entrée du pipeline
    ├── train.sh
    ├── export_splat_to_ply.sh
    └── clean_ply.sh
```

### 1.3. Résumé du flux

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

---

## 2. Installation et prérequis

### 2.1. GPU et Python

- Un GPU NVIDIA : `nvidia-smi` doit répondre, sinon le pipeline s'arrête.
- Python 3.10 ou 3.11.

### 2.2. Environnement Conda

Un environnement conda nommé `gsplat`, contenant Nerfstudio (`ns-train` et `ns-export`), est nécessaire.

Le dépôt fournit le fichier de définition suivant pour préparer cet environnement :

```text
environment/ign.slurm/conda_env.yml
```

---

## 3. Préparation du dataset

### 3.1. Créer un dataset depuis un dossier d'images — optionnel

#### 3.1.1. Commande

```bash
python "$GIT_ROOT/python/images_to_colmap.py" \
  --image_dir /path/to/images \
  --output_dir /path/to/dataset \
  --camera_model OPENCV \
  --matcher sequential \
  --verbose \
  --no-gpu
```

Le dossier produit peut être passé directement à `scripts/run.sh --dataset-dir`.

#### 3.1.2. Étapes de reconstruction

1. Copie des images. Seul le premier niveau du dossier est lu. Extensions acceptées : jpg, jpeg, png, tif, tiff, webp. Il faut au moins 2 images.
2. Suppression des résultats d'un précédent lancement : `database.db`, `sparse/`, `sparse_txt/` et `sparse_pc.ply`.
3. Extraction des points caractéristiques SIFT.
4. Appariement des images.
5. Reconstruction (`mapper`). Si COLMAP produit plusieurs modèles, celui qui contient le plus d'images est conservé dans `sparse/0`.
6. *(si `--bundle_adjustment`)* Bundle adjustment supplémentaire. Un échec à cette étape ne bloque pas la suite.
7. Conversion du modèle en texte, puis écriture de `sparse_pc.ply`.

#### 3.1.3. Options

| Option | Défaut | Description |
|---|---|---|
| `--image_dir` | *(requis)* | dossier des images |
| `--output_dir` | `<image_dir>_colmap` | dossier de sortie |
| `--colmap` | `colmap` | exécutable COLMAP |
| `--matcher` | `exhaustive` | `exhaustive`, `sequential`, `spatial`, `vocab_tree` |
| `--camera_model` | `SIMPLE_RADIAL` | modèle caméra COLMAP : `SIMPLE_PINHOLE`, `PINHOLE`, `SIMPLE_RADIAL`, `RADIAL`, `OPENCV`, `FULL_OPENCV`, `SIMPLE_RADIAL_FISHEYE`, `RADIAL_FISHEYE`, `OPENCV_FISHEYE`, `THIN_PRISM_FISHEYE` |
| `--separate_cameras` | désactivé | une caméra par image (sinon une seule caméra partagée) |
| `--max_image_size` | `3200` | taille maximale des images pour SIFT |
| `--no_gpu` | désactivé | force le mode CPU même si un environnement GPU est détecté |
| `--bundle_adjustment` | désactivé | bundle adjustment supplémentaire |
| `--verbose` | désactivé | affiche la sortie de COLMAP |

#### 3.1.4. Dataset produit

```text
<output_dir>/                      # défaut : <image_dir>_colmap
├── images/                        # copie des images source
├── colmap/
│   ├── database.db
│   ├── sparse/0/{cameras,images,points3D}.bin
│   └── sparse_txt/0/{cameras,images,points3D}.txt
└── sparse_pc.ply                  # PLY ASCII xyz + rgb issu de points3D
```

#### 3.1.5. Compatibilité et images enregistrées

> ⚠️ `FULL_OPENCV` et `THIN_PRISM_FISHEYE` sont acceptés ici, mais refusés par `colmap_to_transforms.py`. Avec ces modèles, `run.sh` échouera. Utilise `PINHOLE`, `SIMPLE_RADIAL`, `RADIAL`, `OPENCV` ou un modèle fisheye pris en charge.

> ℹ️ Seules les images que COLMAP a réussi à placer apparaissent dans le modèle, donc dans `transforms.json`.

### 3.2. Structure attendue par le pipeline

#### 3.2.1. Fichiers d'entrée

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

Le fichier `transforms.json` requis par Nerfstudio est généré, et écrasé, dans `<dataset-dir>/transforms.json` à chaque lancement.

#### 3.2.2. Nuage initial : `sparse_pc.ply`

`sparse_pc.ply` est une entrée. Il peut provenir de COLMAP (par exemple via `images_to_colmap.py`) ou avoir été enrichi avec des données externes, comme un nuage LAZ.

Il doit être exprimé **dans le repère mondial COLMAP**.

Il sert à initialiser les Gaussians : sa densité, sa répartition, ses couleurs et sa cohérence avec les poses influencent fortement le résultat.

#### 3.2.3. Rôle des fichiers

| Fichier | Utilisé par |
|---|---|
| `colmap/sparse/0/cameras.bin`, `images.bin` | `colmap_to_transforms.py` |
| `colmap/sparse/0/` (modèle complet) | `estimate_planes.py` |
| `colmap/sparse/0/points3D.bin` | `clean_ply.py` |
| `images/` | Nerfstudio, via `transforms.json` |
| `sparse_pc.ply` | Nerfstudio, initialisation des Gaussians via `ply_file_path` |
| `transforms.json` *(généré)* | dataparser Nerfstudio |

### 3.3. Génération de `transforms.json`

#### 3.3.1. Commande et options

Commande appelée par `run.sh` :

```bash
python3 python/colmap_to_transforms.py \
  --colmap-model <dataset-dir>/colmap/sparse/0 \
  --images-dir   <dataset-dir>/images \
  --output       <dataset-dir>/transforms.json \
  --image-prefix images \
  --ply-file-path sparse_pc.ply
```

Le script accepte aussi :

- `--no-validate-images` : désactive la vérification de l'existence des images ;
- `--ply-file-path ""` : permet de ne pas écrire le champ `ply_file_path`.

#### 3.3.2. Conversion des poses et repères

- COLMAP stocke des poses **world-to-camera** :
  - X vers la droite ;
  - Y vers le bas ;
  - Z vers l'avant.
- `transforms.json` contient des poses **camera-to-world** au format OpenGL/Nerfstudio :
  - X vers la droite ;
  - Y vers le haut ;
  - Z vers l'arrière.
- **Le repère mondial COLMAP est conservé.** Seuls les axes Y et Z locaux de chaque caméra sont inversés. Aucun recentrage ni changement d'échelle n'est appliqué.

#### 3.3.3. Structure du fichier

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

Les `...` représentent ici les valeurs de la matrice caméra ; ils ne constituent pas du JSON valide.

**Intrinsèques :** si toutes les images partagent les mêmes paramètres caméra, ceux-ci sont écrits à la racine. Sinon, ils sont écrits dans chaque `frame`.

#### 3.3.4. Modèles caméra

Le champ `camera_model` vaut toujours `OPENCV` ou `OPENCV_FISHEYE`.

| Modèle COLMAP | `camera_model` |
|---|---|
| `SIMPLE_PINHOLE`, `PINHOLE`, `SIMPLE_RADIAL`, `RADIAL`, `OPENCV` | `OPENCV` |
| `OPENCV_FISHEYE`, `SIMPLE_RADIAL_FISHEYE`, `RADIAL_FISHEYE` | `OPENCV_FISHEYE` |
| `FULL_OPENCV`, `FOV`, `THIN_PRISM_FISHEYE` | **refusé** |

#### 3.3.5. Champs de transformation globale

- `applied_transform` : toujours l'identité ;
- `applied_scale` : toujours `1.0`.

Ces champs ne doivent **pas être confondus avec `dataparser_transforms.json`**, que Nerfstudio produit pendant l'entraînement.

---

## 4. Exécution et configuration du pipeline

### 4.1. Lancement avec `scripts/run.sh`

#### 4.1.1. Commande

```bash
./scripts/run.sh \
  --dataset-dir /abs/path/to/dataset \
  --output-dir /abs/path/to/output \
  --gsplat-profile balanced
```

> ⚠️ Utilise des **chemins absolus**. `run.sh` se place dans `scripts/` avant de résoudre les chemins : un chemin relatif est donc interprété depuis `scripts/`. C'est aussi le cas de la valeur par défaut `runs/default`.

#### 4.1.2. Options

| Option | Défaut | Description |
|---|---|---|
| `--dataset-dir` | *(requis)* | racine du dataset |
| `--output-dir`, `-o` | `runs/default` | dossier de sortie |
| `--name` | `gsplat_<timestamp>` | nom du `.ply` final |
| `--gsplat-profile` | aucun | profil d'entraînement |
| `--max-jobs` | `2` | nombre de tâches parallèles |
| `--two-stages` | désactivé | entraînement en deux passes, voir plus bas |
| `--skip-conda` | désactivé | n'active pas l'environnement conda `gsplat` |
| `--skip-training` | désactivé | saute l'entraînement |
| `--skip-export` | désactivé | saute l'export **et** le nettoyage |
| `--no-proxy` | désactivé | désactive le proxy IGN (`proxy.ign.fr:3128`), activé par défaut |

#### 4.1.3. Étapes exécutées

Le script effectue les opérations suivantes :

1. Vérification du dataset.
2. **Génération de `transforms.json`** (`colmap_to_transforms.py`).
3. Chargement de `config/config.sh`, puis du profil.
4. Vérification du GPU, activation de Conda si demandée et vérification de la version de Python.
5. **Estimation de `near` / `far`** (`estimate_planes.py`).
6. **Entraînement Splatfacto** (`scripts/train.sh`).
7. **Export du `.ply`** (`scripts/export_splat_to_ply.sh`).
8. **Nettoyage** (`scripts/clean_ply.sh` → `python/clean_ply.py`).

### 4.2. Configuration générale

#### 4.2.1. Valeurs de `config/config.sh`

Ce fichier fixe notamment les valeurs suivantes :

| Variable | Valeur |
|---|---|
| `CONDA_ENV_NAME` | `gsplat` |
| `EXPERIMENT_NAME` | `model3d` |
| `MODEL_IMPLEMENTATION` | `torch` (`tcnn` possible si la stack le permet) |
| `MAX_ITER` | `3000` |
| `REFINE_EVERY` | `100` |
| `STEPS_PER_SAVE` | `5000` |
| `STEPS_PER_EVAL_ALL_IMAGES` | `2000` |
| `CAMERA_RES_SCALE_FACTOR` | `0.5` |
| `NUM_DOWNSCALES` | `0` |
| `TRAIN_VIS_MODE` | `tensorboard` |

#### 4.2.2. Paramètres définis par les profils

Les paramètres de densification et de culling sont laissés vides par défaut. Ils sont définis par les profils :

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

### 4.3. Profils d'entraînement

#### 4.3.1. Profils fournis

Profils fournis dans `config/profiles/` :

- `balanced`
- `best`
- `fast`
- `quality`

#### 4.3.2. Profils personnalisés

Pour utiliser des profils hors du dépôt :

```bash
export GSPLAT_PROFILE_PATH=/path/to/custom/profiles
```

#### 4.3.3. Ordre de recherche

Le premier fichier trouvé est utilisé :

```text
1. $GSPLAT_PROFILE_PATH/<profile>
2. $GSPLAT_PROFILE_PATH/<profile>.sh
3. <repo>/config/profiles/<profile>
4. <repo>/config/profiles/<profile>.sh
```

### 4.4. Estimation de `near` et `far`

`estimate_planes.py` analyse le modèle COLMAP avant l'entraînement.

Cette étape est **toujours exécutée** et transmet les valeurs suivantes à l'entraînement :

- `COLLIDER_NEAR`
- `COLLIDER_FAR`
- `ENABLE_COLLIDER=True`

### 4.5. Mode deux passes : `--two-stages`

#### 4.5.1. Première passe

- `CAMERA_RES_SCALE_FACTOR=0.5`
- `NUM_DOWNSCALES=2`
- `MAX_RES=8192`
- densification active.

#### 4.5.2. Deuxième passe

- reprise depuis le checkpoint ;
- résolution du profil ;
- `MAX_ITER + 1000` itérations ;
- densification désactivée :
  - `REFINE_EVERY=999999`
  - `STOP_SPLIT_AT=0`

---

## 5. Export, nettoyage et visualisation

### 5.1. Export du modèle Gaussian Splatting

#### 5.1.1. Sélection du run et export

`scripts/export_splat_to_ply.sh` :

- prend le run le plus récent dans `<output-dir>/model3d/splatfacto/` ;
- lance `ns-export gaussian-splat --load-config <run>/config.yml` ;
- écrit le `.ply` dans `<output-dir>/model3d/`.

#### 5.1.2. Archivage ZIP

Le script peut aussi archiver le run en ZIP :

- `ZIP_RUN=1` par défaut ;
- `run.sh` désactive cette option avec `ZIP_RUN=0`.

### 5.2. Nettoyage du PLY

#### 5.2.1. Appel et fichiers utilisés

La commande suivante appelle `python/clean_ply.py` :

```bash
scripts/clean_ply.sh \
  --dataset-dir <dataset-dir> \
  --output-dir <output-dir>/model3d
```

Les arguments transmis sont :

| Argument | Valeur |
|---|---|
| `--in-ply` | premier `.ply` trouvé dans `<out>/`, `<out>/model3d/` ou `<out>/exports/` |
| `--points` | `<dataset-dir>/colmap/sparse/0/points3D.bin` |
| `--transform` | `dataparser_transforms.json` le plus récent sous `<out>` |
| `--out-ply` | `<out>/cleaned/<nom>_cleaned.ply` |

Ici, `<out>` désigne le dossier passé à `clean_ply.sh --output-dir`.

Le script utilise `dataparser_transforms.json` — la transformation globale appliquée par Nerfstudio pendant l'entraînement — et non `transforms.json`.

#### 5.2.2. Livrable final

`run.sh` renomme ensuite le résultat en :

```text
<output-dir>/model3d/cleaned/<BASENAME>.ply
```

**Ce fichier constitue le livrable final du pipeline.**

### 5.3. Arborescence des sorties

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

### 5.4. Visualisation locale

#### 5.4.1. Lancement du serveur

```bash
python python/server.py
```

#### 5.4.2. Accès et arrêt

Ce script :

- sert le dossier `python/html/` sur **`http://127.0.0.1:8000/`** ;
- tente d'ouvrir le navigateur automatiquement ;
- ne prend aucun argument ;
- utilise un port et un hôte fixés dans le script ;
- n'écoute qu'en local.

Arrêt : `Ctrl+C`.

---

## 6. Exécution sur SLURM

### 6.1. Fonctionnement

`environment/ign.slurm/launch.sh` :

1. soumet `launch.slurm` avec `sbatch` ;
2. affiche les logs en direct jusqu'à la fin du job ;
3. renvoie `0` si l'état final est `COMPLETED`, `1` sinon.

Le job active l'environnement conda `gsplat`, puis lance `run.sh` avec `--skip-conda`.

### 6.2. Commandes de lancement

#### 6.2.1. Avec un profil fourni

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

`[partition]` représente un argument optionnel à remplacer par le nom de la partition, ou à omettre.

#### 6.2.2. Avec un profil personnalisé

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

### 6.3. Variables de configuration

#### 6.3.1. Variables requises et optionnelles

| Variable | Requise | Description |
|---|---|---|
| `GIT_ROOT` | oui | racine du dépôt |
| `OUTPUT_DIR` | oui | dossier de sortie (logs inclus) |
| `DATASET_DIR` | oui | dataset passé à `run.sh` |
| `GSPLAT_PROFILE` | oui | profil d'entraînement |
| `BASENAME` | oui | nom du `.ply` final |
| `CONFIG_SH` | non | fichier sourcé avant soumission et dans le job (défaut : `./config.sh`, relatif au dossier courant) |
| `PARTITION` | non | partition SLURM (défaut : `jean-zellou`, ou 1er argument) |
| `GSPLAT_PROFILE_PATH` | non | dossier de profils personnalisés |
| `SKIP_TRAINING`, `SKIP_EXPORT` | non | `true` → `--skip-training` / `--skip-export` |
| `VERBOSE` | non | `1`/`true` : logs complets + état CPU/GPU toutes les 120 s ; sinon logs filtrés |

#### 6.3.2. Transmission au job

Les variables requises peuvent être passées en environnement ou définies dans `CONFIG_SH`.

Toutes les variables d'environnement sont transmises au job avec `--export=ALL`.

### 6.4. Ressources du job

Ressources définies dans `launch.slurm` :

| Ressource | Valeur |
|---|---|
| Nœuds | 1 |
| GPU | 1 |
| CPU | 8 |
| Mémoire | 16 Go |
| Durée maximale | 48 h |

### 6.5. Journaux d'exécution

```text
<OUTPUT_DIR>/logs/
├── submit.log
├── gsplat-<jobid>.out
└── gsplat-<jobid>.err
```

---

## ## 7. Exemple complet depuis un dossier d'images

Cet exemple montre comment :

1. préparer un dataset COLMAP à partir d'un dossier d'images ;
2. définir les variables du job dans un fichier `config.sh` ;
3. lancer le pipeline sur SLURM.

### 7.1. Préparer les images d'exemple

Exemple avec le dossier `poster/images` du dataset Nerfstudio :

```bash
mkdir -p /tmp/nerfstudio_poster
cd /tmp/nerfstudio_poster
```

Télécharger les images du dataset :

https://huggingface.co/datasets/nerfstudioteam/datasets/tree/main/poster/images

Une fois téléchargées localement, on suppose qu'elles se trouvent dans :

```text
/tmp/nerfstudio_poster/images
```

### 7.2. Générer le dataset COLMAP

Lancer `images_to_colmap.py` pour produire un dataset compatible avec `scripts/run.sh` :

```bash
python "$GIT_ROOT/python/images_to_colmap.py" \
  --image_dir /tmp/nerfstudio_poster/images \
  --output_dir /tmp/nerfstudio_poster/poster_colmap \
  --camera_model OPENCV \
  --matcher sequential \
  --verbose
```

Le dossier généré pourra être utilisé directement comme `--dataset-dir` :

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

### 7.3. Créer un fichier `config.sh`

Créer un fichier `config.sh` dans le dossier de travail, par exemple :

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

### 7.4. Lancer le job SLURM

Depuis le dossier contenant `config.sh` :

```bash
cd /tmp/nerfstudio_poster

CONFIG_SH=/tmp/nerfstudio_poster/config.sh \
GIT_ROOT=/path/to/repo \
OUTPUT_DIR=/tmp/nerfstudio_poster/output \
bash "$GIT_ROOT/environment/ign.slurm/launch.sh"
```

Le script :

1. charge `config.sh` ;
2. soumet `environment/ign.slurm/launch.slurm` avec `sbatch` ;
3. transmet les variables au job ;
4. suit les logs jusqu'à la fin de l'exécution.

### 7.5. Résultat attendu

Le pipeline exécuté sur SLURM va :

1. générer `/tmp/nerfstudio_poster/poster_colmap/transforms.json` ;
2. estimer `near` / `far` depuis le modèle COLMAP ;
3. entraîner Splatfacto ;
4. exporter le Gaussian Splat en `.ply` ;
5. nettoyer le `.ply` final.

Le livrable final sera écrit dans :

```text
/tmp/nerfstudio_poster/output/model3d/cleaned/poster.ply
```

Les logs SLURM seront écrits dans :

```text
/tmp/nerfstudio_poster/output/logs/
├── submit.log
├── gsplat-<jobid>.out
└── gsplat-<jobid>.err
```
```
