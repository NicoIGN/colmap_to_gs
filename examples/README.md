# Exemples de commandes

Depuis la racine du dépôt, initialiser `GIT_ROOT` une fois :

```bash
export GIT_ROOT="$(git rev-parse --show-toplevel)"
```

Les chemins d'entrée et de sortie et les coordonnées indiqués sont des exemples
à adapter. Activer l'environnement Python adapté avant de lancer les commandes.
Les prérequis complets sont indiqués dans la documentation de chaque outil.

- [LiDAR : récupérer un `.laz` IGN et le convertir](lidar.md)
- [Pipelines images, vidéo et Panoramax : exécution directe ou Slurm, helper de paramètres](pipelines.md)
- [Panoramax : télécharger des images par emprise](panoramax.md)
- [Images, vidéos et COLMAP](images-colmap.md)
- [Post-traitement et visualisation](postprocess.md)
