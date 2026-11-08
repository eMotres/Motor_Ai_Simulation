"""This server's state for Admin -> Servers -> Regions (and for the peer's same card).

``local_status()`` -> ``{region, ok, version, commit, load, queue, disk, ts}``; every part is best-effort and cheap (one env
read, one ``os.getloadavg``, one ``disk_usage`` and the job view the admin already polls), so the peer may ask every 15 s.
Nothing in it is personal data: the queue depth is counts only, never an owner.
"""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, Optional

from motor_ai_sim.regions import config as RC

_REPO = Path(__file__).resolve().parents[3]


def version() -> str:
    try:
        return (_REPO / "VERSION").read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"


def commit() -> str:
    """The deployed commit: the process build id (blue/green image tag), else ``APP_GIT_SHA``, else ``""``."""
    from motor_ai_sim import roles as R
    return R.build_id() or str(os.environ.get("APP_GIT_SHA", "") or "").strip()


def load() -> Dict[str, Any]:
    out: Dict[str, Any] = {"cores": os.cpu_count() or 0, "load1": None, "load_pct": None}
    try:
        l1 = os.getloadavg()[0]                              # not on Windows: stays None there
        out["load1"] = round(l1, 2)
        if out["cores"]:
            out["load_pct"] = round(100.0 * l1 / out["cores"], 1)
    except (AttributeError, OSError):
        pass
    return out


def queue() -> Dict[str, Any]:
    try:
        from motor_ai_sim import cluster_monitor as CM
        v = CM.jobs_view()
        return {"running": int(v.get("running") or 0), "queued": int(v.get("queued") or 0),
                "oldest_wait_s": float(v.get("oldest_wait_s") or 0.0),
                "worker_reachable": v.get("worker_reachable", True) is not False}
    except Exception:                                       # noqa: BLE001
        return {"running": None, "queued": None, "oldest_wait_s": None, "worker_reachable": False}


def _data_root() -> Path:
    from motor_ai_sim import users as U
    p = Path(U._USERS_FILE).parent
    return p.parent if p.name == "identity" else p          # /srv/motres/identity -> /srv/motres


def disk(path: Optional[Path] = None) -> Dict[str, Any]:
    p = Path(path) if path else _data_root()
    try:
        u = shutil.disk_usage(str(p))
        return {"total_gb": round(u.total / 1e9, 1), "free_gb": round(u.free / 1e9, 1),
                "used_pct": round(100.0 * (u.total - u.free) / u.total, 1) if u.total else None}
    except OSError:
        return {"total_gb": None, "free_gb": None, "used_pct": None}


def local_status(now: Optional[float] = None) -> Dict[str, Any]:
    return {"region": RC.local_region(), "ok": True, "version": version(), "commit": commit(),
            "load": load(), "queue": queue(), "disk": disk(), "ts": round(time.time() if now is None else now, 1)}


def regions_view() -> Dict[str, Any]:
    """Both regions for the admin card.  Single-region mode: the local server only (``peer: None``)."""
    from motor_ai_sim.regions import peer as P
    view: Dict[str, Any] = {**RC.describe(), "local": {**local_status(), "role": "this_server"}, "peer": None}
    if RC.peer_configured():
        st = P.get("/health")
        if isinstance(st, dict):
            view["peer"] = {**{k: st.get(k) for k in ("region", "version", "commit", "load", "queue", "disk", "ts")},
                            "ok": True, "role": "peer", "reachable": True}
        else:
            view["peer"] = {"region": RC.peer_region(), "ok": False, "role": "peer", "reachable": False}
    return view
