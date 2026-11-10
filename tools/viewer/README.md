# Visualisation locale

Depuis la racine du dépôt, avec Python 3.10+ :

```bash
python3 tools/viewer/server.py /chemin/vers/scene.splat
```

Le serveur ouvre le visualiseur dans le navigateur. Les ressources de l'interface
sont dans `html/`, résolues par rapport au script, indépendamment du dossier courant.
Le serveur écoute uniquement sur `127.0.0.1` ; arrêt avec `Ctrl+C`.

Options utiles : `--port 8080`, `--no-browser`, `--flip-up`,
`--camera-pos="px,py,pz,tx,ty,tz"` et `--data-directory /chemin/vers/datasets`.
Les fichiers du dossier de données sont accessibles par la route
`/__datasets__/<chemin relatif>`. Sans fichier en argument, utiliser l'interface
pour importer une scène. Voir `--help` pour les options complètes.

## 3D Tiles avec fond OpenStreetMap

```bash
bash tools/viewer/3dtiles/view.sh /chemin/vers/tileset.json
```

Ce deuxième visualiseur utilise CesiumJS avec un fond OSM sans jeton Cesium ion.
Options : `--port 8080`, `--no-open`. Internet est requis pour CesiumJS et OSM ;
le fond est sans relief. Les contenus référencés par le tileset doivent être
dans son dossier ou ses sous-dossiers.

Le pipeline de conversion reste dans [`../splat_to_3dtiles/`](../splat_to_3dtiles/README.md).

## Migration

`python/server.py` et `python/html/` sont déplacés ici ; le lanceur Cesium
`tools/splat_to_3dtiles/view.sh` est maintenant `tools/viewer/3dtiles/view.sh`.
# Taille des frustums COLMAP

Les points sparse (2 px par défaut) et centres caméra (4 px) sont des disques
de taille écran, indépendants de l'échelle SfM et du GPX. Deux curseurs dans
la section COLMAP permettent d'ajuster leur diamètre sans recharger le modèle.
La densité de pixels de l'écran est prise en compte (facteur maximal 2).

Un dossier COLMAP peut aussi être glissé directement dans la vue : le viewer
parcourt le dossier, repère un même sous-dossier contenant `cameras.bin`,
`images.bin` et `points3D.bin`, puis le charge avec le même importeur que le
bouton. Les dépôts de scène, JSON et GPX gardent leur comportement existant.

Glisser un fichier `.splat` ou `.ply` dans la vue l'ajoute aux scènes déjà
chargées sans recadrer ni déplacer la caméra. Le bouton d'ouverture conserve
son comportement de remplacement. Le panneau d'informations regroupe les
cases de visibilité des scènes splat, des points sparse, des centres et
frustums COLMAP, ainsi que de la trajectoire GPS.

La profondeur des frustums vaut 1 % du rayon de l'étendue des centres caméra,
bornée aux quantiles 2/98 % pour limiter l'effet des caméras aberrantes.
Il n'y a pas de taille minimale absolue : l'affichage suit aussi les scènes
SfM sans GPX dont l'échelle est arbitraire. Si les centres sont confondus,
le rayon de la scène sert de référence.
