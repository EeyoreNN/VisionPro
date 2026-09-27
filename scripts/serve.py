#!/usr/bin/env python3
"""Local dev server for Vision Pro web projects.

Serves a folder with the MIME types Safari on visionOS expects for spatial
assets (USDZ models, EXR/HDR lighting maps) and prints the LAN address so you
can open it on Apple Vision Pro on the same Wi-Fi network.

    python3 scripts/serve.py                                 # every project + a gallery, served live
    python3 scripts/serve.py projects/aurora-overlook/site   # one project
    python3 scripts/serve.py --port 9000
"""

import argparse
import http.server
import socket
import sys
from functools import partial
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_gallery  # noqa: E402

SPATIAL_TYPES = {
    ".usdz": "model/vnd.usdz+zip",
    ".usda": "model/vnd.usda",
    ".usdc": "model/vnd.usdc",
    ".reality": "model/vnd.reality",
    ".exr": "image/aces",
    ".hdr": "image/vnd.radiance",
    ".heic": "image/heic",
    ".mjs": "text/javascript",
    ".js": "text/javascript",
    ".json": "application/json",
    ".webmanifest": "application/manifest+json",
}


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map, **SPATIAL_TYPES}
    gallery = False

    def do_GET(self):
        if self.gallery and self.path.split("?")[0] in ("/", "/index.html"):
            body = build_gallery.render_index(build_gallery.load_projects()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def translate_path(self, path):
        # Gallery mode: /<slug>/... is served live from projects/<slug>/site/...
        if self.gallery:
            parts = path.split("?")[0].split("#")[0].lstrip("/").split("/", 1)
            site = ROOT / "projects" / parts[0] / "site"
            if parts[0] and site.is_dir():
                rest = parts[1] if len(parts) > 1 else ""
                # Let the base class sanitize the path, then re-root it in the project's site/.
                inner = Path(super().translate_path("/" + rest)).relative_to(self.directory)
                return str(site / inner)
        return super().translate_path(path)

    def end_headers(self):
        # Always serve fresh files while iterating on a headset.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def lan_address():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 80))  # no packets are sent
            return s.getsockname()[0]
    except OSError:
        return "localhost"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", nargs="?", help="folder to serve (default: every project plus a gallery page)")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.folder:
        folder = Path(args.folder).resolve()
    else:
        folder = ROOT / "projects"
        Handler.gallery = True

    handler = partial(Handler, directory=str(folder))
    with http.server.ThreadingHTTPServer(("0.0.0.0", args.port), handler) as httpd:
        print(f"Serving {folder}")
        print(f"  this computer:  http://localhost:{args.port}/")
        print(f"  Vision Pro:     http://{lan_address()}:{args.port}/   (same Wi-Fi)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
