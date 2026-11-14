# Recaler et enrichir un modèle COLMAP avec du LiDAR

```bash
python -m pip install -r tools/lidar/requirements-colmap.txt
```

L'outil lit un modèle sparse COLMAP (dossier contenant cameras/images/points3D,
texte ou binaire), recale points et poses caméra par une similitude et exporte
un **nouveau modèle binaire**. Les intrinsics et tracks existants sont conservés.
Les modèles avec rigs/frames sont transformés par l'API pycolmap.
Le dossier de sortie doit être absent ; les entrées ne sont pas modifiées.

## Modèle déjà géoréférencé (GPX ou EXIF)

```bash
python tools/lidar/align_colmap_to_lidar.py \
  --model reconstruction/georef/0 --lidar zone.laz --output colmap_lidar
```

**Aucune coordonnée d'initialisation n'est requise** quand le modèle possède
`georef_stats.json` (GPX) ou `orientation_reference.json` avec une référence
`local_ENU`. Un rapport ailleurs peut être donné avec `--georef rapport.json`.
Le LiDAR projeté est converti dans le repère ENU du modèle via pyproj, et non
simplement translaté. `--lidar-crs EPSG:2154` est le CRS source par défaut.
Le modèle résultant reste dans ce repère ENU ; sa référence est exportée.

Si l'ancrage EXIF n'a pas de rapport ENU, fournir `--images dossier_images` :
l'outil retrouve une similitude entre centres des caméras et GPS EXIF.
Au moins trois caméras GPS avec altitude et une géométrie non colinéaire sont
nécessaires ; des résidus >20 m sont refusés. **GPS présent n'est pas une garantie
de précision**. Si les EXIF sont incomplets, utiliser le rapport de référence
original plutôt que deviner l'origine choisie par le SfM.

Vérifier les altitudes : les hauteurs GPX/EXIF brutes ne sont pas automatiquement
converties du géoïde vers l'ellipsoïde. Une erreur verticale peut empêcher l'ICP.
Les poses déjà géoréférencées servent de départ, puis un ICP Sim(3) affine
rotation, translation et échelle.

Pour un modèle explicitement dans les mêmes coordonnées projetées que le LAZ,
sans rapport ENU, utiliser `--already-projected`. `--origin X,Y,Z` soustrait
cette origine aux coordonnées globales des deux entrées ; la sortie est locale.
Ne pas l'utiliser pour un modèle déjà exprimé en ENU.

## Modèle non géoréférencé

```bash
python tools/lidar/align_colmap_to_lidar.py \
  --model sparse/0 --lidar zone.laz --output colmap_lidar \
  --terrain-center 650050,6860050,120 --origin 650000,6860000,0 \
  --scale-min 0.5 --scale-max 2 \
  --search-radius 5 --translation-step 5 --max-tilt 10
```

`--terrain-center` désigne la coordonnée terrain XYZ approximative du **centre
arithmétique du nuage sparse**, dans le repère projeté du LiDAR. Z du modèle
est supposé approximativement orienté vers le haut.

La recherche teste les caps (36 par défaut), échelles (9 valeurs logarithmiques
entre 0.1 et 10 par défaut) et translations XYZ autour du centre terrain.
Les meilleurs candidats sont affinés par ICP Sim(3), avec une inclinaison
finale limitée à `--max-tilt` (10° par défaut). L'inclinaison est corrigée par
l'ICP, pas explorée exhaustivement au départ. Une échelle inconnue hors des
bornes ou une trop forte inclinaison nécessite de modifier ces paramètres.
La zone LAZ doit couvrir la scène à l'échelle attendue, avec une marge.

`--max-correspondence-distance` vaut 2 m, `--voxel-size` 0.2 m,
`--min-overlap` 0.3. `--max-candidates` limite le coût, `--search-points` limite
le sparse utilisé pour la recherche et `--refine-candidates` vaut 8.
Il s'agit d'une recherche bornée et approximative, pas d'une localisation globale
garantie. Un nuage répétitif ou principalement plan peut donner un faux optimum.
Des solutions distinctes de scores proches sont refusées, sauf
`--accept-ambiguous` après contrôle ; l'absence d'ambiguïté détectée ne prouve pas
l'unicité réelle. Inspecter `lidar_alignment.json` et superposer les nuages.

Les références de recalage utilisent les classes 2,3,4,5,6,67 par défaut,
incluant végétation et bâti incertain (`--lidar-classes` pour modifier).

## Injection optionnelle avec observations et tracks

Ajouter :

```bash
--inject --images dossier_images \
--inject-voxel 0.2 --inject-max-points 50000 \
--min-views 2 --min-view-angle 2 --depth-tolerance 0.1
```

Après recalage, le LiDAR est sous-échantillonné et les points proches du sparse
existant sont exclus (`--inject-min-separation`, 0.1 m par défaut).
Les candidats sont projetés avec les modèles de caméra pycolmap (distorsion comprise).
Un z-buffer constitué de **tout le LiDAR et des points sparse existants**, par
cellules de 4 pixels et voisinage 3×3, élimine les points derrière une surface
plus proche. Les points derrière les caméras ou hors image sont exclus.
La tolérance est exprimée en profondeur caméra, en mètres.

Au moins deux vues et une séparation angulaire de 2° sont exigées. Au plus
une observation synthétique par cellule et image ; les candidats trop proches
des points2D existants sont exclus (`--keypoint-separation`, 2 pixels).
Les nouveaux points2D sont ajoutés en fin de liste sans modifier les indices
existants, avec des tracks réciproques. Leur couleur est la médiane des pixels
observés. Les images doivent avoir les dimensions de la calibration.

**Ce sont des observations synthétiques par projection, pas des correspondances
de texture mesurées.** Leur erreur de reprojection nulle est construite, pas une
preuve de précision ; `database.db`, features et matches ne sont pas modifiés.
Ne pas lancer un bundle adjustment en supposant que ces tracks apportent des
mesures indépendantes. Pour un BA contrôlé, des correspondances image mesurées
ou de vraies contraintes LiDAR seraient nécessaires.

Les trous de couverture LiDAR ne prouvent pas l'absence d'occlusion. Le z-buffer
est conservateur mais approximatif, et n'est pas un test sur maillage.
Les points injectés peuvent servir à l'initialisation 3DGS après vérification.
L'injection échoue explicitement si aucun point ne satisfait les conditions.
Le modèle exporté est relu et sa cohérence vérifiée.

## Tests

```bash
cd tools/lidar
python -m unittest test_colmap_lidar
```
