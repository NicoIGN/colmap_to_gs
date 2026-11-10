# Préparation des données

Ces outils autonomes étaient dans `python/`. Depuis la racine du dépôt :

```bash
python3 tools/preprocess/images_to_colmap.py --help
```

| Outil | Fonction |
|---|---|
| `extract_frames_from_video.py` | Extraction d'images depuis une vidéo |
| `image_info.py` | Inspection d'une image et de ses informations GPS |
| `images_to_colmap.py` | Reconstruction COLMAP depuis des images |
| `georef_colmap_from_gpx.py` | Recalage d'un modèle COLMAP sur une trace GPX |
| `colmap_split_blocks.py` | Découpage d'un modèle COLMAP en blocs |
| `filter_sparse_density.py` | Filtrage expérimental des points sparse isolés et lointains |

Les arguments et formats ne changent pas ; seul le chemin des scripts change.
Les dépendances restent celles de l'environnement du projet, et les outils
externes requis dépendent de la commande (COLMAP, ffmpeg, exiftool, etc.).

Les composants internes de Nerfstudio `colmap_to_transforms.py` et
`estimate_planes.py` restent dans `python/` et sont appelés par `scripts/run.sh`.

### Filtrage expérimental du sparse par densité

```bash
python3 tools/preprocess/filter_sparse_density.py \
  --input /chemin/vers/sparse/0 \
  --output /chemin/vers/sparse_density_filtered/0
```

Le filtre analyse, pour chaque image, les profondeurs des points observés dans
un cône angulaire (5° et jusqu'à 128 voisins par défaut). Une KDE gaussienne
repère les paquets de profondeur ; un paquet doit représenter au moins
`max(5 points, 5 % du voisinage)`. Un point n'est proposé au retrait que s'il
est derrière tous les paquets soutenus, en dehors de leur enveloppe à 3σ et à
faible densité, avec confirmation par au moins 75 % de deux vues évaluables.
Les voisinages insuffisants sont conservés.

La sortie est un nouveau modèle COLMAP binaire : les références aux points
retirés sont remplacées par `-1` dans `images.bin`. Le modèle d'entrée reste
intact. `density_report.json` récapitule les paramètres, les points rejetés et
leurs votes ; `points3D_kept.ply` et `points3D_removed.ply` permettent de
comparer le résultat dans un viewer. Les éventuels `rigs.bin` et `frames.bin`
sont également conservés. Il s'agit d'un filtre à valider visuellement :
tester le sparse original et sa sortie dans le viewer avant de l'utiliser pour
l'entraînement.

### Découpage et entraînement Spirula par blocs

Depuis n'importe quel dossier, avec l'environnement Python du projet activé :

```bash
DIR=/chemin/vers/panoramax SPLIT_NUM_BLOCKS=4 \
  bash /chemin/vers/colmap_to_gs/scripts/run_spirula_blocks.sh
```

Le lanceur réutilise le SfM existant `DIR/sfm-TRAIN_NUM_ITER` s'il est complet ;
sinon il lance la reconstruction Panoramax. Il découpe ensuite `sparse/0`
avec ce script dans `blocks-SPLIT_NUM_BLOCKS/`, puis entraîne chaque bloc
indépendamment dans `DIR/gs-TRAIN_NUM_ITER-blocks-SPLIT_NUM_BLOCKS/block_NNN/`.
Le manifeste et les blocs COLMAP complets sont réutilisés à la relance.
Les sorties de découpage partielles ou incohérentes ne sont jamais supprimées
automatiquement. `SPLIT_NUM_BLOCKS`, `TRAIN_NUM_ITER`, `TRAIN_CAP_MAX` et les paramètres SfM
sont configurables par variables d'environnement ; voir `--help`.

Le découpage spectral recherche les séparations de faible covisibilité
(peu de points 3D partagés, pondérés par la distance entre caméras).
Un ajustement glouton des frontières vise ensuite environ `N / SPLIT_NUM_BLOCKS`
images principales par bloc, avec une tolérance de 20 % arrondie à l'entier.
Il privilégie les déplacements coupant le moins de poids et ne déconnecte
pas un bloc donneur connexe. Si aucun déplacement sûr n'est possible,
les tailles inégales sont acceptées avec un avertissement : l'équilibrage
n'arrête plus le pipeline. Le recouvrement est ajouté après cet ajustement
et peut augmenter les effectifs finaux.

`TRAIN_CAP_MAX` s'applique à chaque bloc séparément : le plafond cumulé peut donc
atteindre `SPLIT_NUM_BLOCKS × TRAIN_CAP_MAX` Gaussians.
