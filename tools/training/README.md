# Entraînement d'un bloc

[Retour à la documentation générale](../../doc/guide_utilisateur.md)

**Objectif.** Entraîner les gaussiennes d'un bloc, indépendamment de la méthode SfM.

**Organisation.** `train_block.sh` choisit le backend selon `TRAIN_BACKEND`
(`spirula` par défaut, ou `nerfstudio`) et lance `backends/` :

| Backend | Script | Rôle |
|---|---|---|
| `spirula` | `backends/train_block_spirula.sh` | Entraînement Spirula, export `export_ply.py` |
| `nerfstudio` | `backends/train_block_nerfstudio.py` | Entraînement Nerfstudio sur le bloc |

Les utilitaires partagés restent dans `tools/libs/` (`scripts/` pour le shell,
`python/` pour `pipeline_lock.py`, `nerfstudio_runtime.py`).
