# Télécharger des photos Panoramax par bbox

[Retour à la documentation générale](../../doc/guide_utilisateur.md)

## 1. Objectif

Ce dossier transforme les photos Panoramax d'une bbox en un fichier `.splat`
3D Gaussian Splatting. Il propose deux niveaux : le pipeline complet
(`scripts/panoramax_to_3dgs.sh`) et le téléchargement seul (`download.sh`).

### Pipeline complet bbox → `.splat`

`scripts/panoramax_to_3dgs.sh` exécute :

```text
Bbox + configuration
        ↓
Téléchargement Panoramax → préparation des images
        ↓
[masques SfM optionnels] → [filtre optionnel des paires GPS]
        ↓
SfM géoréférencé par EXIF → découpage COLMAP
        ↓
Entraînement Spirula par bloc → recadrage bbox des blocs
        ↓
Fusion voxelisée → filtre contextuel (diagnostic par défaut)
        ↓
.splat + références .georef/
```

Les splats alignés des blocs sont fusionnés en un unique fichier final. La
configuration est décrite en section 3.

### Téléchargement seul (`download.sh`)

Le lanceur télécharge les images HD disponibles dans une bbox depuis le
catalogue fédéré Panoramax, sans compte utilisateur. La bbox est transformée
en WGS84 pour la recherche STAC, puis chaque point de prise de vue est vérifié
dans la bbox d'origine.

```bash
bash tools/panoramax/download.sh \
  --bbox "651000,6861000,652000,6862000" --crs EPSG:2154 \
  --output-dir /chemin/vers/panoramax
```

| Option | Description |
|---|---|
| `--bbox "xmin,ymin,xmax,ymax"` | Zone de recherche, dans le système indiqué par `--crs` |
| `--output-dir DOSSIER` | Dossier de sortie (images, métadonnées, manifeste, `summary.json`) |
| `--crs EPSG:2154` | Système de coordonnées de `--bbox` ; défaut `EPSG:2154` (Lambert-93) |
| `--datetime "2026-01-01T00:00:00Z/2026-10-31T23:59:59Z"` | Intervalle de prise de vue, transmis à l'API |
| `--filter KEY=VALEURS` | Filtre répétable sur les propriétés STAC ou les tags EXIF (voir section 2) |
| `--image-max 400` | Limite le nombre total de photos sélectionnées, toutes les pages confondues |
| `--quality sd` | Résolution SD ; valeurs : `hd` (défaut), `sd`, `thumb` |
| `--api-url https://une-instance/api` | Autre API compatible ; défaut : catalogue fédéré `explore.panoramax.fr` |
| `--timeout 120` | Délai réseau par requête, en secondes |

Les coordonnées de l'exemple sont illustratives, pas une garantie de présence de photos.

## 2. Entrées et sorties

L'ordre est **xmin,ymin,xmax,ymax**, dans le système de coordonnées désigné
par le code EPSG. Le défaut est **EPSG:2154 (Lambert-93)**. Le script transforme
la bbox en WGS84 pour la recherche STAC, puis vérifie chaque point de prise de
vue dans la bbox et son système d'origine.



### Sorties et reprise

```text
output-dir/
├── <id-collection-stac>/images/<identifiant-haché>.jpg
├── <id-collection-stac>/metadata/<identifiant-haché>.json
├── <id-collection-stac>-<LARGEURxHAUTEUR>/...   # seulement si la collection mélange plusieurs tailles
├── pages/page-000001.json
├── manifest.jsonl
├── request.json               # bbox, code EPSG d'entrée, requête API et filtres
└── summary.json
```

Chaque collection STAC a son dossier (identifiant STAC). Si une collection contient
plusieurs tailles d'image, ses photos sont réparties dans `<id>-<LxH>/` pour garantir
une taille unique par dossier.

Filtres répétables `--filter KEY=VALEURS` : KEY est une propriété STAC (ex.
`geovisio:producer`) ou un tag EXIF (ex. `Exif.Image.Software`) ; les valeurs
séparées par des virgules sont en OU, les filtres successifs en ET, sans tenir
compte de la casse :

```bash
bash tools/panoramax/download.sh \
  --bbox "651000,6861000,652000,6862000" --crs EPSG:2154 \
  --output-dir /chemin/vers/panoramax \
  --filter "geovisio:producer=immergis,overflorian" \
  --filter "Exif.Image.Software=H19.03.02.02.00"
```

Dans le pipeline complet, la même syntaxe s'écrit dans une seule variable :
`PANORAMAX_FILTERS="geovisio:producer=immergis,overflorian;Exif.Image.Software=H19.03.02.02.00"`.
Exemples commentés et `PANORAMAX_PROVIDER` : [filtrer par producteur ou par logiciel EXIF](../../examples/panoramax.md#filtrer-par-producteur-ou-par-logiciel-exif).

Les métadonnées conservent le Feature STAC complet : coordonnées, date, séquence,
propriétés, assets, liens de provenance et informations de licence/attribution
présentes dans la réponse. Les fichiers sont nommés à partir d'un hash de
collection + identifiant photo pour éviter collisions et noms dangereux.
Le manifeste associe les identifiants Panoramax aux fichiers locaux.

## 3. Configuration et utilisation

### Entraînement Spirula ou Nerfstudio

Le pipeline global est `scripts/panoramax_to_3dgs.sh`.
Le SfM géoréférencé utilise Spirula par défaut ; `TRAIN_BACKEND=nerfstudio` permet
d'entraîner les mêmes blocs avec Splatfacto. PyTorch et NVIDIA/CUDA sont alors
obligatoires, sans repli CPU/Metal. Les masques d'entraînement sont produits
par le [tool de segmentation](../segmentation_masks/README.md), avec
Mask2Former ou SegFormer : `TRAIN_MASKS_BACKEND=segmentation`, modèle local
dans `TRAIN_MASKS_MODEL`, `TRAIN_MASKS_LABELS=sky` et
`TRAIN_MASKS_MODE=ignore`. Les PNG sont référencés dans le dataset Nerfstudio ;
le mode `empty` n'est pas transposé.
Les résultats Nerfstudio ont un suffixe `-nerfstudio` pour ne pas réutiliser
les checkpoints Spirula. La fusion reste en coordonnées COLMAP originales.
Les réglages Spirula de cap/croissance/qualité ne sont pas des budgets Splatfacto.
`TRAIN_NS_TRAIN_BIN`, `TRAIN_NS_EXPORT_BIN` et `TRAIN_NS_DOWNSCALE_FACTOR` règlent ce moteur.

### Paramètres par backend

Les variables `SFM_SP_*` pilotent le SfM Spirula
de Panoramax, indépendamment de `TRAIN_BACKEND`. Le mode HLOC 360 ci-dessous
utilise les réglages `HLOC_*` à la place.
`SFM_MAX_ERROR`, `SFM_MIN_INLIERS`, `SFM_MIN_TRI_ANGLE`, `SFM_MAX_IMAGE_SIZE`,
`SFM_ALIKED_MAX_FEATURES`, `NUM_CPUS` et `SFM_CAMERA_MODEL` sont les paramètres communs exposés
au SfM Spirula. HLOC conserve ses propres réglages et seuils pycolmap. Si `SFM_CAMERA_MODEL` n'est pas défini, le modèle Spirula est détecté (360° -> `equirectangular`,
sans focale ; sinon `opencv`). Le définir force le modèle. Les options `TRAIN_SP_*` concernent uniquement l'entraînement
Spirula ; `TRAIN_NS_*` ne concerne que Nerfstudio/Splatfacto. `TRAIN_NUM_ITER`
reste partagé. Les exemples et commentaires de
[`config/defaults/train.default.sh`](../../config/defaults/train.default.sh) et [`config.default.sh`](config.default.sh) regroupent ces paramètres par backend.

### Panoramas 360 avec HLOC/pycolmap

Utiliser un nouveau `OUTPUT_DIR` :

```bash
PANORAMAX_PICTURE_TYPE="360"
COLMAP_METHOD="hloc"
HLOC_CAMERA_MODEL="EQUIRECTANGULAR"
SFM_EQUIRECTANGULAR_VIEW_SIZE="1024"
```

Les panoramas complets 2:1 deviennent six vues pinhole de champ 110° avec
recouvrement, utilisées pour le SfM et l'entraînement choisi par
`TRAIN_BACKEND`. `HLOC_CAMERA_MODEL=EQUIRECTANGULAR` décrit les images d'entrée ;
les vues sont reconstruites en `PINHOLE` avec leur focale fixée. Les vues
d'un même panorama ne sont pas contraintes en rig. Installer HLOC avec
`bash $GIT_ROOT/tools/colmap/install-hloc.sh` ; numpy, OpenCV, Pillow, scipy,
pyproj et pycolmap sont requis dans `PYTHON_BIN`.

Les positions d'origine sont conservées dans `sfm/sparse/0/gps_positions.json`.
Le modèle est recalé en ENU local métrique, et l'origine est décrite dans le
CRS `PANORAMAX_BBOX_EPSG` (2154 par défaut). Le recalage exige au moins trois
positions reconstruites et une trajectoire non rectiligne. Sans altitudes
complètes, seul le plan horizontal est ancré : l'altitude locale est arbitraire
et la verticale suppose les panoramas redressés. Contrôler les résidus dans
`georef_stats.json` et vérifier visuellement ; les altitudes GPS brutes ne sont
pas corrigées du géoïde. Les masques générés sont appliqués aux vues projetées.
Le filtre `SFM_SP_PAIR_MAX_DISTANCE`, MVS et LiDAR ne sont pas disponibles dans
ce mode. Les changements d'images ou de réglages exigent un nouveau workspace.

### Corrections photométriques Spirula

`SFM_SP_IMAGE_EXPOSURE` concerne
l'analyse SfM et n'agit pas sur l'entraînement. Les `TRAIN_SP_*` règlent la
correction PPISP et la grille bilatérale à l'entraînement ; `no_crf` inclut le
vignettage, `no_crf_no_vig` l'omet, et l'initialisation EXIF ne fonctionne que
si ces métadonnées sont présentes. Ces options ne sont pas transmises à
Nerfstudio. L'exposition SfM n'est pas disponible avec le filtre
`SFM_SP_PAIR_MAX_DISTANCE`. Valeurs et défauts dans
[`config/defaults/train.default.sh`](../../config/defaults/train.default.sh).

### Paramètres SfM exposés

- `SFM_MAX_ERROR`, `SFM_MIN_INLIERS` et `SFM_MIN_TRI_ANGLE` règlent les seuils
  géométriques. `SFM_SP_BA_LOSS` et `SFM_SP_BA_LOSS_PARAM` sont propres au
  bundle adjustment Spirula.
- `SFM_SP_FEATURES`, `SFM_SP_MATCHER`, `SFM_SP_PAIRS`,
  `SFM_SP_IMAGE_EXPOSURE` et `SFM_SP_PAIR_MAX_DISTANCE` sont spécifiques à
  Spirula. Le dernier filtre les paires par distance GPS.
- Transmission : au SfM natif et, si le filtre GPS est activé, aux étapes
  `match` et `map` concernées.
- `tools/sfm/spirula/run_spirula_sfm.sh` : wrapper direct ; options CLI équivalentes
  `--max-error`, `--min-inliers`, `--min-tri-angle`, `--ba-loss`,
  `--ba-loss-param`.
- Vérifier la disponibilité de ces options dans la version installée de
  [Spirula](../spirula/README.md).



### Installation

Dans ton environnement Python actif (Python 3.10 ou plus récent) :

```bash
python3 -m pip install -r tools/panoramax/requirements.txt
```


### Utilisation

Les options du téléchargement sont décrites en section 1, dans
« Téléchargement seul ».

### Reconstruction Spirula géoréférencée par l'EXIF

Les JPEG HD de Panoramax conservent le GPS EXIF (latitude, longitude,
altitude, cap). Spirula s'en sert directement pour placer le modèle dans un
repère local est-nord-haut en mètres, sans recalage GPX a posteriori :

```bash
bash tools/sfm/spirula/run_spirula_sfm.sh \
  --spirula-bin /chemin/vers/spirula \
  --dataset OUTPUT_DIR \
  --output-dir OUTPUT_DIR/sfm \
  --preset panoramax
```

`--preset panoramax` règle `--data-type individual`, `--pairs auto`, aucune
séquence (les noms de fichiers sont des empreintes), `--metric-gps full` et
`--sensor-pairs --sensor-pair-radius 30`. Un code de sortie 4 signifie que le
modèle a été écrit sans le repère métrique demandé.

### Configuration du pipeline complet

- Variante : le [nettoyage expérimental Clean-GS](../clean_gs/README.md) peut remplacer le filtre
  contextuel.
- Configuration : fichier Bash (code de confiance) ; chaque variable, ses
  valeurs possibles et son défaut sont commentés dans
  [`tools/panoramax/config.default.sh`](config.default.sh) (propres à Panoramax) et
  [`config/defaults/`](../../config/defaults/) (communs par phase).
- Défauts : `config/defaults/*.default.sh` puis `config.default.sh` sont chargés en premier par le pipeline ; la configuration
  utilisateur ne contient que ses surcharges. Il n'y a pas de préréglage de croissance sobre.
- Valeurs à définir dans la configuration utilisateur (sans défaut) : `OUTPUT_DIR` et `PANORAMAX_BBOX`.

Le découpage privilégie les séparations de faible covisibilité et vise des
blocs d'environ `N / SPLIT_NUM_BLOCKS` images principales (tolérance de 20 %,
arrondie à l'entier). La connexité prime : des tailles plus inégales sont
acceptées avec un avertissement si aucun déplacement de frontière sûr
n'est possible. Le recouvrement peut ensuite augmenter les effectifs.

Un exemple de configuration minimale et complète, avec la commande de lancement,
est dans [l'exemple Panoramax](../../examples/pipelines.md#panoramax--3dgs).

- Validation de configuration : avant téléchargement et, avec le lanceur Slurm,
  avant toute soumission.
- Erreurs : regroupées et indiquent le paramètre, sa valeur et les valeurs
  attendues (par exemple `lightblue` est refusé pour `SFM_SP_MATCHER`, sans
  correction automatique).
- Listes Spirula : correspondent aux options prises en charge par ce lanceur ;
  les actualiser si une future version ajoute des extracteurs, apparieurs ou préréglages.
- Validation seule : ne nécessite ni GPU, ni Python, ni Slurm.

```bash
bash tools/panoramax/validate_config.sh /chemin/vers/paris.sh slurm
```


Les intermédiaires sont rangés sous `OUTPUT_DIR/panoramax/`. Le résultat est
`OUTPUT_DIR/EXPORT_SPLAT_NAME`; son dossier adjacent `EXPORT_SPLAT_NAME` sans extension suivi
de `.georef/` contient `georeference.json`, les métadonnées Panoramax, le
modèle COLMAP métrique, le manifeste de blocs et les transformations
d'entraînement. Le `.splat` est en coordonnées du repère métrique local de
Spirula, aligné sur le GPS EXIF ; ce n'est pas un fichier de coordonnées EPSG
directes. Ces éléments latéraux conservent les références nécessaires pour
retrouver/interpréter ce repère.

- Filtre géométrique : **global, après fusion**, sans seuil en mètres.
- `POSTPROCESS_FILTER_MODE="diagnostic"` : défaut ; valeurs alternatives
  `"apply"` ou `"off"`.
  Le mode `diagnostic` conserve la scène et exporte les grands splats
  périphériques sans support de surface dans `NOM.rejected.splat` ; `apply`
  retire les candidats après examen ; `off` désactive le test.
- Sorties : `NOM.unfiltered.splat` conserve la fusion d'origine ;
  `NOM.filter.json` donne les compteurs.
- `POSTPROCESS_FILTER_SIZE_FACTOR="10"` : règle le rapport de taille locale ;
  défaut `10`.
Voir [la description et les limites du filtre](../postprocess/README.md).

- `POSTPROCESS_CLEANER="cleangs"` : remplace le filtre par le
  nettoyage expérimental du ciel multivues.
- `CLEANGS_MODEL` : dossier local de segmentation requis par ce nettoyeur.
- `CLEANGS_MODE="diagnostic"` : conserve les splats et exporte les rejets ;
  `apply` les retire.
- Slurm avec Clean-GS : la fusion demande alors un GPU NVIDIA.
- Installation, dépendances et précautions :
  [tools/clean_gs](../clean_gs/README.md).
- Installation complète : `bash tools/clean_gs/install.sh
  --install-dir /chemin/partage/cleangs --python python3` ; le script affiche
  `CLEANGS_PYTHON` et `CLEANGS_MODEL` à ajouter à la configuration.

- `TRAIN_MASKS="on"` : active les masques d'entraînement ; défaut `off`.
- `TRAIN_MASKS_BACKEND="segmentation"` : utilise SegFormer/Mask2Former.
- `TRAIN_MASKS_BACKEND="spirula"` : utilise SAM 3 natif sur Vulkan.
- `TRAIN_MASKS_SPIRULA_MODEL` : checkpoint GGML local utilisé par le backend `spirula`.
- Génération : avant le découpage, un segmenteur local génère un masque par
  image dans `sfm/train_masks/`, transmis à Spirula.
- Défaut des labels : seul le ciel (nuages compris) est masqué et appris comme vide.
- `TRAIN_MASKS_LABELS` : permet d'ajouter d'autres labels (`person`, `car`…).
- Installation, variables et limites :
  [tools/segmentation_masks](../segmentation_masks/README.md).

#### Qualité d'entraînement

- `TRAIN_SP_DISTRACTION_ROBUSTNESS="off"` : défaut ; valeurs possibles `mild` et
  `strong` également ;
  transmet `--distraction-robustness` à Spirula en local et sous Slurm.
- `mild` et `strong` modifient les scores de densification pour limiter
  l'influence des incohérences entre photos. Commencer par `mild` ; `strong`
  peut aussi atténuer des détails peu observés.
- Ce réglage ne remplace ni les masques ni `TRAIN_SP_FLOATER_SUPPRESSION`.
- `TRAIN_SP_WARP_SPHERICAL=true` (défaut Spirula) découpe les photos 360 en vues
  pinhole ; `false` entraîne directement sur l'équirectangulaire (rendu à vérifier).
- Appliquer à un bloc terminé : réentraîner avec `TRAIN_CLEAN=true`.

- `TRAIN_SP_QUALITY=""` : défaut ; valeurs possibles `low`, `medium`, `high` ou
  `ultra`. Vide conserve le comportement précédent. Une valeur renseignée transmet
  `--quality` à `spirula train`, en local comme sous Slurm.
- Dans Spirula, cette macro propose un budget de splats et une durée ; ce n'est
  pas un réglage de résolution.
- Priorité : les valeurs explicites `TRAIN_SP_CAP_MAX` (éventuellement limité par
  `TRAIN_SP_SPLATS_PER_IMAGE`) et `TRAIN_NUM_ITER` du pipeline restent prioritaires ;
  changer uniquement `TRAIN_SP_QUALITY` ne relève donc pas ces deux valeurs.

#### Masques avant SfM et partage avec l'entraînement

- `SFM_MASKS="on"` : prépare les images puis calcule les masques **avant**
  l'extraction/appariement.
- Transmission : le lanceur passe `--feature-masks` à Spirula (y compris avec
  le filtre GPS) et force la réextraction lorsqu'un nouveau SfM est nécessaire.
- `SFM_MASKS_*` / `TRAIN_MASKS_*` : masques pour le SfM / l'entraînement ;
  réglages, héritage et défauts dans
  [`config/defaults/masques.default.sh`](../../config/defaults/masques.default.sh).
- `MASKS_ACCEPT_LICENSE="true"` : transmet explicitement
  `--accept-license sam3=yes` pour les deux calculs SAM 3 non interactifs.
  Défaut `false` ; ne l'activer qu'après lecture et acceptation des conditions Meta.
- Partage des masques : si les labels (ordre/casse ignorés), le backend, le
  modèle, le périphérique, le Python et les seuils de génération sont identiques,
  le pipeline calcule une seule série dans `sfm/train_masks/` puis crée le
  lien symbolique `sfm/feature_masks -> train_masks`. Sinon, il calcule les
  deux séries.
- Des labels identiques avec des seuils différents ne doivent pas partager les
  mêmes PNG.
- `TRAIN_MASKS=off` : permet aussi de masquer uniquement le SfM.

- Signature : la politique SfM est enregistrée dans `sfm_mask_policy.json`.
  Un SfM ou des caractéristiques déjà présents sans signature compatible sont
  refusés ; choisir un nouvel `OUTPUT_DIR` ou déplacer le workspace en sauvegarde.
- `TRAIN_CLEAN=true` : ne recalcule **pas** le SfM.
- Ne pas modifier les fichiers de masques manuellement pendant les calculs.
- Si `feature_masks` est déjà un dossier réel et qu'un nouveau réglage demande
  un partage, le pipeline refuse de le remplacer par un lien ; utiliser un
  nouveau workspace. Inversement, séparer des masques précédemment partagés
  préserve une copie des masques SfM avant de changer ceux d'entraînement.
- `SFM_MASKS_LABELS="sky"` : peut réduire les correspondances parasites dans
  les nuages. Ajouter `person,car` retire également des points sur les voitures
  stationnées et peut réduire le nombre de vues enregistrées.
- Les résultats avec SAM/Spirula réels restent à contrôler sur votre matériel.
- Les blocs déjà entraînés ne sont pas recalculés ; utiliser
  `TRAIN_CLEAN=true`.


- Spirula (MCMC) augmente le nombre de splats jusqu'à `cap_max` ; atteindre le
  plafond est donc attendu.
- `TRAIN_SP_CAP_MAX` : plafond dur **par bloc** ; valeur initiale `1500000`.
- `TRAIN_SP_SPLATS_PER_IMAGE` : défaut vide ; le budget reste `TRAIN_SP_CAP_MAX`.
- `TRAIN_SP_GROWTH_FACTOR` : défaut vide ; le facteur de croissance est estimé
  automatiquement. Une valeur explicite reste prioritaire et n'est pas bornée
  automatiquement.
- `TRAIN_SP_REFINE_STOP_ITER` : défaut vide ; valeur d'arrêt effective par défaut
  déterminée avec `TRAIN_SP_REFINE_STOP_NUM_ITER`.
- `TRAIN_SP_REFINE_STOP_NUM_ITER` : défaut vide ; paramètres d'arrêt par défaut
  `14000/2500`.
- Avec ces variables vides, le budget reste `TRAIN_SP_CAP_MAX`, la croissance est
  estimée automatiquement et les paramètres d'arrêt utilisés sont `14000/2500`.
- Estimation automatique : si `TRAIN_SP_GROWTH_FACTOR` est absent ou vide, chaque
  worker local ou Slurm estime le facteur pour approcher le plafond **à la fin
  de la croissance** avec `facteur = (plafond / nombre_initial_estimé)^(1 / cycles)`,
  borné à **[1.001, 1.05]**.
- Initialisation estimée : `10 %` du budget (rapport égal à 10 quel que soit
  le plafond, y compris avec `TRAIN_SP_SPLATS_PER_IMAGE`). L'option worker
  `--min-init-fraction`, si utilisée, remplace cette fraction.
- Cycles : tous les 100 pas, strictement après 500 et strictement avant
  `min(TRAIN_NUM_ITER, arrêt_effectif)`, selon le calendrier natif Spirula.
- Exemple : avec 20000 itérations et un arrêt 14000/4000, cela fait 154 cycles
  et un facteur d'environ **1.015064**, laissant 4000 pas sans croissance.
- Logs : facteur estimé, nombre de cycles et hypothèses affichés ; un bornage
  déclenche un avertissement.
- Si aucun cycle n'est disponible ou si la fraction initiale est nulle,
  l'estimation échoue explicitement : modifier le calendrier ou fournir un
  facteur explicite.
- L'estimation est une approximation, pas une garantie d'atteindre le plafond :
  le nombre initial peut dépasser 10 % si le nuage sparse est plus fourni, et
  la dynamique réelle de densification/relocalisation peut modifier la trajectoire.
- Exemple de réglage sobre :
  - `TRAIN_SP_SPLATS_PER_IMAGE="2000"` : budget
    `min(TRAIN_SP_CAP_MAX, images × TRAIN_SP_SPLATS_PER_IMAGE)`.
  - `TRAIN_SP_GROWTH_FACTOR="1.03"`.
  - `TRAIN_SP_REFINE_STOP_ITER="18000"`.
  - `TRAIN_SP_REFINE_STOP_NUM_ITER="12000"`.
  Avec 30000 itérations, la croissance s'arrête à 60 % du calcul.
- Arrêt effectif : `max(TRAIN_SP_REFINE_STOP_ITER, TRAIN_NUM_ITER - TRAIN_SP_REFINE_STOP_NUM_ITER)`.
- Calibrer `TRAIN_SP_SPLATS_PER_IMAGE` en comparant des rendus de même vue, pas
  sur le PSNR d'une étape isolée.
- Réexport des blocs : sans filtre de BBOX/taille lors de leur relance et sans
  réentraînement si le checkpoint final existe et `TRAIN_CLEAN=false` ; c'est
  nécessaire pour récupérer les splats supprimés par un ancien export.

- Reprise : un `OUTPUT_DIR` existant ne peut être repris qu'avec les mêmes bbox,
  dates, qualité et limite d'images. Pour changer la sélection, choisir un
  nouveau `OUTPUT_DIR` afin de ne pas mélanger des images de plusieurs requêtes.
- `PANORAMAX_REPLACE_REQUEST="true"` : renomme l'ancien `OUTPUT_DIR/panoramax`
  en `panoramax.replaced-AAAAMMJJ-HHMMSS` (rien n'est supprimé), puis la
  nouvelle requête est téléchargée dans un dossier vide.

#### Filtre exclusif des paires par distance GPS

- `SFM_SP_PAIR_MAX_DISTANCE` : rayon en mètres ; valeur `""` désactive le filtre
  (défaut).

```bash
SFM_SP_PAIR_MAX_DISTANCE="30"  # mètres ; "" désactive le filtre (défaut)
```

Quand il est activé, le pipeline exécute les commandes natives Spirula
`sfm extract`, `sfm match`, puis filtre les correspondances **avant**
`sfm map`. Aucun patch ni recompilation de Spirula n'est nécessaire.
Pour deux photos positionnées, seules les paires à distance **strictement
inférieure** au rayon sont conservées. Les paires dont au moins une photo
n'a pas de GPS exploitable sont conservées, avec un avertissement.
La distance est géodésique horizontale WGS84, calculée depuis les coordonnées
des métadonnées Panoramax ; l'altitude n'intervient pas.

Ce filtre retire des paires candidates ; il n'en ajoute aucune, contrairement
au `--sensor-pairs` du mode auto non filtré. `SFM_SP_PAIRS` continue de choisir les
candidats initiaux. L'appariement est effectué avant le filtre : celui-ci ne
réduit donc pas son coût.

Le workspace `panoramax/sfm/` conserve `matches.bin` original et
`matches-gps.bin` filtré. Le marqueur `.gps_filter_done` enregistre la signature
du filtrage (rayon, appariement, géométrie, empreinte des masques) : un SfM
identique est réutilisé, tout autre réglage est refusé sur un workspace existant.
Le marqueur est copié dans le dossier final `.georef/` sous
`gps_filter_signature.txt`. Les formats VKMT v2, v3 et v4 sont reconnus ; un format inconnu
ou tronqué provoque une erreur explicite, sans écraser l'original.
Si aucune paire ne subsiste, la reconstruction ne démarre pas.

Un SfM existant ne peut pas être corrigé en filtrant son modèle COLMAP final :
il faut le reconstruire. Pour activer, désactiver ou changer le rayon sur un
calcul existant, déplacer le workspace `panoramax/sfm/` **et**
les blocs `blocks/` et les entraînements `gs/` en sauvegarde,
puis relancer la préparation et les blocs ; garder les dossiers `<id-collection>*/` (images et metadata)
pour éviter de retélécharger. Le lanceur refuse de réutiliser un SfM dont
la signature ne correspond pas au rayon, aux paramètres et aux entrées.
À paramètres identiques, un SfM filtré terminé est réutilisé ; après un échec
avant reconstruction, extraction et appariement sont relancés. Un modèle
partiel doit être déplacé en sauvegarde avant de réessayer.

#### Exécution GPU sur Slurm

Le [guide de lancement Bash / Slurm](../../doc/lancement_bash_slurm.md) décrit
les ressources `SLURM_*`, le parallélisme des blocs, l'environnement des nœuds,
les dépendances et le suivi des jobs.

```bash
# Slurm : ressources SLURM_* de la configuration, un GPU par bloc actif.
bash scripts/launch_panoramax_to_3dgs.sh /chemin/partage/config.sh
```

- Sorties : le `.splat` fusionné et son dossier `.georef/` restent dans `OUTPUT_DIR`.

#### Récupération après SIGSEGV

Si `sfm auto` termine avec SIGSEGV après l'export, le wrapper vérifie que les
trois fichiers binaires de `sparse/0` ont été créés pendant cette exécution,
se lisent entièrement et contiennent des caméras, images et points. Si ces
conditions sont satisfaites, il poursuit avec un avertissement explicite.
Des fichiers préexistants, absents, vides ou tronqués restent bloquants.
Cette récupération ne corrige pas le crash et ne certifie pas la qualité du
modèle ; les codes d'erreur de qualité ou de géoréférencement restent propagés.
#### Ressources CPU

`NUM_CPUS` (défaut `2`) limite les CPU utilisés par SfM, les masques et les
processus parallèles ; sous Slurm, il sert aussi à `--cpus-per-task`.

#### Étapes séparées

Les étapes sont également accessibles individuellement, avec la même config :

```bash
bash scripts/panoramax_to_3dgs.sh config.sh prepare
bash scripts/panoramax_to_3dgs.sh config.sh train-block 0  # indices 0 à N-1
bash scripts/panoramax_to_3dgs.sh config.sh merge
```

Seule `prepare` télécharge et découpe ; chaque tâche `train-block` ne modifie
que sa sortie. `merge` exige tous les splats et ne relance aucun entraînement.
Sans argument d'étape, le pipeline local reste séquentiel et complet.

#### Préparation SfM (prepare_sfm.py)

Le préréglage lance d'abord `prepare_sfm.py` :

- seules les images de la taille la plus fréquente sont gardées (une bbox
  mélange souvent plusieurs appareils : 360° en 2:1, téléphones...) ; elles
  sont liées par lien physique dans `OUTPUT_DIR/sfm/images`, sans toucher
  aux originaux ; `--keep-size LxH` impose une autre taille ;
- la focale initiale est déduite du champ de vue horizontal des métadonnées,
  `f = (largeur / 2) / tan(fov / 2)`, soit environ 1877 px pour une imajbox
  4096×2160 à 95° ; l'EXIF HD n'ayant pas de focale, Spirula la devinerait
  sinon très loin ; `--focal` reste prioritaire ;
- si toutes les images retenues sont à 360° (`type` équirectangulaire,
  GPano ou champ de vue 360 dans les métadonnées), `--camera-model` passe à
  `equirectangular` et aucune focale n'est transmise ; un `--camera-model` ou
  `CAMERA_MODEL` explicite reste prioritaire ;
- le choix est résumé dans `OUTPUT_DIR/sfm/selection.json`.

`--no-prepare` lance Spirula sur le dataset tel quel. Le script se lance
seul :

```bash
python3 tools/sfm/prepare_sfm.py --dataset OUTPUT_DIR \
  --output-dir OUTPUT_DIR/sfm --dry-run
```

### Vérification locale

```bash
python3 -m unittest discover -s tools/panoramax -p 'test_*.py'
```

Les tests utilisent une API HTTP locale pour vérifier la pagination, la sélection
géographique, la reprise, les erreurs de téléchargement et les nouvelles tentatives.


## 4. Fonctionnement interne

Le pipeline **Panoramax** recherche et télécharge les photos, puis prépare
la reconstruction, entraîne les blocs et rassemble le résultat. Il partage
avec le pipeline images locales des outils pour gérer les masques, vérifier
les réglages, recadrer les résultats et les fusionner.

- Les scripts communs de configuration vérifient les réglages avant le calcul.
- Les scripts communs de masques appliquent les mêmes règles à la reconstruction
  et à l'entraînement.
- Les scripts communs de post-traitement recadrent et fusionnent les blocs.
- En mode Slurm, les scripts communs soumettent les étapes et arrêtent les
  calculs dépendants lorsqu'une étape échoue.

Les étapes propres à Panoramax sont la recherche et le téléchargement des photos,
la gestion de leurs métadonnées et l'utilisation de leur position GPS. Le
pipeline images locales sélectionne les fichiers fournis par l'utilisateur et
peut recaler la reconstruction sur une trajectoire GPS au format GPX.

## 5. Notes et informations complémentaires

### Mode Clean-GS adapté

Ce mode est une adaptation de Clean-GS, pas son algorithme Pareto officiel.
Voir [tools/clean_gs](../clean_gs/README.md).



### Validation de la configuration

La validation contrôle les choix SfM/entraînement, entiers, rayons, recouvrement,
booléens, bbox ordonnée, URL et nom de sortie, ainsi que la syntaxe des
ressources Slurm en mode `slurm`. Elle n'exécute pas Spirula, ne vérifie pas
la disponibilité des partitions/ressources du cluster et ne garantit pas
la compatibilité de la version installée. Le fichier Bash reste du code
de confiance et est sourcé, même en validation seule.



### Références

- API : https://explore.panoramax.fr/swagger
- CLI officielle : https://docs.panoramax.fr/cli/COMMANDS/#panoramax_cli-download
- Catalogue GeoParquet (alternative pour les grandes recherches) :
  https://docs.panoramax.fr/federated-catalog/tutorials/duckdb/



### Reprise et manifeste

Relancer la même commande reprend les images dont l'URL et la taille correspondent
aux métadonnées sauvegardées. Les téléchargements utilisent des fichiers temporaires
avant remplacement ; les photos en échec sont signalées par un avertissement,
indiquées dans le manifeste et comptées dans `summary.json`. Le pipeline continue
avec les photos utilisables. Aucune photo utilisable, une erreur de recherche API
ou une autre erreur fatale entraîne un code de sortie non nul.
Les réponses API brutes sont conservées.
Les types HTTP `image/jpeg`, `application/octet-stream` et `binary/octet-stream`
sont acceptés ; la signature JPEG et la taille téléchargée restent vérifiées.
Le manifeste et le résumé décrivent le dernier lancement ; les anciens fichiers
restent présents, donc utiliser un dossier distinct pour une autre sélection.

### Limites de l'API

Les requêtes sont séquentielles, avec trois tentatives en cas d'erreur réseau,
HTTP 429 ou 5xx. Une grande bbox peut représenter un volume important ; commencer
par une petite zone et respecter les conditions d'utilisation et d'attribution
des instances et des photos.

### Progression

Dans un terminal interactif, une barre de progression affiche les photos
téléchargées, réutilisées ou en erreur. Si moins que la limite demandée sont
trouvées, la barre se termine à 100 % du total effectivement sélectionné.

### Recherche et comptage

Le script demande les métadonnées à l'API (sans télécharger les images à cette
étape) en suivant la pagination. L'API ne renvoie jamais plus de 10 000 résultats
par requête : si une requête en atteint 10 000, la zone est découpée en quatre
quadrants, récursivement, jusqu'à `MAX_SPLIT_DEPTH` (8). Les doublons sont
supprimés par `collection/id`. Le script compte ensuite les photos dont le point
est dans la bbox exacte Lambert-93, affiche ce total et l'écrit dans
`summary.json` (`available`, `available_is_exact`). Le total n'est une borne
basse (`+`) que si la profondeur maximale est atteinte, et un avertissement le
précise. Chaque page brute est écrite dans `pages/`.

`--image-max` limite ensuite le nombre de photos à télécharger ou réutiliser,
sans réduire le comptage : par exemple, `--image-max 400` affiche le total
disponible dans la bbox et en traite au plus 400.



### Robustesse SfM

La perte Huber reste robuste ; le seuil de reprojection et l'angle plus stricts
visent les appariements incohérents et les profondeurs à faible parallaxe.
Ils ne garantissent pas l'absence de parasites et peuvent réduire la couverture,
éliminer des bâtiments lointains valides ou fragmenter la reconstruction.

Ces paramètres agissent pendant le calcul, pas sur un modèle déjà terminé.
Pour les appliquer à un ancien SfM, choisir un nouvel `OUTPUT_DIR` ; `TRAIN_CLEAN`
ne recalcule que les splats. Les nouveaux workspaces enregistrent la politique
dans `sfm_geometry_policy.json` et refusent les changements incompatibles avant
de réutiliser le SfM ou les blocs. Un ancien SfM sans signature est conservé
avec un avertissement explicite : ses contrôles ne sont pas connus.
Un nouveau calcul via le worker revérifie les matches (`--reuse-matches redo`).



### Périmètre

« Dans la bbox » signifie **position de prise de vue dans la bbox**, pas contenu
visible dans la photo. Toutes les photos retournées sont candidates, panoramiques
ou non, sans restriction d'auteur ou de séquence. Le script ne télécharge pas les
photos extérieures de séquences qui traversent la zone.

La qualité HD est l'asset public `hd` annoncé par l'API ; elle ne garantit pas
l'accès à l'original brut non flouté. Aucun remplacement silencieux par une qualité
inférieure n'est effectué si l'asset demandé manque.
