# Masques de segmentation pour l'entraînement 3DGS

Outil optionnel : un segmenteur sémantique local produit, pour chaque image,
un masque transmis à l'entraînement Spirula. Par défaut il masque le **ciel**
(les nuages font partie de la classe `sky` ; il n'existe pas de classe
« nuage » distincte dans ADE20K ou Mapillary Vistas). D'autres objets peuvent
être masqués en listant leurs labels.

Deux moteurs sont disponibles via `TRAIN_MASKS_BACKEND` :
`segmentation` (défaut, SegFormer/Mask2Former avec Python) et `spirula`
(SAM natif sur Vulkan). Ce choix change le **calcul** des masques, pas leur
utilisation par l'entraînement.

## Installation

```bash
bash tools/segmentation_masks/install.sh \
  --install-dir /chemin/partage/segmentation-masks \
  --python python3
```

Le script :

1. crée un environnement isolé `INSTALL/venv` (torch, transformers 4.46.3,
   Pillow, SciPy, NumPy >= 1.24 et < 2) ;
2. télécharge le modèle dans `INSTALL/models/<organisation>--<modèle>` ;
3. écrit `labels.txt` (toutes les classes du modèle) dans ce dossier ;
4. vérifie que les labels demandés (`--labels`, défaut `sky`) existent ;
5. affiche les variables à copier dans la configuration.

Options : `--model ID_HUGGING_FACE`, `--labels "sky,person,car"`.
Le nœud d'installation doit accéder à PyPI et Hugging Face ; le calcul utilise
ensuite uniquement le modèle local. Linux (CUDA) et macOS (MPS) sont pris en
charge ; sans GPU, la génération fonctionne sur CPU mais lentement.

### Correction NumPy/PyTorch sur macOS

Les roues PyTorch 2.2.x ne sont pas compatibles avec NumPy 2 (`_ARRAY_API
not found`). L'installateur impose désormais `numpy<2`, y compris dans un
environnement existant, et vérifie le pont NumPy/PyTorch avant de télécharger
le modèle. Relancer la même commande d'installation suffit ; pas besoin de
supprimer le venv ni les poids déjà téléchargés.

### Backend natif Spirula

```bash
TRAIN_MASKS="on"
TRAIN_MASKS_BACKEND="spirula"
TRAIN_MASKS_SPIRULA_MODEL="/chemin/sam3-q4_0.ggml"
TRAIN_MASKS_SPIRULA_DEVICE="auto"
TRAIN_MASKS_LABELS="sky"
TRAIN_MASKS_MODE="auto"
```

Il faut un Spirula compilé avec `SS_BUILD_SAM=ON` (backend Vulkan) et un
checkpoint **SAM 3 GGML**, pas les poids transformers de l'installateur.
Obtenir les poids via Spirula et accepter leur licence Meta avant un lancement
non interactif, avec le même compte et le même HOME que les jobs. Par exemple,
`"$SPIRULA_BIN" sam devices --accept-license sam3` affiche les termes et demande
confirmation sur un terminal. Aucune licence n'est acceptée automatiquement
par ce pipeline.

`TRAIN_MASKS_MODEL` et `TRAIN_MASKS_PYTHON` ne sont pas utilisés dans ce mode :
le calcul est natif, et l'adaptation des PNG utilise `PYTHON_BIN` avec NumPy,
Pillow et SciPy (ce dernier seulement si une dilatation est demandée).
`TRAIN_MASKS_SPIRULA_DEVICE` utilise les noms Vulkan, pas `cuda` ou `mps`.

Les labels deviennent des **prompts anglais libres**, unis par SAM :
`"sky,person,car"` devient `"sky; person; car"`. SAM 2 seul n'accepte pas de
texte ; cette intégration attend donc SAM 3. Chaque image est segmentée
indépendamment (`spirula sam track --mask-mode image --remove-prompted`),
sans suivi vidéo, car les photos Panoramax ne constituent pas une séquence
ordonnée. Les sorties `frame_XXXXX.png` sont renommées pour l'entraînement,
y compris pour des images dans des sous-dossiers. Les formats pris en charge
sont JPEG, PNG, BMP et TIFF ; un autre format provoque une erreur explicite.

`TRAIN_MASKS_CONFIDENCE` règle ici le **score de détection SAM**, pas une
probabilité sémantique par pixel. La dilatation reste une fraction de la
diagonale de l'image, appliquée après SAM ; son élargissement natif par objet
est désactivé. Le garde-fou `TRAIN_MASKS_MAX_FRACTION` reste actif.
Un changement de moteur régénère les masques ; `CLEAN_GS=true` est nécessaire
pour réentraîner des blocs déjà terminés.

### Segmenteurs

| Modèle | Données | Intérêt |
|---|---|---|
| `nvidia/segformer-b5-finetuned-ade-640-640` (défaut) | ADE20K, 150 classes | `sky`, `person`, `car`, `bicycle`, `truck`, `bus`, `tree`… ; léger |
| `facebook/mask2former-swin-large-mapillary-vistas-semantic` | Mapillary Vistas | spécialisé vues de rue (proche de Panoramax), plus lourd |

Les deux familles (SegFormer et Mask2Former) sont gérées. Les noms exacts des
labels dépendent du modèle : consulter `labels.txt`, ou :

```bash
"$TRAIN_MASKS_PYTHON" tools/segmentation_masks/generate_masks.py \
  --model "$TRAIN_MASKS_MODEL" --list-labels
```

La comparaison est insensible à la casse ; un identifiant numérique est aussi
accepté. Un label inconnu arrête le calcul avant toute écriture, avec les
labels proches suggérés.

Licences : les poids de ces modèles ne sont pas sous licence MIT (licence
NVIDIA pour SegFormer, termes ADE20K/Mapillary pour les données). Vérifier
leur compatibilité avec l'usage prévu, en particulier commercial.

## Variables de configuration

À ajouter dans le fichier de configuration du pipeline Panoramax :

```bash
TRAIN_MASKS="on"                      # off par défaut
TRAIN_MASKS_BACKEND="segmentation"    # segmentation ou spirula
TRAIN_MASKS_PYTHON="/chemin/partage/segmentation-masks/venv/bin/python"
TRAIN_MASKS_MODEL="/chemin/partage/segmentation-masks/models/nvidia--segformer-b5-finetuned-ade-640-640"
TRAIN_MASKS_LABELS="sky"              # ex. "sky,person,car,bicycle"
TRAIN_MASKS_MODE="auto"               # auto, ignore ou empty
TRAIN_MASKS_CONFIDENCE="0.5"          # probabilité minimale pour masquer
TRAIN_MASKS_DILATE="auto"             # auto ou fraction de la diagonale image
TRAIN_MASKS_MAX_FRACTION="0.8"        # au-delà, l'image n'est pas masquée
TRAIN_MASKS_DEVICE="auto"             # auto, cuda, cuda:N, mps ou cpu
```

`TRAIN_MASKS_PYTHON` et `TRAIN_MASKS_MODEL` sont affichés par l'installateur.
Sur Slurm, la génération a lieu dans le job de préparation, qui dispose déjà
d'un GPU ; les chemins doivent être visibles depuis les nœuds de calcul.

### Modes

Spirula propose deux interprétations d'un pixel masqué
(`--apply-loss-for-mask`) :

- `ignore` : pixels exclus de la perte. Adapté aux objets mobiles (piétons,
  voitures) : la façade cachée derrière n'est pas effacée.
- `empty` : pixels appris comme **vides** (transparents). Adapté au ciel :
  les splats qui se projettent dans le ciel sont poussés vers l'opacité nulle,
  ce qui vise directement les flotteurs-nuages.
- `auto` (défaut) : `empty` si seul `sky` est demandé, sinon `ignore`.
  Spirula n'applique qu'un mode par entraînement : avec `sky,person`, le ciel
  est aussi en `ignore`. Forcer `empty` avec des objets effacerait ce qu'ils
  masquent.

`TRAIN_MASKS_DILATE="auto"` élargit le masque de 0,2 % de la diagonale de
l'image en mode `ignore` (marge prudente autour des objets) et ne l'élargit pas
en mode `empty` (pour ne pas creuser les bords des toits et des arbres).

## Fonctionnement

- Entrée : `sfm-*/images` (images utilisées par l'entraînement).
- Sortie : `sfm-*/train_masks/<nom image>.png`, PNG 8 bits, **255 = gardé,
  0 = masqué** (convention COLMAP/Spirula), à la résolution de l'image et dans
  l'orientation brute du fichier (l'image est redressée selon l'EXIF pour la
  segmentation, puis le masque est remis dans le repère du fichier, comme le
  fait Spirula pour ses masques).
- `masks.json` : paramètres, fraction masquée par image, résumé.
- Reprise : avec les mêmes paramètres, seules les images sans masque sont
  traitées. Si le modèle, les labels ou les seuils changent, tous les masques
  sont régénérés dans un dossier temporaire puis remplacent l'ancien.
- Garde-fou : une image masquée à plus de `TRAIN_MASKS_MAX_FRACTION` garde un
  masque entièrement blanc (signalé), pour éviter qu'une erreur de
  segmentation supprime une vue entière.
- Chaque bloc enregistre les masques utilisés dans `train_masks.txt`. Un bloc
  déjà entraîné est réutilisé, avec un avertissement si ses masques diffèrent ;
  `CLEAN_GS=true` force le réentraînement.

## Utilisation seule

```bash
"$TRAIN_MASKS_PYTHON" tools/segmentation_masks/generate_masks.py \
  --images DATASET/sfm-12000/images \
  --output DATASET/sfm-12000/train_masks \
  --model "$TRAIN_MASKS_MODEL" --labels "sky"
```

Contrôler visuellement quelques masques (zones noires = masquées) avant un
long entraînement.

## Limites

- Non testé ici avec un vrai modèle ni avec Spirula : les tests automatiques
  utilisent un faux segmenteur et un faux Spirula. Valider sur un bloc.
- Le comportement exact du mode `empty` avec le fond de rendu Spirula n'a pas
  été vérifié ; comparer un bloc `empty` et `ignore`.
- Les masques ne servent qu'à l'entraînement ; le SfM (points sparse) n'est
  pas masqué. Des points 3D dans les nuages peuvent donc encore initialiser
  des splats, que le mode `empty` doit ensuite rendre transparents.
- Une erreur de segmentation (arbre ou toit classé `sky`) se propage : en mode
  `empty`, elle peut creuser la géométrie. Augmenter
  `TRAIN_MASKS_CONFIDENCE` (ex. 0.7) si cela arrive.

## Tests

```bash
python -m unittest discover -s tools/segmentation_masks -p 'test_*.py'
```

Ils n'exigent ni torch ni transformers (Pillow et SciPy suffisent).
