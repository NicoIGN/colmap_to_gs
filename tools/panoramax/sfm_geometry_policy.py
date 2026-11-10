#!/usr/bin/env python3
"""Guard reuse of Panoramax SfM geometry settings without modifying old models."""

import json
from pathlib import Path
import sys


def check(workspace, values):
    settings = dict(zip(
        ("SFM_MAX_ERROR", "SFM_MIN_INLIERS", "SFM_MIN_TRI_ANGLE",
         "SFM_BA_LOSS", "SFM_BA_LOSS_PARAM"),
        (float(values[0]), int(values[1]), float(values[2]), values[3], float(values[4])),
    ))
    marker = workspace / "sfm_geometry_policy.json"
    if marker.is_file():
        if json.loads(marker.read_text()) != settings:
            raise ValueError(
                "Contrôles géométriques SfM modifiés : choisir un nouvel OUTPUT_DIR "
                f"ou déplacer {workspace} en sauvegarde, avec ses anciens entraînements. "
                "TRAIN_CLEAN ne recalcule pas le SfM."
            )
    elif any((workspace / name).exists() for name in
             ("sparse", "features", "matches.bin", "gps_filter_run.json")):
        print(
            "[AVERTISSEMENT] SfM existant sans signature géométrique : "
            "les nouveaux seuils ne sont PAS appliqués à ce modèle. "
            "Pour les appliquer, choisir un nouvel OUTPUT_DIR.",
            file=sys.stderr,
        )
    else:
        workspace.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps(settings, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    try:
        check(Path(sys.argv[1]), sys.argv[2:])
    except (OSError, ValueError) as error:
        sys.exit(f"[ERREUR] Politique géométrique SfM : {error}")
