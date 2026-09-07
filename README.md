# COLMAP → Gaussian Splats (.ply) avec Nerfstudio

Ce script prend en entrée un dataset **déjà préparé** (caméras + poses + images) et produit un **Gaussian Splat au format `.ply`** via **Nerfstudio**.

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

## `transforms.json` : contenu attendu

`transforms.json` décrit la calibration caméra + les poses image par image, au format consommé par Nerfstudio.

Champs principaux :

- `w`, `h` : largeur/hauteur des images
- `fl_x`, `fl_y` : focales en pixels
- `cx`, `cy` : point principal
- `camera_model` : modèle caméra (ex: `PINHOLE`)
- `ply_file_path` : chemin du nuage sparse (ici `sparse_pc.ply`)
- `frames` : liste des vues

Chaque entrée de `frames` contient :

- `file_path` : chemin de l’image (ex: `./images/xxx.jpg`)
- `transform_matrix` : matrice 4x4 pose caméra
- `colmap_im_id` : identifiant image COLMAP

Optionnellement, on peut aussi trouver :

- `applied_transform` : changement de repère global appliqué
- `applied_scale` : facteur d’échelle global

## À propos de `sparse_pc.ply`

`sparse_pc.ply` est le nuage de points sparse (généralement exporté depuis COLMAP) référencé par `transforms.json` via `ply_file_path`.

Il sert de géométrie sparse de référence (visualisation / cohérence de repère selon pipeline).

## Sortie

Le script génère un `.ply` final de Gaussian Splats, typiquement dans :

```text
<root>/exports/<name>.ply
```

## Exemple

```bash
./run.sh \
  --dataset-dir /path/to/colmap/data \
  --output_dir /path/to/output/data \
  --gsplat-profile quality
```
