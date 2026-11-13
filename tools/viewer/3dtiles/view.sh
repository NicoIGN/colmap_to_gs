#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
command -v python3 >/dev/null 2>&1 || {
  echo "Erreur : Python 3 est requis." >&2
  exit 1
}

exec python3 - "$SCRIPT_DIR" "$@" <<'PY'
import argparse
import functools
import json
import sys
import threading
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

assets = Path(sys.argv.pop(1))
parser = argparse.ArgumentParser(
    prog="view.sh",
    description="Visualiser un tileset Gaussian Splat dans CesiumJS avec un fond OSM.",
)
parser.add_argument("tileset", type=Path, help="Chemin du tileset.json")
parser.add_argument("--port", type=int, default=8001, help="Port local (défaut : 8001 ; 0 : automatique)")
parser.add_argument("--no-open", action="store_true", help="Ne pas ouvrir le navigateur")
args = parser.parse_args()
tileset = args.tileset.expanduser().resolve()
if not tileset.is_file():
    parser.error(f"Tileset introuvable : {tileset}")
if not 0 <= args.port <= 65535:
    parser.error("--port doit être compris entre 0 et 65535.")
try:
    metadata = json.loads(tileset.read_text(encoding="utf-8"))
except (OSError, UnicodeError, json.JSONDecodeError) as exc:
    parser.error(f"Impossible de lire le tileset : {exc}")
if not isinstance(metadata, dict) or "asset" not in metadata or "root" not in metadata:
    parser.error("Le fichier doit être un tileset 3D Tiles avec asset et root.")

root = tileset.parent
page = (assets / "viewer.html").read_bytes()
config = json.dumps({"tilesetUrl": "/tiles/" + quote(tileset.name)}).encode("utf-8")


class Handler(SimpleHTTPRequestHandler):
    def serve(self, head=False):
        path = urlsplit(self.path).path
        if path in ("/", "/config.json"):
            body = page if path == "/" else config
            mime = "text/html; charset=utf-8" if path == "/" else "application/json"
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if not head:
                self.wfile.write(body)
            return
        if not path.startswith("/tiles/"):
            self.send_error(404)
            return
        relative = unquote(path[len("/tiles/"):])
        target = (root / relative).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            self.send_error(404, "Contenu absent ou hors du dossier du tileset")
            return
        self.path = "/" + quote(relative, safe="/")
        if head:
            super().do_HEAD()
        else:
            super().do_GET()

    def do_GET(self):
        self.serve()

    def do_HEAD(self):
        self.serve(head=True)

    def list_directory(self, path):
        self.send_error(403)


try:
    server = ThreadingHTTPServer(
        ("127.0.0.1", args.port),
        functools.partial(Handler, directory=str(root)),
    )
except OSError as exc:
    parser.error(f"Impossible de démarrer le serveur : {exc}")

with server:
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"Tileset : {tileset}\nVisualiseur : {url}\nArrêt : Ctrl+C", flush=True)
    if not args.no_open:
        def open_browser():
            try:
                if not webbrowser.open(url):
                    print(f"Navigateur non ouvert automatiquement ; ouvrir {url}", file=sys.stderr)
            except webbrowser.Error as exc:
                print(f"Impossible d'ouvrir le navigateur : {exc}. Ouvrir {url}", file=sys.stderr)

        threading.Timer(0.5, open_browser).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServeur arrêté.", flush=True)
PY
