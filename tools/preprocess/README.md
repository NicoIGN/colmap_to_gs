# Préparation des données

[Retour à la documentation générale](../../doc/guide_utilisateur.md)

## 1. Objectif

Outils de préparation d’images pour COLMAP, géoréférencement et découpage de reconstructions, et filtrage expérimental du sparse.

Exemple d’invocation :

```bash
python3 tools/preprocess/images_to_colmap.py --help
```

## 2. Entrées et sorties

- Exécuter les scripts depuis la racine du dépôt :

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

- Les dépendances sont celles de l'environnement du projet.
- Les outils externes requis dépendent de la commande (COLMAP, ffmpeg, exiftool, etc.).

- `tools/libs/python/colmap_to_transforms.py` : conversion pour [Nerfstudio](../../doc/nerfstudio.md),
  appelée par `tools/libs/scripts/run.sh`.
- `tools/libs/python/estimate_planes.py` : estimation des plans, appelée par `tools/libs/scripts/run.sh`.

## 3. Configuration et utilisation

### Découpage et entraînement Spirula par blocs

Pour installer Spirula, consulter [son README](../spirula/README.md).

Depuis n'importe quel dossier, avec l'environnement Python du projet activé :

```bash
DIR=/chemin/vers/panoramax SPLIT_NUM_BLOCKS=4 \
  bash /chemin/vers/colmap_to_gs/tools/libs/scripts/run_spirula_blocks.sh
```

- **SfM :** réutilise `DIR/sfm-TRAIN_NUM_ITER` s'il est complet ; sinon, lance
  la [reconstruction Panoramax](../panoramax/README.md).
- **Découpage :** applique ce script à `sparse/0` et écrit les blocs dans
  `blocks-SPLIT_NUM_BLOCKS/`.
- **Entraînement :** entraîne chaque bloc indépendamment dans
  `DIR/gs-TRAIN_NUM_ITER-blocks-SPLIT_NUM_BLOCKS/block_NNN/`.
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

Les outils de `tools/preprocess/` préparent les images et les reconstructions
COLMAP. En particulier, `filter_sparse_density.py` cherche les points 3D
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
  `SFM_THREADS` ; augmenter cette valeur peut accélérer cette étape si autant
  de CPU sont alloués au job.
- La progression s'affiche par défaut ; `--verbose` ajoute des détails et
  `--no-progress` désactive les barres de progression.
- Dépendances du filtre : `numpy`, `scipy`, `tqdm`.

## 5. Notes et informations complémentaires

Il s'agit d'un filtre à valider visuellement :
tester le sparse original et sa sortie dans le [viewer](../viewer/README.md) avant de l'utiliser pour
l'entraînement.
