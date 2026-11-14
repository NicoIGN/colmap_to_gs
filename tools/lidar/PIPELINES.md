# Options LiDAR des pipelines principaux

Les mêmes variables de configuration sont accessibles depuis **tous les scripts
de `scripts/`** : images, vidéos, Panoramax et leurs lanceurs Slurm.
Elles sont transmises aux jobs de préparation, aux blocs et à la fusion.
Le LiDAR est désactivé par défaut : le comportement existant reste inchangé.

```bash
# Après SfM/GPX et filtrage sparse, avant le découpage et l'entraînement :
LIDAR_COLMAP_ALIGN=true
LIDAR_COLMAP_INJECT=true

# Après fusion et filtres existants :
LIDAR_CLEAN=true
LIDAR_CLEAN_MODE=diagnostic   # apply pour remplacer le splat final avec sauvegarde

LIDAR_MARGIN=20
LIDAR_PDAL=pdal
LIDAR_CLEAN_DISTANCE=0.5
LIDAR_CLEAN_MIN_OPACITY=0.05
```

Le modèle recalé/enrichi est `OUTPUT_DIR/colmap_lidar/` et devient la source
des blocs. Le SfM brut reste intact. Le LAZ partagé reste dans
`OUTPUT_DIR/lidar/lidar.laz` avec manifeste d'emprise/CRS et taille/SHA256.
**Quand COLMAP a été recalé, le splat final n'est pas recalé une seconde fois** :
seul le nettoyage géométrique/opaque est lancé dans le repère approprié.
Les points synthétiques et leurs limites sont décrits dans [COLMAP.md](COLMAP.md).

Sans recalage COLMAP, `LIDAR_CLEAN=true` utilise le maître splat avec recalage
avant nettoyage. Il faut alors une référence ENU avec `LIDAR_CLEAN_GEOREF`
(automatique pour le GPX du pipeline images), ou une origine projetée connue
avec `LIDAR_CLEAN_ORIGIN`. Pour Panoramax sans rapport ENU, activer d'abord
`LIDAR_COLMAP_ALIGN=true` afin de retrouver l'ancrage EXIF ; ne pas supposer que
les coordonnées ENU brutes sont des coordonnées Lambert-93.

## Paramètres communs

| Variable | Défaut | Usage |
|---|---|---|
| `LIDAR_PDAL` | `pdal` | Exécutable PDAL pour extraction COPC |
| `LIDAR_MARGIN` | `20` | Marge de récupération en mètres |
| `LIDAR_MAX_TILES` | `100` | Limite de tuiles |
| `LIDAR_MAX_AREA_KM2` | `25` | Surface maximale extraite |

## Recalage COLMAP et injection

| Variable | Défaut | Usage |
|---|---|---|
| `LIDAR_COLMAP_ALIGN` | `false` | Activer avant découpage |
| `LIDAR_COLMAP_INJECT` | `false` | Ajouter points avec tracks ; exige ALIGN |
| `LIDAR_COLMAP_GEOREF` | vide | Rapport ENU explicite ; rapport GPX du modèle détecté sinon |
| `LIDAR_COLMAP_TERRAIN_CENTER` | vide | XYZ terrain du centre sparse non géoréférencé |
| `LIDAR_COLMAP_ORIGIN` | `0,0,0` | Origine projetée pour initialisation manuelle |
| `LIDAR_COLMAP_ALREADY_PROJECTED` | `false` | Modèle connu dans le CRS projeté LiDAR |
| `LIDAR_COLMAP_DISTANCE` | `2` | Seuil initial ICP, mètres |
| `LIDAR_COLMAP_VOXEL` | `0.2` | Voxel de recalage, mètres |
| `LIDAR_COLMAP_MIN_OVERLAP` | `0.3` | Recouvrement minimal |
| `LIDAR_COLMAP_CLASSES` | `2,3,4,5,6,67` | Sol, végétation et bâti incertain compris |
| `LIDAR_COLMAP_SCALE_MIN/MAX` | `0.1` / `10` | Bornes d'échelle pour modèle non géoréférencé |
| `LIDAR_COLMAP_SCALE_STEPS` | `9` | Échelles initiales |
| `LIDAR_COLMAP_YAW_STEPS` | `36` | Caps initiaux |
| `LIDAR_COLMAP_SEARCH_RADIUS` | `5` | Incertitude XYZ autour du centre terrain |
| `LIDAR_COLMAP_TRANSLATION_STEP` | `5` | Pas initial de translation |
| `LIDAR_COLMAP_MAX_TILT` | `10` | Inclinaison maximale, degrés |
| `LIDAR_COLMAP_ACCEPT_AMBIGUOUS` | `false` | Accepter explicitement un résultat ambigu |
| `LIDAR_COLMAP_INJECT_VOXEL` | `0.2` | Sous-échantillonnage des points à injecter |
| `LIDAR_COLMAP_INJECT_MAX_POINTS` | `50000` | Budget de candidats |
| `LIDAR_COLMAP_INJECT_MIN_SEPARATION` | `0.1` | Distance aux points sparse existants |
| `LIDAR_COLMAP_MIN_VIEWS` | `2` | Nombre minimal de vues |
| `LIDAR_COLMAP_MIN_VIEW_ANGLE` | `2` | Angle minimal entre vues, degrés |
| `LIDAR_COLMAP_DEPTH_TOLERANCE` | `0.1` | Tolérance z-buffer, mètres |
| `LIDAR_COLMAP_VISIBILITY_CELL` | `4` | Taille de cellule, pixels |
| `LIDAR_COLMAP_KEYPOINT_SEPARATION` | `2` | Distance aux observations existantes, pixels |

Pour GPX/EXIF, ne pas renseigner de centre terrain. L'ancrage vient du rapport
ENU ou des GPS EXIF des images. Pour un modèle non géoréférencé, fournir
`LIDAR_COLMAP_TERRAIN_CENTER`, des bornes d'échelle réalistes et éventuellement
une origine locale ; Z du modèle est supposé approximativement vertical.

## Nettoyage final

| Variable | Défaut | Usage |
|---|---|---|
| `LIDAR_CLEAN` | `false` | Activer |
| `LIDAR_CLEAN_MODE` | `diagnostic` | Rapport seul ou `apply` |
| `LIDAR_CLEAN_DISTANCE` | `0.5` | Distance au support ellipsoïdal, mètres |
| `LIDAR_CLEAN_SUPPORT_SIGMA` | `3` | Étendue maximale en écarts-types |
| `LIDAR_CLEAN_SPACING_FACTOR` | `1` | Tolérance selon la densité LiDAR |
| `LIDAR_CLEAN_MIN_OPACITY` | `0.05` | Opacité locale minimale du support |
| `LIDAR_CLEAN_CLASSES` | vide | Toutes les classes ; restreindre avec prudence |
| `LIDAR_CLEAN_GEOREF` | vide | Rapport ENU ; référence du COLMAP recalé détectée |
| `LIDAR_CLEAN_ORIGIN` | `0,0,0` | Origine projetée si pas de référence ENU/recalage COLMAP |

Les artefacts de nettoyage sont dans `OUTPUT_DIR/lidar_splat/`. En mode
`apply`, le splat avant LiDAR est sauvegardé sous `*.before_lidar.splat`.
Un résultat vide est refusé, sans remplacement du final.

## Ressources et reprise

Installer [les dépendances LiDAR](requirements.txt), et
[pycolmap/Pillow](requirements-colmap.txt) pour le recalage/injection COLMAP.
PDAL COPC et l'accès réseau IGN sont requis pour une première extraction,
pas pour un LAZ partagé déjà disponible. Les vidéos vérifient ces ressources
avant extraction via leur prévalidation commune.

**Activer le recalage sur des blocs déjà produits est refusé** : choisir un
nouveau workspace. Une modification des entrées ou paramètres COLMAP LiDAR
nécessite également un nouveau workspace ; signatures du modèle, des images
et des références sont comparées avant réutilisation et entraînement.
Le nettoyage dispose d'une signature propre et ne réutilise pas silencieusement
un ancien résultat avec de nouveaux paramètres. Un LAZ trop petit ou altéré
provoque une erreur explicite.

Commencer avec `LIDAR_CLEAN_MODE=diagnostic` : un objet absent du LiDAR n'est pas
forcément un artefact, et l'opacité locale d'une gaussienne n'est pas l'opacité
finale composée du rendu.
