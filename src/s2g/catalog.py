"""Small SQLite catalog. Portable per-photo edits remain in JSON sidecars."""
import json
from pathlib import Path
import sqlite3
from contextlib import contextmanager

from .edits import Edits


class Catalog:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS assets(id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL);
                CREATE TABLE IF NOT EXISTS presets(name TEXT PRIMARY KEY, edits TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, report TEXT NOT NULL);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def add(self, source: Path):
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO assets(path) VALUES(?)", (str(source),))
            return db.execute("SELECT id FROM assets WHERE path=?", (str(source),)).fetchone()[0]

    def assets(self):
        with self.connect() as db:
            return [{"id": row[0], "path": row[1], "name": Path(row[1]).name}
                    for row in db.execute("SELECT id,path FROM assets ORDER BY id")]

    def source(self, asset_id):
        with self.connect() as db:
            row = db.execute("SELECT path FROM assets WHERE id=?", (asset_id,)).fetchone()
        if row is None:
            raise ValueError("Photo not found in the library.")
        return Path(row[0])

    def save_preset(self, name, values):
        if not isinstance(name, str) or not name.strip() or len(name) > 80:
            raise ValueError("Preset name must contain 1–80 characters.")
        values = Edits.from_dict(values).to_dict()
        with self.connect() as db:
            db.execute("INSERT INTO presets(name,edits) VALUES(?,?) ON CONFLICT(name) DO UPDATE SET edits=excluded.edits",
                       (name.strip(), json.dumps(values)))

    def presets(self):
        with self.connect() as db:
            return [{"name": row[0], "edits": json.loads(row[1])} for row in db.execute("SELECT name,edits FROM presets ORDER BY name")]

    def save_job(self, job):
        with self.connect() as db:
            db.execute("INSERT INTO jobs(id,report) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET report=excluded.report",
                       (job["id"], json.dumps(job)))
