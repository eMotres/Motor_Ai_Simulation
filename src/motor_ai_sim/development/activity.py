"""Live activity of a development project: which of its motor variants is being computed on the server right now.

Owner 2026-10-27: customers of a project must see in real time which variant is being worked on.  ``GET
/api/projects/{project_id}/activity`` answers it for the project's linked motors, from two sources:

* the app's own registered runs (``jobs.live_targets`` - every running / queued run knows the machine it is for, see
  ``run_target``).  Since deploy stage 2 the runs live in the compute worker, so the API process asks the worker
  (``worker_client.fetch_state()['activity']``, cached for a few seconds); the single-process install reads its own queue;
* an OPTIONAL status file the host-side queue writes (``MOTRES_COMPUTE_STATUS``, default
  ``/srv/motres/shared/compute/status.json``): ``{"updated": iso, "jobs": [{"id", "state", "started", "title", "targets":
  [{"die", "config", "what"}], "eta_min"}]}``.  Missing, unreadable or malformed = ignored silently.

Customer-safe by construction: a row carries a short English label per run kind, the machine (a linked motor of THIS project),
the duty, the state, the start and the progress - never a run id, an owner, an internal path or another project's motor.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request

from motor_ai_sim.development import access

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects/{project_id}/activity", tags=["development-activity"])

ENV_STATUS = "MOTRES_COMPUTE_STATUS"
DEFAULT_STATUS = "/srv/motres/shared/compute/status.json"
#: a host status file whose ``updated`` is older than this is a dead scheduler's, not live work
HOST_STALE_S = 12 * 3600.0
#: the worker is asked at most this often (every viewer of every project polls)
WORKER_TTL_S = 5.0

#: run kind -> the short phrase a customer sees (exact kind first, then the family before the first dot)
LABELS: Dict[str, str] = {
    "transient": "Electromagnetic run",
    "coupled.run": "Coupled thermal run",
    "coupled.constants_20c": "Constants at 20 °C",
    "coupled.continuous_rating": "Continuous rating (S1)",
    "thermal.coupled": "Thermal run",
    "thermal.field": "Thermal field",
    "thermal.duty_cycle": "Time to the temperature limit",
    "cogging": "Cogging torque",
    "static3d.solve": "3-D end-effect run",
}
FAMILY_LABELS: Dict[str, str] = {
    "mechanical": "Mechanics",
    "optimizer": "Design search",
    "pipeline": "Design search",
    "sweep": "Design search",
    "thermal": "Thermal run",
    "coupled": "Coupled thermal run",
}
DEFAULT_LABEL = "Computation"


def label_for(kind: str) -> str:
    k = str(kind or "").strip().lower()
    if k in LABELS:
        return LABELS[k]
    return FAMILY_LABELS.get(k.split(".", 1)[0], DEFAULT_LABEL)


def _iso(epoch: Any) -> Optional[str]:
    try:
        v = float(epoch)
        if v <= 0:
            return None
        return datetime.fromtimestamp(v, tz=timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _parse_iso(value: Any) -> Optional[float]:
    try:
        text = str(value or "").strip()
        if not text:
            return None
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (TypeError, ValueError):
        return None


def _text(value: Any, limit: int = 80) -> str:
    """One short printable line of free text from a host file."""
    return " ".join("".join(ch if ch.isprintable() else " " for ch in str(value or "")).split())[:limit]


# ── sources ──────────────────────────────────────────────────────────────────

_worker_cache: Dict[str, Any] = {"at": 0.0, "rows": []}
_worker_lock = threading.Lock()


def app_rows() -> List[Dict[str, Any]]:
    """The running / queued runs of the app, for every account: the compute worker's when this is the API process."""
    try:
        from motor_ai_sim import roles
        if not roles.is_api():
            from motor_ai_sim import jobs
            return list(jobs.live_targets())
        with _worker_lock:
            if time.time() - float(_worker_cache["at"]) < WORKER_TTL_S:
                return list(_worker_cache["rows"])
        from motor_ai_sim import worker_client
        state = worker_client.fetch_state()
        rows = state.get("activity") if isinstance(state, dict) else None
        rows = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
        with _worker_lock:
            _worker_cache["at"], _worker_cache["rows"] = time.time(), rows
        return list(rows)
    except Exception as exc:                                # noqa: BLE001 - a missing bar must never break the page
        log.debug("live activity: app runs not read: %s", exc)
        return []


def host_rows() -> List[Dict[str, Any]]:
    """The host-side queue's jobs, one row per (job, target).  ``[]`` when the file is missing, stale or malformed."""
    path = Path(os.environ.get(ENV_STATUS) or DEFAULT_STATUS)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    try:
        if not isinstance(doc, dict) or not isinstance(doc.get("jobs"), list):
            return []
        updated = _parse_iso(doc.get("updated"))
        if updated is not None and time.time() - updated > HOST_STALE_S:
            return []
        out: List[Dict[str, Any]] = []
        for job in doc["jobs"]:
            if not isinstance(job, dict) or job.get("state") not in ("running", "queued"):
                continue
            started = _parse_iso(job.get("started"))
            eta = job.get("eta_min")
            eta = float(eta) if isinstance(eta, (int, float)) and not isinstance(eta, bool) and eta >= 0 else None
            for target in job.get("targets") or []:
                if not isinstance(target, dict) or not target.get("die") or not target.get("config"):
                    continue
                out.append({"kind": "host", "state": job["state"], "die": str(target["die"]),
                            "config": str(target["config"]), "duty": _text(target.get("duty"), 60),
                            "label": _text(target.get("what")) or _text(job.get("title")) or DEFAULT_LABEL,
                            "started_at": _iso(started) if started else None, "progress": None, "eta_min": eta})
        return out
    except Exception as exc:                                # noqa: BLE001
        log.debug("live activity: host status ignored: %s", exc)
        return []


# ── matching ─────────────────────────────────────────────────────────────────

def _die_names(die: str) -> set:
    """The names one linked die answers to: itself and, for a published label, the bare name its publisher uses."""
    names = {die.casefold()}
    try:
        from motor_ai_sim.workspace import split_published_label
        names.add(split_published_label(die)[0].casefold())
    except Exception:                                       # noqa: BLE001
        pass
    return names


def _alias_names(canonical: str) -> set:
    try:
        from motor_ai_sim.development import motors
        from motor_ai_sim.routes import family
        alias = motors._ordinary_alias_for_canonical(family, canonical)
        return {alias.casefold()} if alias else set()
    except Exception:                                       # noqa: BLE001 - no alias is the ordinary case
        return set()


def project_activity(project: dict, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The ``rows`` that belong to a motor linked to ``project``, in the customer-safe shape."""
    links = [m for m in project.get("linked_motors", []) if isinstance(m, dict) and m.get("die") and m.get("config")]
    if not links or not rows:
        return []
    names = {i: _die_names(str(m["die"])) for i, m in enumerate(links)}
    alias_done: set = set()
    out: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("state") not in ("running", "queued"):
            continue
        rdie, rcfg = str(row.get("die") or "").casefold(), str(row.get("config") or "").casefold()
        if not rdie or not rcfg:
            continue
        rbare = rdie
        try:
            from motor_ai_sim.workspace import split_published_label
            rbare = split_published_label(str(row.get("die") or ""))[0].casefold()
        except Exception:                                   # noqa: BLE001
            pass
        hit = None
        for i, link in enumerate(links):
            if str(link["config"]).casefold() != rcfg:
                continue
            if i not in alias_done and not ({rdie, rbare} & names[i]):
                alias_done.add(i)
                names[i] |= _alias_names(str(link["die"]))
            if {rdie, rbare} & names[i]:
                hit = link
                break
        if hit is None:
            continue
        started = row.get("started_at")
        if isinstance(started, (int, float)) and not isinstance(started, bool):
            started = _iso(started)
        progress = row.get("progress")
        progress = (round(min(1.0, max(0.0, float(progress))), 3)
                    if isinstance(progress, (int, float)) and not isinstance(progress, bool) else None)
        item = {"die": str(hit["die"]), "config": str(hit["config"]), "duty": _text(row.get("duty"), 60),
                "kind": str(row.get("kind") or ""),
                "label": _text(row.get("label")) or label_for(str(row.get("kind") or "")),
                "state": row["state"], "started_at": started if isinstance(started, str) else None,
                "progress": progress}
        if row.get("eta_min") is not None:
            item["eta_min"] = row["eta_min"]
        out.append(item)
    out.sort(key=lambda r: (0 if r["state"] == "running" else 1, r["started_at"] or "9999"))
    return out


@router.get("")
def project_activity_route(project_id: str, request: Request) -> Dict[str, Any]:
    """Live runs on the motors linked to this project (members and engineers; same access as the project's motor list)."""
    _user, project, _role = access.require_project(request, project_id)
    rows = project_activity(project, app_rows() + host_rows())
    return {"project_id": project_id, "at": _iso(time.time()), "activity": rows}
