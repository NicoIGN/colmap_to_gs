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
- Les fichiers `.splat` et `.ply` ajoutés par glisser-déposer s'ajoutent à la
  scène déjà ouverte. Le bouton d'ouverture remplace la scène.
- Dans une vue vide, le visualiseur cadre automatiquement le modèle COLMAP.
- Les commandes de souris permettent d'orbiter, déplacer et cadrer la vue ;
  les mesures restent accessibles avec Maj + clic.
- L'affichage des points et caméras conserve une taille lisible à l'écran,
  même si l'échelle du modèle COLMAP est inconnue.

## 5. Notes et informations complémentaires

La profondeur des frustums vaut 1 % du rayon de l'étendue des centres caméra,
bornée aux quantiles 2/98 % pour limiter l'effet des caméras aberrantes.
Il n'y a pas de taille minimale absolue : l'affichage suit aussi les scènes
SfM sans GPX dont l'échelle est arbitraire. Si les centres sont confondus,
le rayon de la scène sert de référence.
