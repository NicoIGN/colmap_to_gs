# COLMAP to Gaussian Splats

Ce projet regroupe les outils nécessaires pour produire des scènes 3D en *Gaussian Splatting* (3DGS) à partir de vidéos, de photographies ou de flux d'images issus des services [Panoramax](https://panoramax.fr). Il couvre quatre grandes étapes :

- **La photogrammétrie (SfM)** : reconstruction des caméras et du nuage de points 3D avec HLOC, COLMAP ou Spirula.
- **L'entraînement 3DGS** : génération et optimisation des gaussiennes avec Nerfstudio (Splatfacto) ou Spirula.
- **L'export et le géoréférencement** : production de fichiers `.splat`, `.ply` ou 3D Tiles, avec possibilité de géoréférencer les scènes à partir des coordonnées GPS des images (EXIF), d'une trajectoire GPS au format GPX ou de fichiers externes de positions.
- **La visualisation 3D** : un viewer web affiche les splats alignés avec des données externes : nuages LiDAR, bâtiments CityJSON (roofer), couches GeoJSON ou GeoPackage, et données COLMAP.

```text
Images → reconstruction des caméras et points 3D → entraînement 3DGS → export → visualisation
```

## 1. Qu’est-ce que le 3DGS ?

### 1.1. Qu’est-ce qu’un Gaussian Splat ?

Un Gaussian Splat représente une scène 3D par un ensemble de gaussiennes :
des ellipsoïdes colorés dotés d’une position, d’une taille, d’une orientation
et d’une opacité. Depuis un point de vue, elles sont projetées sur l’image puis
compositées pour produire le rendu.

Contrairement à un maillage composé de surfaces, cette représentation décrit
directement l’apparence de la scène depuis plusieurs points de vue. Une fois
entraîné, le modèle peut être rendu et exploré en temps réel sur un matériel
adapté.

![Exemple de gaussienne](doc/technopitch/images/splat.png)

### Principe général

```text
Images + modèle COLMAP
        ↓
Initialisation des gaussiennes avec le nuage de points
        ↓
Entraînement GPU (Splatfacto ou Spirula)
        ↓
Export → post-traitement éventuel → visualisation
```

COLMAP met en correspondance les images et estime les paramètres des caméras
ainsi qu’un nuage de points 3D clairsemé. L’entraînement ajuste les gaussiennes
pour rapprocher les vues synthétiques des images d’entrée.

![Reconstruction SfM](doc/technopitch/images/sfm.jpg)

## 2. Installation

Trois étapes :

1. **Environnement Conda** : les librairies nécessaires aux pipelines sont contenues dans l'environnement Conda de votre plateforme, dans [`environment/`](environment/) (`macos`, `ign.slurm`, `google.colab`). Exemple : `conda env create --yes --file environment/macos/conda_env.yml`. Ensuite, activez-le avec `conda activate gsplat`.

2. **Spirula Studio** : compilez-le avec [`install/spirula/install_spirula.sh`](install/spirula/README.md), qui applique les bonnes options et télécharge le modèle SAM3 utilisé pour les masques.

3. **COLMAP et HLOC** : si le SfM utilise HLOC, installez-le avec `bash install/colmap/install-hloc.sh`. Voir le [README SfM](tools/sfm/README.md).

## 3. Pipelines principaux

Le projet propose 3 pipelines complets : à partir d'une vidéo, d'un dossier d'images ou d'un flux Panoramax, ils calculent un modèle 3D Gaussian Splatting. Des informations de positionnement des images permettent de le géoréférencer. Chaque pipeline existe en version exécutable sur la machine courante et en version lançable sur un serveur Slurm. Le résultat est un fichier `.splat` accompagné d'un fichier JSON de métadonnées.

> **Pour bien démarrer, consultez le [guide utilisateur](doc/guide_utilisateur.md)** : il aide à choisir le parcours, le moteur d'entraînement et le pipeline adaptés à votre point de départ.

Exemples de lancement :

```bash
bash scripts/images_to_3dgs.sh /chemin/config_image.sh
bash scripts/videos_to_3dgs.sh /chemin/config_video.sh
bash scripts/panoramax_to_3dgs.sh /chemin/config_panoramax.sh
```

Sur Slurm, remplacer par `scripts/launch_<pipeline>.sh`, lancé avec `bash` depuis le nœud de connexion. Voir le [guide Bash / Slurm](doc/lancement_bash_slurm.md) et [les exemples de configuration](scripts/README.md).

## 4. Viewer web

Le viewer web s'ouvre dans le navigateur en lançant la commande:

```bash
python3 tools/viewer/server.py
```

Depuis l'interface web, on importe une scène 3DGS, à laquelle on peut ajouter des données externes alignées sur le splat : LiDAR, CityJSON, GeoJSON, GeoPackage, modèle SfM COLMAP. Détails dans la [documentation du viewer](tools/viewer/README.md).


## 5. Liste des outils

### Acquisition et préparation

- [Préparation des images et modèles COLMAP](tools/prepare/README.md).
- [Poses caméra IGNF `.CON` + LiDAR `.LAZ` vers orientations](tools/con_laz_to_orientations/README.md).

### Reconstruction

- [Images, orientations et LiDAR vers COLMAP](tools/sfm/README.md).
- [Recalage et nettoyage des gaussiennes avec un LiDAR LAS/LAZ](tools/lidar/README.md).
- [Masques](tools/masks/README.md).

### Entraînement

- [Entraînement](tools/training/README.md).

### Export et visualisation

- [Export, filtrage et fusion](tools/export/README.md).
- [Nettoyage multivues du ciel](tools/export/clean_gs/README.md).
- [Conversion en 3D Tiles](tools/export/export_3dtiles.md).
- [Visualisation](tools/viewer/README.md).

### Utilitaires
- [Téléchargement Panoramax par bbox](tools/panoramax/README.md).
- [Données vectorielles (BD TOPO, OSM) en GeoPackage](tools/vector/README.md).
- [Rapports automatiques de durée des pipelines](tools/libs/python/PIPELINE_REPORT.md).


## 6. Exemples

- [Exemples de commandes par usage](examples/README.md)

## 7. Tests

Les tests Python sont dans `tests/`, en miroir de `tools/`. Depuis la racine du dépôt, venv activé :

```bash
PYTHON_BIN=python tests/run_all.sh --phase export
```

Détails dans le [README des tests](tests/README.md).
