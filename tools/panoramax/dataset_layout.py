"""Lecture des datasets Panoramax.

Deux layouts sont acceptés : à plat (images/, metadata/) ou par collection
(<collection>/images/, <collection>/metadata/), tel que produit par download.py.
"""

import json
import os
import re
from pathlib import Path

XMP_HEADER = b"http://ns.adobe.com/xap/1.0/\x00"
GPANO_PROJECTION = re.compile(r'GPano:ProjectionType\s*(?:=\s*"([^"]*)"|>\s*([^<\s]+)\s*<)')
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


def _subdirs(dataset, name, partner):
    """Dossier racine `name`, sinon dossiers `<coll>/name` ayant aussi `<coll>/partner` (exclut sfm/images)."""
    dataset = Path(dataset)
    if (dataset / name).is_dir():
        return [dataset / name]
    return sorted(
        path / name for path in dataset.iterdir()
        if (path / name).is_dir() and (path / partner).is_dir()
    )


def image_files(dataset):
    return sorted(
        path for folder in _subdirs(dataset, "images", "metadata")
        for path in folder.rglob("*") if path.is_file()
    )


def json_index(folder):
    """Associe chaque chemin relatif sans suffixe (ex. Spirula) à son fichier JSON."""
    folder = Path(folder)
    return {
        path.relative_to(folder).with_suffix("").as_posix(): path
        for path in sorted(folder.rglob("*.json")) if path.is_file()
    }


def exif_of(properties):
    """EXIF STAC : dict, ou chaîne JSON selon l'API ; renvoie toujours un dict."""
    exif = properties.get("exif") or {}
    if isinstance(exif, str):
        try:
            exif = json.loads(exif)
        except ValueError:
            return {}
    return exif if isinstance(exif, dict) else {}


def picture_type_of(feature):
    """Type STAC (equirectangular, flat...) ; sinon déduit de l'EXIF GPano ou du champ de vue 360.

    Le type peut être absent de la collection et n'apparaître que dans l'EXIF.
    """
    properties = feature.get("properties") or {}
    if properties.get("type") is not None:
        return properties["type"]
    for key, value in exif_of(properties).items():
        if key.split(":")[-1].split(".")[-1].casefold() == "projectiontype" and \
                str(value).strip().casefold() == "equirectangular":
            return "equirectangular"
    orientation = properties.get("pers:interior_orientation") or {}
    try:
        if float(orientation.get("field_of_view")) == 360:
            return "equirectangular"
    except (TypeError, ValueError):
        pass
    return None


def image_is_equirectangular(path):
    """Lit GPano:ProjectionType dans le XMP du JPEG, pour les photos dont le type n'est pas dans la collection."""
    with Path(path).open("rb") as stream:
        if stream.read(2) != b"\xff\xd8":
            return False
        while True:
            head = stream.read(4)
            if len(head) < 4 or head[0] != 0xFF or head[1] in (0xD9, 0xDA):
                return False
            length = int.from_bytes(head[2:4], "big")
            if length < 2:
                return False
            payload = stream.read(length - 2)
            if head[1] == 0xE1 and payload.startswith(XMP_HEADER):
                xml = payload[len(XMP_HEADER):].decode("utf-8", "replace")
                for match in GPANO_PROJECTION.finditer(xml):
                    value = (match.group(1) or match.group(2) or "").strip()
                    if value.casefold() == "equirectangular":
                        return True


def resolved_picture_type(feature, image=None):
    """Type STAC/EXIF de la collection, sinon XMP GPano de l'image elle-même."""
    kind = picture_type_of(feature)
    if kind is None and image is not None and image_is_equirectangular(image):
        return "equirectangular"
    return kind


def picture_type_matches(kind, wanted):
    """wanted : all, 360 (equirectangular) ou normal (tout le reste)."""
    if wanted == "all":
        return True
    if wanted == "360":
        return kind == "equirectangular"
    return kind != "equirectangular"


def metadata_index(dataset):
    index = {}
    for folder in _subdirs(dataset, "metadata", "images"):
        for name, path in json_index(folder).items():
            if name in index:
                raise ValueError(f"Métadonnées en double pour {name} : {index[name]} et {path}")
            index[name] = path
    return index
