#!/usr/bin/env python3
"""
Fusion par voxels de Gaussian Splats déjà alignés.

Dans chaque voxel, seule la scène au meilleur score moyen est conservée :
score = 2 * proximité au centre de sa scène - log(taille / taille médiane).

Entrées : PLY 3DGS binaire, ou .splat classique de 32 octets par splat.
Sortie : .ply, avec toutes les propriétés, ou .splat, avec la couleur DC seulement.
"""

import argparse
import math
import os
import tempfile
from pathlib import Path

import numpy as np
from plyfile import PlyData
from tqdm import tqdm

CHUNK = 1_000_000
SAMPLE = 100_000
C0 = 0.28209479177387814
BITS = 21
OFFSET = 1 << (BITS - 1)          # cellules entre -1 048 576 et +1 048 575
MASK = (1 << BITS) - 1

BASE_NAMES = (
    "x", "y", "z", "f_dc_0", "f_dc_1", "f_dc_2", "opacity",
    "scale_0", "scale_1", "scale_2", "rot_0", "rot_1", "rot_2", "rot_3",
)
BASE_DTYPE = np.dtype([(n, "<f4") for n in BASE_NAMES])
SPLAT_DTYPE = np.dtype([
    ("position", "<f4", (3,)), ("scale", "<f4", (3,)),
    ("rgba", "u1", (4,)), ("rotation", "u1", (4,)),
])
PLY_TYPES = {
    ("i", 1): "char", ("u", 1): "uchar", ("i", 2): "short",
    ("u", 2): "ushort", ("i", 4): "int", ("u", 4): "uint",
    ("f", 4): "float", ("f", 8): "double",
}


def matrix(data, names):
    return np.column_stack([data[n] for n in names])


def sigmoid(v):
    return np.exp(-np.logaddexp(0.0, -np.asarray(v, dtype=np.float64)))


def discover(inputs, output):
    found, dest = {}, output.resolve()
    for item in inputs:
        p = Path(item).expanduser()
        if p.is_dir():
            cands = sorted(p.iterdir())
        elif p.is_file():
            cands = [p]
        else:
            raise ValueError(f"Entrée introuvable : {p}")
        for c in cands:
            if (c.is_file() and c.suffix.lower() in (".ply", ".splat")
                    and c.resolve() != dest):
                found.setdefault(c.resolve(), c)
    if not found:
        raise ValueError("Aucun fichier .ply ou .splat trouvé")
    return list(found.values())


def check_binary_ply(path):
    with path.open("rb") as f:
        if f.readline().strip() != b"ply":
            raise ValueError(f"{path}: en-tête PLY invalide")
        for _ in range(100_000):
            line = f.readline()
            if not line:
                break
            if line.startswith(b"format ") and b"binary" not in line:
                raise ValueError(f"{path}: convertir le PLY ASCII en binaire")
            if line.startswith(b"property list "):
                raise ValueError(f"{path}: propriétés PLY de type liste non prises en charge")
            if line.strip() == b"end_header":
                return
    raise ValueError(f"{path}: en-tête PLY incomplet")


class Scene:
    def __init__(self, path):
        self.path = path
        self.is_splat = path.suffix.lower() == ".splat"
        self.raw = None
        if self.is_splat:
            size = path.stat().st_size
            if size % SPLAT_DTYPE.itemsize:
                raise ValueError(f"{path}: fichier .splat de 32 octets invalide")
            self.count = size // SPLAT_DTYPE.itemsize
            self.dtype = BASE_DTYPE
            if self.count:
                self.raw = np.memmap(path, dtype=SPLAT_DTYPE, mode="r",
                                     shape=(self.count,))
        else:
            check_binary_ply(path)
            self.raw = PlyData.read(str(path), mmap="r")["vertex"].data
            self.count = len(self.raw)
            self.dtype = self.raw.dtype
        missing = set(BASE_NAMES) - set(self.dtype.names or ())
        if missing:
            raise ValueError(f"{path}: propriétés 3DGS manquantes : {sorted(missing)}")
        for n in self.dtype.names:
            f = self.dtype.fields[n][0]
            if (f.kind, f.itemsize) not in PLY_TYPES:
                raise ValueError(f"{path}: type non pris en charge pour la propriété {n}")

    def geometry(self, sel):
        raw = self.raw[sel]
        if self.is_splat:
            xyz = raw["position"].astype(np.float64)
            s = raw["scale"].astype(np.float64)
            if np.any(~np.isfinite(s)) or np.any(s <= 0):
                raise ValueError(f"{self.path}: échelles invalides")
            log_size = np.log(s.max(axis=1))
        else:
            xyz = matrix(raw, ("x", "y", "z")).astype(np.float64)
            log_size = matrix(raw, ("scale_0", "scale_1", "scale_2")
                              ).astype(np.float64).max(axis=1)
        if not (np.isfinite(xyz).all() and np.isfinite(log_size).all()):
            raise ValueError(f"{self.path}: géométrie non finie")
        return xyz, log_size

    def records(self, start, stop):
        raw = self.raw[start:stop]
        if not self.is_splat:
            return raw
        d = np.zeros(len(raw), dtype=BASE_DTYPE)
        for i, n in enumerate("xyz"):
            d[n] = raw["position"][:, i]
        s = raw["scale"].astype(np.float64)
        if np.any(~np.isfinite(s)) or np.any(s <= 0):
            raise ValueError(f"{self.path}: échelles invalides")
        for i in range(3):
            d[f"scale_{i}"] = np.log(s[:, i])
            d[f"f_dc_{i}"] = (raw["rgba"][:, i] / 255.0 - 0.5) / C0
        a = np.clip(raw["rgba"][:, 3] / 255.0, 1e-6, 1 - 1e-6)
        d["opacity"] = np.log(a / (1 - a))
        q = (raw["rotation"].astype(np.float64) - 128) / 128
        norm = np.linalg.norm(q, axis=1)
        if np.any(norm < 1e-8):
            raise ValueError(f"{self.path}: quaternion nul")
        q /= norm[:, None]
        for i in range(4):
            d[f"rot_{i}"] = q[:, i]
        return d

    def close(self):
        mm = getattr(self.raw, "_mmap", None)
        self.raw = None
        if mm is not None:
            mm.close()


def signature(dtype):
    return sorted((n, dtype.fields[n][0].kind, dtype.fields[n][0].itemsize)
                  for n in dtype.names)


def inspect(paths):
    meta, samples, sig, out_dtype = [], [], None, None
    for path in tqdm(paths, desc="1/5 Analyse", unit="fichier"):
        sc = Scene(path)
        try:
            if not sc.count:
                continue
            cur = signature(sc.dtype)
            if sig is None:
                sig = cur
                out_dtype = np.dtype([(n, sc.dtype.fields[n][0].newbyteorder("<"))
                                      for n in sc.dtype.names])
            elif cur != sig:
                raise ValueError(f"{path}: schéma différent, notamment le degré SH")
            step = max(1, math.ceil(sc.count / SAMPLE))
            xyz, ls = sc.geometry(slice(0, sc.count, step))
            lo, hi = np.quantile(xyz, [0.02, 0.98], axis=0)
            half = (hi - lo) / 2
            meta.append({
                "path": path, "count": sc.count, "center": (lo + hi) / 2,
                "half": half,
                "active": half > max(float(half.max()) * 1e-6, 1e-12),
            })
            samples.append(ls)
        finally:
            sc.close()
    if not meta:
        raise ValueError("Toutes les entrées sont vides")
    return meta, out_dtype, float(np.median(np.concatenate(samples)))


def voxel_keys(xyz, voxel):
    with np.errstate(over="ignore", invalid="ignore"):
        cells = np.floor(xyz / voxel)
    if not np.isfinite(cells).all() or np.any(np.abs(cells) >= OFFSET):
        raise ValueError(
            "Scène trop étendue pour la taille de voxel : augmenter --strength")
    c = cells.astype(np.int64) + OFFSET
    return (c[:, 0] << (2 * BITS)) | (c[:, 1] << BITS) | c[:, 2]


def index_scenes(meta, root, voxel, median_ls):
    """Écrit sur disque une clé de voxel et un score pour chaque splat."""
    total = sum(m["count"] for m in meta)
    with tqdm(total=total, desc="2/5 Indexation", unit="spl", unit_scale=True) as bar:
        for s, m in enumerate(meta):
            keys = np.lib.format.open_memmap(root / f"k{s}.npy", mode="w+",
                                             dtype=np.int64, shape=(m["count"],))
            scores = np.lib.format.open_memmap(root / f"s{s}.npy", mode="w+",
                                               dtype=np.float32, shape=(m["count"],))
            sc = Scene(m["path"])
            try:
                for a in range(0, sc.count, CHUNK):
                    b = min(a + CHUNK, sc.count)
                    xyz, ls = sc.geometry(slice(a, b))
                    keys[a:b] = voxel_keys(xyz, voxel)
                    act = m["active"]
                    if act.any():
                        rel = np.abs((xyz[:, act] - m["center"][act]) / m["half"][act])
                        cen = 1 - np.clip(rel.max(axis=1), 0, 1)
                    else:
                        cen = np.ones(b - a)
                    scores[a:b] = 2 * cen - (ls - median_ls)
                    bar.update(b - a)
            finally:
                sc.close()
                keys.flush()
                scores.flush()
                del keys, scores


def voxel_stats(meta, root):
    """Calcule, pour chaque scène, ses voxels et leur score moyen."""
    ukeys, means = [], []
    for s, m in enumerate(tqdm(meta, desc="3/5 Agrégation", unit="scène")):
        k = np.load(root / f"k{s}.npy")
        sc = np.load(root / f"s{s}.npy", mmap_mode="r")
        u, inv = np.unique(k, return_inverse=True)
        del k
        sums = np.bincount(inv, weights=sc, minlength=len(u))
        cnt = np.bincount(inv, minlength=len(u))
        del inv
        ukeys.append(u)
        means.append((sums / cnt).astype(np.float32))
    return ukeys, means


def choose_winners(ukeys, means):
    """Garde un seul couple (voxel, scène) par voxel : le meilleur score moyen."""
    print("4/5 Choix de la meilleure scène par voxel…", flush=True)
    sizes = [len(u) for u in ukeys]
    allk = np.concatenate(ukeys)
    allm = np.concatenate(means)
    alls = np.repeat(np.arange(len(ukeys), dtype=np.int32), sizes)
    # Clé principale : voxel ; puis score décroissant ; puis numéro de scène.
    order = np.lexsort((alls, -allm, allk))
    del allm, alls
    sk = allk[order]
    del allk
    first = np.empty(len(sk), dtype=bool)
    first[0] = True
    np.not_equal(sk[1:], sk[:-1], out=first[1:])
    del sk
    keep = np.zeros(len(order), dtype=bool)
    keep[order[first]] = True
    del order, first
    return np.split(keep, np.cumsum(sizes)[:-1])


def build_masks(meta, root, ukeys, keeps):
    masks = []
    total = sum(m["count"] for m in meta)
    with tqdm(total=total, desc="5/5 Masques", unit="spl", unit_scale=True) as bar:
        for s, m in enumerate(meta):
            k = np.load(root / f"k{s}.npy", mmap_mode="r")
            mask = np.lib.format.open_memmap(root / f"m{s}.npy", mode="w+",
                                             dtype=np.bool_, shape=(m["count"],))
            n = 0
            for a in range(0, m["count"], CHUNK):
                b = min(a + CHUNK, m["count"])
                sel = keeps[s][np.searchsorted(ukeys[s], k[a:b])]
                mask[a:b] = sel
                n += int(sel.sum())
                bar.update(b - a)
            mask.flush()
            del mask, k
            masks.append((root / f"m{s}.npy", n))
    return masks


def to_splat(d):
    r = np.empty(len(d), dtype=SPLAT_DTYPE)
    r["position"] = matrix(d, ("x", "y", "z"))
    with np.errstate(over="ignore", under="ignore"):
        s = np.exp(matrix(d, ("scale_0", "scale_1", "scale_2")
                          ).astype(np.float64)).astype(np.float32)
    if np.any(~np.isfinite(s)) or np.any(s <= 0):
        raise ValueError("Échelles non représentables en float32 pour le format .splat")
    r["scale"] = s
    rgb = np.clip(0.5 + C0 * matrix(d, ("f_dc_0", "f_dc_1", "f_dc_2")), 0, 1)
    r["rgba"][:, :3] = np.rint(rgb * 255).astype(np.uint8)
    r["rgba"][:, 3] = np.rint(sigmoid(d["opacity"]) * 255).astype(np.uint8)
    q = matrix(d, ("rot_0", "rot_1", "rot_2", "rot_3")).astype(np.float64)
    norm = np.linalg.norm(q, axis=1)
    if np.any(~np.isfinite(q)) or np.any(norm < 1e-8):
        raise ValueError("Quaternion invalide")
    q /= norm[:, None]
    r["rotation"] = np.clip(np.rint(q * 128 + 128), 0, 255).astype(np.uint8)
    return r


def export(meta, masks, dtype, output):
    kept = sum(n for _, n in masks)
    total = sum(m["count"] for m in meta)
    is_splat = output.suffix.lower() == ".splat"
    if is_splat and any(n.startswith("f_rest_") for n in dtype.names):
        print("Attention : l’export .splat ne conserve pas les SH d’ordre supérieur.")
    fd, tmp = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp",
                               dir=output.parent)
    tmp = Path(tmp)
    try:
        with os.fdopen(fd, "wb") as f:
            if not is_splat:
                lines = ["ply", "format binary_little_endian 1.0",
                         f"element vertex {kept}"]
                for n in dtype.names:
                    fd_ = dtype.fields[n][0]
                    lines.append(f"property {PLY_TYPES[(fd_.kind, fd_.itemsize)]} {n}")
                lines.append("end_header")
                f.write(("\n".join(lines) + "\n").encode("ascii"))
            with tqdm(total=total, desc="Export", unit="spl", unit_scale=True) as bar:
                for m, (mp, _) in zip(meta, masks):
                    sc = Scene(m["path"])
                    mask = np.load(mp, mmap_mode="r")
                    try:
                        for a in range(0, sc.count, CHUNK):
                            b = min(a + CHUNK, sc.count)
                            sel = np.asarray(mask[a:b])
                            if sel.any():
                                d = sc.records(a, b)[sel]
                                if is_splat:
                                    out = to_splat(d)
                                else:
                                    out = np.empty(len(d), dtype=dtype)
                                    for n in dtype.names:
                                        out[n] = d[n]
                                f.write(out.tobytes())
                            bar.update(b - a)
                    finally:
                        del mask
                        sc.close()
        os.replace(tmp, output)
    finally:
        tmp.unlink(missing_ok=True)
    return kept


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("inputs", nargs="+", help="Dossier(s) ou fichiers")
    p.add_argument("-o", "--output", required=True, type=Path)
    p.add_argument("--strength", type=float, default=1.0,
                   help="Taille relative des voxels : 0.5 prudent, 1 normal, 2 agressif")
    p.add_argument("--work-dir", type=Path, help="Dossier temporaire, de préférence sur SSD")
    args = p.parse_args()
    if not np.isfinite(args.strength) or args.strength <= 0:
        p.error("--strength doit être > 0")
    if args.output.suffix.lower() not in (".ply", ".splat"):
        p.error("La sortie doit être en .ply ou en .splat")
    try:
        args.output = args.output.expanduser()
        paths = discover(args.inputs, args.output)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        if args.work_dir:
            args.work_dir.mkdir(parents=True, exist_ok=True)

        meta, dtype, med = inspect(paths)
        voxel = math.exp(med) * args.strength
        total = sum(m["count"] for m in meta)
        print(f"Entrée : {total:,} splats, {len(meta)} scènes ; voxel = {voxel:.6g}")

        with tempfile.TemporaryDirectory(prefix="merge-splats-",
                                         dir=args.work_dir) as tmp:
            root = Path(tmp)
            index_scenes(meta, root, voxel, med)
            ukeys, means = voxel_stats(meta, root)
            keeps = choose_winners(ukeys, means)
            del means
            masks = build_masks(meta, root, ukeys, keeps)
            del ukeys, keeps
            kept = export(meta, masks, dtype, args.output)

        print(f"Terminé : {total:,} → {kept:,} splats "
              f"(-{100 * (1 - kept / total):.1f} %) → {args.output}")
    except Exception as e:
        p.exit(1, f"Erreur : {e}\n")


if __name__ == "__main__":
    main()
