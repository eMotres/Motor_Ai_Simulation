"""Server-side sweep / optimization config.

The selected sweep variables, the two operating points and the ripple limit used
to live only in one browser's localStorage, so a different browser (e.g. the
clean Claude-Preview browser) showed an empty Sweep panel.  This stores that
config on the BACKEND instead, so it follows the user across browsers.

Same per-user bucketed-JSON pattern as ``saved_sims``: the on-disk store is
``{bucket: config}`` where the bucket is the signed-in user's uid, or ``"local"``
for anonymous use (so every anonymous browser on this machine shares one config).
All file ops are best-effort — a missing/corrupt store never crashes the API.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Request

from motor_ai_sim.auth import resolve_user

router = APIRouter(prefix="/api/sweep", tags=["sweep-config"])

_ROOT = Path(__file__).parent.parent.parent.parent
_STORE = _ROOT / "config" / "sweep_config.json"
_LOCK = threading.Lock()


def _load_all() -> Dict[str, dict]:
    if not _STORE.exists():
        return {}
    try:
        data = json.loads(_STORE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_all(store: Dict[str, dict]) -> None:
    try:
        _STORE.parent.mkdir(parents=True, exist_ok=True)
        _STORE.write_text(json.dumps(store, indent=2, ensure_ascii=False),
                          encoding="utf-8")
    except Exception:
        pass  # best-effort; never crash the API over a config write


def _bucket(request: Request) -> str:
    """Per-user key: uid when signed in, else 'local'.  Never raises."""
    try:
        user = resolve_user(request.headers.get("authorization"))
        if user and user.get("uid"):
            return str(user["uid"])
    except Exception:
        pass
    return "local"


@router.get("/config")
def get_sweep_config(request: Request):
    """This browser-context's saved sweep config, or ``null`` if none saved yet."""
    return {"config": _load_all().get(_bucket(request))}


@router.put("/config")
async def put_sweep_config(request: Request):
    """Persist the sweep config (variations + operating points + ripple limit).

    Accepts the raw JSON body as-is so the frontend's SweepConfig shape can evolve
    without a server change."""
    try:
        body: Dict[str, Any] = await request.json()
    except Exception:
        return {"status": "error", "detail": "invalid JSON body"}
    if not isinstance(body, dict):
        return {"status": "error", "detail": "config must be an object"}
    with _LOCK:
        store = _load_all()
        store[_bucket(request)] = body
        _save_all(store)
    return {"status": "ok"}
