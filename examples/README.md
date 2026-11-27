# Exemples de commandes

Depuis la racine du dépôt, initialiser `GIT_ROOT` pour le code et `DATA_ROOT`
pour les données et résultats. Adapter `DATA_ROOT` à son emplacement :

```bash
export GIT_ROOT="$(git rev-parse --show-toplevel)"
export DATA_ROOT="$HOME/colmap-data"
```

Créer le dossier de données si nécessaire. Sur Slurm, choisir un emplacement
accessible aux nœuds de calcul. Les chemins et coordonnées indiqués sont des
exemples à adapter. Activer l'environnement Python adapté avant de lancer les commandes.
Les prérequis complets sont indiqués dans la documentation de chaque outil.

- [LiDAR : récupérer un `.laz` IGN et le convertir](lidar.md)
- [Pipelines images, vidéo et Panoramax : exécution directe ou Slurm, helper de paramètres](../scripts/README.md)
- [Panoramax : télécharger des images par emprise](panoramax.md)
- [Mode GRID : dalles géoréférencées clippées](grid.md)
- [Images, vidéos et COLMAP](images-colmap.md)
- [Post-traitement et visualisation](postprocess.md)
- [Tester le filtre de densité sparse](sparse-density-filter.md)
