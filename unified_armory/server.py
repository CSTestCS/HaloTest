"""Local editor: a stdlib HTTP server for the web UI and a small JSON API."""
from __future__ import annotations

import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .backends import build_all
from .catalog import Catalog
from .export import export
from .profile import ProfileError, save_profile, validate_profile
from .resolver import resolve_all

WEB_DIR = Path(__file__).parent / "web"


def make_handler(catalog: Catalog, profile_path: Path, out_dir: Path):
    catalog_json = json.dumps(catalog.to_json()).encode()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # quieter console
            pass

        def _send(self, status: int, body: bytes, ctype: str = "application/json") -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, obj) -> None:
            self._send(status, json.dumps(obj).encode())

        def _body(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length > 1_000_000:
                raise ProfileError("request too large")
            return json.loads(self.rfile.read(length) or b"{}")

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/api/catalog":
                return self._send(200, catalog_json)
            if path == "/api/profile":
                from .profile import load_profile, new_profile
                prof = load_profile(profile_path, catalog) if profile_path.exists() else new_profile()
                return self._json(200, prof)
            name = "index.html" if path in ("/", "") else path.lstrip("/")
            target = (WEB_DIR / name).resolve()
            if WEB_DIR.resolve() not in target.parents or not target.is_file():
                return self._json(404, {"error": "not found"})
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            return self._send(200, target.read_bytes(), ctype)

        def do_POST(self):
            path = self.path.split("?", 1)[0]
            try:
                prof = validate_profile(self._body(), catalog)
            except (ProfileError, json.JSONDecodeError) as e:
                return self._json(400, {"error": str(e)})
            if path == "/api/resolve":
                return self._json(200, {"resolved": resolve_all(catalog, prof), "plans": build_all(catalog, prof)})
            if path == "/api/profile":
                save_profile(prof, profile_path)
                return self._json(200, {"saved": str(profile_path)})
            if path == "/api/export":
                save_profile(prof, profile_path)
                plans = export(catalog, prof, out_dir)
                return self._json(200, {"out_dir": str(out_dir.resolve()),
                                        "edits": {g: len(p["edits"]) for g, p in plans.items()}})
            return self._json(404, {"error": "not found"})

    return Handler


def serve(catalog: Catalog, profile_path: Path, out_dir: Path, host: str = "127.0.0.1", port: int = 8642):
    httpd = ThreadingHTTPServer((host, port), make_handler(catalog, Path(profile_path), Path(out_dir)))
    print(f"Unified Armory editor: http://{host}:{httpd.server_address[1]}/  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
