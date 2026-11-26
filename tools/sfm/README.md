# SfM : images et orientations vers COLMAP

[Retour à la documentation générale](../../doc/guide_utilisateur.md)

**Objectif.** Préparer un modèle COLMAP indépendamment du moteur 3DGS.
La configuration est un fichier Bash de confiance :

```bash
bash tools/sfm/run_standard_images.sh /chemin/config.sh
```

**Organisation.** Tout le code SfM est regroupé ici, avec une interface commune
(`run_standard_images.sh` puis `sfm_run.sh`, qui appelle `backends/$COLMAP_METHOD.sh`) :

| Emplacement | Contenu |
|---|---|
| `run_standard_images.sh`, `sfm_run.sh`, `param.default.sh` | Entrée commune, cœur SfM (reprise, split, vérification), défauts |
| `backends/` | Un script par méthode : `spirula.sh`, `colmap.sh`, `nerfstudio.sh`, `hloc.sh`, `orientations.sh`. Étapes via `SFM_BACKEND_STEP` : `recover`, `reconstruct`, `refine` (les méthodes sans objet sortent à `recover`/`refine`) |
| `prepare_panoramax.py`, `sparse_density.sh`, `filter_sparse_density.py`, `sfm_geometry_policy.py` | Étapes partagées (préparation, filtre de densité, politique géométrique) |
| `colmap_split_blocks.py`, `diagnose_colmap_models.py` | Découpage en blocs, diagnostic |
| `spirula/` | Scripts qui appellent Spirula (`sfm_spirula.sh`, `camera_split.py`, `spirula_progress.py`) |
| `colmap/` | Outils COLMAP seul, sans Spirula (`reconstruct.py`, `hloc_reconstruct.py`, `mvs_densify.py`, conversions d'orientations et LAS) |

Les scripts d'appel direct à Spirula ne sont appelés que depuis `spirula/` ;
les outils n'utilisant que COLMAP (dont HLOC) sont dans `colmap/`.
Le découpage équirectangulaire des images 360 (utilisé par HLOC) reste dans
[`tools/prepare`](../prepare/README.md). Le recalage GPX et GPS est dans
[`tools/georef`](../georef/).

**Méthodes.** `COLMAP_METHOD` choisit la production des caméras et points :

- `spirula` : SfM Spirula existant, masques et géoréférencement EXIF automatiques.
- `colmap` : COLMAP direct avec SIFT, masques par image, matching exhaustif ou
  séquentiel ; `SFM_COLMAP_USE_GPU=false` permet le CPU.
- `nerfstudio` : `ns-process-data images` avec COLMAP/SIFT, sans copie ni renommage
  des images. PyTorch et une carte NVIDIA utilisable via CUDA sont obligatoires.
  Cette interface ne transmet pas les masques SfM : `SFM_MASKS=off` et
  `SFM_CAMERA_MODE=single` sont requis. Le regroupement des caméras et les seuils
  SfM sont ceux de Nerfstudio, pas les réglages Spirula.
- `orientations` : conversion de poses caméra calibrées (position, rotation,
  intrinsèques) et d'un PLY coloré, sans nouvelle estimation photogrammétrique.
  `SFM_MASKS=off` est requis.
- `hloc` : SfM complet via [Hierarchical-Localization](https://github.com/cvg/Hierarchical-Localization)
  (`hloc_reconstruct.py`, extraction, appariement, triangulation pycolmap). Réglages
  `HLOC_*` : `HLOC_FEATURES`, `HLOC_MATCHER`, `HLOC_PAIRS` (retrieval ou exhaustive),
  `HLOC_CAMERA_MODEL`. `HLOC_WORK_DIR` fixe le workspace (cache features) ; par défaut `SFM/hloc`. Si `SFM_MASKS=on`, les points tombant sur un masque SAM
  (0 = masqué) sont retirés avant l'appariement. Le modèle `equirectangular` n'est pas
  supporté. `SFM_CAMERA_SPLIT=false` est requis. HLOC est installé par `conda_env.yml`.

**Réglages.** Copier [`param.default.sh`](param.default.sh). Les masques,
la reprise et le recalage GPX reprennent les réglages du
[pipeline images](../libs/README.md). Les options `SFM_SP_FEATURES`,
`SFM_SP_MATCHER`, `SFM_SP_PAIRS` et de bundle adjustment concernent Spirula.
COLMAP direct applique les seuils d'erreur, d'inliers et d'angle aux étapes
correspondantes, mais ne transpose pas la politique de perte robuste Spirula.
`SFM_COLMAP_*` règle COLMAP direct ; `SFM_NS_PROCESS_DATA_BIN` choisit
`ns-process-data`. `SFM_COLMAP_MATCHING` est pris en charge par COLMAP direct
et transmis aussi à `ns-process-data`. Les variables `TRAIN_SP_*` / `TRAIN_NS_*`
ne concernent pas cette préparation autonome : aucun entraînement 3DGS n'est lancé.

**Sorties.** `colmap_dataset.json` indique les chemins relatifs `images` et
`sparse_model`. Le modèle brut est `sfm/sparse/0`, remplacé pour les étapes
suivantes par `georef/0` si GPX, puis `sparse_clean` si filtrage activé.
Les coordonnées EXIF ne sont utilisées automatiquement que par Spirula ;
un GPX configuré est toujours prioritaire. Les autres méthodes ne prétendent
pas fournir un résultat géoréférencé à partir des EXIF.

**Réutilisation.** `inputs.json` protège les images, la méthode et les données
d'orientation/LiDAR. Une méthode différente exige un nouveau workspace.
Le pipeline `scripts/images_to_3dgs.sh` appelle ce même outil avant de découper
et entraîner ; il réutilise donc une préparation compatible déjà effectuée.
Une sortie COLMAP direct ou Nerfstudio partielle doit être sauvegardée/déplacée ;
elle n'est pas reprise implicitement.

**Pseudo-trajectoire GPX.** `colmap/colmap_pseudo_gpx.py --model DOSSIER` lit les
centres caméra d'un modèle COLMAP et son `DOSSIER/gauge.txt` (ENU centré sur
`origin_lat/origin_lon/origin_alt`). Il ordonne les
sommets par plus court chemin ouvert (plus proche voisin depuis plusieurs extrémités,
puis 2-opt) et écrit `DOSSIER/pseudo_trajectory.gpx`. `--order frame` suit l'indice
`_fXXXXX` des noms d'images à la place. L'ordre géométrique ne traite pas les passages
multiples au même endroit : il suppose une trajectoire simple.
`--tolerance X` (mètres) régularise la courbe : Douglas-Peucker ne garde que les sommets
nécessaires pour rester à moins de X m de la polyligne reliant tous les sommets.

## Orientations et LiDAR

**Convention.** `ORIENTATIONS_JSON` décrit des poses caméra (extrinsèques) OpenCV monde → caméra :
`X_camera = R(qvec) × X_world + tvec`, quaternion `[w,x,y,z]`.
Les pixels ont leur origine en haut à gauche. Les images conservent leurs noms
relatifs et leurs dimensions brutes. PINHOLE et OPENCV sont supportés.

```json
{
  "version": 1,
  "pose_convention": "opencv_world_to_camera",
  "reference": {"description": "coordonnees locales metriques communes"},
  "cameras": [
    {"id": 1, "model": "PINHOLE", "width": 4000, "height": 3000,
     "params": [3000, 3000, 2000, 1500]}
  ],
  "images": [
    {"name": "photo1.jpg", "camera_id": 1,
     "qvec": [1, 0, 0, 0], "tvec": [0, 0, 0]},
    {"name": "photo2.jpg", "camera_id": 1,
     "qvec": [1, 0, 0, 0], "tvec": [-1, 0, 0]}
  ]
}
```

**Calibration.** PINHOLE : `[fx,fy,cx,cy]` ; OPENCV :
`[fx,fy,cx,cy,k1,k2,p1,p2]`. `LIDAR_PLY` contient `x,y,z,red,green,blue` ;
les couleurs sont sur 8 bits. Les points et les centres caméra doivent déjà être
dans les mêmes coordonnées et unités. Aucune conversion de CRS ou de verticale
implicite n'est effectuée. Préférer des coordonnées locales métriques pour
éviter la perte de précision pendant l'entraînement.

**Observations.** Le convertisseur projette les points dans les images, garde
le point le plus proche par pixel et conserve ceux observés dans au moins deux
vues. Il construit les observations et tracks réciproques nécessaires au
découpage existant. Ce ne sont pas des correspondances vérifiées par SfM :
occlusions résiduelles et erreurs de calibration restent possibles.
Valider visuellement les projections ; réduire les gros nuages avant conversion.

**Conversions.** Les scripts suivants produisent `ORIENTATIONS_JSON` et
`LIDAR_PLY` :

| Données source | Outil |
|---|---|
| Poses caméra IGNF `.CON` + LiDAR `.LAZ` | [`tools/con_laz_to_orientations`](../con_laz_to_orientations/README.md) |
| `transforms.json` Nerfstudio | `colmap/transforms_to_orientations.py` (ci-dessous) |
| LAS/LAZ coloré seul | `las_to_ply.py` (ci-dessous) |

```bash
# .CON + .LAZ : poses caméra et nuage, avec origine locale commune.
python tools/con_laz_to_orientations/con_laz_to_orientations.py \
  --images /chemin/images --laz /chemin/lidar.laz --output-dir /chemin/ori --stride 10
```

Les deux scripts suivants ne modifient pas le repère :

```bash
# transforms.json : matrices OpenGL camera-to-world explicites, pas un checkpoint.
python tools/sfm/colmap/transforms_to_orientations.py \
  --input /chemin/transforms.json --output /chemin/orientations.json

# LAS/LAZ colore : laspy et lazrs necessaires ; stride=10 garde un point sur dix.
python tools/sfm/colmap/las_to_ply.py \
  --input /chemin/points.laz --output /chemin/points.ply --stride 10
```

`las_to_ply.py` accepte aussi `--origin X,Y,Z` (soustrait) et
`--bbox xmin,ymin,xmax,ymax` (appliquée avant `--stride`). Le champ `reference` du JSON est conservé dans
`sfm/sparse/0/orientation_reference.json`.
