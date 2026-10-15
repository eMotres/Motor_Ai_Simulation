"""Test doubles for the applicant verification: a fake HTTP client (no network)."""
from __future__ import annotations

import json

from motor_ai_sim.verification.net import HttpResult


class FakeHttp:
    """routes: {url: str | dict | HttpResult}; a route also answers the same URL with a query string; anything else = 404."""

    def __init__(self, routes=None):
        self.routes = dict(routes or {})
        self.calls = []

    def _find(self, url):
        if url in self.routes:
            return self.routes[url]
        keys = [k for k in self.routes if url.startswith(k + "?")]          # a route also answers the same path with a query string
        return self.routes[max(keys, key=len)] if keys else None

    def get(self, url, **kw):
        self.calls.append((url, kw))
        v = self._find(url)
        if v is None:
            return HttpResult(url, 404, "")
        if isinstance(v, HttpResult):
            return v
        return HttpResult(url, 200, v if isinstance(v, str) else json.dumps(v))

    def post_json(self, url, body, **kw):
        return self.get(url)
