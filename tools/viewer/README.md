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

## 4. Fonctionnement internes

### Taille des frustums COLMAP

Les points sparse (2 px par défaut) et centres caméra (4 px) sont des disques
de taille écran, indépendants de l'échelle SfM et du GPX. Deux curseurs dans
la section COLMAP permettent d'ajuster leur diamètre sans recharger le modèle.
La densité de pixels de l'écran est prise en compte (facteur maximal 2).

Un dossier COLMAP peut aussi être glissé directement dans la vue : le viewer
parcourt le dossier et charge tous les sous-dossiers contenant
`cameras.bin`, `images.bin` et `points3D.bin`. Les imports successifs
s'ajoutent à la vue. Chaque modèle apparaît comme un groupe de couches avec
des cases de masquage en ligne pour les points 3D, les centres caméra et les
frustums ; la case du groupe masque ou réaffiche toutes ses couches.
Les dépôts de scène, JSON et GPX gardent leur comportement existant.
Dans une vue vide, le modèle COLMAP est automatiquement cadré et permet la
navigation sans charger de splat. Le bouton de cadrage et la touche `R`
permettent de retrouver sa vue d'ensemble. Ajouter un COLMAP à une scène splat
existante ne déplace pas la caméra.
Le clic gauche-glisser fait orbiter la vue autour de la cible. Ctrl + clic
droit-glisser déplace latéralement ; le clic milieu-glisser reste une autre
commande de translation. Maj + clic reste réservé aux mesures.
Ctrl + clic gauche-glisser trace une zone : au relâchement du bouton,
la caméra se rapproche et centre cette zone au plan de la cible, sans rotation.
Le cadrage conserve les proportions de la vue et inclut tout le rectangle.

Glisser un fichier `.splat` ou `.ply` dans la vue l'ajoute aux scènes déjà
chargées sans recadrer ni déplacer la caméra. Le bouton d'ouverture conserve
son comportement de remplacement. Le panneau d'informations regroupe les
cases de visibilité des scènes splat, des groupes COLMAP et de la trajectoire
GPS.

## 5. Notes et informations complémentaires

La profondeur des frustums vaut 1 % du rayon de l'étendue des centres caméra,
bornée aux quantiles 2/98 % pour limiter l'effet des caméras aberrantes.
Il n'y a pas de taille minimale absolue : l'affichage suit aussi les scènes
SfM sans GPX dont l'échelle est arbitraire. Si les centres sont confondus,
le rayon de la scène sert de référence.
