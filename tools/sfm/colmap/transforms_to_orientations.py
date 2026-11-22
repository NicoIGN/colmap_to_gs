#!/usr/bin/env python3
"""Convert explicit OpenGL camera-to-world transforms.json into orientation JSON."""

import argparse
import json
from pathlib import Path
import sys

import numpy as np
from scipy.spatial.transform import Rotation


def convert(source, output):
    data = json.loads(source.read_text())
    cameras, images = [], []
    for index, frame in enumerate(data["frames"], 1):
        calibration = {**data, **frame}
        model = calibration.get("camera_model", "OPENCV")
        if model not in ("OPENCV", "PINHOLE"):
            raise ValueError(f"Modèle caméra non supporté : {model}")
        params = [calibration[key] for key in ("fl_x", "fl_y", "cx", "cy")]
        if model == "OPENCV":
            params.extend(calibration.get(key, 0.0) for key in ("k1", "k2", "p1", "p2"))
        matrix = np.asarray(frame["transform_matrix"], dtype=float)
        if (matrix.shape != (4, 4) or not np.isfinite(matrix).all()
                or not np.allclose(matrix[3], [0, 0, 0, 1])
                or not np.allclose(matrix[:3, :3].T @ matrix[:3, :3], np.eye(3))
                or not np.isclose(np.linalg.det(matrix[:3, :3]), 1)):
            raise ValueError("Une pose rigide camera-to-world 4x4 est requise.")
        world_to_camera = np.linalg.inv(matrix @ np.diag([1, -1, -1, 1]))
        quat = Rotation.from_matrix(world_to_camera[:3, :3]).as_quat()[[3, 0, 1, 2]]
        name = frame["file_path"]
        if name.startswith("images/"):
            name = name[len("images/"):]
        cameras.append({"id": index, "model": model, "width": calibration["w"],
                        "height": calibration["h"], "params": params})
        images.append({"name": name, "camera_id": index, "qvec": quat.tolist(),
                       "tvec": world_to_camera[:3, 3].tolist()})
    if output.exists():
        raise ValueError(f"Sortie déjà présente : {output}")
    output.write_text(json.dumps({
        "version": 1, "pose_convention": "opencv_world_to_camera",
        "reference": data.get("reference"), "cameras": cameras, "images": images,
    }, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        convert(args.input, args.output)
    except (OSError, ValueError, KeyError, TypeError) as error:
        sys.exit(f"[ERREUR] Conversion des poses : {error}")
