# Guide utilisateur — produire et visualiser un Gaussian Splat

[Accueil du projet](../README.md)

Ce guide aide à choisir comment produire les données COLMAP et entraîner une
scène 3D. Le [README général](../README.md#1-quest-ce-que-le-3dgs-)
présente
les Gaussian Splats et le principe général. Les commandes complètes, options,
paramètres et détails de chaque outil sont documentés dans les README
spécialisés liés ci-dessous.

## 1. Installation

1. **Environnement Conda** : les librairies nécessaires aux pipelines sont contenues dans l'environnement Conda de votre plateforme, dans [`environment/`](environment/) (`macos`, `ign.slurm`, `google.colab`). Exemple : `conda env create --yes --file environment/macos/conda_env.yml`. Ensuite, activez-le avec `conda activate gsplat`.

2. **Spirula Studio** : compilez-le avec [`install/spirula/install_spirula.sh`](install/spirula/README.md), qui applique les bonnes options et télécharge le modèle SAM3 utilisé pour les masques.

3. **COLMAP et HLOC** : HLOC améliore la mise en correspondance des images pour la reconstruction 3D. Si vous utilisez cette option, installez-le avec `bash install/colmap/install-hloc.sh`. Voir [HLOC sur GitHub](https://github.com/cvg/Hierarchical-Localization) et le [README SfM](tools/sfm/README.md).

## 2. Choisir un parcours

### 2.1. Produire les données COLMAP

La première étape consiste à reconstruire les caméras et les points 3D depuis
les images, ou à réutiliser un modèle COLMAP existant avec ses images.

- Images : l'[outil images vers COLMAP](../tools/sfm/README.md)
  propose `COLMAP_METHOD=spirula|colmap|nerfstudio|orientations`, avec une
  configuration commune. Les outils de préparation (`../tools/prepare/README.md`)
  permettent aussi d'extraire des images d'une vidéo.
- Orientations et LiDAR : convertir les poses caméra vers le JSON explicite documenté
  et le nuage coloré vers PLY, puis utiliser `COLMAP_METHOD=orientations`.
  Pour des poses IGNF `.CON` et un `.LAZ`, utiliser
  [tools/con_laz_to_orientations](../tools/con_laz_to_orientations/README.md).
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
  de `scripts/images_to_3dgs.sh` ou `scripts/panoramax_to_3dgs.sh`.
  Le modèle COLMAP et le découpage restent communs ; chaque moteur dispose
  de ses sorties d'entraînement. Nerfstudio exige PyTorch et NVIDIA/CUDA,
  sans repli CPU/Metal. Ses masques sont en mode `ignore`, pas `empty`.
- **Nerfstudio / Splatfacto** : `tools/libs/scripts/run.sh` prépare les données
  Nerfstudio, entraîne et exporte en `.ply` (parcours historique) ; voir le
  [guide Nerfstudio](nerfstudio.md).
- **Spirula** : installer l'outil suivant son
  [README](../install/spirula/README.md), puis entraîner depuis le modèle COLMAP
  et ses images avec `tools/training/backends/train_block_spirula.sh`. Exemple :

```bash
bash tools/training/backends/train_block_spirula.sh \
  --sparse-dir /chemin/colmap/sparse/0 \
  --images-dir /chemin/images \
  --output-dir /chemin/resultats
```

Les options de ce lanceur sont accessibles avec `--help` ; l'export `.splat`
est décrit dans le [README du post-traitement](../tools/export/README.md).

### 2.3. Utiliser un pipeline complet

Trois pipelines enchaînent production COLMAP, entraînement et export. Chacun a une version directe (machine courante) et une version Slurm (`launch_*`, à lancer avec `bash`, pas `sbatch`, depuis le nœud de connexion) :

| Pipeline | Entrée | Direct | Slurm |
|---|---|---|---|
| Images locales | dossier de photos | `bash scripts/images_to_3dgs.sh CONFIG` | `bash scripts/launch_images_to_3dgs.sh CONFIG --wait` |
| Vidéo | fichier vidéo (extraction de `NUM_FRAMES` images) | `bash scripts/videos_to_3dgs.sh CONFIG` | `bash scripts/launch_videos_to_3dgs.sh CONFIG --wait` |
| Panoramax | emprise (`PANORAMAX_BBOX`) | `bash scripts/panoramax_to_3dgs.sh CONFIG` | `bash scripts/launch_panoramax_to_3dgs.sh CONFIG --wait` |

Sortie : un `.splat` et ses métadonnées dans `OUTPUT_DIR`. Les journaux sont dans `OUTPUT_DIR/logs/`.

Sans `--wait`, un retour réussi confirme seulement la soumission. Le détail des modes et des ressources est dans le [guide Bash / Slurm](lancement_bash_slurm.md), et les exemples de configuration sont dans [scripts/README.md](../scripts/README.md).

#### Grandes options : SfM et entraînement

- **SfM** (`COLMAP_METHOD`) : `spirula` (défaut, appariement par `SFM_SP_MATCHER`), `colmap` (extraction et appariement COLMAP, GPU optionnel), `hloc` (SuperPoint et LightGlue, retrieval NetVLAD), `nerfstudio` (`ns-process-data`, NVIDIA/CUDA requis) ou `orientations` (poses JSON fournies, sans SfM).
- **Entraînement** (`TRAIN_BACKEND`) : `spirula` (défaut, budget de splats réglé par `TRAIN_SP_QUALITY`) ou `nerfstudio` (`ns-train`, NVIDIA/CUDA requis, sans repli CPU).
- **Blocs** : chaque bloc s'entraîne sur un GPU ; `NUM_GPUS` fixe combien tournent en parallèle.

#### Paramètres utiles par phase

Valeurs par défaut exactes : `bash scripts/helper.sh <PARAMÈTRE>`.

**Commun**
- `SFM_IMAGES_DIR` : dossier source des photos (`--images` peut le remplacer).
- `OUTPUT_DIR` : workspace de sortie, distinct et extérieur au dossier d'images. Changer d'images ou de paramètres SfM impose un nouveau workspace.
- `SPIRULA_BIN` : chemin de l'exécutable Spirula.
- `NUM_CPUS` : threads du SfM, des masques, de l'entraînement et de la fusion.
- `NUM_GPUS` : nombre de GPU, un par bloc actif.
- `TRAIN_BACKEND` : moteur d'entraînement, `spirula` (défaut) ou `nerfstudio`.
- `PIPELINE_RESUME` : `true` pour reprendre après une interruption, sans supprimer les artefacts.

**Téléchargement (Panoramax)**
- `PANORAMAX_BBOX` : emprise `xmin,ymin,xmax,ymax` (obligatoire).
- `PANORAMAX_BBOX_EPSG` : système de coordonnées de l'emprise (`2154` par défaut).
- `PANORAMAX_PICTURE_TYPE` : `all`, `normal` (photos classiques) ou `360` (équirectangulaires).
- `PANORAMAX_QUALITY` : `hd`, `sd` ou `thumb`.
- `PANORAMAX_IMAGE_MAX` : nombre maximal de photos (vide = aucune limite).
- `PANORAMAX_DATETIME` : filtre de date au format ISO 8601 (vide = toutes dates).
- `PANORAMAX_FILTERS` : filtre sur les propriétés STAC ou EXIF, syntaxe `CLÉ=V1,V2;CLÉ2=V3`.
- `PANORAMAX_REPLACE_REQUEST` : `true` archive une requête différente puis retélécharge ; `false` arrête le lancement.

**SfM**
- `COLMAP_METHOD` : producteur du modèle (`spirula`, `colmap`, `hloc`, `nerfstudio`, `orientations`).
- `SFM_MAX_IMAGE_SIZE` : résolution maximale des images pour le SfM (`4096` par défaut). La réduire diminue fortement la mémoire GPU.
- `SFM_ALIKED_MAX_FEATURES` : nombre maximal de points par image (`8192` par défaut).
- `SFM_SP_QUALITY` : préréglage Spirula, de `low` à `extreme` (`high` par défaut).
- `SFM_CAMERA_MODE` : partage des intrinsèques, `folder` (défaut), `single` ou `image`.
- `SFM_CAMERA_MODEL` : force le modèle de caméra (sinon détecté : `equirectangular` pour le 360°, `opencv` sinon).
- `SFM_CAMERA_SPLIT` : `true` relance le SfM avec une caméra par modèle si Spirula en détecte plusieurs.
- `SFM_SP_PAIR_MAX_DISTANCE` : distance GPS maximale entre paires d'images, en mètres (vide = désactivé).
- `SFM_SP_METRIC_POSITIONS` : fichier de positions en mètres, qui remplace les GPS EXIF.
- `GEOREF_GPX_FILE` : trace GPX pour géoréférencer le modèle (prioritaire sur les GPS EXIF).
- `GEOREF_EPSG` : EPSG de l'origine projetée dans le rapport (`2154` par défaut).

**Masques**
- `SFM_MASKS` et `TRAIN_MASKS` : `on` ou `off`, pour le SfM et l'entraînement.
- `TRAIN_MASKS_BACKEND` : `spirula` ou `segmentation` (modèle local).
- `TRAIN_MASKS_SPIRULA_MODEL` : chemin du modèle SAM Spirula (`.ggml`).
- `MASKS_ACCEPT_LICENSE` : `true` seulement après acceptation des conditions SAM 3 de Meta.
- `TRAIN_MASKS_LABELS` : éléments à masquer, en anglais (`sky` par défaut).
- `TRAIN_MASKS_MODE` : `empty` (défaut, le ciel est appris comme vide), `ignore` (pixels exclus de la perte) ou `auto`.
- `TRAIN_MASKS_DIR` : dossier de masques PNG déjà calculés, réutilisé sans modèle ni GPU.

**Découpage**
- `SPLIT_NUM_BLOCKS` : nombre de blocs (`1` = pas de découpage).
- `SPLIT_OVERLAP_MAX_FRACTION` : recouvrement maximal entre blocs, entre 0 et 1 (`0.10` par défaut).

**Entraînement**
- `TRAIN_NUM_ITER` : nombre d'itérations par bloc (`20000` par défaut).
- `TRAIN_SP_QUALITY` : budget Spirula, de `low` (200 k splats) à `ultra` (10 M). Les budgets explicites priment.
- `TRAIN_SP_CAP_MAX` : nombre maximal de splats par bloc.
- `TRAIN_SP_FLOATER_SUPPRESSION` : suppression des flottants, `off`, `mild` ou `strong`.
- `TRAIN_NS_DOWNSCALE_FACTOR` : facteur de réduction des images (Nerfstudio uniquement, `1` = résolution native).
- `TRAIN_CLEAN` : `true` recalcule les sorties 3DGS des blocs, sans toucher au SfM (Nerfstudio uniquement).

**Nettoyage**
- `POSTPROCESS_CLEANER` : `contextual` (filtres géométriques, défaut) ou `cleangs` (nettoyage du ciel par vues multiples, CUDA requis).
- `POSTPROCESS_FILTER_MODE` : `diagnostic` (exporte les candidats rejetés sans les retirer), `apply` (les retire) ou `off`.

**Export**
- `MERGE_STRENGTH` : multiplicateur de taille des voxels de fusion (`1.0` par défaut).
- `EXPORT_SPLAT_NAME` : nom du fichier `.splat` exporté.

**Slurm**
- `SLURM_PARTITION` : partition du cluster.
- `SLURM_MEMORY` : mémoire par job (`64G` par défaut).
- `SLURM_TIME_LIMIT` : limite de temps par job (`48:00:00` par défaut).
- `SLURM_GPU_TYPE` : type de GPU (vide = `gpu:1`).
- `SLURM_ACCOUNT` et `SLURM_QOS` : compte et QoS, vides = valeurs par défaut du cluster.
- `SLURM_ENV_SETUP` : script sourcé sur chaque nœud pour charger l'environnement (modules, conda).

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
  [tools/prepare](../tools/prepare/README.md).
- **Choix du producteur COLMAP et conversion orientations/LiDAR** :
  [tools/sfm](../tools/sfm/README.md).
- **Conversion des poses caméra IGNF `.CON` et du LiDAR `.LAZ`** :
  [tools/con_laz_to_orientations](../tools/con_laz_to_orientations/README.md).
- **Masques de segmentation** :
  [tools/masks](../tools/masks/README.md).
- **Filtrage et fusion des splats** :
  [tools/export](../tools/export/README.md).
- **Nettoyage multivues du ciel** :
  [tools/export/clean_gs](../tools/export/clean_gs/README.md).
- **Installation de Spirula** :
  [install/spirula](../install/spirula/README.md).
- **Visualisation locale** :
  [tools/viewer](../tools/viewer/README.md).
- **Conversion d'un SPLAT géoréférencé en 3D Tiles** :
  [tools/export/export_3dtiles.md](../tools/export/export_3dtiles.md).

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
