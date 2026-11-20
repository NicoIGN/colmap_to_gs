# Tester le filtre de densité sparse (SFM_FILTER_DENSITY)

[Retour aux exemples](README.md) · [Documentation du filtre](../tools/preprocess/README.md)

Le filtre retire les points COLMAP isolés et lointains. Il lit un modèle
sparse binaire (`cameras.bin`, `images.bin`, `points3D.bin`) et écrit deux
modèles : `XXX_clean` (points conservés) et `XXX_rejected` (points retirés).
L'entrée n'est pas modifiée.

## Lancer le filtre sur un dossier sparse

Depuis la racine du dépôt, avec l'environnement Python du projet actif :

```bash
export GIT_ROOT="$(git rev-parse --show-toplevel)"
export DATA_ROOT="$HOME/colmap-data"

python "$GIT_ROOT/tools/preprocess/filter_sparse_density.py" \
  --input "$DATA_ROOT/sfm/sparse/0" \
  --output "$DATA_ROOT/density_test/sparse" \
  --vote-fraction 0.75 --far-support-rate 0.25 \
  --workers 4 --verbose
```

Avec `--output "$DATA_ROOT/density_test/sparse"`, les résultats sont dans
`$DATA_ROOT/density_test/sparse_clean` et `$DATA_ROOT/density_test/sparse_rejected`.
Choisir un préfixe dont les dossiers `_clean` et `_rejected` n'existent pas encore.

Les valeurs `--vote-fraction` et `--far-support-rate` correspondent à
`SFM_DENSITY_VOTE_FRACTION` et `SFM_DENSITY_FAR_SUPPORT_RATE` (défauts 0.75 et 0.25).
Les autres options (`--neighbors`, `--min-views`, `--low-density-fraction`, etc.)
ont les défauts du script ; lancer `--help` pour la liste complète.

## Vérifier le résultat

```bash
python -m json.tool "$DATA_ROOT/density_test/sparse_clean/density_report.json" | head -40
```

Comparer le nombre de points conservés et retirés. Pour contrôler visuellement,
ouvrir les deux modèles dans COLMAP ou dans un viewer et vérifier que les points
retirés sont bien des points isolés ou lointains, pas la structure principale.

## Tester une valeur plus stricte ou plus souple

Pour comparer plusieurs réglages, utiliser un préfixe différent à chaque essai :

```bash
for vote in 0.6 0.75 0.9; do
  python "$GIT_ROOT/tools/preprocess/filter_sparse_density.py" \
    --input "$DATA_ROOT/sfm/sparse/0" \
    --output "$DATA_ROOT/density_test/vote_$vote" \
    --vote-fraction "$vote" --workers 4
done
```

Une `--vote-fraction` plus haute retire moins de points ; une valeur plus basse
retire davantage. Garder le réglage choisi dans `SFM_DENSITY_VOTE_FRACTION`.
