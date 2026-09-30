#!/usr/bin/env python3

import http.server
import socketserver
import webbrowser
from pathlib import Path


HOST = "127.0.0.1"
PORT = 8000

ROOT = Path(__file__).resolve().parent
HTML_DIR = ROOT / "html"


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(HTML_DIR), **kwargs)

    def log_message(self, format, *args):
        print(f"[HTTP] {self.address_string()} - {format % args}")


def main():
    if not HTML_DIR.is_dir():
        raise RuntimeError(f"Dossier introuvable : {HTML_DIR}")

    index = HTML_DIR / "index.html"
    if not index.is_file():
        raise RuntimeError(f"Fichier introuvable : {index}")

    with socketserver.ThreadingTCPServer((HOST, PORT), Handler) as server:
        url = f"http://{HOST}:{PORT}/"

        print()
        print("========================================")
        print(" Gaussian Splat Viewer")
        print("========================================")
        print(f" Dossier : {HTML_DIR}")
        print(f" URL     : {url}")
        print(" Ctrl+C pour arrêter le serveur")
        print("========================================")
        print()

        # Ouvre le navigateur après le démarrage du serveur
        webbrowser.open(url)

        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nArrêt du serveur.")


if __name__ == "__main__":
    main()
