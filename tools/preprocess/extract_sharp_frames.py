#!/usr/bin/env python3
"""Extrait des images nettes et réparties dans le temps depuis une ou plusieurs vidéos.

Inspiré de video_to_ply (scripts/extract_frames.sh et scripts/filter_frames.py) :
- sur-échantillonnage de OVERSAMPLE × NUM_FRAMES candidates réparties sur la durée ;
- filtre de flou : seuil adaptatif (30e centile de la netteté, variance du Laplacien) ;
- filtre de doublons : écart moyen avec la dernière image gardée < 0,5 × médiane des écarts ;
- équilibrage : la durée est découpée en NUM_FRAMES tranches, on garde la plus nette de chaque tranche.

Sortie : OUT/frame_00000.jpg, ou OUT/<nom_vidéo>/frame_00000.jpg si plusieurs vidéos.
Dépendances : opencv-python-headless, numpy, tqdm.
"""

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np
from tqdm import tqdm

OVERSAMPLE = 3
BLUR_PERCENTILE = 30.0
DIFF_FACTOR = 0.5
SCORE_MAX_WIDTH = 960
THUMB_WIDTH = 160


def sharpness(gray):
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def mean_abs_diff(a, b):
    return float(cv2.absdiff(a, b).mean())


def shrink_width(gray, max_width):
    height, width = gray.shape[:2]
    if width <= max_width:
        return gray
    return cv2.resize(gray, (max_width, round(height * max_width / width)),
                      interpolation=cv2.INTER_AREA)


def select_indices(sharp, thumbs, positions, num_frames, blur_percentile=BLUR_PERCENTILE):
    """Retourne les positions (triées) des candidates retenues.

    sharp : netteté de chaque candidate ; thumbs : miniatures grises pour les doublons ;
    positions : position temporelle normalisée dans [0, 1[ ; num_frames : nombre visé.
    """
    if len(sharp) == 0:
        return []
    sharp = np.asarray(sharp, dtype=float)
    blur_threshold = np.percentile(sharp, blur_percentile)
    kept = [i for i in range(len(sharp)) if sharp[i] >= blur_threshold]

    diffs = [mean_abs_diff(thumbs[b], thumbs[a]) for a, b in zip(kept, kept[1:])]
    diff_threshold = DIFF_FACTOR * float(np.median(diffs)) if diffs else 0.0

    unique = []
    for i in kept:
        if unique and mean_abs_diff(thumbs[i], thumbs[unique[-1]]) < diff_threshold:
            continue
        unique.append(i)

    best = {}
    for i in unique:
        bucket = min(int(positions[i] * num_frames), num_frames - 1)
        if bucket not in best or sharp[i] > sharp[best[bucket]]:
            best[bucket] = i
    return sorted(best.values())


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
    total, _ = read_video_info(path)
    wanted = sample_indices(total, min(total, OVERSAMPLE * num_frames))
    scan = {"indices": [], "sharp": [], "thumbs": [], "positions": [], "size": None}
    for index, frame in tqdm(iter_frames(path, wanted), total=len(wanted),
                             desc=f"Analyse {path.name}", unit="img", dynamic_ncols=True):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if scan["size"] is None:
            scan["size"] = (frame.shape[1], frame.shape[0])
        thumb_height = max(1, round(gray.shape[0] * THUMB_WIDTH / gray.shape[1]))
        scan["indices"].append(index)
        scan["sharp"].append(sharpness(shrink_width(gray, SCORE_MAX_WIDTH)))
        scan["thumbs"].append(cv2.resize(gray, (THUMB_WIDTH, thumb_height),
                                         interpolation=cv2.INTER_AREA))
        scan["positions"].append(index / total)
    return scan


def write_frames(path, frame_indices, out_dir, ext, out_size):
    out_dir.mkdir(parents=True, exist_ok=True)
    names = {index: out_dir / f"frame_{k:05d}{ext}" for k, index in enumerate(frame_indices)}
    written = 0
    for index, frame in tqdm(iter_frames(path, frame_indices), total=len(frame_indices),
                             desc=f"Écriture {path.name}", unit="img", dynamic_ncols=True):
        if out_size is not None:
            frame = cv2.resize(frame, out_size, interpolation=cv2.INTER_AREA)
        cv2.imwrite(str(names[index]), frame)
        written += 1
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
    ap.add_argument("--reset", action="store_true",
                    help="Supprime les frame_* existantes dans les dossiers de sortie.")
    args = ap.parse_args()

    if args.num_frames < 1:
        ap.error("--num-frames doit être >= 1.")
    if args.width < 0 or args.height < 0 or (args.width > 0) != (args.height > 0):
        ap.error("--width et --height doivent être fournis ensemble et positifs.")
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
        chosen = select_indices(scan["sharp"], scan["thumbs"], scan["positions"],
                                args.num_frames)
        frame_indices = [scan["indices"][k] for k in chosen]
        selections.append(frame_indices)
        print(f"[sélection] {video.name} : {len(frame_indices)} / {args.num_frames} image(s)")
        if len(frame_indices) < args.num_frames:
            print(f"[AVERTISSEMENT] {video.name} : moins d'images que demandé "
                  "(vidéo courte ou images trop floues/identiques).")

    for video, frame_indices, out_dir in zip(args.video, selections, out_dirs):
        written = write_frames(video, frame_indices, out_dir, ext, out_size)
        print(f"[OK] {video.name} : {written} image(s) -> {out_dir}")


if __name__ == "__main__":
    main()
