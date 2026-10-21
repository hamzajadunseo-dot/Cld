"""SQLite index + content-addressed cache.

Tables
  files   one row per file on disk (change detection by size+mtime, identity by sha1)
  assets  one row per usable visual: a photo, one shot of a clip, or a music track
  cache   expensive results keyed by content hash (faces, CLIP vectors, captions,
          scene lists), so moving/renaming a file never costs a second API call
"""
import json
import sqlite3
import time
from pathlib import Path

import numpy as np

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path        TEXT PRIMARY KEY,
    sha1        TEXT,
    size        INTEGER,
    mtime       REAL,
    kind        TEXT,
    status      TEXT,
    error       TEXT,
    indexed_at  REAL
);
CREATE TABLE IF NOT EXISTS assets (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_key     TEXT UNIQUE,
    path          TEXT,
    sha1          TEXT,
    type          TEXT,
    shot_index    INTEGER,
    start_sec     REAL,
    end_sec       REAL,
    duration      REAL,
    width         INTEGER,
    height        INTEGER,
    orientation   TEXT,
    category      TEXT,
    folder_person TEXT,
    event         TEXT,
    year          INTEGER,
    date          TEXT,
    location      TEXT,
    mood          TEXT,
    shot_type     TEXT,
    image_kind    TEXT,
    people        TEXT,
    people_scores TEXT,
    faces         TEXT,
    face_count    INTEGER,
    caption       TEXT,
    tags          TEXT,
    license       TEXT,
    author        TEXT,
    source_page   TEXT,
    source_desc   TEXT,
    quality       REAL,
    clip_vec      BLOB,
    updated_at    REAL
);
CREATE INDEX IF NOT EXISTS idx_assets_path ON assets(path);
CREATE INDEX IF NOT EXISTS idx_assets_type ON assets(type);
CREATE TABLE IF NOT EXISTS cache (
    kind   TEXT,
    key    TEXT,
    value  BLOB,
    PRIMARY KEY (kind, key)
);
"""

JSON_COLS = {"people", "people_scores", "faces", "tags"}


class IndexDB:
    def __init__(self, path: Path):
        self.conn = sqlite3.connect(str(path), check_same_thread=False, timeout=120)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(assets)")}
        if "dup_group" not in cols:  # near-duplicate cluster id (burst shots, crops of the same photo)
            self.conn.execute("ALTER TABLE assets ADD COLUMN dup_group INTEGER")
        self.conn.commit()

    # ------------------------------------------------------------ cache
    def cache_get(self, kind: str, key: str):
        row = self.conn.execute("SELECT value FROM cache WHERE kind=? AND key=?", (kind, key)).fetchone()
        return None if row is None else row[0]

    def cache_put(self, kind: str, key: str, value) -> None:
        self.conn.execute("INSERT OR REPLACE INTO cache(kind,key,value) VALUES(?,?,?)", (kind, key, value))

    def cache_get_json(self, kind: str, key: str):
        v = self.cache_get(kind, key)
        return None if v is None else json.loads(v)

    def cache_put_json(self, kind: str, key: str, obj) -> None:
        self.cache_put(kind, key, json.dumps(obj, ensure_ascii=False))

    def cache_get_vec(self, kind: str, key: str):
        v = self.cache_get(kind, key)
        return None if v is None else np.frombuffer(v, dtype=np.float16).astype(np.float32)

    def cache_put_vec(self, kind: str, key: str, vec: np.ndarray) -> None:
        self.cache_put(kind, key, np.asarray(vec, dtype=np.float16).tobytes())

    # ------------------------------------------------------------ files
    def get_file(self, path: str):
        return self.conn.execute("SELECT * FROM files WHERE path=?", (path,)).fetchone()

    def all_file_paths(self) -> set[str]:
        return {r[0] for r in self.conn.execute("SELECT path FROM files")}

    def upsert_file(self, path, sha1, size, mtime, kind, status, error=None):
        self.conn.execute(
            "INSERT OR REPLACE INTO files(path,sha1,size,mtime,kind,status,error,indexed_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (path, sha1, size, mtime, kind, status, error, time.time()),
        )

    def remove_file(self, path: str) -> None:
        self.conn.execute("DELETE FROM files WHERE path=?", (path,))
        self.conn.execute("DELETE FROM assets WHERE path=?", (path,))

    # ------------------------------------------------------------ assets
    def replace_assets_for_path(self, path: str, rows: list[dict]) -> None:
        self.conn.execute("DELETE FROM assets WHERE path=?", (path,))
        for row in rows:
            self.insert_asset(row)

    def insert_asset(self, row: dict) -> None:
        row = {k: (json.dumps(v, ensure_ascii=False) if k in JSON_COLS and v is not None else v)
               for k, v in row.items()}
        row["updated_at"] = time.time()
        cols = ",".join(row)
        qs = ",".join("?" * len(row))
        self.conn.execute(f"INSERT INTO assets({cols}) VALUES({qs})", list(row.values()))

    def update_asset(self, asset_id: int, **fields) -> None:
        fields = {k: (json.dumps(v, ensure_ascii=False) if k in JSON_COLS and v is not None else v)
                  for k, v in fields.items()}
        fields["updated_at"] = time.time()
        sets = ",".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE assets SET {sets} WHERE id=?", [*fields.values(), asset_id])

    def assets(self, where: str = "1=1", params=()) -> list[dict]:
        rows = self.conn.execute(f"SELECT * FROM assets WHERE {where}", params).fetchall()
        return [row_to_dict(r) for r in rows]

    def commit(self) -> None:
        self.conn.commit()


def row_to_dict(r: sqlite3.Row) -> dict:
    d = dict(r)
    for k in JSON_COLS:
        if d.get(k):
            d[k] = json.loads(d[k])
    if d.get("clip_vec") is not None:
        d["clip_vec"] = np.frombuffer(d["clip_vec"], dtype=np.float16).astype(np.float32)
    return d
