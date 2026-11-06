# Préparation des données

Ces outils autonomes étaient dans `python/`. Depuis la racine du dépôt :

```bash
python3 tools/preprocess/images_to_colmap.py --help
```

| Outil | Fonction |
|---|---|
| `extract_frames_from_video.py` | Extraction d'images depuis une vidéo |
| `image_info.py` | Inspection d'une image et de ses informations GPS |
| `images_to_colmap.py` | Reconstruction COLMAP depuis des images |
| `georef_colmap_from_gpx.py` | Recalage d'un modèle COLMAP sur une trace GPX |
| `colmap_split_blocks.py` | Découpage d'un modèle COLMAP en blocs |

Les arguments et formats ne changent pas ; seul le chemin des scripts change.
Les dépendances restent celles de l'environnement du projet, et les outils
externes requis dépendent de la commande (COLMAP, ffmpeg, exiftool, etc.).

Les composants internes de Nerfstudio `colmap_to_transforms.py` et
`estimate_planes.py` restent dans `python/` et sont appelés par `scripts/run.sh`.

### Découpage et entraînement Spirula par blocs

Depuis n'importe quel dossier, avec l'environnement Python du projet activé :

```bash
DIR=/chemin/vers/panoramax NUM_BLOCKS=4 \
  bash /chemin/vers/colmap_to_gs/scripts/run_spirula_blocks.sh
```

Le lanceur réutilise le SfM existant `DIR/sfm-NUM_ITER` s'il est complet ;
sinon il lance la reconstruction Panoramax. Il découpe ensuite `sparse/0`
avec ce script dans `blocks-NUM_BLOCKS/`, puis entraîne chaque bloc
indépendamment dans `DIR/gs-NUM_ITER-blocks-NUM_BLOCKS/block_NNN/`.
Le manifeste et les blocs COLMAP complets sont réutilisés à la relance.
Les sorties de découpage partielles ou incohérentes ne sont jamais supprimées
automatiquement. `NUM_BLOCKS`, `NUM_ITER`, `CAP_MAX` et les paramètres SfM
sont configurables par variables d'environnement ; voir `--help`.

`CAP_MAX` s'applique à chaque bloc séparément : le plafond cumulé peut donc
atteindre `NUM_BLOCKS × CAP_MAX` Gaussians.
