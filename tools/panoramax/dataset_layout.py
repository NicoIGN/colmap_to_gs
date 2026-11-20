"""Lecture des datasets Panoramax.

Deux layouts sont acceptés : à plat (images/, metadata/) ou par collection
(<collection>/images/, <collection>/metadata/), tel que produit par download.py.
"""

import os
from pathlib import Path

SOF_MARKERS = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def jpeg_size(path):
    """Retourne (largeur, hauteur) lues dans l'en-tête SOF du JPEG."""
    with Path(path).open("rb") as stream:
        if stream.read(2) != b"\xff\xd8":
            raise ValueError("pas un JPEG")
        while True:
            byte = stream.read(1)
            if not byte:
                raise ValueError("en-tête SOF introuvable")
            if byte != b"\xff":
                continue
            marker = stream.read(1)
            while marker == b"\xff":
                marker = stream.read(1)
            if not marker:
                raise ValueError("en-tête SOF introuvable")
            code = marker[0]
            if code in (0x01, 0xD8) or 0xD0 <= code <= 0xD7:
                continue
            length_bytes = stream.read(2)
            if len(length_bytes) != 2:
                raise ValueError("segment JPEG tronqué")
            length = int.from_bytes(length_bytes, "big")
            if code in SOF_MARKERS:
                data = stream.read(5)
                if len(data) != 5:
                    raise ValueError("segment SOF tronqué")
                return int.from_bytes(data[3:5], "big"), int.from_bytes(data[1:3], "big")
            stream.seek(length - 2, os.SEEK_CUR)


def _subdirs(dataset, name):
    dataset = Path(dataset)
    if (dataset / name).is_dir():
        return [dataset / name]
    return sorted(path / name for path in dataset.iterdir() if (path / name).is_dir())


def image_files(dataset):
    return sorted(
        path for folder in _subdirs(dataset, "images") for path in folder.rglob("*") if path.is_file()
    )


def json_index(folder):
    """Associe chaque chemin relatif sans suffixe (ex. Spirula) à son fichier JSON."""
    folder = Path(folder)
    return {
        path.relative_to(folder).with_suffix("").as_posix(): path
        for path in sorted(folder.rglob("*.json")) if path.is_file()
    }


def metadata_index(dataset):
    index = {}
    for folder in _subdirs(dataset, "metadata"):
        for name, path in json_index(folder).items():
            if name in index:
                raise ValueError(f"Métadonnées en double pour {name} : {index[name]} et {path}")
            index[name] = path
    return index
