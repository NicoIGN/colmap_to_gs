# Images locales vers Gaussian Splats

Pipeline Spirula sans téléchargement Panoramax :
sélection d'images → masques partagés → SfM global → recalage GPX optionnel → blocs COLMAP →
entraînement → recadrage bbox COLMAP **avant fusion** → `.splat`.

## Socle commun

Les deux pipelines utilisent `python/mask_policies.py` pour les politiques et
l'appel du générateur de masques, ainsi que `scripts/pipeline_config.sh` pour les
validations communes et les seuils SfM. `python/sfm_geometry_policy.py` protège
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

## Utilisation

Copier `config.sh.example` vers un fichier personnel, régler les chemins et
les ressources. Pour SAM 3, accepter ses conditions explicitement avec
`MASKS_ACCEPT_LICENSE=true`, après les avoir lues.
Activer l'environnement Python habituel (par exemple `mamba activate gsplat`).
Spirula doit disposer du SfM et de l'entraînement ; SAM/Vulkan est nécessaire
si un backend de masques `spirula` est activé.
Les outils réutilisés nécessitent notamment numpy, scipy, tqdm, Pillow et plyfile.

Depuis la racine du dépôt, **même configuration pour les deux modes** :

```bash
# Local : tous les blocs sont entraînés successivement.
bash tools/images_to_splat/run_pipeline.sh /chemin/config.sh

# Slurm : préparation GPU, tableau de jobs GPU, puis recadrage/fusion CPU.
bash tools/images_to_splat/launch.sh /chemin/config.sh

# Attendre la fin et retourner une erreur si le calcul échoue.
bash tools/images_to_splat/launch.sh /chemin/config.sh --wait
```

Le dossier d'images peut être indiqué dans la configuration ou remplacé pour
les deux modes par `--images /chemin/absolu/images`. `SFM_IMAGES_DIR` définit
la source et `SFM_IMAGES_RECURSIVE=true`
parcourt les sous-dossiers ordinaires. Les liens vers des fichiers sont acceptés,
mais pas le parcours des dossiers symboliques. Les noms relatifs sont préservés :
`h00_y0/photo.jpg` et `h01_y45/photo.jpg` restent distincts.
Le workspace doit être extérieur au dossier source. Le dossier `images/` du
workspace contient uniquement des liens symboliques vers la sélection.

`launch.sh` soumet les jobs : ne pas le lancer avec `sbatch`.
`launch.slurm` est le worker interne ; les paramètres Slurm viennent de la config,
pas de directives `#SBATCH` séparées. Les jobs héritent de l'environnement actif ;
`SLURM_ENV_SETUP` permet de charger les modules/activer l'environnement sur les
nœuds. `SLURM_BLOCK_PARALLELISM=4` autorise quatre tâches GPU simultanées ;
le mode local reste séquentiel. Les étapes suivantes ont une dépendance `afterok`.
Les journaux sont dans `OUTPUT_DIR/logs/`.

## Masques, budgets et repères

Les variables sont celles de `tools/panoramax` : `SFM_MASKS_*` pour l'extraction
et `TRAIN_MASKS_*` pour l'entraînement. Une valeur SfM vide hérite de la valeur
train correspondante, sauf `SFM_MASKS_DILATE`, dont le défaut est `0.002`.
Les politiques peuvent différer (labels, modèle, seuils, périphérique, backend).
`spirula` utilise SAM 3 natif (défaut de ce pipeline) ; `segmentation` utilise
le modèle Transformers local défini par `*_MODEL` et l'interpréteur `*_PYTHON`.
Si les politiques effectives sont identiques, les masques sont calculés une seule
fois dans `masks/`. Sinon chaque étape activée reçoit son propre dossier
`feature_masks/` ou `train_masks/`. Les chemins relatifs des images sont conservés,
avec le suffixe `.png`. `MASKS_ACCEPT_LICENSE` reste commun aux deux étapes,
comme dans Panoramax.
`sky,cloud` conserve les voitures et passants dans les images d'entraînement,
sans garantir la reconstruction des objets mobiles.

Le réglage d'exemple vise 4 blocs × 2 millions de splats, à 20000 itérations
**par bloc**. Le cap est un maximum ; ni le nombre final ni la qualité ne sont
garantis. Le facteur de croissance vide est estimé par le worker existant,
en supposant une initialisation à 10 % du cap ; un nuage sparse plus fourni
peut faire atteindre le cap plus tôt.
Le calcul utilise la locale `C` pour transmettre un point décimal à Spirula,
y compris sur un système configuré en français.
L'arrêt 14000/4000 laisse 4000 pas sans croissance.
`TRAIN_MASKS_MODE=empty` apprend le ciel comme vide ; `ignore` exclut sa perte.
`auto` utilise la résolution du mode déjà implémentée par le worker Panoramax.

Les anciennes clés `INPUT_IMAGES_DIR`, `INPUT_RECURSIVE` et les paramètres de
politique `MASKS_*` non préfixés sont refusés explicitement : les remplacer par
`SFM_IMAGES_DIR`, `SFM_IMAGES_RECURSIVE` et `SFM_MASKS_*`/`TRAIN_MASKS_*`.
Pour conserver une politique commune, définir `TRAIN_MASKS_*` et laisser les
valeurs `SFM_MASKS_*` correspondantes vides. Si seuls les paramètres SfM sont
définis, renseigner également ceux du train lorsque `TRAIN_MASKS=on`.
La signature de préparation inclut désormais les deux politiques : ne pas
effacer son `inputs.json`. La reprise explicite ci-dessous permet d'adopter
certaines signatures anciennes ; une politique connue incompatible reste refusée.

Le SfM utilise des photos individuelles, sans filtrage des paires GPS.
Une caméra par sous-dossier est le défaut ; vérifier que les images d'un même
dossier partagent bien les mêmes caractéristiques optiques. L'alignement
géographique et l'échelle métrique ne sont pas garantis sans GPX optionnel.
Tous les blocs restent alignés dans le même repère SfM.

Les contrôles géométriques utilisent des défauts stricts, communs à Panoramax :
`SFM_MAX_ERROR=2` (pixels d'extraction, vérification des paires et mapping),
`SFM_MIN_INLIERS=30` (minimum par paire, pas par point),
`SFM_MIN_TRI_ANGLE=3` (degrés), `SFM_BA_LOSS=huber` et
`SFM_BA_LOSS_PARAM=2` (échelle robuste en pixels d'extraction).
Le seuil d'angle cible les profondeurs mal contraintes ; augmenter ces exigences
peut aussi éliminer des bâtiments lointains valides et fragmenter la reconstruction.
Ces réglages ne garantissent pas l'absence de faux points et ne filtrent pas
un modèle déjà calculé. Leur modification exige un nouvel `OUTPUT_DIR` ;
`TRAIN_CLEAN` ne recalcule pas le SfM. Les matches sont revérifiés lors d'un
nouveau calcul SfM, pour ne pas hériter d'anciens seuils.

Le découpage conserve les poses caméra et les coordonnées des points ; il
sélectionne les observations sans recentrer ni redimensionner les sous-modèles.
Chaque entraînement peut utiliser sa propre normalisation interne :
`scene_transform.json` est inversé lors de l'export `block_NNN.splat`, pour les
centres, tailles et rotations des Gaussians. Les checkpoints PLY bruts ne doivent
donc pas être mélangés directement aux splats exportés.
Cette cohérence de repère ne garantit pas des surfaces identiques aux frontières :
les optimisations indépendantes peuvent produire des écarts de reconstruction.

## GPX optionnel

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

## Reprise et fusion seule

Le pipeline conserve `inputs.json` (sélection, tailles/dates, paramètres SfM,
masques et découpage). Une modification incompatible est refusée : choisir un
nouvel `OUTPUT_DIR`, sans mélanger plusieurs reconstructions.
Ne pas modifier les images/configurations pendant des jobs actifs.
Une préparation compatible reprend les masques et réutilise le SfM/découpage.
Les entraînements terminés sont réutilisés par défaut ; `TRAIN_CLEAN=true`
recalcule le 3DGS uniquement. Les sorties partielles du train nécessitent aussi
ce réglage, après sauvegarde. L'exemple ne supprime jamais les images source.

### Masques terminés, SfM interrompu

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
