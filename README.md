# COLMAP → Gaussian Splats (`.ply`) avec Nerfstudio

Ce pipeline prend en entrée un dataset déjà préparé — caméras, poses, images et nuage de points — puis entraîne un modèle **Gaussian Splatting avec Nerfstudio/Splatfacto**. Après l'entraînement, le nuage de Gaussians est exporté au format `.ply`, puis nettoyé avec `clean_ply.py`.

## Entrée attendue

Le dossier passé à `--dataset-dir` doit contenir :

```text
<dataset-dir>/
├── colmap/
│   └── sparse/
│       └── 0/
│           ├── cameras.bin
│           ├── images.bin
│           └── points3D.bin
├── images/
│   └── *.jpg / *.jpeg / *.png
├── sparse_pc.ply
└── transforms.json
```

## Rôle des fichiers

Les fichiers COLMAP et les fichiers utilisés directement par Nerfstudio n'ont pas le même rôle.

### Fichiers utilisés directement par Nerfstudio

Pour l'entraînement Splatfacto, Nerfstudio utilise directement :

```text
transforms.json
images/
sparse_pc.ply
```

#### `transforms.json`

Ce fichier contient :

- les paramètres intrinsèques de la caméra ;
- les poses caméra image par image ;
- les chemins vers les images ;
- la référence vers le nuage initial via `ply_file_path`.

#### `images/`

Ce dossier contient les images référencées par les champs `file_path` de `transforms.json`.

#### `sparse_pc.ply`

Ce fichier contient le nuage de points utilisé pour l'initialisation des Gaussians lorsque le dataparser est configuré avec :

```text
load_3D_points=True
random_init=False
```

Dans ce pipeline, `sparse_pc.ply` est donc le nuage initial utilisé par Splatfacto.

### Fichiers COLMAP utilisés en amont ou pour le diagnostic

Les fichiers suivants sont produits par COLMAP :

```text
colmap/sparse/0/cameras.bin
colmap/sparse/0/images.bin
colmap/sparse/0/points3D.bin
```

Ils ne sont pas lus directement par Nerfstudio pendant l'entraînement Splatfacto.

Ils servent notamment :

- à générer les poses et les paramètres caméra du `transforms.json` ;
- à générer ou vérifier `sparse_pc.ply` ;
- à estimer les plans `near` et `far` avant l'entraînement ;
- à effectuer des diagnostics de cohérence ;
- à nettoyer le nuage de Gaussians après export avec `clean_ply.py`.

Les fichiers texte COLMAP suivants sont optionnels :

```text
cameras.txt
images.txt
points3D.txt
```

Ils sont uniquement utiles pour l'inspection et le diagnostic manuel. Ils ne sont pas nécessaires à l'entraînement si les fichiers binaires COLMAP et `transforms.json` sont déjà disponibles.

## `transforms.json`

`transforms.json` est le fichier principal lu par le dataparser Nerfstudio.

Exemple de structure :

```json
{
  "w": 7952,
  "h": 5304,
  "fl_x": 4737.208277698065,
  "fl_y": 4737.208277698065,
  "cx": 3956.924225984225,
  "cy": 2611.9564087456456,
  "camera_model": "PINHOLE",
  "ply_file_path": "sparse_pc.ply",
  "frames": []
}
```

### Champs principaux

- `w`, `h` : largeur et hauteur des images ;
- `fl_x`, `fl_y` : focales en pixels ;
- `cx`, `cy` : coordonnées du point principal ;
- `camera_model` : modèle caméra, par exemple `PINHOLE` ;
- `ply_file_path` : chemin vers le nuage initial ;
- `frames` : liste des poses et images.

Chaque élément de `frames` contient notamment :

```json
{
  "file_path": "./images/image.jpg",
  "transform_matrix": [
    [1.0, 0.0, 0.0, 0.0],
    [0.0, 1.0, 0.0, 0.0],
    [0.0, 0.0, 1.0, 0.0],
    [0.0, 0.0, 0.0, 1.0]
  ],
  "colmap_im_id": 1
}
```

- `file_path` : chemin relatif vers l'image ;
- `transform_matrix` : pose caméra `camera-to-world` au format attendu par Nerfstudio ;
- `colmap_im_id` : identifiant de l'image dans COLMAP.

Les matrices `transform_matrix` doivent utiliser la convention de repère attendue par Nerfstudio/OpenGL. La conversion COLMAP → Nerfstudio est effectuée lors de la génération du fichier.

### Champs de normalisation optionnels

Le fichier peut également contenir :

- `applied_transform` : transformation globale appliquée lors de la génération du dataset ;
- `applied_scale` : facteur d'échelle appliqué lors de la génération du dataset.

Ces champs décrivent la transformation appliquée au dataset avant l'entraînement.

Ils ne doivent pas être confondus avec le fichier `dataparser_transforms.json` généré par Nerfstudio pendant le training.

## `sparse_pc.ply`

`sparse_pc.ply` est le nuage de points initial référencé par :

```json
"ply_file_path": "sparse_pc.ply"
```

Il peut provenir directement de COLMAP ou avoir été enrichi avec des données externes, par exemple un nuage LAZ.

Il est utilisé par Splatfacto pour initialiser les Gaussians. Sa densité, sa distribution spatiale, ses couleurs et sa cohérence avec les poses caméra peuvent influencer fortement :

- le nombre initial de Gaussians ;
- les gradients ;
- les duplications ;
- les splits ;
- la qualité finale du modèle.

## Entraînement

Le script lance Nerfstudio/Splatfacto avec les paramètres définis dans le profil sélectionné :

```bash
./script/run.sh \
  --dataset-dir /path/to/dataset \
  --output-dir /path/to/output \
  --gsplat-profile balanced
```

Les profils peuvent contrôler notamment :

- le nombre maximal d'itérations ;
- la fréquence de densification ;
- le seuil de gradient pour les splits ;
- la taille minimale de split à l'écran ;
- le culling des Gaussians ;
- le nombre maximal de Gaussians ;
- la résolution d'entraînement.

Un profil custom hors du dépot GIT pppeut être utilisé en initialisant la variable d'environnement :

```bash
export GSPLAT_PROFILE_PATH=/path/to/custom/profiles
```

Le profil est recherché dans cet ordre :

```text
1. $GSPLAT_PROFILE_PATH/<profile>.sh
2. $GSPLAT_PROFILE_PATH/<profile>
3. <repo>/config/profiles/<profile>.sh
4. <repo>/config/profiles/<profile>
```

## Estimation de `near` et `far`

Avant l'entraînement, le pipeline peut analyser le modèle COLMAP :

```text
colmap/sparse/0/
├── cameras.bin
├── images.bin
└── points3D.bin
```

Le script `estimate_planes.py` estime les valeurs `near` et `far`.

Ces valeurs sont ensuite transmises au processus d'entraînement via :

```bash
COLLIDER_NEAR
COLLIDER_FAR
ENABLE_COLLIDER
```

Cette étape utilise les fichiers COLMAP, mais elle ne remplace pas le rôle de `transforms.json` et de `sparse_pc.ply` dans le dataparser Nerfstudio.

## Export du modèle Gaussian Splatting

Après l'entraînement, le modèle est exporté au format `.ply`.

Selon la configuration, le fichier brut est généralement produit sous :

```text
<output-dir>/model3d/<run>/splat.ply
```

Le script d'export est :

```text
export_splat_to_ply.sh
```

## Nettoyage avec `clean_ply.py`

Après l'export, le pipeline appelle automatiquement :

```text
python/clean_ply.py
```

Cette étape prend :
- le `.ply` brut produit par Nerfstudio ;
- le nuage COLMAP `points3D.bin` ;
- la transformation globale du dataparser Nerfstudio.

Le fichier final nettoyé est écrit sous :
```text
<output-dir>/cleaned/<BASENAME>.ply
```

Ce fichier constitue le livrable final du pipeline.

## Résumé du flux

```text
Dataset COLMAP préparé
        │
        ├── images/
        ├── transforms.json
        └── sparse_pc.ply
                │
                ▼
      Nerfstudio / Splatfacto
                │
                ▼
       Export Gaussian Splats
                │
                ▼
            splat.ply
                │
                ├── points3D.bin
                └── dataparser_transforms.json
                        │
                        ▼
              clean_ply.py
                        │
                        ▼
          <BASENAME>.ply final nettoyé
```

## Sorties principales

Après exécution, l'arborescence ressemble généralement à :

```text
<output-dir>/
├── model3d/
│   └── <run>/
│       ├── config.yml
│       ├── dataparser_transforms.json
│       └── splat.ply
├── cleaned/
│   └── <BASENAME>.ply
└── ...
```

## Exemple local

```bash
./script/run.sh \
  --dataset-dir /path/to/colmap/data \
  --output-dir /path/to/output/data \
  --gsplat-profile quality
```

Le résultat final sera disponible dans :

```text
/path/to/output/data/cleaned/<BASENAME>.ply
```

## Lancement depuis SLURM

Pour lancer le traitement sur l'environnement **SLURM**, définir `GIT_ROOT`, `OUTPUT_DIR` et `DATASET_DIR`, puis appeler le script `launch.sh`.

Depuis le dossier contenant les données :

```bash
cd /path/to/data

VERBOSE=1 \
GIT_ROOT=/path/to/repo \
OUTPUT_DIR=/path/to/output \
DATASET_DIR=/path/to/dataset \
bash "$GIT_ROOT/environment/ign.slurm/launch.sh"
```

Le script `launch.sh` soumet ensuite le traitement à SLURM avec la configuration définie dans l'environnement du projet.

## Exemple SLURM avec profil custom

```bash
cd /path/to/data

VERBOSE=1 \
GIT_ROOT=/path/to/repo \
OUTPUT_DIR=/path/to/output \
DATASET_DIR=/path/to/dataset \
GSPLAT_PROFILE_PATH=/path/to/custom/profiles \
GSPLAT_PROFILE=fast_compact \
bash "$GIT_ROOT/environment/ign.slurm/launch.sh"
```
