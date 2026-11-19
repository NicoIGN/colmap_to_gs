# Post-traitement et visualisation

[Retour aux exemples](README.md) · [Documentation du post-traitement](../tools/postprocess/README.md)

## Diagnostiquer les gaussiennes aberrantes

```bash
python "$GIT_ROOT/tools/postprocess/filter_splats.py" \
  --input scene.splat --output diagnostic.splat --mode diagnostic
```

Le mode diagnostic conserve les gaussiennes et produit un rapport ainsi qu'un
fichier de candidats rejetables. Les examiner avant d'utiliser `--mode apply` :
un filtre peut aussi supprimer des éléments réels. L'entrée n'est pas écrasée.

## Convertir un splat en 3D Tiles

```bash
python "$GIT_ROOT/tools/splat_to_3dtiles/convert.py" \
  --input scene.splat --georef-summary georef_summary.json \
  --output-dir scene_tiles --no-open-inspector
```

Le splat doit être en ENU local et le résumé de géoréférencement doit provenir
du même traitement. Adapter les entrées et choisir un dossier de sortie qui
n'existe pas. Node.js et le convertisseur externe sont nécessaires ; pour
leurs prérequis et la visualisation, consulter la
[documentation 3D Tiles](../tools/splat_to_3dtiles/README.md).
