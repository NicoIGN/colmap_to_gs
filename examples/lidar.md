# LiDAR

[Retour aux exemples](README.md) · [Documentation LiDAR](../tools/lidar/README.md)

## Récupérer un `.laz` IGN

La commande extrait les points LiDAR HD IGN dans une emprise Lambert-93
(`EPSG:2154`). Remplacer les coordonnées dans l'ordre
`xmin,ymin,xmax,ymax` et le nom du fichier de sortie :

```bash
python "$GIT_ROOT/tools/lidar/fetch_ign_lidar.py" \
  --bbox 650000,6860000,650100,6860100 \
  --crs EPSG:2154 --output "$DATA_ROOT/zone.laz"
```

Elle produit `zone.laz` et son manifeste `zone.laz.json`, sans écraser une
sortie existante. Le Python utilisé doit disposer des dépendances de
[`requirements.txt`](../tools/lidar/requirements.txt). Il faut aussi un accès réseau à la Géoplateforme et à conda-forge, ainsi que Conda, Mamba ou
Micromamba dans le `PATH`. PDAL est lancé dans un environnement dédié ; voir
l'[installation de l'env `lidar-pdal`](../tools/lidar/README.md#installation-de-lenv-lidar-pdal)
et l'utilisation d'un PDAL déjà installé dans la [documentation LiDAR](../tools/lidar/README.md).

## Convertir un LAZ coloré en PLY

```bash
python "$GIT_ROOT/tools/images_to_colmap/las_to_ply.py" \
  --input "$DATA_ROOT/lidar-colore.laz" --output "$DATA_ROOT/points.ply" --stride 10
```

`--stride 10` conserve un point sur dix. Le LAZ doit contenir les couleurs
RGB, ce qui n'est pas garanti pour le LAZ IGN récupéré ci-dessus ; la conversion
ne change pas le repère. Pour générer également les poses
caméra depuis des fichiers `.CON`, voir
[la conversion CON/LAZ](../tools/con_laz_to_orientations/README.md).
