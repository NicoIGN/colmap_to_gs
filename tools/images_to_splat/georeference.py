#!/usr/bin/env python3
"""Run and validate the existing GPX alignment before block partitioning."""

import argparse
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys


def validate(output):
    root = output / "georef"
    summary = json.loads((root / "georef_summary.json").read_text())
    models = summary["models"]
    if (len(models) != 1 or models[0]["model"] != "0" or
            models[0]["status"] != "exported_requires_validation"):
        raise ValueError("Un seul modèle GPX valide attendu (modèle 0).")
    for name in ("cameras.bin", "images.bin", "points3D.bin"):
        path = root / "0" / name
        if not path.is_file() or not path.stat().st_size:
            raise ValueError(f"Modèle géoréférencé incomplet : {path}")
    stats = json.loads((root / "0/georef_stats.json").read_text())
    reference = stats["output_reference"]
    if (reference["type"] != "local_ENU" or reference["units"] != "meters" or
            reference["axes"] != ["East", "North", "Up"] or
            len(reference["origin_lat_lon_height"]) != 3 or
            not all(math.isfinite(x) for x in reference["origin_lat_lon_height"])):
        raise ValueError("Référence locale ENU invalide dans le rapport GPX.")
    error = stats["normalized_ordered_rmse"]
    if not math.isfinite(error) or error < 0:
        raise ValueError("Erreur normalisée du recalage GPX invalide.")
    for warning in stats["warnings"]:
        print(f"[AVERTISSEMENT] GPX : {warning}", flush=True)
    if stats["ambiguous"] or error > 0.08:
        if os.environ["GEOREF_ALLOW_UNCERTAIN"] != "true":
            raise ValueError(
                "Recalage GPX ambigu ou erreur normalisée > 0.08 : examiner "
                "georef/georef_trajectories.gpkg et georef/0/georef_stats.json. "
                "Corriger le GPX/GEOREF_VIEW_PREFIX dans un nouveau workspace, "
                "ou accepter explicitement avec GEOREF_ALLOW_UNCERTAIN=true."
            )
        print("[AVERTISSEMENT] Recalage GPX incertain explicitement accepté.", flush=True)
    print("[georef] Repère ENU en mètres ; validation visuelle requise. "
          f"Erreur normalisée : {error:.6g}", flush=True)
    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "check", "export"))
    args = parser.parse_args()
    output = Path(os.environ["OUTPUT_DIR"])
    root = output / "georef"
    marker = root / ".done"
    if args.phase == "prepare":
        if not (root / "georef_summary.json").exists():
            if root.exists() and any(root.iterdir()):
                raise ValueError(
                    "Sortie GPX partielle : sauvegarder/déplacer le dossier georef "
                    "avant de relancer la préparation (sans toucher au SfM brut)."
                )
            command = [
                os.environ["PYTHON_BIN"],
                str(Path(__file__).parents[1] / "preprocess/georef_colmap_from_gpx.py"),
                "--gpx", os.environ["GEOREF_GPX_FILE"],
                "--sparse-dir", str(output / "sfm/sparse"),
                "--output-dir", str(root),
                "--epsg", os.environ["GEOREF_EPSG"],
            ]
            if os.environ["GEOREF_VIEW_PREFIX"]:
                command += ["--view-prefix", os.environ["GEOREF_VIEW_PREFIX"]]
            subprocess.run(command, check=True)
        validate(output)
        shutil.copy2(os.environ["GEOREF_GPX_FILE"], root / "source.gpx")
        marker.touch()
    else:
        if not marker.is_file():
            raise ValueError("Géoréférencement incomplet : relancer --phase prepare.")
        stats = validate(output)
        if args.phase == "export":
            name = Path(os.environ["EXPORT_SPLAT_NAME"])
            sidecar = output / f"{name.stem}.georef.json"
            stats["splat"] = name.name
            stats["georef_summary"] = "georef/georef_summary.json"
            stats["source_gpx"] = "georef/source.gpx"
            sidecar.write_text(json.dumps(stats, indent=2, ensure_ascii=False) + "\n")
            print(f"[georef] Référence du splat : {sidecar}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
        sys.exit(f"[ERREUR] Géoréférencement GPX : {exc}")
