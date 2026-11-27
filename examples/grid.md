# Mode GRID : dalles géoréférencées

[Retour aux exemples](README.md) · [Pipeline Panoramax](panoramax.md)

Le mode GRID découpe une emprise géoréférencée en dalles. Chaque dalle est un calcul
indépendant (Panoramax ou images avec GPS EXIF), calculé avec une marge, puis clippé
à l'emprise de la dalle. Chaque dalle produit son splat dans `GRID_OUTPUT_DIR/splats/`.
Il n'y a pas d'export fusionné.

Contraintes vérifiées avant calcul :

- `SPLIT_NUM_BLOCKS=1` (chaque dalle est déjà un bloc).
- `NUM_CPUS x NUM_GPUS < 64`.
- Images : chaque image doit avoir une position, soit dans le fichier `GRID_POSITIONS`
  (WGS84, lignes `nom_image lat lon [alt]`, prioritaire), soit dans son GPS EXIF. Une image
  sans position déclenche une erreur.
- Panoramax : les positions viennent toujours de l'EXIF. `GRID_POSITIONS` et un
  `SFM_SP_METRIC_POSITIONS` global sont refusés en mode panoramax, car la liste d'images d'une
  dalle n'est connue qu'au moment du téléchargement.

Configuration (uniquement les surcharges) :

```bash
cat > "$DATA_ROOT/config-grid.sh" <<'EOF'
#!/usr/bin/env bash
GRID_BBOX="651000,6861000,653000,6863000"
GRID_CRS="2154"
GRID_TILE_SIZE="500"
GRID_OVERLAP="20"
GRID_MARGIN="30"
OUTPUT_DIR="/chemin/sortie"
PANORAMAX_BBOX_EPSG="2154"
NUM_CPUS="4"
NUM_GPUS="2"
EOF
```

La source est choisie par le lanceur, pas par la configuration :

- Panoramax : `launch_panoramax_to_3dgs.sh CONFIG --grid`
- Images : `launch_images_to_3dgs.sh CONFIG --grid`, avec `SFM_IMAGES_DIR="/chemin/photos"` dans
  la configuration. Les positions peuvent venir d'un fichier externe :
  `GRID_POSITIONS="/chemin/positions.txt"` (lignes `nom_image lat lon [alt]`, WGS84).

Vérifier le découpage sans calcul :

```bash
bash "$GIT_ROOT/scripts/launch_panoramax_to_3dgs.sh" "$DATA_ROOT/config-grid.sh" --grid --plan-only
```

Lancer les calculs :

```bash
bash "$GIT_ROOT/scripts/launch_panoramax_to_3dgs.sh" "$DATA_ROOT/config-grid.sh" --grid          # local, bloquant
bash "$GIT_ROOT/scripts/launch_panoramax_to_3dgs.sh" "$DATA_ROOT/config-grid.sh" --grid --slurm  # job Slurm
```

`--slurm` soumet un job unique (`NUM_GPUS` GPU, `NUM_CPUS x NUM_GPUS` CPU) qui relance le même
lanceur en local ; il exige `SLURM_PARTITION`, `SLURM_MEMORY` et `SLURM_TIME_LIMIT` renseignés.
Progression : `GRID_OUTPUT_DIR/logs/grid.log` ; logs par dalle : `GRID_OUTPUT_DIR/work/<dalle>/tile.log`.

Une configuration qui contient des `GRID_*` est refusée par les lanceurs sans `--grid` et par
les scripts `images_to_3dgs.sh` / `panoramax_to_3dgs.sh`. Le contrôle lit le fichier de
configuration : des `GRID_*` définis dans un fichier sourcé ne sont pas détectés.

Sorties, dans `GRID_OUTPUT_DIR` (défaut `OUTPUT_DIR/grid`) :

- `manifest.json` : emprises de chaque dalle (`core` exporté, `compute` calculé).
- `splats/<dalle>.splat` : résultat clippé à l'emprise `core`.
- `logs/<dalle>.log` et `logs/failed.txt` en cas d'échec.
- `work/<dalle>/` : calcul intermédiaire, avec `tile.conf` et les liens d'images en mode images.
  En mode images, `work/<dalle>/images/` contient des liens symboliques vers les photos d'origine
  (aucune copie).
- `work/<dalle>/metric_positions.txt` (mode images avec positions) : fichier `SFM_SP_METRIC_POSITIONS`
  propre à la dalle, passé au backend Spirula via `tile.conf`. Son en-tête `# origin_lat/lon/alt` est
  le centre de la dalle ; seules les images de la dalle y figurent. Chaque dalle a donc son propre repère
  et son propre géoréférencement.

Les dalles déjà présentes dans `splats/` sont ignorées ; `--redo` recalcule tout.

Règles de découpage :

- Les dalles ont la taille `GRID_TILE_SIZE` et se recouvrent de `GRID_OVERLAP` mètres.
- La marge `GRID_MARGIN` élargit le calcul ; seul le splat à l'intérieur de la dalle est gardé.
- Le clip utilise `filter_splat_by_bbox.py` : un splat est gardé si son support est dans la dalle.
  Un recouvrement supérieur à quelques tailles de splat évite les trous aux bords.
- Une dalle sans image (mode images) est ignorée avec un avertissement.

Parallélisme : `NUM_GPUS` workers. Le worker `w` utilise `CUDA_VISIBLE_DEVICES=w` et traite les dalles
d'indice `i` avec `i % NUM_GPUS = w`. Chaque dalle tourne sur un GPU et `NUM_CPUS` CPU.
