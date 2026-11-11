#!/usr/bin/env python3
"""Convert IGNF CON camera orientations and a colored LAS/LAZ into the
orientation JSON + PLY inputs of tools/images_to_colmap (COLMAP_METHOD=orientations)."""

import argparse
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image
from scipy.spatial.transform import Rotation

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "images_to_colmap"))
from las_to_ply import convert as convert_las  # noqa: E402

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}


def _float(node, path, source):
    text = node.findtext(path)
    if text is None:
        raise ValueError(f"{source}: balise {path} absente")
    return float(text)


def parse_con(path):
    """Read one IGNF conic orientation (.CON XML) as an OpenCV world-to-camera pose."""
    root = ET.parse(path).getroot()
    extr = root.find("geometry/extrinseque")
    sensor = root.find("geometry/intrinseque/sensor")
    if extr is None or sensor is None:
        raise ValueError(f"{path}: geometry/extrinseque ou intrinseque/sensor absent")
    eucl, sommet, rotation = extr.find("systeme/euclidien"), extr.find("sommet"), extr.find("rotation")
    if eucl is None or sommet is None or rotation is None:
        raise ValueError(f"{path}: extrinsèque incomplet")
    center = np.array([
        _float(eucl, "x", path) + _float(sommet, "easting", path),
        _float(eucl, "y", path) + _float(sommet, "northing", path),
        _float(sommet, "altitude", path),
    ])
    matrix = np.array([[_float(rotation, f"mat3d/{row}/pt3d/{axis}", path) for axis in "xyz"]
                       for row in ("l1", "l2", "l3")])
    image2ground = str(rotation.findtext("Image2Ground", "")).strip().lower() == "true"
    world_to_camera = matrix.T if image2ground else matrix
    if (not np.allclose(world_to_camera @ world_to_camera.T, np.eye(3), atol=1e-6)
            or np.linalg.det(world_to_camera) <= 0):
        raise ValueError(f"{path}: rotation non orthonormée directe")
    focal = _float(sensor, "ppa/focale", path)
    calibration = (int(_float(sensor, "image_size/width", path)),
                   int(_float(sensor, "image_size/height", path)),
                   focal, focal, _float(sensor, "ppa/c", path), _float(sensor, "ppa/l", path))
    distortion = sensor.find("distortion")
    if distortion is not None:
        terms = [float(distortion.findtext(key) or 0) for key in ("r3", "r5", "r7")]
        if any(terms):
            raise ValueError(f"{path}: distorsion radiale r3/r5/r7 non nulle, non convertible en PINHOLE")
    systematism = sensor.find("transfo2d/tr2delem")
    shift_px = 0.0
    if systematism is not None:
        kind = systematism.attrib.get("Type")
        if kind != "systematismeCylindriqueTopAero":
            raise ValueError(f"{path}: transfo2d {kind!r} non supportée")
        c0 = float(systematism.attrib.get("C0", 0))
        reach = max(abs(c0), abs(calibration[0] - c0))
        shift_px = reach * max(abs(float(systematism.attrib.get("S1", 0))),
                               abs(float(systematism.attrib.get("S2", 0))))
    return {"center": center, "rotation": world_to_camera,
            "calibration": calibration, "systematism_px": shift_px}


def find_pairs(images_dir):
    images, cons = {}, {}
    for path in sorted(images_dir.rglob("*")):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        table = images if suffix in IMAGE_EXTS else cons if suffix == ".con" else None
        if table is None:
            continue
        if path.stem in table:
            raise ValueError(f"Nom ambigu ({path.stem}) : {table[path.stem]} et {path}")
        table[path.stem] = path
    missing = sorted(set(images) - set(cons))
    pairs = [(images[stem], cons[stem]) for stem in sorted(set(images) & set(cons))]
    return pairs, missing


def parse_triplet(text, name):
    values = [float(value) for value in text.split(",")]
    if len(values) != 3 or not np.isfinite(values).all():
        raise ValueError(f"{name} attend X,Y,Z")
    return values


def convert(images_dir, laz, output_dir, stride=1, origin="auto", bbox=None,
            crs=None, max_systematism_px=0.5):
    output_dir.mkdir(parents=True, exist_ok=True)
    orientations_path, points_path = output_dir / "orientations.json", output_dir / "points.ply"
    for path in (orientations_path, points_path):
        if path.exists():
            raise ValueError(f"Sortie déjà présente : {path}")
    pairs, missing = find_pairs(images_dir)
    if not pairs:
        raise ValueError(f"Aucune paire image/.CON dans {images_dir}")
    for stem in missing:
        print(f"[AVERTISSEMENT] .CON absent, image ignorée : {stem}", file=sys.stderr)
    poses = []
    for image_path, con_path in pairs:
        pose = parse_con(con_path)
        if pose["systematism_px"] > max_systematism_px:
            raise ValueError(
                f"{con_path}: systématisme cylindrique jusqu'à {pose['systematism_px']:.2f} px "
                f"> {max_systematism_px} px, non représentable en PINHOLE")
        with Image.open(image_path) as image:
            if image.size != pose["calibration"][:2]:
                raise ValueError(f"{image_path}: taille {image.size} ≠ .CON {pose['calibration'][:2]}")
        poses.append((image_path, pose))
    centers = np.array([pose["center"] for _, pose in poses])
    if origin == "auto":
        shift = np.round(centers.mean(axis=0))
    elif origin == "none":
        shift = np.zeros(3)
    else:
        shift = np.asarray(parse_triplet(origin, "--origin"))
    cameras, images = {}, []
    for image_path, pose in poses:
        camera_id = cameras.setdefault(pose["calibration"], len(cameras) + 1)
        rotation = pose["rotation"]
        quat = Rotation.from_matrix(rotation).as_quat()[[3, 0, 1, 2]]
        quat = quat if quat[0] >= 0 else -quat
        images.append({"name": image_path.relative_to(images_dir).as_posix(),
                       "camera_id": camera_id, "qvec": quat.tolist(),
                       "tvec": (-rotation @ (pose["center"] - shift)).tolist()})
    reference = {
        "description": "IGNF .CON + LAZ ; coordonnees source = coordonnees locales + origin",
        "origin": shift.tolist(), "crs": crs,
    }
    convert_las(laz, points_path, stride, origin=shift, bbox=bbox)
    orientations_path.write_text(json.dumps({
        "version": 1, "pose_convention": "opencv_world_to_camera", "reference": reference,
        "cameras": [{"id": camera_id, "model": "PINHOLE", "width": cal[0], "height": cal[1],
                     "params": list(cal[2:])} for cal, camera_id in cameras.items()],
        "images": images,
    }, indent=2) + "\n")
    return orientations_path, points_path, len(images), len(cameras), shift


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--images", type=Path, required=True,
                        help="Dossier des images et de leurs .CON (même nom de base, recherche récursive)")
    parser.add_argument("--laz", type=Path, required=True, help="Nuage LAS/LAZ coloré")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--stride", type=int, default=1, help="Garder un point LiDAR sur N")
    parser.add_argument("--bbox", help="xmin,ymin,xmax,ymax dans le CRS du LAZ, avant --stride")
    parser.add_argument("--origin", default="auto",
                        help="auto (moyenne arrondie des sommets), none, ou X,Y,Z soustrait")
    parser.add_argument("--crs", help="CRS source, seulement consigné (ex. EPSG:2154)")
    parser.add_argument("--max-systematism-px", type=float, default=0.5)
    args = parser.parse_args()
    try:
        if args.stride < 1:
            raise ValueError("--stride doit être >= 1.")
        bbox = None
        if args.bbox:
            bbox = [float(value) for value in args.bbox.split(",")]
            if len(bbox) != 4 or bbox[0] >= bbox[2] or bbox[1] >= bbox[3]:
                raise ValueError("--bbox attend xmin,ymin,xmax,ymax")
        orientations, points, count, cameras, shift = convert(
            args.images.resolve(), args.laz, args.output_dir, args.stride, args.origin,
            bbox, args.crs, args.max_systematism_px)
    except (OSError, ValueError, ET.ParseError) as error:
        sys.exit(f"[ERREUR] Conversion .CON/LAZ : {error}")
    print(f"[OK] {count} images, {cameras} caméra(s), origine {shift.tolist()}")
    print(f'ORIENTATIONS_JSON="{orientations.resolve()}"')
    print(f'LIDAR_PLY="{points.resolve()}"')


if __name__ == "__main__":
    main()
