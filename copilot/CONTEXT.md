# Note de contexte Copilot — colmap_to_gs

## Message à copier pour une nouvelle session

```text
Nous travaillons sur NicoIGN/colmap_to_gs (photos, vidéos ou Panoramax → scènes 3D Gaussian Splatting). Branche review_copilot. 
Lis d'abord copilot/CONTEXT.md. Utilise GIT_ROOT pour les chemins, sans chemin absolu. 
Commandes : examples/ ; paramètres : scripts/helper.sh. 
Avant tout test, active ~/.venvs/colmap_to_gs (« Dépendances Python ») : sans lui, les échecs numpy/pyproj ne sont pas une régression.
```

À lire en premier au démarrage d'une session. Mettre à jour après chaque changement notable.

## Projet
- Pipeline images/vidéos → 3DGS (SfM HLOC / Spirula / COLMAP / Nerfstudio, entraînement Spirula, export `.splat`).
- Point d'entrée vidéo : `scripts/launch_videos_to_3dgs.sh <config.sh>` (`VIDEO`, `NUM_FRAMES` dans la configuration).
- Config : `config/defaults/*.default.sh` (défauts, chargés en premier) puis `config.sh` utilisateur (a le dernier mot).
- Avant d'ajouter un paramètre, vérifier si les paramètres existants permettent déjà de satisfaire le besoin ; privilégier leur réutilisation et éviter les réglages redondants.
- Branche de travail : `review_copilot`. Pousser les changements terminés sur la branche (pas de merge direct).
- Avant de continuer, toujours mettre le projet à jour avec `git pull` sur la branche (`review_copilot`).
- Chemins du projet : `$GIT_ROOT/...` ; données et résultats : `$DATA_ROOT/...`. Ne pas utiliser de chemin absolu dans les réponses.

## Dépendances Python (venv dédié)
Un venv évite l'erreur PEP 668 (`externally-managed-environment`) et les `No module named ...` :

```bash
bash "$GIT_ROOT/copilot/setup_env.sh"           # crée ~/.venvs/colmap_to_gs et installe tout
source ~/.venvs/colmap_to_gs/bin/activate
```

Environnement de référence de l'agent : `copilot/conda_env_copilot.yml` (Python 3.12.3, versions épinglées, testé avec `~/.venvs/colmap_to_gs`). Pour une prochaine session, créer et utiliser cet env en priorité :

```bash
conda env create --yes --file "$GIT_ROOT/copilot/conda_env.yml"
conda activate colmap_to_gs_copilot
```

Il ne contient pas `fiona` (requis par `tools/georef/georef_colmap_from_gpx.py`, test `georeference`) : `pip install fiona` si ce test est lancé.
Il ne contient pas `pyogrio` (requis par `tools/vector/requirements.txt` et `tests/vector/test_fetch_vector.py`) : `pip install pyogrio` si ce test est lancé.

Paquets utilisés par les tests et le pipeline :
- Tests images/Spirula : `pillow numpy opencv-python-headless tqdm scipy plyfile`
- Lidar / panoramax : `pyproj laspy lazrs` (`tools/lidar/requirements.txt`, `tools/panoramax/requirements.txt`)
- HLOC / COLMAP : `pycolmap>=4.2` (`tools/lidar/requirements-colmap.txt`)

Sans `numpy`, `scipy`, `plyfile`, `cv2`, `PIL`, `tqdm`, `pyproj` : les suites et scripts échouent avec `ModuleNotFoundError`, ce ne sont pas des bugs du code. Vérifier d'abord : `python -c "import numpy, scipy, plyfile, tqdm, cv2, PIL"`.

### pyproj (projections CRS)
- Rôle : conversions WGS84 ↔ projection (Lambert-93 EPSG:2154 par défaut, ou CRS GRID/Panoramax). Import `from pyproj import Transformer`, parfois `CRS`, `Geod`, `ProjError`.
- Utilisé par : `tools/georef/georef_gps.py` et `tools/georef/positions_to_geojson.py`, `tools/sfm/spirula/gps_to_metric_positions.py` (ENU), `tools/georef/georef_colmap_from_gpx.py` (import tardif, avec fiona), `tools/grid/grid_tiles.py` (emprises GRID), `tools/export/filter_splat_by_bbox.py` (clip), `tools/panoramax/download.py` et `sfm_filter_matches_gps.py`, `tools/lidar/*`, `tools/vector/fetch_vector.py`, `tools/prepare/prepare_image_info.py`.
- Installation : `copilot/setup_env.sh` l'installe avec `laspy lazrs fiona`. Version épinglée dans `tools/panoramax/requirements.txt` (`pyproj>=3.4,<4`). Dans les environnements conda, `copilot/conda_env.yml` fixe `pyproj==3.8.0`.
- Pièges : les modules qui l'importent en tête (ex. `tools/grid/grid_tiles.py`, `tools/sfm/spirula/gps_to_metric_positions.py`) échouent dès l'import si `pyproj` manque, même pour une commande qui n'en a pas besoin à l'exécution. Les imports tardifs (`georef_colmap_from_gpx.py`, `tools/lidar/pipeline_options.py`) ne l'exigent que pour leur option.
- Ordre des axes : `Transformer.from_crs(..., always_xy=True)` donne (lon, lat) → (x, y). Sans `always_xy`, EPSG:4326 est lu en (lat, lon) et les points tombent au mauvais endroit.
- Tests qui exigent pyproj : `tests/panoramax/test_download.py`, `tests/panoramax/test_sfm_filter_matches_gps.py`, `tests/lidar/test_colmap_lidar.py`, `tests/grid/test_grid_tiles.py`.
- Vérification : `python -c "import pyproj; print(pyproj.__version__)"` dans `~/.venvs/colmap_to_gs`.

- Test rapide sans polluer le venv : `python3 -m venv /tmp/x && /tmp/x/bin/pip install -q numpy scipy plyfile`.
- Sans numpy, `test_lidar_recalage_precedes_partition_and_block_jobs_check_it` (`test_run_panoramax.py`) échoue. D'autres échecs préexistants sont indépendants de numpy : `test_validate_config` refuse `KMP_DUPLICATE_LIB_OK` pourtant présent dans les défauts, et `tools/sfm/test_tools.py` référence `script/images_to_3dgs.sh` au lieu de `scripts/images_to_3dgs.sh`.
- Quatre tests `test_run_pipeline` liés au verrou `.download.lock` ont aussi échoué dans un environnement sans numpy.

## Viewer (`tools/viewer`)
- Tests Node (pas `unittest`) : `cd tools/viewer && node --test test_*.mjs`.
- Prérequis : `georef.js` importe `proj4` sans `package.json`. Installer `proj4` hors dépôt (`npm i --prefix /tmp/proj4mod proj4`), puis lier `tools/viewer/node_modules` vers `/tmp/proj4mod/node_modules` et supprimer le lien après les tests.
- Syntaxe seule : `node --check html/viewer/colmap.js`.
- Caméras COLMAP : le modèle `17` (`EQUIRECTANGULAR`, 2 paramètres `width, height`) est écrit par `tools/sfm/colmap_split_blocks.py` et lu par `html/viewer/colmap.js`. Garder les deux synchronisés.
- Les tests ne vérifient pas le rendu réel. Pour une modif visuelle, lancer le viewer dans un navigateur (Playwright dispo) avant de conclure.

## macOS : HLOC et OpenMP
- HLOC s'installe avec `install/colmap/install-hloc.sh` (contraintes : torch/torchvision figés, `numpy<2`, pins dans `environment/macos/conda_env.yml`). Ne pas installer hloc à la main avec pip : il réécrit numpy/torch et casse les imports (`torchvision::nms`, numpy 2.x).
- LiDAR macOS : même env PDAL que Slurm, défini par `environment/ign.slurm/pdal_env.yml`. Créer une fois : `conda env create --yes --name lidar-pdal --file $GIT_ROOT/environment/ign.slurm/pdal_env.yml`, puis `conda activate lidar-pdal` et lancer `python $GIT_ROOT/tools/lidar/fetch_ign_lidar.py ...`. `tools/lidar/pdal_runtime.py` crée aussi cet env automatiquement dans `~/.cache/colmap_to_gs/lidar-pdal` pour les sous-processus PDAL.
- macOS : plusieurs `libomp.dylib` (torch, pycolmap, conda) provoquent un SIGSEGV dans `hloc_reconstruct.py`. Correctif : `install-hloc.sh` remplace les `*/.dylibs/libomp*.dylib` pip par des liens vers `$CONDA_PREFIX/lib/libomp.dylib`. `KMP_DUPLICATE_LIB_OK` ne fait que masquer le problème. Un venv ou un conteneur ne règle rien : hloc importe torch et pycolmap dans le même processus.
- HLOC ne sélectionne que `cuda` ou `cpu` (jamais `mps`). Les runs Mac sont donc CPU. Pour le GPU, passer par Slurm (`scripts/launch_videos_to_3dgs.sh CONFIG`, variables `SLURM_*`).
- Vérifier le backend réel : `python -c "import torch; print(torch.cuda.is_available(), torch.backends.mps.is_available())"`.

## Bash : `set -u` et tableaux vides
Le bash 3.2 de macOS échoue sur `"${arr[@]}"` quand `arr` est vide (`unbound variable`). Utiliser `${arr[@]+"${arr[@]}"}`. Cas présents : `videos_to_3dgs.sh`, `tools/sfm/backends/*.sh`.

## Densification MVS (`COLMAP_USE_MVS`, défaut `false`)
- Clés : `COLMAP_USE_MVS`, `MVS_MAX_IMAGE_SIZE` (2000), `MVS_GPU_INDEX` (0) dans `config/defaults/sfm.default.sh`.
- Script : `tools/sfm/colmap/mvs_densify.py` (image_undistorter, patch_match_stereo, stereo_fusion). Nécessite un COLMAP compilé avec CUDA : à lancer sur Slurm, pas sur Mac.
- Le sparse dense est `sfm/mvs/sparse/0` ; `run_standard_images.sh` et `scripts/launch_images_to_3dgs.sh` pointent dessus quand activé. Incompatible avec `GEOREF_GPX_FILE` (refus explicite).
- Les points denses reprennent le track du point sparse le plus proche (scipy `cKDTree`), pour que le découpage par tracks fonctionne. Approximatif si `SPLIT_NUM_BLOCKS > 1`.
- Format `points3D.bin` : lecteur/écrivain dans `mvs_densify.py` (`read_points3d`, `write_points3d`). Tracks = tableau `(k, 2)` uint32 `(image_id, point2D_idx)`.

## Pièges généraux
- Commits : exclure les `.pyc` (suivis dans le dépôt, modifiés à chaque lancement de tests). Ajouter les fichiers par chemin explicite (`git add <fichiers>`), jamais `git add -A` ni `git add .`.
- `SFM_FILTER_DENSITY` est basé sur les tracks ; combinaison avec MVS non testée.
- `colmap_split_blocks.py` ignore ou refuse les points sans track.
- `resume.py` valide le sparse et doit être appelé après chaque modification de `sparse/0`.

## Lancer les tests
Depuis la racine du dépôt, venv activé. Avant de lancer les suites, vérifier les imports :

```bash
python -c "import numpy, scipy, plyfile, tqdm, cv2, PIL, pyproj" && echo "imports OK"
```

Sans ces imports, attendre 2 erreurs (`ModuleNotFoundError: numpy` ou `pyproj`) dans `test_run_panoramax.py` et `tools/panoramax` (`test_download`, `test_filter_matches_gps`). Ne pas les traiter comme une régression du code : les corriger en installant les paquets, puis relancer.

Tous les tests sont dans `tests/`, en miroir de `tools/` : `tests/<domaine>/test_X.py` teste `tools/<domaine>/X.py` (voir `tests/README.md`).

```bash
PYTHON_BIN=python tests/run_all.sh                 # tout, séquentiel
PYTHON_BIN=python tests/run_all.sh --phase masks   # une phase (tests/masks)
python tests/libs/test_pipeline.py -k <nom>        # ciblé
python -m unittest discover -s tests/panoramax -p 'test_*.py'   # panoramax
python -m unittest discover -s tests/masks -p 'test_*.py'       # masques
```

Les tests écrivent des `__pycache__` suivis par git : vérifier l'état avant et après les tests et ne restaurer que les fichiers modifiés par ses propres tests, sans écraser d'éventuelles modifications préexistantes.

## Validation de configuration
- `validate_sfm_geometry_config` (`tools/libs/scripts/pipeline_config.sh`) et `validate_config` (`tools/libs/common.sh`).
- Les `SFM_SP_*` ont toujours une valeur par défaut (`config/defaults/sfm.default.sh`) : ne jamais les contrôler comme « absentes » pour un autre backend. Les contrôles « ne s'applique qu'à spirula » ont été retirés (commit `c808dfd`).
- Vérification sans calcul : `bash "$GIT_ROOT/tools/panoramax/validate_config.sh" config.sh slurm`.

## Environnement
- Docker : daemon non lancé par défaut. Vérifier `docker info` ; sinon `nohup dockerd >/tmp/dockerd.log 2>&1 </dev/null &`.

## Panoramas HLOC
- `COLMAP_METHOD=hloc` + `HLOC_CAMERA_MODEL=EQUIRECTANGULAR` : six vues pinhole 110° pour SfM pycolmap et entraînement ; focale fixée. Panoramax exige `PANORAMAX_PICTURE_TYPE=360`.
- L'ancien paramètre `SFM_EQUIRECTANGULAR_VIEWS` est refusé dans les nouvelles configurations ; les signatures locales où il valait `true` restent compatibles avec le mode `EQUIRECTANGULAR`. En modifiant ces réglages, préserver les contrôles de reprise `inputs.json`, `projection.json` et `hloc_policy.json`.
- GPS EXIF ou métadonnées Panoramax conservés, puis modèle recalé en ENU local. Altitudes manquantes : recalage horizontal et verticale supposant les panoramas redressés ; contrôler les résidus et `georef_stats.json`.
- Local : sans trois GPS exploitables, le modèle reste explicitement non géoréférencé ; Panoramax refuse. GPX, MVS, LiDAR et masques externes ne sont pas pris en charge dans ce mode.

## Documentation (README et `doc/`)
- Structure obligatoire, dans cet ordre : d'abord l'objectif de l'outil, du script ou du pipeline (une ou deux phrases, puis le pipeline ou le flux en bref), ensuite les entrées et sorties, la configuration, le fonctionnement interne et les notes. Les détails vont dans des sous-sections (`###`) après l'objectif, jamais avant.
- Ne pas éparpiller une même notion (ex. paramètres de téléchargement) sur plusieurs sections : une section la décrit en entier, les autres renvoient vers elle.
- Ne pas dupliquer : si une information existe déjà dans `doc/`, `examples/` ou le README d'un autre outil, mettre un lien relatif vers l'ancre existante au lieu de la recopier. Exemples : `doc/guide_utilisateur.md`, `examples/pipelines.md`, `doc/lancement_bash_slurm.md`.
- Les commandes exécutables vont dans `examples/*.md`, pas dans les README d'outils.
- Après une modification : vérifier que les liens et ancres existent, et que les blocs de code sont équilibrés.

## Conventions
- Commits : message en anglais, trailer `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`.
- Commentaires de code : seulement si nécessaires. Messages utilisateur et logs en français.
- Une correction = tests ciblés qui échouent sans le correctif.
