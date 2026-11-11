# Composants partagés

[Retour à la documentation générale](../../doc/guide_utilisateur.md)

**Bibliothèques.** Les anciens dossiers racine `scripts/` et `python/` sont
regroupés ici :

- `scripts/` : lancement des workers, configuration, post-traitement et Slurm.
- `python/` : conversions, politiques de masques et contrôle géométrique.
- `scripts/run_3dgs_block.sh` : sélection du worker Spirula ou Nerfstudio.
- `python/nerfstudio_runtime.py` : refus explicite sans PyTorch ou GPU NVIDIA/CUDA.

**Orchestration.** Les pipelines globaux sont dans `script/` à la racine.
Les anciennes entrées `tools/images_to_splat/run_pipeline.sh` et
`tools/panoramax/run_pipeline.sh` restent des renvois de compatibilité.
Le parcours Nerfstudio historique reste accessible par `scripts/run.sh` dans
ce dossier, mais le nouveau pipeline partage préparation COLMAP, blocs et export.
