"""How an API process asks the compute worker about itself (deploy stage 2).

Stdlib only, short timeouts, never raises: the worker being restarted must not turn an admin page into an error.  The
channel is authenticated with :func:`motor_ai_sim.roles.internal_headers` (an HMAC of the deployment's auth secret), so
nothing new lives in ``api.env`` and the worker's ``/api/worker/*`` routes stay closed to the outside (nginx does not
route them; the door only opens for loopback or this key).
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from motor_ai_sim import roles

log = logging.getLogger(__name__)

STATE_PATH = "/api/worker/state"
DRAIN_PATH = "/api/worker/drain"


def _call(method: str, path: str, body: Optional[Dict[str, Any]] = None, timeout: float = 2.0) -> Optional[Dict[str, Any]]:
    url = roles.worker_url() + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = dict(roles.internal_headers())
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:                  # noqa: S310 - fixed internal URL
            out = json.loads(r.read().decode("utf-8") or "{}")
            return out if isinstance(out, dict) else None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log.debug("worker %s %s failed: %s", method, path, exc)
        return None


def fetch_state(timeout: float = 2.0) -> Optional[Dict[str, Any]]:
    """The worker's ``/api/worker/state`` payload, or None when it cannot be reached."""
    return _call("GET", STATE_PATH, timeout=timeout)


def set_drain(on: bool, timeout: float = 5.0) -> Optional[Dict[str, Any]]:
    return _call("POST", DRAIN_PATH, {"on": bool(on)}, timeout=timeout)
