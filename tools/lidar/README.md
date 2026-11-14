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
6 = bâtiment, 9 = eau ; 67 = bâti incertain selon le jeu IGN utilisé.
Vérifier la nomenclature du producteur (67 est un code spécifique).
Les classes sélectionnées sont enregistrées dans le rapport.

Pour le recalage, sol et bâtiments peuvent fournir des références plus stables
que la végétation. Ils doivent cependant couvrir suffisamment la scène :
un sol seul ne contraint pas toujours correctement toutes les directions.
Pour le nettoyage, restreindre les références à 2 et 6 **peut supprimer la
végétation réelle**. Commencer par toutes les classes et un `--dry-run`.
Les scripts ne prétendent pas connaître la classe sémantique des splats :
une association au point LiDAR le plus proche n'est pas une classification fiable.

## Pipeline IGN automatique

```bash
python tools/lidar/lidar_pipeline.py \
  --input scene.splat --output-dir lidar_result \
  --crs EPSG:2154 --origin 650000,6860000,0 \
  --margin 10 --max-correspondence-distance 2 --max-distance 0.5
```

Le script calcule l'emprise XY des ellipsoïdes orientés à 3σ, ajoute 10 m de marge,
interroge l'index WFS IGN LiDAR HD, extrait les points COPC dans `lidar.laz`
via PDAL, puis lance le recalage et le nettoyage. Il produit `aligned.splat`,
`cleaned.splat`, `rejected.splat` et leurs rapports (mêmes noms en `.ply`
pour une entrée PLY). `pipeline.json` conserve paramètres, taille/SHA256 de
l'entrée et état de chaque étape ; les erreurs interrompent l'enchaînement.
Le dossier de sortie doit être nouveau. Les fichiers intermédiaires restent
disponibles pour diagnostic, sans reprise implicite ni écrasement.

**Par défaut, le recalage utilise les classes 2,3,4,5,6,67**, dont la végétation
et le bâti incertain. Le nettoyage utilise toutes les classes, donc inclut aussi
3/4/5/67. La classification originale est conservée : pas de conversion 67 vers 6.
`--align-classes` et `--filter-classes` permettent de restreindre ces références.
Cela ne constitue pas une classification sémantique des gaussiennes.

`--dry-run-clean` réalise téléchargement et recalage, mais seulement un diagnostic
du nettoyage. `--estimate-scale`, `--voxel-size`, `--min-overlap` et
`--lidar-stride` sont transmis aux outils appropriés. `--support-sigma` (3 par défaut) et `--spacing-factor` (1 par défaut) règlent
le support gaussien et la tolérance de densité du nettoyage. Une correction sortant de
l'emprise récupérée bloque le nettoyage : relancer dans un nouveau dossier avec
plus de marge. La marge doit couvrir au moins les deux seuils de distance.

Le pipeline est limité aux données métropolitaines **Lambert-93 / EPSG:2154** ;
il ne devine ni l'origine des splats ni le référentiel vertical. Une vérification
grossière du domaine Lambert-93 et une limite de 25 km²/100 tuiles évitent une
extraction accidentelle trop grande (`--max-area-km2`, `--max-tiles`).
Des splats aberrants peuvent gonfler l'emprise ; les nettoyer en amont si nécessaire.

Installer également **PDAL avec `readers.copc`, `filters.merge`, `filters.crop`
et `writers.las`**, par exemple dans un environnement conda-forge adapté.
Vérifier avec `pdal --drivers`. `--pdal` permet de donner le chemin de l'exécutable.
Le réseau doit accéder au WFS et aux URL HTTPS COPC ; PDAL extrait la zone via
les requêtes HTTP partielles plutôt que télécharger chaque tuile entière.
Les classes ne sont pas filtrées lors de la récupération.

La récupération peut aussi être utilisée seule :

```bash
python tools/lidar/fetch_ign_lidar.py \
  --bbox 650000,6860000,650100,6860100 \
  --crs EPSG:2154 --output zone.laz
```

### Source de l'approche WFS/COPC

Le projet [IGNfab roofer-with-ignf-datasets](https://github.com/ignfab/roofer-with-ignf-datasets)
(MIT) utilise l'index `IGNF_LIDAR-HD_METADONNEE:metadata` sur
`https://data.geopf.fr/wfs`, son champ `url_npl` et PDAL `readers.copc`.
Nous utilisons ces interfaces avec une implémentation Python propre :
pagination WFS, réécriture des liens `/telechargement/` vers
`/chunk/telechargement/`, fusion puis crop exact. Contrairement à ce projet,
la classe 67 n'est pas réassignée à 6 et aucune étape roofer/BD TOPO n'est requise.
Les tests de récupération utilisent des réponses simulées ; la disponibilité
du service et la récupération d'une emprise réelle restent à vérifier sur une
machine disposant de PDAL et de l'accès réseau IGN.

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

Rejette les gaussiennes dont l'**ellipsoïde orienté à 3σ** n'est proche
d'aucun point LiDAR. Les tailles et quaternions sont pris en compte : un centre
éloigné reste conservé si l'étendue de la gaussienne rencontre le LiDAR.
La distance euclidienne au volume ellipsoïdal est calculée sur tous les points
candidats dans sa sphère englobante, pas seulement le voisin le plus proche du centre.

`--max-distance 0.5` est la tolérance de base en mètres entre ellipsoïde et
points. On ajoute par défaut l'espacement local LiDAR : distance au sixième
voisin du point le plus proche du centre, divisée par sqrt(6) (moins de voisins
si le nuage est petit). `--spacing-factor 0` désactive cette tolérance ;
`--support-sigma` modifie le rayon en écarts-types.
Le rapport compte les splats conservés malgré un centre éloigné.

### Support suffisamment opaque (optionnel)

Ajouter `--min-support-opacity 0.1` au filtre ou au script maître pour n'accepter
que la partie où l'opacité gaussienne locale atteint au moins 10 % :
`alpha(r) = alpha_centre * exp(-r²/2)`.
Le rayon testé en écarts-types devient
`min(support_sigma, sqrt(2 * log(alpha_centre / seuil)))`.
L'opacité au centre est le canal alpha/255 du `.splat`, ou la sigmoid de
la propriété `opacity` (logit) du PLY 3DGS.

Si l'opacité au centre est inférieure au seuil, aucun support suffisamment opaque
n'existe : le splat est proposé au rejet, même si son centre touche le LiDAR.
À égalité, le support se réduit au centre. La bbox reste respectée : les splats
hors zone sont conservés. Le seuil doit être dans ]0,1] et est **désactivé par
défaut**, préservant le test précédent à 3σ. Le rapport indique le seuil et
le nombre de rejets faute d'opacité au centre.

Il s'agit d'une approximation 3D par gaussienne, **pas de l'opacité finale
composée du rendu**, qui dépend du point de vue et des autres splats.
La tolérance métrique/densité s'applique autour de ce support opaque.
Le maître conserve l'emprise à `support_sigma` pour l'extraction, volontairement
plus large que le support opaque, afin de ne pas réduire la couverture LiDAR.

Ce test est volontairement conservateur, pas une preuve d'utilité visuelle :
les gaussiennes très grandes peuvent être conservées malgré un rendu incorrect.
Un support gaussien est théoriquement infini ; 3σ est une approximation finie,
et un seul point proche suffit ici. Les trous de couverture, changements de
date et erreurs de recalage peuvent encore provoquer des rejets injustifiés.
La tolérance de densité n'interpole pas une surface LiDAR.
`--bbox xmin,ymin,zmin,xmax,ymax,zmax` limite le filtrage aux centres dans une zone du
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
python -m unittest test_lidar test_ign_pipeline
```
