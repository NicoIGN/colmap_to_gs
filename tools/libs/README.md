# Composants partagés

[Retour à la documentation générale](../../doc/guide_utilisateur.md)

**Bibliothèques.**

- `scripts/` : lancement des workers, configuration, post-traitement et Slurm.
- `python/` : conversions, politiques de masques et contrôle géométrique.
- `scripts/train_block.sh` : déplacé dans `training/` (voir ci-dessous).
- `scripts/pipeline_slurm.sh` : soumission des étapes Slurm dépendantes.
- `python/nerfstudio_runtime.py` : refus explicite sans PyTorch ou GPU NVIDIA/CUDA.

**Orchestration.** Les pipelines globaux sont dans `scripts/` à la racine.
`scripts/run.sh` entraîne une scène unique avec Nerfstudio, sans découpage
en blocs ni fusion.
