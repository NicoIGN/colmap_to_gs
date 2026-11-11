# COLMAP to Gaussian Splats

Produire des scènes 3D Gaussian Splatting à partir d'images ou d'un modèle COLMAP,
puis les exporter, les traiter et les visualiser.

```text
Images → reconstruction des caméras et points 3D → entraînement → export → visualisation
```

## Documentation

Commencer par le [guide utilisateur](doc/guide_utilisateur.md) pour comprendre
le résultat et choisir un pipeline.

- **Images locales avec Spirula** : [pipeline images vers splat](tools/images_to_splat/README.md).
- **Images Panoramax avec Spirula** : [téléchargement et pipeline Panoramax](tools/panoramax/README.md).
- **Dataset COLMAP avec Nerfstudio / Splatfacto** : [guide du pipeline](doc/nerfstudio.md).
- **Exécution directe ou sur cluster** : [lancement Bash / Slurm](doc/lancement_bash_slurm.md).

## Démarrer

Pour le pipeline images locales, depuis la racine du dépôt :

```bash
cp tools/images_to_splat/config.sh.example /chemin/config.sh
# Adapter les chemins et réglages dans /chemin/config.sh.
bash tools/images_to_splat/run_pipeline.sh /chemin/config.sh
```

Les installations, données attendues, paramètres et sorties sont décrits dans
le README du pipeline choisi.

## Outils

- [Préparation des images et modèles COLMAP](tools/preprocess/README.md).
- [Installation de Spirula](tools/spirula/README.md).
- [Masques](tools/segmentation_masks/README.md).
- [Export, filtrage et fusion](tools/postprocess/README.md).
- [Nettoyage multivues du ciel](tools/clean_gs/README.md).
- [Visualisation](tools/viewer/README.md).
- [Conversion en 3D Tiles](tools/splat_to_3dtiles/README.md).
