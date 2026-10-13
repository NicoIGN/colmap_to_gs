#!/usr/bin/env python3

import argparse
import http.server
import math
import mimetypes
import os
import webbrowser
from functools import partial
from pathlib import Path
from urllib.parse import quote, unquote, urlencode, urlsplit


HOST = "127.0.0.1"
ROOT = Path(__file__).resolve().parent
HTML_DIR = ROOT / "html"


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, splat_file=None, **kwargs):
        self.splat_file = splat_file
        self.splat_path = (
            f"/__local_splat__/{splat_file.name}"
            if splat_file is not None
            else None
        )
        super().__init__(*args, directory=str(HTML_DIR), **kwargs)

    def send_head(self):
        request_path = unquote(urlsplit(self.path).path)

        if self.splat_file is None or request_path != self.splat_path:
            return super().send_head()

        # Sert uniquement le fichier fourni au lancement.
        try:
            file = self.splat_file.open("rb")
        except OSError:
            self.send_error(404, "Fichier splat inaccessible")
            return None

        try:
            stat = os.fstat(file.fileno())
            content_type = (
                mimetypes.guess_type(self.splat_file.name)[0]
                or "application/octet-stream"
            )

            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(stat.st_size))
            self.send_header(
                "Last-Modified",
                self.date_time_string(stat.st_mtime),
            )
            self.end_headers()
            return file
        except Exception:
            file.close()
            raise

    def log_message(self, format, *args):
        print(f"[HTTP] {self.address_string()} - {format % args}")


def parse_camera_pos(value):
    """Valide position, cible et vecteur haut optionnel."""
    error_message = (
        "--camera-pos attend 6 ou 9 nombres finis : "
        "px,py,pz,tx,ty,tz[,ux,uy,uz]"
    )

    try:
        values = [float(part.strip()) for part in value.split(",")]
    except ValueError:
        raise argparse.ArgumentTypeError(error_message) from None

    if len(values) not in (6, 9) or not all(
        math.isfinite(v) for v in values
    ):
        raise argparse.ArgumentTypeError(error_message)

    position = values[:3]
    target = values[3:6]

    if math.dist(position, target) < 1e-6:
        raise argparse.ArgumentTypeError(
            "La position caméra et la cible doivent être différentes."
        )

    if len(values) == 9:
        up = values[6:9]

        if math.hypot(*up) < 1e-6:
            raise argparse.ArgumentTypeError(
                "Le vecteur haut de la caméra (ux,uy,uz) "
                "doit être non nul."
            )

    return ",".join(str(v) for v in values)


def main():
    parser = argparse.ArgumentParser(
        description="Démarre le Gaussian Splat Viewer."
    )
    parser.add_argument(
        "splat",
        nargs="?",
        type=Path,
        help="Fichier splat local à charger (optionnel).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port HTTP (8000 par défaut).",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Ne pas ouvrir automatiquement le navigateur.",
    )
    parser.add_argument(
        "--flip-up",
        action="store_true",
        help="Active l'option flipUp au chargement.",
    )
    parser.add_argument(
        "--camera-pos",
        type=parse_camera_pos,
        metavar="px,py,pz,tx,ty,tz[,ux,uy,uz]",
        help=(
            "Position caméra puis cible, avec vecteur haut optionnel : "
            "6 ou 9 nombres séparés par des virgules. "
            "Utiliser --camera-pos=\"...\" si la première valeur "
            "est négative."
        ),
    )
    args = parser.parse_args()

    if not 1 <= args.port <= 65535:
        parser.error("Le port doit être compris entre 1 et 65535.")

    if not HTML_DIR.is_dir():
        parser.error(f"Dossier introuvable : {HTML_DIR}")

    if not (HTML_DIR / "index.html").is_file():
        parser.error(f"Fichier introuvable : {HTML_DIR / 'index.html'}")

    splat_file = None
    if args.splat is not None:
        splat_file = args.splat.expanduser().resolve()
        if not splat_file.is_file():
            parser.error(f"Fichier splat introuvable : {splat_file}")

    handler = partial(Handler, splat_file=splat_file)

    try:
        server = http.server.ThreadingHTTPServer(
            (HOST, args.port), handler
        )
    except OSError as exc:
        parser.error(f"Impossible de démarrer le serveur : {exc}")

    with server:
        url = f"http://{HOST}:{args.port}/"

        params = {}
        if splat_file is not None:
            params["file"] = "/__local_splat__/" + quote(
                splat_file.name, safe=""
            )
        if args.flip_up:
            params["flipUp"] = "1"
        if args.camera_pos is not None:
            params["camera_pos"] = args.camera_pos

        if params:
            url += "?" + urlencode(params)

        print()
        print("========================================")
        print(" Gaussian Splat Viewer")
        print("========================================")
        print(f" Dossier : {HTML_DIR}")
        if splat_file is not None:
            print(f" Splat   : {splat_file}")
        print(f" FlipUp  : {'oui' if args.flip_up else 'non'}")
        if args.camera_pos is not None:
            print(f" Caméra  : {args.camera_pos}")
        print(f" URL     : {url}")
        print(" Ctrl+C pour arrêter le serveur")
        print("========================================")
        print()

        try:
            if not args.no_browser:
                webbrowser.open(url)

            server.serve_forever()
        except KeyboardInterrupt:
            print("\nArrêt du serveur.")


if __name__ == "__main__":
    main()