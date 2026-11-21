# Environnements Slurm IGN

`conda_env.yml` définit l'environnement principal. `pdal_env.yml` définit
séparément les dépendances natives de PDAL afin de ne pas modifier `gsplat`.

Le fonctionnement (création automatique à `~/.cache/colmap_to_gs/lidar-pdal`,
`LIDAR_PDAL`, installation et vérification) est décrit dans le
[README LiDAR](../../tools/lidar/README.md#installation-de-lenv-lidar-pdal).
Seuls les sous-processus PDAL utilisent cet environnement ; le pipeline reste
dans son environnement Python initial.
