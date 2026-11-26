# Images, vidéos et COLMAP

[Retour aux exemples](README.md) · [Images vers COLMAP](../tools/sfm/README.md) · [Images vers 3DGS](../tools/images_to_splat/README.md)

## Extraire des images nettes d'une vidéo

```bash
python "$GIT_ROOT/tools/preprocess/prepare_video_frames.py" \
  --video "$DATA_ROOT/video.mp4" --out "$DATA_ROOT/frames" --num-frames 200
```

Adapter la vidéo, le dossier et le nombre d'images. L'extracteur nécessite
OpenCV, NumPy et tqdm ; le nombre obtenu peut être inférieur à celui demandé.

## Préparer un modèle COLMAP

Après avoir préparé un fichier `config-images.sh` avec `SFM_IMAGES_DIR`,
`OUTPUT_DIR` et la méthode `COLMAP_METHOD` souhaitée :

```bash
bash "$GIT_ROOT/tools/sfm/sfm_run.sh" "$DATA_ROOT/config-images.sh"
```

Cette commande prépare le modèle sans entraîner de 3DGS. Pour produire aussi
un splat avec une configuration compatible :

```bash
bash "$GIT_ROOT/scripts/images_to_3dgs.sh" "$DATA_ROOT/config-images.sh"
```

Voir les [exemples des pipelines images et vidéo, en direct ou sur Slurm](pipelines.md).

Pour des images 360 équirectangulaires avec HLOC, définir
`COLMAP_METHOD=hloc` et `HLOC_CAMERA_MODEL=EQUIRECTANGULAR`. Les vues
perspectives sont reconstruites en `PINHOLE`. Voir les prérequis et les
restrictions de ce mode dans la
[documentation du pipeline images](../tools/images_to_splat/README.md).
