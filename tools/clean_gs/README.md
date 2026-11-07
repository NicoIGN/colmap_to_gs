# Nettoyage multivues du ciel inspiré de Clean-GS

Cette adaptation expérimentale utilise le parseur COLMAP du projet
[Clean-GS](https://github.com/smlab-niser/clean-gs) (MIT, parseur gsplat
Apache-2.0) et la projection/rasterisation gsplat. Contrairement à Clean-GS
officiel, elle ne sélectionne pas trois vues par Pareto et ne filtre pas un
objet segmenté : elle analyse **toutes les vues enregistrées**, segmente le
ciel et ne rejette que les centres projetés avec au moins deux votes de ciel
et aucun vote de premier plan. Les splats non observés ou aux pixels incertains
sont conservés. Une erreur de segmentation peut masquer des détails utiles.
La visibilité est celle de projection de gsplat (frustum/rayon positif), pas
un test d'occlusion exacte. Cela n'enlève pas nécessairement les flotteurs
qui se projettent devant les bâtiments ou la végétation.

## Installation

Sur Linux avec GPU NVIDIA, CUDA et `nvcc`, installer Clean-GS selon ses
instructions, à la révision de référence de l'API
`78a39e941c14df5857903b1112040d117f230ac4`, puis installer `transformers`
dans son environnement Python. Un environnement séparé est possible :
`CLEANGS_PYTHON` doit alors désigner son interpréteur et contenir également
numpy, scipy, tqdm et plyfile. Les versions et compatibilités CUDA doivent être
vérifiées selon le cluster. Exemple après installation des prérequis CUDA et
PyTorch indiqués par Clean-GS :

```bash
git clone https://github.com/smlab-niser/clean-gs.git
git -C clean-gs checkout 78a39e941c14df5857903b1112040d117f230ac4
python -m pip install -e ./clean-gs
python -m pip install 'transformers==4.46.3' pyproj
```

Préparer localement un modèle Hugging Face de segmentation ADE20K contenant
une classe nommée `sky`, par exemple
`nvidia/segformer-b0-finetuned-ade-512-512`. Télécharger ses fichiers avec
`huggingface-cli download nvidia/segformer-b0-finetuned-ade-512-512 --local-dir /chemin/segformer`
dans un environnement autorisé à accéder au réseau. L'inférence utilise
uniquement ces fichiers locaux : aucune image n'est envoyée au service.

## Utilisation autonome

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
Examiner les masques et les rejets avant `--mode apply` ; par défaut plus de
20 % de suppressions appliquées sont refusées. Ce filtre n'est pas combiné
automatiquement aux filtres contextuel et bbox.

Le format `.splat` ne contient ni les harmoniques sphériques d'ordre supérieur
ni l'apparence originale complète ; les enregistrements conservés sont copiés
à l'identique. La méthode peut être coûteuse : chaque caméra est rasterisée
sur GPU. Elle n'est pas adaptée au Mac sans CUDA.
