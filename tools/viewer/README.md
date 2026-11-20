# Visualisation locale

[Retour à la documentation générale](../../doc/guide_utilisateur.md)

## 1. Objectif

Visualiser dans un navigateur des scènes `.splat`, des données COLMAP ou des tilesets 3D Tiles.

Exemple d’invocation :

```bash
python3 tools/viewer/server.py /chemin/vers/scene.splat
```

## 2. Entrées et sorties

Les fichiers du dossier de données sont accessibles par la route
`/__datasets__/<chemin relatif>`. Sans fichier en argument, utiliser l'interface
pour importer une scène. Voir `--help` pour les options complètes.

## 3. Configuration et utilisation

Depuis la racine du dépôt, avec Python 3.10+ :

```bash
python3 tools/viewer/server.py /chemin/vers/scene.splat
```

Le serveur ouvre le visualiseur dans le navigateur. Les ressources de l'interface
sont dans `html/`, résolues par rapport au script, indépendamment du dossier courant.
Le serveur écoute uniquement sur `127.0.0.1` ; arrêt avec `Ctrl+C`.

Options du serveur :

- `splat` : fichier local à charger ; facultatif.
- `--data-directory PATH` : dossier servi sous `/__datasets__/` ; facultatif.
- `--port PORT` : port HTTP, `8000` par défaut, valeurs de `1` à `65535` (par exemple `8080`).
- `--no-browser` : désactive l'ouverture automatique du navigateur.
- `--flip-up` : active `flipUp` dans l'URL ouverte au lancement.
- `--camera-pos px,py,pz,tx,ty,tz[,ux,uy,uz]` : position et cible caméra, avec vecteur haut optionnel ; 6 ou 9 nombres.

Voir `--help` pour les options complètes.

### 3D Tiles avec fond OpenStreetMap

```bash
bash tools/viewer/3dtiles/view.sh /chemin/vers/tileset.json
```

Ce deuxième visualiseur utilise CesiumJS avec un fond OSM sans jeton Cesium ion.
Options : `tileset` (requis), `--port PORT` (défaut `8000`, valeurs de `0` à
`65535`, `0` choisit un port automatique ; par exemple `8080`) et `--no-open`
(désactive l'ouverture du navigateur). Internet est requis pour CesiumJS et OSM ;
le fond est sans relief. Les contenus référencés par le tileset doivent être
dans son dossier ou ses sous-dossiers.

Le pipeline de conversion reste dans [`../splat_to_3dtiles/`](../splat_to_3dtiles/README.md).

## 4. Fonctionnement interne

Le visualiseur web affiche les scènes `.splat`, les modèles COLMAP et les
trajectoires GPS dans un même espace. Il permet de contrôler les reconstructions,
de masquer ou d'afficher leurs éléments, et de naviguer dans la scène.

- Un modèle COLMAP peut être ajouté par glisser-déposer ; ses points et caméras
  apparaissent comme un groupe que l'on peut masquer ou afficher.
- Les caméras équirectangulaires (modèle COLMAP `EQUIRECTANGULAR`, produit par le
  pipeline 360°) sont acceptées. En mode Images, la photo 360° s'affiche sur une
  sphère vue de l'intérieur ; le cadre de sélection est un cadre nominal de 90°.
  Le panneau « Options » (haut droite) permet de passer à un cube map (six faces
  rééchantillonnées depuis l'équirectangulaire) si la sphère déforme trop l'image.
- Les fichiers `.splat` et `.ply` ajoutés par glisser-déposer s'ajoutent à la
  scène déjà ouverte. Le bouton d'ouverture remplace la scène.
- Un nuage LiDAR `.laz` ou `.las` ajouté par glisser-déposer apparaît comme une
  couche dans la liste des couches. Il est placé via le géoréférencement JSON
  s'il a le même CRS (EPSG) ; sinon il est centré à l'origine et un message le
  signale. Le nuage est sous-échantillonné à 2 millions de points au maximum,
  coloré par RVB si présent, sinon par classe ASPRS. Décodage via laz-perf
  (chargé depuis jsdelivr) ; limite : 1 Gio.
- Un GeoPackage `.gpkg` déposé seul (par ex. produit par `tools/vector`) apparaît
  en données 3D filaires : chaque table d'entités devient une sous-couche de
  lignes (polylignes et contours de polygones ; points ignorés). Il suit le même
  géoréférencement / CRS (EPSG) que le LiDAR. La couleur se règle dans
  « Options » (« Couleur des données filaires »). Décodage SQLite via sql.js
  (chargé depuis jsdelivr) ; limite : 512 Mio, 4 millions de sommets affichés.
- Dans une vue vide, le visualiseur cadre automatiquement le modèle COLMAP.
- Le clic gauche-glisser orbite autour de la cible ; le clic droit-glisser ou
  milieu-glisser déplace la vue sans touche supplémentaire. Ctrl + clic
  droit-glisser pivote le regard sur place ; Ctrl + clic gauche-glisser zoome
  sur une zone ; Maj + clic permet de mesurer.
- L'affichage des points et caméras conserve une taille lisible à l'écran,
  même si l'échelle du modèle COLMAP est inconnue.
- Après avoir orienté le modèle à la souris, **Définir la verticale** prend
  le haut de l'écran (axe Y de la caméra) comme axe principal personnalisé.
  L'option **Verticale définie** est alors activée et sélectionnée automatiquement,
  même si une mesure ou une sélection de pivot suspend momentanément les déplacements.
  Les orbites, le verrouillage et **Redresser la vue** utilisent cette verticale.
  Même sans verrouillage, les rotations horizontales autour de la cible et du
  regard conservent cet axe après une inclinaison ; les mouvements verticaux
  restent libres.
  Le cadrage et les coordonnées des modèles, du COLMAP et du GPX restent inchangés.
  Les axes fixes X/Y/Z restent sélectionnables ; ouvrir une nouvelle scène
  réinitialise la verticale personnalisée.

## 5. Notes et informations complémentaires

La profondeur des frustums vaut 1 % du rayon de l'étendue des centres caméra,
bornée aux quantiles 2/98 % pour limiter l'effet des caméras aberrantes.
Il n'y a pas de taille minimale absolue : l'affichage suit aussi les scènes
SfM sans GPX dont l'échelle est arbitraire. Si les centres sont confondus,
le rayon de la scène sert de référence.
