#!/usr/bin/env python3
"""Convert a local-ENU .splat file into a georeferenced 3D Tiles tileset."""

import argparse
import json
import math
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

C0 = 0.28209479177387814
SPLAT_DTYPE = np.dtype(
    [
        ("position", "<f4", (3,)),
        ("scale", "<f4", (3,)),
        ("rgba", "u1", (4,)),
        ("rotation", "u1", (4,)),
    ]
)
PLY_DTYPE = np.dtype(
    [
        ("x", "<f4"),
        ("y", "<f4"),
        ("z", "<f4"),
        ("f_dc_0", "<f4"),
        ("f_dc_1", "<f4"),
        ("f_dc_2", "<f4"),
        ("opacity", "<f4"),
        ("scale_0", "<f4"),
        ("scale_1", "<f4"),
        ("scale_2", "<f4"),
        ("rot_0", "<f4"),
        ("rot_1", "<f4"),
        ("rot_2", "<f4"),
        ("rot_3", "<f4"),
    ]
)
PLY_HEADER = b"""ply
format binary_little_endian 1.0
comment offsetx=0
comment offsety=0
comment offsetz=0
element vertex {count}
property float x
property float y
property float z
property float f_dc_0
property float f_dc_1
property float f_dc_2
property float opacity
property float scale_0
property float scale_1
property float scale_2
property float rot_0
property float rot_1
property float rot_2
property float rot_3
end_header
"""


def read_georeference(path, model=None):
    with path.open(encoding="utf-8") as stream:
        metadata = json.load(stream)

    reference = metadata.get("output_reference")
    if reference is None and isinstance(metadata.get("models"), list):
        models = [
            item
            for item in metadata["models"]
            if isinstance(item, dict)
            and item.get("status") != "failed"
            and item.get("stats_file")
        ]
        if model is not None:
            models = [item for item in models if item.get("model") == model]
            if not models:
                raise ValueError(f"Modèle absent du résumé ou en échec : {model}")
        elif len(models) != 1:
            choices = ", ".join(str(item.get("model")) for item in models)
            raise ValueError(
                "Le résumé contient plusieurs modèles ; choisir explicitement "
                f"avec --model. Modèles disponibles : {choices or 'aucun'}."
            )
        if len(models) != 1:
            raise ValueError("Impossible de déterminer un modèle unique.")

        model_record = models[0]
        stats_path = Path(model_record["stats_file"]).expanduser()
        if not stats_path.is_absolute():
            stats_path = path.parent / stats_path
        if not stats_path.is_file():
            model_path = Path(str(model_record.get("model", ".")))
            stats_path = path.parent / model_path / "georef_stats.json"
        if not stats_path.is_file():
            raise ValueError(
                "Fichier georef_stats.json introuvable pour le modèle "
                f"{model_record.get('model')}: {stats_path}"
            )
        with stats_path.open(encoding="utf-8") as stream:
            metadata = json.load(stream)
        reference = metadata.get("output_reference")

    if not isinstance(reference, dict) or reference.get("type") != "local_ENU":
        raise ValueError(
            "Le rapport de géoréférencement doit décrire un repère local_ENU."
        )
    if reference.get("axes") != ["East", "North", "Up"]:
        raise ValueError("Le repère attendu doit avoir les axes East, North, Up.")
    if reference.get("units") != "meters":
        raise ValueError("Le repère local_ENU doit être exprimé en mètres.")

    origin = reference.get("origin_lat_lon_height")
    if not isinstance(origin, list) or len(origin) != 3:
        raise ValueError("origin_lat_lon_height doit contenir [latitude, longitude, hauteur].")

    try:
        latitude, longitude, height = (float(value) for value in origin)
    except (TypeError, ValueError) as exc:
        raise ValueError("Origine géographique invalide.") from exc
    if not all(math.isfinite(value) for value in (latitude, longitude, height)):
        raise ValueError("L'origine géographique doit contenir des valeurs finies.")
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise ValueError("Latitude ou longitude hors limites.")

    return latitude, longitude, height


def convert_splat_to_ply(source, destination):
    size = source.stat().st_size
    if size == 0 or size % SPLAT_DTYPE.itemsize:
        raise ValueError(
            f"Fichier .splat invalide: {size} octets, "
            f"chaque splat doit occuper {SPLAT_DTYPE.itemsize} octets."
        )

    count = size // SPLAT_DTYPE.itemsize
    splats = np.memmap(source, dtype=SPLAT_DTYPE, mode="r", shape=(count,))
    try:
        with destination.open("wb") as stream:
            stream.write(PLY_HEADER.replace(b"{count}", str(count).encode("ascii")))
            for start in range(0, count, 250_000):
                raw = splats[start : start + 250_000]
                result = np.empty(len(raw), dtype=PLY_DTYPE)
                positions = raw["position"]
                scales = raw["scale"].astype(np.float64)
                quaternions = (raw["rotation"].astype(np.float64) - 128.0) / 128.0
                quaternion_norms = np.linalg.norm(quaternions, axis=1)

                if not np.isfinite(positions).all():
                    raise ValueError("Le fichier .splat contient des positions non finies.")
                if not np.isfinite(scales).all() or np.any(scales <= 0):
                    raise ValueError("Le fichier .splat contient des échelles invalides.")
                if np.any(quaternion_norms < 1e-8):
                    raise ValueError("Le fichier .splat contient un quaternion nul.")

                result["x"], result["y"], result["z"] = positions.T
                result["f_dc_0"], result["f_dc_1"], result["f_dc_2"] = (
                    (raw["rgba"][:, :3].astype(np.float64) / 255.0 - 0.5) / C0
                ).T
                alpha = np.clip(raw["rgba"][:, 3].astype(np.float64) / 255.0, 1e-6, 1 - 1e-6)
                result["opacity"] = np.log(alpha / (1.0 - alpha))
                result["scale_0"], result["scale_1"], result["scale_2"] = np.log(scales).T
                result["rot_0"], result["rot_1"], result["rot_2"], result["rot_3"] = (
                    quaternions / quaternion_norms[:, None]
                ).T
                stream.write(result.tobytes())
    finally:
        mmap = getattr(splats, "_mmap", None)
        if mmap is not None:
            mmap.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Fichier .splat en repère local ENU")
    parser.add_argument("--georef-summary", required=True, type=Path, help="georef_summary.json de georef_colmap_from_gpx.py")
    parser.add_argument(
        "--model",
        help="Modèle du résumé à utiliser (requis si le rapport en contient plusieurs)",
    )
    parser.add_argument("--output-dir", required=True, type=Path, help="Dossier de sortie 3D Tiles")
    parser.add_argument(
        "--converter",
        default="3dgs-ply-3dtiles-converter",
        help="Commande du convertisseur Node.js installé (défaut: 3dgs-ply-3dtiles-converter)",
    )
    parser.add_argument("--overwrite", action="store_true", help="Autorise le convertisseur à remplacer le dossier de sortie")
    parser.add_argument("--no-open-inspector", action="store_true", help="N'ouvre pas le visualiseur après conversion")
    args = parser.parse_args()

    source = args.input.expanduser().resolve()
    georef_path = args.georef_summary.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    if not source.is_file():
        parser.error(f"Fichier .splat introuvable : {source}")
    if source.suffix.lower() != ".splat":
        parser.error("Le fichier d'entrée doit avoir l'extension .splat.")
    if not georef_path.is_file():
        parser.error(f"Fichier de géoréférencement introuvable : {georef_path}")
    if output_dir.exists() and not args.overwrite:
        parser.error(f"Le dossier de sortie existe déjà : {output_dir} (utiliser --overwrite pour le remplacer)")

    converter = shutil.which(args.converter)
    if converter is None:
        parser.error(
            f"Convertisseur introuvable : {args.converter}. "
            "Installer 3dgs-ply-3dtiles-converter (voir README.md)."
        )

    try:
        latitude, longitude, height = read_georeference(georef_path, args.model)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        parser.error(str(exc))

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="splat-to-3dtiles-") as temporary_dir:
        ply_path = Path(temporary_dir) / "scene.ply"
        try:
            convert_splat_to_ply(source, ply_path)
        except (OSError, ValueError) as exc:
            parser.error(str(exc))

        command = [
            converter,
            str(ply_path),
            str(output_dir),
            "--coordinate",
            json.dumps([latitude, longitude, height], separators=(",", ":")),
        ]
        if args.no_open_inspector:
            command.append("--no-open-inspector")
        try:
            subprocess.run(command, check=True)
        except subprocess.CalledProcessError as exc:
            parser.exit(exc.returncode, f"Erreur: échec du convertisseur 3D Tiles.\n")
        except OSError as exc:
            parser.error(f"Impossible de lancer le convertisseur : {exc}")

    tileset = output_dir / "tileset.json"
    if not tileset.is_file():
        parser.error(f"Le convertisseur n'a pas produit {tileset}.")
    print(f"Tileset créé : {tileset}")


if __name__ == "__main__":
    main()
