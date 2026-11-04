# `.splat` vers 3D Tiles

Ce sous-outil convertit un `.splat` produit par ce dépôt en tileset 3D Tiles 1.1 contenant des Gaussian Splats. Il s'appuie sur le convertisseur externe [`3dgs-ply-3dtiles-converter`](https://github.com/razor-ai/3dgs-ply-3dtiles-converter), qui construit les LOD et compresse le contenu des tuiles en SPZ.

## Données prises en charge

Cette première version vise les `.splat` produits par `tools/postprocess/export_ply.py` après le géoréférencement GPX de `tools/preprocess/georef_colmap_from_gpx.py`. Les positions doivent être en repère **local ENU (Est, Nord, Haut), en mètres**, et l'origine WGS84 `[latitude, longitude, hauteur]` doit provenir du `georef_summary.json` du même traitement.

Le `georef_summary.json` de ce dépôt contient un tableau `models` et pointe vers un `georef_stats.json` par modèle. Le convertisseur lit l'origine ENU dans ce fichier de statistiques. Pour un résumé avec un seul modèle, celui-ci est sélectionné automatiquement ; s'il y en a plusieurs, indique le modèle avec `--model`.

Le script valide ces métadonnées et les passe au convertisseur 3D Tiles. Il refuse les fichiers sans rapport géographique compatible au lieu de deviner le placement. Les données altimétriques du dépôt sont les altitudes GPX brutes, sans correction de géoïde ; le placement vertical dans Cesium hérite de cette limite.

## Prérequis

- Python 3.10+ avec NumPy (déjà requis par le pipeline).
- Node.js 18+ et npm.
- Le convertisseur 3D Tiles installé et accessible dans le `PATH` :

```bash
npm install --global 3dgs-ply-3dtiles-converter
```

Le premier lancement du convertisseur peut ouvrir son inspecteur ; la commande ci-dessous le désactive pour un traitement batch.

## Utilisation

```bash
python3 tools/splat_to_3dtiles/convert.py \
  --input /chemin/vers/scene.splat \
  --georef-summary /chemin/vers/georef_summary.json \
  --output-dir /chemin/vers/scene_tiles \
  --no-open-inspector
```

Pour un résumé contenant plusieurs modèles :

```bash
python3 tools/splat_to_3dtiles/convert.py \
  --input /chemin/vers/scene.splat \
  --georef-summary /chemin/vers/georef_summary.json \
  --model "." \
  --output-dir /chemin/vers/scene_tiles \
  --no-open-inspector
```

Le résultat est `scene_tiles/tileset.json` et les contenus référencés sous `scene_tiles/tiles/`. Le répertoire de sortie doit ne pas exister ; `--overwrite` autorise explicitement le convertisseur à le remplacer.

## Conversion intermédiaire

Le `.splat` compact est transformé en PLY 3DGS binaire temporaire, puis ce PLY est passé au convertisseur. Les positions et orientations sont préservées ; les couleurs DC et l'opacité sont décodées depuis le `.splat`. Le format `.splat` ne stocke pas les harmoniques sphériques d'ordre supérieur, elles ne peuvent donc pas être recréées par cette conversion.

Les commentaires `offsetx=0`, `offsety=0`, `offsetz=0` du PLY identifient le repère local Z-up au convertisseur. La coordonnée d'origine fournit son placement ENU dans le monde, sans ajouter artificiellement cette origine aux positions locales.

## Validation

Un lanceur local est fourni, sans dépendance Python externe ni jeton Cesium ion :

```bash
bash tools/viewer/3dtiles/view.sh /chemin/vers/scene_tiles/tileset.json
```

Il ouvre une page CesiumJS centrée sur le tileset, avec un fond OpenStreetMap
et un bouton pour revenir à la scène. `--port 8080` permet de changer de port ;
`--no-open` désactive l'ouverture automatique du navigateur. Le serveur écoute
uniquement sur `127.0.0.1` et s'arrête avec `Ctrl+C`.

Une connexion Internet est nécessaire pour CesiumJS (version 1.147.0, chargée depuis
jsDelivr) et le fond OSM. Aucun fichier du tileset n'est envoyé à un service externe.
Le serveur expose les fichiers situés dans le dossier contenant le `tileset.json`,
mais pas ceux situés en dehors : les contenus référencés doivent donc se trouver
sous ce dossier. Le fond est sans relief (ellipsoïde WGS84), pas un modèle de terrain.

Après conversion, ouvrir le tileset dans CesiumJS ou l'inspecteur 3D Tiles et vérifier le placement contre des repères connus. Vérifier également l'altitude : un alignement horizontal correct ne garantit pas la cohérence du référentiel vertical.

Cette version ne prend pas en charge les `.splat` en coordonnées projetées (par exemple Lambert-93) ou ECEF, ni d'autres conventions d'axes. Ces entrées nécessitent une transformation cartographique explicite et ne doivent pas être passées en mode ENU.
