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
from typing import Any, Dict, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from motor_ai_sim.auth import resolve_user

router = APIRouter(prefix="/api/sweep", tags=["sweep-config"])

_ROOT = Path(__file__).parent.parent.parent.parent
# Beside the config THIS PROCESS is pointed at (``MOTOR_AI_SIM_CONFIG``).  It was
# pinned to the repo's own config/, and `_save_all` WRITES it — a redirected
# process overwrote the sweep the user has set up on the machine they have open.
# With no env var set this is byte-identical to `_ROOT / "config" / …`.
#
# Migration Stage 1: resolved PER CALL against the caller's workspace — with
# none set, the same folder and the same file.  The NAME survives for the
# completeness test.
def _store() -> Path:
    _ov = globals().get("_STORE")
    if _ov is not None:
        return Path(str(_ov))
    try:
        from motor_ai_sim.workspace import root as _ws_root
        return _ws_root() / "sweep_config.json"
    except Exception:                   # noqa: BLE001 — never break a save
        return _ROOT / "config" / "sweep_config.json"


def __getattr__(name):
    if name == "_STORE":
        return _store()
    raise AttributeError(name)
_LOCK = threading.Lock()


def _load_all() -> Dict[str, dict]:
    if not _store().exists():
        return {}
    try:
        data = json.loads(_store().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_all(store: Dict[str, dict]) -> None:
    try:
        _store().parent.mkdir(parents=True, exist_ok=True)
        _store().write_text(json.dumps(store, indent=2, ensure_ascii=False),
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


def _revision_of(cfg: Any) -> int:
    """The server-issued revision of a stored config (0 = stored before revisions existed, or nothing stored)."""
    try:
        return max(0, int(cfg.get("_rev"))) if isinstance(cfg, dict) else 0
    except (TypeError, ValueError):
        return 0


@router.get("/config")
def get_sweep_config(request: Request):
    """This browser-context's saved sweep config, or ``null`` if none saved yet, with its server revision."""
    cfg = _load_all().get(_bucket(request))
    return {"config": cfg, "revision": _revision_of(cfg)}


@router.put("/config")
async def put_sweep_config(request: Request, expected_revision: Optional[int] = None):
    """Persist the sweep config (variations + operating points + ripple limit).

    Accepts the raw JSON body as-is so the frontend's SweepConfig shape can evolve
    without a server change.  Every save is stamped with a server-issued, monotonic revision (``_rev``).  A client that
    states the revision it last saw (``expected_revision``) writes only if that is still the stored one: the check and
    the write are one step under the lock, so two tabs that read the same base cannot both win - the second one gets
    HTTP 409 with the stored copy and keeps its own edit.  Without ``expected_revision`` the save is unconditional
    (older clients), and still advances the revision."""
    try:
        body: Dict[str, Any] = await request.json()
    except Exception:
        return {"status": "error", "detail": "invalid JSON body"}
    if not isinstance(body, dict):
        return {"status": "error", "detail": "config must be an object"}
    with _LOCK:
        store = _load_all()
        bucket = _bucket(request)
        current = store.get(bucket)
        cur_rev = _revision_of(current)
        if expected_revision is not None and int(expected_revision) != cur_rev:
            return JSONResponse(status_code=409, content={
                "status": "conflict", "detail": "the stored sweep config changed since this copy was loaded",
                "revision": cur_rev, "config": current})
        body = {k: v for k, v in body.items() if k != "_rev"}
        body["_rev"] = cur_rev + 1
        store[bucket] = body
        _save_all(store)
    return {"status": "ok", "revision": body["_rev"]}
