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
