# Environnements Slurm IGN

`conda_env.yml` définit l'environnement principal. `pdal_env.yml` définit
séparément les dépendances natives de PDAL afin de ne pas modifier `gsplat`.

Les scripts LiDAR créent automatiquement ce second environnement à
`~/.cache/colmap_to_gs/lidar-pdal` quand `LIDAR_PDAL=pdal` (défaut), sauf si
un LAZ partagé permet d'éviter l'extraction. Mamba, micromamba ou Conda doit
être disponible sur les nœuds. L'installation nécessite un accès à conda-forge.
Seuls les sous-processus PDAL utilisent cet environnement ; le pipeline reste
dans son environnement Python initial.
