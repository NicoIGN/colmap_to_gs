# Images / COLMAP → Gaussian Splats (`.ply`) avec Nerfstudio

Ce dépôt entraîne un modèle **Gaussian Splatting** avec **Nerfstudio / Splatfacto** à partir d'un modèle COLMAP, l'exporte en `.ply`, puis le nettoie.

Le pipeline se déroule en plusieurs étapes :

1. *(optionnel)* `python/images_to_colmap.py` : dossier d'images → dataset COLMAP prêt à l'emploi ;
2. `scripts/run.sh` :
   - génération de `transforms.json` depuis COLMAP ;
   - estimation de `near` / `far` ;
   - entraînement Splatfacto ;
   - export du `.ply` ;
   - nettoyage du `.ply` ;
3. *(optionnel)* `python/server.py` : serveur web local de visualisation du résultat.

## Organisation du dépôt

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
│   ├── estimate_planes.py         # estimation near / far
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

## Prérequis

- Un GPU NVIDIA : `nvidia-smi` doit répondre, sinon le pipeline s'arrête.
- Python 3.10 ou 3.11.
- Un environnement conda nommé gsplat, contenant Nerfstudio (ns-train et ns-export). Le dépôt fournit le fichier de définition environment/ign.slurm/conda_env.yml pour préparer cet environnement.
---

## 1. (Optionnel) Créer un dataset depuis un dossier d'images

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

### Sortie

```text
<output_dir>/                      # défaut : <image_dir>_colmap
├── images/                        # copie des images source
├── colmap/
│   ├── database.db
│   ├── sparse/0/{cameras,images,points3D}.bin
│   └── sparse_txt/0/{cameras,images,points3D}.txt
└── sparse_pc.ply                  # PLY ASCII xyz + rgb issu de points3D
```

### Étapes
1. Copie des images. Seul le premier niveau du dossier est lu. Extensions acceptées : jpg, jpeg, png, tif, tiff, webp. Il faut au moins 2 images.
2. Suppression des résultats d'un précédent lancement : `database.db`, `sparse/`, `sparse_txt/` et `sparse_pc.ply`.
3. Extraction des points caractéristiques SIFT.
4. Appariement des images
5. Reconstruction (`mapper`). Si COLMAP produit plusieurs modèles, celui qui contient le plus d'images est conservé dans `sparse/0`.
6. *(si `--bundle_adjustment`)* Bundle adjustment supplémentaire. Un échec à cette étape ne bloque pas la suite.
7. Conversion du modèle en texte, puis écriture de `sparse_pc.ply`.

### Options
| Option | Défaut | Description |
|---|---|---|
| `--image_dir` | *(requis)* | dossier des images |
| `--output_dir` | `<image_dir>_colmap` | dossier de sortie |
| `--colmap` | `colmap` | exécutable COLMAP |
| `--matcher` | `exhaustive` | `exhaustive`, `sequential`, `spatial`, `vocab_tree` |
| `--camera_model` | `SIMPLE_RADIAL` | modèle caméra COLMAP : SIMPLE_PINHOLE, PINHOLE, SIMPLE_RADIAL, RADIAL, OPENCV, FULL_OPENCV, SIMPLE_RADIAL_FISHEYE, RADIAL_FISHEYE, OPENCV_FISHEYE, THIN_PRISM_FISHEYE |
| `--separate_cameras` | désactivé | une caméra par image (sinon une seule caméra partagée) |
| `--max_image_size` | `3200` | taille maximale des images pour SIFT |
| `--no_gpu` | désactivé | force le mode CPU meme si un environnement GPU est détecté |
| `--bundle_adjustment` | désactivé | bundle adjustment supplémentaire |
| `--verbose` | désactivé | affiche la sortie de COLMAP |


> ⚠️ `FULL_OPENCV` et `THIN_PRISM_FISHEYE` sont acceptés ici, mais refusés par `colmap_to_transforms.py`. Avec ces modèles, `run.sh` échouera. Utilise `PINHOLE`, `SIMPLE_RADIAL`, `RADIAL`, `OPENCV` ou un modèle fisheye pris en charge.

> ℹ️ Seules les images que COLMAP a réussi à placer apparaissent dans le modèle, donc dans `transforms.json`.

---

## 2. Entrée attendue par `run.sh`

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

le fichier`transforms.json` requis apr NerfStudio est généré, et écrasé, dans `<dataset-dir>/transforms.json` à chaque lancement.

`sparse_pc.ply` est une entrée. Il peut provenir de COLMAP (par exemple via `images_to_colmap.py`) ou avoir été enrichi avec des données externes, comme un nuage LAZ. Il doit être exprimé **dans le repère  COLMAP**. Il sert à initialiser les Gaussians : sa densité, sa répartition, ses couleurs et sa cohérence avec les poses influencent fortement le résultat.

### Rôle des fichiers

| Fichier | Utilisé par |
|---|---|
| `colmap/sparse/0/cameras.bin`, `images.bin` | `colmap_to_transforms.py` |
| `colmap/sparse/0/` (modèle complet) | `estimate_planes.py` |
| `colmap/sparse/0/points3D.bin` | `clean_ply.py` |
| `images/` | Nerfstudio, via `transforms.json` |
| `sparse_pc.ply` | Nerfstudio, initialisation des Gaussians via `ply_file_path` |
| `transforms.json` *(généré)* | dataparser Nerfstudio |

---

## 3. Lancer le pipeline : `scripts/run.sh`

```bash
./scripts/run.sh \
  --dataset-dir /abs/path/to/dataset \
  --output-dir /abs/path/to/output \
  --gsplat-profile balanced
```

> ⚠️ Utilise des **chemins absolus**. `run.sh` se place dans `scripts/` avant de résoudre les chemins : un chemin relatif est donc interprété depuis `scripts/`. C'est aussi le cas de la valeur par défaut `runs/default`.

### Options

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

### Étapes exécutées

1. Vérification du dataset, du GPU et de la version de Python.
2. **Génération de `transforms.json`** (`colmap_to_transforms.py`).
3. Chargement de `config/config.sh`, puis du profil.
4. **Estimation de `near` / `far`** (`estimate_planes.py`). Cette étape est toujours exécutée et transmet `COLLIDER_NEAR`, `COLLIDER_FAR` et `ENABLE_COLLIDER=True` à l'entraînement.
5. **Entraînement Splatfacto** (`scripts/train.sh`).
6. **Export du `.ply`** (`scripts/export_splat_to_ply.sh`).
7. **Nettoyage** (`scripts/clean_ply.sh` → `python/clean_ply.py`).

---

## 4. Génération de `transforms.json`

Commande appelée par `run.sh` :

```bash
python3 python/colmap_to_transforms.py \
  --colmap-model <dataset-dir>/colmap/sparse/0 \
  --images-dir   <dataset-dir>/images \
  --output       <dataset-dir>/transforms.json \
  --image-prefix images \
  --ply-file-path sparse_pc.ply
```

Le script accepte aussi `--no-validate-images`, qui désactive la vérification de l'existence des images. `--ply-file-path ""` permet de ne pas écrire le champ `ply_file_path`.

### Conversion

- COLMAP stocke des poses world-to-camera (axes caméra : X droite, Y bas, Z avant).
- `transforms.json` contient des poses camera-to-world au format OpenGL/Nerfstudio (axes caméra : X droite, Y haut, Z arrière).
- **Le repère mondial COLMAP est conservé.** Seuls les axes Y et Z locaux de chaque caméra sont inversés. Aucun recentrage ni changement d'échelle n'est appliqué.

### Contenu

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

- **Intrinsèques** : si toutes les images partagent les mêmes paramètres caméra, ceux-ci sont écrits à la racine. Sinon, ils sont écrits dans chaque `frame`.
- **`camera_model`** : toujours `OPENCV` ou `OPENCV_FISHEYE`.

| Modèle COLMAP | `camera_model` |
|---|---|
| `SIMPLE_PINHOLE`, `PINHOLE`, `SIMPLE_RADIAL`, `RADIAL`, `OPENCV` | `OPENCV` |
| `OPENCV_FISHEYE`, `SIMPLE_RADIAL_FISHEYE`, `RADIAL_FISHEYE` | `OPENCV_FISHEYE` |
| `FULL_OPENCV`, `FOV`, `THIN_PRISM_FISHEYE` | **refusé** |

- **`applied_transform` / `applied_scale`** : toujours l'identité et `1.0`.
- **À ne pas confondre** avec `dataparser_transforms.json`, que Nerfstudio produit pendant l'entraînement.

---

## 5. Profils et configuration

### `config/config.sh`

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

Les paramètres de densification et de culling sont laissés vides par défaut. Ils sont définis par les profils : `DENSIFY_GRAD_THRESH`, `CULL_ALPHA_THRESH`, `CULL_SCREEN_SIZE`, `SPLIT_SCREEN_SIZE`, `MAX_GAUSS_RATIO`, `STOP_SPLIT_AT`, `RESET_ALPHA_EVERY`, `CULL_SCALE_THRESH`, `USE_SCALE_REGULARIZATION`, `USE_BILATERAL_GRID`, `SSIM_LAMBDA`, `MAX_RES`, etc.

### Profils

Profils fournis dans `config/profiles/` : `balanced`, `best`, `fast`, `quality`.

Pour utiliser des profils hors du dépôt :

```bash
export GSPLAT_PROFILE_PATH=/path/to/custom/profiles
```

Ordre de recherche (le premier fichier trouvé est utilisé) :

```text
1. $GSPLAT_PROFILE_PATH/<profile>
2. $GSPLAT_PROFILE_PATH/<profile>.sh
3. <repo>/config/profiles/<profile>
4. <repo>/config/profiles/<profile>.sh
```

### Mode deux passes (`--two-stages`)

1. **Passe 1** : `CAMERA_RES_SCALE_FACTOR=0.5`, `NUM_DOWNSCALES=2`, `MAX_RES=8192`, densification active.
2. **Passe 2** : reprise depuis le checkpoint, résolution du profil, `MAX_ITER + 1000` itérations, densification désactivée (`REFINE_EVERY=999999`, `STOP_SPLIT_AT=0`).

---

## 6. Export

`scripts/export_splat_to_ply.sh` :

- prend le run le plus récent dans `<output-dir>/model3d/splatfacto/` ;
- lance `ns-export gaussian-splat --load-config <run>/config.yml` ;
- écrit le `.ply` dans `<output-dir>/model3d/`.

Le script peut aussi archiver le run en ZIP (`ZIP_RUN=1`, valeur par défaut), mais `run.sh` désactive cette option (`ZIP_RUN=0`).

## 7. Nettoyage

`scripts/clean_ply.sh --dataset-dir <dataset-dir> --output-dir <output-dir>/model3d` appelle `python/clean_ply.py` avec les éléments suivants :

| Argument | Valeur |
|---|---|
| `--in-ply` | premier `.ply` trouvé dans `<out>/`, `<out>/model3d/` ou `<out>/exports/` |
| `--points` | `<dataset-dir>/colmap/sparse/0/points3D.bin` |
| `--transform` | `dataparser_transforms.json` le plus récent sous `<out>` |
| `--out-ply` | `<out>/cleaned/<nom>_cleaned.ply` |

`run.sh` renomme ensuite le résultat en **`<output-dir>/model3d/cleaned/<BASENAME>.ply`**. C'est le livrable final.

Le script utilise `dataparser_transforms.json` (la transformation globale appliquée par Nerfstudio pendant l'entraînement), et non `transforms.json`.

---

## 8. Sorties

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

---

## 9. Lancement SLURM

`environment/ign.slurm/launch.sh` soumet `launch.slurm` avec `sbatch`, affiche les logs en direct jusqu'à la fin du job, puis renvoie `0` si l'état final est `COMPLETED`, `1` sinon.

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

### Variables

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

Les variables requises peuvent être passées en environnement ou définies dans `CONFIG_SH`. Toutes les variables d'environnement sont transmises au job (`--export=ALL`).

### Ressources du job

Ressources définies dans `launch.slurm` : 1 nœud, 1 GPU, 8 CPU, 16 Go de mémoire, 48 h maximum.

Le job active l'environnement conda `gsplat`, puis lance `run.sh` avec `--skip-conda`.

### Logs

```text
<OUTPUT_DIR>/logs/
├── submit.log
├── gsplat-<jobid>.out
└── gsplat-<jobid>.err
```

---

## 10. Visualisation

```bash
python python/server.py
```

Ce script :

- sert le dossier `python/html/` sur **`http://127.0.0.1:8000/`** ;
- tente d'ouvrir le navigateur automatiquement ;
- ne prend aucun argument. Le port et l'hôte sont fixés dans le script, et le serveur n'écoute qu'en local.

Arrêt : `Ctrl+C`.

---

## Résumé du flux

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
