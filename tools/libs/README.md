# Composants partagés

[Retour à la documentation générale](../../doc/guide_utilisateur.md)

**Bibliothèques.**

- `scripts/` : lancement des workers, configuration, post-traitement et Slurm.
- `python/` : conversions, politiques de masques et contrôle géométrique.
- `scripts/run_3dgs_block.sh` : sélection du worker Spirula ou Nerfstudio.
- `scripts/pipeline_sbatch.sh` : exécution préparation → blocs parallèles → fusion
  dans une seule allocation, utilisée par `script/*.slurm`.
- `python/nerfstudio_runtime.py` : refus explicite sans PyTorch ou GPU NVIDIA/CUDA.

**Orchestration.** Les pipelines globaux sont dans `script/` à la racine.
`scripts/run.sh` entraîne une scène unique avec Nerfstudio, sans découpage
en blocs ni fusion.
