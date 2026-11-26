# Guide utilisateur — produire et visualiser un Gaussian Splat

[Accueil du projet](../README.md)

Ce guide aide à choisir comment produire les données COLMAP et entraîner une
scène 3D. Le [README général](../README.md#1-quest-ce-que-le-3dgs-)
présente
les Gaussian Splats et le principe général. Les commandes complètes, options,
paramètres et détails de chaque outil sont documentés dans les README
spécialisés liés ci-dessous.

## 1. Installation

1. **Environnement Conda** : les librairies nécessaires aux pipelines sont contenues dans l'environnement Conda de votre plateforme, dans [`environment/`](environment/) (`macos`, `ign.slurm`, `google.colab`). Exemple : `conda env create --yes --file environment/macos/conda_env.yml`. Ensuite, activez-le avec `conda activate gsplat`.

2. **Spirula Studio** : compilez-le avec [`install/spirula/install_spirula.sh`](install/spirula/README.md), qui applique les bonnes options et télécharge le modèle SAM3 utilisé pour les masques.

3. **COLMAP et HLOC** : HLOC améliore la mise en correspondance des images pour la reconstruction 3D. Si vous utilisez cette option, installez-le avec `bash install/colmap/install-hloc.sh`. Voir [HLOC sur GitHub](https://github.com/cvg/Hierarchical-Localization) et le [README SfM](tools/sfm/README.md).

## 2. Choisir un parcours

### 2.3. Utiliser un pipeline complet

Trois pipelines enchaînent production COLMAP, entraînement et export. Chacun existe en version directe (machine courante) et en version Slurm (`launch_*`, à lancer avec `bash`, pas `sbatch`, depuis le nœud de connexion) :

| Pipeline | Entrée | Direct | Slurm |
|---|---|---|---|
| Images locales | dossier de photos | `bash scripts/images_to_3dgs.sh CONFIG` | `bash scripts/launch_images_to_3dgs.sh CONFIG --wait` |
| Vidéo | fichier vidéo | `bash scripts/videos_to_3dgs.sh CONFIG` | `bash scripts/launch_videos_to_3dgs.sh CONFIG --wait` |
| Panoramax | emprise (`PANORAMAX_BBOX`) | `bash scripts/panoramax_to_3dgs.sh CONFIG` | `bash scripts/launch_panoramax_to_3dgs.sh CONFIG --wait` |

Exemple de configuration (images locales), qui ne contient que les surcharges :

```bash
cat > "$DATA_ROOT/config-images.sh" <<'EOF'
#!/usr/bin/env bash
SFM_IMAGES_DIR="/chemin/images"
OUTPUT_DIR="/chemin/sortie"
EOF
```

Lancement standard, sur la machine courante :

```bash
bash "$GIT_ROOT/scripts/images_to_3dgs.sh" "$DATA_ROOT/config-images.sh"
```

Lancement Slurm, depuis le nœud de connexion : soumission puis attente de la fin.

```bash
bash "$GIT_ROOT/scripts/launch_images_to_3dgs.sh" "$DATA_ROOT/config-images.sh" --wait
```

Sortie : un `.splat` et ses métadonnées dans `OUTPUT_DIR`. Les journaux sont dans `OUTPUT_DIR/logs/`.

Sans `--wait`, un retour réussi confirme seulement la soumission. Le détail des modes et des ressources est dans le [guide Bash / Slurm](lancement_bash_slurm.md), et les autres exemples sont dans [scripts/README.md](../scripts/README.md).

## 3. Préparer les données et lancer un calcul

Le principe commun est de fournir des images et un modèle COLMAP cohérent :
les caméras, images enregistrées et points 3D doivent décrire la même scène.
Selon le parcours choisi, le dépôt peut préparer ce modèle ou réutiliser un
dataset déjà constitué.

Pour le pipeline Nerfstudio / Splatfacto, le point d'entrée est
`tools/libs/scripts/run.sh`. Exemple :

```bash
./tools/libs/scripts/run.sh \
  --dataset-dir /chemin/absolu/vers/mon_dataset \
  --output-dir /chemin/absolu/vers/mes_resultats \
  --gsplat-profile balanced \
  --name ma_scene
```

Cette commande illustre le lancement ; les options, valeurs possibles,
prérequis et étapes exécutées sont décrits dans le
[guide Nerfstudio](nerfstudio.md#3-configuration-et-utilisation).

Pour les autres parcours, utiliser les commandes et fichiers de configuration
de leur README : le pipeline images locales et le pipeline Panoramax ont des
étapes et sorties propres.

## 4. Consulter les guides des outils

- **Lancement Bash ou Slurm, ressources et suivi des jobs** :
  [guide de lancement](lancement_bash_slurm.md).
- **Préparation des données et sparse COLMAP** :
  [tools/prepare](../tools/prepare/README.md).
- **Choix du producteur COLMAP et conversion orientations/LiDAR** :
  [tools/sfm](../tools/sfm/README.md).
- **Conversion des poses caméra IGNF `.CON` et du LiDAR `.LAZ`** :
  [tools/con_laz_to_orientations](../tools/con_laz_to_orientations/README.md).
- **Masques de segmentation** :
  [tools/masks](../tools/masks/README.md).
- **Filtrage et fusion des splats** :
  [tools/export](../tools/export/README.md).
- **Nettoyage multivues du ciel** :
  [tools/export/clean_gs](../tools/export/clean_gs/README.md).
- **Installation de Spirula** :
  [install/spirula](../install/spirula/README.md).
- **Visualisation locale** :
  [tools/viewer](../tools/viewer/README.md).
- **Conversion d'un SPLAT géoréférencé en 3D Tiles** :
  [tools/export/export_3dtiles.md](../tools/export/export_3dtiles.md).

Ces documents détaillent les paramètres, dépendances, fichiers produits,
commandes de validation et particularités de chaque outil.

## 5. Limites, conseils pratiques et à savoir

- Une couverture suffisante de la scène et des points de vue variés sont
  importants ; des images prises presque sous le même angle peuvent mal
  contraindre la géométrie.
- Les surfaces cachées dans les images ne sont pas reconstruites de façon
  fiable par le seul apprentissage des splats.
- Les très grandes images et les scènes volumineuses peuvent demander beaucoup
  de mémoire et de temps de calcul.
- Des artefacts et des splats parasites peuvent apparaître, notamment aux
  limites de la scène ou dans les zones peu observées.
- Le résultat est une représentation visuelle complémentaire : il ne remplace
  pas automatiquement un maillage ou un produit géospatial répondant à des
  exigences métriques.
- Les scènes aériennes ou géoréférencées peuvent nécessiter des traitements
  complémentaires et ne garantissent pas à elles seules une reconstruction
  fiable.

Le pipeline Nerfstudio conserve un `transforms.json` déjà présent. Pour repartir
des paramètres COLMAP courants, supprimer ce fichier avant de relancer
`tools/libs/scripts/run.sh`. Les détails de ce comportement et des transformations sont
dans le [guide Nerfstudio](nerfstudio.md#2-entrées-et-sorties).
