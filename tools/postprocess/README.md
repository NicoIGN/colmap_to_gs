# Traitement des résultats

Ces outils proviennent de `python/` et de `scripts/`. Les pipelines utilisent
maintenant directement leurs chemins sous `tools/postprocess/`.

| Outil | Fonction |
|---|---|
| `export_splat_to_ply.sh` | Export Nerfstudio via `ns-export` |
| `clean_ply.sh` et `clean_ply.py` | Nettoyage d'un PLY avec les points COLMAP |
| `export_ply.py` | Nettoyage, retour au repère monde et export `.splat` |
| `merge_splats.py` | Fusion de résultats déjà alignés par sélection dans les voxels |
| `colmap_to_gpkg.py` | Export de données COLMAP en GeoPackage (GDAL requis) |

Exemples depuis la racine du dépôt :

```bash
python3 tools/postprocess/export_ply.py --help
bash tools/postprocess/clean_ply.sh --help
```

Les arguments ne changent pas. `scripts/run.sh` appelle les lanceurs Nerfstudio ;
`scripts/run_spirula_block.sh` utilise `export_ply.py` par défaut, tout en conservant
la surcharge `--export-script` / `EXPORT_SCRIPT`.

La conversion `.splat` vers 3D Tiles conserve son dossier autonome
[`../splat_to_3dtiles/`](../splat_to_3dtiles/README.md).
