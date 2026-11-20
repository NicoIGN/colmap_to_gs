# Panoramax

[Retour aux exemples](README.md) · [Documentation Panoramax](../tools/panoramax/README.md)

## Télécharger les images d'une emprise

Adapter la bbox `xmin,ymin,xmax,ymax`, ici en Lambert-93 (`EPSG:2154`), et
le dossier de sortie :

```bash
bash "$GIT_ROOT/tools/panoramax/download.sh" \
  --bbox "651000,6861000,652000,6862000" \
  --crs EPSG:2154 --output-dir "$DATA_ROOT/panoramax"
```

Les images et leurs métadonnées sont enregistrées dans un dossier par collection STAC,
`$DATA_ROOT/panoramax/<id-collection>/images/` et `.../metadata/`. Si une collection
mélange plusieurs tailles d'image, elle est répartie dans `<id-collection>-<LxH>/`.
Un accès réseau et les dépendances de
[`requirements.txt`](../tools/panoramax/requirements.txt) sont nécessaires.

## Filtrer par producteur ou par logiciel EXIF

`--filter KEY=VALEURS` est répétable. Les valeurs séparées par des virgules sont en OU,
les filtres successifs en ET, sans tenir compte de la casse. `KEY` peut être une propriété
STAC (`geovisio:producer`, `collection`) ou un tag EXIF (`Exif.Image.Software`).

```bash
bash "$GIT_ROOT/tools/panoramax/download.sh" \
  --bbox "651000,6861000,652000,6862000" \
  --crs EPSG:2154 --output-dir "$DATA_ROOT/panoramax" \
  --filter geovisio:producer=immergis,overflorian \
  --filter Exif.Image.Software=H19.03.02.02.00
```

Pour ne garder qu'une collection précise, utiliser son identifiant STAC :

```bash
bash "$GIT_ROOT/tools/panoramax/download.sh" \
  --bbox "651000,6861000,652000,6862000" \
  --crs EPSG:2154 --output-dir "$DATA_ROOT/panoramax" \
  --filter collection=<uuid-collection>
```

Les filtres sont enregistrés dans `request.json`. Pour lister les valeurs disponibles avant de
filtrer, consulter les `properties` des métadonnées téléchargées.

## Lancer le pipeline complet

Après avoir préparé un fichier `config-panoramax.sh` contenant au minimum
`OUTPUT_DIR` et `PANORAMAX_BBOX` :

```bash
bash "$GIT_ROOT/scripts/panoramax_to_3dgs.sh" "$DATA_ROOT/config-panoramax.sh"
```

Voir aussi l'[exemple du pipeline Panoramax en direct et sur Slurm](pipelines.md).

Pour les panoramas 360 avec HLOC, ajouter à la configuration
`PANORAMAX_PICTURE_TYPE=360`, `COLMAP_METHOD=hloc` et
`HLOC_CAMERA_MODEL=EQUIRECTANGULAR`. Les prérequis de reconstruction et
d'entraînement et les limites de ce mode sont décrits dans la documentation.
