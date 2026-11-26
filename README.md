# COLMAP to Gaussian Splats

Produire des scènes 3D Gaussian Splatting à partir d'images ou d'un modèle COLMAP,
puis les exporter, les traiter et les visualiser.

```text
Images → reconstruction des caméras et points 3D → entraînement → export → visualisation
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

## 2. Que fait ce projet ?

Ce projet fournit des outils pour préparer des images ou des données COLMAP,
entraîner des scènes 3DGS avec différents moteurs, puis exporter et traiter
les résultats. Le [guide utilisateur](doc/guide_utilisateur.md) aide à choisir
le parcours adapté.

Pour un modèle COLMAP, fournir le modèle et les images correspondantes, ou
produire les données COLMAP depuis des images avec les
[outil images vers COLMAP](tools/sfm/README.md). L'entraînement peut ensuite
se faire avec [Nerfstudio / Splatfacto](doc/nerfstudio.md) ou Spirula.

```text
Images → données COLMAP → entraînement → export et post-traitement
```

Les moteurs d'entraînement proposés sont :

- **[Nerfstudio](https://github.com/nerfstudio-project/nerfstudio)** : framework
  Python de reconstruction et de rendu 3D, dont **Splatfacto** est la méthode
  Gaussian Splatting utilisée ici. Développé et maintenu par l'équipe
  **Nerfstudio**, issue de **Berkeley / KAIR (BAIR)**, et les contributeurs
  de la communauté.
- **[Spirula Studio](https://github.com/harry7557558/spirula-studio)** : outil
  de reconstruction et d'entraînement Gaussian Splatting intégrant notamment
  le SfM et les masques, avec un backend GPU Vulkan. Projet maintenu par
  **harry7557558**, ingénieur indépendant qui le développe seul.

### Pipelines complets

Deux pipelines dans `scripts/` enchaînent les étapes depuis les images jusqu'à
l'export d'un splat. `TRAIN_BACKEND` choisit Spirula ou Nerfstudio/Splatfacto :

- **Bbox + flux Panoramax → splat** :
  [téléchargement et pipeline Panoramax](tools/panoramax/README.md).
- **Images locales → splat** : [pipeline images vers splat](tools/images_to_splat/README.md).

La préparation COLMAP possède son propre outil et sa configuration :
Spirula, COLMAP direct, `ns-process-data` ou conversion de poses caméra et LiDAR.
Nerfstudio exige PyTorch et une carte NVIDIA utilisable via CUDA ; le lancement
échoue explicitement s'ils sont absents.

### Démarrer

Les défauts sont chargés automatiquement depuis `config/defaults/*.default.sh`, puis
`tools/<pipeline>/config.default.sh`. Un fichier de configuration ne contient que les surcharges :

```bash
cat > /chemin/config_image.sh <<'EOF'
#!/usr/bin/env bash
SFM_IMAGES_DIR="/chemin/images"
OUTPUT_DIR="/chemin/sortie"
EOF
```

Slurm (*Simple Linux Utility for Resource Management*) est un gestionnaire
de ressources et de tâches de calcul utilisé sur les clusters.

**Exécution directe, sans soumission Slurm** — les calculs tournent sur la
machine courante et les blocs sont entraînés successivement. Ces commandes
ne réservent aucune ressource Slurm, même si la configuration contient des
paramètres `SLURM_*` :

```bash
# Images locales → 3DGS
bash scripts/images_to_3dgs.sh /chemin/config_image.sh

# Panoramax → 3DGS
bash scripts/panoramax_to_3dgs.sh /chemin/config_panoramax.sh
```

**Soumission à un cluster Slurm** — utiliser les lanceurs `launch_*` ci-dessous
depuis le nœud de connexion. Ils lisent les ressources `SLURM_*` de la
configuration et appellent eux-mêmes `sbatch` : les calculs s'exécutent sur les
nœuds alloués, avec un GPU par bloc actif et au plus
`NUM_GPUS` blocs simultanés. Les lanceurs se démarrent avec
`bash`, pas avec `sbatch` ; c'est le choix du script `launch_*` qui déclenche
la soumission Slurm.

```bash
# Images locales → 3DGS
bash scripts/launch_images_to_3dgs.sh /chemin/config_image.sh

# Panoramax → 3DGS
bash scripts/launch_panoramax_to_3dgs.sh /chemin/config_panoramax.sh
```

Voir le [guide de lancement Bash / Slurm](doc/lancement_bash_slurm.md) et les
[exemples de configuration complète](scripts/README.md) pour les paramètres
commentés de chaque pipeline.

Les installations, données attendues, paramètres et sorties sont décrits dans
le README du pipeline choisi.

### Convertir et visualiser un splat en 3D Tiles

Le fichier `.splat` géoréférencé (repère ENU) se convertit en tileset 3D Tiles,
puis s'ouvre dans le viewer HTML avec fond OpenStreetMap. Prérequis, une fois :

```bash
npm install --global 3dgs-ply-3dtiles-converter
```

```bash
# Conversion (--georef-summary est facultatif)
python3 tools/splat_to_3dtiles/convert.py \
  --input /chemin/vers/scene.splat \
  --output-dir /chemin/vers/scene_tiles \
  --no-open-inspector

# Visualisation
bash tools/viewer/3dtiles/view.sh /chemin/vers/scene_tiles/tileset.json
```

Le `--georef-summary` est facultatif si le résumé se trouve à côté du `.splat` ; les règles de recherche et les options sont dans la [documentation 3D Tiles](tools/splat_to_3dtiles/README.md). La visualisation est décrite dans la [documentation du viewer](tools/viewer/README.md).

### Documentation des outils

- [Exemples de commandes par usage](examples/README.md), dont la récupération d'un `.laz` IGN.
- [Préparation des images et modèles COLMAP](tools/preprocess/README.md).
- [Images, orientations et LiDAR vers COLMAP](tools/sfm/README.md).
- [Poses caméra IGNF `.CON` + LiDAR `.LAZ` vers orientations](tools/con_laz_to_orientations/README.md).
- [Installation de Spirula](tools/spirula/README.md).
- [Masques](tools/segmentation_masks/README.md).
- [Export, filtrage et fusion](tools/postprocess/README.md).
- [Recalage et nettoyage des gaussiennes avec un LiDAR LAS/LAZ](tools/lidar/README.md).
- [Rapports automatiques de durée des pipelines](tools/libs/python/PIPELINE_REPORT.md).
- [Nettoyage multivues du ciel](tools/clean_gs/README.md).
- [Visualisation](tools/viewer/README.md).
- [Conversion en 3D Tiles](tools/splat_to_3dtiles/README.md).
