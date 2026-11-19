# Panoramax

[Retour aux exemples](README.md) · [Documentation Panoramax](../tools/panoramax/README.md)

## Télécharger les images d'une emprise

Adapter la bbox `xmin,ymin,xmax,ymax`, ici en Lambert-93 (`EPSG:2154`), et
le dossier de sortie :

```bash
bash "$GIT_ROOT/tools/panoramax/download.sh" \
  --bbox "651000,6861000,652000,6862000" \
  --bbox-epsg 2154 --output-dir panoramax
```

Les images et leurs métadonnées sont enregistrées dans `panoramax/`. Un accès
réseau et les dépendances de
[`requirements.txt`](../tools/panoramax/requirements.txt) sont nécessaires.

## Lancer le pipeline complet

Après avoir préparé un fichier `config-panoramax.sh` contenant au minimum
`OUTPUT_DIR` et `PANORAMAX_BBOX` :

```bash
bash "$GIT_ROOT/scripts/panoramax_to_3dgs.sh" config-panoramax.sh
```

Voir aussi l'[exemple du pipeline Panoramax en direct et sur Slurm](pipelines.md).

Pour les panoramas 360 avec HLOC, ajouter à la configuration
`PANORAMAX_PICTURE_TYPE=360`, `COLMAP_METHOD=hloc` et
`HLOC_CAMERA_MODEL=EQUIRECTANGULAR`. Les prérequis de reconstruction et
d'entraînement et les limites de ce mode sont décrits dans la documentation.
