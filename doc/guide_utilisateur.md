# Guide utilisateur — produire et visualiser un Gaussian Splat

[Accueil du projet](../README.md)

Ce guide aide à choisir comment produire les données COLMAP et entraîner une
scène 3D. Le [README général](../README.md#1-quest-ce-que-le-3dgs)
présente
les Gaussian Splats et le principe général. Les commandes complètes, options,
paramètres et détails de chaque outil sont documentés dans les README
spécialisés liés ci-dessous.

## 2. Choisir un parcours

### 2.1. Produire les données COLMAP

La première étape consiste à reconstruire les caméras et les points 3D depuis
les images, ou à réutiliser un modèle COLMAP existant avec ses images.

- Images : l'[outil images vers COLMAP](../tools/images_to_colmap/README.md)
  propose `COLMAP_METHOD=spirula|colmap|nerfstudio|orientations`, avec une
  configuration commune. Les [outils de préparation](../tools/preprocess/README.md)
  permettent aussi d'extraire des images d'une vidéo.
- Orientations et LiDAR : convertir les poses vers le JSON explicite documenté
  et le nuage coloré vers PLY, puis utiliser `COLMAP_METHOD=orientations`.
- Modèle existant : conserver les images associées, les paramètres des caméras,
  leurs poses et les points 3D.

### 2.2. Choisir l'entraînement

Une fois les données COLMAP disponibles, choisir le moteur d'entraînement :

- **[Nerfstudio](https://github.com/nerfstudio-project/nerfstudio)** :
  framework Python de reconstruction et de rendu 3D ; **Splatfacto** est sa
  méthode Gaussian Splatting utilisée ici. Le projet est développé et maintenu
  par l'équipe **Nerfstudio**, issue de **Berkeley / KAIR (BAIR)**, et les
  contributeurs de la communauté.
- **[Spirula Studio](https://github.com/harry7557558/spirula-studio)** :
  outil de reconstruction et d'entraînement Gaussian Splatting intégrant le
  SfM et les masques, avec un backend GPU Vulkan ; maintenu par
  **harry7557558**, ingénieur indépendant qui le développe seul.

Dans ce dépôt, les points d'entrée correspondants sont :

- **Pipeline commun** : `TRAIN_BACKEND=spirula|nerfstudio` dans la configuration
  de `script/images_to_3dgs.sh` ou `script/panoramax_to_3dgs.sh`.
  Le modèle COLMAP et le découpage restent communs ; chaque moteur dispose
  de ses sorties d'entraînement. Nerfstudio exige PyTorch et NVIDIA/CUDA,
  sans repli CPU/Metal. Ses masques sont en mode `ignore`, pas `empty`.
- **Nerfstudio / Splatfacto** : `tools/libs/scripts/run.sh` prépare les données
  Nerfstudio, entraîne et exporte en `.ply` (parcours historique) ; voir le
  [guide Nerfstudio](nerfstudio.md).
- **Spirula** : installer l'outil suivant son
  [README](../tools/spirula/README.md), puis entraîner depuis le modèle COLMAP
  et ses images avec `tools/libs/scripts/run_spirula_block.sh`. Exemple :

```bash
bash tools/libs/scripts/run_spirula_block.sh \
  --sparse-dir /chemin/colmap/sparse/0 \
  --images-dir /chemin/images \
  --output-dir /chemin/resultats
```

Les options de ce lanceur sont accessibles avec `--help` ; l'export `.splat`
est décrit dans le [README du post-traitement](../tools/postprocess/README.md).

### 2.3. Utiliser un pipeline complet

Deux pipelines dans `script/` enchaînent la production des données COLMAP,
l'entraînement et l'export. Spirula reste le moteur par défaut ; Nerfstudio
peut être choisi pour l'entraînement avec `TRAIN_BACKEND=nerfstudio` :

- **Bbox + flux Panoramax → splat** : recherche et téléchargement des images,
  reconstruction, entraînement par blocs et fusion ; voir le
  [pipeline Panoramax](../tools/panoramax/README.md).
- **Images locales → splat** : sélection des images, reconstruction,
  entraînement par blocs et fusion ; voir le
  [pipeline images locales](../tools/images_to_splat/README.md).

## 3. Préparer les données et lancer un calcul

Le principe commun est de fournir des images et un modèle COLMAP cohérent :
les caméras, images enregistrées et points 3D doivent décrire la même scène.
Selon le parcours choisi, le dépôt peut préparer ce modèle ou réutiliser un
dataset déjà constitué.

Pour le pipeline Nerfstudio / Splatfacto, le point d'entrée est
`tools/libs/scripts/run.sh`. Exemple :

```bash
./tools/libs/scripts/run.sh \
  --dataset-dir /chemin/absolu/vers/mon_dataset \
  --output-dir /chemin/absolu/vers/mes_resultats \
  --gsplat-profile balanced \
  --name ma_scene
```

Cette commande illustre le lancement ; les options, valeurs possibles,
prérequis et étapes exécutées sont décrits dans le
[guide Nerfstudio](nerfstudio.md#3-configuration-et-utilisation).

Pour les autres parcours, utiliser les commandes et fichiers de configuration
de leur README : le pipeline images locales et le pipeline Panoramax ont des
étapes et sorties propres.

## 4. Consulter les guides des outils

- **Lancement Bash ou Slurm, ressources et suivi des jobs** :
  [guide de lancement](lancement_bash_slurm.md).
- **Préparation des données et sparse COLMAP** :
  [tools/preprocess](../tools/preprocess/README.md).
- **Choix du producteur COLMAP et conversion orientations/LiDAR** :
  [tools/images_to_colmap](../tools/images_to_colmap/README.md).
- **Masques de segmentation** :
  [tools/segmentation_masks](../tools/segmentation_masks/README.md).
- **Filtrage et fusion des splats** :
  [tools/postprocess](../tools/postprocess/README.md).
- **Nettoyage multivues du ciel** :
  [tools/clean_gs](../tools/clean_gs/README.md).
- **Installation de Spirula** :
  [tools/spirula](../tools/spirula/README.md).
- **Visualisation locale** :
  [tools/viewer](../tools/viewer/README.md).
- **Conversion d'un SPLAT géoréférencé en 3D Tiles** :
  [tools/splat_to_3dtiles](../tools/splat_to_3dtiles/README.md).

Ces documents détaillent les paramètres, dépendances, fichiers produits,
commandes de validation et particularités de chaque outil.

## 5. Limites, conseils pratiques et à savoir

- Une couverture suffisante de la scène et des points de vue variés sont
  importants ; des images prises presque sous le même angle peuvent mal
  contraindre la géométrie.
- Les surfaces cachées dans les images ne sont pas reconstruites de façon
  fiable par le seul apprentissage des splats.
- Les très grandes images et les scènes volumineuses peuvent demander beaucoup
  de mémoire et de temps de calcul.
- Des artefacts et des splats parasites peuvent apparaître, notamment aux
  limites de la scène ou dans les zones peu observées.
- Le résultat est une représentation visuelle complémentaire : il ne remplace
  pas automatiquement un maillage ou un produit géospatial répondant à des
  exigences métriques.
- Les scènes aériennes ou géoréférencées peuvent nécessiter des traitements
  complémentaires et ne garantissent pas à elles seules une reconstruction
  fiable.

Le pipeline Nerfstudio conserve un `transforms.json` déjà présent. Pour repartir
des paramètres COLMAP courants, supprimer ce fichier avant de relancer
`tools/libs/scripts/run.sh`. Les détails de ce comportement et des transformations sont
dans le [guide Nerfstudio](nerfstudio.md#2-entrées-et-sorties).
