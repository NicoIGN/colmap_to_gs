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
variable et son rôle sont commentés dans `tools/panoramax/config.sh.example`.

```bash
cp tools/panoramax/config.sh.example /chemin/vers/paris.sh
# Éditer paris.sh : bbox Lambert-93, OUTPUT_DIR, nombre de blocs, CAP_MAX, etc.
bash tools/panoramax/run_pipeline.sh /chemin/vers/paris.sh
```

Les intermédiaires sont rangés sous `OUTPUT_DIR/panoramax/`. Le résultat est
`OUTPUT_DIR/SPLAT_NAME`; son dossier adjacent `SPLAT_NAME` sans extension suivi
de `.georef/` contient `georeference.json`, les métadonnées Panoramax, le
modèle COLMAP métrique, le manifeste de blocs et les transformations
d'entraînement. Le `.splat` est en coordonnées du repère métrique local de
Spirula, aligné sur le GPS EXIF ; ce n'est pas un fichier de coordonnées EPSG
directes. Ces éléments latéraux conservent les références nécessaires pour
retrouver/interpréter ce repère.

Un OUTPUT_DIR existant ne peut être repris qu'avec les mêmes bbox, dates,
qualité et limite d'images. Pour changer la sélection, choisir un nouveau
OUTPUT_DIR afin de ne pas mélanger des images de plusieurs requêtes.

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
`SLURM_BLOCK_PARALLELISM` limite les blocs simultanés (défaut : `NUM_BLOCKS`,
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
