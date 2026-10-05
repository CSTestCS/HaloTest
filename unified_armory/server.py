"""Local editor: a stdlib HTTP server for the web UI and a small JSON API."""
from __future__ import annotations

import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .catalog import Catalog
from .export import export, needed_models
from .porting.cache import is_extracted
from .profile import ProfileError, load_profile, new_profile, save_profile, validate_profile

WEB_DIR = Path(__file__).parent / "web"


def status(catalog: Catalog, profile: dict, cache: Path) -> dict:
    """Which source models this loadout needs and whether each is extracted."""
    out = []
    for gid, model in sorted(needed_models(catalog, profile)):
        out.append({"game": gid, "model": model, "ready": is_extracted(cache, catalog.game(gid), model)})
    return {"models": out}


def make_handler(catalog: Catalog, profile_path: Path, out_dir: Path, cache: Path):
    catalog_json = json.dumps(catalog.to_json()).encode()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj) -> None:
            self._send(code, json.dumps(obj).encode())

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/api/catalog":
                return self._send(200, catalog_json)
            if path == "/api/profile":
                try:
                    prof = load_profile(profile_path, catalog) if profile_path.exists() else new_profile()
                except ProfileError:
                    prof = new_profile()
                return self._json(200, prof)
            name = "index.html" if path in ("/", "") else path.lstrip("/")
            target = (WEB_DIR / name).resolve()
            if WEB_DIR.resolve() not in target.parents or not target.is_file():
                return self._json(404, {"error": "not found"})
            return self._send(200, target.read_bytes(), mimetypes.guess_type(target.name)[0] or "application/octet-stream")

        def do_POST(self):
            path = self.path.split("?", 1)[0]
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length > 1_000_000:
                    raise ProfileError("request too large")
                prof = validate_profile(json.loads(self.rfile.read(length) or b"{}"), catalog)
            except (ProfileError, json.JSONDecodeError) as e:
                return self._json(400, {"error": str(e)})
            if path == "/api/status":
                return self._json(200, status(catalog, prof, cache))
            if path == "/api/profile":
                save_profile(prof, profile_path)
                return self._json(200, {"saved": str(profile_path)})
            if path == "/api/build":
                save_profile(prof, profile_path)
                r = export(catalog, prof, out_dir, cache)
                return self._json(200, {"out_dir": str(out_dir.resolve()), "built": list(r["built"]),
                                        "waiting": r["waiting"], "errors": r["errors"], "extract": r["extract"],
                                        "warnings": {g: p["warnings"] for g, p in r["built"].items()}})
            return self._json(404, {"error": "not found"})

    return Handler


def serve(catalog: Catalog, profile_path: Path, out_dir: Path, cache: Path, host: str = "127.0.0.1", port: int = 8642):
    httpd = ThreadingHTTPServer((host, port), make_handler(catalog, Path(profile_path), Path(out_dir), Path(cache)))
    print(f"Unified Armory editor: http://{host}:{httpd.server_address[1]}/  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
