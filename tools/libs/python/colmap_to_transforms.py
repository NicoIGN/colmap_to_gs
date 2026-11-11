#!/usr/bin/env python3

"""
Génère un transforms.json compatible avec Nerfstudio directement depuis
un modèle COLMAP binaire.

Entrées COLMAP :
    cameras.bin
    images.bin

Convention COLMAP :
    - extrinsèques world-to-camera ;
    - caméra : X vers la droite, Y vers le bas, Z vers l'avant.

Convention transforms.json / OpenGL / Nerfstudio :
    - matrices camera-to-world ;
    - caméra : X vers la droite, Y vers le haut, Z vers l'arrière.

La conversion conserve le repère mondial COLMAP. Seuls les axes locaux des
caméras Y et Z sont inversés. sparse_pc.ply doit donc rester exprimé dans le
repère mondial COLMAP.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import struct
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


# ============================================================================
# COLMAP CAMERA MODELS
# ============================================================================

@dataclass(frozen=True)
class CameraModel:
    model_id: int
    name: str
    num_params: int


CAMERA_MODELS = {
    0: CameraModel(0, "SIMPLE_PINHOLE", 3),
    1: CameraModel(1, "PINHOLE", 4),
    2: CameraModel(2, "SIMPLE_RADIAL", 4),
    3: CameraModel(3, "RADIAL", 5),
    4: CameraModel(4, "OPENCV", 8),
    5: CameraModel(5, "OPENCV_FISHEYE", 8),
    6: CameraModel(6, "FULL_OPENCV", 12),
    7: CameraModel(7, "FOV", 5),
    8: CameraModel(8, "SIMPLE_RADIAL_FISHEYE", 4),
    9: CameraModel(9, "RADIAL_FISHEYE", 5),
    10: CameraModel(10, "THIN_PRISM_FISHEYE", 12),
}


# ============================================================================
# DATA TYPES
# ============================================================================

@dataclass
class Camera:
    camera_id: int
    model: CameraModel
    width: int
    height: int
    params: tuple[float, ...]


@dataclass
class ImagePose:
    image_id: int
    qvec: tuple[float, float, float, float]
    tvec: tuple[float, float, float]
    camera_id: int
    name: str


# ============================================================================
# BINARY HELPERS
# ============================================================================

def read_exact(file: BinaryIO, size: int) -> bytes:
    data = file.read(size)
    if len(data) != size:
        raise EOFError(
            f"Fichier COLMAP tronqué : attendu {size} octets, "
            f"reçu {len(data)}"
        )
    return data


def read_struct(file: BinaryIO, fmt: str):
    binary_format = "<" + fmt
    size = struct.calcsize(binary_format)
    return struct.unpack(binary_format, read_exact(file, size))


def read_c_string(file: BinaryIO) -> str:
    data = bytearray()

    while True:
        byte = file.read(1)

        if not byte:
            raise EOFError(
                "Fin de fichier rencontrée pendant la lecture "
                "d'une chaîne COLMAP"
            )

        if byte == b"\x00":
            break

        data.extend(byte)

    return data.decode("utf-8", errors="replace")


# ============================================================================
# COLMAP READERS
# ============================================================================

def read_cameras_binary(path: Path) -> dict[int, Camera]:
    path = Path(path)

    if not path.is_file():
        raise FileNotFoundError(f"cameras.bin introuvable : {path}")

    cameras: dict[int, Camera] = {}

    with path.open("rb") as file:
        (num_cameras,) = read_struct(file, "Q")

        for _ in range(num_cameras):
            camera_id, model_id, width, height = read_struct(
                file,
                "IiQQ",
            )

            model = CAMERA_MODELS.get(model_id)
            if model is None:
                raise RuntimeError(
                    f"Modèle caméra COLMAP inconnu : model_id={model_id}"
                )

            params = read_struct(
                file,
                "d" * model.num_params,
            )

            cameras[camera_id] = Camera(
                camera_id=camera_id,
                model=model,
                width=width,
                height=height,
                params=tuple(float(value) for value in params),
            )

    if not cameras:
        raise RuntimeError(f"Aucune caméra trouvée dans {path}")

    return cameras


def read_images_binary(path: Path) -> dict[int, ImagePose]:
    path = Path(path)

    if not path.is_file():
        raise FileNotFoundError(f"images.bin introuvable : {path}")

    images: dict[int, ImagePose] = {}

    with path.open("rb") as file:
        (num_images,) = read_struct(file, "Q")

        for _ in range(num_images):
            values = read_struct(file, "IdddddddI")

            image_id = int(values[0])
            qvec = tuple(float(value) for value in values[1:5])
            tvec = tuple(float(value) for value in values[5:8])
            camera_id = int(values[8])

            name = read_c_string(file)

            (num_points2d,) = read_struct(file, "Q")

            # Chaque observation est composée de :
            #   X: double
            #   Y: double
            #   POINT3D_ID: int64
            observation_size = struct.calcsize("<ddq")
            file.seek(num_points2d * observation_size, os.SEEK_CUR)

            images[image_id] = ImagePose(
                image_id=image_id,
                qvec=qvec,
                tvec=tvec,
                camera_id=camera_id,
                name=name,
            )

    if not images:
        raise RuntimeError(f"Aucune image trouvée dans {path}")

    return images


# ============================================================================
# MATRIX HELPERS
# ============================================================================

def normalize_quaternion(
    qvec: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    norm = math.sqrt(sum(value * value for value in qvec))

    if norm < 1e-15:
        raise ValueError("Quaternion COLMAP nul")

    return tuple(value / norm for value in qvec)


def quaternion_to_rotation_matrix(
    qvec: tuple[float, float, float, float],
) -> list[list[float]]:
    """
    Convertit le quaternion COLMAP (qw, qx, qy, qz) en matrice de rotation
    world-to-camera.
    """

    qw, qx, qy, qz = normalize_quaternion(qvec)

    return [
        [
            1.0 - 2.0 * (qy * qy + qz * qz),
            2.0 * (qx * qy - qw * qz),
            2.0 * (qx * qz + qw * qy),
        ],
        [
            2.0 * (qx * qy + qw * qz),
            1.0 - 2.0 * (qx * qx + qz * qz),
            2.0 * (qy * qz - qw * qx),
        ],
        [
            2.0 * (qx * qz - qw * qy),
            2.0 * (qy * qz + qw * qx),
            1.0 - 2.0 * (qx * qx + qy * qy),
        ],
    ]


def transpose_3x3(matrix: list[list[float]]) -> list[list[float]]:
    return [
        [matrix[column][row] for column in range(3)]
        for row in range(3)
    ]


def matrix_vector_product(
    matrix: list[list[float]],
    vector: tuple[float, float, float],
) -> list[float]:
    return [
        sum(matrix[row][column] * vector[column] for column in range(3))
        for row in range(3)
    ]


def colmap_pose_to_nerfstudio_matrix(
    image: ImagePose,
) -> list[list[float]]:
    """
    COLMAP stocke :

        x_camera = R_world_to_camera * x_world + t

    Le centre caméra dans le monde est donc :

        C = -R^T * t

    La rotation camera-to-world est R^T. Pour passer de la convention caméra
    COLMAP à OpenGL/Nerfstudio, les colonnes Y et Z sont inversées.
    """

    rotation_world_to_camera = quaternion_to_rotation_matrix(image.qvec)
    rotation_camera_to_world = transpose_3x3(
        rotation_world_to_camera
    )

    rotated_translation = matrix_vector_product(
        rotation_camera_to_world,
        image.tvec,
    )
    camera_center = [-value for value in rotated_translation]

    # R_c2w @ diag(1, -1, -1)
    rotation_opengl = [
        [
            rotation_camera_to_world[row][0],
            -rotation_camera_to_world[row][1],
            -rotation_camera_to_world[row][2],
        ]
        for row in range(3)
    ]

    matrix = [
        [
            rotation_opengl[0][0],
            rotation_opengl[0][1],
            rotation_opengl[0][2],
            camera_center[0],
        ],
        [
            rotation_opengl[1][0],
            rotation_opengl[1][1],
            rotation_opengl[1][2],
            camera_center[1],
        ],
        [
            rotation_opengl[2][0],
            rotation_opengl[2][1],
            rotation_opengl[2][2],
            camera_center[2],
        ],
        [0.0, 0.0, 0.0, 1.0],
    ]

    ensure_finite_matrix(matrix, image.name)
    return matrix


def ensure_finite_matrix(
    matrix: list[list[float]],
    image_name: str,
) -> None:
    for row in matrix:
        for value in row:
            if not math.isfinite(value):
                raise ValueError(
                    f"Pose non finie pour l'image {image_name}"
                )


# ============================================================================
# CAMERA CONVERSION
# ============================================================================

def camera_to_nerfstudio(camera: Camera) -> dict:
    """
    Convertit les paramètres caméra COLMAP vers les champs attendus dans
    transforms.json.

    Les modèles pinhole/radial/OpenCV classiques sont représentés comme
    OPENCV. Les modèles fisheye sont représentés comme OPENCV_FISHEYE.
    """

    model = camera.model.name
    params = camera.params

    intrinsics = {
        "w": int(camera.width),
        "h": int(camera.height),
    }

    if model == "SIMPLE_PINHOLE":
        focal, cx, cy = params

        intrinsics.update({
            "camera_model": "OPENCV",
            "fl_x": focal,
            "fl_y": focal,
            "cx": cx,
            "cy": cy,
            "k1": 0.0,
            "k2": 0.0,
            "k3": 0.0,
            "k4": 0.0,
            "p1": 0.0,
            "p2": 0.0,
        })

    elif model == "PINHOLE":
        fx, fy, cx, cy = params

        intrinsics.update({
            "camera_model": "OPENCV",
            "fl_x": fx,
            "fl_y": fy,
            "cx": cx,
            "cy": cy,
            "k1": 0.0,
            "k2": 0.0,
            "k3": 0.0,
            "k4": 0.0,
            "p1": 0.0,
            "p2": 0.0,
        })

    elif model == "SIMPLE_RADIAL":
        focal, cx, cy, k1 = params

        intrinsics.update({
            "camera_model": "OPENCV",
            "fl_x": focal,
            "fl_y": focal,
            "cx": cx,
            "cy": cy,
            "k1": k1,
            "k2": 0.0,
            "k3": 0.0,
            "k4": 0.0,
            "p1": 0.0,
            "p2": 0.0,
        })

    elif model == "RADIAL":
        focal, cx, cy, k1, k2 = params

        intrinsics.update({
            "camera_model": "OPENCV",
            "fl_x": focal,
            "fl_y": focal,
            "cx": cx,
            "cy": cy,
            "k1": k1,
            "k2": k2,
            "k3": 0.0,
            "k4": 0.0,
            "p1": 0.0,
            "p2": 0.0,
        })

    elif model == "OPENCV":
        fx, fy, cx, cy, k1, k2, p1, p2 = params

        intrinsics.update({
            "camera_model": "OPENCV",
            "fl_x": fx,
            "fl_y": fy,
            "cx": cx,
            "cy": cy,
            "k1": k1,
            "k2": k2,
            "k3": 0.0,
            "k4": 0.0,
            "p1": p1,
            "p2": p2,
        })

    elif model == "OPENCV_FISHEYE":
        fx, fy, cx, cy, k1, k2, k3, k4 = params

        intrinsics.update({
            "camera_model": "OPENCV_FISHEYE",
            "fl_x": fx,
            "fl_y": fy,
            "cx": cx,
            "cy": cy,
            "k1": k1,
            "k2": k2,
            "k3": k3,
            "k4": k4,
            "p1": 0.0,
            "p2": 0.0,
        })

    elif model == "SIMPLE_RADIAL_FISHEYE":
        focal, cx, cy, k1 = params

        intrinsics.update({
            "camera_model": "OPENCV_FISHEYE",
            "fl_x": focal,
            "fl_y": focal,
            "cx": cx,
            "cy": cy,
            "k1": k1,
            "k2": 0.0,
            "k3": 0.0,
            "k4": 0.0,
            "p1": 0.0,
            "p2": 0.0,
        })

    elif model == "RADIAL_FISHEYE":
        focal, cx, cy, k1, k2 = params

        intrinsics.update({
            "camera_model": "OPENCV_FISHEYE",
            "fl_x": focal,
            "fl_y": focal,
            "cx": cx,
            "cy": cy,
            "k1": k1,
            "k2": k2,
            "k3": 0.0,
            "k4": 0.0,
            "p1": 0.0,
            "p2": 0.0,
        })

    elif model == "FULL_OPENCV":
        raise RuntimeError(
            "Le modèle COLMAP FULL_OPENCV ne peut pas être représenté "
            "exactement par le modèle OPENCV de Nerfstudio. Utilise plutôt "
            "PINHOLE ou OPENCV dans le pipeline COLMAP, ou rectifie les "
            "images avant l'entraînement."
        )

    elif model in {"FOV", "THIN_PRISM_FISHEYE"}:
        raise RuntimeError(
            f"Le modèle COLMAP {model} n'est pas pris en charge par ce "
            "générateur Nerfstudio. Rectifie les images ou utilise un "
            "modèle PINHOLE/OPENCV."
        )

    else:
        raise RuntimeError(
            f"Modèle caméra COLMAP non pris en charge : {model}"
        )

    fl_x = float(intrinsics["fl_x"])
    fl_y = float(intrinsics["fl_y"])

    if fl_x <= 0.0 or fl_y <= 0.0:
        raise ValueError(
            f"Focale invalide pour camera_id={camera.camera_id}: "
            f"fl_x={fl_x}, fl_y={fl_y}"
        )

    intrinsics["camera_angle_x"] = 2.0 * math.atan(
        camera.width / (2.0 * fl_x)
    )
    intrinsics["camera_angle_y"] = 2.0 * math.atan(
        camera.height / (2.0 * fl_y)
    )

    return intrinsics


# ============================================================================
# PATH AND OUTPUT HELPERS
# ============================================================================

def normalize_image_name(name: str) -> str:
    """
    Normalise les séparateurs sans autoriser un chemin absolu ou une remontée
    hors du dossier images.
    """

    normalized = name.replace("\\", "/")
    path = Path(normalized)

    if path.is_absolute():
        raise ValueError(
            f"Chemin d'image COLMAP absolu interdit : {name}"
        )

    if ".." in path.parts:
        raise ValueError(
            f"Chemin d'image COLMAP invalide : {name}"
        )

    return path.as_posix()


def make_file_path(
    image_name: str,
    image_prefix: str,
) -> str:
    image_name = normalize_image_name(image_name)
    prefix = image_prefix.strip().strip("/")

    if prefix:
        return f"./{prefix}/{image_name}"

    return f"./{image_name}"


def validate_image_file(
    image: ImagePose,
    images_dir: Path,
) -> None:
    relative_name = normalize_image_name(image.name)
    image_path = images_dir / relative_name

    if not image_path.is_file():
        raise FileNotFoundError(
            f"Image référencée par COLMAP introuvable : {image_path}"
        )


def intrinsics_equal(left: dict, right: dict) -> bool:
    if left.keys() != right.keys():
        return False

    for key in left:
        left_value = left[key]
        right_value = right[key]

        if isinstance(left_value, float):
            if not math.isclose(
                left_value,
                float(right_value),
                rel_tol=1e-12,
                abs_tol=1e-12,
            ):
                return False
        elif left_value != right_value:
            return False

    return True


def write_json_atomic(path: Path, data: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )

    try:
        with os.fdopen(
            file_descriptor,
            "w",
            encoding="utf-8",
        ) as file:
            json.dump(
                data,
                file,
                indent=2,
                ensure_ascii=False,
                allow_nan=False,
            )
            file.write("\n")

        os.replace(temporary_name, path)

    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


# ============================================================================
# TRANSFORMS GENERATION
# ============================================================================

def generate_transforms(
    colmap_model: Path,
    images_dir: Path,
    output: Path,
    image_prefix: str,
    ply_file_path: str | None,
    validate_images: bool,
) -> dict:
    colmap_model = Path(colmap_model).resolve()
    images_dir = Path(images_dir).resolve()
    output = Path(output).resolve()

    cameras = read_cameras_binary(
        colmap_model / "cameras.bin"
    )
    images = read_images_binary(
        colmap_model / "images.bin"
    )

    camera_intrinsics = {
        camera_id: camera_to_nerfstudio(camera)
        for camera_id, camera in cameras.items()
    }

    used_camera_ids = {
        image.camera_id
        for image in images.values()
    }

    missing_camera_ids = used_camera_ids - cameras.keys()
    if missing_camera_ids:
        raise RuntimeError(
            "Les images COLMAP référencent des caméras absentes : "
            + ", ".join(
                str(camera_id)
                for camera_id in sorted(missing_camera_ids)
            )
        )

    # Si toutes les images utilisent exactement les mêmes intrinsèques,
    # celles-ci sont placées à la racine du JSON. Sinon, chaque frame reçoit
    # ses propres paramètres caméra.
    first_camera_id = min(used_camera_ids)
    common_intrinsics = camera_intrinsics[first_camera_id]

    shared_intrinsics = all(
        intrinsics_equal(
            common_intrinsics,
            camera_intrinsics[camera_id],
        )
        for camera_id in used_camera_ids
    )

    frames = []

    for image in sorted(
        images.values(),
        key=lambda item: item.image_id,
    ):
        if validate_images:
            validate_image_file(image, images_dir)

        frame = {
            "file_path": make_file_path(
                image.name,
                image_prefix,
            ),
            "transform_matrix": (
                colmap_pose_to_nerfstudio_matrix(image)
            ),
            "colmap_im_id": int(image.image_id),
        }

        if not shared_intrinsics:
            frame.update(
                camera_intrinsics[image.camera_id]
            )

        frames.append(frame)

    transforms = {
        "frames": frames,

        # Aucun recentrage ni changement d'échelle n'est appliqué ici.
        # Le PLY et les poses restent dans le repère mondial COLMAP.
        "applied_transform": [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0],
        ],
        "applied_scale": 1.0,
    }

    if shared_intrinsics:
        transforms.update(common_intrinsics)

    if ply_file_path:
        transforms["ply_file_path"] = ply_file_path

    write_json_atomic(output, transforms)

    return {
        "num_images": len(images),
        "num_cameras": len(used_camera_ids),
        "shared_intrinsics": shared_intrinsics,
        "output": output,
    }


# ============================================================================
# CLI
# ============================================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Génère un transforms.json Nerfstudio depuis "
            "un modèle COLMAP binaire."
        )
    )

    parser.add_argument(
        "--colmap-model",
        required=True,
        type=Path,
        help=(
            "Dossier contenant cameras.bin et images.bin, "
            "par exemple colmap/sparse/0."
        ),
    )

    parser.add_argument(
        "--images-dir",
        required=True,
        type=Path,
        help=(
            "Dossier réel contenant les images, utilisé pour "
            "valider les chemins COLMAP."
        ),
    )

    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Chemin du transforms.json à générer.",
    )

    parser.add_argument(
        "--image-prefix",
        default="images",
        help=(
            "Préfixe relatif écrit devant les noms d'images "
            "dans transforms.json. Défaut : images."
        ),
    )

    parser.add_argument(
        "--ply-file-path",
        default="sparse_pc.ply",
        help=(
            "Chemin relatif du PLY écrit dans transforms.json. "
            "Utiliser une chaîne vide pour ne pas écrire ce champ."
        ),
    )

    parser.add_argument(
        "--no-validate-images",
        action="store_true",
        help=(
            "Ne vérifie pas que chaque image enregistrée par "
            "COLMAP existe dans --images-dir."
        ),
    )

    return parser


def main() -> int:
    args = build_parser().parse_args()

    if not args.colmap_model.is_dir():
        raise FileNotFoundError(
            f"Modèle COLMAP introuvable : {args.colmap_model}"
        )

    if not args.images_dir.is_dir():
        raise FileNotFoundError(
            f"Dossier images introuvable : {args.images_dir}"
        )

    result = generate_transforms(
        colmap_model=args.colmap_model,
        images_dir=args.images_dir,
        output=args.output,
        image_prefix=args.image_prefix,
        ply_file_path=args.ply_file_path or None,
        validate_images=not args.no_validate_images,
    )

    print(
        "[OK] transforms.json généré : "
        f"{result['output']}"
    )
    print(
        "[INFO] Images enregistrées  : "
        f"{result['num_images']}"
    )
    print(
        "[INFO] Caméras utilisées    : "
        f"{result['num_cameras']}"
    )
    print(
        "[INFO] Intrinsèques globales: "
        f"{result['shared_intrinsics']}"
    )

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            f"[FATAL] {exc}",
            file=sys.stderr,
            flush=True,
        )
        raise SystemExit(1)
