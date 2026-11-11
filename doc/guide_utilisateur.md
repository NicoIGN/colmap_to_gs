# Guide utilisateur — produire et visualiser un Gaussian Splat

Ce guide présente les Gaussian Splats, le rôle de ce projet et les grandes étapes pour transformer des images accompagnées d'un modèle COLMAP en scène 3D visualisable.

## 1. Présentation

### 1.1. Qu'est-ce qu'un Gaussian Splat ?

Un Gaussian Splat représente une scène 3D par un ensemble de gaussiennes : des ellipsoïdes colorés dotés d'une position, d'une taille, d'une orientation et d'une opacité. À partir d'un point de vue, elles sont projetées sur l'image puis compositées pour produire le rendu.

Contrairement à un maillage composé de surfaces, cette représentation décrit directement l'apparence de la scène depuis plusieurs points de vue. Une fois entraîné, le modèle peut être rendu et exploré en temps réel sur un matériel adapté.

![Exemple de gaussienne](technopitch/images/splat.png)

### 1.2. À quoi sert ce projet ?

Ce dépôt fournit un pipeline expérimental fondé sur COLMAP, Nerfstudio et Splatfacto. Il permet de préparer des images et les données de caméra associées, d'entraîner une représentation Gaussian Splat, puis d'exporter un fichier `.ply` destiné à la visualisation.

Le projet explore notamment la restitution immersive de scènes acquises par smartphone, drone et imagerie aérienne. Les essais décrits dans le technopitch montrent que les résultats dépendent fortement de la qualité des images, de leur variété de points de vue, de la reconstruction des caméras et des ressources de calcul. Les scènes aériennes ou géoréférencées peuvent nécessiter des traitements complémentaires et ne garantissent pas à elles seules une reconstruction fiable.

### 1.3. Comment la scène est-elle reconstruite ?

Le principe général est le suivant :

1. **Photogrammétrie** : COLMAP met en correspondance les images et estime les paramètres des caméras ainsi qu'un nuage de points 3D clairsemé.
2. **Initialisation** : le nuage de points fournit une géométrie initiale aux gaussiennes.
3. **Entraînement** : Splatfacto génère des vues synthétiques et ajuste itérativement les gaussiennes pour les rapprocher des images d'entrée. Cette étape utilise le GPU.
4. **Export et nettoyage** : le modèle entraîné est exporté en `.ply`, puis peut être filtré à partir du modèle COLMAP et des transformations produites pendant l'entraînement.

![Reconstruction SfM](technopitch/images/sfm.jpg)

## 2. Préparer l'environnement et les données

### 2.1. Prérequis

- Un GPU NVIDIA fonctionnel (`nvidia-smi` doit répondre) pour l'entraînement de ce pipeline.
- Python 3.10 ou 3.11.
- Un environnement Conda avec Nerfstudio, notamment `ns-train` et `ns-export`, ainsi que les dépendances du projet.
- COLMAP si vous devez construire le modèle photogrammétrique à partir des images.

Le dépôt contient une définition d'environnement dans `environment/ign.slurm/conda_env.yml`. L'environnement est activé automatiquement par défaut par `scripts/run.sh`; `--skip-conda` permet de ne pas l'activer si l'environnement est déjà préparé.

### 2.2. Dataset attendu

Le pipeline attend un dossier de cette forme :

```text
mon_dataset/
├── images/                      # au moins deux images .jpg, .jpeg ou .png
├── colmap/
│   └── sparse/
│       └── 0/
│           ├── cameras.bin
│           ├── images.bin
│           └── points3D.bin
└── sparse_pc.ply
```

Le modèle COLMAP doit contenir les paramètres des caméras et les points 3D. `sparse_pc.ply` sert de nuage initial au modèle et doit être cohérent avec le repère du modèle COLMAP.

Pour créer ce dataset depuis un dossier d'images, le dépôt fournit `tools/preprocess/images_to_colmap.py`. Par exemple :

```bash
python3 tools/preprocess/images_to_colmap.py \
  --image_dir /chemin/vers/images \
  --output_dir /chemin/vers/mon_dataset \
  --camera_model OPENCV \
  --matcher sequential
```

Ce script nécessite COLMAP et réalise la reconstruction photogrammétrique. Pour des données déjà préparées, vérifiez directement la structure attendue ci-dessus.

## 3. Lancer le pipeline

### 3.1. Lancement local

Depuis la racine du dépôt, lancez par exemple :

```bash
./scripts/run.sh \
  --dataset-dir /chemin/absolu/vers/mon_dataset \
  --output-dir /chemin/absolu/vers/mes_resultats \
  --gsplat-profile balanced \
  --name ma_scene
```

Les chemins absolus sont recommandés. Les profils disponibles sont `fast`, `balanced`, `quality` et `best`. Pour consulter les options :

```bash
./scripts/run.sh --help
```

Options utiles :

| Option | Rôle |
|---|---|
| `--dataset-dir` | Dossier dataset requis |
| `--output-dir` | Racine des résultats |
| `--name` | Nom du fichier `.ply` final |
| `--gsplat-profile` | Profil de paramètres |
| `--two-stages` | Entraînement en deux passes |
| `--max-jobs` | Nombre de tâches parallèles |
| `--skip-conda` | Utilise l'environnement déjà actif |
| `--skip-training` | Saute l'entraînement |
| `--skip-export` | Saute l'export et le nettoyage |
| `--no-proxy` | Désactive le proxy configuré par défaut |

### 3.2. Étapes exécutées

Le script `scripts/run.sh` orchestre les étapes suivantes :

1. Vérifie la présence des images et des fichiers COLMAP requis.
2. Génère `transforms.json` depuis le modèle COLMAP si le fichier n'existe pas déjà. Un fichier existant est conservé.
3. Charge la configuration générale et, si demandé, le profil d'entraînement.
4. Vérifie la disponibilité du GPU, prépare l'environnement Conda et vérifie la version de Python.
5. Estime les plans `near` et `far` à partir du modèle COLMAP.
6. Lance l'entraînement Splatfacto, éventuellement en deux passes.
7. Exporte le modèle entraîné en `.ply`, puis le nettoie.

### 3.3. Résultats

Les résultats sont écrits sous la racine choisie avec `--output-dir`. L'arborescence visée comprend le modèle Nerfstudio, le `.ply` exporté et le fichier final nettoyé :

```text
mes_resultats/
└── model3d/
    ├── splatfacto/
    │   └── <run>/
    │       ├── config.yml
    │       ├── dataparser_transforms.json
    │       └── nerfstudio_models/
    ├── splat.ply
    └── cleaned/
        └── ma_scene.ply
```

Le fichier `dataparser_transforms.json` décrit les transformations appliquées par Nerfstudio pendant l'entraînement. Le nettoyage s'en sert avec le modèle COLMAP ; il ne faut pas le confondre avec le `transforms.json` d'entrée.

### 3.4. Visualiser un fichier `.ply`

Le dépôt comprend un visualiseur local. Lancez-le depuis la racine :

```bash
python3 tools/viewer/server.py
```

Puis ouvrez `http://127.0.0.1:8000/` dans un navigateur. Le serveur n'écoute que sur la machine locale ; arrêtez-le avec `Ctrl+C`.

### 3.5. Exécution sur SLURM

Pour soumettre un calcul au cluster, le dépôt fournit `environment/ign.slurm/launch.sh` et `launch.slurm`. Le lanceur attend notamment les variables `GIT_ROOT`, `OUTPUT_DIR`, `DATASET_DIR`, `GSPLAT_PROFILE` et `BASENAME`. Les détails et exemples de soumission sont décrits dans la section « Configuration et utilisation » du [README](../README.md#3-configuration-et-utilisation).

## 4. Limites et conseils pratiques

- Une couverture suffisante de la scène et des points de vue variés sont importants ; des images prises presque sous le même angle peuvent mal contraindre la géométrie.
- Les surfaces cachées dans les images ne sont pas reconstruites de façon fiable par le seul apprentissage des splats.
- Les très grandes images et les scènes volumineuses peuvent demander beaucoup de mémoire et de temps de calcul.
- Des artefacts et des splats parasites peuvent apparaître, notamment aux limites de la scène ou dans les zones peu observées.
- Le résultat est une représentation visuelle complémentaire : il ne remplace pas automatiquement un maillage ou un produit géospatial répondant à des exigences métriques.

## 5. À savoir

Le pipeline conserve un `transforms.json` déjà présent. Pour repartir des paramètres COLMAP courants, supprime ce fichier avant de relancer `scripts/run.sh`.
