#!/usr/bin/env bash
# Défauts de la fusion et de l'export .splat.

# ============================================================================
# 6. Fusion et export
# ============================================================================
MERGE_STRENGTH="1.0"     # > 0 ; multiplicateur de taille des voxels
# Nom du fichier .splat exporté ; chaque pipeline le définit dans son config.default.sh.
EXPORT_SPLAT_NAME=""
# La fusion peut retirer des doublons : 8 M est un plafond, pas un nombre garanti.
# Le .splat classique conserve les couleurs DC, pas les SH d'ordre supérieur.
