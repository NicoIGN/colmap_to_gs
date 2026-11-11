# Traitement des résultats

[Retour à la documentation générale](../../doc/guide_utilisateur.md)

## 1. Objectif

Outils d’export, de nettoyage, de filtrage contextuel et de fusion des résultats Gaussian Splatting.

Exemple d’invocation :

```bash
python3 tools/postprocess/export_ply.py --help
```

## 2. Entrées et sorties

| Outil | Fonction |
|---|---|
| `export_splat_to_ply.sh` | Export [Nerfstudio](../../doc/nerfstudio.md) via `ns-export` |
| `clean_ply.sh` et `clean_ply.py` | Nettoyage d'un PLY avec les points COLMAP |
| `export_ply.py` | Nettoyage, retour au repère monde et export `.splat` |
| `export_ply_legacy.py` | Exporteur alternatif : filtre par BBOX et seuil global de taille |
| `filter_splats.py` | Diagnostic/nettoyage contextuel d'un `.splat`, sans seuil de distance absolu |
| `merge_splats.py` | Fusion de résultats déjà alignés par sélection dans les voxels |
| `colmap_to_gpkg.py` | Export de données COLMAP en GeoPackage (GDAL requis) |

Exemples depuis la racine du dépôt :

```bash
python3 tools/postprocess/export_ply.py --help
bash tools/postprocess/clean_ply.sh --help
```

## 3. Configuration et utilisation

- `merge_splats.py` : fusionne des blocs déjà alignés sans modifier leurs coordonnées.
- `--strength` : multiplie la taille médiane des grands demi-axes pour fixer la
  taille des voxels ; `0.5` est prudent, `1` normal, `2` plus agressif.
- Sélection : dans chaque voxel, conserve les splats du bloc au meilleur score moyen.
- Clés de voxels : trois coordonnées int64, soit 24 octets par splat ;
  prévoir leur coût mémoire/disque lors du tri.
- Validation : les géométries non finies et les indices hors plage int64 restent refusés.
- Pour modifier la fusion, la relancer sans réentraîner les blocs.

- `tools/libs/scripts/run.sh` : appelle les lanceurs Nerfstudio.
- `tools/libs/scripts/run_spirula_block.sh` : utilise `export_ply.py` par défaut ;
  surcharge possible avec `--export-script` / `EXPORT_SCRIPT`.

## 4. Fonctionnement interne

Les scripts de `tools/postprocess/` exportent les modèles, fusionnent les blocs
et proposent des filtres pour retirer certains splats indésirables.

- `export_ply.py` prépare l'export `.splat` ; il retire les valeurs invalides,
  mais ne supprime pas automatiquement les splats éloignés des points COLMAP.
- `filter_splats.py` repère les splats anormalement grands, situés en bordure
  de scène et sans support clair sur une surface voisine.
- Ce filtre est une heuristique, pas une reconnaissance d'objets ni une mesure
  directe de ce qui est visible dans les photos. Il peut manquer des parasites
  ou proposer des rejets incorrects, notamment sur des surfaces courbes ou des
  scènes peu denses.
- `merge_splats.py` rassemble les blocs déjà alignés et choisit les splats à
  conserver dans les zones où ils se chevauchent.

Le mode diagnostic permet d'examiner les candidats avant leur suppression.

Pour un `.splat` fusionné existant, sans réentraîner :

```bash
python3 tools/postprocess/filter_splats.py \
  --input scene.splat --output essai.splat --mode diagnostic
```

Le fichier `essai.splat` conserve tous les enregistrements à l'identique.
`essai.rejected.splat` contient les candidats au rejet (vide si aucun candidat),
et `essai.filter.json` contient les compteurs et paramètres. Visualiser ces
candidats séparément avant d'utiliser `--mode apply` :

```bash
python3 tools/postprocess/filter_splats.py \
  --input scene.splat --output essai-nettoye.splat --mode apply
```

- L'entrée n'est jamais écrasée.
- `--size-factor` : rapport de taille local ; défaut `10`.
- `--neighbors`, `--reference-limit`, `--seed`, `--batch-size` : pilotent le
  voisinage et le coût mémoire.
- `--mode off` : copie simplement l'entrée.
- Plus de 20 % de rejets en mode `apply` provoque une erreur sans remplacer les
  sorties ; examiner le diagnostic avant de modifier `--max-removal-fraction`.
- Un rejet de tous les splats est toujours refusé ; le filtre ne désactive pas
  silencieusement une règle trop destructive.

- `--sparse-points sparse/0/points3D.bin` : ajoute un recadrage sur la bbox des
  points COLMAP, dans le même repère monde que le `.splat`.
- Bbox sparse : orientée sur les axes principaux, bornée par
  `--sparse-bbox-quantile` (défaut `0.005` de chaque côté), puis élargie de
  `--sparse-bbox-margin` (défaut `0.05`) × son plus grand côté ; test invariant
  par rotation, translation et échelle.
- Test bbox : seul le centre du splat est testé.
- `--sparse-bbox-mode` : valeurs `apply` (défaut), `diagnostic` ou `off` ;
  indépendant de `--mode`.
- Rejets bbox : les splats hors bbox sont exclus du filtre contextuel et
  comptés dans `sparse_bbox.outside_sparse_bbox` du JSON.
- Si aucun centre n'est dans la boîte, le filtre échoue (repère probablement différent).

- `--sparse-support-mode` : filtre expérimental des amas flottants ; valeurs
  `off` (défaut), `diagnostic` ou `apply`.
- Positions testées : neuf points sur le plan central du splat, jusqu'à une
  échelle gaussienne le long de ses deux plus grands axes.
- `--sparse-support-distance-factor` : distance sparse maximale en multiples
  de l'espacement local ; facteur `3`, espacement estimé par la distance au
  huitième voisin divisée par `sqrt(8)`.
- `--sparse-support-min-fraction` : fraction minimale de positions supportées ;
  défaut `0.5`.
- Invariance : les rapports restent invariants à l'échelle, rotation et translation.
- Le test ne dépend ni de la couleur ni de la hauteur et préserve les grandes
  surfaces correctement couvertes par le sparse ; il peut néanmoins retirer
  des routes, toitures ou végétations peu couvertes.
- Examiner les rejets avant `--sparse-support-mode apply`.
- Ce mode est indépendant du filtre contextuel et de la bbox ; tous les rejets
  appliqués partagent la limite de sécurité de 20 %.
- Le JSON contient un compteur distinct `sparse_support.unsupported`.

- [Pipeline Panoramax](../panoramax/README.md) : **les blocs sont exportés sans filtre géométrique** ;
  le filtre contextuel est évalué après fusion, sur l'ensemble de la scène, afin
  que les frontières internes des blocs ne soient pas traitées comme des bords de chantier.
- `POSTPROCESS_FILTER_MODE="diagnostic"` : défaut ; valeurs alternatives
  `"apply"` ou `"off"`.
- `POSTPROCESS_FILTER_SIZE_FACTOR="10"` : règle le rapport de taille locale ;
  valeur de configuration `10`.
- Sorties : fusion avant filtre dans `NOM.unfiltered.splat`, candidats dans
  `NOM.rejected.splat`, résumé dans `NOM.filter.json`.
- En mode diagnostic, `NOM.splat` est identique à la fusion non filtrée.
- La fusion conserve ses propres règles de déduplication.

- `export_ply.py` autonome : utilise le mode diagnostic par défaut
  (`--filter-mode diagnostic|apply|off`) sur la scène fournie ; produit les
  mêmes diagnostics et conserve le PLY d'entrée.
- Arguments `--points`, `--dist`, `--radius-factor`, `--outlier-percent`
  et `--max-points` : acceptés mais sans effet sur les splats retenus ;
  un message le précise.
- `--max-world-scale` : refusé avec le filtre contextuel, car il impose un seuil absolu.

## 5. Notes et informations complémentaires

`export_ply_legacy.py` applique un filtre par BBOX et un seuil global de taille,
avec ses garde-fous (avertissement au-delà de 10 %, filtre ignoré au-delà de 20 %).
Pour le réutiliser explicitement :

```bash
EXPORT_SCRIPT="$PWD/tools/postprocess/export_ply_legacy.py" \
  bash tools/libs/scripts/run_spirula_block.sh --sparse-dir ... --images-dir ... --output-dir ...
```

Cet exporteur accepte `MAX_WORLD_SCALE` via le lanceur. Cette surcharge peut
retirer de grands splats de route : elle ne convient pas pour évaluer le filtre
contextuel sur des blocs.

Pour réexporter un entraînement existant, relancer la même commande
`run_spirula_block.sh` **sans `--clean`** : le checkpoint final est réutilisé,
sans nouvel entraînement, et le `.splat` est remplacé.
Pour récupérer les splats supprimés par un export précédent, il faut
réexporter depuis le checkpoint PLY : filtrer un `.splat` existant ne peut pas
recréer les splats manquants.

```bash
python3 -m unittest discover -s tools/postprocess -p 'test_*.py'
```

La conversion `.splat` vers 3D Tiles conserve son dossier autonome
[`../splat_to_3dtiles/`](../splat_to_3dtiles/README.md).
