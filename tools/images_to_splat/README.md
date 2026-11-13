# Images locales vers Gaussian Splats

[Retour à la documentation générale](../../doc/guide_utilisateur.md)

## 1. Objectif

Pipeline configurable pour produire un Gaussian Splat `.splat` à partir d’images,
avec [Spirula](../spirula/README.md) ou Nerfstudio/Splatfacto :

```text
Images
  → sélection → [masques] → SfM global [GPS EXIF ou GPX prioritaire]
  → découpage COLMAP → entraînement par blocs
  → recadrage bbox → fusion → fichier .splat
```

Les [masques](../segmentation_masks/README.md) et le
[recalage sur une trajectoire GPS](../preprocess/README.md) sont optionnels.

**Préparation.** L'[outil images vers COLMAP](../images_to_colmap/README.md)
est appelé avant le découpage. `COLMAP_METHOD` choisit `spirula` (défaut),
`colmap`, `nerfstudio` (`ns-process-data`) ou `orientations` (poses + LiDAR).
Il peut être lancé seul ; les résultats compatibles sont réutilisés.

**Entraînement.** `TRAIN_BACKEND=spirula|nerfstudio` choisit le moteur sans
changer le modèle COLMAP. Nerfstudio exige PyTorch et un GPU NVIDIA utilisable
via CUDA ; sinon le calcul est refusé avant l'entraînement, sans repli CPU.
Les résultats sont séparés dans `gs/` et `gs-nerfstudio/`.
Le recadrage et la fusion utilisent des exports dans les coordonnées COLMAP
originales, après inversion de la transformation de chaque entraînement.

**Corrections photométriques Spirula.** `SFM_SP_IMAGE_EXPOSURE` ajuste
l'exposition utilisée pendant l'analyse SfM ; ce réglage n'agit pas sur
l'entraînement. Les paramètres `TRAIN_SP_*` pilotent la correction PPISP et la
grille bilatérale pendant l'entraînement. `no_crf` inclut la correction du
vignettage, contrairement à `no_crf_no_vig`. `TRAIN_SP_PPISP_EXPOSURE_FROM_EXIF`
peut initialiser la correction depuis les balises EXIF disponibles. Ces options
ne sont pas transmises à Nerfstudio. Les valeurs vides conservent les défauts
de Spirula installée ; voir [`config.sh.example`](config.sh.example).

**Paramètres par backend.** Le fichier d'exemple regroupe les réglages SfM
communs (`SFM_MAX_*`, seuils géométriques et threads), puis les options
`SFM_SP_*`, `SFM_COLMAP_*` et `SFM_NS_*` selon le producteur sélectionné par
`COLMAP_METHOD`. Les réglages `TRAIN_SP_*` ne s'appliquent qu'à
`TRAIN_BACKEND=spirula` ; `TRAIN_NS_*` ne s'appliquent qu'à
`TRAIN_BACKEND=nerfstudio`. `TRAIN_NUM_ITER` et `TRAIN_CLEAN` sont communs.
Les options SfM `SFM_SP_*` non applicables à la méthode sélectionnée sont
refusées ; les options d'entraînement `TRAIN_SP_*` n'affectent pas Nerfstudio
et ne lui sont pas transmises.

**Masques Nerfstudio.** Ils sont générés par
[`tools/segmentation_masks`](../segmentation_masks/README.md), avec
**Mask2Former ou SegFormer** (`TRAIN_MASKS_BACKEND=segmentation` et
`TRAIN_MASKS_MODEL` pointant vers le modèle local). Définir explicitement
`TRAIN_MASKS_MODE=ignore`. Le worker renseigne `mask_path` dans chaque vue
du `transforms.json` transmis à Nerfstudio.
Le mode `empty` de Spirula n'est pas transposé. Les paramètres Spirula de cap,
qualité, croissance, flottants et distractions ne sont pas appliqués à Splatfacto ;
`TRAIN_NUM_ITER` reste commun. `TRAIN_NS_DOWNSCALE_FACTOR` règle la réduction des images.
`TRAIN_NS_TRAIN_BIN` et `TRAIN_NS_EXPORT_BIN` choisissent les exécutables de l'environnement
correspondant à `PYTHON_BIN`. `TRAIN_CLEAN=true` recalcule ce moteur uniquement.

**Exemple Nerfstudio.** Le fichier
[`config.nerfstudio.sh.example`](config.nerfstudio.sh.example) montre une
configuration avec masques sémantiques pour le SfM et l'entraînement.
Les modèles ADE20K/Mapillary ont le label `sky`, pas de label `cloud` distinct.
Les masques SfM peuvent aussi garder une politique indépendante.

Exemple d’invocation :

```bash
bash script/images_to_3dgs.sh /chemin/config.sh
```

## 2. Entrées et sorties

- `SFM_IMAGES_DIR` : dossier source des images ; l'option
  `--images /chemin/absolu/images` peut remplacer cette valeur à l'appel.
- `SFM_IMAGES_RECURSIVE=true` : parcourt les sous-dossiers ordinaires.
- `GEOREF_GPX_FILE` : fichier GPX facultatif ; s'il est renseigné, il est
  utilisé à la place du GPS EXIF pour géoréférencer le résultat.
- Liens : les liens vers des fichiers sont acceptés, mais pas le parcours des
  dossiers symboliques.
- Noms relatifs : préservés ; `h00_y0/photo.jpg` et `h01_y45/photo.jpg`
  restent distincts.
- Workspace : doit être extérieur au dossier source ; son dossier `images/`
  contient uniquement des liens symboliques vers la sélection.

## 3. Configuration et utilisation

- Configuration : copier [`config.sh.example`](config.sh.example) vers un fichier personnel et
  régler les chemins et les ressources.
- `MASKS_ACCEPT_LICENSE=true` : accepte les conditions SAM 3 après les avoir
  lues.
- Environnement Python : activer l'environnement habituel (par exemple
  `mamba activate gsplat`).
- [Spirula](../spirula/README.md) : doit disposer du SfM et de l'entraînement ; SAM/Vulkan est
  nécessaire si un backend de masques `spirula` est activé.
- Dépendances réutilisées : notamment numpy, scipy, tqdm, Pillow et plyfile.

Les valeurs possibles, défauts et exemples des réglages avancés sont documentés
dans [`config.sh.example`](config.sh.example), organisé par étape du pipeline.

Depuis la racine du dépôt :

```bash
# Exécution directe dans le shell : tous les blocs s'exécutent successivement.
bash script/images_to_3dgs.sh /chemin/config.sh

# Slurm : ressources SLURM_* de la configuration, un GPU par bloc actif.
bash script/launch_images_to_3dgs.sh /chemin/config.sh

# Slurm : recalculer seulement le SfM et les blocs COLMAP.
bash script/launch_images_to_3dgs.sh /chemin/config.sh --prepare-only --wait
```

Les ressources, le parallélisme, l'environnement des nœuds et le suivi des jobs
sont décrits dans le [guide de lancement Bash / Slurm](../../doc/lancement_bash_slurm.md).

### Masques

Les masques indiquent les zones d'image à exclure de la reconstruction ou à
traiter séparément pendant l'entraînement.

- `SFM_MASKS_*` / `TRAIN_MASKS_*` : masques pour l'extraction / l'entraînement ;
  réglages et héritage dans [`config.sh.example`](config.sh.example),
  section « 1. Masques SfM / train ». Le fonctionnement des moteurs est décrit
  dans le [README des masques](../segmentation_masks/README.md).
- `SFM_MASKS_DIR` / `TRAIN_MASKS_DIR` : dossier optionnel de masques déjà
  calculés ; chaque image doit avoir son PNG monochrome binaire de mêmes
  dimensions. Le pipeline vérifie les fichiers, crée un lien dans le workspace
  et ne charge pas de modèle ni ne relance la segmentation.
  Ne pas modifier ces dossiers pendant le calcul : la signature contrôle taille,
  date et SHA256 de chaque masque. En cas de divergence, le diagnostic indique
  l'image et le champ modifié.
- Partage des masques : si les politiques effectives sont identiques, les
  masques sont calculés une seule fois dans `masks/` ; sinon chaque étape activée
  reçoit son propre dossier `feature_masks/` ou `train_masks/`.
- En Slurm, si les deux politiques sont activées et différentes, deux jobs
  GPU indépendants calculent les masques simultanément lorsque deux GPU sont
  disponibles. La préparation SfM attend les deux ; avec moins de ressources,
  Slurm les exécute à la suite. Les masques complets déjà présents sont réutilisés.
- Fichiers de masque : les chemins relatifs des images sont conservés, avec le
  suffixe `.png`.
- Labels `sky,cloud` : conservent les voitures et passants dans les images
  d'entraînement, sans garantir la reconstruction des objets mobiles.
- Si seuls les paramètres SfM sont définis, renseigner également ceux du train
  lorsque `TRAIN_MASKS=on`.

### Budgets d'entraînement

Les budgets fixent la durée de l'entraînement en itérations et le nombre maximal
de splats pour chaque bloc.

- `TRAIN_*` : budgets, croissance et arrêt de l'entraînement ; voir
  [`config.sh.example`](config.sh.example), section « 4. Entrainement de chaque bloc ».
  Les budgets s'appliquent **par bloc**.
- Cap : maximum ; ni le nombre final ni la qualité ne sont garantis.
- `TRAIN_SP_GROWTH_FACTOR` vide : le worker existant estime le facteur en supposant
  une initialisation à 10 % du cap ; un nuage sparse plus fourni peut faire
  atteindre le cap plus tôt.
### Protection des résultats et reprise

La signature de préparation permet de vérifier que les résultats réutilisés
correspondent aux images et aux réglages choisis.

- Signature de préparation : enregistre les politiques SfM et entraînement ; ne pas
  effacer `inputs.json`.
- Reprise explicite : permet d'adopter certaines signatures anciennes ; une
  politique connue incompatible reste refusée.

### Reconstruction des caméras et des points 3D

Le SfM estime les positions des caméras et un nuage de points 3D à partir des
images.

- Images SfM : photos individuelles, sans filtrage des paires GPS.
- Caméra : une caméra par sous-dossier par défaut ; vérifier que les images
  d'un même dossier partagent bien les mêmes caractéristiques optiques.
- `SFM_*` : reconstruction et contrôles géométriques ; valeurs et unités dans
  [`config.sh.example`](config.sh.example), section « 2. Reconstruction SfM globale ».
  Le seuil d'angle cible les profondeurs mal contraintes ; augmenter ces exigences peut aussi éliminer
  des bâtiments lointains valides et fragmenter la reconstruction.
- Avec `COLMAP_METHOD=spirula`, sans GPX, Spirula utilise les coordonnées GPS EXIF valides
  d'au moins deux images. Le mode est `full` si toutes les images géolocalisées
  ont une altitude, sinon `horizontal` ; les paires d'images proches sont alors
  ajoutées dans un rayon de 30 m. Si un GPX est configuré, l'EXIF est ignoré.
- Ces réglages ne garantissent pas l'absence de faux points et ne filtrent pas
  un modèle déjà calculé. Leur modification exige un nouvel `OUTPUT_DIR` ;
  `TRAIN_CLEAN` ne recalcule pas le SfM.
- Les matches sont revérifiés lors d'un nouveau calcul SfM, pour ne pas hériter
  d'anciens seuils.

### Coordonnées et alignement des blocs

Les blocs utilisent un même système de coordonnées pour pouvoir être fusionnés
sans déplacer ni redimensionner leurs reconstructions.

- Sans GPX ni GPS EXIF exploitable, l'alignement géographique et l'échelle
  métrique ne sont pas garantis. Avec un GPS EXIF, Spirula construit un repère
  local métrique à partir des coordonnées des images ; si l'altitude manque sur
  au moins une image géolocalisée, le recalage est horizontal seulement.
- Découpage : conserve les poses caméra et les coordonnées des points ;
  sélectionne les observations sans recentrer ni redimensionner les sous-modèles.
- Export `block_NNN.splat` : inverse la normalisation interne de chaque
  entraînement, enregistrée dans `scene_transform.json`, pour les centres,
  tailles et rotations des Gaussians.
- Checkpoints PLY bruts : ne pas les mélanger directement aux splats exportés,
  car leurs coordonnées peuvent être normalisées.
- Frontières entre blocs : les optimisations indépendantes peuvent produire
  des écarts de reconstruction, même avec des coordonnées cohérentes.


### Fusion et export

La fusion rassemble les blocs déjà alignés en un seul fichier `.splat` et réduit
les doublons dans leurs zones de recouvrement, sans modifier leurs coordonnées.

- Sélection : l'espace est découpé en petites cellules (voxels) ; dans chacune,
  les splats du bloc au meilleur score moyen sont conservés.
- `MERGE_STRENGTH` : règle la taille des cellules et donc l'intensité de la
  fusion ; `EXPORT_SPLAT_NAME` définit le nom du fichier final. Voir
  [`config.sh.example`](config.sh.example), section « 6. Fusion et export ».
- Nombre final : peut être inférieur à la somme des budgets des blocs.
- Détails de l'algorithme : voir le [README du post-traitement](../postprocess/README.md).

### Recalage sur une trajectoire GPS

Le recalage utilise une trajectoire GPS fournie au format GPX. Pour l'activer,
renseigner `GEOREF_GPX_FILE` dans le fichier de configuration :

# Refaire seulement le SfM et le découpage sur Slurm :
bash script/launch_images_to_3dgs.sh /chemin/config.sh --prepare-only --wait
```bash
GEOREF_GPX_FILE="/chemin/absolu/parcours.gpx"
GEOREF_VIEW_PREFIX="h00_y0"  # vide : sélection automatique du dossier
GEOREF_EPSG="2154"
GEOREF_ALLOW_UNCERTAIN="false"
```

Si `GEOREF_GPX_FILE` est absent ou vide, les coordonnées EXIF GPS sont utilisées
automatiquement lorsqu'au moins deux images en possèdent ; fiona et pyproj ne
sont alors pas nécessaires.

Le pipeline réutilise `tools/preprocess/georef_colmap_from_gpx.py` : il recale
la trajectoire caméra sur un sous-parcours GPX par similitude 3D, sans FPS,
timestamps ni offset temporel. Les images doivent porter des indices `_fXXXXX`
et fournir au moins six frames distinctes dans un dossier exploitable.
Les parcours doivent avoir une géométrie suffisamment caractéristique ; des
segments droits, répétitifs ou fortement déformés peuvent donner un recalage
ambigu ou incorrect. Ce n'est pas une garantie de précision cartographique.

Le recalage précède le découpage et l'entraînement. Le modèle brut
`sfm/sparse/0` est conservé ; `georef/0` devient la source commune du découpage
et du recadrage bbox. Le résultat est en **ENU local métrique**
(Est, Nord, Haut), dont l'origine WGS84 est dans les rapports.
`GEOREF_EPSG` définit seulement la référence projetée de cette origine.
Les altitudes GPX sont utilisées sans correction géoïde.

Sorties complémentaires : `georef/source.gpx`, `georef/georef_summary.json`,
`georef/0/georef_stats.json`, trajectoire caméra GPX et
`georef/georef_trajectories.gpkg` pour contrôle dans QGIS.
Un recalage ambigu ou d'erreur normalisée > 0.08 arrête la préparation avant
le découpage. Après contrôle, `GEOREF_ALLOW_UNCERTAIN=true` permet d'accepter
explicitement cette solution sans refaire le SfM ; les avertissements restent
affichés. **Tout recalage demande une validation visuelle**, même sans alerte.

Le résultat final est accompagné de `<nom-du-splat>.georef.json`, compatible
avec `tools/splat_to_3dtiles/convert.py --georef-summary`, sans appliquer une
seconde transformation aux coordonnées. Un `.splat` seul n'embarque pas de CRS :
conserver le fichier de référence avec lui.

GPX activé : fiona et pyproj sont requis par l'outil existant. Les modèles
COLMAP contenant `rigs.*` ou `frames.*` sont refusés explicitement par celui-ci.
Une sortie GPX interrompue sans rapport doit être sauvegardée/déplacée avant
de relancer la préparation ; le SfM brut n'a pas à être recalculé.
Modifier/ajouter/retirer le GPX après préparation exige un **nouvel OUTPUT_DIR** :
ne pas mélanger des blocs entraînés dans des repères différents.

Sans GPX, le GPS EXIF des images locales peut fournir le repère métrique
directement pendant le SfM, comme dans le pipeline Panoramax. La détection et
l'empreinte des coordonnées utilisées sont inscrites dans `inputs.json` ;
modifier les métadonnées GPS impose donc un nouvel `OUTPUT_DIR`. Un GPX
renseigné reste prioritaire et désactive entièrement cette voie EXIF.

## 4. Fonctionnement interne

Le pipeline **Images locales vers Gaussian Splats** prépare les photos,
reconstruit la scène et entraîne les blocs. Il partage avec le pipeline
Panoramax plusieurs outils pour effectuer les mêmes étapes de façon cohérente :

- `tools/libs/python/mask_policies.py` : prépare les réglages de masques et leur génération.
- `tools/libs/scripts/pipeline_config.sh` : vérifie que la configuration du calcul est valide.
- `tools/libs/python/sfm_geometry_policy.py` : vérifie qu'une reconstruction existante
  convient aux réglages avant de la réutiliser.
- `tools/libs/scripts/pipeline_postprocess.sh` : recadre et fusionne les résultats.
- `tools/libs/scripts/pipeline_slurm.sh` : soumet les étapes au cluster et gère les échecs.
- `tools/libs/scripts/` contient les scripts qui lancent Spirula pour reconstruire la scène
  et entraîner les blocs.
- `inputs.json` garde une trace des images et réglages utilisés pour préparer
  le calcul.

Les étapes qui diffèrent entre les deux pipelines sont :

- **Images locales** : sélection des fichiers, choix du producteur COLMAP,
  organisation du dossier de travail et, si demandé, recalage sur une trajectoire GPS.
- **Panoramax** : recherche et téléchargement des photos, gestion de leurs
  métadonnées et utilisation de leur position GPS.

Chaque pipeline possède sa configuration, ses chemins de sortie et ses options
(repère, seuil de recadrage, nettoyage final).

## 5. Notes et informations complémentaires

#### Filtrage optionnel du sparse

```bash
SFM_FILTER_DENSITY=true  # false par défaut ; local et Slurm
```

Le filtre expérimental de [`tools/preprocess/filter_sparse_density.py`](../preprocess/README.md) s'exécute
sur CPU après le SfM et le recalage GPX éventuel, avant le découpage. Il utilise
ses seuils par défaut et écrit deux modèles COLMAP binaires : `sparse_clean`
(points conservés) et `sparse_rejected` (points retirés), chacun avec son
rapport. Aucun PLY n'est produit ; les originaux restent intacts.
Les blocs et le recadrage bbox final utilisent tous deux ce sparse filtré.
Les étapes sont journalisées en mode verbose, sans barres interactives en batch.
Un résultat terminé et valide est réutilisé à la reprise.

Activer avant la préparation et vérifier visuellement les points rejetés :
ce filtre reste expérimental. Changer ce réglage après découpage/entraînement
nécessite un nouvel `OUTPUT_DIR` ; `TRAIN_CLEAN` ne redécoupe pas les blocs.
Le défaut `false` reste compatible avec les signatures antérieures.

#### Réutilisation du workspace

Le pipeline conserve `inputs.json` (sélection, tailles/dates, paramètres SfM,
masques et découpage). Une modification incompatible est refusée : choisir un
nouvel `OUTPUT_DIR`, sans mélanger plusieurs reconstructions.
Ne pas modifier les images/configurations pendant des jobs actifs.
Une préparation compatible reprend les masques et réutilise le SfM/découpage.
Les entraînements terminés sont réutilisés par défaut ; `TRAIN_CLEAN=true`
recalcule le 3DGS uniquement. Les sorties partielles du train nécessitent aussi
ce réglage, après sauvegarde. L'exemple ne supprime jamais les images source.

#### Masques terminés, SfM interrompu

Pour reprendre un workspace partiel, activer explicitement dans la configuration :

```bash
PIPELINE_RESUME=true
```

Puis relancer la même commande, après avoir arrêté les anciens jobs :

```bash
bash "$GIT_ROOT/tools/images_to_splat/launch.sh" \
  /shared/home/NBellaiche/data/Paris9/config.sh
```

Ce mode ne désactive pas les validations : il vérifie pour **chaque image**
le masque attendu (`<chemin relatif de l'image>.png`), son décodage PNG,
ses dimensions brutes, son mode monochrome et ses valeurs 0/255. Un dossier
existant incomplet/corrompu est refusé avec le chemin du fichier, sans lancer
le SfM. Les masques complets sont réutilisés sans relancer SAM, même si leur
rapport est absent/interrompu ; les paramètres présents dans ce rapport
doivent rester compatibles. Si le rapport est absent, un `masks.json` de
validation est créé pour les workers d'entraînement, avec une provenance
explicitement inconnue (pas une attribution rétroactive au modèle configuré).

Le sparse SfM est lu intégralement : caméras, images, points et références
observations/tracks doivent être cohérents. Si le sparse est absent ou invalide,
le pipeline relance `sfm auto` dans le même workspace (features en mode `auto`,
matches en mode `redo`) et valide sa nouvelle sortie avant le découpage.
Il ne reprend pas une optimisation SfM à l'itération interrompue.
Les erreurs Spirula continuent d'arrêter le job.

Une signature `inputs.json` absente peut être adoptée **avant** le recalage,
le découpage et l'entraînement. Une signature ancienne peut être enrichie si
tous ses paramètres enregistrés et ses images sont inchangés ; elle est
sauvegardée dans `inputs.before_resume.json`. Les changements connus d'images,
de paramètres ou de GPX restent bloquants. Sans métadonnées, la présence de
fichiers valides ne prouve pas leur provenance : conserver les mêmes images
et réglages.

En cas de différence de signature, l'erreur indique le chemin d'`inputs.json`,
la valeur effective de `PIPELINE_RESUME` et jusqu'à 12 champs différents
(valeurs enregistrées/actuelles, tailles/dates des images, ajouts ou retraits).
Une configuration Bash inchangée ne garantit pas une signature identique :
les valeurs par défaut du code, les variables d'environnement, les chemins
résolus et les dates des fichiers entrent aussi dans la comparaison.
Ne pas supprimer `inputs.json` pour contourner ce diagnostic.

```bash
bash script/images_to_3dgs.sh /chemin/config.sh --phase prepare
bash script/images_to_3dgs.sh /chemin/config.sh --phase train-block --block 0
bash script/images_to_3dgs.sh /chemin/config.sh --phase merge
```

La fusion seule exige tous les blocs terminés. Elle recadre chaque brut vers
`gs/block_NNN/splats/block_NNN.bbox.splat`, avec les points **globaux**
`sfm/sparse/0/points3D.bin` : les recouvrements entre blocs sont préservés.
Seuls les fichiers explicitement retenus sont fusionnés (pas de glob mêlant
bruts, rejets et copies recadrées).
Les diagnostics `.filter.json` et `.rejected.splat` sont conservés à côté.
La sécurité de rejet de 20 % s'applique par bloc ; examiner le diagnostic avant
de modifier `POSTPROCESS_MAX_REMOVAL_FRACTION`. Le filtre contextuel est désactivé.
La fusion écrit `OUTPUT_DIR/EXPORT_SPLAT_NAME`, sans réentraîner.
