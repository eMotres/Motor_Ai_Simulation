"""Running computations per motor and per server, for the admin's Motors catalog (owner 2026-11-17: "for the admin, add the
status of every motor: which stage it is in, whether computations and optimisation are running on a server, and on which
one").  READ ONLY: nothing here starts, stops or writes a job.

Sources, both servers:

* the agent job launcher's status file (``motres-agent-job``, ``/var/lib/motres-agent-job/jobs_status.json``, override
  ``MOTRES_JOBS_STATUS``; contract ``~/reports/overview_job_contract_2026-11-16.md``), mounted read-only into the API
  container by the deploy.  A file older than :data:`STALE_S` is "stale" with its age, a missing one is "no status":
  never shown as zero load;
* the application's own queue (``motor_ai_sim.jobs``: the solves, optimisation and refine runs of this process), whose
  records name their motor in ``target``;
* the OTHER server's view of the same, through the signed peer link (``GET /api/peer/v1/jobs/status``), cached
  :data:`PEER_TTL_S`; a peer that does not answer is "no status".

A job names its motor by the exact stored die / configuration names (``job.json``); after a rename an old manifest simply
names a die that no longer exists, and the catalog shows it under "unknown die" instead of guessing.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

STATUS_PATH = "/var/lib/motres-agent-job/jobs_status.json"
STALE_S = 120.0
PEER_TTL_S = 15.0
LIVE_STATES = ("queued", "running")
_PEER: Dict[str, Any] = {"ts": 0.0, "value": None}
_LOCK = threading.Lock()


def status_path() -> Path:
    return Path(os.environ.get("MOTRES_JOBS_STATUS") or STATUS_PATH)


def _region() -> str:
    try:
        from motor_ai_sim.regions import config as RC
        return RC.local_region()
    except Exception:                                       # noqa: BLE001
        return ""


def _epoch(iso: Any) -> Optional[float]:
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _iso(ts: Any) -> Optional[str]:
    try:
        return datetime.fromtimestamp(float(ts), timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z") if ts else None
    except (TypeError, ValueError, OverflowError):
        return None


def _job(j: Dict[str, Any], source: str, server: str) -> Dict[str, Any]:
    """One job in the reader's shape: what the chip and its tooltip show."""
    prog = j.get("progress") if isinstance(j.get("progress"), dict) else None
    return {
        "source": source, "tag": str(j.get("tag") or j.get("run_id") or ""), "server": str(j.get("server") or server or ""),
        "state": str(j.get("state") or ""), "kind": str(j.get("kind") or "other"), "session": str(j.get("session") or j.get("requester") or ""),
        "die": str(j.get("die") or "") or None, "configuration": str(j.get("configuration") or "") or None,
        "started_at": j.get("started_at"), "submitted_at": j.get("submitted_at"),
        "progress": {"done": prog.get("done"), "total": prog.get("total"), "unit": prog.get("unit")} if prog else None,
        "points_total": j.get("points_total"),
        "cpus_reserved": j.get("cpus_reserved", j.get("cpus_req")), "cpu_used": j.get("cpu_used"),
        "mem_reserved_gb": j.get("mem_reserved_gb", j.get("mem_gb_req")), "mem_now_gb": j.get("mem_now_gb"),
    }


def _launcher(now: float) -> Dict[str, Any]:
    """The launcher's file: ``{"state": live|stale|missing, "age_s", "budget", "totals", "jobs"}``."""
    p = status_path()
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"state": "missing", "age_s": None, "budget": None, "totals": None, "jobs": []}
    except Exception:                                       # noqa: BLE001 - unreadable = no status, never zero load
        return {"state": "missing", "age_s": None, "budget": None, "totals": None, "jobs": []}
    written = _epoch(doc.get("written_at"))
    age = None if written is None else max(0.0, now - written)
    server = str(doc.get("server") or _region())
    jobs = [_job(j, "agent", server) for j in doc.get("jobs") or [] if isinstance(j, dict) and j.get("state") in LIVE_STATES]
    return {"state": "live" if age is not None and age <= STALE_S else "stale", "age_s": None if age is None else round(age),
            "budget": doc.get("budget"), "totals": doc.get("totals"), "jobs": jobs}


def _app_jobs() -> List[Dict[str, Any]]:
    """The application's own queued and running records of this process (``jobs.JobRecord.target`` names the motor)."""
    out: List[Dict[str, Any]] = []
    try:
        from motor_ai_sim import jobs as J
        q = J.queue()
        recs = q.live_records() if hasattr(q, "live_records") else q.list_for_owner("", limit=500, is_admin=True)
        for r in recs:
            if r.state not in (J.JobState.QUEUED, J.JobState.RUNNING):
                continue
            t = r.target or {}
            out.append(_job({"tag": r.run_id, "state": r.state, "kind": r.kind, "session": r.owner, "die": t.get("die"),
                             "configuration": t.get("config"), "started_at": _iso(r.started_at), "submitted_at": _iso(r.queued_at)},
                            "app", _region()))
    except Exception:                                       # noqa: BLE001
        pass
    return out


def local_view(now: Optional[float] = None) -> Dict[str, Any]:
    """This server: the launcher's file and the application's queue."""
    now = time.time() if now is None else now
    lv = _launcher(now)
    return {"server": _region(), **{k: lv[k] for k in ("state", "age_s", "budget", "totals")},
            "jobs": lv["jobs"] + _app_jobs()}


def peer_view(now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """The other server's :func:`local_view` through the peer link, cached; None in single-region mode."""
    try:
        from motor_ai_sim.regions import config as RC
        if not RC.peer_configured():
            return None
        region = RC.peer_region()
    except Exception:                                       # noqa: BLE001
        return None
    now = time.time() if now is None else now
    with _LOCK:
        if _PEER["value"] is not None and now - _PEER["ts"] < PEER_TTL_S:
            return _PEER["value"]
    try:
        from motor_ai_sim.regions import peer as P
        v = P.get("/jobs/status", timeout=2.0)
    except Exception:                                       # noqa: BLE001
        v = None
    view = v if isinstance(v, dict) and "jobs" in v else {"server": region, "state": "no_status", "age_s": None,
                                                          "budget": None, "totals": None, "jobs": []}
    with _LOCK:
        _PEER.update(ts=now, value=view)
    return view


def by_motor(views: List[Dict[str, Any]], known_dies: Optional[set] = None) -> Dict[str, Any]:
    """``{"motors": {"<die>/<config>": [job]}, "dies": {"<die>": [job]}, "unknown": [job], "unnamed": [job]}``.

    A job naming a configuration goes to that motor, one naming only the die to the die; a die no longer in the catalog
    (``known_dies`` given) goes to ``unknown``; a job naming no die only shows in the server strip (``unnamed``)."""
    out: Dict[str, Any] = {"motors": {}, "dies": {}, "unknown": [], "unnamed": []}
    for v in views:
        for j in (v or {}).get("jobs") or []:
            die, cfg = j.get("die"), j.get("configuration")
            if not die:
                out["unnamed"].append(j)
            elif known_dies is not None and die not in known_dies:
                out["unknown"].append(j)
            elif cfg:
                out["motors"].setdefault(f"{die}/{cfg}", []).append(j)
            else:
                out["dies"].setdefault(die, []).append(j)
    return out
