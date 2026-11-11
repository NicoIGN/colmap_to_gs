# `.splat` vers 3D Tiles

## 1. Objectif

Convertir un `.splat` local géoréférencé en tileset 3D Tiles 1.1 contenant des Gaussian Splats.

Exemple d’invocation :

```bash
python3 tools/splat_to_3dtiles/convert.py \
  --input /chemin/vers/scene.splat \
  --georef-summary /chemin/vers/georef_summary.json \
  --output-dir /chemin/vers/scene_tiles \
  --no-open-inspector
```

Le convertisseur externe [`3dgs-ply-3dtiles-converter`](https://github.com/razor-ai/3dgs-ply-3dtiles-converter) construit les LOD et compresse le contenu des tuiles en SPZ.

## 2. Entrées et sorties

Cette première version vise les `.splat` produits par `tools/postprocess/export_ply.py` après le géoréférencement GPX de `tools/preprocess/georef_colmap_from_gpx.py`. Les positions doivent être en repère **local ENU (Est, Nord, Haut), en mètres**, et l'origine WGS84 `[latitude, longitude, hauteur]` doit provenir du `georef_summary.json` du même traitement.

Le `georef_summary.json` de ce dépôt contient un tableau `models` et pointe vers un `georef_stats.json` par modèle. Le convertisseur lit l'origine ENU dans ce fichier de statistiques. Pour un résumé avec un seul modèle, celui-ci est sélectionné automatiquement ; s'il y en a plusieurs, indique le modèle avec `--model`.

Le script valide ces métadonnées et les passe au convertisseur 3D Tiles. Il refuse les fichiers sans rapport géographique compatible au lieu de deviner le placement. Les données altimétriques du dépôt sont les altitudes GPX brutes, sans correction de géoïde ; le placement vertical dans Cesium hérite de cette limite.

## 3. Configuration et utilisation

- Python 3.10+ avec NumPy (déjà requis par le pipeline).
- Node.js 18+ et npm.
- Le convertisseur 3D Tiles installé et accessible dans le `PATH` :

```bash
npm install --global 3dgs-ply-3dtiles-converter
```

Le premier lancement du convertisseur peut ouvrir son inspecteur ; la commande ci-dessous le désactive pour un traitement batch.

Options de `convert.py` :

- `--input PATH`, `--georef-summary PATH`, `--output-dir PATH` : paramètres requis.
- `--model NAME` : modèle du résumé géographique ; facultatif avec un seul modèle, requis s'il y en a plusieurs.
- `--converter COMMAND` : commande du convertisseur installé ; défaut `3dgs-ply-3dtiles-converter`.
- `--overwrite` : autorise le remplacement du dossier de sortie.
- `--no-open-inspector` : désactive l'ouverture de l'inspecteur après conversion.


### Utilisation


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

## 4. Fonctionnement internes

Le `.splat` compact est transformé en PLY 3DGS binaire temporaire, puis passé au convertisseur externe, qui construit les LOD et compresse le contenu des tuiles en SPZ. Les positions et orientations sont préservées ; les couleurs DC et l'opacité sont décodées depuis le `.splat`. Le format `.splat` ne stocke pas les harmoniques sphériques d'ordre supérieur, elles ne peuvent donc pas être recréées par cette conversion.

Les commentaires `offsetx=0`, `offsety=0`, `offsetz=0` du PLY identifient le repère local Z-up au convertisseur. La coordonnée d'origine fournit son placement ENU dans le monde, sans ajouter artificiellement cette origine aux positions locales.

## 5. Notes et informations complémentaires

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
