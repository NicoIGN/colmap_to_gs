# Nettoyage multivues du ciel inspiré de Clean-GS

## 1. Objectif

Filtrer les splats projetés dans le ciel à partir des masques générés pour toutes les vues COLMAP.

Exemple d’invocation :

```bash
python tools/clean_gs/clean_sky.py \
  --input /chemin/scene.unfiltered.splat \
  --output /chemin/scene.cleangs.splat \
  --scene /chemin/sfm-12000 \
  --model /chemin/segformer --mode diagnostic
```

Le mode `diagnostic` est utilisé par défaut. Examiner les masques et les rejets avant de lancer `apply`.

## 2. Entrées et sorties

```bash
python tools/clean_gs/clean_sky.py \
  --input /chemin/scene.unfiltered.splat \
  --output /chemin/scene.cleangs.splat \
  --scene /chemin/sfm-12000 \
  --model /chemin/segformer --mode diagnostic
```

Le dossier scène doit contenir `images/` et `sparse/0/`, dans le même repère
que le SPLAT. Sont supportées les caméras COLMAP perspective PINHOLE,
SIMPLE_PINHOLE, SIMPLE_RADIAL, RADIAL et OPENCV ; les images sont corrigées
de leur distorsion avant segmentation et projection. Les masques PNG du ciel,
le SPLAT des rejets et le rapport JSON sont enregistrés à côté de la sortie.
Par défaut, plus de 20 % de suppressions appliquées sont refusées. Ce filtre
n'est pas combiné automatiquement aux filtres contextuel et bbox.

Options du script `clean_sky.py` :

- `--input PATH`, `--output PATH`, `--scene PATH`, `--model PATH` : entrées requises ; le modèle de segmentation doit déjà être téléchargé localement.
- `--mode diagnostic|apply` : `diagnostic` par défaut ; `apply` applique les rejets.
- `--sky-confidence FLOAT` : seuil du masque ciel, `0.9` par défaut.
- `--min-views INT` : votes de ciel minimum, `2` par défaut.
- `--max-removal-fraction FLOAT` : fraction maximale de suppressions appliquées, `0.2` par défaut.
- `--overwrite` : autorise le remplacement des sorties existantes.

## 3. Configuration et utilisation

L'installateur prépare un environnement **séparé de `gsplat`**, la révision
Clean-GS `78a39e941c14df5857903b1112040d117f230ac4` avec son verrou de dépendances,
PyTorch CUDA 12.8, transformers, pyproj et le modèle SegFormer local :

```bash
bash "$GIT_ROOT/tools/clean_gs/install.sh" \
  --install-dir /shared/home/NBellaiche/envs/cleangs \
  --python python3
```

Prérequis : Linux, Python **3.11 ou 3.12** avec `venv`, git, accès réseau
(GitHub, PyPI, PyTorch et Hugging Face), compilateur C++ pour les dépendances
natives et plusieurs Go libres. Le dossier choisi doit être accessible depuis
les nœuds GPU. Sans options, l'installation utilise
`$HOME/.local/share/colmap-to-gs/cleangs`. `CLEANGS_INSTALL_DIR` et
`CLEANGS_INSTALL_PYTHON` sont les alternatives aux arguments correspondants.
Une relance réutilise le dossier ; une source modifiée ou à une autre révision
est refusée. Aucun environnement existant n'est supprimé.

Options de l'installateur : `--install-dir PATH` définit le dossier
d'installation (défaut `$HOME/.local/share/colmap-to-gs/cleangs`) ;
`--python PATH` choisit Python (défaut `python3`) ; `--check-gpu` compile et
teste gsplat sur un GPU NVIDIA à la fin de l'installation.

Le GPU n'est pas obligatoire sur le nœud d'installation. **Avant utilisation**,
charger CUDA 12.8 (`nvcc`) et un compilateur C++ dans une allocation GPU NVIDIA,
puis vérifier la compilation effective et un petit rendu :

```bash
/shared/home/NBellaiche/envs/cleangs/source/.venv/bin/python \
  "$GIT_ROOT/tools/clean_gs/check_runtime.py"
```

L'option `--check-gpu` fait cette vérification à la fin de l'installation si
elle est lancée directement sur un nœud GPU. Le script ne peut pas installer
le pilote NVIDIA ni les modules CUDA du cluster : ils doivent être fournis
par le site. Le cache de compilation gsplat doit rester accessible en écriture.
Les images ne sont jamais envoyées à un service ; seule l'installation
télécharge les poids, l'inférence les charge ensuite sans accès réseau.


Variables à ajouter dans la [configuration Panoramax](../panoramax/README.md) :


L'installateur affiche les chemins exacts à reporter dans `config.sh` :

```bash
POSTPROCESS_CLEANER="cleangs"  # contextual : conserver le nettoyage existant
CLEANGS_PYTHON="/shared/home/NBellaiche/envs/cleangs/source/.venv/bin/python"
CLEANGS_MODEL="/shared/home/NBellaiche/envs/cleangs/models/segformer"
CLEANGS_MODE="diagnostic"  # apply après inspection des masques et rejets
CLEANGS_MIN_VIEWS="2"
CLEANGS_SKY_CONFIDENCE="0.9"
CLEANGS_MAX_REMOVAL_FRACTION="0.2"
```

`PYTHON_BIN` et `SLURM_CONDA_ENV` peuvent rester ceux du pipeline habituel :
seul le nettoyage utilise `CLEANGS_PYTHON`. En mode `cleangs`, la fusion Slurm
réserve automatiquement un GPU. Si les modules CUDA/C++ ne sont pas hérités,
ajouter dans la config :

```bash
SLURM_ENV_SETUP="/chemin/absolu/vers/setup-cuda.sh"
```

Ce script Bash de confiance doit charger les modules CUDA 12.8 et compilateur
du **site** (leurs noms dépendent du cluster). Il est chargé avant Conda.
Pour revenir à l'ancien nettoyage, mettre `POSTPROCESS_CLEANER="contextual"`.
Les options `SPLAT_FILTER_*` et `SPLAT_SPARSE_BBOX_*` n'agissent pas en mode
`cleangs`. Le diagnostic produit le `.splat` final sans suppressions ;
`CLEANGS_MODE="apply"` active le nettoyage avec la limite indiquée.

Valeurs de configuration : `CLEANGS_PYTHON` et `CLEANGS_MODEL` sont les
chemins de l'environnement et du modèle ; `CLEANGS_MODE` accepte `diagnostic`
(défaut) ou `apply` ; `CLEANGS_MIN_VIEWS` vaut `2` par défaut ;
`CLEANGS_SKY_CONFIDENCE` vaut `0.9` par défaut ;
`CLEANGS_MAX_REMOVAL_FRACTION` vaut `0.2` par défaut.

## 4. Fonctionnement internes

Cette adaptation expérimentale utilise le parseur COLMAP du projet
[Clean-GS](https://github.com/smlab-niser/clean-gs) (MIT, parseur gsplat
Apache-2.0) et la projection/rasterisation gsplat. Elle analyse toutes les
vues enregistrées, segmente le ciel et rejette seulement les centres projetés
avec au moins deux votes de ciel et aucun vote de premier plan. Elle ne
sélectionne pas trois vues par Pareto et ne filtre pas un objet segmenté.
Les splats non observés ou aux pixels incertains sont conservés. La visibilité
est déterminée par la projection gsplat (frustum et rayon positif), sans test
d'occlusion exacte ; les flotteurs devant les bâtiments ou la végétation ne
sont donc pas nécessairement supprimés.

## 5. Notes et informations complémentaires

Le format `.splat` ne contient ni les harmoniques sphériques d'ordre supérieur
ni l'apparence originale complète ; les enregistrements conservés sont copiés
à l'identique. La méthode peut être coûteuse : chaque caméra est rasterisée
sur GPU. Une erreur de segmentation peut masquer des détails utiles. Elle
n'enlève pas nécessairement les flotteurs qui se projettent devant les
bâtiments ou la végétation. Elle n'est pas adaptée au Mac sans CUDA.
