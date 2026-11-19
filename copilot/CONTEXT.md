# Note de contexte Copilot — colmap_to_gs

À lire en premier au démarrage d'une session. Mettre à jour après chaque changement notable.

## Projet
- Pipeline images/vidéos → 3DGS (SfM HLOC / Spirula / COLMAP / Nerfstudio, entraînement Spirula, export `.splat`).
- Point d'entrée vidéo : `scripts/videos_to_3dgs.sh <config.sh> --video <fichier> --num-frames N`.
- Config : `config/defaults/*.default.sh` (défauts, chargés en premier) puis `config.sh` utilisateur (a le dernier mot).
- Branche de travail : `review_copilot`. Pousser les changements terminés sur la branche (pas de merge direct).
- Chemins dans les commandes projet : `$GIT_ROOT/...`, pas de chemin absolu.

## Dépendances Python (venv dédié)
Un venv évite l'erreur PEP 668 (`externally-managed-environment`) et les `No module named ...` :

```bash
bash copilot/setup_env.sh                       # crée ~/.venvs/colmap_to_gs et installe tout
source ~/.venvs/colmap_to_gs/bin/activate
```

Paquets utilisés par les tests et le pipeline :
- Tests images/Spirula : `pillow numpy opencv-python-headless tqdm scipy plyfile`
- Lidar / panoramax : `pyproj laspy lazrs` (`tools/lidar/requirements.txt`, `tools/panoramax/requirements.txt`)
- HLOC / COLMAP : `pycolmap>=4.2` (`tools/lidar/requirements-colmap.txt`)

Sans `numpy`, `scipy`, `plyfile`, `cv2`, `PIL`, `tqdm` : les suites et scripts échouent avec `ModuleNotFoundError`, ce ne sont pas des bugs du code. Vérifier d'abord : `python -c "import numpy, scipy, plyfile, tqdm, cv2, PIL"`.
- Test rapide sans polluer le venv : `python3 -m venv /tmp/x && /tmp/x/bin/pip install -q numpy scipy plyfile`.
- Sur la machine de session, `numpy` n'est pas installé par défaut ; `pycolmap` et `torch` non plus.
- Sans numpy, `test_lidar_recalage_precedes_partition_and_block_jobs_check_it` (`test_run_spirula_blocks.py`) échoue, comme les 4 tests `test_run_pipeline` liés au verrou `.download.lock` et `test_validate_config` (`KMP_DUPLICATE_LIB_OK`) : échecs préexistants, vérifiés sans changements.

## Viewer (`tools/viewer`)
- Tests Node (pas `unittest`) : `cd tools/viewer && node --test test_*.mjs`.
- Syntaxe seule : `node --check html/viewer/colmap.js`.
- Caméras COLMAP : le modèle `17` (`EQUIRECTANGULAR`, 2 paramètres `width, height`) est écrit par `tools/preprocess/colmap_split_blocks.py` et lu par `html/viewer/colmap.js`. Garder les deux synchronisés.
- Les tests ne vérifient pas le rendu réel. Pour une modif visuelle, lancer le viewer dans un navigateur (Playwright dispo) avant de conclure.

## macOS : HLOC et OpenMP
- HLOC s'installe avec `tools/colmap/install-hloc.sh` (contraintes : torch/torchvision figés, `numpy<2`, pins dans `environment/macos/conda_env.yml`). Ne pas installer hloc à la main avec pip : il réécrit numpy/torch et casse les imports (`torchvision::nms`, numpy 2.x).
- macOS : plusieurs `libomp.dylib` (torch, pycolmap, conda) provoquent un SIGSEGV dans `hloc_reconstruct.py`. Correctif : `install-hloc.sh` remplace les `*/.dylibs/libomp*.dylib` pip par des liens vers `$CONDA_PREFIX/lib/libomp.dylib`. `KMP_DUPLICATE_LIB_OK` ne fait que masquer le problème. Un venv ou un conteneur ne règle rien : hloc importe torch et pycolmap dans le même processus.
- HLOC ne sélectionne que `cuda` ou `cpu` (jamais `mps`). Les runs Mac sont donc CPU. Pour le GPU, passer par Slurm (`scripts/launch_videos_to_3dgs.sh CONFIG`, variables `SLURM_*`).
- Vérifier le backend réel : `python -c "import torch; print(torch.cuda.is_available(), torch.backends.mps.is_available())"`.

## Bash : `set -u` et tableaux vides
Le bash 3.2 de macOS échoue sur `"${arr[@]}"` quand `arr` est vide (`unbound variable`). Utiliser `${arr[@]+"${arr[@]}"}`. Cas présents : `videos_to_3dgs.sh`, `tools/images_to_colmap/reconstruct.sh`.

## Densification MVS (`COLMAP_USE_MVS`, défaut `false`)
- Clés : `COLMAP_USE_MVS`, `MVS_MAX_IMAGE_SIZE` (2000), `MVS_GPU_INDEX` (0) dans `config/defaults/sfm.default.sh`.
- Script : `tools/images_to_colmap/mvs_densify.py` (image_undistorter, patch_match_stereo, stereo_fusion). Nécessite un COLMAP compilé avec CUDA : à lancer sur Slurm, pas sur Mac.
- Le sparse dense est `sfm/mvs/sparse/0` ; `run.sh` et `scripts/images_to_3dgs.sh` pointent dessus quand activé. Incompatible avec `GEOREF_GPX_FILE` (refus explicite).
- Les points denses reprennent le track du point sparse le plus proche (scipy `cKDTree`), pour que le découpage par tracks fonctionne. Approximatif si `SPLIT_NUM_BLOCKS > 1`.
- Format `points3D.bin` : lecteur/écrivain dans `mvs_densify.py` (`read_points3d`, `write_points3d`). Tracks = tableau `(k, 2)` uint32 `(image_id, point2D_idx)`.

## Pièges généraux
- `SFM_FILTER_DENSITY` est basé sur les tracks ; combinaison avec MVS non testée.
- `colmap_split_blocks.py` ignore ou refuse les points sans track.
- `resume.py` valide le sparse et doit être appelé après chaque modification de `sparse/0`.

## Lancer les tests
Depuis la racine du dépôt, venv activé :

```bash
python -m unittest tools/images_to_splat/test_pipeline.py tools/panoramax/test_validate_config.py tools/libs/scripts/test_run_spirula_blocks.py   # ~4 min, 109 tests
python -m unittest tools/images_to_splat/test_pipeline.py -k <nom>        # ciblé
python -m unittest discover -s tools/panoramax -p 'test_*.py'             # panoramax
python -m unittest discover -s tools/segmentation_masks -p 'test_*.py'    # masques
```

Les tests écrivent des `__pycache__` suivis par git : avant commit, faire `git checkout -- '*__pycache__*'` ou ne pas les ajouter.

## Validation de configuration
- `validate_sfm_geometry_config` (`tools/libs/scripts/pipeline_config.sh`) et `validate_config` (`tools/images_to_splat/common.sh`).
- Les `SFM_SP_*` ont toujours une valeur par défaut (`config/defaults/sfm.default.sh`) : ne jamais les contrôler comme « absentes » pour un autre backend. Les contrôles « ne s'applique qu'à spirula » ont été retirés (commit `c808dfd`).
- Vérification sans calcul : `bash tools/panoramax/validate_config.sh config.sh slurm`.

## Environnement
- Docker : daemon non lancé par défaut. Vérifier `docker info` ; sinon `nohup dockerd >/tmp/dockerd.log 2>&1 </dev/null &`.
- Pas de dépôt local sur la machine de session : cloner avec `gh repo clone NicoIGN/colmap_to_gs -- -b review_copilot`.
- Les chemins de l'utilisateur sont sous `/Users/nbellaiche/DATA/dev/gsplat/...` (macOS) ; ne pas les mettre en dur, utiliser `$GIT_ROOT`.

## Conventions
- Commits : message en anglais, trailer `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`.
- Commentaires de code : seulement si nécessaires. Messages utilisateur et logs en français.
- Une correction = tests ciblés qui échouent sans le correctif.

## Historique récent
- `c808dfd` : ignorer les défauts `SFM_SP_*` quand `COLMAP_METHOD` n'est pas `spirula`.
- `3189c77` : création de `copilot/` (cette note et `setup_env.sh`).
- `d211ae8` : h5py ajouté aux deux `conda_env.yml` (conda-forge recommandé plutôt que pip).
- `1256a17` : dédoublonnage de libomp sur macOS dans `install-hloc.sh` (confirmé fonctionnel).
- `4ff510e`, `f1f12ae` : correctifs `set -u` sur tableaux vides (bash 3.2).
- `1399b67` : densification MVS optionnelle (`COLMAP_USE_MVS`, défaut `false`), non testée sur Slurm.
