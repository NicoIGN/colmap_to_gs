# Masques de segmentation pour l'entraînement 3DGS

[Retour à la documentation générale](../../doc/guide_utilisateur.md)

## 1. Objectif

Générer des masques locaux pour les images d'entraînement 3DGS/[Spirula](../spirula/README.md). Par défaut, le masque exclut le ciel.

Exemple d’invocation :

```bash
"$TRAIN_MASKS_PYTHON" tools/segmentation_masks/generate_masks.py \
  --model mapillary --list-labels
```

Le générateur local produit un masque par image. Les nuages appartiennent à la
classe `sky` : ADE20K et Mapillary Vistas n'ont pas de classe « nuage »
distincte. D'autres objets peuvent être masqués en indiquant leurs labels.

`TRAIN_MASKS_BACKEND` choisit le moteur de calcul, sans changer l'utilisation
des masques par l'entraînement :

- `segmentation` (défaut) : modèle Transformers de segmentation sémantique, notamment SegFormer ou Mask2Former ;
- `spirula` : SAM natif exécuté par Spirula sur Vulkan.

## 2. Entrées et sorties

- **Entrée** : `sfm-*/images` (images utilisées par l'entraînement), ou tout autre dossier fourni par `--images`.
- **Sortie** : `sfm-*/train_masks/<chemin relatif de l'image>.png`, PNG 8 bits, **255 = gardé, 0 = masqué**, à la résolution de l'image et dans l'orientation brute du fichier. Pour la segmentation Transformers, l'image est redressée selon l'EXIF avant inférence, puis le masque et les diagnostics sont remis dans le repère du fichier.
- **`masks.json`** : paramètres de génération, fraction masquée et statut pour chaque image, ainsi qu'un résumé global.
- **Reprise** : si les paramètres enregistrés sont identiques, les masques existants présents dans le rapport sont réutilisés lorsque `--verbose` n'est pas activé. Si les paramètres changent, les masques sont régénérés dans un dossier temporaire puis remplacent l'ancien dossier à la fin du traitement.
- **Mode verbose** : `tmp_labels/` est recréé et les images sont recalculées pour produire les diagnostics, même si des masques existaient déjà.
- **Garde-fou** : si une image dépasse `--max-masked-fraction`, son masque de sortie est entièrement blanc (aucun pixel masqué) et son statut est `overmasked_kept`. Cela évite qu'une erreur de segmentation supprime une vue entière.
- **Sous-dossiers** : les chemins relatifs des images sont conservés dans les dossiers de sortie, y compris pour les diagnostics.
- **Intégration pipeline** : chaque bloc enregistre les masques utilisés dans `train_masks.txt`. Un bloc déjà entraîné est réutilisé, avec un avertissement si ses masques diffèrent ; `TRAIN_CLEAN=true` force le réentraînement.

#### Diagnostics `--verbose`

Avec `--verbose`, le dossier suivant est produit à côté des masques :

```text
tmp_labels/
├── labels.txt
├── semantic/       # carte des classes en fausses couleurs RGB
├── overlay/        # image originale avec les couleurs de classes superposées
├── probability/    # score normalisé du ou des labels demandés (noir=0, blanc=1)
├── threshold/      # résultat binaire : classe demandée et score >= confidence
└── ids/            # identifiants de classes numériques, PNG 16 bits
```

`labels.txt` donne, pour chaque classe, son identifiant, son nom et sa couleur hexadécimale ; les classes demandées sont marquées `[MASQUER]`. Les fichiers de diagnostics gardent l'arborescence relative des images sources.

Les logs indiquent, pour chaque image, la proportion de pixels dont la classe gagnante est demandée, la proportion retenue après `--confidence` et le score maximal du label demandé. Pour le backend Spirula, la carte de diagnostic est binaire : SAM renvoie l'union des régions trouvées pour les prompts, pas une classe distincte par prompt.

## 3. Configuration et utilisation

```bash
bash tools/segmentation_masks/install.sh \
  --install-dir /chemin/partage/segmentation-masks \
  --python python3
```

L'installateur crée un environnement Python isolé `INSTALL/venv` et installe :

- PyTorch `>=2.1,<2.9` ;
- Transformers `4.46.3` ;
- `safetensors`, Pillow et SciPy ;
- NumPy `>=1.24,<2`.

Il télécharge **les deux modèles** dans `INSTALL/models/`, avec un nom de dossier de la forme `<organisation>--<modèle>` :

1. `nvidia/segformer-b5-finetuned-ade-640-640` (ADE20K, généraliste) ;
2. `facebook/mask2former-swin-large-mapillary-vistas-semantic` (Mapillary Vistas, scènes urbaines extérieures).

Pour chacun, il écrit `labels.txt` avec les identifiants et noms de toutes les classes, vérifie que les labels demandés (`--labels`, défaut `sky`) existent et affiche les variables à reporter dans la configuration. Le modèle ADE20K reste le modèle par défaut affiché par l'installateur ; Mapillary peut être sélectionné en remplaçant `TRAIN_MASKS_MODEL`.

Options de l'installateur :

- `--install-dir PATH` : répertoire d'installation ;
- `--python PATH` : Python utilisé pour créer le venv ;
- `--model ID_HUGGING_FACE` : checkpoint ADE20K (ou modèle généraliste de remplacement) ;
- `--mapillary-model ID_HUGGING_FACE` : checkpoint Mapillary ;
- `--labels "sky,person,car"` : labels à vérifier dans les deux modèles.

L'installation nécessite un accès réseau à PyPI et Hugging Face. Après téléchargement, la génération utilise les modèles locaux (`local_files_only=True`) et ne nécessite plus d'accès réseau. Python >= 3.9 est requis. Linux (CUDA) et macOS (MPS) sont pris en charge par PyTorch ; sans accélérateur compatible, le calcul peut être effectué sur CPU, mais il sera plus lent.

#### NumPy / PyTorch sur macOS

L'installateur impose `numpy<2` et vérifie le passage d'un tableau NumPy vers PyTorch puis retour, afin d'éviter les incompatibilités ABI rencontrées avec certaines roues PyTorch et NumPy 2 (`_ARRAY_API not found`). Relancer la même commande d'installation met à jour les dépendances sans devoir supprimer le venv ni les poids déjà téléchargés.

#### Choix du périphérique (CUDA, MPS ou CPU)

Avec `--device auto`, le script choisit dans cet ordre : CUDA, MPS, puis CPU. Il est possible de forcer le périphérique avec `--device cuda`, `--device cuda:N`, `--device mps` ou `--device cpu`.

**Sur le Mac utilisé pour les tests Mapillary de ce projet, MPS a produit 0 % de pixels `Sky` sur l'image de contrôle, alors que le même checkpoint et le même traitement sur CPU ont détecté 4 % de pixels `Sky`.** Pour ce modèle sur cette machine, utiliser explicitement `--device cpu` (ou `TRAIN_MASKS_DEVICE="cpu"` dans la configuration du pipeline). Ne pas supposer que `auto` choisira le CPU : sur Apple Silicon, il sélectionne MPS lorsqu'il est disponible.

#### Backend natif Spirula

Exemple de configuration du pipeline :

```bash
TRAIN_MASKS="on"
TRAIN_MASKS_BACKEND="spirula"
TRAIN_MASKS_SPIRULA_MODEL="/chemin/sam3-q4_0.ggml"
TRAIN_MASKS_SPIRULA_DEVICE="auto"
TRAIN_MASKS_LABELS="sky"
TRAIN_MASKS_MODE="auto"
```

Il faut un Spirula compilé avec `SS_BUILD_SAM=ON` (backend Vulkan) et un checkpoint **SAM 3 GGML**, pas les poids Transformers de l'installateur.

Lire les conditions de la licence SAM 3 Meta avant de l'accepter. Pour un lancement non interactif, après acceptation explicite par l'utilisateur, définir `MASKS_ACCEPT_LICENSE="true"` dans la configuration : chaque appel natif reçoit `--accept-license sam3=yes`, aussi bien pour le SfM que pour le 3DGS. Cette option vaut `false` par défaut. Pour le générateur autonome, l'équivalent est `--accept-spirula-license`.

On peut aussi accepter sur un terminal avec `"$SPIRULA_BIN" sam devices --accept-license sam3`, avec le même compte et le même `HOME` que les jobs. Les messages et erreurs de Spirula sont transmis aux journaux, même sans `--verbose`.

La progression `fait/total` est affichée sans `--verbose`, pour les deux
backends et les masques réutilisés. Avec SAM, `Calcul SAM` compte les PNG
écrits pendant l'inférence ; `Génération des masques` suit ensuite leur
adaptation et leur export. Chaque mise à jour est une ligne lisible dans
les logs Slurm. `--verbose` reste réservé aux diagnostics détaillés et
force la régénération : il n'est pas nécessaire pour suivre l'avancement.

`TRAIN_MASKS_MODEL` et `TRAIN_MASKS_PYTHON` ne sont pas utilisés dans ce mode : le calcul est natif, et l'adaptation des PNG utilise `PYTHON_BIN` avec NumPy, Pillow et SciPy (SciPy seulement si une dilatation est demandée). `TRAIN_MASKS_SPIRULA_DEVICE` utilise les noms Vulkan, pas `cuda` ou `mps`.

Les labels deviennent des **prompts anglais libres**, unis par SAM : `"sky,person,car"` devient `"sky; person; car"`. SAM 2 seul n'accepte pas de texte ; cette intégration attend donc SAM 3. Chaque image est segmentée indépendamment (`spirula sam track --mask-mode image --remove-prompted`), sans suivi vidéo, car les photos Panoramax ne constituent pas une séquence ordonnée. Les sorties `frame_XXXXX.png` sont renommées pour l'entraînement, y compris pour des images dans des sous-dossiers. Formats pris en charge par ce backend : JPEG, PNG, BMP et TIFF ; un autre format provoque une erreur explicite.

Pour SAM, `TRAIN_MASKS_CONFIDENCE` règle le **score de détection SAM** transmis à Spirula (`--threshold`), pas une probabilité sémantique par pixel. La dilatation reste une fraction de la diagonale de l'image, appliquée après SAM ; l'élargissement natif par objet est désactivé. Le garde-fou `TRAIN_MASKS_MAX_FRACTION` reste actif.

Un changement de moteur ou de paramètres de génération invalide les masques existants. `TRAIN_CLEAN=true` est nécessaire pour réentraîner des blocs déjà terminés.


### Variables de configuration du pipeline


À ajouter dans le fichier de configuration du [pipeline Panoramax](../panoramax/README.md). Ces variables sont consommées par l'orchestration du pipeline, qui les transmet au script sous forme d'options de ligne de commande :

```bash
TRAIN_MASKS="on"                    # off par défaut
TRAIN_MASKS_BACKEND="segmentation"  # segmentation ou spirula
TRAIN_MASKS_PYTHON="/chemin/partage/segmentation-masks/venv/bin/python"
TRAIN_MASKS_MODEL="/chemin/partage/segmentation-masks/models/facebook--mask2former-swin-large-mapillary-vistas-semantic"
TRAIN_MASKS_LABELS="sky"            # ex. "sky,person,car,bicycle"
TRAIN_MASKS_MODE="auto"             # auto, ignore ou empty
TRAIN_MASKS_CONFIDENCE="0"          # seuil complémentaire ; 0 = aucune filtration supplémentaire
TRAIN_MASKS_DILATE="auto"           # auto ou fraction de la diagonale image
TRAIN_MASKS_MAX_FRACTION="0.95"     # au-delà, l'image n'est pas masquée
TRAIN_MASKS_DEVICE="cpu"            # auto, cuda, cuda:N, mps ou cpu
```

`TRAIN_MASKS_PYTHON` et les chemins des modèles sont affichés par l'installateur. Pour sélectionner Mapillary dans le pipeline, `TRAIN_MASKS_MODEL` doit pointer vers son dossier local `facebook--mask2former-swin-large-mapillary-vistas-semantic` (ou vers le chemin effectivement utilisé lors de l'installation). Pour ADE20K, utiliser le dossier `nvidia--segformer-b5-finetuned-ade-640-640`.

Sur le Mac utilisé pour les tests Mapillary, conserver `TRAIN_MASKS_DEVICE="cpu"` : le mode `auto` sélectionne MPS et le test observé sur MPS ne détectait pas la classe `Sky`. Sur une machine Linux avec CUDA, `auto` sélectionne CUDA si PyTorch le détecte.

Sur Slurm, la génération a lieu dans le job de préparation, qui dispose déjà d'un GPU ; les chemins doivent être visibles depuis les nœuds de calcul.

> **À distinguer :** le script autonome lit directement `TRAIN_MASKS_INSTALL_DIR` (pour résoudre les préréglages) et `TRAIN_MASKS_MODEL` (valeur par défaut de `--model`). Les autres variables ci-dessus sont des paramètres de l'orchestration du pipeline ; lors d'un appel autonome, les options correspondantes doivent être passées explicitement (`--labels`, `--confidence`, `--dilate-fraction`, `--max-masked-fraction`, `--device`, etc.).


### Options du script `generate_masks.py`


| Option | Défaut | Description |
|---|---:|---|
| `--images PATH` | requis | Dossier source ; les sous-dossiers sont parcourus récursivement. |
| `--output PATH` | requis | Dossier de sortie des masques. Il doit être distinct du dossier source et ne pas se trouver à l'intérieur de celui-ci. |
| `--backend` | `segmentation` | `segmentation` ou `spirula`. |
| `--model` | `TRAIN_MASKS_MODEL`, sinon `ade20k` | Préréglage `ade20k`, `mapillary`, chemin Transformers local ou checkpoint SAM GGML selon le backend. |
| `--labels` | `sky` | Labels séparés par des virgules. |
| `--confidence` | `0` en segmentation ; `0.5` avec Spirula | Segmentation : seuil de score complémentaire appliqué uniquement aux pixels dont la classe gagnante est demandée. Spirula : seuil de détection transmis à SAM. |
| `--dilate-fraction` | `0` | Rayon de dilatation exprimé en fraction de la diagonale de l'image ; valeur attendue dans `[0,0.1[`. |
| `--max-masked-fraction` | `0.8` | Si la fraction masquée dépasse cette valeur, le masque de l'image est entièrement désactivé. Valeur attendue dans `]0,1]`. |
| `--device` | `auto` | Périphérique Transformers : `auto`, `cuda`, `cuda:N`, `mps` ou `cpu`. |
| `--spirula-bin` | `spirula` | Exécutable Spirula utilisé avec le backend natif. |
| `--spirula-device` | `auto` | Périphérique Vulkan : `auto`, index, nom ou UUID. |
| `--accept-spirula-license` | désactivé | Accepte explicitement la licence SAM 3 pour le lancement natif de Spirula. |
| `--verbose` | désactivé | Affiche les étapes, une barre de progression et les diagnostics d'image dans `tmp_labels/`. |
| `--list-labels` | désactivé | Affiche les classes du modèle Transformers et quitte. Non disponible avec le backend Spirula. |

Sans argument, le script affiche son aide et quitte. Exemple de commande autonome avec le modèle urbain Mapillary et le CPU :

```bash
export TRAIN_MASKS_INSTALL_DIR="$HOME/.local/share/colmap-to-gs/segmentation-masks"
export TRAIN_MASKS_PYTHON="$TRAIN_MASKS_INSTALL_DIR/venv/bin/python"

"$TRAIN_MASKS_PYTHON" tools/segmentation_masks/generate_masks.py \
  --backend segmentation \
  --images DATASET/sfm-12000/images \
  --output DATASET/sfm-12000/train_masks \
  --model mapillary \
  --labels "sky" \
  --confidence 0 \
  --max-masked-fraction 0.95 \
  --dilate-fraction 0 \
  --device cpu \
  --verbose
```

#### Interprétation de `--confidence` en segmentation

Le masque s'appuie d'abord sur la **classe gagnante** prédite pour chaque pixel : un pixel n'est masqué que si sa classe gagnante appartient aux labels demandés. `--confidence` ajoute ensuite un seuil sur le score de cette classe ; il ne permet pas de transformer une autre classe en `sky`.

- `--confidence 0` : aucune filtration supplémentaire ; tous les pixels classés dans la classe demandée sont masqués.
- Une valeur supérieure à zéro : seuls les pixels classés dans cette classe et dont le score atteint le seuil sont masqués.

Pour Mapillary sur le Mac testé dans ce projet, le traitement CPU a reconnu 4 % de pixels `Sky` sur l'image de contrôle, tandis que MPS en reconnaissait 0 %. Le réglage `--confidence 0` ne remplace donc pas le choix du périphérique : utiliser `--device cpu` sur cette configuration Mac.

## 4. Fonctionnement internes

Le script `generate_masks.py` traite les images une par une et produit, pour
chacune, une carte indiquant les zones à garder ou à masquer.

| Modèle | Données | Intérêt |
|---|---|---|
| `nvidia/segformer-b5-finetuned-ade-640-640` (`ade20k`) | ADE20K, 150 classes | Généraliste ; classes `sky`, `person`, `car`, `bicycle`, `truck`, `bus`, `tree`… ; plus léger. |
| `facebook/mask2former-swin-large-mapillary-vistas-semantic` (`mapillary`) | Mapillary Vistas | Spécialisé dans les scènes urbaines extérieures et les vues de rue, proches des images Panoramax ; plus lourd. |

Avec les modèles Transformers, le script classe les pixels de chaque image,
puis garde les classes demandées dans le masque. Le paramètre `--confidence`
peut écarter les pixels dont la détection est trop incertaine.

Avec le backend Spirula, SAM 3 produit directement les zones correspondant
aux mots-clés demandés. Ce backend produit un masque combiné, pas une classe
différente pour chaque mot-clé.

Les noms exacts des labels dépendent du modèle. Consulter le `labels.txt` du modèle ou afficher les classes depuis le script :

```bash
"$TRAIN_MASKS_PYTHON" tools/segmentation_masks/generate_masks.py \
  --model mapillary --list-labels
```

La comparaison des noms est insensible à la casse ; un identifiant numérique est aussi accepté. Un label inconnu arrête le calcul avant l'écriture des masques, avec des suggestions de labels proches.

Les préréglages `ade20k` et `mapillary` sont résolus dans :

```text
${TRAIN_MASKS_INSTALL_DIR:-~/.local/share/colmap-to-gs/segmentation-masks}/models/
```

On peut aussi fournir à `--model` le chemin local d'un autre modèle Transformers. Avec `--backend spirula`, `--model` doit obligatoirement être le chemin d'un checkpoint SAM GGML ; les préréglages Transformers ne sont pas utilisables avec ce backend.

Licences : les poids de ces modèles ne sont pas sous licence MIT (licence NVIDIA pour SegFormer, termes ADE20K/Mapillary pour les données). Vérifier leur compatibilité avec l'usage prévu, en particulier commercial.


### Modes de perte des masques


Spirula propose deux interprétations d'un pixel masqué (`--apply-loss-for-mask`) :

- `ignore` : pixels exclus de la perte. Adapté aux objets mobiles (piétons, voitures) ; la façade cachée derrière eux n'est pas effacée.
- `empty` : pixels appris comme **vides** (transparents). Adapté au ciel : les splats qui se projettent dans le ciel sont poussés vers l'opacité nulle, ce qui vise directement les flotteurs-nuages.
- `auto` (défaut) : `empty` si seul `sky` est demandé, sinon `ignore`. Spirula n'applique qu'un mode par entraînement : avec `sky,person`, le ciel est aussi en `ignore`. Forcer `empty` avec des objets effacerait ce qu'ils masquent.

`TRAIN_MASKS_DILATE="auto"` élargit le masque de 0,2 % de la diagonale de l'image en mode `ignore` (marge prudente autour des objets) et ne l'élargit pas en mode `empty` (pour ne pas creuser les bords des toits et des arbres). Le script autonome, lui, attend une valeur numérique dans `--dilate-fraction` ; la conversion de `auto` est faite par l'orchestration du pipeline.

## 5. Notes et informations complémentaires

- Le modèle est un classifieur sémantique : il peut manquer une partie du ciel ou confondre certains éléments avec le ciel. Contrôler visuellement les overlays et les masques sur un échantillon représentatif avant un long entraînement.
- Les résultats CPU/MPS/CUDA peuvent différer selon le modèle, la version de PyTorch et le matériel. Sur la configuration Mac testée ici, Mapillary doit être exécuté sur CPU pour obtenir la détection observée.
- Les masques ne servent qu'à l'entraînement ; le SfM (points sparse) n'est pas masqué. Des points 3D dans les nuages peuvent donc encore initialiser des splats, que le mode `empty` doit ensuite rendre transparents.
- Une erreur de segmentation (arbre ou toit classé `sky`) se propage : en mode `empty`, elle peut creuser la géométrie. Contrôler les diagnostics et augmenter `TRAIN_MASKS_CONFIDENCE` (par exemple `0.7`) si des pixels mal classés doivent être écartés.
- Le comportement exact du mode `empty` avec le fond de rendu Spirula doit être validé sur un bloc représentatif ; comparer un bloc `empty` et un bloc `ignore`.


### Tests


```bash
python -m unittest discover -s tools/segmentation_masks -p 'test_*.py'
```

Les tests automatiques n'exigent pas Torch ni Transformers (Pillow et SciPy suffisent). Ils ne remplacent pas une vérification visuelle avec les poids réels et le backend utilisé en production.
