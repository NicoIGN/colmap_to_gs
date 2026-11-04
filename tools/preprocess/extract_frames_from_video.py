#!/usr/bin/env python3
"""Extrait des images d'une vidéo et, si un GPX est fourni, les géolocalise.

- Le premier point GPX horodaté correspond à t=0 de la vidéo.
- Les images candidates sont extraites à --fps.
  * Si --fps est omis, la cadence est estimée automatiquement via ffprobe
    (avg_frame_rate, sinon r_frame_rate).
- On ne conserve que le minimum d'images respectant :
    * --max-dist : distance max (m) entre deux images conservées (GPX requis)
    * --min-fps  : cadence minimale (écart max de 1/min-fps s entre deux images)
- Positions/altitudes interpolées linéairement entre les points GPX.
- Format de sortie paramétrable : --format {jpg,png,tiff}
- Barre de progression sur extraction FFmpeg + interpolation + écriture.
- Option --reset (false par défaut) :
    * false : conserve les images existantes et ignore les collisions de noms
    * true  : supprime les frame_* existantes avant extraction

Dépendances : ffmpeg/ffprobe dans le PATH, pip install piexif tqdm.
"""

import argparse
import bisect
import datetime as dt
import math
from fractions import Fraction
from pathlib import Path
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET

import piexif
from tqdm import tqdm

EARTH_RADIUS = 6371008.8  # m


# ---------------------------------------------------------------- GPX -----

def parse_time(text: str) -> dt.datetime:
    value = dt.datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError(f"Fuseau horaire absent du timestamp : {text}")
    return value.astimezone(dt.timezone.utc)


def read_gpx(path: Path):
    root = ET.parse(path).getroot()
    points = []
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] != "trkpt":
            continue
        fields = {c.tag.rsplit("}", 1)[-1]: (c.text or "").strip() for c in el}
        if not fields.get("time"):
            continue

        t = parse_time(fields["time"])
        if points and t <= points[-1]["time"]:
            continue  # ignore doublons / retours en arrière

        lat = float(el.attrib["lat"])
        lon = float(el.attrib["lon"])
        alt = float(fields["ele"]) if fields.get("ele") else None

        if not math.isfinite(lat) or not -90 <= lat <= 90:
            raise ValueError(f"Latitude GPX invalide: {lat}")
        if not math.isfinite(lon) or not -180 <= lon <= 180:
            raise ValueError(f"Longitude GPX invalide: {lon}")
        if alt is not None and not math.isfinite(alt):
            raise ValueError("Altitude GPX invalide.")

        points.append({
            "time": t,
            "lat": lat,
            "lon": lon,
            "alt": alt,
        })

    if not points:
        raise ValueError("Aucun point GPX horodaté trouvé.")

    t0 = points[0]["time"]
    for p in points:
        p["seconds"] = (p["time"] - t0).total_seconds()

    return points


def interpolate(points, times, s: float):
    i = bisect.bisect_right(times, s)
    if i == 0:
        p = points[0]
        return p["lat"], p["lon"], p["alt"]
    if i >= len(points):
        p = points[-1]
        return p["lat"], p["lon"], p["alt"]

    a, b = points[i - 1], points[i]
    r = (s - a["seconds"]) / (b["seconds"] - a["seconds"])

    lat = a["lat"] + r * (b["lat"] - a["lat"])
    dlon = (b["lon"] - a["lon"] + 180.0) % 360.0 - 180.0
    lon = (a["lon"] + r * dlon + 180.0) % 360.0 - 180.0

    if a["alt"] is not None and b["alt"] is not None:
        alt = a["alt"] + r * (b["alt"] - a["alt"])
    else:
        alt = a["alt"] if a["alt"] is not None else b["alt"]

    return lat, lon, alt


def haversine(p, q) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (p[0], p[1], q[0], q[1]))
    h = (math.sin((la2 - la1) / 2.0) ** 2
         + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2.0) ** 2)
    return 2.0 * EARTH_RADIUS * math.asin(min(1.0, math.sqrt(h)))


# --------------------------------------------------------------- EXIF -----

def degrees_to_dms(value: float):
    units = 1_000_000
    total = round(abs(value) * 3600.0 * units)
    d, rem = divmod(total, 3600 * units)
    m, s = divmod(rem, 60 * units)
    return ((d, 1), (m, 1), (s, units))


def write_exif(path: Path, lat: float, lon: float, alt, ts: dt.datetime):
    gps = {
        piexif.GPSIFD.GPSVersionID: (2, 3, 0, 0),
        piexif.GPSIFD.GPSLatitudeRef: b"N" if lat >= 0 else b"S",
        piexif.GPSIFD.GPSLatitude: degrees_to_dms(lat),
        piexif.GPSIFD.GPSLongitudeRef: b"E" if lon >= 0 else b"W",
        piexif.GPSIFD.GPSLongitude: degrees_to_dms(lon),
        piexif.GPSIFD.GPSMapDatum: b"WGS-84",
        piexif.GPSIFD.GPSDateStamp: ts.strftime("%Y:%m:%d").encode(),
        piexif.GPSIFD.GPSTimeStamp: (
            (ts.hour, 1),
            (ts.minute, 1),
            (ts.second * 1_000_000 + ts.microsecond, 1_000_000),
        ),
    }

    if alt is not None:
        gps[piexif.GPSIFD.GPSAltitudeRef] = 0 if alt >= 0 else 1
        gps[piexif.GPSIFD.GPSAltitude] = (round(abs(alt) * 1000), 1000)

    exif_dict = piexif.load(str(path))
    exif_dict["GPS"] = gps
    exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal] = ts.strftime(
        "%Y:%m:%d %H:%M:%S").encode()
    exif_dict["Exif"][piexif.ExifIFD.SubSecTimeOriginal] = (
        f"{ts.microsecond:06d}".encode()
    )

    exif_bytes = piexif.dump(exif_dict)
    piexif.insert(exif_bytes, str(path))


# ---------------------------------------------------------- Sélection -----

def select_frames(n: int, fps: float, positions, max_dist, min_fps):
    """Sélection gloutonne: minimum d'images tout en respectant les contraintes."""
    if n == 0:
        return []
    max_gap = (1.0 / min_fps) if min_fps else math.inf
    eps = 1e-9

    def ok(k, i):
        if (i - k) / fps > max_gap + eps:
            return False
        if max_dist is not None and haversine(positions[k], positions[i]) > max_dist:
            return False
        return True

    kept = [0]
    k, i = 0, 1
    while i < n:
        if ok(k, i):
            i += 1
            continue
        k = i - 1 if i - 1 > k else i
        kept.append(k)
        i = k + 1

    if kept[-1] != n - 1:
        kept.append(n - 1)
    return kept


# --------------------------------------------------------------- Utils -----

def video_duration(ffprobe: str, video: Path):
    out = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
        check=True, capture_output=True, text=True).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return None


def parse_fraction(text: str):
    text = (text or "").strip()
    if not text:
        return None
    try:
        frac = Fraction(text)
    except Exception:
        return None
    if frac <= 0:
        return None
    return float(frac)


def detect_fps(ffprobe: str, video: Path) -> float:
    """Détecte fps via ffprobe: avg_frame_rate puis r_frame_rate."""
    out = subprocess.run(
        [
            ffprobe, "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=avg_frame_rate,r_frame_rate",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(video),
        ],
        check=True, capture_output=True, text=True
    ).stdout.strip().splitlines()

    avg = parse_fraction(out[0]) if len(out) >= 1 else None
    nominal = parse_fraction(out[1]) if len(out) >= 2 else None

    fps = avg or nominal
    if fps is None or not math.isfinite(fps) or fps <= 0:
        raise ValueError("Impossible d'estimer le fps depuis ffprobe.")
    return fps


def ffmpeg_codec_for(fmt: str):
    if fmt == "jpg":
        return ["-c:v", "mjpeg", "-q:v", "2"], "jpg"
    if fmt == "png":
        return ["-c:v", "png", "-compression_level", "3"], "png"
    if fmt == "tiff":
        return ["-c:v", "tiff"], "tiff"
    raise ValueError(f"Format inconnu: {fmt}")


def run_ffmpeg_with_progress(cmd, total=None):
    command = [cmd[0], "-progress", "pipe:1", "-nostats", *cmd[1:]]

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=None,  # garde warnings/erreurs FFmpeg visibles
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    try:
        with tqdm(total=total, desc="Extraction FFmpeg", unit="img", dynamic_ncols=True) as bar:
            for line in process.stdout:
                key, sep, value = line.strip().partition("=")
                if sep and key == "frame":
                    try:
                        count = int(value.strip())
                    except ValueError:
                        continue
                    if count > bar.n:
                        bar.update(count - bar.n)

            rc = process.wait()

        if rc != 0:
            raise subprocess.CalledProcessError(rc, command)

    except BaseException:
        if process.poll() is None:
            process.kill()
        process.wait()
        raise
    finally:
        if process.stdout:
            process.stdout.close()


# --------------------------------------------------------------- Main -----

def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--video", required=True, type=Path)
    ap.add_argument("--gpx", type=Path, default=None,
                    help="Trace GPX optionnelle (1er point = t0 vidéo).")
    ap.add_argument("--out", type=Path, default=Path("frames_out"))
    ap.add_argument("--format", choices=["jpg", "png", "tiff"], default="jpg",
                    help="Format de sortie (défaut: jpg).")
    ap.add_argument("--fps", type=float, default=None,
                    help="Cadence d'extraction des images candidates. "
                         "Si omis: estimation auto via ffprobe.")
    ap.add_argument("--max-dist", type=float, default=None,
                    help="Distance max (m) entre 2 images conservées (nécessite GPX).")
    ap.add_argument("--min-fps", type=float, default=None,
                    help="Nombre minimal d'images conservées par seconde.")
    ap.add_argument(
        "--reset",
        action="store_true",
        help="Si activé: supprime les images frame_* existantes dans --out avant extraction. "
             "Par défaut (false): conserve les existantes et n'écrase rien."
    )
    args = ap.parse_args()

    if not args.video.is_file():
        ap.error(f"Vidéo introuvable: {args.video}")

    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        ap.error("ffmpeg/ffprobe introuvables dans le PATH.")

    # FPS auto si absent
    if args.fps is None:
        try:
            args.fps = detect_fps(ffprobe, args.video)
        except Exception as e:
            ap.error(f"Impossible de détecter automatiquement le fps: {e}")
        print(f"[fps] --fps non fourni, estimation automatique: {args.fps:.6f} img/s")

    if not (math.isfinite(args.fps) and args.fps > 0):
        ap.error("--fps doit être > 0.")
    if args.min_fps is not None and not (args.min_fps > 0 and args.min_fps <= args.fps):
        ap.error("--min-fps doit être > 0 et <= --fps.")
    if args.max_dist is not None and args.max_dist <= 0:
        ap.error("--max-dist doit être > 0.")
    if args.max_dist is not None and args.gpx is None:
        ap.error("--max-dist nécessite --gpx.")
    if args.gpx is not None and not args.gpx.is_file():
        ap.error(f"GPX introuvable: {args.gpx}")

    args.out.mkdir(parents=True, exist_ok=True)

    existing = sorted(args.out.glob("frame_*.*"))
    if existing and args.reset:
        print(f"[reset] Suppression de {len(existing)} image(s) existante(s) dans {args.out}")
        for p in tqdm(existing, desc="Reset sortie", unit="img", dynamic_ncols=True):
            try:
                p.unlink()
            except Exception as e:
                ap.error(f"Impossible de supprimer {p}: {e}")
    elif existing:
        print(f"[info] {len(existing)} image(s) déjà présentes dans {args.out} (mode --reset=false).")
        print("[info] Elles sont conservées; les nouvelles images seront ajoutées sans écrasement.")

    points = read_gpx(args.gpx) if args.gpx else None
    times = [p["seconds"] for p in points] if points else None

    duration = video_duration(ffprobe, args.video)
    if points:
        duration = min(duration, times[-1]) if duration is not None else times[-1]
    max_frames = (math.floor(duration * args.fps) + 1) if duration is not None else None

    codec_args, ext = ffmpeg_codec_for(args.format)

    with tempfile.TemporaryDirectory(dir=args.out) as tmpdir:
        tmp = Path(tmpdir)

        pattern = tmp / f"cand_%09d.{ext}"
        cmd = [
            ffmpeg, "-hide_banner", "-loglevel", "warning", "-nostdin",
            "-i", str(args.video.resolve()),
            "-map", "0:v:0",
            "-vf", f"setpts=PTS-STARTPTS,fps=fps={args.fps}:start_time=0,format=yuvj420p",
            *codec_args,
            "-start_number", "0",
        ]
        if max_frames is not None:
            cmd += ["-frames:v", str(max_frames)]
        cmd += [str(pattern)]

        print(f"[1/3] Extraction des candidates à {args.fps:g} img/s en {args.format}…")
        run_ffmpeg_with_progress(cmd, total=max_frames)

        cands = sorted(tmp.glob(f"cand_*.{ext}"))
        n = len(cands)
        if n == 0:
            raise RuntimeError("FFmpeg n'a produit aucune image.")

        positions = None
        if points:
            positions = [
                interpolate(points, times, i / args.fps)
                for i in tqdm(range(n), desc="Interpolation GPS", unit="img", dynamic_ncols=True)
            ]

        kept = select_frames(n, args.fps, positions, args.max_dist, args.min_fps)
        print(f"[2/3] {n} candidates -> {len(kept)} images conservées.")

        exif_fail = 0
        written = 0
        skipped_existing = 0

        for j, i in enumerate(
            tqdm(
                kept,
                desc="Écriture images + EXIF" if points else "Écriture images",
                unit="img",
                dynamic_ncols=True
            )
        ):
            tsec = i / args.fps
            dst = args.out / f"frame_{j:06d}_t{tsec:010.3f}.{ext}"

            if dst.exists():
                skipped_existing += 1
                tqdm.write(f"[skip] {dst.name} existe déjà, ignoré.")
                continue

            shutil.move(str(cands[i]), dst)
            written += 1

            if points:
                lat, lon, alt = positions[i]
                ts = points[0]["time"] + dt.timedelta(seconds=tsec)
                try:
                    write_exif(dst, lat, lon, alt, ts)
                except Exception as e:
                    exif_fail += 1
                    tqdm.write(f"[WARN] EXIF GPS non écrit pour {dst.name}: {e}")

        print(f"[3/3] Terminé: {written} image(s) écrite(s) dans {args.out}")
        if skipped_existing:
            print(f"      {skipped_existing} image(s) ignorée(s) car déjà présentes.")
        if points:
            if exif_fail == 0:
                print("      EXIF GPS écrit sur toutes les images écrites.")
            else:
                print(f"      EXIF GPS échoué sur {exif_fail} image(s).")
                if args.format != "jpg":
                    print("      piexif.insert peut échouer sur PNG/TIFF selon les versions.")
                    print("      Si besoin: exiftool pour fiabiliser PNG/TIFF.")


if __name__ == "__main__":
    main()
