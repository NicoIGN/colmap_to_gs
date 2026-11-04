# Télécharger des photos Panoramax par bbox Lambert-93

Ce lanceur recherche les photos du catalogue fédéré Panoramax via `/api/search`,
pagine jusqu'au dernier résultat et télécharge les images HD disponibles.
Il n'a pas besoin de compte Panoramax.

## Installation

Dans ton environnement Python actif (Python 3.10 ou plus récent) :

```bash
python3 -m pip install -r tools/panoramax/requirements.txt
```

`pyproj` est déjà présent dans l'environnement Conda fourni par ce dépôt.

## Utilisation

Depuis la racine du dépôt :

```bash
bash tools/panoramax/download.sh \
  --bbox "651000,6861000,652000,6862000" \
  --output-dir /chemin/vers/panoramax
```

L'ordre est **xmin,ymin,xmax,ymax**, en mètres, en **Lambert-93 (EPSG:2154)**.
Les coordonnées de l'exemple sont illustratives, pas une garantie de présence de photos.
Le script transforme une enveloppe densifiée en WGS84 pour la recherche STAC,
puis vérifie chaque point de prise de vue dans la bbox Lambert-93 initiale.

Options :

| Option | Description |
|---|---|
| `--datetime "2026-01-01T00:00:00Z/2026-10-31T23:59:59Z"` | Intervalle de prise de vue, transmis à l'API |
| `--image-max 400` | Limite le nombre total de photos sélectionnées, toutes les pages confondues |
| `--quality sd` | Résolution SD ; valeurs : `hd` (défaut), `sd`, `thumb` |
| `--api-url https://une-instance/api` | Autre API compatible ; défaut : catalogue fédéré `explore.panoramax.fr` |
| `--timeout 120` | Délai réseau par requête, en secondes |

## Périmètre

« Dans la bbox » signifie **position de prise de vue dans la bbox**, pas contenu
visible dans la photo. Toutes les photos retournées sont candidates, panoramiques
ou non, sans restriction d'auteur ou de séquence. Le script ne télécharge pas les
photos extérieures de séquences qui traversent la zone.

La qualité HD est l'asset public `hd` annoncé par l'API ; elle ne garantit pas
l'accès à l'original brut non flouté. Aucun remplacement silencieux par une qualité
inférieure n'est effectué si l'asset demandé manque.

## Sorties et reprise

```text
output-dir/
├── images/<identifiant-haché>.jpg
├── metadata/<identifiant-haché>.json
├── pages/page-000001.json
├── manifest.jsonl
├── request.json
└── summary.json
```

Les métadonnées conservent le Feature STAC complet : coordonnées, date, séquence,
propriétés, assets, liens de provenance et informations de licence/attribution
présentes dans la réponse. Les fichiers sont nommés à partir d'un hash de
collection + identifiant photo pour éviter collisions et noms dangereux.
Le manifeste associe les identifiants Panoramax aux fichiers locaux.

Relancer la même commande reprend les images dont l'URL et la taille correspondent
aux métadonnées sauvegardées. Les téléchargements utilisent des fichiers temporaires
avant remplacement ; les photos en échec sont indiquées dans le manifeste et
entraînent un code de sortie non nul. Les réponses API brutes sont conservées.
Le manifeste et le résumé décrivent le dernier lancement ; les anciens fichiers
restent présents, donc utiliser un dossier distinct pour une autre sélection.

Les requêtes sont séquentielles, avec trois tentatives en cas d'erreur réseau,
HTTP 429 ou 5xx. Une grande bbox peut représenter un volume important ; commencer
par une petite zone et respecter les conditions d'utilisation et d'attribution
des instances et des photos.

`--image-max` arrête la pagination dès que la limite de photos retenues dans la
bbox est atteinte. La limite inclut les images déjà présentes et réutilisées :
par exemple, relancer avec `--image-max 400` peut reprendre jusqu'à 400 images,
sans compter le nombre de nouveaux téléchargements uniquement.

## Références

- API : https://explore.panoramax.fr/swagger
- CLI officielle : https://docs.panoramax.fr/cli/COMMANDS/#panoramax_cli-download
- Catalogue GeoParquet (alternative pour les grandes recherches) :
  https://docs.panoramax.fr/federated-catalog/tutorials/duckdb/

## Vérification locale

```bash
python3 -m unittest discover -s tools/panoramax -p 'test_*.py'
```

Les tests utilisent une API HTTP locale pour vérifier la pagination, la sélection
géographique, la reprise, les erreurs de téléchargement et les nouvelles tentatives.

## Reconstruction Spirula géoréférencée par l'EXIF

Les JPEG HD de Panoramax conservent le GPS EXIF (latitude, longitude,
altitude, cap). Spirula s'en sert directement pour placer le modèle dans un
repère local est-nord-haut en mètres, sans recalage GPX a posteriori :

```bash
bash scripts/run_spirula_sfm.sh \
  --spirula-bin /chemin/vers/spirula \
  --dataset OUTPUT_DIR \
  --output-dir OUTPUT_DIR/sfm \
  --preset panoramax
```

`--preset panoramax` règle `--data-type individual`, `--pairs auto`, aucune
séquence (les noms de fichiers sont des empreintes), `--metric-gps full` et
`--sensor-pairs --sensor-pair-radius 30`. Un code de sortie 4 signifie que le
modèle a été écrit sans le repère métrique demandé.

Le préréglage lance d'abord `prepare_sfm.py` :

- seules les images de la taille la plus fréquente sont gardées (une bbox
  mélange souvent plusieurs appareils : 360° en 2:1, téléphones...) ; elles
  sont liées par lien physique dans `OUTPUT_DIR/sfm/images`, sans toucher
  aux originaux ; `--keep-size LxH` impose une autre taille ;
- la focale initiale est déduite du champ de vue horizontal des métadonnées,
  `f = (largeur / 2) / tan(fov / 2)`, soit environ 1877 px pour une imajbox
  4096×2160 à 95° ; l'EXIF HD n'ayant pas de focale, Spirula la devinerait
  sinon très loin ; `--focal` reste prioritaire ;
- le choix est résumé dans `OUTPUT_DIR/sfm/selection.json`.

`--no-prepare` lance Spirula sur le dataset tel quel. Le script se lance
seul :

```bash
python3 tools/panoramax/prepare_sfm.py --dataset OUTPUT_DIR \
  --output-dir OUTPUT_DIR/sfm --dry-run
```
