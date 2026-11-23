# Post-traitement et visualisation

[Retour aux exemples](README.md) · [Documentation du post-traitement](../tools/postprocess/README.md)

## Diagnostiquer les gaussiennes aberrantes

```bash
python "$GIT_ROOT/tools/postprocess/filter_splats.py" \
  --input "$DATA_ROOT/scene.splat" --output "$DATA_ROOT/diagnostic.splat" --mode diagnostic
```

Le mode diagnostic conserve les gaussiennes et produit un rapport ainsi qu'un
fichier de candidats rejetables. Les examiner avant d'utiliser `--mode apply` :
un filtre peut aussi supprimer des éléments réels. L'entrée n'est pas écrasée.

## Exporter un splat filtré avec les points COLMAP

```bash
python "$GIT_ROOT/tools/postprocess/filter_splats.py" \
  --input "$DATA_ROOT/scene.splat" --output "$DATA_ROOT/scene.clean.splat" \
  --mode off \
  --sparse-points "$DATA_ROOT/sfm/sparse/0/points3D.bin" \
  --sparse-cameras "$DATA_ROOT/sfm/sparse/0/images.bin" \
  --sparse-bbox-mode apply --sparse-bbox-scope planar --sparse-bbox-margin 0.02 \
  --sparse-bbox-quantile 0.02 \
  --sparse-support-mode apply --sparse-support-distance-factor 3.0 \
  --sparse-support-isolation-factor 3.0 --sparse-support-min-opacity 0 \
  --max-removal-fraction 0.20 --overwrite
```

`points3D.bin` et `images.bin` doivent être dans le même repère que le splat.
Le recadrage retire les gaussiennes hors de la boîte sparse (`planar` : seulement
le plan horizontal, la verticale est estimée depuis les caméras) et les
flottants isolés. Les valeurs sont celles de `config/defaults/postprocess.default.sh`.
Les sorties sont `scene.clean.splat`, `scene.clean.rejected.splat` (gaussiennes
retirées) et `scene.clean.filter.json` (rapport). Si la part retirée dépasse
`--max-removal-fraction`, le filtre est ignoré et l'entrée est conservée : examiner
le rapport avant d'augmenter cette limite. Pour seulement diagnostiquer, remplacer `--sparse-bbox-mode apply` et
`--sparse-support-mode apply` par `diagnostic`.

## Convertir un splat en 3D Tiles

```bash
python "$GIT_ROOT/tools/splat_to_3dtiles/convert.py" \
  --input "$DATA_ROOT/scene.splat" --georef-summary "$DATA_ROOT/georef_summary.json" \
  --output-dir "$DATA_ROOT/scene_tiles" --no-open-inspector
```

Le splat doit être en ENU local et le résumé de géoréférencement doit provenir
du même traitement. Adapter les entrées et choisir un dossier de sortie qui
n'existe pas. Node.js et le convertisseur externe sont nécessaires ; pour
leurs prérequis et la visualisation, consulter la
[documentation 3D Tiles](../tools/splat_to_3dtiles/README.md).
