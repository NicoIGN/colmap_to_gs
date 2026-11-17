#!/usr/bin/env python3
"""Compare two COLMAP models and, optionally, Spirula's verified image pairs."""

import argparse
from collections import Counter
from pathlib import Path
import struct
import sys

from colmap_split_blocks import (
    CAMERA_MODELS, read_cameras_binary, read_cameras_text,
    read_images_binary, read_images_text,
)


def load_model(folder):
    if (folder / "images.bin").is_file() and (folder / "cameras.bin").is_file():
        cameras = read_cameras_binary(folder / "cameras.bin")
        images = read_images_binary(folder / "images.bin")
    elif (folder / "images.txt").is_file() and (folder / "cameras.txt").is_file():
        cameras = read_cameras_text(folder / "cameras.txt")
        images = read_images_text(folder / "images.txt")
    else:
        raise ValueError(f"Caméras et images COLMAP absentes dans {folder}")
    by_name = {}
    for image in images.values():
        if image.name in by_name:
            raise ValueError(f"Nom d'image dupliqué dans {folder} : {image.name}")
        if image.camera_id not in cameras:
            raise ValueError(f"Caméra {image.camera_id} absente pour {image.name}")
        by_name[image.name] = image
    if not by_name:
        raise ValueError(f"Modèle sans images : {folder}")
    return cameras, by_name


def read_matches(path):
    """Read the pair table of Spirula VKMT v2-v5, without loading match arrays."""
    size = path.stat().st_size
    with path.open("rb") as stream:
        def read(n):
            data = stream.read(n)
            if len(data) != n:
                raise ValueError(f"Fichier de correspondances tronqué : {path}")
            return data

        magic, version, count = struct.unpack("<4sII", read(12))
        if magic != b"VKMT" or version not in (2, 3, 4, 5):
            raise ValueError(f"Format VKMT non pris en charge : {magic!r}, version {version}")
        if count > size // 8:
            raise ValueError("Nombre d'images VKMT invalide")
        names = []
        for _ in range(count):
            length = struct.unpack("<I", read(4))[0]
            if length > 1024 * 1024:
                raise ValueError("Nom d'image VKMT trop long")
            names.append(read(length).decode("utf-8"))
            read(4)
        pair_count = struct.unpack("<I", read(4))[0]
        if pair_count > (size - stream.tell()) // 16:
            raise ValueError("Nombre de paires VKMT invalide ou fichier incomplet")
        pairs = []
        for _ in range(pair_count):
            a, b, config, n = struct.unpack("<IIiI", read(16))
            if a >= count or b >= count or stream.tell() + n * 8 > size:
                raise ValueError("Paire VKMT invalide ou tronquée")
            pairs.append((a, b, config, n))
            stream.seek(n * 8, 1)
    return names, pairs, version


def feature_lookup(model_names, feature_names):
    """VKMT stores feature stems; require unique relative stems."""
    stems = {}
    for name in model_names:
        stem = str(Path(name).with_suffix(""))
        if stem in stems and stems[stem] != name:
            raise ValueError(f"Noms d'images ambigus pour le stem {stem}")
        stems[stem] = name
    resolved = []
    for name in feature_names:
        resolved.append(name if name in model_names else stems.get(name))
    return resolved


def analyze_matches(path, names_a, names_b):
    feature_names, pairs, version = read_matches(path)
    resolved = feature_lookup(names_a | names_b, feature_names)
    only_a, only_b = names_a - names_b, names_b - names_a
    cross = []
    for a, b, config, count in pairs:
        x, y = resolved[a], resolved[b]
        if x is None or y is None:
            continue
        # Images registered in both models cannot identify a model boundary.
        if (x in only_a and y in only_b) or (y in only_a and x in only_b):
            cross.append((x, y, config, count))
    return feature_names, resolved, pairs, cross, version


def report(model_a, model_b, matches=None):
    cameras_a, images_a = load_model(model_a)
    cameras_b, images_b = load_model(model_b)
    names_a, names_b = set(images_a), set(images_b)
    common = sorted(names_a & names_b)
    for label, folder, cameras, images in (
            ("A", model_a, cameras_a, images_a), ("B", model_b, cameras_b, images_b)):
        print(f"\nModèle {label} : {folder} ; {len(images)} images, {len(cameras)} caméras")
        counts = Counter(image.camera_id for image in images.values())
        for cid, camera in sorted(cameras.items()):
            model = CAMERA_MODELS[camera.model_id][0]
            params = ", ".join(f"{value:.5g}" for value in camera.params)
            print(f"  caméra {cid} : {counts[cid]} images, {camera.width}x{camera.height}, {model}, paramètres [{params}]")
    print(f"\nImages communes : {len(common)} ; uniquement A : {len(names_a - names_b)} ; uniquement B : {len(names_b - names_a)}")
    for name in common:
        a, b = images_a[name], images_b[name]
        tracked_a = sum(point >= 0 for point in a.point3D_ids)
        tracked_b = sum(point >= 0 for point in b.point3D_ids)
        xy_a = {tuple(xy) for xy, point in zip(a.xys, a.point3D_ids) if point >= 0}
        xy_b = {tuple(xy) for xy, point in zip(b.xys, b.point3D_ids) if point >= 0}
        print(f"  {name} : points 3D observés A={tracked_a}, B={tracked_b}, "
              f"coordonnées 2D exactes communes={len(xy_a & xy_b)}")
    if not common:
        print("  Aucune image commune : impossible d'aligner les modèles à partir de poses communes seules.")
    print("  Les ID de points et les coordonnées 3D ne sont pas directement comparables entre modèles indépendants.")
    if common:
        print("  Les coordonnées 2D sont comparées exactement : une différence de précision peut masquer une correspondance.")

    ordered = sorted(names_a | names_b)
    only_a, only_b = names_a - names_b, names_b - names_a
    transitions = [(a, b) for a, b in zip(ordered, ordered[1:])
                   if (a in only_a and b in only_b) or (a in only_b and b in only_a)]
    print(f"\nTransitions A/B dans l'ordre des noms : {len(transitions)}")
    for a, b in transitions:
        print(f"  {a} -> {b}")
    print("  Cet ordre est temporel seulement si les noms le sont ; les images non enregistrées ne sont pas incluses.")

    if matches is None:
        print("\nCorrespondances : non fournies. Les modèles seuls ne révèlent pas les paires testées ou rejetées.")
        return
    feature_names, resolved, pairs, cross, version = analyze_matches(matches, names_a, names_b)
    print(f"\nCorrespondances VKMT v{version} : {matches}")
    print(f"  {len(feature_names)} images, {len(pairs)} paires enregistrées, "
          f"{sum(name is not None for name in resolved)} images associées aux modèles")
    if any(name is None for name in resolved):
        print("  Des noms VKMT ne correspondent pas aux modèles ; ils peuvent aussi être des images non enregistrées.")
    print(f"  Paires entre images exclusives de A et B : {len(cross)}")
    print(f"  Configurations (code brut : nombre de paires) : {dict(sorted(Counter(p[2] for p in cross).items()))}")
    for a, b, config, count in sorted(cross, key=lambda p: p[3], reverse=True)[:20]:
        print(f"  {a} <-> {b} : {count} correspondances, config={config}")
    pair_index = {frozenset((a, b)): (config, count) for a, b, config, count in cross}
    for a, b in transitions:
        result = pair_index.get(frozenset((a, b)))
        if result is None:
            print(f"  Transition {a} -> {b} : paire absente du fichier")
        else:
            print(f"  Transition {a} -> {b} : {result[1]} correspondances, config={result[0]}")
    if not cross:
        print("  Aucune paire inter-modèles enregistrée : vérifier la sélection des paires et l'association des noms.")
    else:
        print("  Une paire enregistrée ne garantit pas une fusion ; son code et sa vérification dépendent de la version de Spirula.")
    print("  Les paires absentes du fichier ne permettent pas de distinguer non-sélection et rejet à la vérification.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model_a", type=Path, help="Premier dossier COLMAP, par ex. sfm/sparse/0")
    parser.add_argument("model_b", type=Path, help="Second dossier COLMAP, par ex. sfm/sparse/1")
    parser.add_argument("--matches", type=Path, help="Fichier Spirula matches.bin facultatif")
    args = parser.parse_args()
    try:
        report(args.model_a, args.model_b, args.matches)
    except (OSError, EOFError, ValueError, UnicodeError, struct.error) as error:
        parser.exit(1, f"[ERREUR] Diagnostic : {error}\n")


if __name__ == "__main__":
    main()
