"""HTTP client of the add-in for /api/fusion/sync (stdlib only: Fusion's Python has no requests)."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Optional

DEFAULT_SERVER = "https://aerostator.com"
PREFIX = "/api/fusion/sync"
TIMEOUT_S = 120


class SyncError(RuntimeError):
    """A refusal or failure, with the server's own words when there are any."""

    def __init__(self, status: int, message: str, body: Optional[dict] = None):
        super().__init__("%s%s" % (("HTTP %d: " % status) if status else "", message))
        self.status, self.body = status, body or {}


class Client:
    def __init__(self, server: str, key: str, opener=None):
        self.server = (server or DEFAULT_SERVER).rstrip("/")
        self.key = key
        self._open = opener or urllib.request.urlopen

    def _url(self, path: str, **query) -> str:
        q = {k: v for k, v in query.items() if v is not None}
        return self.server + PREFIX + path + (("?" + urllib.parse.urlencode(q)) if q else "")

    @staticmethod
    def seg(text: str) -> str:
        """A die / config name as one URL path segment ('CIANO14 50 edited' -> 'CIANO14%2050%20edited')."""
        return urllib.parse.quote(str(text), safe="")

    def _call(self, method: str, url: str, data: Optional[bytes] = None, ctype: Optional[str] = None) -> dict:
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", "Bearer " + self.key)
        req.add_header("Accept", "application/json")
        if ctype:
            req.add_header("Content-Type", ctype)
        try:
            with self._open(req, timeout=TIMEOUT_S) as r:
                txt = r.read().decode("utf-8")
                return json.loads(txt) if txt.strip() else {}
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            try:
                body = json.loads(raw)
            except ValueError:
                body = {"detail": raw[:500]}
            det = body.get("detail", body)
            msg = det.get("message") if isinstance(det, dict) else str(det)
            raise SyncError(e.code, msg or raw[:300], body)
        except urllib.error.URLError as e:
            raise SyncError(0, "server not reachable: %s" % e.reason)

    def get_link(self, urn: str) -> Optional[dict]:
        try:
            return self._call("GET", self._url("/link", urn=urn))
        except SyncError as e:
            if e.status == 404:
                return None
            raise

    def put_link(self, urn: str, document: str, die: str, config: str) -> dict:
        body = json.dumps(dict(urn=urn, document=document, die=die, config=config)).encode("utf-8")
        return self._call("PUT", self._url("/link"), body, "application/json")

    def targets(self) -> dict:
        return self._call("GET", self._url("/targets"))

    def params(self, die: str, config: str) -> dict:
        return self._call("GET", self._url("/%s/%s/params.json" % (self.seg(die), self.seg(config))))

    def status(self, die: str, config: str) -> dict:
        return self._call("GET", self._url("/%s/%s/status" % (self.seg(die), self.seg(config))))

    def upload(self, zip_path: str) -> dict:
        boundary = "----aerostator" + uuid.uuid4().hex
        with open(zip_path, "rb") as fh:
            payload = fh.read()
        head = ('--%s\r\nContent-Disposition: form-data; name="file"; filename="%s"\r\nContent-Type: application/zip\r\n\r\n'
                % (boundary, os.path.basename(zip_path))).encode("utf-8")
        tail = ("\r\n--%s--\r\n" % boundary).encode("utf-8")
        return self._call("POST", self._url("/bundles"), head + payload + tail, "multipart/form-data; boundary=" + boundary)
