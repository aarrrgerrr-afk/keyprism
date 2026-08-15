"""KeyPrism Cloud — stdlib-only backend for Railway.

Zero dependencies: runs on `python server/main.py` anywhere (locally or on
Railway).  Stores library songs + lifetime stats in SQLite.

Endpoints
---------
GET  /api/health            -> {"status": "ok", "service": "keyprism-cloud", "time": ...}
GET  /api/stats             -> {"songs_played": n, "notes_played": n}
POST /api/stats             -> body {"songs_played": n, "notes_played": n} (merge = max)
GET  /api/library           -> list of {id, name, size, notes, duration, tracks, uploaded_at}
POST /api/library           -> body [{...}, ...]  (server keeps the newest per name)
GET  /api/songs/{id}        -> raw .mid download (Content-Disposition attachment)
POST /api/songs             -> multipart upload (field "file") -> {id, name, size}
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import sqlite3
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

DB_PATH = os.environ.get("KEYPRISM_DB", os.path.join(os.path.dirname(__file__), "keyprism.db"))
HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "8000"))

# Current app version — update this when pushing a new release
APP_VERSION = "2.1.0"
UPDATE_URL = "https://github.com/aarrrgerrr-afk/keyprism/releases/latest/download/KeyPrism.exe"

_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def _init_db() -> None:
    with _lock, _connect() as con:
        con.execute(
            "CREATE TABLE IF NOT EXISTS songs ("
            " id TEXT PRIMARY KEY, name TEXT, data BLOB, size INTEGER,"
            " notes INTEGER DEFAULT 0, duration REAL DEFAULT 0, tracks INTEGER DEFAULT 0,"
            " uploaded_at REAL)"
        )
        con.execute(
            "CREATE TABLE IF NOT EXISTS stats ("
            " key TEXT PRIMARY KEY, value INTEGER)"
        )
        con.execute("INSERT OR IGNORE INTO stats VALUES ('songs_played', 0)")
        con.execute("INSERT OR IGNORE INTO stats VALUES ('notes_played', 0)")
        con.commit()


def _get_stats() -> dict:
    with _connect() as con:
        rows = dict(con.execute("SELECT key, value FROM stats"))
    return {"songs_played": rows.get("songs_played", 0), "notes_played": rows.get("notes_played", 0)}


def _merge_stats(songs: int, notes: int) -> dict:
    with _lock, _connect() as con:
        con.execute(
            "UPDATE stats SET value = MAX(value, ?) WHERE key = 'songs_played'", (int(songs),))
        con.execute(
            "UPDATE stats SET value = MAX(value, ?) WHERE key = 'notes_played'", (int(notes),))
        con.commit()
    return _get_stats()


def _list_library() -> list[dict]:
    with _connect() as con:
        rows = con.execute(
            "SELECT id, name, size, notes, duration, tracks, uploaded_at FROM songs"
            " ORDER BY uploaded_at DESC").fetchall()
    return [dict(r) for r in rows]


def _upsert_library(entries: list[dict]) -> int:
    """Server keeps the newest row per song name (clients may hold older copies)."""
    changed = 0
    with _lock, _connect() as con:
        for e in entries:
            name = str(e.get("name", ""))
            if not name:
                continue
            cur = con.execute(
                "SELECT uploaded_at, id FROM songs WHERE name = ?", (name,)).fetchone()
            ts = float(e.get("uploaded_at") or 0)
            if cur is None or ts > (cur["uploaded_at"] or 0):
                if cur is not None:
                    con.execute("DELETE FROM songs WHERE id = ?", (cur["id"],))
                con.execute(
                    "INSERT INTO songs (id, name, data, size, notes, duration, tracks, uploaded_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (str(uuid.uuid4())[:8], name, None, 0,
                     int(e.get("notes", 0) or 0), float(e.get("duration", 0) or 0),
                     int(e.get("tracks", 0) or 0), ts))
                changed += 1
        con.commit()
    return changed


def _store_song(name: str, data: bytes, meta: dict | None = None) -> dict:
    meta = meta or {}
    song_id = str(uuid.uuid4())[:8]
    with _lock, _connect() as con:
        con.execute(
            "INSERT INTO songs (id, name, data, size, notes, duration, tracks, uploaded_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (song_id, name, data, len(data),
             int(meta.get("notes", 0) or 0), float(meta.get("duration", 0) or 0),
             int(meta.get("tracks", 0) or 0), time.time()))
        con.commit()
    return {"id": song_id, "name": name, "size": len(data)}


def _get_song(song_id: str) -> tuple[str, bytes] | None:
    with _connect() as con:
        row = con.execute("SELECT name, data FROM songs WHERE id = ?", (song_id,)).fetchone()
    return (row["name"], row["data"]) if row and row["data"] is not None else None


def _parse_multipart(body: bytes, boundary: str) -> dict:
    """Return {field_name: (filename, data)} for the first file part."""
    delim = b"--" + boundary.encode()
    parts = body.split(delim)
    result = {}
    for part in parts:
        if b"\r\n\r\n" not in part:
            continue
        head, _, payload = part.partition(b"\r\n\r\n")
        payload = payload.rsplit(b"\r\n", 1)[0]
        head_text = head.decode("latin-1", "replace")
        m = re.search(r'name="([^"]+)"', head_text)
        f = re.search(r'filename="([^"]*)"', head_text)
        field = m.group(1) if m else None
        if f:
            result["_file"] = (unquote(f.group(1)) or "song.mid", payload)
        elif field:
            result[field] = payload.decode("utf-8", "replace")
    return result


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "KeyPrismCloud/1.0"

    # ---- plumbing ---------------------------------------------------------
    def log_message(self, fmt, *args):
        print("[%s] %s" % (time.strftime("%H:%M:%S"), fmt % args))

    def _send(self, code: int, body: bytes, ctype: str = "application/json", extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj):
        self._send(code, json.dumps(obj).encode())

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0) or 0)
        return self.rfile.read(length) if length else b""

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    # ---- routes -----------------------------------------------------------
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/health":
            return self._json(200, {"status": "ok", "service": "keyprism-cloud",
                                    "time": time.time()})
        if path == "/api/version":
            return self._json(200, {
                "version": APP_VERSION,
                "download_url": UPDATE_URL,
                "changelog": "UI redesign, multi-track songs, auto-update"
            })
        if path == "/api/stats":
            return self._json(200, _get_stats())
        if path == "/api/library":
            return self._json(200, {"songs": _list_library()})
        m = re.match(r"^/api/songs/([A-Za-z0-9_-]+)$", path)
        if m:
            song = _get_song(m.group(1))
            if song is None:
                return self._json(404, {"error": "not found"})
            name, data = song
            return self._send(200, data, "audio/midi",
                              {"Content-Disposition": f'attachment; filename="{name}"'})
        return self._json(404, {"error": "no such route", "path": path})

    def do_POST(self):
        path = urlparse(self.path).path
        ctype = self.headers.get("Content-Type", "")
        body = self._read_body()

        if path == "/api/stats":
            try:
                payload = json.loads(body or b"{}")
            except Exception:
                return self._json(400, {"error": "bad json"})
            return self._json(200, _merge_stats(payload.get("songs_played", 0),
                                                payload.get("notes_played", 0)))
        if path == "/api/library":
            try:
                payload = json.loads(body or b"{}")
                entries = payload.get("songs", []) if isinstance(payload, dict) else payload
            except Exception:
                return self._json(400, {"error": "bad json"})
            changed = _upsert_library(entries or [])
            return self._json(200, {"changed": changed, "songs": _list_library()})
        if path == "/api/songs" and "multipart/form-data" in ctype:
            m = re.search(r'boundary=(?:"([^"]+)"|([^;]+))', ctype)
            if not m:
                return self._json(400, {"error": "no boundary"})
            parsed = _parse_multipart(body, m.group(1) or m.group(2))
            if "_file" not in parsed:
                return self._json(400, {"error": "no file part"})
            fname, fdata = parsed["_file"]
            meta = {}
            for key in ("notes", "duration", "tracks"):
                if key in parsed:
                    try:
                        meta[key] = float(parsed[key])
                    except ValueError:
                        pass
            return self._json(200, _store_song(fname, fdata, meta))
        return self._json(404, {"error": "no such route", "path": path})


def main():
    _init_db()
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"KeyPrism Cloud listening on http://{HOST}:{PORT} (db: {DB_PATH})")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
