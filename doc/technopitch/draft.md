# Les splats gaussiens, une nouvelle manière de représenter nos données 3D


## 1. Un Gaussian Splat, c'est quoi ?
--

Pour représenter une scène en trois dimensions, on utilisait en général :
- **un maillage texturé** : une surface composée de triangles, sur laquelle sont projetées des images
- **un nuage de points** : des positions 3D, éventuellement accompagnées de couleurs

Récemment, deux approches paramétriques modernes se sont imposées :
a) **Les NeRF (Neural Radiance Fields)** :  un réseau neuronal encode la radiance (couleur + luminosité) en fonction de la position et de la direction d'observation.
- ✓ Qualité visuelle exceptionnelle
- ✗ Rendu très lent (secondes par image)

b) L**es 3D Gaussian Splatting** : la scène est décrite par un ensemble de **gaussiennes 3D** : des ellipsoïdes colorés avec une certaine opacité.
Chaque gaussienne possède :
une **position** dans l'espace ;
  - une **échelle** et une **rotation**, qui définissent sa forme et son orientation ;
  - une **opacité** ;
  - des **attributs de couleur**, pouvant représenter une apparence qui varie selon la direction d'observation.

<p align="center">
  <img src="images/splat.png" alt="Un splat gaussien" width="400"><br>
  <em>Source : <a href="https://brunzema.github.io/visualizations/gaussian-splatting">A visual exploration of Gaussian Splatting — Paul Brunzema</a></em>
</p>

Le rendu peut alors se faire en **temps réel**, permettant une navigation 3D immersive avec une finesse de rendu très au-dessus des approches plus classiques.

[Démo interactive: Niseko Village — Snowy Ski Resort [Source : Splat Labs — Gaussian Splatting](https://cloud.splatlabs.ai/viewer/0729c1d5-8d65-4dbc-b4f4-a7ed4fbd6ce3)]

## 2. Pourquoi s'y intéresser maintenant ?
---

### 2.1 Maturité de la technologie : d'un sujet de recherche à une brique standardisée
Les Gaussian Splats ne sont plus une technique expérimentale confinée à la recherche académique. Ils s'imposent comme **le paradigme dominant pour la visualisation 3D immersive et interactive** à grande échelle.
**Signaux forts de maturité technologique :**
- **Standardisation dans l'écosystème glTF** : le groupe **Khronos**, qui définit la plupart des standards de rendu graphique 3D, a annoncé l'intégration des Gaussian Splats comme primitive 3D officielle dans les standards web et géospatiaux.
- **Intégration dans les workflows de streaming géospatial** : **Cesium** offre désormais un support natif des Gaussian Splats dans les **3D Tiles**, avec gestion hiérarchique du LOD (Level of Detail) pour le streaming progressif de scènes massives.
- **Exploration des formats 360°** : des outils comme **360 Splat Pro** appliquent la technologie aux panoramas, ouvrant de nouveaux usages de capture.
- **Adoption par les géants de la cartographie** : **Google** (Immersive View) et **Apple** (Detailed City Experience) renforcent leurs offres de cartographie 3D photoréaliste immersive.

### 2.2 Valoriser l’existant : enrichir les usages de nos données
L’IGNF dispose d’un patrimoine de données géoréférencées dont les Gaussian Splats pourraient offrir une **nouvelle forme de restitution 3D photoréaliste** :
- Prises de vues aériennes  (**PCRS**, autres PVA haute et très haute résolution)
- Nuages de points **LiDAR**
- **Acquisitions Stéréopolis**, pour restituer les environnements urbains depuis le sol
- **Acquisitions à façon des Travaux Spéciaux**, pour des sites ou des besoins spécifiques

L’intérêt serait de **tirer davantage de valeur des acquisitions existantes**, en proposant une exploration immersive complémentaire aux cartes, aux nuages de points et aux maillages : découverte d’un territoire, consultation à distance d’un site ou médiation auprès des utilisateurs.

### 2.3 Explorer de nouvelles offres de capture et de restitution 3D
Les Gaussian Splats invitent également à étudier des **dispositifs d’acquisition légers et ciblés** :**
- véhicule (vélo ou autre) + téléphone portable + GPS : explorer la capture rapide de rues et d’itinéraires, avec un matériel peu coûteux
- **Drone** : documenter un site depuis des points de vue complémentaires, selon les besoins et les contraintes de vol

Pour l’IGNF, l’enjeu serait d’évaluer la possibilité de produire des **scènes 3D immersives à la demande**, sur des zones ciblées ou lors d’acquisitions répétées.

**Conclusion :**  
Les Gaussian Splats sont en transition de **technologie de recherche** à **brique technologique standardisée**. Nous pensons que c'est une technologie en voie d'usage intensif et que l'IGNF doit assurer une veille technologique conséquente et en explorer les potentialités dans les contextes pertinents pour ses domaines d'expertise.

---
## 2. Comment génère-t-on des splats ?
La génération de Gaussian Splats repose sur un apprentissage itératif. Il prend en entrée:
1. des **images de la scène**, prises depuis plusieurs points de vue ;
2. les **paramètres des caméras** : position, orientation et paramètres internes ;
3. une **géométrie initiale**, sous la forme d’un nuage de points 3D peu dense, obtenu par photogrammétrie (on utilise en général colmap).
### Étape 1 : Photogrammétrie et reconstruction de la géométrie initiale

À partir d'un ensemble d'images de la scène, on utilise des méthodes de type **Structure-from-Motion (SfM)** (en général via **COLMAP**) pour :
détecter les points caractéristiques dans chaque image ;
- les mettre en correspondance entre les différentes vues ;
- estimer la **position et l'orientation de chaque caméra** (paramètres extrinsèques) ;
- estimer les **paramètres de calibration des caméras** (paramètres intrinsèques : focale, centre optique) ;
- reconstruire un **nuage de points 3D peu dense** (sparse point cloud) représentant la géométrie grossière de la scène.

<p align="center">
  <img src="images/sfm.jpg" alt="Schéma du processus de Structure-from-Motion (SfM)" width="600"><br>
  <em>Source : <a href="https://learnopencv.com/">LearnOpenCV</a></em>
</p>

### Étape 2 : Initialisation et optimisation des Gaussian Splats
Le nuage de points 3D sparse sert à **initialiser une représentation de la scène**. Chaque point est converti en une gaussienne 3D caractérisée par :
sa position dans l'espace, sa taille, son orientation, une opacité et une couleur.
À partir de ces gaussiennes, un moteur d'optimisation sur GPU (typiquement **GSplat**) exécute une boucle d'apprentissage :

1. rend une image de la scène depuis une caméra donnée ;
2. la compare aux images réelles correspondantes ;
3. calcule l'erreur entre le rendu et la réalité ;
4. met à jour les paramètres des gaussiennes pour réduire cette erreur.

Au cours de l’optimisation, la représentation peut également évoluer pour mieux décrire la scène :**
- **Split** : subdiviser des gaussiennes pour mieux représenter les zones complexes ou insuffisamment détaillées.
* **Pruning** : supprimer les gaussiennes peu contributives ou dont l’opacité est trop faible.
* **Culling** : écarter du rendu les éléments qui ne contribuent pas à l’image considérée, par exemple parce qu’ils sont hors du champ de vision.

Ces opérations permettent d’ajuster progressivement la densité et la répartition des gaussiennes en fonction des détails de la scène.

<p align="center">
  <img src="images/learning.png" alt="Schéma du processus d'apprentissage des Gaussian Splats" width="600"><br>
  <em>Source : <a href="https://zhangtemplar.github.io/3d-gaussian-splatting/">3D Gaussian Splatting for Real-Time Radiance Field Rendering (2023) — Qiang Zhang</a></em>
</p>

--

## 4. Notre approche : des données métier à une scène visualisable
### Durée indicative : 1 min 15

### Le rôle du pipeline `aerial_data_to_gaussian_splats`

Le pipeline développé relie les données géoréférencées aux outils d’entraînement et de visualisation des splats gaussiens.

### Étape 1 — Préparer les données

**Entrées :**

- images orientées au format **IGNF CON/XML** ;
- nuage de points **LiDAR au format `.laz`**.

Les images, les paramètres caméra et les points LiDAR sont convertis vers une structure compatible avec **COLMAP et Nerfstudio**.

Cette préparation comprend notamment :

- le sous-échantillonnage des images et du LiDAR ;
- la conversion des paramètres caméra ;
- la reprojection des points LiDAR dans les images ;
- la production des fichiers nécessaires à l’entraînement.

**Particularité importante :**
dans cette voie de traitement, on ne réestime pas les orientations par une reconstruction photogrammétrique classique à partir de correspondances entre images.

On exploite **des orientations déjà connues et un nuage LiDAR existant**.

### Étape 2 — Entraîner et exporter

Le pipeline :

- estime les limites proches et lointaines utiles à l’entraînement ;
- entraîne un modèle **`splatfacto` de Nerfstudio** ;
- exporte les gaussiennes ;
- applique un nettoyage spatial fondé sur la proximité à la géométrie de référence.

La préparation des données ne nécessite pas de GPU.
L’entraînement est réalisé sur GPU, dans un environnement prévu notamment pour des exécutions sur serveur ou cluster Slurm.

### Le livrable : un `.ply` enrichi

Le résultat est un fichier **`.ply` contenant les paramètres des gaussiennes** :
positions, échelles, rotations, opacités et attributs d’apparence.

Ce n’est donc **pas un simple nuage de points**, même si l’extension est identique.

Il peut être ouvert dans un outil compatible avec les Gaussian Splats, comme **SuperSplat**.

**Visuel conseillé :**
Images orientées + LiDAR → conversion COLMAP/Nerfstudio
→ entraînement → export et nettoyage → viewer.

---

## 5. Le point de vigilance à garder en tête
### Durée indicative : 30 s

### Qualité visuelle et qualité géométrique ne sont pas équivalentes

Une scène peut être visuellement convaincante sans que sa géométrie soit suffisamment exacte pour un usage de mesure.

Inversement, une géométrie bien mesurée ne garantit pas un rendu satisfaisant depuis tous les points de vue.

L’utilisation du LiDAR comme initialisation et référence de nettoyage est un atout, mais elle ne suffit pas à garantir l’exactitude géométrique du modèle final.

Il faut donc distinguer :

- la **fidélité visuelle** aux images ;
- la **cohérence géométrique** ;
- la **couverture des points de vue** ;
- les **performances de calcul et de visualisation**.

> **Conclusion de l’introduction :**
> les splats gaussiens constituent une piste prometteuse pour la visualisation
> de nos données. Nos travaux visent à comprendre comment les produire à partir
> de nos jeux de données, avec quelles qualités et quelles limites.
