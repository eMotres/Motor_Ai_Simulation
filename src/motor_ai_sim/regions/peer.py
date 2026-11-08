"""The server-to-server channel between the two regions: HMAC-SHA256 signed requests.

Every request carries four headers::

    X-Motres-Peer-Region   the SENDER's region (eu | us)
    X-Motres-Peer-Ts       unix seconds when it was signed
    X-Motres-Peer-Nonce    16+ random url-safe characters, never accepted twice within the skew window
    X-Motres-Peer-Sig      hex HMAC-SHA256(secret, "v1\\n<METHOD>\\n<path?query>\\n<ts>\\n<nonce>\\n<region>\\n<sha256(body)>")

The receiver accepts it only when multi-region mode is configured, the sender region is the configured peer region,
|now - ts| <= 300 s, the nonce is new and the signature matches (constant-time).  Anything else is the same 404 a
single-region server answers, so a probe learns nothing.  The secret comes from env (``MOTRES_PEER_HMAC_SECRET`` or
``..._FILE``); it is never logged and never in git.  Transport: WireGuard between the hosts in stage 2 (the peer URL is
the tunnel address), TLS on top is allowed but not required by this layer.

The client never raises: an unreachable / unconfigured / refusing peer is ``None`` and the caller shows "unreachable".
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import threading
import time
from typing import Any, Dict, Mapping, Optional, Tuple

from motor_ai_sim.regions import config as RC

log = logging.getLogger(__name__)

H_REGION = "X-Motres-Peer-Region"
H_TS = "X-Motres-Peer-Ts"
H_NONCE = "X-Motres-Peer-Nonce"
H_SIG = "X-Motres-Peer-Sig"
SKEW_S = 300
TIMEOUT_S = 4.0
#: The peer API lives under this prefix on both servers.
PREFIX = "/api/peer/v1"

_NONCES: Dict[str, float] = {}
_NONCE_LOCK = threading.Lock()
_NONCE_MAX = 20000


def reset_nonces() -> None:
    with _NONCE_LOCK:
        _NONCES.clear()


def body_hash(body: bytes) -> str:
    return hashlib.sha256(body or b"").hexdigest()


def canonical(method: str, path: str, ts: str, nonce: str, region: str, body: bytes) -> bytes:
    return "\n".join(("v1", method.upper(), path, ts, nonce, region, body_hash(body))).encode("utf-8")


def signature(secret: str, method: str, path: str, ts: str, nonce: str, region: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), canonical(method, path, ts, nonce, region, body), hashlib.sha256).hexdigest()


def sign_headers(method: str, path: str, body: bytes = b"", *, secret: Optional[str] = None,
                 region: Optional[str] = None, now: Optional[float] = None) -> Dict[str, str]:
    """Headers for one outgoing request (``path`` includes the query string exactly as sent)."""
    sec = RC.peer_secret() if secret is None else secret
    reg = region or RC.local_region()
    ts = str(int(time.time() if now is None else now))
    nonce = secrets.token_urlsafe(16)
    return {H_REGION: reg, H_TS: ts, H_NONCE: nonce, H_SIG: signature(sec, method, path, ts, nonce, reg, body)}


def _hget(headers: Mapping[str, str], name: str) -> str:
    try:
        return str(headers.get(name) or headers.get(name.lower()) or "")
    except Exception:                                       # noqa: BLE001
        return ""


def verify(method: str, path: str, headers: Mapping[str, str], body: bytes = b"", *,
           now: Optional[float] = None) -> Tuple[bool, str]:
    """-> ``(ok, reason)``.  Reasons: ``not_configured`` ``wrong_region`` ``stale`` ``bad_nonce`` ``replay``
    ``bad_signature``.  The nonce is remembered only after a valid signature (a forger cannot burn real nonces)."""
    if not RC.peer_configured():
        return False, "not_configured"
    region, ts, nonce, sig = (_hget(headers, h) for h in (H_REGION, H_TS, H_NONCE, H_SIG))
    if RC.norm_region(region) != RC.peer_region():
        return False, "wrong_region"
    t = time.time() if now is None else now
    try:
        if abs(t - int(ts)) > SKEW_S:
            return False, "stale"
    except ValueError:
        return False, "stale"
    if not (16 <= len(nonce) <= 64):
        return False, "bad_nonce"
    want = signature(RC.peer_secret(), method, path, ts, nonce, region, body)
    if not sig or not hmac.compare_digest(sig, want):
        return False, "bad_signature"
    with _NONCE_LOCK:
        for k in [k for k, v in _NONCES.items() if v < t - SKEW_S]:
            _NONCES.pop(k, None)
        if nonce in _NONCES:
            return False, "replay"
        if len(_NONCES) >= _NONCE_MAX:
            _NONCES.clear()
        _NONCES[nonce] = t
    return True, "ok"


def request(method: str, path: str, payload: Any = None, *, timeout: float = TIMEOUT_S,
            transport: Any = None) -> Optional[Any]:
    """Send one signed request to the peer -> its JSON answer, or ``None`` (not configured, unreachable, non-2xx,
    not JSON).  ``path`` is relative to the peer prefix (``"/health"``) or absolute (``"/api/peer/v1/health"``)."""
    if not RC.peer_configured():
        return None
    full = path if path.startswith("/api/") else PREFIX + path
    body = b"" if payload is None else json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    headers = sign_headers(method, full, body)
    if body:
        headers["Content-Type"] = "application/json"
    try:
        import httpx
        kw = {"transport": transport} if transport is not None else {}
        with httpx.Client(base_url=RC.peer_url(), timeout=timeout, **kw) as c:
            r = c.request(method.upper(), full, content=body or None, headers=headers)
        if r.status_code // 100 != 2:
            log.info("regions: peer answered %s on %s", r.status_code, full.split("?")[0])
            return None
        return r.json()
    except Exception as e:                                  # noqa: BLE001 - the peer being down is a state, not an error
        log.info("regions: peer unreachable on %s (%s)", full.split("?")[0], type(e).__name__)
        return None


def get(path: str, **kw) -> Optional[Any]:
    return request("GET", path, **kw)


def post(path: str, payload: Any, **kw) -> Optional[Any]:
    return request("POST", path, payload, **kw)
