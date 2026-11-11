# Images locales vers Gaussian Splats

## 1. Objectif

Pipeline [Spirula](../spirula/README.md) pour produire un Gaussian Splat `.splat` à partir d’images :

```text
Images
  → sélection → [masques] → SfM global → [recalage sur une trajectoire GPS]
  → découpage COLMAP → entraînement par blocs
  → recadrage bbox → fusion → fichier .splat
```

Les [masques](../segmentation_masks/README.md) et le
[recalage sur une trajectoire GPS](../preprocess/README.md) sont optionnels.

Exemple d’invocation :

```bash
bash tools/images_to_splat/run_pipeline.sh /chemin/config.sh
```

## 2. Entrées et sorties

- `SFM_IMAGES_DIR` : dossier source des images ; l'option
  `--images /chemin/absolu/images` peut remplacer cette valeur à l'appel.
- `SFM_IMAGES_RECURSIVE=true` : parcourt les sous-dossiers ordinaires.
- `GEOREF_GPX_FILE` : fichier au format GPX contenant la trajectoire GPS de
  référence pour le recalage ; facultatif, absent ou vide pour ne pas recaler.
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

Les paramètres avancés, leurs valeurs possibles et leurs valeurs d'exemple
sont commentés dans [`config.sh.example`](config.sh.example), par étape :

- `SFM_*` : section « 2. Reconstruction SfM globale ».
- `GEOREF_*` : section « 2b. Georeferencement GPX OPTIONNEL ».
- `SPLIT_*` : section « 3. Decoupage ».
- `TRAIN_*` hors masques : section « 4. Entrainement de chaque bloc ».
- `POSTPROCESS_*` : section « 5. Recadrage AVANT fusion ».
- Fusion et export : section « 6. Fusion et export ».
- `SLURM_*` : section « 7. Slurm uniquement ».

Depuis la racine du dépôt :

```bash
# Exécution directe dans le shell : tous les blocs s'exécutent successivement.
bash tools/images_to_splat/run_pipeline.sh /chemin/config.sh

# Soumission à Slurm : préparation GPU, tâches GPU, puis fusion CPU.
bash tools/images_to_splat/launch.sh /chemin/config.sh

# Variante Slurm : attendre la fin et retourner un code d'erreur en cas d'échec.
bash tools/images_to_splat/launch.sh /chemin/config.sh --wait
```

- `launch.sh` : soumet les jobs ; ne pas le lancer avec `sbatch`.
- `launch.slurm` : worker interne ; les paramètres Slurm viennent de la config,
  pas de directives `#SBATCH` séparées.
- Environnement Slurm : les jobs héritent de l'environnement actif ;
  `SLURM_ENV_SETUP` permet de charger les modules/activer l'environnement sur les nœuds.
- `SLURM_BLOCK_PARALLELISM=4` : autorise quatre tâches GPU simultanées ;
  le mode local reste séquentiel.
- Dépendances : les étapes suivantes ont une dépendance `afterok`.
- Journaux : `OUTPUT_DIR/logs/`.

### Masques, budgets et repères

- `SFM_MASKS_*` : paramètres de masques pour l'extraction ; détail des modèles,
  backends, labels, seuils et périphériques dans
  [`config.sh.example`, section « 1. Masques SfM / train »](config.sh.example).
- `TRAIN_MASKS_*` : paramètres de masques pour l'entraînement ; documentés dans
  la même section de [`config.sh.example`](config.sh.example).
- Héritage : une valeur SfM vide hérite de la valeur train correspondante, sauf
  `SFM_MASKS_DILATE` (défaut `0.002`).
- Les politiques peuvent différer par labels, modèle, seuils, périphérique et backend.
- `spirula` : utilise SAM 3 natif (défaut de ce pipeline).
- `segmentation` : utilise le modèle Transformers local défini par `*_MODEL`
  et l'interpréteur `*_PYTHON`.
- Partage des masques : si les politiques effectives sont identiques, les
  masques sont calculés une seule fois dans `masks/` ; sinon chaque étape activée
  reçoit son propre dossier `feature_masks/` ou `train_masks/`.
- Fichiers de masque : les chemins relatifs des images sont conservés, avec le
  suffixe `.png`.
- `MASKS_ACCEPT_LICENSE` : reste commun aux deux étapes, comme dans Panoramax.
- Labels `sky,cloud` : conservent les voitures et passants dans les images
  d'entraînement, sans garantir la reconstruction des objets mobiles.

- Exemple de budget : 4 blocs × 2 millions de splats, à 20000 itérations
  **par bloc**.
- Cap : maximum ; ni le nombre final ni la qualité ne sont garantis.
- `TRAIN_GROWTH_FACTOR` vide : le worker existant estime le facteur en supposant
  une initialisation à 10 % du cap ; un nuage sparse plus fourni peut faire
  atteindre le cap plus tôt.
- Locale : le calcul utilise la locale `C` pour transmettre un point décimal à
  Spirula, y compris sur un système configuré en français.
- Arrêt 14000/4000 : laisse 4000 pas sans croissance.
- `TRAIN_MASKS_MODE=empty` : apprend le ciel comme vide.
- `TRAIN_MASKS_MODE=ignore` : exclut la perte du ciel.
- `TRAIN_MASKS_MODE=auto` : utilise la résolution du mode déjà implémentée par
  le worker Panoramax.

- Anciens noms refusés : `INPUT_IMAGES_DIR`, `INPUT_RECURSIVE` et paramètres de
  politique `MASKS_*` non préfixés. Les remplacer par `SFM_IMAGES_DIR`,
  `SFM_IMAGES_RECURSIVE` et `SFM_MASKS_*`/`TRAIN_MASKS_*`.
- Politique commune : définir `TRAIN_MASKS_*` et laisser vides les valeurs
  `SFM_MASKS_*` correspondantes.
- Si seuls les paramètres SfM sont définis, renseigner également ceux du train
  lorsque `TRAIN_MASKS=on`.
- Signature de préparation : inclut désormais les deux politiques ; ne pas
  effacer `inputs.json`.
- Reprise explicite : permet d'adopter certaines signatures anciennes ; une
  politique connue incompatible reste refusée.

- Images SfM : photos individuelles, sans filtrage des paires GPS.
- Caméra : une caméra par sous-dossier par défaut ; vérifier que les images
  d'un même dossier partagent bien les mêmes caractéristiques optiques.
- Sans GPX optionnel, l'alignement géographique et l'échelle métrique ne sont
  pas garantis.
- Tous les blocs restent alignés dans le même repère SfM.

- `SFM_MAX_ERROR=2` : seuil en pixels d'extraction, pour l'extraction, la
  vérification des paires et le mapping.
- `SFM_MIN_INLIERS=30` : minimum par paire, pas par point.
- `SFM_MIN_TRI_ANGLE=3` : angle minimum en degrés.
- `SFM_BA_LOSS=huber` : fonction de perte BA.
- `SFM_BA_LOSS_PARAM=2` : échelle robuste en pixels d'extraction.
- Ces défauts stricts sont communs à Panoramax. Le seuil d'angle cible les
  profondeurs mal contraintes ; augmenter ces exigences peut aussi éliminer
  des bâtiments lointains valides et fragmenter la reconstruction.
- Ces réglages ne garantissent pas l'absence de faux points et ne filtrent pas
  un modèle déjà calculé. Leur modification exige un nouvel `OUTPUT_DIR` ;
  `TRAIN_CLEAN` ne recalcule pas le SfM.
- Les matches sont revérifiés lors d'un nouveau calcul SfM, pour ne pas hériter
  d'anciens seuils.

Le découpage conserve les poses caméra et les coordonnées des points ; il
sélectionne les observations sans recentrer ni redimensionner les sous-modèles.
Chaque entraînement peut utiliser sa propre normalisation interne :
`scene_transform.json` est inversé lors de l'export `block_NNN.splat`, pour les
centres, tailles et rotations des Gaussians. Les checkpoints PLY bruts ne doivent
donc pas être mélangés directement aux splats exportés.
Cette cohérence de repère ne garantit pas des surfaces identiques aux frontières :
les optimisations indépendantes peuvent produire des écarts de reconstruction.


### GPX optionnel


Sans `GEOREF_GPX_FILE` (absent ou vide), **aucun changement de comportement** :
le pipeline n'appelle pas le recalage et ne nécessite ni fiona ni pyproj.
Pour l'activer dans la même config Bash/Slurm :

```bash
GEOREF_GPX_FILE="/chemin/absolu/parcours.gpx"
GEOREF_VIEW_PREFIX="h00_y0"  # vide : sélection automatique du dossier
GEOREF_EPSG="2154"
GEOREF_ALLOW_UNCERTAIN="false"
```

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

## 4. Fonctionnement internes

Les pipelines **images locales** et **Panoramax** partagent certains scripts :
`python/mask_policies.py` gère les politiques et l'appel du générateur de
masques ; `scripts/pipeline_config.sh` porte les validations communes et les
seuils SfM. `python/sfm_geometry_policy.py` protège
la réutilisation des modèles du worker multi-blocs ; les images locales
enregistrent ces paramètres dans leur signature `inputs.json`.
`scripts/pipeline_postprocess.sh` partage le recadrage sparse et la fusion,
et `scripts/pipeline_slurm.sh` la soumission, le contrôle des réponses et
l'annulation des jobs en cas d'échec. Les workers Spirula restent dans `scripts/`.

Ce dossier conserve la sélection des fichiers locaux, la disposition du
workspace, ses signatures et le recalage GPX. Panoramax conserve l'acquisition,
les métadonnées et le traitement GPS EXIF. Les commandes, configurations et
chemins de sortie ne changent pas ; les options propres à chaque pipeline
(repère, seuil de recadrage, nettoyage final) ne sont pas uniformisées.

## 5. Notes et informations complémentaires

#### Choisir comment lancer le pipeline

Depuis la racine du dépôt, avec le même fichier de configuration :

```bash
# Directement dans le shell Bash ; les blocs sont exécutés successivement.
bash tools/images_to_splat/run_pipeline.sh /chemin/config.sh

# Soumettre les étapes au gestionnaire Slurm.
bash tools/images_to_splat/launch.sh /chemin/config.sh
```

La première commande exécute le pipeline directement dans le shell. La seconde
soumet les jobs à Slurm ; ne pas la préfixer avec `sbatch`. Pour attendre la fin
des jobs et obtenir un code de retour non nul en cas d'échec, ajouter
`--wait` à la commande Slurm. Les détails d'ordonnancement et de ressources
Slurm sont décrits dans la section « Configuration et utilisation » ci-dessus.

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
et réglages. Le mode normal (`false`) reste inchangé.

En cas de différence de signature, l'erreur indique le chemin d'`inputs.json`,
la valeur effective de `PIPELINE_RESUME` et jusqu'à 12 champs différents
(valeurs enregistrées/actuelles, tailles/dates des images, ajouts ou retraits).
Une configuration Bash inchangée ne garantit pas une signature identique :
les valeurs par défaut du code, les variables d'environnement, les chemins
résolus et les dates des fichiers entrent aussi dans la comparaison.
Ne pas supprimer `inputs.json` pour contourner ce diagnostic.

```bash
bash tools/images_to_splat/run_pipeline.sh /chemin/config.sh --phase prepare
bash tools/images_to_splat/run_pipeline.sh /chemin/config.sh --phase train-block --block 0
bash tools/images_to_splat/run_pipeline.sh /chemin/config.sh --phase merge
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
