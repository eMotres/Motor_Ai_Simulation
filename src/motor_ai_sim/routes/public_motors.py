"""``GET /api/public/motors`` - the landing page's public motor preview.

Anonymous, read-only, cacheable.  It is one of the very few /api routes a caller without
credentials may reach (``auth._ANON_OK_PATHS``), so it carries what the other open route that
touches the catalogue (``/api/support/chat``) carries: a per-IP rate limit, computed from the
same visitor address (``routes.support.client_ip``).  What it returns - and, as important, what
it cannot return - is the business of ``motor_ai_sim.public_motors``.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Dict, List

from fastapi import APIRouter, HTTPException, Request, Response

from motor_ai_sim import public_motors as PM
from motor_ai_sim.routes.support import client_ip

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/public", tags=["public"])

#: Per IP, per window.  The answer is cached, so a request is cheap; this only stops a script.
BURST_MAX = 60
BURST_WINDOW_S = 600.0
_ENV_BURST = "PUBLIC_MOTORS_BURST_MAX"

_lock = threading.Lock()
_hits: Dict[str, List[float]] = {}


def _cap() -> int:
    raw = os.environ.get(_ENV_BURST, "").strip()
    if not raw:
        return BURST_MAX
    try:
        return max(0, int(raw))
    except ValueError:
        return BURST_MAX


def reset_limits() -> None:
    """Forget every counter (tests)."""
    with _lock:
        _hits.clear()


def _charge(ip: str, now: float | None = None) -> bool:
    """Record one request; False when this address is over its window."""
    now = time.time() if now is None else now
    key = ip or "?"
    with _lock:
        hits = [t for t in _hits.get(key, ()) if now - t < BURST_WINDOW_S]
        if len(hits) >= _cap():
            _hits[key] = hits
            return False
        hits.append(now)
        _hits[key] = hits
        if len(_hits) > 5000:                                   # pragma: no cover
            for k, v in list(_hits.items()):
                if not v or now - v[-1] > BURST_WINDOW_S:
                    _hits.pop(k, None)
    return True


@router.get("/motors")
def get_public_motors(request: Request, response: Response):
    """Headline numbers of the public catalogue motors (allow-listed fields only)."""
    if not _charge(client_ip(request)):
        raise HTTPException(429, detail="Too many requests - try again in a few minutes.",
                            headers={"Retry-After": str(int(BURST_WINDOW_S))})
    try:
        rows = PM.public_motors()
    except Exception as e:                                       # noqa: BLE001
        # never a stack trace and never a half answer: the page then simply shows no preview
        log.warning("public motors unavailable (%s: %s)", type(e).__name__, e)
        raise HTTPException(503, detail="The motor preview is not available right now.")
    response.headers["Cache-Control"] = f"public, max-age={int(PM.CACHE_TTL_S)}"
    return {"motors": rows}
