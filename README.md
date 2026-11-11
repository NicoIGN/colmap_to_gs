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
[outil images vers COLMAP](tools/images_to_colmap/README.md). L'entraînement peut ensuite
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

Deux pipelines dans `script/` enchaînent les étapes depuis les images jusqu'à
l'export d'un splat. `TRAIN_BACKEND` choisit Spirula ou Nerfstudio/Splatfacto :

- **Bbox + flux Panoramax → splat** :
  [téléchargement et pipeline Panoramax](tools/panoramax/README.md).
- **Images locales → splat** : [pipeline images vers splat](tools/images_to_splat/README.md).

La préparation COLMAP possède son propre outil et sa configuration :
Spirula, COLMAP direct, `ns-process-data` ou conversion de poses caméra et LiDAR.
Nerfstudio exige PyTorch et une carte NVIDIA utilisable via CUDA ; le lancement
échoue explicitement s'ils sont absents.

### Démarrer

Depuis la racine du dépôt, copier puis adapter les configurations :

```bash
cp tools/images_to_splat/config.sh.example /chemin/images.sh
cp tools/panoramax/config.sh.example /chemin/panoramax.sh
```

**En Bash** — exécution directe, avec les blocs entraînés successivement :

```bash
# Images locales → 3DGS
bash script/images_to_3dgs.sh /chemin/images.sh

# Panoramax → 3DGS
bash script/panoramax_to_3dgs.sh /chemin/panoramax.sh
```

**Sur Slurm** — depuis le nœud de connexion, les lanceurs appellent `sbatch`
avec les ressources `SLURM_*` de la configuration. Les blocs sont des tâches
GPU indépendantes ; `SLURM_BLOCK_PARALLELISM` fixe leur parallélisme maximal.

```bash
# Images locales → 3DGS
bash script/launch_images_to_3dgs.sh /chemin/images.sh

# Panoramax → 3DGS
bash script/launch_panoramax_to_3dgs.sh /chemin/panoramax.sh
```

Voir le [guide de lancement Bash / Slurm](doc/lancement_bash_slurm.md).

Les installations, données attendues, paramètres et sorties sont décrits dans
le README du pipeline choisi.

### Documentation des outils

- [Préparation des images et modèles COLMAP](tools/preprocess/README.md).
- [Images, orientations et LiDAR vers COLMAP](tools/images_to_colmap/README.md).
- [Poses caméra IGNF `.CON` + LiDAR `.LAZ` vers orientations](tools/con_laz_to_orientations/README.md).
- [Installation de Spirula](tools/spirula/README.md).
- [Masques](tools/segmentation_masks/README.md).
- [Export, filtrage et fusion](tools/postprocess/README.md).
- [Nettoyage multivues du ciel](tools/clean_gs/README.md).
- [Visualisation](tools/viewer/README.md).
- [Conversion en 3D Tiles](tools/splat_to_3dtiles/README.md).
