# Orientations IGNF `.CON` + LiDAR `.LAZ` → entrées COLMAP

[Retour à la documentation générale](../../doc/guide_utilisateur.md)

**Objectif.** Convertir des poses caméra IGNF (fichiers XML `.CON`) et un
nuage LiDAR coloré `.LAS`/`.LAZ` vers les deux entrées de la méthode
`COLMAP_METHOD=orientations` de [images → COLMAP](../images_to_colmap/README.md) :
`orientations.json` (poses caméra OpenCV monde → caméra et calibrations) et
`points.ply`. Aucune estimation photogrammétrique n'est effectuée.

```bash
python tools/con_laz_to_orientations/con_laz_to_orientations.py \
  --images /chemin/images --laz /chemin/lidar.laz --output-dir /chemin/ori \
  --stride 10 --crs EPSG:2154
```

La commande affiche les deux lignes à copier dans la configuration :

```bash
COLMAP_METHOD=orientations
SFM_IMAGES_DIR="/chemin/images"
ORIENTATIONS_JSON="/chemin/ori/orientations.json"
LIDAR_PLY="/chemin/ori/points.ply"
SFM_MASKS=off
```

Puis lancer `bash tools/images_to_colmap/run.sh CONFIG` pour le modèle COLMAP
seul, ou `bash script/images_to_3dgs.sh CONFIG` pour le pipeline complet.

## Entrées

- `--images` : dossier des images ; chaque image est associée au `.CON`
  (ou `.con`) de même nom de base, recherché récursivement. Les images sans
  `.CON` sont ignorées avec un avertissement ; un nom de base présent deux fois
  est refusé. Les noms relatifs au dossier sont conservés.
- `--laz` : nuage LAS/LAZ avec couleurs `red/green/blue` (`laspy` et `lazrs`).
- `--stride N` : garde un point sur N, après `--bbox` ; défaut `1`.
- `--bbox xmin,ymin,xmax,ymax` : emprise XY dans le CRS du LAZ.
- `--origin auto|none|X,Y,Z` : origine soustraite aux sommets et aux points.
  `auto` (défaut) prend la moyenne arrondie au mètre des sommets, pour éviter
  la perte de précision des coordonnées Lambert 93 en float32 à l'entraînement.
- `--crs` : CRS source, seulement consigné dans `reference`.
- `--max-systematism-px` : écart maximal toléré du systématisme cylindrique,
  défaut `0.5` px.

## Lecture du `.CON`

| Élément XML | Utilisation |
|---|---|
| `extrinseque/systeme/euclidien/{x,y}` + `sommet/{easting,northing}`, `sommet/altitude` | Centre optique |
| `rotation/mat3d/l1..l3/pt3d` | Rotation ; transposée si `Image2Ground=true` |
| `sensor/image_size/{width,height}` | Taille, comparée à l'image réelle |
| `sensor/ppa/{c,l,focale}` | Caméra `PINHOLE` `[focale, focale, c, l]` |

Les caméras de calibration identique sont regroupées. La pose écrite est
`tvec = -R (C - origin)`, avec la convention profondeur `z > 0` du script
`con_laz_to_colmap.py` du projet
[aerial_data_to_gaussian_splats](https://github.com/NicoIGN/aerial_data_to_gaussian_splats).

Refus explicites, car non représentables en `PINHOLE` :

- distorsion radiale `sensor/distortion` avec `r3`, `r5` ou `r7` non nul ;
- `transfo2d` d'un autre type que `systematismeCylindriqueTopAero` ;
- systématisme cylindrique dont l'écart dépasse `--max-systematism-px` ;
- taille d'image différente de `image_size` (pas de rééchantillonnage).

## Sorties

- `orientations.json` : format version 1 décrit dans
  [images → COLMAP](../images_to_colmap/README.md#orientations-et-lidar) ;
  `reference.origin` et `reference.crs` permettent de revenir aux coordonnées
  source (`source = local + origin`). Ce champ est recopié dans
  `sfm/sparse/0/orientation_reference.json`.
- `points.ply` : `x,y,z` locaux (double) et couleurs 8 bits.

Les sorties existantes ne sont pas écrasées.

## Tests

```bash
python -m unittest discover -s tools/con_laz_to_orientations -p 'test_*.py'
```
