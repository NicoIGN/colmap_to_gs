# Télécharger des photos Panoramax par bbox Lambert-93

Ce lanceur recherche les photos du catalogue fédéré Panoramax via `/api/search`,
pagine jusqu'au dernier résultat et télécharge les images HD disponibles.
Il n'a pas besoin de compte Panoramax.

## Installation

Dans ton environnement Python actif (Python 3.10 ou plus récent) :

```bash
python3 -m pip install -r tools/panoramax/requirements.txt
```

`pyproj` est déjà présent dans l'environnement Conda fourni par ce dépôt.

## Utilisation

Depuis la racine du dépôt :

```bash
bash tools/panoramax/download.sh \
  --bbox "651000,6861000,652000,6862000" \
  --output-dir /chemin/vers/panoramax
```

L'ordre est **xmin,ymin,xmax,ymax**, en mètres, en **Lambert-93 (EPSG:2154)**.
Les coordonnées de l'exemple sont illustratives, pas une garantie de présence de photos.
Le script transforme une enveloppe densifiée en WGS84 pour la recherche STAC,
puis vérifie chaque point de prise de vue dans la bbox Lambert-93 initiale.

Options :

| Option | Description |
|---|---|
| `--datetime "2026-01-01T00:00:00Z/2026-10-31T23:59:59Z"` | Intervalle de prise de vue, transmis à l'API |
| `--image-max 400` | Limite le nombre total de photos sélectionnées, toutes les pages confondues |
| `--quality sd` | Résolution SD ; valeurs : `hd` (défaut), `sd`, `thumb` |
| `--api-url https://une-instance/api` | Autre API compatible ; défaut : catalogue fédéré `explore.panoramax.fr` |
| `--timeout 120` | Délai réseau par requête, en secondes |

## Périmètre

« Dans la bbox » signifie **position de prise de vue dans la bbox**, pas contenu
visible dans la photo. Toutes les photos retournées sont candidates, panoramiques
ou non, sans restriction d'auteur ou de séquence. Le script ne télécharge pas les
photos extérieures de séquences qui traversent la zone.

La qualité HD est l'asset public `hd` annoncé par l'API ; elle ne garantit pas
l'accès à l'original brut non flouté. Aucun remplacement silencieux par une qualité
inférieure n'est effectué si l'asset demandé manque.

## Sorties et reprise

```text
output-dir/
├── images/<identifiant-haché>.jpg
├── metadata/<identifiant-haché>.json
├── pages/page-000001.json
├── manifest.jsonl
├── request.json
└── summary.json
```

Les métadonnées conservent le Feature STAC complet : coordonnées, date, séquence,
propriétés, assets, liens de provenance et informations de licence/attribution
présentes dans la réponse. Les fichiers sont nommés à partir d'un hash de
collection + identifiant photo pour éviter collisions et noms dangereux.
Le manifeste associe les identifiants Panoramax aux fichiers locaux.

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

Les requêtes sont séquentielles, avec trois tentatives en cas d'erreur réseau,
HTTP 429 ou 5xx. Une grande bbox peut représenter un volume important ; commencer
par une petite zone et respecter les conditions d'utilisation et d'attribution
des instances et des photos.

Dans un terminal interactif, une barre de progression affiche les photos
téléchargées, réutilisées ou en erreur. Si moins que la limite demandée sont
trouvées, la barre se termine à 100 % du total effectivement sélectionné.

Le script demande jusqu'à 10 000 métadonnées à l'API (sans télécharger les
images à cette étape), compte les photos dont le point est dans la bbox exacte
Lambert-93, puis affiche ce total et l'écrit dans `summary.json` (`available`,
`available_is_exact`). Si l'API renvoie 10 000 résultats sur sa dernière page,
le total affiché est une borne basse (`+`) et un avertissement le précise.

`--image-max` limite ensuite le nombre de photos à télécharger ou réutiliser,
sans réduire le comptage : par exemple, `--image-max 400` affiche le total
disponible dans la bbox et en traite au plus 400.

## Références

- API : https://explore.panoramax.fr/swagger
- CLI officielle : https://docs.panoramax.fr/cli/COMMANDS/#panoramax_cli-download
- Catalogue GeoParquet (alternative pour les grandes recherches) :
  https://docs.panoramax.fr/federated-catalog/tutorials/duckdb/

## Vérification locale

```bash
python3 -m unittest discover -s tools/panoramax -p 'test_*.py'
```

Les tests utilisent une API HTTP locale pour vérifier la pagination, la sélection
géographique, la reprise, les erreurs de téléchargement et les nouvelles tentatives.

## Reconstruction Spirula géoréférencée par l'EXIF

Les JPEG HD de Panoramax conservent le GPS EXIF (latitude, longitude,
altitude, cap). Spirula s'en sert directement pour placer le modèle dans un
repère local est-nord-haut en mètres, sans recalage GPX a posteriori :

```bash
bash scripts/run_spirula_sfm.sh \
  --spirula-bin /chemin/vers/spirula \
  --dataset OUTPUT_DIR \
  --output-dir OUTPUT_DIR/sfm \
  --preset panoramax
```

`--preset panoramax` règle `--data-type individual`, `--pairs auto`, aucune
séquence (les noms de fichiers sont des empreintes), `--metric-gps full` et
`--sensor-pairs --sensor-pair-radius 30`. Un code de sortie 4 signifie que le
modèle a été écrit sans le repère métrique demandé.

## Pipeline complet bbox → `.splat`

Le lanceur `run_pipeline.sh` enchaîne le téléchargement Panoramax, le SfM
géoréférencé, le découpage COLMAP, l'entraînement Spirula de chaque bloc et la
fusion voxelisée de leurs splats alignés en un unique fichier final. Il prend
un fichier de configuration Bash (fichier de code de confiance) ; chaque
variable, ses valeurs possibles et son défaut sont commentés dans
`tools/panoramax/config.sh.example`, organisé par étape du pipeline.
Le modèle reprend les défauts, sans préréglage de croissance sobre ; seuls
`OUTPUT_DIR` et `PANORAMAX_BBOX_LAMBERT93` sont des exemples obligatoires à remplacer.

Le découpage privilégie les séparations de faible covisibilité et vise des
blocs d'environ `N / SPLIT_NUM_BLOCKS` images principales (tolérance de 20 %,
arrondie à l'entier). La connexité prime : des tailles plus inégales sont
acceptées avec un avertissement si aucun déplacement de frontière sûr
n'est possible. Le recouvrement peut ensuite augmenter les effectifs.

```bash
cp tools/panoramax/config.sh.example /chemin/vers/paris.sh
# Éditer paris.sh : bbox Lambert-93, OUTPUT_DIR, nombre de blocs, TRAIN_CAP_MAX, etc.
bash tools/panoramax/run_pipeline.sh /chemin/vers/paris.sh
```

Les valeurs sont vérifiées avant téléchargement et, avec `launch.sh`, avant
toute soumission Slurm. Les erreurs sont regroupées et indiquent le paramètre,
sa valeur et les valeurs attendues (par exemple `lightblue` est refusé pour
`SFM_MATCHER`, sans correction automatique). Les listes Spirula correspondent
aux options prises en charge par ce lanceur ; elles devront être actualisées
si une future version ajoute des extracteurs, apparieurs ou préréglages.
La validation seule ne nécessite ni GPU, ni Python, ni Slurm :

```bash
bash tools/panoramax/validate_config.sh /chemin/vers/paris.sh slurm
```

Elle contrôle les choix SfM/entraînement, entiers, rayons, recouvrement,
booléens, bbox ordonnée, URL et nom de sortie, ainsi que la syntaxe des
ressources Slurm en mode `slurm`. Elle n'exécute pas Spirula, ne vérifie pas
la disponibilité des partitions/ressources du cluster et ne garantit pas
la compatibilité de la version installée. Le fichier Bash reste du code
de confiance et est sourcé, même en validation seule.

Les intermédiaires sont rangés sous `OUTPUT_DIR/panoramax/`. Le résultat est
`OUTPUT_DIR/EXPORT_SPLAT_NAME`; son dossier adjacent `EXPORT_SPLAT_NAME` sans extension suivi
de `.georef/` contient `georeference.json`, les métadonnées Panoramax, le
modèle COLMAP métrique, le manifeste de blocs et les transformations
d'entraînement. Le `.splat` est en coordonnées du repère métrique local de
Spirula, aligné sur le GPS EXIF ; ce n'est pas un fichier de coordonnées EPSG
directes. Ces éléments latéraux conservent les références nécessaires pour
retrouver/interpréter ce repère.

Le filtre géométrique est maintenant **global, après fusion**, sans seuil en
mètres. Par défaut `POSTPROCESS_FILTER_MODE="diagnostic"` conserve la scène et exporte
les grands splats périphériques sans support de surface dans
`NOM.rejected.splat`. `NOM.unfiltered.splat` conserve la fusion d'origine et
`NOM.filter.json` donne les compteurs. Après examen, `"apply"` retire les
candidats ; `"off"` désactive le test. Le rapport de taille local se règle par
`POSTPROCESS_FILTER_SIZE_FACTOR` (défaut 10).
Voir [la description et les limites du filtre](../postprocess/README.md).

Pour remplacer ce filtre par le nettoyage expérimental du ciel multivues,
définir `POSTPROCESS_CLEANER="cleangs"` et `CLEANGS_MODEL` (dossier local de
segmentation). `CLEANGS_MODE="diagnostic"` conserve les splats et exporte les
rejets ; `apply` les retire. La fusion Slurm demande alors un GPU NVIDIA.
Ce mode est une adaptation de Clean-GS, pas son algorithme Pareto officiel :
installation, dépendances et précautions sont décrites dans
[tools/clean_gs](../clean_gs/README.md).
L'installation complète se fait avec `bash tools/clean_gs/install.sh
--install-dir /chemin/partage/cleangs --python python3` ; le script affiche
`CLEANGS_PYTHON` et `CLEANGS_MODEL` à ajouter à la configuration.

Masques d'entraînement optionnels (`TRAIN_MASKS="on"`, défaut `off`) :
`TRAIN_MASKS_BACKEND="segmentation"` utilise SegFormer/Mask2Former ;
`TRAIN_MASKS_BACKEND="spirula"` utilise SAM 3 natif sur Vulkan, avec
`TRAIN_MASKS_SPIRULA_MODEL` pointant vers un checkpoint GGML local. Avant
le découpage, un segmenteur local génère un masque par image dans
`sfm-*/train_masks/`, transmis à Spirula. Par défaut seul le ciel (nuages
compris) est masqué et appris comme vide ; d'autres labels (`person`, `car`…)
peuvent être ajoutés via `TRAIN_MASKS_LABELS`. Installation, variables et
limites : [tools/segmentation_masks](../segmentation_masks/README.md).

### Qualité d'entraînement

Le paramètre optionnel `TRAIN_QUALITY=""` transmet `--quality` à
`spirula train` lorsqu'il vaut `low`, `medium`, `high` ou `ultra`, en local
comme sous Slurm. Vide conserve le comportement précédent. Dans Spirula,
cette macro propose un budget de splats et une durée ; ce n'est pas un
réglage de résolution. Les valeurs explicites `TRAIN_CAP_MAX` (éventuellement
limité par `TRAIN_SPLATS_PER_IMAGE`) et `TRAIN_NUM_ITER` du pipeline restent prioritaires :
changer uniquement `TRAIN_QUALITY` ne relève donc pas ces deux valeurs.

### Masques avant SfM et partage avec l'entraînement

`SFM_MASKS="on"` prépare les images puis calcule les masques **avant**
l'extraction/appariement. Le lanceur transmet `--feature-masks` à Spirula
(y compris avec le filtre GPS), et force la réextraction lorsqu'un nouveau
SfM est nécessaire. Le téléchargement, les jobs Slurm et le reste du pipeline
restent inchangés ; aucun script de calcul personnalisé n'est nécessaire.

Les paramètres `SFM_MASKS_BACKEND`, `SFM_MASKS_LABELS`,
`SFM_MASKS_SPIRULA_MODEL`, `SFM_MASKS_SPIRULA_DEVICE`, `SFM_MASKS_MODEL`,
`SFM_MASKS_PYTHON`, `SFM_MASKS_DEVICE`, `SFM_MASKS_CONFIDENCE` et
`SFM_MASKS_MAX_FRACTION` héritent de leurs équivalents `TRAIN_MASKS_*` si vides.
`SFM_MASKS_DILATE` vaut 0.002 par défaut (ou `auto`, équivalent).
Ils peuvent tous être réglés indépendamment. Le mode de perte
`TRAIN_MASKS_MODE` reste propre au 3DGS.

Pour les appels SAM 3 non interactifs, `MASKS_ACCEPT_LICENSE="true"` transmet
explicitement `--accept-license sam3=yes` pour les deux calculs. Ne l'activer
qu'après lecture et acceptation des conditions Meta ; défaut `false`.

Si les labels (ordre/casse ignorés), le backend, le modèle, le périphérique,
le Python et les seuils de génération sont identiques, le pipeline calcule
une seule série dans `sfm-*/train_masks/` puis crée le lien symbolique
`sfm-*/feature_masks -> train_masks`. Sinon, il calcule les deux séries.
Des labels identiques avec des seuils différents ne doivent pas partager
les mêmes PNG. Mettre `TRAIN_MASKS=off` permet aussi de masquer uniquement le SfM.

La politique SfM est enregistrée dans `sfm_mask_policy.json`. Un SfM ou des
caractéristiques déjà présents sans signature compatible sont refusés :
choisir un nouvel `OUTPUT_DIR` ou déplacer le workspace en sauvegarde.
`TRAIN_CLEAN=true` ne recalcule **pas** le SfM. Ne pas modifier les fichiers
de masques manuellement pendant les calculs.
Si `feature_masks` est déjà un dossier réel et qu'un nouveau réglage demande
un partage, le pipeline refuse de le remplacer par un lien : utiliser un
nouveau workspace. Inversement, séparer des masques précédemment partagés
préserve une copie des masques SfM avant de changer ceux d'entraînement.

Pour les nuages, `SFM_MASKS_LABELS="sky"` peut réduire les correspondances
parasites. Ajouter `person,car` retire également des points sur les voitures
stationnées et peut réduire le nombre de vues enregistrées. Les résultats
avec SAM/Spirula réels restent à contrôler sur votre matériel.
Les blocs déjà entraînés ne sont pas recalculés : utiliser `TRAIN_CLEAN=true`.

Avant ce filtre, les splats dont le **centre** sort de la bbox des points 3D
sparse (`sfm-*/sparse/0/points3D.bin`, même repère que les splats) sont retirés
(`POSTPROCESS_SPARSE_BBOX_MODE="apply"` par défaut ; `diagnostic` ou `off`). La boîte
est orientée sur les axes principaux des points, bornée aux quantiles
0,5 %/99,5 %, et élargie de `POSTPROCESS_SPARSE_BBOX_MARGIN` × son plus grand côté
(défaut 0.05, sans unité). Le filtre contextuel n'est ensuite évalué que sur les
splats restants. La limite de 20 % de rejets appliqués reste active.

Budget d'entraînement : Spirula (MCMC) augmente le nombre de splats jusqu'à
`cap_max`, donc atteindre le plafond est attendu. `TRAIN_CAP_MAX` est un plafond dur
**par bloc** (valeur initiale 1500000). Par défaut `TRAIN_SPLATS_PER_IMAGE`,
`TRAIN_GROWTH_FACTOR`, `TRAIN_REFINE_STOP_ITER` et `TRAIN_REFINE_STOP_NUM_ITER` restent vides :
budget `TRAIN_CAP_MAX`, croissance Spirula 1.05 et paramètres d'arrêt 14000/2500.
Pour un réglage plus sobre, définir par exemple `TRAIN_SPLATS_PER_IMAGE="2000"`
(budget `min(TRAIN_CAP_MAX, images × TRAIN_SPLATS_PER_IMAGE)`), `TRAIN_GROWTH_FACTOR="1.03"`,
`TRAIN_REFINE_STOP_ITER="18000"` et `TRAIN_REFINE_STOP_NUM_ITER="12000"` : avec 30000
itérations, la croissance s'arrête à 60 % du calcul. L'arrêt effectif est
`max(TRAIN_REFINE_STOP_ITER, TRAIN_NUM_ITER - TRAIN_REFINE_STOP_NUM_ITER)`.
Calibrer
`TRAIN_SPLATS_PER_IMAGE` en comparant des rendus de même vue, pas sur le PSNR d'une
étape isolée.
Les blocs sont réexportés sans filtre de BBOX/taille lors de leur relance,
sans réentraîner si le checkpoint final existe et `TRAIN_CLEAN=false` ; c'est
nécessaire pour récupérer les splats supprimés par un ancien export.

Un OUTPUT_DIR existant ne peut être repris qu'avec les mêmes bbox, dates,
qualité et limite d'images. Pour changer la sélection, choisir un nouveau
OUTPUT_DIR afin de ne pas mélanger des images de plusieurs requêtes.
Avec `PANORAMAX_REPLACE_REQUEST="true"`, l'ancien `OUTPUT_DIR/panoramax` est
renommé en `panoramax.replaced-AAAAMMJJ-HHMMSS` (rien n'est supprimé), puis la
nouvelle requête est téléchargée dans un dossier vide.

### Filtre exclusif des paires par distance GPS

Dans le fichier de configuration :

```bash
SFM_PAIR_MAX_DISTANCE="30"  # mètres ; "" désactive le filtre (défaut)
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
au `--sensor-pairs` du mode auto non filtré. `SFM_PAIRS` continue de choisir les
candidats initiaux. L'appariement est effectué avant le filtre : celui-ci ne
réduit donc pas son coût et ne corrige pas un plantage LightGlue. En cas de
plantage connu, configurer séparément `SFM_MATCHER="bruteforce"`.

Le workspace `panoramax/sfm-TRAIN_NUM_ITER/` conserve `matches.bin` original,
`matches-gps.bin` filtré et `gps_filter_summary.json` (paires conservées,
retirées et sans GPS). Le résumé est aussi copié dans le dossier final
`.georef/`. Les formats VKMT v2, v3 et v4 sont reconnus ; un format inconnu
ou tronqué provoque une erreur explicite, sans écraser l'original.
Si aucune paire ne subsiste, la reconstruction ne démarre pas.

Un SfM existant ne peut pas être corrigé en filtrant son modèle COLMAP final :
il faut le reconstruire. Pour activer, désactiver ou changer le rayon sur un
calcul existant, déplacer le workspace `panoramax/sfm-TRAIN_NUM_ITER/` **et**
les entraînements `panoramax/gs-TRAIN_NUM_ITER-blocks-SPLIT_NUM_BLOCKS/` en sauvegarde,
puis relancer la préparation et les blocs ; garder `images/` et `metadata/`
pour éviter de retélécharger. Le lanceur refuse de réutiliser un SfM dont
la signature ne correspond pas au rayon, aux paramètres et aux entrées.
À paramètres identiques, un SfM filtré terminé est réutilisé ; après un échec
avant reconstruction, extraction et appariement sont relancés. Un modèle
partiel doit être déplacé en sauvegarde avant de réessayer.

### Exécution GPU sur Slurm

`launch.sh` soumet **le même pipeline complet** en trois étapes dépendantes :
un job GPU de téléchargement/SfM/découpage, un tableau Slurm (`--array`) avec
un GPU NVIDIA par bloc, puis un job CPU de fusion et de copie du géoréférencement.
Les blocs sont entraînés en parallèle, sous réserve des GPU disponibles.
Le `.splat` fusionné et son dossier `.georef/` restent dans `OUTPUT_DIR`.
Les ressources sont définies par les variables `SLURM_*` commentées dans
`config.sh.example` : partition, CPU, RAM, durée, type GPU, compte et QoS.
Contrairement aux paramètres d'entraînement, la RAM Slurm n'est pas la VRAM.
CPU, RAM et durée sont demandés **par job/tâche**, pas pour l'ensemble.
`SLURM_BLOCK_PARALLELISM` limite les blocs simultanés (défaut : `SPLIT_NUM_BLOCKS`,
`1` pour les entraîner successivement). Quatre blocs simultanés réservent
jusqu'à quatre GPU et quatre fois les CPU/RAM configurés.
La partition choisie doit aussi autoriser le job de fusion sans GPU.

```bash
# Sur le nœud de connexion, après adaptation de la configuration au cluster :
bash tools/panoramax/launch.sh /chemin/partage/config.sh
# Ou attendre la fin, avec un code de retour non nul si le job échoue :
bash tools/panoramax/launch.sh /chemin/partage/config.sh --wait
```

Sans `--wait`, le lanceur confirme uniquement les trois soumissions et affiche
leurs ID, les commandes `squeue`/`sacct`/`scancel` et les chemins des journaux :
`OUTPUT_DIR/logs/submit.log`, `panoramax-prepare-JOBID.{out,err}`,
`panoramax-block-ARRAYID_INDICE.{out,err}` et `panoramax-merge-JOBID.{out,err}`.
Les dépendances `afterok` empêchent d'entraîner sans préparation réussie et de
fusionner si un bloc échoue. Les jobs dont la dépendance échoue sont annulés
(`--kill-on-invalid-dep=yes`), sans rester indéfiniment en attente.
`--wait` attend la fusion et renvoie un échec si elle échoue ou ne peut pas
démarrer. Une soumission partiellement refusée annule les jobs déjà soumis.
Le dépôt, la configuration, les exécutables et les données doivent être
accessibles sur le nœud de calcul aux mêmes chemins. Les chemins relatifs sont
interprétés depuis le dossier de soumission ; préférer des chemins absolus.
Ne pas modifier la configuration tant que le job est en attente ou en cours.
Le nœud doit avoir accès à l'API Panoramax pour le téléchargement.

Spirula doit être compilé pour **Vulkan sous Linux** (par exemple
`spirula-studio/build_vulkan/spirula`, à renseigner dans `SPIRULA_BIN`) ;
la disponibilité CUDA/PyTorch seule ne suffit pas. Le pilote NVIDIA et son
ICD Vulkan doivent être installés sur les nœuds. Slurm doit isoler les
périphériques du job (cgroups) : `CUDA_VISIBLE_DEVICES` seul ne sélectionne
pas un GPU Vulkan. Les GPU MIG ne sont pas pris en charge par ce lanceur.

Pour charger les modules du site ou configurer Vulkan, renseigner
`SLURM_ENV_SETUP` avec un script Bash de confiance. Pour activer Conda,
renseigner `SLURM_CONDA_ENV` (par exemple `gsplat`) et, si nécessaire,
`SLURM_CONDA_BASE`. Sinon l'environnement est hérité de la soumission.
Python doit contenir `numpy`, `scipy`, `tqdm`, `pyproj` et `plyfile`.
Chaque job vérifie ces imports ; les jobs GPU vérifient aussi `nvidia-smi` ;
toute erreur de préparation ou de calcul est propagée à Slurm.
En cas d'arrêt du pipeline, un message précise l'étape concernée ; le bilan
Slurm indique le job, le code de sortie et les chemins des journaux. Les lignes
`srun: error` relaient cet arrêt et ne sont pas, à elles seules, sa cause.

Si Spirula plante dans LightGlue avec `gemm: weight is ... but out has ...`,
il s'agit d'une incompatibilité de dimensions dans l'appariement, avant la
reconstruction et l'entraînement des splats. Le code `139` indique un SIGSEGV,
pas à lui seul un manque de mémoire GPU. Essayer `SFM_MATCHER="bruteforce"`
pour contourner LightGlue, ou mettre à jour Spirula ; aucun contournement
automatique ne change la méthode de calcul à l'insu de l'utilisateur.
Si `sfm auto` termine avec SIGSEGV après l'export, le wrapper vérifie que les
trois fichiers binaires de `sparse/0` ont été créés pendant cette exécution,
se lisent entièrement et contiennent des caméras, images et points. Si ces
conditions sont satisfaites, il poursuit avec un avertissement explicite.
Des fichiers préexistants, absents, vides ou tronqués restent bloquants.
Cette récupération ne corrige pas le crash et ne certifie pas la qualité du
modèle ; les codes d'erreur de qualité ou de géoréférencement restent propagés.
`SFM_THREADS` permet de limiter les fils SfM ; sous Slurm, son défaut suit
`SLURM_CPUS_PER_TASK` au lieu du nombre de CPU physiques du nœud.

Les étapes sont également accessibles individuellement, avec la même config :

```bash
bash tools/panoramax/run_pipeline.sh config.sh prepare
bash tools/panoramax/run_pipeline.sh config.sh train-block 0  # indices 0 à N-1
bash tools/panoramax/run_pipeline.sh config.sh merge
```

Seule `prepare` télécharge et découpe ; chaque tâche `train-block` ne modifie
que sa sortie. `merge` exige tous les splats et ne relance aucun entraînement.
Sans argument d'étape, le pipeline local reste séquentiel et complet.

Le préréglage lance d'abord `prepare_sfm.py` :

- seules les images de la taille la plus fréquente sont gardées (une bbox
  mélange souvent plusieurs appareils : 360° en 2:1, téléphones...) ; elles
  sont liées par lien physique dans `OUTPUT_DIR/sfm/images`, sans toucher
  aux originaux ; `--keep-size LxH` impose une autre taille ;
- la focale initiale est déduite du champ de vue horizontal des métadonnées,
  `f = (largeur / 2) / tan(fov / 2)`, soit environ 1877 px pour une imajbox
  4096×2160 à 95° ; l'EXIF HD n'ayant pas de focale, Spirula la devinerait
  sinon très loin ; `--focal` reste prioritaire ;
- le choix est résumé dans `OUTPUT_DIR/sfm/selection.json`.

`--no-prepare` lance Spirula sur le dataset tel quel. Le script se lance
seul :

```bash
python3 tools/panoramax/prepare_sfm.py --dataset OUTPUT_DIR \
  --output-dir OUTPUT_DIR/sfm --dry-run
```

### Noms des paramètres par étape

Les configurations locales et Slurm utilisent les mêmes noms préfixés.
Les chemins et outils communs (`OUTPUT_DIR`, `GIT_ROOT`, `SPIRULA_BIN`,
`PYTHON_BIN`) ainsi que les variables natives d'environnement restent inchangés.
Les anciens noms ci-dessous sont **refusés**, même vides ou accompagnés du
nouveau nom ; mettre à jour le fichier de configuration avant de relancer.
Les options CLI Spirula et les noms des dossiers de calcul ne changent pas.

| Ancien nom | Nouveau nom |
|---|---|
| `BBOX_LAMBERT93` | `PANORAMAX_BBOX_LAMBERT93` |
| `NUM_BLOCKS` | `SPLIT_NUM_BLOCKS` |
| `OVERLAP_MAX_FRACTION` | `SPLIT_OVERLAP_MAX_FRACTION` |
| `NUM_ITER` | `TRAIN_NUM_ITER` |
| `CAP_MAX` | `TRAIN_CAP_MAX` |
| `SPLATS_PER_IMAGE` | `TRAIN_SPLATS_PER_IMAGE` |
| `GROWTH_FACTOR` | `TRAIN_GROWTH_FACTOR` |
| `REFINE_STOP_ITER` | `TRAIN_REFINE_STOP_ITER` |
| `REFINE_STOP_NUM_ITER` | `TRAIN_REFINE_STOP_NUM_ITER` |
| `FLOATER_SUPPRESSION` | `TRAIN_FLOATER_SUPPRESSION` |
| `CLEAN_GS` | `TRAIN_CLEAN` |
| `SAM_ACCEPT_LICENSE` | `MASKS_ACCEPT_LICENSE` |
| `SPLAT_NAME` | `EXPORT_SPLAT_NAME` |
| `SPLAT_MERGE_STRENGTH` | `MERGE_STRENGTH` |
| `SPLAT_CLEANER` | `POSTPROCESS_CLEANER` |
| `SPLAT_FILTER_MODE` | `POSTPROCESS_FILTER_MODE` |
| `SPLAT_FILTER_SIZE_FACTOR` | `POSTPROCESS_FILTER_SIZE_FACTOR` |
| `SPLAT_SPARSE_BBOX_MODE` | `POSTPROCESS_SPARSE_BBOX_MODE` |
| `SPLAT_SPARSE_BBOX_MARGIN` | `POSTPROCESS_SPARSE_BBOX_MARGIN` |

Pour le worker `scripts/run_spirula_block.sh`, les anciennes variables
`QUALITY`, `NUM_ITERATIONS` et `CLEAN` sont également refusées : utiliser
`TRAIN_QUALITY`, `TRAIN_NUM_ITER` et `TRAIN_CLEAN`. Les arguments CLI du
worker (`--quality`, `--num-iterations`, `--clean`) restent disponibles.
