"""Loopback-only offline editor; no external service or JavaScript dependency."""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
from pathlib import Path
import secrets
import threading
from urllib.parse import parse_qs, urlparse
import uuid
import webbrowser
from urllib.request import urlopen
from urllib.error import URLError

from PIL import Image

from . import __version__
from .catalog import Catalog
from .color import srgb_profile
from .develop import export_developed
from .edits import Edits, load_sidecar, save_sidecar
from .pipeline import LOOKS, render_pixels, resize_camera
from .raw import ProcessingError, camera_identity, decode, validate_source

WEB = Path(__file__).parent / "resources" / "web"
PROJECT = Path(__file__).resolve().parents[2]


class EditorApp:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.catalog = Catalog(self.root / ".s2g" / "catalog.sqlite3")
        self.token = secrets.token_urlsafe(32)
        self.cache = OrderedDict()
        self.cache_lock = threading.RLock()
        self.jobs_lock = threading.RLock()
        self.jobs = {}
        self.cancel_events = {}
        self.export_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="s2g-export")
        samples = self.root / "samples"
        if samples.exists():
            self.import_path(samples)

    def within_root(self, path):
        path = Path(path).resolve()
        if not path.is_relative_to(self.root):
            raise ProcessingError(f"This prototype accepts paths inside {self.root}.")
        return path

    def source(self, asset_id):
        if type(asset_id) is not int or asset_id <= 0:
            raise ProcessingError("Invalid photo ID.")
        return validate_source(self.within_root(self.catalog.source(asset_id)))

    def import_path(self, path):
        path = self.within_root(path)
        if not path.exists():
            raise ProcessingError("Import path does not exist.")
        candidates = sorted((p for p in path.iterdir() if p.is_file() and p.suffix.lower() == ".arw"), key=lambda p: p.name.lower()) if path.is_dir() else [path]
        if len(candidates) > 1000:
            raise ProcessingError("Import up to 1000 photos at a time.")
        result = {"imported": [], "errors": []}
        for source in candidates:
            try:
                source = validate_source(self.within_root(source))
                camera_identity(source)
                result["imported"].append(self.catalog.add(source))
            except (ProcessingError, OSError, ValueError) as exc:
                result["errors"].append({"name": source.name, "error": str(exc)})
        return result

    def proxy(self, asset_id):
        source = self.source(asset_id)
        stamp = (source.stat().st_mtime_ns, source.stat().st_size)
        with self.cache_lock:
            key = (asset_id, stamp)
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
            camera, metadata = decode(source, sensor=True)
            camera = resize_camera(camera, 1400)
            self.cache[key] = (camera, metadata)
            while len(self.cache) > 2:
                self.cache.popitem(last=False)
            return camera, metadata

    def asset(self, asset_id):
        source = self.source(asset_id)
        camera, metadata = self.proxy(asset_id)
        return {"id": asset_id, "name": source.name, "metadata": metadata,
                "edits": load_sidecar(source).to_dict(), "proxy_size": list(camera.shape[:2])}

    def preview(self, asset_id, values, max_size=1400):
        if type(max_size) is not int or not 64 <= max_size <= 1400:
            raise ProcessingError("Preview size must be 64–1400 pixels.")
        edit = Edits.from_dict(values)
        camera, metadata = self.proxy(asset_id)
        rgb = render_pixels(camera, metadata, edit, max_size=max_size)
        stream = BytesIO()
        Image.fromarray(rgb).save(stream, format="JPEG", quality=95, subsampling=0, icc_profile=srgb_profile())
        return stream.getvalue()

    def job_snapshot(self, job_id):
        with self.jobs_lock:
            if job_id not in self.jobs:
                raise ProcessingError("Export job not found.")
            return json.loads(json.dumps(self.jobs[job_id]))

    def enqueue(self, values):
        ids = values.get("ids")
        if not isinstance(ids, list) or not 1 <= len(ids) <= 1000 or len(set(ids)) != len(ids):
            raise ProcessingError("Choose 1–1000 distinct photos.")
        sources = [self.source(asset_id) for asset_id in ids]
        edit = Edits.from_dict(values.get("edits", {}))
        folder = self.within_root(values.get("folder", str(self.root / "output" / "editor")))
        fmt = values.get("format", "jpg")
        if fmt not in ("jpg", "tif"):
            raise ProcessingError("Export format must be jpg or tif.")
        max_size = values.get("max_size")
        if max_size is not None and (type(max_size) is not int or not 64 <= max_size <= 16000):
            raise ProcessingError("Export size must be 64–16000 pixels or original.")
        with self.jobs_lock:
            if any(j["state"] in ("queued", "running") for j in self.jobs.values()):
                raise ProcessingError("Finish or cancel the active export first.")
            job_id = uuid.uuid4().hex[:12]
            self.jobs[job_id] = {"id": job_id, "state": "queued", "total": len(ids), "completed": 0,
                                 "progress": 0, "files": [], "folder": str(folder)}
            self.cancel_events[job_id] = threading.Event()
        self.export_pool.submit(self._run_export, job_id, sources, folder, fmt, edit, max_size)
        return self.job_snapshot(job_id)

    def _run_export(self, job_id, sources, folder, fmt, edit, max_size):
        event = self.cancel_events[job_id]
        with self.jobs_lock:
            job = self.jobs[job_id]
            job["state"] = "running"
        suffix = "positive" if "positive" in edit.look else "negative" if "negative" in edit.look else "neutral"
        if edit.look.endswith("_v2"):
            suffix += "-v2"
        try:
            for index, source in enumerate(sources):
                if event.is_set():
                    break
                output = folder / f"{source.stem}-{suffix}.{fmt}"
                def progress(value):
                    with self.jobs_lock:
                        job["progress"] = (index + value) / len(sources)
                try:
                    report = export_developed(source, output, edit, max_size=max_size, progress=progress, cancelled=event.is_set)
                    item = {"name": source.name, "status": "done", "output": str(output), "sha256": report["output"]["sha256"]}
                except Exception as exc:
                    item = {"name": source.name, "status": "cancelled" if event.is_set() else "failed", "error": str(exc)}
                with self.jobs_lock:
                    job["files"].append(item)
                    job["completed"] = index + 1
                self.catalog.save_job(self.job_snapshot(job_id))
            with self.jobs_lock:
                failures = sum(item["status"] == "failed" for item in job["files"])
                job["state"] = "cancelled" if event.is_set() else "completed_with_errors" if failures else "completed"
                if not event.is_set():
                    job["progress"] = 1
        except Exception as exc:
            with self.jobs_lock:
                job["state"], job["error"] = "failed", str(exc)
        finally:
            self.catalog.save_job(self.job_snapshot(job_id))


class EditorServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, address, app):
        self.app = app
        super().__init__(address, EditorHandler)


class EditorHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"S2G HTTP: {fmt % args}", flush=True)

    @property
    def app(self):
        return self.server.app

    def respond(self, body, kind="application/json; charset=utf-8", status=200):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' blob: data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # Aborted stale previews are expected while dragging sliders.

    def trusted_host(self):
        expected = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        if self.headers.get("Host") not in expected:
            raise ProcessingError("Invalid local editor host.")
        origin = self.headers.get("Origin")
        if origin and origin not in {f"http://{host}" for host in expected}:
            raise ProcessingError("Cross-origin requests are not allowed.")

    def do_GET(self):
        try:
            self.trusted_host()
            url = urlparse(self.path)
            if url.path in ("/", "/index.html"):
                body = (WEB / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", self.app.token)
                self.respond(body.encode("utf-8"), "text/html; charset=utf-8")
            elif url.path in ("/app.js", "/style.css"):
                self.respond((WEB / url.path[1:]).read_bytes(), "text/javascript; charset=utf-8" if url.path.endswith("js") else "text/css; charset=utf-8")
            elif url.path == "/api/library":
                self.respond({"assets": self.app.catalog.assets(), "presets": self.app.catalog.presets(), "root": str(self.app.root),
                              "version": __version__, "looks": [{"id": key, "label": value["label"]} for key, value in LOOKS.items()]})
            elif url.path == "/api/asset":
                self.respond(self.app.asset(int(parse_qs(url.query)["id"][0])))
            elif url.path == "/api/thumbnail":
                self.respond(self.app.preview(int(parse_qs(url.query)["id"][0]), Edits().to_dict(), max_size=320), "image/jpeg")
            elif url.path == "/api/job":
                self.respond(self.app.job_snapshot(parse_qs(url.query)["id"][0]))
            elif url.path == "/comparison" or url.path.startswith("/comparison/"):
                directory = self.app.root / "output" / "color-v03"
                name = "index.html" if url.path == "/comparison" else url.path.removeprefix("/comparison/")
                if not name or Path(name).name != name or Path(name).suffix not in (".html", ".jpg", ".json", ".css", ".js"):
                    raise ProcessingError("Invalid comparison file.")
                target = (directory / name).resolve()
                if not target.is_relative_to(directory.resolve()) or not target.is_relative_to(self.app.root):
                    raise ProcessingError("Comparison path escapes its folder.")
                if not target.is_file():
                    self.respond({"error": "Generate comparison with tools/compare_looks.py first."}, status=404)
                    return
                mime = {".html": "text/html; charset=utf-8", ".jpg": "image/jpeg", ".json": "application/json", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}[target.suffix]
                self.respond(target.read_bytes(), mime)
            else:
                self.respond({"error": "Not found"}, status=404)
        except (ProcessingError, OSError, ValueError, KeyError, TypeError) as exc:
            self.respond({"error": str(exc)}, status=400)

    def do_POST(self):
        try:
            self.trusted_host()
            if not secrets.compare_digest(self.headers.get("X-S2G-Token", ""), self.app.token):
                self.respond({"error": "Invalid editor session token"}, status=403)
                return
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 1024 * 1024:
                raise ProcessingError("Request body must be between 1 byte and 1 MB.")
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise ProcessingError("JSON request required.")
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ProcessingError("JSON object required.")
            if self.path == "/api/import":
                result = self.app.import_path(data["path"])
            elif self.path == "/api/preview":
                self.respond(self.app.preview(data["id"], data["edits"]), "image/jpeg")
                return
            elif self.path == "/api/save":
                ids = data["ids"]
                if not isinstance(ids, list) or not 1 <= len(ids) <= 1000:
                    raise ProcessingError("Choose 1–1000 photos.")
                edit = Edits.from_dict(data["edits"])
                result = {"saved": [], "errors": []}
                for asset_id in ids:
                    try:
                        save_sidecar(self.app.source(asset_id), edit)
                        result["saved"].append(asset_id)
                    except (ProcessingError, OSError, ValueError) as exc:
                        result["errors"].append({"id": asset_id, "error": str(exc)})
            elif self.path == "/api/preset":
                self.app.catalog.save_preset(data["name"], data["edits"])
                result = {"presets": self.app.catalog.presets()}
            elif self.path == "/api/export":
                result = self.app.enqueue(data)
            elif self.path == "/api/cancel":
                with self.app.jobs_lock:
                    if data["id"] not in self.app.cancel_events:
                        raise ProcessingError("Export job not found.")
                    self.app.cancel_events[data["id"]].set()
                result = self.app.job_snapshot(data["id"])
            else:
                self.respond({"error": "Not found"}, status=404)
                return
            self.respond(result)
        except (ProcessingError, OSError, ValueError, KeyError, TypeError) as exc:
            self.respond({"error": str(exc)}, status=400)


def run_server(port=8876, *, open_browser=False):
    if type(port) is not int or not 1 <= port <= 65535:
        raise ProcessingError("Port must be between 1 and 65535.")
    if open_browser:
        url = f"http://127.0.0.1:{port}"
        try:
            with urlopen(url + "/api/library", timeout=.5) as response:
                running = json.loads(response.read())
            if running.get("version") == __version__ and running.get("root") == str(PROJECT):
                webbrowser.open(url)
                print(f"Opened the running S2G editor: {url}")
                return
        except (URLError, TimeoutError, ValueError, OSError):
            pass
    app = EditorApp(PROJECT)
    try:
        with EditorServer(("127.0.0.1", port), app) as server:
            url = f"http://127.0.0.1:{port}"
            print(f"S2G {__version__} editor: {url}\nPress Ctrl+C to stop.", flush=True)
            if open_browser:
                webbrowser.open(url)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
    finally:
        for event in app.cancel_events.values():
            event.set()
        app.export_pool.shutdown(wait=True, cancel_futures=True)
