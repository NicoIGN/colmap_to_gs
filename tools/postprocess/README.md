# Traitement des résultats

Ces outils proviennent de `python/` et de `scripts/`. Les pipelines utilisent
maintenant directement leurs chemins sous `tools/postprocess/`.

| Outil | Fonction |
|---|---|
| `export_splat_to_ply.sh` | Export Nerfstudio via `ns-export` |
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

`scripts/run.sh` appelle les lanceurs Nerfstudio ;
`scripts/run_spirula_block.sh` utilise `export_ply.py` par défaut, tout en conservant
la surcharge `--export-script` / `EXPORT_SCRIPT`.

### Nouveau filtre contextuel (diagnostic par défaut)

Le seuil global de taille et la suppression hors BBOX COLMAP sont retirés de
l'exporteur par défaut. Aucun test de proximité aux points COLMAP n'est utilisé :
les routes peu texturées peuvent avoir peu de points, mais des splats utiles.
Les attributs non finis et rotations nulles sont toujours supprimés à l'export
PLY ; le seuil d'opacité du lanceur est désormais désactivé (`MIN_OPACITY=0`).
Un seuil d'opacité explicite reste possible, indépendamment du filtre géométrique.

Le nouveau filtre ne propose un rejet que si **les trois conditions** sont réunies :

1. Le plus grand demi-axe dépasse 10 fois la médiane locale des grands demi-axes.
2. Le centre est périphérique dans le repère principal de la scène, calculé sur
   une référence robuste : bornes aux quantiles 2/98 %, bande à partir de 90 %
   des demi-étendues. Les dimensions quasi dégénérées sont ignorées pour ne pas
   considérer toute une route plane comme périphérique.
3. Le splat n'a pas de support de surface local : les 24 voisins ne forment pas
   un plan suffisamment bidimensionnel et mince, ou le centre en est éloigné,
   ou l'axe mince du splat n'est pas aligné sur la normale (tolérance 30°),
   ou le splat n'est pas aplati (petit/grand demi-axe > 0,2).

L'écart au plan est rapporté à la dispersion des voisins dans le plan
(seuil 0,25 fois l'écart-type suivant le deuxième axe principal).
Le voisinage utilise un KD-tree sur au plus 100 000 centres échantillonnés
avec une graine fixe ; calcul par lots, sans matrice de distances tous-à-tous.
Ces seuils sont sans unité : changer uniformément l'échelle, translater ou
tourner la scène ne change pas la règle, aux arrondis numériques près.
La taille seule ne provoque jamais de rejet.

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

L'entrée n'est jamais écrasée. `--size-factor` règle le rapport de taille local
(défaut 10) ; `--neighbors`, `--reference-limit`, `--seed` et `--batch-size`
pilotent le voisinage et le coût mémoire. `--mode off` copie simplement l'entrée.
Plus de 20 % de rejets en mode `apply` provoque une erreur sans remplacer les
sorties ; examiner le diagnostic avant de modifier `--max-removal-fraction`. Un rejet de tous les splats est toujours refusé.
Le filtre ne désactive pas silencieusement une règle trop destructive.

`--sparse-points sparse/0/points3D.bin` ajoute un recadrage sur la bbox des
points COLMAP (même repère monde que le `.splat`). La boîte est orientée sur
leurs axes principaux, bornée par `--sparse-bbox-quantile` (0.005 de chaque
côté) et élargie de `--sparse-bbox-margin` (0.05) × son plus grand côté : test
invariant par rotation, translation et échelle. Seul le centre du splat est
testé. `--sparse-bbox-mode apply` (défaut), `diagnostic` ou `off` est
indépendant de `--mode` ; les splats hors bbox sont exclus du filtre contextuel
et comptés dans `sparse_bbox.outside_sparse_bbox` du JSON. Si aucun centre
n'est dans la boîte, le filtre échoue (repère probablement différent).

Dans le pipeline Panoramax, **les blocs sont exportés sans filtre géométrique**
et le nouveau filtre est évalué après fusion, sur l'ensemble de la scène :
les frontières internes des blocs ne sont pas traitées comme des bords de
chantier. Configurer `SPLAT_FILTER_MODE="diagnostic"` (défaut), `"apply"` ou
`"off"` et `SPLAT_FILTER_SIZE_FACTOR="10"`. La fusion avant filtre est conservée
dans `NOM.unfiltered.splat`, les candidats dans `NOM.rejected.splat`, et le
résumé dans `NOM.filter.json`. En diagnostic, `NOM.splat` est identique à la
fusion non filtrée. La fusion conserve ses propres règles de déduplication.

En usage autonome, `export_ply.py` utilise aussi le mode diagnostic par défaut
(`--filter-mode diagnostic|apply|off`), sur la scène fournie. Il produit les mêmes
diagnostics et conserve le PLY d'entrée. Les anciens arguments `--points`,
`--dist`, `--radius-factor`, `--outlier-percent` et `--max-points` restent acceptés
pour compatibilité, mais ne servent plus à retirer des splats ; un message le
précise. `--max-world-scale` est refusé avec le nouveau filtre, car il impose
un seuil absolu.

### Ancien filtre en sauvegarde

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
