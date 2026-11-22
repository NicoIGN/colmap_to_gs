# Post-traitement LiDAR

Outils CPU autonomes pour LAS/LAZ et `.splat` classique (32 octets par
gaussienne) ou PLY 3DGS binaire. Les sorties gardent le format d'entrée.
Les fichiers existants ne sont pas écrasés.

Pour les modèles sparse COLMAP, voir [recalage et injection LiDAR avec tracks](COLMAP.md).
Le script chapeau `colmap_lidar_pipeline.py` conserve le LAZ partagé dans
`workspace/lidar/` pour le réutiliser ensuite avec le splat final.
Voir [les paramètres d'intégration des pipelines images/vidéos/Panoramax et Slurm](PIPELINES.md).
Les maîtres conservent aussi les [dates et durées de leurs phases](../libs/python/PIPELINE_REPORT.md)
dans `pipeline_report.json`.

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

`--lidar-file workspace/lidar/lidar.laz` réutilise une extraction précédente
sans téléchargement ni PDAL, après vérification du manifeste, de l'emprise et
de la signature taille/SHA256. Pour un splat dans le repère ENU du modèle
COLMAP, ajouter `--georef workspace/colmap_lidar/georef_stats.json` :
le maître convertit une copie du LAZ en ENU sans modifier le fichier partagé.
Sans `--georef`, les coordonnées projetées et `--origin` restent utilisées.

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

Avec `--pdal pdal` (défaut), les scripts créent automatiquement un environnement
isolé à `~/.cache/colmap_to_gs/lidar-pdal`, défini dans
`environment/ign.slurm/pdal_env.yml`, puis le réutilisent. Mamba, micromamba ou
Conda doit être accessible dans le PATH. Un verrou sérialise la création entre
jobs Slurm. PDAL est exécuté via `run --prefix` : le Python appelant reste dans
son environnement initial (par exemple `gsplat`). Aucun `pip install pdal`
n'est nécessaire. La première création nécessite un accès à conda-forge et un
répertoire personnel accessible en écriture et partagé avec les nœuds.
`--pdal /chemin/pdal` permet de fournir un exécutable existant sans création.
PDAL doit proposer `readers.copc`, `filters.merge`, `filters.crop` et `writers.las`.
Si un environnement incomplet est détecté après interruption, une erreur demande
de le déplacer avant de relancer ; il n'est jamais supprimé automatiquement.
Le réseau doit accéder au WFS et aux URL HTTPS COPC ; PDAL extrait la zone via
les requêtes HTTP partielles plutôt que télécharger chaque tuile entière.
Les classes ne sont pas filtrées lors de la récupération.

La récupération peut aussi être utilisée seule :

```bash
python tools/lidar/fetch_ign_lidar.py \
  --bbox 650000,6860000,650100,6860100 \
  --crs EPSG:2154 --output zone.laz
```

Depuis l'env `gsplat`, sans le quitter, ce script s'exécute tel quel : PDAL est
lancé dans `lidar-pdal` automatiquement. Le Python du script doit avoir `numpy`.
Pour exécuter le script lui-même dans `lidar-pdal`, sans activer l'env :

```bash
mamba run -n lidar-pdal python $GIT_ROOT/tools/lidar/fetch_ign_lidar.py \
  --bbox xmin,ymin,xmax,ymax --crs EPSG:2154 --output zone.laz
```

Avec Conda, remplacer `mamba` par `conda`.

#### Installation de l'env `lidar-pdal`

Une seule fois (Linux/Slurm et macOS, même fichier `environment/ign.slurm/pdal_env.yml`) :

```bash
mamba env create -y -n lidar-pdal -f $GIT_ROOT/environment/ign.slurm/pdal_env.yml
```

Pour la mettre à jour après modification du fichier :

```bash
mamba env update -n lidar-pdal -f $GIT_ROOT/environment/ign.slurm/pdal_env.yml --prune
```

Vérification : `mamba run -n lidar-pdal pdal --version`. La commande doit afficher
une version de PDAL.

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

### LiDAR aérien et images prises au sol

Le mode par défaut est désormais **`--evidence-mode aerial`**, destiné notamment
aux splats parasites flottant en haut d'une scène reconstruite depuis un vélo.
Être gros ou épars ne suffit pas à prouver qu'un splat est un artefact :
le critère reste une incompatibilité géométrique avec la couverture aérienne.

Le test d'ellipsoïde ci-dessous produit des candidats sans support proche.
Le mode aérien ne les rejette que si la totalité de la boîte XY du support opaque,
élargie par la tolérance métrique, est couverte par des cellules LiDAR de 1 m
contenant au moins 3 retours distincts. Le bas de ce support doit être au-dessus
du retour le plus haut de chaque cellule et de ses voisines, avec la tolérance
`--max-distance`. Un splat passant sous un toit, une canopée ou le bord d'une
structure couvrante reste donc conservé.

On exige également des points **sol classés 2** à moins de deux tailles de cellule
de chaque cellule testée. Le support doit être entièrement à plus de
**`--ground-clearance 2` mètres au-dessus de ce sol**, réglable. On utilise
la hauteur maximale des points sol locaux pour protéger les terrains en pente.
Cette hauteur inclut un voisinage raster 5×5 légèrement plus large que le rayon :
cela peut protéger davantage de splats, mais n'autorise pas un rejet quand le
rayon de deux cellules ne contient aucun point sol.
Un support touchant la bande protégée près du sol n'est pas supprimé.

Un trou dans le LiDAR, une densité insuffisante, l'absence de sol classé ou une
emprise très grande à vérifier sont traités comme **inconnus, donc conservés**.
Une gaussienne sans partie atteignant le seuil d'opacité est également conservée
dans ce mode : elle n'apporte pas de support opaque à tester, et ce n'est pas un
rejet d'opacité global. Le vide dans les splats n'est jamais utilisé comme preuve.
Les structures couvrantes utilisent toutes les classes du LAZ à leur densité
originale, même si `--lidar-classes` restreint les références de proximité.

Réglages : `--aerial-cell-size 1`, `--aerial-min-returns 3` et
`--ground-clearance 2`. Le rapport distingue les rejets aériens et les
protections près du sol, sous couverture, sans couverture fiable ou sans support opaque.

### Volumes de 1 à 8 millions de splats

Les scènes restent mappées en mémoire et sont traitées par lots : 10 000 splats
pour la proximité, 1 000 candidats pour la visibilité. Les cellules LiDAR sont
des tableaux triés compacts ; hauteurs de couverture et de sol sont pré-calculées
une seule fois, sans dictionnaire Python par retour ni raster de toute l'emprise.
La proximité utilise des tests vectorisés avant les intersections détaillées.

Pour borner les coûts, un support nécessitant plus de **4 096 voisins** ou
dépassant le budget cumulé de **50 000 voisins par lot** est conservé comme inconnu.
De même pour une emprise dépassant **256 cellules** par splat.
Les recherches ne sont jamais tronquées puis interprétées comme un rejet.
Le rapport indique `protected_support_budget`, `protected_cell_budget` et les
limites utilisées ; un avertissement signale ces conservations.
Le chargement du filtre refuse plus de **20 millions de points LiDAR sélectionnés**
(en mode aérien, cette limite s'applique aussi à la couverture toutes classes
sans stride). Réduire/cadrer le LAZ plutôt que supprimer arbitrairement des retours
de couverture. Un stride de proximité ne réduit pas la couverture aérienne.

Mesure synthétique Linux de ce filtre, avec 30 000 retours sol et 20 % de splats
hauts à rejeter : **1 million en 3.1 s / 450 Mio RSS**, **8 millions en 22 s /
591 Mio RSS**. Les rejets attendus sont vérifiés exactement. Cela mesure le
chargement et les deux critères, pas l'écriture, le téléchargement ni l'ICP.
Ces chiffres ne prédisent pas une scène réelle : davantage de retours,
des supports anisotropes et beaucoup de cellules augmentent temps et mémoire.
La cible de 16 Go concerne le filtrage, pas l'entraînement 3DGS ou l'ensemble
du pipeline ; elle n'est pas une garantie universelle pour tout LAZ.

**Limite :** ce sont des colonnes verticales échantillonnées, pas les trajectoires
réelles du laser. Une surface mesurée sous un arbre ne prouve pas que toute la
colonne était libre, surtout si la canopée a changé depuis l'acquisition.
Ce mode réduit les rejets abusifs mais ne garantit pas la conservation de tout
objet réel. Une boîte XY incomplètement couverte est conservée volontairement,
donc de grands parasites peuvent subsister. Commencer en diagnostic.

L'ancien test de proximité peut être demandé explicitement avec
`--evidence-mode proximity` ; il ne protège pas les objets non observés depuis
le ciel et n'est pas recommandé pour un nettoyage général de scènes vélo.

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
n'existe : en mode `proximity`, le splat est proposé au rejet, même si son centre
touche le LiDAR ; en mode `aerial`, il est conservé comme indéterminé.
À égalité, le support se réduit au centre. La bbox reste respectée : les splats
hors zone sont conservés. Le seuil doit être dans ]0,1] et est **désactivé par
défaut dans le filtre autonome**, préservant le test précédent à 3σ.
Le **script maître utilise 0.05 (5 %) par défaut**, un compromis conservateur
qui exclut les queues très transparentes sans exiger un support fortement opaque.
Ce seuil reste ajustable selon la scène et n'est pas une garantie de qualité visuelle.
Le rapport indique le seuil et
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

### Fusion LiDAR + 3DGS (`fuse_lidar_splats.py`)

Entrée et sortie en `.splat` ou `.ply` (même extension). Par défaut, seule la
règle 1 s'applique, plus le nettoyage planimétrique :

0. Un splat dont le support planimétrique (`--footprint-sigmas` × σ, 3 par défaut)
   n'atteint pas le LiDAR le plus proche en XY est retiré (il déborde du LAZ).
   `--footprint-sigmas 0` désactive ce nettoyage.
   Passe bbox (toujours active) : tout splat dont le support (même demi-taille) sort de
   la bbox globale du LAZ (X, Y et Z) est retiré. Compteur `removed_outside_bbox`.

1. Un splat dont le Z (ou le support vertical, `--footprint-sigmas` × σ) sort de la bande
   Z LiDAR locale, à `--z-margin` près, est retiré. Un grand splat de bord qui descend
   sous le LiDAR est donc retiré même si son centre reste dans la bande. La bande est
   le min/max des `--z-neighbours` points les plus proches en XY dans `--z-radius`
   (par défaut 64 points dans 5 m), élargi au support horizontal du splat si celui-ci
   est plus grand. Un grand voisinage évite de retirer les objets (bâtiments,
   végétation) à cause d'un LiDAR sol trop étroit. Un splat sans LiDAR dans ce rayon
   est conservé. `--z-offset` ajoute un décalage
   vertical aux splats pour ce test seulement (utile si le datum diffère du LAZ) ;
   le fichier de sortie garde les Z d'origine.
2. Avec `--fill-holes`, dans chaque voxel `--voxel` où les splats sont moins nombreux que
   `--density-ratio` × points LiDAR, un point LiDAR est ajouté comme splat
   (écart-type `--scale-factor` × distance LiDAR au plus proche, opacité `--opacity`).
   Sa couleur est la moyenne des splats conservés dans `--colour-radius`, sinon gris (128).
   `--min-splat-distance` écarte les points LiDAR situés à moins de cette distance d'un
   splat conservé, pour ne garder que les zones isolées.

```bash
python tools/lidar/fuse_lidar_splats.py \
  --lidar terrain.laz --input scene.ply --output scene_fused.ply \
  --origin 650000,6860000,0 --z-margin 0.5 --voxel 0.5 --fill-holes
```

Le rapport JSON (`scene_fused.ply.json`) compte les retraits, ajouts et couleurs
propagées ou grises. `--dry-run` écrit uniquement le rapport.

## Tests

```bash
cd tools/lidar
python -m unittest test_lidar test_ign_pipeline
```
