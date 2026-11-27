# Récupération de données vecteur

`fetch_vector.py` télécharge une couche vecteur dans une emprise Lambert-93 (`EPSG:2154`) et l'écrit dans un GeoPackage.

Sources :
- `--source ign` (défaut) : BD TOPO via le WFS de la Géoplateforme, avec pagination.
- `--source osm` : OpenStreetMap via l'API Overpass (ways uniquement ; les relations multipolygones ne sont pas récupérées).

Couches (`--layer`) :

| Couche | IGN (BD TOPO) | OSM |
|---|---|---|
| `building` (défaut) | `BDTOPO_V3:batiment` | `way["building"]` |
| `road` | `BDTOPO_V3:troncon_de_route` | `way["highway"]` |
| `water` | `BDTOPO_V3:surface_hydrographique` | `way["natural"="water"]`, `way["waterway"]` |
| `vegetation` | `BDTOPO_V3:zone_de_vegetation` | `way["natural"="wood"]`, `way["landuse"="forest"]` |

Pour `--source ign`, un nom WFS complet (ex. `BDTOPO_V3:cours_d_eau`) est aussi accepté.

## Usage

```bash
pip install -r "$GIT_ROOT/tools/vector/requirements.txt"

python "$GIT_ROOT/tools/vector/fetch_vector.py" \
  --bbox 666201,6859851,666701,6860351 \
  --layer building \
  --output-dir "$DATA_ROOT/vector"

python "$GIT_ROOT/tools/vector/fetch_vector.py" \
  --bbox 666201,6859851,666701,6860351 \
  --source osm --layer road \
  --output-dir "$DATA_ROOT/vector"
```

Le fichier produit est `<output-dir>/<source>_<couche>.gpkg` (ex. `ign_building.gpkg`), avec une couche du même nom. Un fichier existant est remplacé.

Le proxy se configure via les variables `HTTPS_PROXY` / `HTTP_PROXY` (utilisées par `urllib`).

## Tests

```bash
python -m unittest discover -s "$GIT_ROOT/tests/vector" -p 'test_*.py'
```

Les tests simulent les réponses WFS et Overpass. Une vérification sur les services réels reste à faire.
