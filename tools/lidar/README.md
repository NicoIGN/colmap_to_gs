# Post-traitement LiDAR

Deux outils CPU autonomes pour LAS/LAZ et `.splat` classique (32 octets par
gaussienne) ou PLY 3DGS binaire. Les sorties gardent le format d'entrée.
Les fichiers existants ne sont pas écrasés.

Installer les dépendances dans l'environnement Python utilisé :

```bash
python -m pip install -r tools/lidar/requirements.txt
```

## Classes LiDAR

Les deux scripts acceptent `--lidar-classes 2,6` pour ne retenir que certains
codes de classification LAS. Par défaut, toutes les classes sont utilisées.
Codes LAS usuels : 2 = sol, 3/4/5 = végétation basse/moyenne/haute,
6 = bâtiment, 9 = eau ; vérifier la nomenclature du producteur.
Les classes sélectionnées sont enregistrées dans le rapport.

Pour le recalage, sol et bâtiments peuvent fournir des références plus stables
que la végétation. Ils doivent cependant couvrir suffisamment la scène :
un sol seul ne contraint pas toujours correctement toutes les directions.
Pour le nettoyage, restreindre les références à 2 et 6 **peut supprimer la
végétation réelle**. Commencer par toutes les classes et un `--dry-run`.
Les scripts ne prétendent pas connaître la classe sémantique des splats :
une association au point LiDAR le plus proche n'est pas une classification fiable.

## Repère et précision

Les splats doivent être approximativement géoréférencés. **Vérifier avant tout
le CRS, les unités métriques et le référentiel vertical** : ces scripts ne
reprojettent pas les données et ne devinent pas le repère d'un `.splat`.
`--origin X,Y,Z` est l'origine globale déjà soustraite aux splats ; elle est
soustraite au LiDAR également. Les sorties restent dans ce repère local.
Sans origine, les deux entrées doivent déjà avoir les mêmes coordonnées.
Éviter des coordonnées projetées de plusieurs millions dans un `.splat`
float32 : la précision perdue ne peut pas être récupérée après export.

## Recalage

```bash
python tools/lidar/align_splats_to_lidar.py \
  --lidar terrain.laz --input scene.splat --output scene_aligned.splat \
  --origin 650000,6860000,0 \
  --max-correspondence-distance 2 --voxel-size 0.2
```

ICP point-à-point à trois seuils décroissants (2 m, 1 m, 0.5 m dans cet
exemple), sous-échantillonnage par voxels, rejet des 20 % de correspondances
les plus éloignées. La rotation et la translation sont estimées ; ajouter
`--estimate-scale` uniquement si l'échelle initiale est incertaine.
`--min-overlap` vaut 0.3 et `--iterations` vaut 60 par seuil.

Les positions, échelles, quaternions et coefficients SH directionnels PLY
(degrés 1 à 3) sont transformés. Les autres propriétés des gaussiennes sont
conservées ; les éléments PLY autres que `vertex` ne sont pas exportés.
Le `.splat` implique une quantification des quaternions à 8 bits.
Un schéma SH non pris en charge est refusé.

Un recouvrement insuffisant, une géométrie dégénérée, une absence de convergence
ou une dégradation des distances bloque l'export. Le rapport
`scene_aligned.splat.json` contient la matrice locale, l'origine, le
recouvrement et les RMSE avant/après sur les mêmes inliers finaux.
Une faible RMSE ne garantit pas un alignement correct sur une scène répétitive :
inspecter visuellement le résultat. Pas de recherche globale automatique.

## Nettoyage

```bash
python tools/lidar/filter_splats_by_lidar.py \
  --lidar terrain.laz --input scene_aligned.splat --output scene_clean.splat \
  --origin 650000,6860000,0 --max-distance 0.5 \
  --rejected-output scene_rejected.splat
```

Rejette les gaussiennes dont le **centre** est à plus de 0.5 m du point LiDAR
le plus proche. La taille et l'étendue de la gaussienne ne sont pas testées.
`--bbox xmin,ymin,zmin,xmax,ymax,zmax` limite le filtrage à une zone dans le
repère local ; les splats hors zone restent inchangés. Sans bbox, tous sont testés.
`--dry-run` écrit uniquement le rapport JSON, pas les scènes.

Faire d'abord un diagnostic : un LiDAR aérien peut ne pas couvrir les façades,
la végétation et les objets peuvent avoir changé. L'absence de point ne prouve
pas un artefact. Choisir le seuil selon la densité LiDAR et l'erreur de recalage.

Les entrées sont lues par lots, mais le nuage LiDAR sélectionné et les centres
des splats résident en RAM. `--lidar-stride N` réduit la mémoire en gardant un
point sur N (par défaut 1) ; pour le nettoyage, cela augmente les distances
au plus proche voisin. Préparer une zone LAZ adaptée pour les grandes acquisitions.

## Tests

```bash
cd tools/lidar
python -m unittest test_lidar
```
