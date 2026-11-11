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
| `export_ply_legacy.py` | Sauvegarde exacte de l'ancien exporteur (BBOX et seuil global de taille) |
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

- `scripts/run.sh` : appelle les lanceurs Nerfstudio.
- `scripts/run_spirula_block.sh` : utilise `export_ply.py` par défaut ;
  surcharge possible avec `--export-script` / `EXPORT_SCRIPT`.

## 4. Fonctionnement internes

- Exporteur par défaut : sans seuil global de taille, suppression hors BBOX
  COLMAP ni test de proximité aux points COLMAP ;
  les routes peu texturées peuvent avoir peu de points, mais des splats utiles.
- Export PLY : les attributs non finis et rotations nulles sont toujours supprimés.
- `MIN_OPACITY=0` : désactive le seuil d'opacité du lanceur.
- Seuil d'opacité explicite : reste possible indépendamment du filtre géométrique.

Le filtre contextuel ne propose un rejet que si **les trois conditions** sont réunies :

1. Le plus grand demi-axe dépasse 10 fois la médiane locale des grands demi-axes.
2. Le centre est périphérique dans le repère principal de la scène, calculé sur
   une référence robuste : bornes aux quantiles 2/98 %, bande à partir de 90 %
   des demi-étendues. Les dimensions quasi dégénérées sont ignorées pour ne pas
   considérer toute une route plane comme périphérique.
3. Le splat n'a pas de support de surface local : les 24 voisins ne forment pas
   un plan suffisamment bidimensionnel et mince, ou le centre en est éloigné,
   ou l'axe mince du splat n'est pas aligné sur la normale (tolérance 30°),
   ou le splat n'est pas aplati (petit/grand demi-axe > 0,2).

- Écart au plan : rapporté à la dispersion des voisins dans le plan, avec un
  seuil de `0,25` fois l'écart-type suivant le deuxième axe principal.
- Voisinage : KD-tree sur au plus `100 000` centres échantillonnés avec une
  graine fixe ; calcul par lots, sans matrice de distances tous-à-tous.
- Invariance : ces seuils sans unité ne changent pas la règle si l'échelle est
  uniformément modifiée, ou si la scène est translatée ou tournée, aux arrondis
  numériques près.
- Taille seule : ne provoque jamais de rejet.

Cela reste une heuristique, pas une reconnaissance de routes ni une mesure
de visibilité dans les images. Elle peut manquer des parasites intérieurs ou
des groupes parasites cohérents et peut proposer de faux rejets sur des
surfaces courbes, des scènes à plusieurs niveaux ou des quartiers peu denses.
L'échantillonnage peut manquer des détails locaux. Les scènes trop petites ou
dégénérées sont conservées avec un avertissement explicite.

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
  le nouveau filtre est évalué après fusion, sur l'ensemble de la scène, afin
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
- Anciens arguments `--points`, `--dist`, `--radius-factor`, `--outlier-percent`
  et `--max-points` : restent acceptés pour compatibilité, mais ne servent plus
  à retirer des splats ; un message le précise.
- `--max-world-scale` : refusé avec le nouveau filtre, car il impose un seuil absolu.

## 5. Notes et informations complémentaires

`export_ply_legacy.py` conserve exactement le comportement précédent, y compris
ses garde-fous (avertissement au-delà de 10 %, filtre ignoré au-delà de 20 %).
Pour le réutiliser explicitement :

```bash
EXPORT_SCRIPT="$PWD/tools/postprocess/export_ply_legacy.py" \
  bash scripts/run_spirula_block.sh --sparse-dir ... --images-dir ... --output-dir ...
```

L'ancien exporteur accepte toujours `MAX_WORLD_SCALE` via le lanceur. Cette
surcharge peut de nouveau retirer de grands splats de route : elle ne doit pas
être utilisée pour tester la nouvelle politique sur des blocs.

Pour réexporter un entraînement existant, relancer la même commande
`run_spirula_block.sh` **sans `--clean`** : le checkpoint final est réutilisé,
sans nouvel entraînement, et le `.splat` est remplacé.
Pour récupérer les splats qu'un ancien export avait supprimés, il faut
réexporter depuis le checkpoint PLY : filtrer un ancien `.splat` ne peut pas
recréer les splats manquants.

```bash
python3 -m unittest discover -s tools/postprocess -p 'test_*.py'
```

La conversion `.splat` vers 3D Tiles conserve son dossier autonome
[`../splat_to_3dtiles/`](../splat_to_3dtiles/README.md).
