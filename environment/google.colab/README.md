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

Le YAML est réutilisé seulement si son SHA256 correspond au marqueur local.
Un environnement ancien non marqué ou un YAML modifié impose un autre
`MAMBA_ENV` dans la cellule environnement. Le nom indiqué par `--name` remplace
celui du YAML ; les dépendances restent celles du fichier.

## Validation locale

```bash
cd environment/google.colab
python -m unittest test_colab_runtime
```

Les tests couvrent l'affichage avant fin du processus, les logs d'échec,
l'extraction ZIP sûre/réutilisable, les sauvegardes et la syntaxe des cellules.
La disponibilité réelle de Vulkan sur Colab doit être vérifiée dans le runtime.
