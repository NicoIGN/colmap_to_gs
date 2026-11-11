# Lancer un pipeline avec Bash ou Slurm

[Retour à la documentation générale](guide_utilisateur.md)

## 1. Objectif

L'exécution directe lance le calcul dans le shell courant. Slurm réserve les
ressources d'un cluster et exécute les étapes sur ses nœuds de calcul.

Ce guide concerne trois pipelines :

- [Images locales vers Gaussian Splats](../tools/images_to_splat/README.md) :
  configuration `tools/images_to_splat/config.sh.example`.
- [Panoramax vers Gaussian Splats](../tools/panoramax/README.md) :
  configuration `tools/panoramax/config.sh.example`.
- [Nerfstudio / Splatfacto](nerfstudio.md) : lanceur distinct dans
  `environment/ign.slurm/`, avec ses propres paramètres.

Depuis la racine du dépôt, après adaptation de la configuration :

| Pipeline | Exécution directe | Soumission Slurm |
|---|---|---|
| Images locales | `bash script/images_to_3dgs.sh /chemin/config.sh` | `bash script/launch_images_to_3dgs.sh /chemin/config.sh` |
| Panoramax | `bash script/panoramax_to_3dgs.sh /chemin/config.sh` | `bash script/launch_panoramax_to_3dgs.sh /chemin/config.sh` |

`TRAIN_BACKEND=nerfstudio` conserve ce découpage en jobs. Les tâches
d'entraînement exigent PyTorch et une carte NVIDIA utilisable via CUDA ;
la fusion reste une étape CPU sans lancement de Nerfstudio.

Les lanceurs `launch.sh` se lancent avec **Bash, pas avec `sbatch`** :
ils soumettent eux-mêmes les jobs. Ajouter `--wait` à leur commande pour attendre
la fin de la fusion et obtenir un échec si elle échoue ou ne peut pas démarrer.
Sans cette option, un retour réussi confirme seulement la soumission.

## 2. Entrées et sorties

Les données et résultats sont ceux du pipeline choisi ; Slurm ajoute les
journaux de soumission et d'exécution.

- Accès : dépôt, configuration, exécutables et données doivent être accessibles
  aux mêmes chemins depuis les nœuds de calcul. Préférer des chemins absolus.
- Configuration : fichier Bash de confiance, exécuté par les scripts ;
  ne pas le modifier tant que les jobs sont en attente ou en cours.
- Panoramax : le nœud de préparation doit avoir accès à l'API pour télécharger
  les images.
- Journaux : `OUTPUT_DIR/logs/`, avec `submit.log`.
- Images locales : `prepare-JOBID.{out,err}`, `block-ARRAYID_INDICE.{out,err}`
  et `merge-JOBID.{out,err}`.
- Panoramax : `panoramax-prepare-JOBID.{out,err}`,
  `panoramax-block-ARRAYID_INDICE.{out,err}` et
  `panoramax-merge-JOBID.{out,err}`.
- Nerfstudio : `gsplat-JOBID.out` et `gsplat-JOBID.err`.

## 3. Configuration et utilisation

### Ressources Slurm des pipelines images locales et Panoramax

Ces paramètres se règlent dans le même fichier que les paramètres du pipeline.
Les ressources sont demandées **par job ou tâche**, pas pour tout le pipeline.
La RAM est la mémoire du nœud, pas la mémoire vidéo du GPU (VRAM).

- `SLURM_PARTITION` : partition du cluster ; défaut `jz` pour les images locales,
  `jean-zellou` pour Panoramax. Adapter au site.
- `SLURM_CPUS_PER_TASK` : CPU par tâche ; défaut `16`.
- `SLURM_MEMORY` : RAM par tâche ; défaut `64G`.
- `SLURM_TIME_LIMIT` : durée maximale de chaque job ; défaut `48:00:00`,
  sous réserve des limites de la partition.
- `SLURM_GPU_TYPE` : type de GPU reconnu par le cluster, sans préfixe `gpu:` ;
  vide par défaut pour demander un GPU sans imposer son type.
- `SLURM_BLOCK_PARALLELISM` : nombre maximal de blocs entraînés simultanément ;
  défaut `1` pour les images locales, `SPLIT_NUM_BLOCKS` pour Panoramax.
  `1` entraîne les blocs successivement ; la disponibilité du cluster peut
  réduire le parallélisme effectif.
- `SLURM_ACCOUNT` / `SLURM_QOS` : compte de facturation / qualité de service ;
  vides par défaut pour utiliser les choix du site.
- `SLURM_ENV_SETUP` : chemin d'un script Bash de confiance chargé sur chaque
  nœud pour préparer les modules et l'environnement ; vide par défaut.
  Sans préparation spécifique, l'environnement de soumission est hérité.

Quatre blocs simultanés peuvent réserver quatre GPU et quatre fois les CPU/RAM
configurés. Augmenter le parallélisme ne change pas le budget de chaque bloc.

Les syntaxes, limites et exemples complets sont dans les configurations
[images locales, section « 7. Slurm uniquement »](../tools/images_to_splat/config.sh.example)
et [Panoramax, section « 7. Execution Slurm »](../tools/panoramax/config.sh.example).
Les réglages Slurm ne réservent aucune ressource en exécution directe.
Pour Panoramax, `SLURM_CPUS_PER_TASK` sert aussi de valeur de repli à
`SFM_THREADS`, même en exécution directe ; les images locales ont leur propre
réglage `SFM_THREADS`.

### Environnement Conda de Panoramax

Le lanceur Panoramax peut activer Conda après le script `SLURM_ENV_SETUP`.
Ces deux paramètres ne s'appliquent pas au lanceur images locales.

- `SLURM_CONDA_ENV` : nom ou chemin de l'environnement à activer ;
  vide par défaut pour conserver l'environnement hérité.
- `SLURM_CONDA_BASE` : racine Conda ; vide par défaut pour utiliser
  `conda info --base`, seulement si `SLURM_CONDA_ENV` est renseigné.

Pour les images locales, activer l'environnement avant soumission ou le préparer
dans `SLURM_ENV_SETUP`.

### Pipeline Nerfstudio / Splatfacto

Ce lanceur n'utilise pas les variables de configuration `SLURM_*` ci-dessus.
Les options du calcul et un exemple de `config.sh` sont dans le
[guide Nerfstudio](nerfstudio.md#exécuter-sur-slurm).

```bash
# Exécution directe, avec l'environnement du projet activé :
bash tools/libs/scripts/run.sh --dataset-dir /chemin/dataset \
  --output-dir /chemin/resultats --gsplat-profile balanced --name ma_scene

# Soumission, avec les paramètres du pipeline définis dans config.sh :
GIT_ROOT=/chemin/depot CONFIG_SH=/chemin/config.sh \
  bash /chemin/depot/environment/ign.slurm/launch.sh
```

- `PARTITION` : partition ; premier argument du lanceur prioritaire,
  puis variable, puis défaut `jean-zellou`.
- `CONFIG_SH` : fichier chargé avant soumission et dans le job ;
  défaut `./config.sh`. Le fichier doit être accessible au job.
- Ressources : définies dans
  [`environment/ign.slurm/launch.slurm`](../environment/ign.slurm/launch.slurm) :
  un nœud, un GPU, huit CPU, `16G` de RAM et `48:00:00`.
- Précaution : `CPUS_PER_TASK` et `MEMORY` sont affichés par le lanceur, mais
  ne sont pas transmis à `sbatch` ; ils ne remplacent pas ces directives.
- Environnement : le job active Conda `gsplat`.
- Suivi : le lanceur suit les journaux jusqu'à la fin ; il renvoie `0` pour
  l'état `COMPLETED`, `1` sinon. Il n'utilise pas l'option `--wait`.
- `VERBOSE` : `1` ou `true` affiche les journaux complets et les ressources
  CPU/GPU toutes les 120 secondes ; sinon les journaux sont filtrés.

## 4. Fonctionnement interne

Les pipelines images locales et Panoramax exécutent les blocs successivement
en Bash direct. Sous Slurm, ils soumettent trois étapes dépendantes :

```text
Préparation GPU : images → masques éventuels → SfM → découpage
                              ↓ réussite requise (afterok)
Tableau de tâches GPU : un bloc par tâche, un GPU par bloc actif
                              ↓ réussite de tous les blocs requise
Fusion CPU → export final
```

- Panoramax avec `POSTPROCESS_CLEANER=cleangs` : la fusion demande aussi un
  GPU NVIDIA ; sinon la partition doit permettre la fusion sans GPU.
- Dépendances : une préparation en échec bloque l'entraînement ; un bloc
  en échec bloque la fusion. Les dépendances invalides entraînent l'annulation
  des jobs dépendants, sans attente indéfinie.
- Soumission partiellement refusée : les jobs déjà soumis sont annulés.
- `launch.sh` : réserve les ressources et soumet les étapes.
- `launch.slurm` : exécute une étape dans l'allocation ; ne pas le lancer
  directement.
- Nerfstudio : utilise un seul job pour l'ensemble du calcul, sans tableau
  de blocs.

## 5. Notes et informations complémentaires

Les paramètres de ressources doivent correspondre au cluster et aux outils
réellement installés sur les nœuds.

- [Spirula](../tools/spirula/README.md) : utiliser une compilation Vulkan sous
  Linux ; la disponibilité CUDA/PyTorch seule ne suffit pas. Le pilote NVIDIA
  et son ICD Vulkan doivent être installés.
- Isolation GPU : Slurm doit isoler les périphériques du job, par exemple par
  cgroups ; `CUDA_VISIBLE_DEVICES` seul ne sélectionne pas un GPU Vulkan.
  Le lanceur Panoramax ne prend pas en charge les GPU MIG.
- Panoramax : chaque job vérifie `numpy`, `scipy`, `tqdm`, `pyproj`, `plyfile` ;
  les jobs GPU vérifient aussi `nvidia-smi`.
- Diagnostic : consulter les fichiers `.out` et `.err` de l'étape en échec.
  Une ligne `srun: error` relaie l'arrêt ; elle n'en donne pas nécessairement
  la cause. Un code `139` indique un SIGSEGV, pas à lui seul un manque de VRAM.
- Suivi manuel : `squeue -j JOBID` pour la file, `sacct -j JOBID` pour le bilan,
  `scancel JOBID` pour annuler un job.
