# Images locales vers Gaussian Splats

Pipeline Spirula sans téléchargement Panoramax :
sélection d'images → masques partagés → SfM global → blocs COLMAP →
entraînement → recadrage bbox COLMAP **avant fusion** → `.splat`.

## Utilisation

Copier `config.sh.example` vers un fichier personnel, régler les chemins et
les ressources. Pour SAM 3, accepter ses conditions explicitement avec
`MASKS_ACCEPT_LICENSE=true`, après les avoir lues.
Activer l'environnement Python habituel (par exemple `mamba activate gsplat`).
Spirula doit disposer du SfM, de l'entraînement et de SAM/Vulkan.
Les outils réutilisés nécessitent notamment numpy, scipy, tqdm, Pillow et plyfile.

Depuis la racine du dépôt, **même configuration pour les deux modes** :

```bash
# Local : tous les blocs sont entraînés successivement.
bash tools/images_to_splat/run_pipeline.sh /chemin/config.sh

# Slurm : préparation GPU, tableau de jobs GPU, puis recadrage/fusion CPU.
bash tools/images_to_splat/launch.sh /chemin/config.sh

# Attendre la fin et retourner une erreur si le calcul échoue.
bash tools/images_to_splat/launch.sh /chemin/config.sh --wait
```

Le dossier d'images peut être indiqué dans la configuration ou remplacé pour
les deux modes par `--images /chemin/absolu/images`. `INPUT_RECURSIVE=true`
parcourt les sous-dossiers ordinaires. Les liens vers des fichiers sont acceptés,
mais pas le parcours des dossiers symboliques. Les noms relatifs sont préservés :
`h00_y0/photo.jpg` et `h01_y45/photo.jpg` restent distincts.
Le workspace doit être extérieur au dossier source. Le dossier `images/` du
workspace contient uniquement des liens symboliques vers la sélection.

`launch.sh` soumet les jobs : ne pas le lancer avec `sbatch`.
`launch.slurm` est le worker interne ; les paramètres Slurm viennent de la config,
pas de directives `#SBATCH` séparées. Les jobs héritent de l'environnement actif ;
`SLURM_ENV_SETUP` permet de charger les modules/activer l'environnement sur les
nœuds. `SLURM_BLOCK_PARALLELISM=4` autorise quatre tâches GPU simultanées ;
le mode local reste séquentiel. Les étapes suivantes ont une dépendance `afterok`.
Les journaux sont dans `OUTPUT_DIR/logs/`.

## Masques, budgets et repères

`MASKS_*` configure une seule politique SAM, utilisée si `SFM_MASKS=on` et/ou
`TRAIN_MASKS=on`. Les deux étapes reçoivent explicitement le même dossier ;
les masques suivent le chemin relatif des images, avec le suffixe `.png`.
Pour des politiques SfM/train différentes, utiliser le pipeline Panoramax ou un
workflow spécifique : cet outil ne calcule pas deux politiques indépendantes.
`sky,cloud` conserve les voitures et passants dans les images d'entraînement,
sans garantir la reconstruction des objets mobiles.

Le réglage d'exemple vise 4 blocs × 2 millions de splats, à 20000 itérations
**par bloc**. Le cap est un maximum ; ni le nombre final ni la qualité ne sont
garantis. Le facteur de croissance vide est estimé par le worker existant,
en supposant une initialisation à 10 % du cap ; un nuage sparse plus fourni
peut faire atteindre le cap plus tôt.
L'arrêt 14000/4000 laisse 4000 pas sans croissance.
`TRAIN_MASKS_MODE=empty` apprend le ciel comme vide ; `ignore` exclut sa perte.

Le SfM utilise des photos individuelles, sans filtrage des paires GPS.
Une caméra par sous-dossier est le défaut ; vérifier que les images d'un même
dossier partagent bien les mêmes caractéristiques optiques. L'alignement
géographique et l'échelle métrique ne sont pas garantis sans références.
Tous les blocs restent alignés dans le même repère SfM.

## Reprise et fusion seule

Le pipeline conserve `inputs.json` (sélection, tailles/dates, paramètres SfM,
masques et découpage). Une modification incompatible est refusée : choisir un
nouvel `OUTPUT_DIR`, sans mélanger plusieurs reconstructions.
Ne pas modifier les images/configurations pendant des jobs actifs.
Une préparation compatible reprend les masques et réutilise le SfM/découpage.
Les entraînements terminés sont réutilisés par défaut ; `TRAIN_CLEAN=true`
recalcule le 3DGS uniquement. Les sorties partielles du train nécessitent aussi
ce réglage, après sauvegarde. L'exemple ne supprime jamais les images source.

```bash
bash tools/images_to_splat/run_pipeline.sh /chemin/config.sh --phase prepare
bash tools/images_to_splat/run_pipeline.sh /chemin/config.sh --phase train-block --block 0
bash tools/images_to_splat/run_pipeline.sh /chemin/config.sh --phase merge
```

La fusion seule exige tous les blocs terminés. Elle recadre chaque brut vers
`gs/block_NNN/splats/block_NNN.bbox.splat`, avec les points **globaux**
`sfm/sparse/0/points3D.bin` : les recouvrements entre blocs sont préservés.
Seuls les fichiers explicitement retenus sont fusionnés (pas de glob mêlant
bruts, rejets et copies recadrées).
Les diagnostics `.filter.json` et `.rejected.splat` sont conservés à côté.
La sécurité de rejet de 20 % s'applique par bloc ; examiner le diagnostic avant
de modifier `POSTPROCESS_MAX_REMOVAL_FRACTION`. Le filtre contextuel est désactivé.
La fusion écrit `OUTPUT_DIR/EXPORT_SPLAT_NAME`, sans réentraîner.
