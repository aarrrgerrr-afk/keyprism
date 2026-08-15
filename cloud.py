"""KeyPrism Cloud client — stdlib only (urllib), no external deps.

Talks to the KeyPrism Cloud backend (server/main.py) hosted on Railway.
"""
from __future__ import annotations

import json
import mimetypes
import uuid
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import urlencode


class CloudError(Exception):
    """Raised for network failures, HTTP errors, or bad payloads."""


class CloudClient:
    def __init__(self, base_url: str, timeout: float = 8.0):
        self.base_url = (base_url or "").rstrip("/")
        self.timeout = timeout

    # ---- low level --------------------------------------------------------
    def _request(self, method: str, path: str, body=None,
                 headers: dict | None = None) -> bytes:
        url = self.base_url + path
        req = urlrequest.Request(url, data=body, method=method, headers=headers or {})
        try:
            with urlrequest.urlopen(req, timeout=self.timeout) as resp:
                return resp.read()
        except urlerror.HTTPError as e:
            raise CloudError(f"server error {e.code}: {e.read()[:200]!r}") from e
        except Exception as e:
            raise CloudError(f"cannot reach {self.base_url} — {e}") from e

    def _json_request(self, method: str, path: str, payload=None):
        headers = {"Content-Type": "application/json"}
        body = json.dumps(payload).encode() if payload is not None else None
        raw = self._request(method, path, body, headers)
        try:
            return json.loads(raw or b"{}")
        except ValueError:
            raise CloudError("server returned non-JSON response")

    # ---- api ---------------------------------------------------------------
    def health(self) -> dict:
        return self._json_request("GET", "/api/health")

    def get_stats(self) -> dict:
        return self._json_request("GET", "/api/stats")

    def push_stats(self, songs_played: int, notes_played: int) -> dict:
        return self._json_request("POST", "/api/stats",
                                  {"songs_played": songs_played, "notes_played": notes_played})

    def list_library(self) -> list[dict]:
        data = self._json_request("GET", "/api/library")
        return data.get("songs", [])

    def push_library(self, entries: list[dict]) -> dict:
        return self._json_request("POST", "/api/library", {"songs": entries})

    def download_song(self, song_id: str) -> bytes:
        return self._request("GET", f"/api/songs/{song_id}")

    def upload_song(self, name: str, data: bytes, meta: dict | None = None) -> dict:
        """Multipart upload (hand-rolled, stdlib-only)."""
        boundary = "----KeyPrism" + uuid.uuid4().hex
        parts = []
        for key, value in (meta or {}).items():
            parts.append(
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n"
                f"{value}\r\n".encode())
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"{name}\"\r\nContent-Type: {ctype}\r\n\r\n".encode())
        parts.append(data)
        parts.append(f"\r\n--{boundary}--\r\n".encode())
        body = b"".join(parts)
        raw = self._request("POST", "/api/songs", body,
                            {"Content-Type": f"multipart/form-data; boundary={boundary}"})
        return json.loads(raw or b"{}")
