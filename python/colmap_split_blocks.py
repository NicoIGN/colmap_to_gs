#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Découpage d'un modèle sparse COLMAP avec recouvrement contrôlé.

Dépendances :
    pip install numpy scipy tqdm

Fonctionnalités :
- Lecture COLMAP binaire ou texte.
- Nettoyage spatial optionnel des points isolés.
- Graphe de covisibilité pondéré par la distance entre caméras.
- Clustering spectral + réparation heuristique de connexité.
- Recouvrement plafonné par bloc et par image.
- Export COLMAP binaire et/ou texte.
- PLY coloré, points multi-blocs en jaune.
- Barres de progression sur les principales boucles.

Important :
- La réparation de connexité est heuristique, pas une garantie.
- Le nombre effectif de blocs et leur connexité sont contrôlés et annoncés.
- Les blocs conservent les IDs et le repère du modèle original.
- Les photos ne sont pas copiées.
"""

import argparse
import colorsys
import json
import math
import struct
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import scipy.sparse as sp
from scipy.cluster.vq import kmeans2
from scipy.sparse.csgraph import connected_components
from scipy.sparse.linalg import eigsh
from scipy.spatial import cKDTree
from tqdm import tqdm


CAMERA_MODELS = {
    0: ("SIMPLE_PINHOLE", 3),
    1: ("PINHOLE", 4),
    2: ("SIMPLE_RADIAL", 4),
    3: ("RADIAL", 5),
    4: ("OPENCV", 8),
    5: ("OPENCV_FISHEYE", 8),
    6: ("FULL_OPENCV", 12),
    7: ("FOV", 5),
    8: ("SIMPLE_RADIAL_FISHEYE", 4),
    9: ("RADIAL_FISHEYE", 5),
    10: ("THIN_PRISM_FISHEYE", 12),
}
CAMERA_MODEL_NAME_TO_ID = {
    name: model_id
    for model_id, (name, _) in CAMERA_MODELS.items()
}

OBS_DTYPE = np.dtype([
    ("x", "<f8"),
    ("y", "<f8"),
    ("pid", "<i8"),
])
POINT_HEADER = struct.Struct("<QdddBBBdQ")


@dataclass
class Camera:
    camera_id: int
    model_id: int
    width: int
    height: int
    params: np.ndarray


@dataclass
class ImageRec:
    image_id: int
    qvec: np.ndarray
    tvec: np.ndarray
    camera_id: int
    name: str
    xys: np.ndarray
    point3D_ids: np.ndarray


@dataclass
class Point3D:
    point3D_id: int
    xyz: np.ndarray
    rgb: np.ndarray
    error: float
    track: list


def info(message):
    tqdm.write(f"[INFO] {message}")


def warn(message):
    tqdm.write(f"[WARN] {message}")


def progress(iterable, desc, total=None, unit="item"):
    return tqdm(
        iterable,
        total=total,
        desc=desc,
        unit=unit,
        mininterval=0.3,
    )


# ---------------------------------------------------------------------------
# Lecture COLMAP
# ---------------------------------------------------------------------------

def read_exact(f, size):
    data = f.read(size)
    if len(data) != size:
        raise EOFError(
            f"Fichier tronqué : {f.name}, position={f.tell()}"
        )
    return data


def unpack(f, fmt):
    return struct.unpack(fmt, read_exact(f, struct.calcsize(fmt)))


def read_cameras_binary(path):
    cameras = {}

    with open(path, "rb") as f:
        count = unpack(f, "<Q")[0]

        for _ in progress(
            range(count), "Reading cameras.bin", unit="cam"
        ):
            cid, model_id, width, height = unpack(f, "<IiQQ")

            if model_id not in CAMERA_MODELS:
                raise ValueError(
                    f"Modèle caméra non supporté : {model_id}"
                )

            num_params = CAMERA_MODELS[model_id][1]
            params = np.frombuffer(
                read_exact(f, 8 * num_params),
                dtype="<f8",
            ).copy()

            cameras[cid] = Camera(
                cid, model_id, width, height, params
            )

    return cameras


def read_images_binary(path):
    images = {}

    with open(path, "rb") as f:
        count = unpack(f, "<Q")[0]

        for _ in progress(
            range(count), "Reading images.bin", unit="image"
        ):
            record = unpack(f, "<I7dI")
            iid = record[0]

            name_bytes = bytearray()
            while True:
                char = read_exact(f, 1)
                if char == b"\x00":
                    break
                name_bytes.extend(char)

            num_points = unpack(f, "<Q")[0]

            records = np.frombuffer(
                read_exact(f, num_points * OBS_DTYPE.itemsize),
                dtype=OBS_DTYPE,
            )

            xys = np.empty((num_points, 2), dtype=np.float64)
            xys[:, 0] = records["x"]
            xys[:, 1] = records["y"]

            images[iid] = ImageRec(
                image_id=iid,
                qvec=np.array(record[1:5], dtype=np.float64),
                tvec=np.array(record[5:8], dtype=np.float64),
                camera_id=record[8],
                name=name_bytes.decode("utf-8"),
                xys=xys,
                point3D_ids=records["pid"].copy(),
            )

    return images


def read_points3d_binary(path):
    points = {}

    with open(path, "rb") as f:
        count = unpack(f, "<Q")[0]

        for _ in progress(
            range(count), "Reading points3D.bin", unit="pt"
        ):
            record = POINT_HEADER.unpack(
                read_exact(f, POINT_HEADER.size)
            )
            pid = record[0]
            track_length = record[-1]

            # Lecture groupée de la track.
            raw_track = read_exact(f, track_length * 8)
            track = list(struct.iter_unpack("<II", raw_track))

            points[pid] = Point3D(
                point3D_id=pid,
                xyz=np.array(record[1:4], dtype=np.float64),
                rgb=np.array(record[4:7], dtype=np.uint8),
                error=record[7],
                track=track,
            )

    return points


def text_data_lines(f):
    for line in f:
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            yield stripped


def read_cameras_text(path):
    cameras = {}

    with open(path, encoding="utf-8") as f:
        for line in progress(
            text_data_lines(f), "Reading cameras.txt", unit="cam"
        ):
            tokens = line.split()
            cid = int(tokens[0])
            model_name = tokens[1]

            if model_name not in CAMERA_MODEL_NAME_TO_ID:
                raise ValueError(
                    f"Modèle caméra non supporté : {model_name}"
                )

            model_id = CAMERA_MODEL_NAME_TO_ID[model_name]
            params = np.array(tokens[4:], dtype=np.float64)

            if len(params) != CAMERA_MODELS[model_id][1]:
                raise ValueError(
                    f"Nombre de paramètres invalide pour caméra {cid}"
                )

            cameras[cid] = Camera(
                cid,
                model_id,
                int(tokens[2]),
                int(tokens[3]),
                params,
            )

    return cameras


def read_images_text(path):
    images = {}

    with open(path, encoding="utf-8") as f:
        with tqdm(
            desc="Reading images.txt",
            unit="image",
            mininterval=0.3,
        ) as bar:
            while True:
                line = f.readline()
                if line == "":
                    break

                if not line.strip() or line.lstrip().startswith("#"):
                    continue

                tokens = line.strip().split(maxsplit=9)
                if len(tokens) != 10:
                    raise ValueError(
                        f"En-tête image invalide dans {path}"
                    )

                iid = int(tokens[0])

                # Cette ligne peut être vide : ne pas la supprimer.
                observations_line = f.readline()
                if observations_line == "":
                    raise EOFError(
                        f"Ligne POINTS2D manquante pour image {iid}"
                    )

                observations = observations_line.split()
                if len(observations) % 3:
                    raise ValueError(
                        f"Triplets POINTS2D invalides pour image {iid}"
                    )

                count = len(observations) // 3
                xys = np.empty((count, 2), dtype=np.float64)
                pids = np.empty(count, dtype=np.int64)

                # Les IDs restent des entiers, sans passage par float.
                for index in range(count):
                    offset = 3 * index
                    xys[index] = (
                        float(observations[offset]),
                        float(observations[offset + 1]),
                    )
                    pids[index] = int(observations[offset + 2])

                images[iid] = ImageRec(
                    iid,
                    np.array(tokens[1:5], dtype=np.float64),
                    np.array(tokens[5:8], dtype=np.float64),
                    int(tokens[8]),
                    tokens[9],
                    xys,
                    pids,
                )
                bar.update(1)

    return images


def read_points3d_text(path):
    points = {}

    with open(path, encoding="utf-8") as f:
        for line in progress(
            text_data_lines(f), "Reading points3D.txt", unit="pt"
        ):
            tokens = line.split()

            if len(tokens) < 8 or (len(tokens) - 8) % 2:
                raise ValueError(
                    f"Point ou track invalide dans {path}"
                )

            pid = int(tokens[0])
            track = [
                (int(tokens[i]), int(tokens[i + 1]))
                for i in range(8, len(tokens), 2)
            ]

            points[pid] = Point3D(
                pid,
                np.array(tokens[1:4], dtype=np.float64),
                np.array(tokens[4:7], dtype=np.uint8),
                float(tokens[7]),
                track,
            )

    return points


def load_colmap_model(folder):
    names = ("cameras", "images", "points3D")

    if all((folder / f"{name}.bin").is_file() for name in names):
        return (
            read_cameras_binary(folder / "cameras.bin"),
            read_images_binary(folder / "images.bin"),
            read_points3d_binary(folder / "points3D.bin"),
            "bin",
        )

    if all((folder / f"{name}.txt").is_file() for name in names):
        return (
            read_cameras_text(folder / "cameras.txt"),
            read_images_text(folder / "images.txt"),
            read_points3d_text(folder / "points3D.txt"),
            "txt",
        )

    raise FileNotFoundError(
        f"Modèle COLMAP incomplet dans {folder}"
    )


# ---------------------------------------------------------------------------
# Nettoyage spatial optionnel
# ---------------------------------------------------------------------------

def remove_isolated_points(
    images,
    points3D,
    radius,
    min_neighbors=3,
    chunk_size=50000,
    workers=1,
):
    """
    Supprime les points ayant moins de min_neighbors AUTRES points
    à une distance <= radius.

    Test global avant partitionnement, effectué en une seule passe :
    pas de suppression récursive des nouveaux points isolés.

    Supprime également les coordonnées non finies.
    Met à -1 les références aux points supprimés, sans changer POINT2D_IDX.
    """
    count = len(points3D)
    if count == 0:
        return

    ids = np.empty(count, dtype=np.uint64)
    xyz = np.empty((count, 3), dtype=np.float64)

    for index, (pid, point) in enumerate(progress(
        points3D.items(),
        "Preparing isolation filter",
        total=count,
        unit="pt",
    )):
        ids[index] = pid
        xyz[index] = point.xyz

    finite = np.isfinite(xyz).all(axis=1)
    finite_indices = np.flatnonzero(finite)
    keep = np.zeros(count, dtype=bool)

    if len(finite_indices):
        valid_xyz = np.ascontiguousarray(xyz[finite])

        # cKDTree ne fournit pas de progression interne fiable.
        info(
            f"Building spatial KD-tree for {len(valid_xyz):,} points "
            "(pas de pourcentage disponible)..."
        )
        tree = cKDTree(valid_xyz)
        info("Spatial KD-tree ready.")

        with tqdm(
            total=len(valid_xyz),
            desc="Counting spatial neighbors",
            unit="pt",
            mininterval=0.3,
        ) as bar:
            for start in range(0, len(valid_xyz), chunk_size):
                end = min(start + chunk_size, len(valid_xyz))

                # return_length évite les listes de voisins, très volumineuses.
                # Le point lui-même est inclus dans le résultat.
                counts = tree.query_ball_point(
                    valid_xyz[start:end],
                    r=radius,
                    return_length=True,
                    workers=workers,
                )

                keep[finite_indices[start:end]] = (
                    counts >= min_neighbors + 1
                )
                bar.update(end - start)

        del tree, valid_xyz

    removed = int(np.count_nonzero(~keep))

    for index in progress(
        range(count),
        "Removing isolated points",
        total=count,
        unit="pt",
    ):
        if not keep[index]:
            del points3D[int(ids[index])]

    # Important : maintenir la cohérence des images avec points3D.
    for image in progress(
        images.values(),
        "Updating image references",
        total=len(images),
        unit="image",
    ):
        pids = image.point3D_ids
        valid = np.fromiter(
            (
                int(pid) == -1 or int(pid) in points3D
                for pid in pids
            ),
            dtype=bool,
            count=len(pids),
        )
        pids[~valid] = -1

    info(
        f"Isolation filter: removed={removed:,}/{count:,} "
        f"({100.0 * removed / count:.2f} %), "
        f"remaining={len(points3D):,}"
    )


# ---------------------------------------------------------------------------
# Géométrie et covisibilité
# ---------------------------------------------------------------------------

def qvec_to_rotmat(qvec):
    qvec = np.asarray(qvec, dtype=np.float64)
    norm = np.linalg.norm(qvec)

    if not np.isfinite(norm) or norm <= 0:
        raise ValueError("Quaternion caméra invalide.")

    w, x, y, z = qvec / norm

    return np.array([
        [1 - 2*y*y - 2*z*z, 2*x*y - 2*z*w, 2*x*z + 2*y*w],
        [2*x*y + 2*z*w, 1 - 2*x*x - 2*z*z, 2*y*z - 2*x*w],
        [2*x*z - 2*y*w, 2*y*z + 2*x*w, 1 - 2*x*x - 2*y*y],
    ], dtype=np.float64)


def image_center_world(image):
    return -qvec_to_rotmat(image.qvec).T @ image.tvec


def build_image_observations(images, valid_points=None):
    observations = {}

    for iid, image in progress(
        images.items(),
        "Collecting image observations",
        total=len(images),
        unit="image",
    ):
        ids = image.point3D_ids[image.point3D_ids != -1]

        if valid_points is None:
            observations[iid] = set(map(int, ids))
        else:
            observations[iid] = {
                int(pid) for pid in ids if int(pid) in valid_points
            }

    return observations


def build_covisibility_graph(
    images,
    points3D,
    min_covisibility=3,
    distance_sigma=15.0,
):
    image_ids = sorted(images)
    n = len(image_ids)
    index_of = {iid: index for index, iid in enumerate(image_ids)}
    observations = build_image_observations(images, points3D)

    point_to_images = {}

    for iid, pids in progress(
        observations.items(),
        "Indexing point->images",
        total=n,
        unit="image",
    ):
        for pid in pids:
            point_to_images.setdefault(pid, []).append(iid)

    # Libérer les ensembles avant de construire le dictionnaire des paires.
    del observations

    pair_counts = {}

    for image_list in progress(
        point_to_images.values(),
        "Counting image pairs",
        total=len(point_to_images),
        unit="pt",
    ):
        # image_ids est trié : les listes inversées le sont aussi.
        for a in range(len(image_list)):
            for b in range(a + 1, len(image_list)):
                key = (image_list[a], image_list[b])
                pair_counts[key] = pair_counts.get(key, 0) + 1

    del point_to_images

    centers = {}
    for iid in progress(
        image_ids, "Computing camera centers", unit="image"
    ):
        center = image_center_world(images[iid])
        if not np.isfinite(center).all():
            raise ValueError(f"Centre caméra non fini : image {iid}")
        centers[iid] = center

    rows, cols, values = [], [], []

    for (ia, ib), shared in progress(
        pair_counts.items(),
        "Building weighted graph",
        total=len(pair_counts),
        unit="paire",
    ):
        if shared < min_covisibility:
            continue

        delta = centers[ia] - centers[ib]
        distance2 = float(delta @ delta)
        weight = shared * math.exp(
            -distance2 / (2.0 * distance_sigma**2)
        )

        if weight <= 0:
            continue

        i, j = index_of[ia], index_of[ib]
        rows.extend((i, j))
        cols.extend((j, i))
        values.extend((weight, weight))

    del pair_counts

    info("Assembling sparse graph...")
    W = sp.csr_matrix(
        (values, (rows, cols)),
        shape=(n, n),
        dtype=np.float64,
    )
    W.eliminate_zeros()

    return W, image_ids


# ---------------------------------------------------------------------------
# Partitionnement
# ---------------------------------------------------------------------------

def spectral_partition(W, k, seed=0):
    n = W.shape[0]

    if not 1 <= k <= n:
        raise ValueError("Il faut 1 <= num_blocks <= nombre d'images.")

    if k == 1:
        return np.zeros(n, dtype=np.int32)

    if k == n:
        return np.arange(n, dtype=np.int32)

    degree = np.asarray(W.sum(axis=1)).ravel()
    Dinv = sp.diags(1.0 / np.sqrt(np.maximum(degree, 1e-12)))
    L = sp.eye(n, format="csr") - Dinv @ W @ Dinv

    rng = np.random.default_rng(seed)

    info(
        "Computing spectral embedding "
        "(eigsh : pas de pourcentage disponible)..."
    )
    _, embedding = eigsh(
        L,
        k=k,
        which="SM",
        v0=rng.standard_normal(n),
    )

    embedding /= np.maximum(
        np.linalg.norm(embedding, axis=1, keepdims=True),
        1e-12,
    )

    info("Clustering spectral embedding with k-means...")

    # Plusieurs essais pour limiter les cas de clusters vides.
    for attempt in range(5):
        _, labels = kmeans2(
            embedding,
            k,
            minit="++",
            iter=100,
            seed=seed + attempt,
        )
        if len(np.unique(labels)) == k:
            return labels.astype(np.int32)

    raise RuntimeError(
        "K-means n'a pas produit K blocs non vides. "
        "Essayer une autre seed ou revoir le graphe."
    )


def enforce_connectivity(labels, W):
    """
    Réparation heuristique compatible avec la version précédente.

    Une composante sans voisin d'un autre bloc n'est PAS rattachée
    arbitrairement : elle est conservée et signalée par le diagnostic.
    """
    labels = labels.copy()
    original_labels = np.unique(labels)

    for label in progress(
        original_labels,
        "Repairing connectivity (heuristic)",
        unit="bloc",
    ):
        nodes = np.flatnonzero(labels == label)
        if len(nodes) <= 1:
            continue

        count, components = connected_components(
            W[nodes][:, nodes],
            directed=False,
        )
        if count <= 1:
            continue

        keep_component = int(np.argmax(np.bincount(components)))

        for component in range(count):
            if component == keep_component:
                continue

            component_nodes = nodes[components == component]
            scores = {}

            for u in component_nodes:
                start, end = W.indptr[u], W.indptr[u + 1]

                for v, weight in zip(
                    W.indices[start:end],
                    W.data[start:end],
                ):
                    other_label = int(labels[v])
                    if other_label != label:
                        scores[other_label] = (
                            scores.get(other_label, 0.0)
                            + float(weight)
                        )

            if scores:
                target = max(scores, key=scores.get)
                labels[component_nodes] = target

    return labels


def report_connectivity(labels, W, requested_k):
    unique = np.unique(labels)

    if len(unique) != requested_k:
        warn(
            f"Nombre effectif de blocs : {len(unique)}, "
            f"demandé : {requested_k}"
        )

    disconnected = 0

    for label in progress(
        unique, "Checking block connectivity", unit="bloc"
    ):
        nodes = np.flatnonzero(labels == label)
        count = connected_components(
            W[nodes][:, nodes],
            directed=False,
            return_labels=False,
        )

        info(
            f"Primary block {int(label):03d}: "
            f"images={len(nodes)}, connected_components={count}"
        )

        if count > 1:
            disconnected += 1

    if disconnected:
        warn(
            f"{disconnected} bloc(s) principal(aux) non connexes. "
            "La réparation actuelle est heuristique."
        )


# ---------------------------------------------------------------------------
# Recouvrement contrôlé
# ---------------------------------------------------------------------------

def build_blocks(
    images,
    points3D,
    image_ids,
    labels,
    overlap_min_shared_points=100,
    *,
    W,
    overlap_min_shared_ratio=0.20,
    overlap_min_affinity_ratio=0.15,
    overlap_max_fraction=0.05,
    overlap_max_extra_blocks=1,
):
    labels = np.asarray(labels, dtype=np.int32)
    n = len(image_ids)
    k = len(np.unique(labels))

    if n == 0 or len(labels) != n or W.shape != (n, n):
        raise ValueError("Dimensions invalides pour build_blocks.")

    if not np.array_equal(np.unique(labels), np.arange(k)):
        raise ValueError("Labels non contigus.")

    W = W.tocsr()
    primary_images = [set() for _ in range(k)]

    for index, iid in progress(
        enumerate(image_ids),
        "Assigning primary images",
        total=n,
        unit="image",
    ):
        primary_images[int(labels[index])].add(iid)

    block_images = [set(ids) for ids in primary_images]

    capacities = np.array([
        math.floor(len(ids) * overlap_max_fraction)
        for ids in primary_images
    ], dtype=np.int64)

    added_per_block = np.zeros(k, dtype=np.int64)
    added_per_image = np.zeros(n, dtype=np.int32)

    if overlap_max_extra_blocks > 0 and np.any(capacities > 0):
        observations = build_image_observations(images, points3D)
        primary_points = [set() for _ in range(k)]

        for index, iid in progress(
            enumerate(image_ids),
            "Collecting primary block points",
            total=n,
            unit="image",
        ):
            primary_points[int(labels[index])].update(
                observations[iid]
            )

        candidates = []

        for index, iid in progress(
            enumerate(image_ids),
            "Scoring boundary images",
            total=n,
            unit="image",
        ):
            seen = observations[iid]
            if not seen:
                continue

            start, end = W.indptr[index], W.indptr[index + 1]
            neighbors = W.indices[start:end]
            weights = W.data[start:end]

            affinity = np.bincount(
                labels[neighbors],
                weights=weights,
                minlength=k,
            )
            total = float(affinity.sum())
            if total <= 0:
                continue

            affinity_ratios = affinity / total
            targets = np.flatnonzero(
                (affinity > 0)
                & (affinity_ratios >= overlap_min_affinity_ratio)
                & (capacities > 0)
            )

            for raw_target in targets:
                target = int(raw_target)

                if target == int(labels[index]):
                    continue

                shared = len(seen & primary_points[target])
                if shared < overlap_min_shared_points:
                    continue

                shared_ratio = shared / len(seen)
                if shared_ratio < overlap_min_shared_ratio:
                    continue

                score = float(affinity_ratios[target]) * shared_ratio
                candidates.append((score, shared, index, target))

        info(f"Sorting {len(candidates):,} overlap candidates...")
        candidates.sort(
            key=lambda item: (
                -item[0], -item[1], item[2], item[3]
            )
        )
        info("Candidate sorting done.")

        for score, shared, index, target in progress(
            candidates,
            "Selecting limited overlap",
            unit="candidat",
        ):
            if added_per_block[target] >= capacities[target]:
                continue
            if added_per_image[index] >= overlap_max_extra_blocks:
                continue

            block_images[target].add(image_ids[index])
            added_per_block[target] += 1
            added_per_image[index] += 1

        info("Releasing temporary observation sets...")
        del observations, primary_points, candidates

    image_to_blocks = {iid: set() for iid in image_ids}

    for block, ids in progress(
        enumerate(block_images),
        "Indexing image memberships",
        total=k,
        unit="bloc",
    ):
        for iid in ids:
            image_to_blocks[iid].add(block)

    point_blocks = {}
    block_point_counts = np.zeros(k, dtype=np.int64)

    for pid, point in progress(
        points3D.items(),
        "Assigning points to blocks",
        total=len(points3D),
        unit="pt",
    ):
        memberships = set()

        for iid, _ in point.track:
            blocks = image_to_blocks.get(iid)
            if blocks:
                memberships.update(blocks)

        if memberships:
            point_blocks[pid] = memberships
            for block in memberships:
                block_point_counts[block] += 1

    duplicated = sum(
        len(blocks) > 1 for blocks in image_to_blocks.values()
    )

    info(
        f"Images multi-blocs : {duplicated}/{n} "
        f"({100.0 * duplicated / n:.2f} %)"
    )

    for block in range(k):
        info(
            f"Block {block:03d}: "
            f"primary={len(primary_images[block])}, "
            f"overlap={int(added_per_block[block])}, "
            f"quota={int(capacities[block])}, "
            f"total={len(block_images[block])}, "
            f"points={int(block_point_counts[block])}"
        )

    return block_images, primary_images, point_blocks


# ---------------------------------------------------------------------------
# Export COLMAP
# ---------------------------------------------------------------------------

def filtered_image_point_ids(image, allowed_points):
    return np.fromiter(
        (
            pid if pid != -1 and pid in allowed_points else -1
            for pid in map(int, image.point3D_ids)
        ),
        dtype=np.int64,
        count=len(image.point3D_ids),
    )


def write_cameras_txt(path, cameras, camera_ids):
    with open(path, "w", encoding="utf-8") as f:
        f.write("# CAMERA_ID MODEL WIDTH HEIGHT PARAMS[]\n")

        for cid in sorted(camera_ids):
            camera = cameras[cid]
            name = CAMERA_MODELS[camera.model_id][0]
            params = " ".join(
                f"{float(value):.17g}" for value in camera.params
            )

            f.write(
                f"{cid} {name} {camera.width} "
                f"{camera.height} {params}\n"
            )


def write_cameras_bin(path, cameras, camera_ids):
    with open(path, "wb") as f:
        f.write(struct.pack("<Q", len(camera_ids)))

        for cid in sorted(camera_ids):
            camera = cameras[cid]
            f.write(struct.pack(
                "<IiQQ",
                int(cid),
                int(camera.model_id),
                int(camera.width),
                int(camera.height),
            ))
            f.write(
                np.asarray(camera.params, dtype="<f8").tobytes()
            )


def write_block_images(
    folder,
    images,
    image_ids,
    allowed_points,
    export_text,
    export_binary,
):
    with ExitStack() as stack:
        text_file = None
        binary_file = None

        if export_text:
            text_file = stack.enter_context(open(
                folder / "images.txt",
                "w",
                encoding="utf-8",
                buffering=1024 * 1024,
            ))
            text_file.write(
                "# IMAGE_ID QW QX QY QZ TX TY TZ CAMERA_ID NAME\n"
                "# POINTS2D[] as X Y POINT3D_ID\n"
            )

        if export_binary:
            binary_file = stack.enter_context(open(
                folder / "images.bin",
                "wb",
                buffering=1024 * 1024,
            ))
            binary_file.write(struct.pack("<Q", len(image_ids)))

        for iid in progress(
            sorted(image_ids),
            f"{folder.name}: writing images",
            total=len(image_ids),
            unit="image",
        ):
            image = images[iid]
            count = len(image.point3D_ids)

            if image.xys.shape != (count, 2):
                raise ValueError(
                    f"Observations incohérentes pour image {iid}"
                )

            # Même filtrage pour les sorties texte et binaire.
            pids = filtered_image_point_ids(image, allowed_points)

            if text_file is not None:
                pose = " ".join(
                    f"{float(value):.17g}"
                    for value in (*image.qvec, *image.tvec)
                )
                text_file.write(
                    f"{iid} {pose} {image.camera_id} {image.name}\n"
                )
                text_file.write(" ".join(
                    f"{x:.17g} {y:.17g} {int(pid)}"
                    for (x, y), pid in zip(image.xys, pids)
                ))
                text_file.write("\n")

            if binary_file is not None:
                binary_file.write(struct.pack(
                    "<I7dI",
                    int(iid),
                    *map(float, image.qvec),
                    *map(float, image.tvec),
                    int(image.camera_id),
                ))
                binary_file.write(
                    image.name.encode("utf-8") + b"\x00"
                )
                binary_file.write(struct.pack("<Q", count))

                records = np.empty(count, dtype=OBS_DTYPE)
                records["x"] = image.xys[:, 0]
                records["y"] = image.xys[:, 1]
                records["pid"] = pids
                binary_file.write(records.tobytes())


def write_block_points_stream(
    folder,
    points3D,
    point_ids,
    image_ids,
    export_text,
    export_binary,
):
    count = 0
    track_sum = 0

    with ExitStack() as stack:
        text_file = None
        binary_file = None

        if export_text:
            text_file = stack.enter_context(open(
                folder / "points3D.txt",
                "w",
                encoding="utf-8",
                buffering=1024 * 1024,
            ))
            text_file.write(
                "# POINT3D_ID X Y Z R G B ERROR "
                "TRACK[] as IMAGE_ID POINT2D_IDX\n"
            )

        if export_binary:
            binary_file = stack.enter_context(open(
                folder / "points3D.bin",
                "wb",
                buffering=1024 * 1024,
            ))
            binary_file.write(struct.pack("<Q", 0))

        for pid in progress(
            point_ids,
            f"{folder.name}: filtering + writing points",
            total=len(point_ids),
            unit="pt",
        ):
            point = points3D[pid]
            track = [
                (iid, index)
                for iid, index in point.track
                if iid in image_ids
            ]

            if not track:
                raise ValueError(
                    f"Point {pid} sans observation dans {folder}"
                )

            count += 1
            track_sum += len(track)

            if text_file is not None:
                xyz = " ".join(
                    f"{float(value):.17g}" for value in point.xyz
                )
                rgb = " ".join(
                    str(int(value)) for value in point.rgb
                )
                track_text = " ".join(
                    f"{iid} {index}" for iid, index in track
                )
                text_file.write(
                    f"{pid} {xyz} {rgb} {point.error:.17g} "
                    f"{track_text}\n"
                )

            if binary_file is not None:
                binary_file.write(POINT_HEADER.pack(
                    int(pid),
                    *map(float, point.xyz),
                    *map(int, point.rgb),
                    float(point.error),
                    len(track),
                ))
                binary_file.write(
                    np.asarray(track, dtype="<u4").tobytes()
                )

        if binary_file is not None:
            binary_file.seek(0)
            binary_file.write(struct.pack("<Q", count))

        mean_track = track_sum / count if count else 0.0

        if text_file is not None:
            text_file.write(
                f"# Number of points: {count}, "
                f"mean track length: {mean_track:.6f}\n"
            )

    return count, mean_track


def export_blocks(
    out_dir,
    cameras,
    images,
    points3D,
    block_images,
    primary_images,
    point_blocks,
    export_text,
    export_binary,
    colors,
    args,
):
    out_dir.mkdir(parents=True, exist_ok=True)
    block_points = [[] for _ in block_images]

    for pid, memberships in progress(
        point_blocks.items(),
        "Indexing points for export",
        total=len(point_blocks),
        unit="pt",
    ):
        for block in memberships:
            block_points[block].append(pid)

    manifest = {
        "parameters": vars(args),
        "cross_block_color": [255, 255, 0],
        "blocks": [],
    }

    for block, image_ids in progress(
        enumerate(block_images),
        "Exporting COLMAP blocks",
        total=len(block_images),
        unit="bloc",
    ):
        folder = out_dir / f"block_{block:03d}"
        folder.mkdir(parents=True, exist_ok=True)

        point_ids = block_points[block]
        allowed_points = set(point_ids)
        camera_ids = {
            images[iid].camera_id for iid in image_ids
        }

        if export_text:
            write_cameras_txt(
                folder / "cameras.txt", cameras, camera_ids
            )

        if export_binary:
            write_cameras_bin(
                folder / "cameras.bin", cameras, camera_ids
            )

        write_block_images(
            folder,
            images,
            image_ids,
            allowed_points,
            export_text,
            export_binary,
        )

        count, mean_track = write_block_points_stream(
            folder,
            points3D,
            point_ids,
            image_ids,
            export_text,
            export_binary,
        )

        # Permet d'identifier les images principales sans ambiguïté.
        with open(
            folder / "image_membership.json",
            "w",
            encoding="utf-8",
        ) as f:
            json.dump({
                "primary_image_ids": sorted(primary_images[block]),
                "overlap_image_ids": sorted(
                    image_ids - primary_images[block]
                ),
            }, f, indent=2)

        manifest["blocks"].append({
            "block_id": block,
            "color": list(colors[block]),
            "num_images": len(image_ids),
            "num_primary_images": len(primary_images[block]),
            "num_overlap_images": (
                len(image_ids) - len(primary_images[block])
            ),
            "num_points3D_written": count,
            "mean_track_length_written": mean_track,
            "path": str(folder.resolve()),
        })

        block_points[block] = []
        del allowed_points

    with open(
        out_dir / "manifest.json", "w", encoding="utf-8"
    ) as f:
        json.dump(manifest, f, indent=2)


# ---------------------------------------------------------------------------
# Couleurs et PLY
# ---------------------------------------------------------------------------

def generate_block_colors_excluding_yellow(k):
    palette = [
        (235, 45, 55),
        (45, 115, 255),
        (40, 205, 85),
        (190, 60, 240),
        (25, 210, 220),
        (250, 75, 165),
    ]

    if k <= len(palette):
        return palette[:k]

    colors = []
    for index in range(k):
        hue = 0.85 * index / k
        if hue >= 0.08:
            hue += 0.15

        rgb = colorsys.hsv_to_rgb(hue, 0.85, 1.0)
        colors.append(tuple(
            int(round(channel * 255)) for channel in rgb
        ))

    return colors


def export_colored_ply(
    path,
    points3D,
    point_blocks,
    block_colors,
):
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(
        path, "w", encoding="utf-8", buffering=1024 * 1024
    ) as f:
        f.write(
            "ply\n"
            "format ascii 1.0\n"
            f"element vertex {len(point_blocks)}\n"
            "property double x\n"
            "property double y\n"
            "property double z\n"
            "property uchar red\n"
            "property uchar green\n"
            "property uchar blue\n"
            "end_header\n"
        )

        for pid, memberships in progress(
            point_blocks.items(),
            "Writing colored PLY",
            total=len(point_blocks),
            unit="pt",
        ):
            xyz = points3D[pid].xyz

            color = (
                (255, 255, 0)
                if len(memberships) > 1
                else block_colors[next(iter(memberships))]
            )

            f.write(
                f"{xyz[0]:.17g} {xyz[1]:.17g} {xyz[2]:.17g} "
                f"{color[0]} {color[1]} {color[2]}\n"
            )


# ---------------------------------------------------------------------------
# CLI et pipeline
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Découpage COLMAP sparse avec recouvrement contrôlé."
    )

    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--num-blocks", "-k", type=int, required=True)
    parser.add_argument("--min-covisibility", type=int, default=3)
    parser.add_argument("--distance-sigma", type=float, default=15.0)
    parser.add_argument("--seed", type=int, default=0)

    parser.add_argument(
        "--overlap-min-shared-points", type=int, default=100
    )
    parser.add_argument(
        "--overlap-min-shared-ratio", type=float, default=0.20
    )
    parser.add_argument(
        "--overlap-min-affinity-ratio", type=float, default=0.15
    )
    parser.add_argument(
        "--overlap-max-fraction", type=float, default=0.05
    )
    parser.add_argument(
        "--overlap-max-extra-blocks", type=int, default=1
    )

    parser.add_argument(
        "--remove-isolated-points",
        action="store_true",
        help="Activer le nettoyage spatial avant le partitionnement.",
    )
    parser.add_argument(
        "--isolation-radius",
        type=float,
        default=None,
        help="Rayon du nettoyage, dans les unités du modèle COLMAP.",
    )
    parser.add_argument(
        "--isolation-min-neighbors",
        type=int,
        default=3,
        help="Nombre minimal d'autres points dans le rayon.",
    )
    parser.add_argument(
        "--isolation-chunk-size",
        type=int,
        default=50000,
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Threads du filtre spatial ; -1 pour tous les CPU.",
    )

    parser.add_argument("--export-text", action="store_true")
    parser.add_argument("--export-binary", action="store_true")
    parser.add_argument("--export-ply", default="")

    args = parser.parse_args()

    if args.num_blocks < 1:
        parser.error("--num-blocks doit être >= 1.")

    if args.min_covisibility < 1:
        parser.error("--min-covisibility doit être >= 1.")

    if not math.isfinite(args.distance_sigma) or args.distance_sigma <= 0:
        parser.error("--distance-sigma doit être fini et > 0.")

    if args.overlap_min_shared_points < 1:
        parser.error("--overlap-min-shared-points doit être >= 1.")

    for name in (
        "overlap_min_shared_ratio",
        "overlap_min_affinity_ratio",
        "overlap_max_fraction",
    ):
        if not 0 <= getattr(args, name) <= 1:
            parser.error(f"--{name.replace('_', '-')} doit être dans [0,1].")

    if args.overlap_max_extra_blocks < 0:
        parser.error("--overlap-max-extra-blocks doit être >= 0.")

    if args.workers != -1 and args.workers < 1:
        parser.error("--workers doit être -1 ou >= 1.")

    if args.isolation_min_neighbors < 1:
        parser.error("--isolation-min-neighbors doit être >= 1.")

    if args.isolation_chunk_size < 1:
        parser.error("--isolation-chunk-size doit être >= 1.")

    if args.remove_isolated_points:
        if (
            args.isolation_radius is None
            or not math.isfinite(args.isolation_radius)
            or args.isolation_radius <= 0
        ):
            parser.error(
                "--remove-isolated-points nécessite "
                "--isolation-radius fini et > 0."
            )

    input_dir = Path(args.input)
    out_dir = Path(args.output)

    if input_dir.resolve() == out_dir.resolve():
        parser.error("Les dossiers entrée et sortie doivent être différents.")

    # Éviter de mélanger anciens .bin, .txt ou anciens blocs.
    if out_dir.exists() and any(out_dir.glob("block_*")):
        parser.error(
            "Le dossier de sortie contient déjà des block_*. "
            "Choisir un nouveau dossier pour éviter les fichiers périmés."
        )

    if args.export_text or args.export_binary:
        export_text = args.export_text
        export_binary = args.export_binary
    else:
        export_text = True
        export_binary = True

    info(f"Loading model from {input_dir}")
    cameras, images, points3D, fmt = load_colmap_model(input_dir)

    info(
        f"Loaded: {len(cameras)} cameras, {len(images)} images, "
        f"{len(points3D):,} points3D (format={fmt})"
    )

    if not images or not points3D:
        parser.error("Le modèle doit contenir des images et des points 3D.")

    if args.num_blocks > len(images):
        parser.error("--num-blocks dépasse le nombre d'images.")

    if args.remove_isolated_points:
        remove_isolated_points(
            images,
            points3D,
            radius=args.isolation_radius,
            min_neighbors=args.isolation_min_neighbors,
            chunk_size=args.isolation_chunk_size,
            workers=args.workers,
        )

        if not points3D:
            parser.error(
                "Le nettoyage a supprimé tous les points. "
                "Augmenter le rayon ou réduire le nombre minimal de voisins."
            )

    info("Building covisibility graph...")
    W, image_ids = build_covisibility_graph(
        images,
        points3D,
        min_covisibility=args.min_covisibility,
        distance_sigma=args.distance_sigma,
    )
    info(
        f"Graph: {len(image_ids)} nodes, "
        f"{W.nnz // 2:,} undirected edges"
    )

    graph_components = connected_components(
        W, directed=False, return_labels=False
    )
    info(f"Graph connected components: {graph_components}")

    if graph_components > args.num_blocks:
        warn(
            "Le graphe possède plus de composantes connexes que de blocs "
            "demandés : il est impossible de couvrir toutes les images "
            "avec seulement K blocs connexes dans ce graphe."
        )

    info(f"Spectral clustering into K={args.num_blocks}...")
    labels = spectral_partition(W, args.num_blocks, seed=args.seed)

    labels = enforce_connectivity(labels, W)

    unique = np.unique(labels)
    remap = {int(old): new for new, old in enumerate(unique)}
    labels = np.array(
        [remap[int(label)] for label in labels],
        dtype=np.int32,
    )

    report_connectivity(labels, W, args.num_blocks)

    info("Building blocks with limited overlap...")
    block_images, primary_images, point_blocks = build_blocks(
        images,
        points3D,
        image_ids,
        labels,
        overlap_min_shared_points=args.overlap_min_shared_points,
        W=W,
        overlap_min_shared_ratio=args.overlap_min_shared_ratio,
        overlap_min_affinity_ratio=args.overlap_min_affinity_ratio,
        overlap_max_fraction=args.overlap_max_fraction,
        overlap_max_extra_blocks=args.overlap_max_extra_blocks,
    )

    colors = generate_block_colors_excluding_yellow(len(block_images))

    info(f"Exporting blocks to {out_dir}")
    export_blocks(
        out_dir,
        cameras,
        images,
        points3D,
        block_images,
        primary_images,
        point_blocks,
        export_text,
        export_binary,
        colors,
        args,
    )

    if args.export_ply:
        export_colored_ply(
            Path(args.export_ply),
            points3D,
            point_blocks,
            colors,
        )

    info("DONE")


if __name__ == "__main__":
    main()
