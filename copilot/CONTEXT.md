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

Sans `scipy`, `plyfile`, `cv2`, `PIL` : les suites échouent avec `ModuleNotFoundError`, ce ne sont pas des bugs du code.

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
- `c808dfd` : ignorer les défauts `SFM_SP_*` quand `COLMAP_METHOD` n'est pas `spirula` (erreur « ne s'appliquent qu'à COLMAP_METHOD=spirula » avec hloc).
