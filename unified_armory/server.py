"""Local editor: a stdlib HTTP server for the web UI and a small JSON API."""
from __future__ import annotations

import json
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import autopilot
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


class Job:
    """The one apply/restore task that can run at a time, with its log for the page to poll."""

    def __init__(self):
        self.lock = threading.Lock()
        self.state, self.kind, self.lines, self.report = "idle", None, [], None

    def start(self, kind: str, fn) -> bool:
        with self.lock:
            if self.state == "running":
                return False
            self.state, self.kind, self.lines, self.report = "running", kind, [], None

        def run():
            try:
                self.report = fn(self.log)
                self.state = "done"
            except Exception as e:  # surfaced to the page, not swallowed
                self.log(f"Error: {e}")
                self.state = "error"
        threading.Thread(target=run, daemon=True).start()
        return True

    def log(self, line: str) -> None:
        self.lines.append(line)

    def to_json(self, since: int = 0) -> dict:
        r = self.report
        return {"state": self.state, "kind": self.kind, "lines": self.lines[since:], "total": len(self.lines),
                "report": r.to_json() if hasattr(r, "to_json") else r}


def load_settings(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


def make_handler(catalog: Catalog, profile_path: Path, out_dir: Path, cache: Path,
                 settings_path: Path | None = None, runner=None):
    catalog_json = json.dumps(catalog.to_json()).encode()
    settings_path = Path(settings_path or Path(profile_path).with_name("armory_settings.json"))
    job = Job()

    def environment():
        return autopilot.detect(catalog, load_settings(settings_path))

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
            if path == "/api/env":
                env = environment()
                return self._json(200, {**env.to_json(), "windows": autopilot.sys.platform == "win32" or runner is not None})
            if path == "/api/job":
                since = int((self.path.split("since=", 1) + ["0"])[1].split("&")[0] or 0)
                return self._json(200, job.to_json(since))
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
                body = json.loads(self.rfile.read(length) or b"{}")
                if path == "/api/settings":
                    clean = {"mcc": str(body.get("mcc") or ""), "kits": {g: str(p) for g, p in (body.get("kits") or {}).items()
                                                                       if g in catalog.games and p}}
                    settings_path.write_text(json.dumps(clean, indent=2))
                    return self._json(200, environment().to_json())
                if path == "/api/restore":
                    env = environment()
                    if not env.mcc:
                        return self._json(400, {"error": "MCC folder not found; set it in Settings"})
                    ok = job.start("restore", lambda log: autopilot.restore(catalog, env.mcc, log))
                    return self._json(200 if ok else 409, {"started": ok})
                prof = validate_profile(body, catalog)
            except (ProfileError, json.JSONDecodeError) as e:
                return self._json(400, {"error": str(e)})
            if path == "/api/apply":
                save_profile(prof, profile_path)
                env = environment()
                ok = job.start("apply", lambda log: autopilot.apply(catalog, prof, env, cache, out_dir, log=log, runner=runner))
                return self._json(200 if ok else 409, {"started": ok})
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


def serve(catalog: Catalog, profile_path: Path, out_dir: Path, cache: Path, host: str = "127.0.0.1", port: int = 8642,
          open_browser: bool = False):
    try:
        httpd = ThreadingHTTPServer((host, port), make_handler(catalog, Path(profile_path), Path(out_dir), Path(cache)))
    except OSError:  # port taken (e.g. already running): let the OS pick one
        httpd = ThreadingHTTPServer((host, 0), make_handler(catalog, Path(profile_path), Path(out_dir), Path(cache)))
    url = f"http://{host}:{httpd.server_address[1]}/"
    print(f"Unified Armory is running at {url}\nKeep this window open while you use it. Close it to quit.", flush=True)
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
