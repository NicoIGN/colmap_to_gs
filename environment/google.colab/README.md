# Notebook vidéo Colab T4

Ouvrir `colmap_to_gs_video_T4.ipynb` dans Colab, sélectionner un runtime GPU et
exécuter les cellules dans l'ordre. Renseigner la vidéo, l'archive Spirula exacte,
le nom de run et l'acceptation personnelle de la licence SAM.

Le notebook utilise `review_copilot` par défaut. Le YAML et le helper
`colab_runtime.py` doivent être présents sur la branche sélectionnée.
Le helper affiche stdout/stderr en direct, y compris les progressions avec
retours chariot, et conserve un log local distinct par commande. Micromamba
est lancé avec `run -n`, sans l'option Conda `--no-capture-output` qui provoque
`exec: --: invalid option` dans le runtime testé. Un programme natif peut
néanmoins tamponner lui-même ses messages.

La cellule Python dédiée `ACCEPT_SAM_LICENSE = True`, juste avant les réglages,
enregistre l'acceptation dans le noyau courant. L'exécuter seulement après
lecture et acceptation personnelle de la licence SAM. Après redémarrage,
réexécuter cette cellule ; aucun `export` Bash n'est nécessaire.

## Paramètres et réexécution

La première cellule lit les variables d'environnement `TRAIN_NUM_ITER`
(12000), `TRAIN_SP_CAP_MAX` (500000 splats maximum **par bloc**),
`SPLIT_NUM_BLOCKS` (**1** par défaut), `MAMBA_ENV` (`gsplat`) et `RUN_NAME`
(`video_run`). On peut aussi modifier directement leurs valeurs Python.
Le notebook transmet les paramètres d'entraînement à la configuration Bash,
avec `export SS_NN_LOG=1`. Pour d'autres données ou réglages d'un entraînement
déjà commencé, choisir un nouveau `RUN_NAME` plutôt que réutiliser ses checkpoints.

À paramètres identiques, **Tout exécuter** réutilise l'environnement Conda,
l'extraction Spirula, la vidéo locale et les frames vérifiées par SHA256.
Les frames ne sont plus effacées à chaque lancement. Une ancienne extraction
signée par `inputs.json` mais sans marqueur vidéo est comparée une seule fois
à une extraction temporaire de la vidéo ; les fichiers originaux ne sont pas
remplacés. Une extraction interrompue dans le nouveau notebook ne publie pas
de frames partielles. Les signatures du pipeline continuent de protéger
les masques, le SfM et les blocs existants.

`SPIRULA_LOCAL_ROOT` facultatif désigne une installation Spirula locale
existante contenant le binaire `SPIRULA_BINARY_RELATIVE` et le modèle SAM.
Dans ce cas, l'archive Spirula Drive n'est pas requise. Sinon, l'extraction
de l'archive est réutilisée par SHA256. Aucun checkout ou environnement
étranger n'est supprimé automatiquement.

## Repartir d'un ZIP COLMAP sur Drive

Renseigner `COLMAP_ARCHIVE` dans la première cellule (ou dans l'environnement) :

```python
COLMAP_ARCHIVE = '/content/drive/MyDrive/colmap_to_gs_runs/mon-run-colmap-20261115.zip'
```

Le ZIP doit contenir un unique jeu `sparse/0/{cameras,images,points3D}.bin`
et `images/` côte à côte, éventuellement dans un dossier parent. Les ZIP
binaires produits par la cellule d'export sont directement utilisables.
Une vidéo n'est pas nécessaire dans ce mode.

L'archive est copiée localement et vérifiée par SHA256, puis extraite sous
`RUN/colmap-input/`. Le modèle est lu entièrement (caméras, poses, observations,
tracks), et l'existence des images référencées est contrôlée avant adoption.
Les chemins sortants, liens ZIP et modèles ambigus sont refusés.
Le sparse est copié dans `output/sfm/sparse/0` ; le pipeline utilise les images
importées et **ne refait ni extraction vidéo ni reconstruction SfM**.
Il génère encore les masques d'entraînement, découpe en blocs, puis entraîne
si `PHASE='all'` (`prepare` s'arrête après la préparation).

Relancer tout avec le même ZIP vérifie et réutilise l'import. Un ZIP modifié,
un sparse local divergent ou un workspace vidéo déjà utilisé sont refusés :
choisir un nouveau `RUN_NAME`. Conserver `COLMAP_ARCHIVE` pour les relances
de ce run. Les images importées restent dans le run, afin que les sauvegardes
soient autonomes.

## Disque local et Drive

`/content/colmap_to_gs_work/<RUN_NAME>/` contient vidéo locale, frames,
configuration, logs et `output/` (masques, SfM, entraînement, exports).
Le traitement n'écrit aucun intermédiaire dans Drive. La vidéo est copiée une
seule fois et vérifiée par SHA256. Une archive Spirula est extraite dans
`/content/colmap_to_gs_work/assets/`, identifiée par son hash, sans effacer une
installation préexistante.

Après succès, une sauvegarde est créée si `SAVE_ON_SUCCESS=True`. Après échec
ou interruption, lancer la cellule de sauvegarde manuelle, une fois les
processus arrêtés. Elle crée localement une archive compressée, matérialise les
liens internes aux images, copie un fichier horodaté dans Drive puis vérifie son
SHA256. Les anciennes archives sont conservées. Prévoir la place pour le run
et son archive ; l'archivage peut être long pour de gros checkpoints.

La cellule **Export COLMAP seul vers Drive (ZIP)** exporte uniquement le modèle
global `output/sfm/sparse/0` et `output/images`, sous `sparse/0/` et `images/`
dans un ZIP horodaté de `DRIVE_BACKUPS`. Les fichiers images liés sont copiés
dans l'archive. Exécuter après le SfM, calcul arrêté ; un modèle incomplet est
refusé. Le ZIP est créé localement, puis la copie Drive est vérifiée par SHA256.

`/content` est éphémère. Il n'y a pas de sauvegarde continue, ni de restauration
automatique des signatures et des liens après changement de runtime.
Les archives permettent de récupérer les données ; elles ne garantissent pas
une reprise directe du pipeline. Dans le même runtime, garder le run local et
`PIPELINE_RESUME=true`. Pour des entrées/réglages différents, choisir un nouveau
RUN_NAME. Ne pas copier le vieux workspace Drive dans le nouveau run local :
les chemins et les liens peuvent ne plus correspondre.

## CUDA et Vulkan sont deux contrôles distincts

`nvidia-smi` et PyTorch CUDA ne prouvent pas que Spirula peut utiliser Vulkan.
Le notebook vérifie `vulkaninfo --summary` dans le système puis dans le même
environnement Conda que le calcul, exige un GPU NVIDIA et s'arrête avant
l'extraction en cas d'échec. `libvulkan1` est le chargeur, pas le pilote NVIDIA.
Un ZIP de Spirula ne transporte pas le pilote du runtime.

Après les dépendances système, une cellule de diagnostic collecte la version
du pilote, les paquets NVIDIA, les chemins et dépendances GLX/EGL, les ICD et les
variables de sélection. Elle teste le chargeur Vulkan sans modifier les pilotes
ni l'environnement. Les erreurs sont signalées sans interrompre la collecte ;
le contrôle bloquant reste obligatoire avant le calcul.
Le rapport complet est enregistré dans `RUN/logs/vulkan-diagnostic-*.log`.
`DIAGNOSTIC_LD_DEBUG=True` ajoute la trace du chargement dynamique au rapport,
avec un affichage filtré et limité. Après un essai de bibliothèques isolées,
relancer le diagnostic avec les mêmes variables que le calcul.

La cellule suivante crée des descripteurs Vulkan et EGL locaux pour les
bibliothèques déjà montées dans `/usr/lib64-nvidia`. Le descripteur EGL est
nécessaire dans le runtime Colab testé : le seul ICD Vulkan échoue avec
`Could not get 'vkCreateInstance'`. Aucun pilote n'est installé ou remplacé.
Les variables `VK_DRIVER_FILES`, `VK_ICD_FILENAMES` et
`__EGL_VENDOR_LIBRARY_FILENAMES` sont exportées aux processus enfants après
validation de Vulkan NVIDIA, puis héritées par Micromamba et le pipeline.
Réexécuter cette configuration après chaque redémarrage du noyau.

Pour `VK_ERROR_INCOMPATIBLE_DRIVER`, inspecter les ICD et les variables affichés
dans la cellule système. Installer arbitrairement un autre pilote NVIDIA dans
Colab peut créer une incompatibilité avec le pilote hôte ; le notebook ne le
fait pas. La présence de Vulkan NVIDIA ne garantit pas toutes les extensions
nécessaires au binaire Spirula : les erreurs natives restent bloquantes.

Un environnement Conda marqué avec le même SHA256 de YAML est réutilisé.
Un environnement existant non marqué ou un YAML modifié déclenche
`micromamba env update` ; le marqueur n'est écrit qu'après succès.
Un dossier existant sans métadonnées Conda est refusé. Le nom `MAMBA_ENV`
indiqué par `--name` remplace celui du YAML.

## Validation locale

```bash
cd environment/google.colab
python -m unittest test_colab_runtime
```

Les tests couvrent l'affichage avant fin du processus, les logs d'échec,
l'extraction ZIP sûre/réutilisable, les sauvegardes, la réutilisation des frames
et de Conda, ainsi qu'une préparation COLMAP importée exécutée deux fois sans
reconstruction native. La validation COLMAP nécessite numpy, scipy et tqdm ;
la préparation des images nécessite Pillow.
La disponibilité réelle de Vulkan sur Colab doit être vérifiée dans le runtime.
