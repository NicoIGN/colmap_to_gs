#!/usr/bin/env python3
"""Extrait des images nettes et réparties dans le temps depuis une ou plusieurs vidéos.

Sur-échantillonne la vidéo puis choisit une candidate par tranche temporelle
en combinant sa netteté et sa proximité du centre de la tranche. Les images
floues ou similaires ne sont pas éliminées globalement : cela pourrait créer
de longues lacunes dans la séquence.

Sortie : OUT/frame_00000.jpg, ou OUT/<nom_vidéo>/frame_00000.jpg si plusieurs vidéos.
Dépendances : opencv-python-headless, numpy, tqdm.
"""

import argparse
import csv
import sys
from pathlib import Path

import cv2
import numpy as np
from tqdm import tqdm

OVERSAMPLE = 3
SCORE_MAX_WIDTH = 960
SHARPNESS_WEIGHT = 0.7


def sharpness(gray):
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def shrink_width(gray, max_width):
    height, width = gray.shape[:2]
    if width <= max_width:
        return gray
    return cv2.resize(gray, (max_width, round(height * max_width / width)),
                      interpolation=cv2.INTER_AREA)


def select_indices(sharp, positions, num_frames, sharpness_weight=SHARPNESS_WEIGHT):
    """Retourne les positions (triées) des candidates retenues.

    sharp : netteté de chaque candidate ; positions : position temporelle
    normalisée dans [0, 1[ ; num_frames : nombre visé.
    """
    if len(sharp) == 0:
        return []
    buckets = {}
    for i in sorted(range(len(sharp)), key=lambda index: positions[index]):
        bucket = min(int(positions[i] * num_frames), num_frames - 1)
        buckets.setdefault(bucket, []).append(i)
    selected = []
    for bucket, indices in sorted(buckets.items()):
        best_sharpness = max(sharp[i] for i in indices)
        center = (bucket + 0.5) / num_frames

        def score(i):
            quality = sharp[i] / best_sharpness if best_sharpness > 0 else 0.0
            centered = max(0.0, 1.0 - 2.0 * num_frames * abs(positions[i] - center))
            return sharpness_weight * quality + (1.0 - sharpness_weight) * centered

        selected.append(max(indices, key=score))
    return selected


def read_video_info(path):
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"Vidéo illisible : {path}")
    fps = cap.get(cv2.CAP_PROP_FPS)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    if not (np.isfinite(fps) and fps > 0) or total <= 0:
        raise RuntimeError(f"Cadence ou nombre d'images inconnu : {path}")
    return total, fps


def sample_indices(total, count):
    return sorted({min(total - 1, int(k * total / count)) for k in range(count)})


def iter_frames(path, wanted):
    """Lit la vidéo séquentiellement et renvoie (index, image) pour les index demandés."""
    cap = cv2.VideoCapture(str(path))
    try:
        current = 0
        for target in wanted:
            while current < target:
                if not cap.grab():
                    return
                current += 1
            ok, frame = cap.read()
            if not ok:
                return
            current += 1
            yield target, frame
    finally:
        cap.release()


def scan_video(path, num_frames):
    total, fps = read_video_info(path)
    wanted = sample_indices(total, min(total, OVERSAMPLE * num_frames))
    scan = {"indices": [], "sharp": [], "positions": [], "size": None, "total": total, "fps": fps}
    for index, frame in tqdm(iter_frames(path, wanted), total=len(wanted),
                             desc=f"Analyse {path.name}", unit="img", dynamic_ncols=True):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if scan["size"] is None:
            scan["size"] = (frame.shape[1], frame.shape[0])
        scan["indices"].append(index)
        scan["sharp"].append(sharpness(shrink_width(gray, SCORE_MAX_WIDTH)))
        scan["positions"].append(index / total)
    if len(scan["indices"]) != len(wanted):
        raise RuntimeError(f"Lecture incomplète de {path} : {len(scan['indices'])}/{len(wanted)} "
                           "frames candidates décodées ; extraction arrêtée.")
    return scan


def write_frames(path, frame_indices, out_dir, ext, out_size):
    out_dir.mkdir(parents=True, exist_ok=True)
    names = {index: out_dir / f"frame_{k:05d}{ext}" for k, index in enumerate(frame_indices)}
    written = 0
    for index, frame in tqdm(iter_frames(path, frame_indices), total=len(frame_indices),
                             desc=f"Écriture {path.name}", unit="img", dynamic_ncols=True):
        if out_size is not None:
            frame = cv2.resize(frame, out_size, interpolation=cv2.INTER_AREA)
        if not cv2.imwrite(str(names[index]), frame):
            raise RuntimeError(f"Écriture de frame impossible : {names[index]}")
        written += 1
    if written != len(frame_indices):
        raise RuntimeError(f"Vidéo interrompue : {written}/{len(frame_indices)} frames écrites : {path}")
    return written


def fail(message):
    print(f"[ERREUR] {message}", file=sys.stderr)
    sys.exit(1)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--video", required=True, nargs="+", type=Path,
                    help="Une ou plusieurs vidéos. Plusieurs vidéos : un sous-dossier par vidéo.")
    ap.add_argument("--out", required=True, type=Path, help="Dossier de sortie.")
    ap.add_argument("--num-frames", required=True, type=int,
                    help="Nombre d'images visé par vidéo.")
    ap.add_argument("--width", type=int, default=0,
                    help="Largeur de sortie (0 = taille source). Nécessite --height.")
    ap.add_argument("--height", type=int, default=0,
                    help="Hauteur de sortie (0 = taille source). Nécessite --width.")
    ap.add_argument("--format", choices=["jpg", "png"], default="jpg")
    ap.add_argument("--sharpness-weight", type=float, default=SHARPNESS_WEIGHT,
                    help="Poids de la netteté dans chaque tranche, entre 0 et 1 (défaut : 0.7).")
    ap.add_argument("--index-csv", type=Path,
                    help="Écrit la correspondance entre noms et indices vidéo ; une seule vidéo.")
    ap.add_argument("--reset", action="store_true",
                    help="Supprime les frame_* existantes dans les dossiers de sortie.")
    args = ap.parse_args()

    if args.num_frames < 1:
        ap.error("--num-frames doit être >= 1.")
    if not np.isfinite(args.sharpness_weight) or not 0 <= args.sharpness_weight <= 1:
        ap.error("--sharpness-weight doit être fini et compris entre 0 et 1.")
    if args.width < 0 or args.height < 0 or (args.width > 0) != (args.height > 0):
        ap.error("--width et --height doivent être fournis ensemble et positifs.")
    if args.index_csv and len(args.video) != 1:
        ap.error("--index-csv nécessite une seule vidéo.")
    for video in args.video:
        if not video.is_file():
            ap.error(f"Vidéo introuvable : {video}")
    stems = [video.stem for video in args.video]
    if len(set(stems)) != len(stems):
        ap.error("Deux vidéos ont le même nom (sans extension) : renommer.")

    ext = f".{args.format}"
    out_dirs = [args.out] if len(args.video) == 1 else [args.out / stem for stem in stems]
    existing = [p for d in out_dirs if d.is_dir() for p in d.glob(f"frame_*{ext}")]
    if existing and not args.reset:
        fail(f"{len(existing)} image(s) déjà présentes ; utiliser --reset pour les remplacer.")
    for p in existing:
        p.unlink()

    scans = [scan_video(video, args.num_frames) for video in args.video]
    sizes = {scan["size"] for scan in scans}
    if args.width:
        out_size = (args.width, args.height)
    elif len(sizes) == 1:
        out_size = None
    else:
        fail(f"Tailles de vidéo différentes {sorted(sizes)} : utiliser --width et --height.")
    shown = out_size or next(iter(sizes))
    print(f"[taille] {shown[0]}x{shown[1]}")

    selections = []
    for video, scan in zip(args.video, scans):
        chosen = select_indices(scan["sharp"], scan["positions"], args.num_frames,
                                sharpness_weight=args.sharpness_weight)
        frame_indices = [scan["indices"][k] for k in chosen]
        selections.append(frame_indices)
        print(f"[sélection] {video.name} : {len(frame_indices)} / {args.num_frames} image(s)")
        if len(frame_indices) < args.num_frames:
            print(f"[AVERTISSEMENT] {video.name} : moins d'images que demandé "
                  "(vidéo courte ou lecture incomplète).")
        if len(frame_indices) > 1:
            position, gap = max(enumerate(b - a for a, b in zip(frame_indices, frame_indices[1:])),
                                key=lambda item: item[1])
            print(f"[sélection] Écart maximal : {gap} frames vidéo (~{gap / scan['fps']:.2f} s "
                  f"à cadence moyenne), frame_{position:05d}{ext} -> frame_{position + 1:05d}{ext}.")

    for video, frame_indices, out_dir in zip(args.video, selections, out_dirs):
        written = write_frames(video, frame_indices, out_dir, ext, out_size)
        print(f"[OK] {video.name} : {written} image(s) -> {out_dir}")
        if args.index_csv:
            with args.index_csv.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.writer(stream)
                writer.writerow(("image", "video_frame_index"))
                for k, index in enumerate(frame_indices):
                    writer.writerow((f"frame_{k:05d}{ext}", index))
            print(f"[index] Correspondance des frames : {args.index_csv}")


if __name__ == "__main__":
    main()
