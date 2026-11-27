#!/usr/bin/env bash
# Défauts du mode GRID (dalles indépendantes, clippées à l'emprise de chaque dalle).

# Panoramax ou images géoréférencées (GPS EXIF).
GRID_SOURCE=""
# Emprise globale xmin,ymin,xmax,ymax dans le SCR GRID_CRS.
GRID_BBOX=""
GRID_CRS="2154"
# Taille d'une dalle et recouvrement entre dalles voisines, en unités du SCR (mètres).
GRID_TILE_SIZE=""
GRID_OVERLAP="0"
# Marge de calcul autour de chaque dalle ; seul le résultat dans la dalle est exporté.
GRID_MARGIN="0"
# Dossier d'images (mode images uniquement).
GRID_IMAGES_DIR=""
# Positions WGS84 externes (mode images, optionnel) : lignes `nom_image lat lon [alt]`.
# Prioritaires sur les GPS EXIF ; sans fichier, chaque image doit avoir un GPS EXIF.
GRID_POSITIONS=""
# Sortie du mode GRID ; vide = $OUTPUT_DIR/grid.
GRID_OUTPUT_DIR=""
