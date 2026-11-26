# Préparation des données

[Retour à la documentation générale](../../doc/guide_utilisateur.md)

## 1. Objectif

Outils de préparation d’images et d’extraction de frames (vidéo, image info), et découpage 360° équirectangulaire. Le SfM et le filtrage sparse sont dans [`tools/sfm`](../sfm/README.md).

Exemple d’invocation :

```bash
python3 tools/prepare/prepare_video_frames.py --help
```

## 2. Entrées et sorties

- Exécuter les scripts depuis la racine du dépôt :

```bash
python3 tools/prepare/prepare_video_frames.py --help
```

| Outil | Fonction |
|---|---|
| `prepare_video_frames.py` | Extraction d'un nombre fixe d'images nettes et réparties sur la durée, depuis une ou plusieurs vidéos (dépendances : `opencv-python-headless`, `numpy`, `tqdm`) |
| `prepare_image_info.py` | Inspection d'une image et de ses informations GPS |

Les outils COLMAP (`images_to_colmap.py`, `georef_colmap_from_gpx.py`,
`colmap_split_blocks.py`, `filter_sparse_density.py`, `diagnose_colmap_models.py`)
ont été déplacés dans [`tools/sfm`](../sfm/README.md).

`prepare_video_frames.py` retient une frame par tranche temporelle non vide,
sans éliminer globalement les séquences floues ou similaires. Le score combine
la netteté relative à la plus nette de la tranche et la proximité du centre.
`--sharpness-weight` règle le poids de la netteté entre 0 et 1 (défaut 0,7) ;
1 privilégie la netteté, 0 une répartition plus régulière. La couverture d'une
tranche non vide reste prioritaire quel que soit ce poids.
Le nombre obtenu peut être inférieur à la demande si la vidéo est trop courte.
L'écart maximal entre deux frames est affiché en nombre de frames vidéo et
en secondes approximatives d'après la cadence moyenne.
Utiliser `--index-csv /chemin/frames.csv` avec une seule vidéo pour conserver
l'indice source de chaque frame (indice zéro, pas un horodatage). Les frames
existantes ne sont pas recalculées automatiquement.

- Les dépendances sont celles de l'environnement du projet.
- Les outils externes requis dépendent de la commande (COLMAP, ffmpeg, exiftool, etc.).

- `tools/training/colmap_to_transforms.py` : conversion pour [Nerfstudio](../../doc/nerfstudio.md),
  appelée par `tools/libs/scripts/run.sh`.
- `tools/libs/python/estimate_planes.py` : estimation des plans, appelée par `tools/libs/scripts/run.sh`.

Pour diagnostiquer deux modèles issus d'un même SfM Spirula :

```bash
python tools/sfm/diagnose_colmap_models.py \
  /chemin/sfm/sparse/0 /chemin/sfm/sparse/1 \
  --matches /chemin/sfm/matches.bin
```

`--matches` est facultatif. Les modèles et le fichier de correspondances doivent
provenir du même calcul et employer les mêmes noms d'images. Le fichier ne
permet pas de distinguer une paire non sélectionnée d'une paire rejetée. Les
modèles seuls ne prouvent pas pourquoi la fusion a échoué.

## 3. Configuration et utilisation

### Découpage et entraînement Spirula par blocs

Pour installer Spirula, consulter [son README](../spirula/README.md).

Depuis n'importe quel dossier, avec l'environnement Python du projet activé :

```bash
DIR=/chemin/vers/panoramax SPLIT_NUM_BLOCKS=4 \
  bash /chemin/vers/colmap_to_gs/tools/libs/scripts/run_panoramax.sh
```

- **SfM :** réutilise `DIR/sfm` s'il est complet et si les seuils géométriques
  sont inchangés (indépendant de `TRAIN_NUM_ITER`) ; sinon, lance
  la [reconstruction Panoramax](../panoramax/README.md).
- **Découpage :** applique ce script à `sparse/0` et écrit les blocs dans
  `OUTPUT_DIR/blocks/`.
- **Entraînement :** entraîne chaque bloc indépendamment dans
  `OUTPUT_DIR/gs/block_NNN/` (`gs-nerfstudio/` avec Nerfstudio).
- **Reprise :** réutilise le manifeste et les blocs COLMAP complets. Les sorties
  de découpage partielles ou incohérentes ne sont jamais supprimées automatiquement.
- `SPLIT_NUM_BLOCKS` : nombre de blocs, défini par variable d'environnement.
- `TRAIN_NUM_ITER` : nombre d'itérations, défini par variable d'environnement.
- `TRAIN_SP_CAP_MAX` : plafond d'entraînement Spirula, défini par variable
  d'environnement ; sans effet sur Nerfstudio/Splatfacto.
- Paramètres SfM : définis par variables d'environnement ; voir `--help`.

Le découpage spectral recherche les séparations de faible covisibilité
(peu de points 3D partagés, pondérés par la distance entre caméras).
Un ajustement glouton des frontières vise ensuite environ `N / SPLIT_NUM_BLOCKS`
images principales par bloc, avec une tolérance de 20 % arrondie à l'entier.
Il privilégie les déplacements coupant le moins de poids et ne déconnecte
pas un bloc donneur connexe. Si aucun déplacement sûr n'est possible,
les tailles inégales sont acceptées avec un avertissement : l'équilibrage
n'arrête plus le pipeline. Le recouvrement est ajouté après cet ajustement
et peut augmenter les effectifs finaux.

- `TRAIN_SP_CAP_MAX` s'applique à chaque bloc séparément avec Spirula ; le plafond cumulé peut
  donc atteindre `SPLIT_NUM_BLOCKS × TRAIN_SP_CAP_MAX` Gaussians.

## 4. Fonctionnement interne

Les outils de `tools/prepare/` préparent les images et les frames. Le SfM
(`tools/sfm/`) prépare les reconstructions COLMAP ; en particulier,
`tools/sfm/filter_sparse_density.py` cherche les points 3D
manifestement isolés ou incohérents en les comparant à ce qui est observé
depuis plusieurs images.

- Le filtre est expérimental : examiner visuellement les points conservés et
  écartés avant d'utiliser le résultat pour l'entraînement.
- Le modèle d'entrée est conservé. Le script produit deux modèles COLMAP :
  `*_clean` avec les points retenus et `*_rejected` avec ceux écartés.
- Chaque résultat conserve les caméras et les images ; le rapport
  `density_report.json` résume le filtrage.
- Un paquet plus lointain doit désormais avoir davantage de support que le
  paquet dense le plus proche : le seuil augmente de 25 % de ce support par
  doublement de profondeur, jusqu'à exiger un support comparable. C'est une
  heuristique volontairement plus stricte ; elle peut aussi écarter des détails
  ou arrière-plans réels mais peu reconstruits. Le rapport enregistre
  `far_support_rate`.
- Le pipeline parallélise les requêtes de voisinage angulaire selon
  `NUM_CPUS` ; augmenter cette valeur peut accélérer cette étape si autant
  de CPU sont alloués au job.
- La progression s'affiche par défaut ; `--verbose` ajoute des détails et
  `--no-progress` désactive les barres de progression.
- Dépendances du filtre : `numpy`, `scipy`, `tqdm`.

## 5. Notes et informations complémentaires

Il s'agit d'un filtre à valider visuellement :
tester le sparse original et sa sortie dans le [viewer](../viewer/README.md) avant de l'utiliser pour
l'entraînement.
