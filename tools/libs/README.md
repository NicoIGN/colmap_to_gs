# Composants partagés et pipeline images locales

[Retour à la documentation générale](../../doc/guide_utilisateur.md)

**Bibliothèques.** `scripts/` (workers, configuration, post-traitement, Slurm), `python/` (conversions, politiques de masques, contrôle géométrique), `config.default.sh` (défauts du pipeline images), `common.sh` (fonctions partagées), `launch.sh` / `launch.slurm` (lancement Bash et Slurm).

## 1. Objectif

Pipeline configurable pour produire un Gaussian Splat `.splat` à partir d’images,
avec [Spirula](../../install/spirula/README.md) ou Nerfstudio/Splatfacto :

```text
Images
  → sélection → [masques] → SfM global [GPS EXIF ou GPX prioritaire]
  → découpage COLMAP → entraînement par blocs
  → export par bloc (filtre 3σ, export_ply) → fusion → fichier .splat
```

Les [masques](../masks/README.md) et le
[recalage sur une trajectoire GPS](../prepare/README.md) sont optionnels.

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

- Configuration : créer un fichier personnel qui ne contient que les surcharges (chemins,
  ressources). Les défauts sont dans [`config/defaults/`](../../config/defaults/) et
  [`config.default.sh`](config.default.sh).
- `MASKS_ACCEPT_LICENSE=true` : accepte les conditions SAM 3 après les avoir
  lues.
- Environnement Python : activer l'environnement habituel (par exemple
  `mamba activate gsplat`).
- [Spirula](../../install/spirula/README.md) : doit disposer du SfM et de l'entraînement ; SAM/Vulkan est
  nécessaire si un backend de masques `spirula` est activé.
- Dépendances réutilisées : notamment numpy, scipy, tqdm, Pillow et plyfile.

Les valeurs possibles, défauts et exemples des réglages avancés sont documentés
dans [`config/defaults/`](../../config/defaults/), organisé par phase, et dans
[`config.default.sh`](config.default.sh) pour les différences propres à ce pipeline.

Depuis la racine du dépôt :

```bash
# Exécution directe dans le shell : tous les blocs s'exécutent successivement.
bash scripts/images_to_3dgs.sh /chemin/config.sh

# Slurm : ressources SLURM_* de la configuration, un GPU par bloc actif.
bash scripts/launch_images_to_3dgs.sh /chemin/config.sh

# Slurm : recalculer seulement le SfM et les blocs COLMAP.
bash scripts/launch_images_to_3dgs.sh /chemin/config.sh --prepare-only --wait
```

Les ressources, le parallélisme, l'environnement des nœuds et le suivi des jobs
sont décrits dans le [guide de lancement Bash / Slurm](../../doc/lancement_bash_slurm.md).

### Vidéo sur Slurm

La configuration, les commandes et les options (`VIDEO`, `NUM_FRAMES`,
`--wait`, `--prepare-only`) sont décrites dans
[l'exemple vidéo](../../scripts/README.md#vidéo--images--3dgs) et le
[guide Bash / Slurm](../../doc/lancement_bash_slurm.md).

`scripts/launch_videos_to_3dgs.sh` effectue deux étapes : extraction CPU des
frames dans un job Slurm, puis appel du lanceur `scripts/launch_images_to_3dgs.sh`
après réussite de l'extraction. Le terminal attend la fin de l'extraction avant
de soumettre les jobs images. Les ressources `SLURM_*` et `SLURM_ENV_SETUP`
s'appliquent aussi à l'extraction. La sélection conserve la couverture
temporelle et utilise la pondération de netteté par défaut de l'extracteur (0,7).
Les frames existantes ne sont pas écrasées : utiliser un nouveau dossier
pour réextraire, ou appeler directement `launch_images_to_3dgs.sh` pour
reprendre à partir des frames déjà extraites.
Les journaux d'extraction sont dans `OUTPUT_DIR/logs/video-frames-%j.out`
et `.err`.

### Préparation COLMAP

L'[outil images vers COLMAP](../sfm/README.md)
est appelé avant le découpage. `COLMAP_METHOD` choisit `spirula` (défaut),
`colmap`, `nerfstudio` (`ns-process-data`), `orientations` (poses + LiDAR) ou `hloc`
(SfM Hierarchical-Localization, `HLOC_*`).
Il peut être lancé seul ; les résultats compatibles sont réutilisés.

### Panoramas 360, entraînement et backends

Les panoramas 360 (HLOC, `HLOC_CAMERA_MODEL=EQUIRECTANGULAR`), le choix du moteur
(`TRAIN_BACKEND`), les corrections photométriques Spirula et les paramètres par
backend sont décrits une seule fois dans le [README Panoramax](../panoramax/README.md),
commun aux deux pipelines : voir [Panoramas 360 avec HLOC/pycolmap](../panoramax/README.md#panoramas-360-avec-hlocpycolmap),
[Entraînement Spirula ou Nerfstudio](../panoramax/README.md#entraînement-spirula-ou-nerfstudio)
et [Corrections photométriques Spirula](../panoramax/README.md#corrections-photométriques-spirula).

### Exemple Nerfstudio

Le fichier
[`param.nerfstudio.default.sh`](param.nerfstudio.default.sh) montre une
configuration avec masques sémantiques pour le SfM et l'entraînement.
Les modèles ADE20K/Mapillary ont le label `sky`, pas de label `cloud` distinct.
Les masques SfM peuvent aussi garder une politique indépendante.

### Exemples de lancement

Exemple d’invocation :

```bash
bash scripts/images_to_3dgs.sh /chemin/config.sh
```

Depuis des vidéos, `scripts/videos_to_3dgs.sh` extrait `--num-frames` images nettes et réparties sur la durée (un sous-dossier par vidéo si plusieurs), puis lance ce pipeline :

```bash
bash scripts/videos_to_3dgs.sh /chemin/config.sh --video a.mp4 --video b.mp4 --num-frames 200 [--width 1920 --height 1080] [--frames-dir DOSSIER] [--phase prepare]
```

Les images sont écrites dans `--frames-dir` (par défaut `frames_3dgs` à côté de la première vidéo) et régénérées à chaque appel.

### Masques et budgets d'entraînement

Les masques (`SFM_MASKS_*` / `TRAIN_MASKS_*`, partage, signature et labels) et
les budgets d'entraînement (`TRAIN_SP_CAP_MAX`, croissance, arrêt) sont décrits
une seule fois dans le [README Panoramax](../panoramax/README.md), commun aux
deux pipelines : voir [Masques avant SfM et partage avec l'entraînement](../panoramax/README.md#masques-avant-sfm-et-partage-avec-lentraînement)
et [Budgets d'entraînement](../panoramax/README.md#budgets-dentraînement).

### Protection des résultats et reprise

La signature de préparation permet de vérifier que les résultats réutilisés
correspondent aux images et aux réglages choisis.

- Signature de préparation : enregistre les politiques SfM et entraînement ; ne pas
  effacer `inputs.json`.
- Reprise explicite : permet d'adopter certaines signatures anciennes ; une
  politique connue incompatible reste refusée.

Le SfM est **global**. L'ordre est : reconstruction globale, géoréférencement
éventuel, puis découpage en sous-modèles COLMAP et entraînement par bloc.
`SPLIT_NUM_BLOCKS` et `SPLIT_OVERLAP_MAX_FRACTION` ne font donc plus partie de
la signature SfM ; les anciennes signatures sont migrées automatiquement.
Modifier ces valeurs puis relancer `all` ou `prepare` conserve les images,
masques, le SfM et le géoréférencement validés. Les anciens blocs, entraînements
et exports sont déplacés dans `output/partition-backups/<horodatage>/` avant
le nouveau découpage, sans suppression. Un worker lancé directement avec
`train-block` ou `merge` refuse un découpage incompatible et demande `prepare`.

### Reconstruction des caméras et des points 3D

Le SfM estime les positions des caméras et un nuage de points 3D à partir des
images.

- Images SfM : photos individuelles, sans filtrage des paires GPS.
- Avec Spirula, `SFM_SP_PAIRS=sequential` traite les images comme une séquence
  ordonnée par leur chemin et requiert `SFM_CAMERA_SPLIT=false`. Les images
  doivent être nommées dans l'ordre de prise de vue ; les dossiers issus de la
  séparation des caméras ne conservent pas cet ordre global. La fermeture de
  boucle dépend de la version et des réglages de Spirula.
- Caméra : une caméra par sous-dossier par défaut ; vérifier que les images
  d'un même dossier partagent bien les mêmes caractéristiques optiques.
  `SFM_CAMERA_SPLIT=true` relance le SfM avec une caméra par modèle quand
  Spirula en produit plusieurs d'au moins `SFM_CAMERA_SPLIT_MIN_IMAGES` images
  (changement d'objectif dans une vidéo, par exemple).
- `SFM_*` : reconstruction et contrôles géométriques ; valeurs et unités dans
  [`config/defaults/sfm.default.sh`](../../config/defaults/sfm.default.sh).
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
  [`config/defaults/export.default.sh`](../../config/defaults/export.default.sh).
- Nombre final : peut être inférieur à la somme des budgets des blocs.
- Détails de l'algorithme : voir le [README du post-traitement](../export/README.md).

### Recalage sur une trajectoire GPS

Le recalage utilise une trajectoire GPS fournie au format GPX. Pour l'activer,
renseigner `GEOREF_GPX_FILE` dans le fichier de configuration :

```bash
GEOREF_GPX_FILE="/chemin/absolu/parcours.gpx"
GEOREF_VIEW_PREFIX="h00_y0"  # vide : sélection automatique du dossier
GEOREF_EPSG="2154"
GEOREF_ALLOW_UNCERTAIN="false"
```

Si `GEOREF_GPX_FILE` est absent ou vide, les coordonnées EXIF GPS sont utilisées
automatiquement lorsqu'au moins deux images en possèdent ; fiona et pyproj ne
sont alors pas nécessaires.

Le pipeline réutilise `tools/georef/georef_colmap_from_gpx.py` : il recale
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
`georef/0/georef_stats.json` et trajectoire caméra GPX. Le GeoPackage
`georef/georef_trajectories.gpkg` pour contrôle dans QGIS n'est produit qu'avec
`--gpkg` (désactivé par défaut).
Un recalage ambigu ou d'erreur normalisée > 0.08 arrête la préparation avant
le découpage. Après contrôle, `GEOREF_ALLOW_UNCERTAIN=true` permet d'accepter
explicitement cette solution sans refaire le SfM ; les avertissements restent
affichés. **Tout recalage demande une validation visuelle**, même sans alerte.

Le résultat final est accompagné de `<nom-du-splat>.georef.json`, compatible
avec `tools/export/export_3dtiles.py --georef-summary`, sans appliquer une
seconde transformation aux coordonnées. Un `.splat` seul n'embarque pas de CRS :
conserver le fichier de référence avec lui.

Les pipelines `images_to_3dgs` et `panoramax_to_3dgs` écrivent aussi
`<nom-du-splat>.metadata.json` (GeoJSON, `tools/libs/python/splat_metadata.py`) :
géoréférencement (`output_reference`, présent seulement si le recalage existe),
CRS, qualité du recalage, avertissements et origine. Les images ne sont pas listées.
Le viewer l'applique comme géoréférencement quand il est déposé avec le splat.

GPX activé : fiona et pyproj sont requis par l'outil existant. Les modèles
COLMAP contenant `rigs.*` ou `frames.*` sont refusés explicitement par celui-ci.
Une sortie GPX interrompue sans rapport doit être sauvegardée/déplacée avant
de relancer la préparation ; le SfM brut n'a pas à être recalculé.
Modifier/ajouter/retirer le GPX après préparation exige un **nouvel OUTPUT_DIR** :
ne pas mélanger des blocs entraînés dans des repères différents.

Les coordonnées EXIF utilisées sont inscrites dans `inputs.json` ; modifier les
métadonnées GPS impose donc un nouvel `OUTPUT_DIR`.

## 4. Fonctionnement interne

Le pipeline **Images locales vers Gaussian Splats** prépare les photos,
reconstruit la scène et entraîne les blocs. Il partage avec le pipeline
Panoramax plusieurs outils pour effectuer les mêmes étapes de façon cohérente :

- `tools/masks/masks.py` : interface commune des masques (entrée dossier, SAM via Spirula ou segmenteur) ; les pipelines l'appellent.
- `tools/libs/scripts/pipeline_config.sh` : vérifie que la configuration du calcul est valide.
- `tools/sfm/sfm_geometry_policy.py` : vérifie qu'une reconstruction existante
  convient aux réglages avant de la réutiliser.
- `tools/export/export.sh` : fusionne les splats exportés par bloc.
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

### Filtrage optionnel du sparse

```bash
SFM_FILTER_DENSITY=true  # false par défaut ; local, Slurm et Panoramax (même étape partagée : tools/sfm/sparse_density.sh)
SFM_DENSITY_VOTE_FRACTION=0.75  # ]0,1[ ; baisser = plus strict
SFM_DENSITY_FAR_SUPPORT_RATE=0.25  # ]0,1] ; monter = plus strict
```

Le filtre expérimental de [`tools/sfm/filter_sparse_density.py`](../sfm/README.md) s'exécute
sur CPU après le SfM et le recalage GPX éventuel, avant le découpage. Il utilise
ces seuils et écrit deux modèles COLMAP binaires : `sparse_clean`
(points conservés) et `sparse_rejected` (points retirés), chacun avec son
rapport. Aucun PLY n'est produit ; les originaux restent intacts.
Les blocs utilisent tous ce sparse filtré.
Les étapes sont journalisées en mode verbose, sans barres interactives en batch.
Un résultat terminé et valide est réutilisé à la reprise.

Activer avant la préparation et vérifier visuellement les points rejetés :
ce filtre reste expérimental. Changer ce réglage après découpage/entraînement
nécessite un nouvel `OUTPUT_DIR` ; `TRAIN_CLEAN` ne redécoupe pas les blocs.
Le défaut `false` reste compatible avec les signatures antérieures.

### Réutilisation du workspace

Le pipeline conserve `inputs.json` (sélection, tailles/dates, paramètres SfM,
masques et découpage). Une modification incompatible est refusée : choisir un
nouvel `OUTPUT_DIR`, sans mélanger plusieurs reconstructions.
Ne pas modifier les images/configurations pendant des jobs actifs.
Une préparation compatible reprend les masques et réutilise le SfM/découpage.
Les entraînements terminés sont réutilisés par défaut ; `TRAIN_CLEAN=true`
recalcule le 3DGS uniquement. La reprise des blocs partiels est décrite dans
« Masques terminés, SfM interrompu ».
L'exemple ne supprime jamais les images source.

### Masques terminés, SfM interrompu

Pour reprendre un workspace partiel, activer explicitement dans la configuration :

```bash
PIPELINE_RESUME=true
REMOVE_LOCKS=false
```

Puis relancer la même commande, après avoir arrêté les anciens jobs :

```bash
bash "$GIT_ROOT/tools/libs/launch.sh" \
  /shared/home/NBellaiche/data/Paris9/config.sh
```

Les verrous d'étape enregistrent l'hôte, le PID et l'identité du processus.
`PIPELINE_RESUME=true` récupère automatiquement un verrou local abandonné,
mais refuse un propriétaire encore actif, un enfant natif orphelin actif ou
un verrou d'un autre hôte. Les anciens verrous vides sans propriétaire sont
vérifiés via les processus locaux avant récupération. Un contenu inconnu
dans un verrou n'est jamais supprimé.

`REMOVE_LOCKS=true` n'est autorisé qu'avec `PIPELINE_RESUME=true` ; sinon la
configuration est refusée avant toute suppression. Il force les verrous rencontrés, avec un
avertissement, même si le propriétaire paraît actif ou distant. **À utiliser
seulement après avoir arrêté tous les anciens jobs** : un verrou n'arrête
pas le calcul qui le détenait et deux calculs peuvent corrompre leurs sorties.
Remettre `REMOVE_LOCKS=false` après cette récupération.

Ce mode ne désactive pas les validations : il vérifie pour **chaque image**
le masque attendu (`<chemin relatif de l'image>.png`), son décodage PNG,
ses dimensions brutes, son mode monochrome et ses valeurs 0/255. Un dossier
vide (par exemple après un refus de licence SAM) reprend automatiquement la
génération. Un rapport `masks.json` partiel compatible permet également de
reprendre les masques manquants : tous les PNG déjà présents sont validés
avant de relancer le générateur, qui réutilise ses résultats enregistrés.
Cela nécessite l'absence de résultats SfM/recalage/découpage/entraînement :
des masques susceptibles d'avoir déjà été utilisés ne sont pas modifiés.
Les erreurs de licence ou de génération restent bloquantes.

Un dossier non vide sans rapport, un rapport complet avec des fichiers manquants,
des PNG corrompus ou des masques externes incomplets sont refusés, sans lancer
le SfM. Les masques complets sont réutilisés sans relancer SAM, même si leur
rapport est absent/interrompu ; les paramètres présents dans ce rapport
doivent rester compatibles. Si le rapport est absent, un `masks.json` de
validation est créé pour les workers d'entraînement, avec une provenance
explicitement inconnue (pas une attribution rétroactive au modèle configuré).

Le sparse SfM est lu intégralement : caméras, images, points et références
observations/tracks doivent être cohérents. Si le sparse est absent ou invalide,
le sparse incomplet est archivé et le pipeline relance `sfm auto` dans le même
workspace, avec features et matches en mode `auto` pour réutiliser les caches.
Sa nouvelle sortie est validée avant le découpage.
Il ne reprend pas une optimisation SfM à l'itération interrompue.
Les erreurs Spirula continuent d'arrêter le job.

Un géoréférencement ou un découpage interrompu est conservé dans une archive
locale de dossier puis régénéré depuis le modèle global, sans refaire le SfM.
Un checkpoint Spirula complet (`config.json`, `state.tar` avec état de reprise)
est transmis à `spirula train --resume` ; cela exige une version native qui
supporte cette option. Le checkpoint incomplet le plus récent est ignoré
avec diagnostic au profit du dernier checkpoint complet. Sans état reprenable,
les fichiers du bloc sont conservés dans `interrupted-*/splats`, puis ce bloc
recommence. Les blocs dont le checkpoint final existe ne sont pas réentraînés ;
un export interrompu peut ainsi repartir du checkpoint final.

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
bash scripts/images_to_3dgs.sh /chemin/config.sh --phase prepare
bash scripts/images_to_3dgs.sh /chemin/config.sh --phase train-block --block 0
bash scripts/images_to_3dgs.sh /chemin/config.sh --phase merge
```

Pour refaire une phase sans nettoyer à la main, ajouter `--redo` à `train-block`
(avec `--block N` ou sans, pour tous les blocs) ou à `merge` :

```bash
bash scripts/images_to_3dgs.sh /chemin/config.sh --phase merge --redo
bash scripts/images_to_3dgs.sh /chemin/config.sh --phase train-block --block 1 --redo
```

Les entrées de fusion (brut et `.export_done` de chaque bloc) sont vérifiées avant tout
nettoyage. `--redo merge` supprime le splat final, son `.metadata.json` et son `.georef.json`.
`--redo train-block` appelle
`--clean` sur le ou les blocs (`splats/`, `.train_done`, `.export_done`) et supprime
en plus les sorties de fusion. Le SfM n'est jamais touché. Sans `--redo`, la fusion
est toujours recalculée et un export dont la commande a changé est relancé automatiquement.

La fusion seule exige tous les blocs terminés. Elle fusionne les splats exportés par bloc
(`gs/block_NNN/splats/block_NNN.splat`) sans recadrage supplémentaire. Le nettoyage est fait
à l'export par `tools/export/export_ply.py` : il retire les splats dont le support 3σ
dépasse de la bbox des points 3D COLMAP (`POSTPROCESS_FILTER_MODE`, `apply` ou `diagnostic`).
Les diagnostics sont conservés à côté du splat par bloc.
La fusion écrit `OUTPUT_DIR/EXPORT_SPLAT_NAME`, sans réentraîner.
