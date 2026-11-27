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

Lancer le téléchargement dans l'env `lidar-pdal` (créé par `environment/ign.slurm/pdal_env.yml`) :

```bash
micromamba -n lidar-pdal run bash "$GIT_ROOT/tools/panoramax/download.sh" \
  --bbox "655150,6861000,656000,6861400" --crs EPSG:2154 \
  --dry-run --output-dir ./
```

`--dry-run` affiche ce qui serait téléchargé sans écrire de fichiers.

Les images et leurs métadonnées sont enregistrées dans un dossier par collection STAC,
`$DATA_ROOT/panoramax/<id-collection>/images/` et `.../metadata/`. Si une collection
mélange plusieurs tailles d'image, elle est répartie dans `<id-collection>-<LxH>/`.
Un accès réseau et les dépendances de
[`requirements.txt`](../tools/panoramax/requirements.txt) sont nécessaires.

## Filtrer par producteur ou par logiciel EXIF

`--filter KEY=VALEURS` est répétable. La logique est la suivante :

- **OU dans une valeur liste** : `KEY=A,B` garde une photo dont `KEY` vaut `A` **ou** `B`.
- **ET entre filtres** : `--filter K1=A --filter K2=B` garde une photo qui vérifie
  `K1=A` **et** `K2=B`.
- Une photo sans la propriété `KEY` est exclue. La casse est ignorée.
- `KEY` peut être une propriété STAC (`geovisio:producer`, `collection`) ou un tag EXIF
  (`Exif.Image.Software`).

Exemple, avec les filtres suivants :

| Photo | `geovisio:producer` | `Exif.Image.Software` | Gardée ? |
|---|---|---|---|
| 1 | `immergis` | `H19.03.02.02.00` | oui (producteur OU, logiciel ET) |
| 2 | `overflorian` | `H19.03.02.02.00` | oui |
| 3 | `immergis` | `imajview 5` | non (logiciel ne correspond pas) |
| 4 | `benoitdd` | `H19.03.02.02.00` | non (producteur ne correspond pas) |

Piège : répéter la **même** clé combine les filtres en ET. `--filter geovisio:producer=immergis
--filter geovisio:producer=overflorian` ne garde aucune photo, car une photo n'a qu'un seul
producteur. Pour un OU sur une même clé, mettre les valeurs dans une seule liste.

```bash
bash "$GIT_ROOT/tools/panoramax/download.sh" \
  --bbox "651000,6861000,652000,6862000" \
  --crs EPSG:2154 --output-dir "$DATA_ROOT/panoramax" \
  --filter geovisio:producer=immergis,overflorian \
  --filter Exif.Image.Software=H19.03.02.02.00
```

Par défaut, le téléchargement affiche pour chaque filtre (type, provider, `--filter`) le nombre de
photos conservées et filtrées, par valeur, par exemple `provider immergis : 75 conservé`. Ajouter
`--no-verbose` pour désactiver ce rapport.

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
bash "$GIT_ROOT/scripts/launch_panoramax_to_3dgs.sh" "$DATA_ROOT/config-panoramax.sh"
```

Voir aussi l'[exemple du pipeline Panoramax en direct et sur Slurm](../scripts/README.md).

Pour filtrer les photos du pipeline complet, utiliser `PANORAMAX_FILTERS` :
`;` sépare les propriétés (ET), `,` sépare les valeurs d'une propriété (OU), la
comparaison est insensible à la casse. La syntaxe ci-dessous garde donc les photos
`immergis` **ou** `overflorian`, **et** dont le logiciel EXIF est `H19.03.02.02.00` :

```bash
PANORAMAX_FILTERS="geovisio:producer=immergis,overflorian;Exif.Image.Software=H19.03.02.02.00"
```

Équivalent en ligne de commande : `--filter geovisio:producer=immergis,overflorian
--filter Exif.Image.Software=H19.03.02.02.00`. Une même clé ne peut pas apparaître deux fois
dans `PANORAMAX_FILTERS` pour exprimer un OU ; utiliser la liste `,` à la place.

Ce filtre est transmis à `download.py` sous forme de `--filter` répétés et enregistré dans
`request.json` ; changer `PANORAMAX_FILTERS` sur un `OUTPUT_DIR` existant est refusé.

Pour ne garder que les photos d'un provider STAC (`properties.providers[].name`,
insensible à la casse), utiliser `PANORAMAX_PROVIDER` :

```bash
PANORAMAX_PROVIDER="gaelle.gabriel@paris.fr"
```

Équivalent en ligne de commande : `--provider gaelle.gabriel@paris.fr`. Sur les photos
ayant un seul provider, cela revient à `PANORAMAX_FILTERS="geovisio:producer=gaelle.gabriel@paris.fr"`.

Pour les panoramas 360 avec HLOC, ajouter à la configuration
`PANORAMAX_PICTURE_TYPE=360`, `COLMAP_METHOD=hloc` et
`HLOC_CAMERA_MODEL=EQUIRECTANGULAR`. Les prérequis de reconstruction et
d'entraînement et les limites de ce mode sont décrits dans la documentation.
`PANORAMAX_PICTURE_TYPE=360` ou `normal` filtre le téléchargement : le type STAC
ou EXIF de la collection est utilisé ; si absent, le XMP GPano du JPEG téléchargé
décide (les photos sans type ni GPano sont considérées comme non 360).
