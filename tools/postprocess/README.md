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

### Nettoyage de l'export Spirula

Le filtre de taille utilise par défaut un seuil relatif : 10 fois la médiane
des plus grands demi-axes des splats (`--max-scale-factor 10`), plutôt qu'un
seuil absolu de 0,35 unité monde. La marge de BBOX automatique dépend de
l'espacement des points COLMAP ; ces critères restent invariants à un changement
d'unité ou d'échelle uniforme. `MAX_WORLD_SCALE` permet encore d'imposer
explicitement une limite absolue au lanceur.

Les filtres sont évalués successivement : attributs invalides, BBOX, taille,
opacité. Pour chacun, le pourcentage est calculé sur les splats **restants** :
plus de 10 % entraîne un avertissement ; plus de 20 % ignore **ce filtre
seulement**, puis poursuit avec le suivant. Exactement 20 % reste appliqué.
La limite est réglable via `--max-removal-fraction` (défaut : `0.2`).
Si le filtre des attributs invalides doit être ignoré, l'export échoue
explicitement sans remplacer le fichier de sortie, plutôt qu'écrire des
positions ou rotations invalides.

Pour réexporter un entraînement existant, relancer la même commande
`run_spirula_block.sh` **sans `--clean`** : le checkpoint final est réutilisé,
sans nouvel entraînement, et le `.splat` est remplacé.

```bash
python3 -m unittest discover -s tools/postprocess -p 'test_*.py'
```

La conversion `.splat` vers 3D Tiles conserve son dossier autonome
[`../splat_to_3dtiles/`](../splat_to_3dtiles/README.md).
