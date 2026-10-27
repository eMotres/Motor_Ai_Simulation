"""Live activity of a development project: which of its motor variants is being computed on the server right now.

Owner 2026-10-27: customers of a project must see in real time which variant is being worked on.  ``GET
/api/projects/{project_id}/activity`` answers it for the project's linked motors, from two sources:

* the app's own registered runs (``jobs.live_targets`` - every running / queued run knows the machine it is for, see
  ``run_target``).  Since deploy stage 2 the runs live in the compute worker, so the API process asks the worker
  (``worker_client.fetch_state()['activity']``, cached for a few seconds); the single-process install reads its own queue;
* an OPTIONAL status file the host-side queue writes (``MOTRES_COMPUTE_STATUS``, default
  ``/srv/motres/shared/compute/status.json``): ``{"updated": iso, "jobs": [{"id", "state", "started", "title", "targets":
  [{"die", "config", "what"}], "eta_min"}]}``.  Missing, unreadable or malformed = ignored silently.

Queue and stages (owner 2026-10-27): each row also says where it stands in the server queue (``position`` / ``ahead``, counts
over the WHOLE server and never whose) and when it is expected to start / finish (``eta``, see ``job_eta``); the response adds a
project ``summary`` (idle | queued | computing | results_ready, run counts, expected_all_done, server load as counts) and the five
``stages`` of every current variant (``development.stages``: EM points, thermal, mechanics, 3-D end effect, report), derived from
the stored duty results, the live rows and the recent failures - not a new pipeline.

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

_worker_cache: Dict[str, Any] = {"at": 0.0, "rows": [], "server": None, "failures": []}
_worker_lock = threading.Lock()


def _worker_state() -> Dict[str, Any]:
    """The compute worker state, cached for a few seconds (every viewer of every project polls).  A worker that is down
    (or an older build without a key) is cached as empty for the same few seconds."""
    with _worker_lock:
        if time.time() - float(_worker_cache["at"]) < WORKER_TTL_S:
            return {"activity": list(_worker_cache["rows"]), "queue": _worker_cache.get("server"),
                    "failures": list(_worker_cache.get("failures") or [])}
    from motor_ai_sim import worker_client
    state = worker_client.fetch_state()
    state = state if isinstance(state, dict) else {}
    rows = state.get("activity")
    rows = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
    fails = state.get("failures")
    fails = [r for r in fails if isinstance(r, dict)] if isinstance(fails, list) else []
    queue = state.get("queue") if isinstance(state.get("queue"), dict) else None
    with _worker_lock:
        _worker_cache.update(at=time.time(), rows=rows, server=queue, failures=fails)
    return {"activity": list(rows), "queue": queue, "failures": list(fails)}


def app_rows() -> List[Dict[str, Any]]:
    """The running / queued runs of the app, for every account: the compute worker's when this is the API process."""
    try:
        from motor_ai_sim import roles
        if not roles.is_api():
            from motor_ai_sim import jobs
            return list(jobs.live_targets())
        return list(_worker_state()["activity"])
    except Exception as exc:                                # noqa: BLE001 - a missing bar must never break the page
        log.debug("live activity: app runs not read: %s", exc)
        return []


def app_extra() -> Dict[str, Any]:
    """``{"server": {running, queued, workers} | None, "failures": [...]}`` - the queue totals (counts only) and its
    recent failures.  Call after :func:`app_rows` (the API process reads both from the one cached worker answer)."""
    try:
        from motor_ai_sim import roles
        if not roles.is_api():
            from motor_ai_sim import jobs
            return {"server": jobs.server_load(), "failures": jobs.recent_failures()}
        state = _worker_state()
        return {"server": state.get("queue"), "failures": state.get("failures") or []}
    except Exception as exc:                                # noqa: BLE001
        log.debug("live activity: queue extras not read: %s", exc)
        return {"server": None, "failures": []}


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


def _link_matcher(project: dict):
    """``row -> linked motor | None``: a run belongs to a linked motor by (die, configuration), case-insensitive, the
    canonical die or its ordinary alias.  ``None`` when the project links nothing."""
    links = [m for m in project.get("linked_motors", []) if isinstance(m, dict) and m.get("die") and m.get("config")]
    if not links:
        return None
    names = {i: _die_names(str(m["die"])) for i, m in enumerate(links)}
    alias_done: set = set()

    def match(row: Dict[str, Any]) -> Optional[dict]:
        rdie, rcfg = str(row.get("die") or "").casefold(), str(row.get("config") or "").casefold()
        if not rdie or not rcfg:
            return None
        rbare = rdie
        try:
            from motor_ai_sim.workspace import split_published_label
            rbare = split_published_label(str(row.get("die") or ""))[0].casefold()
        except Exception:                                   # noqa: BLE001
            pass
        for i, link in enumerate(links):
            if str(link["config"]).casefold() != rcfg:
                continue
            if i not in alias_done and not ({rdie, rbare} & names[i]):
                alias_done.add(i)
                names[i] |= _alias_names(str(link["die"]))
            if {rdie, rbare} & names[i]:
                return link
        return None
    return match


def _int_or_none(value: Any, minimum: int) -> Optional[int]:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) and value >= minimum else None


def _clean_eta(eta: Any, eta_min: Any = None) -> Optional[Dict[str, Any]]:
    """The customer-facing estimate of one row (only the four known keys).  A host-file row has just ``eta_min`` (minutes
    left, from the scheduler itself): turned into a rounded finish time at medium confidence."""
    if isinstance(eta, dict):
        conf = eta.get("confidence") if eta.get("confidence") in ("low", "medium", "high") else "low"
        return {"expected_start": eta.get("expected_start") if isinstance(eta.get("expected_start"), str) else None,
                "expected_finish": eta.get("expected_finish") if isinstance(eta.get("expected_finish"), str) else None,
                "confidence": conf, "basis": str(eta.get("basis") or "history")[:20]}
    if isinstance(eta_min, (int, float)) and not isinstance(eta_min, bool) and eta_min >= 0:
        from motor_ai_sim import job_eta
        now = time.time()
        return job_eta.public_eta({"expected_finish": now + float(eta_min) * 60.0, "confidence": "medium", "basis": "host"}, now)
    return None


def project_activity(project: dict, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The ``rows`` that belong to a motor linked to ``project``, in the customer-safe shape."""
    match = _link_matcher(project)
    if match is None or not rows:
        return []
    out: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("state") not in ("running", "queued"):
            continue
        hit = match(row)
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
                "progress": progress,
                # the queue: the place among the queued runs of the WHOLE server (a count, never whose) and the estimate
                "position": _int_or_none(row.get("position"), 1) if row["state"] == "queued" else None,
                "ahead": _int_or_none(row.get("ahead"), 0) if row["state"] == "queued" else None,
                "eta": _clean_eta(row.get("eta"), row.get("eta_min"))}
        if row.get("eta_min") is not None:
            item["eta_min"] = row["eta_min"]
        out.append(item)
    out.sort(key=lambda r: (0 if r["state"] == "running" else 1, r["started_at"] or "9999", r["position"] or 0))
    return out


def project_failures(project: dict, failures: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The recent failures that belong to a linked motor of the project (kind, machine, duty, when - nothing else)."""
    match = _link_matcher(project)
    if match is None:
        return []
    out = []
    for f in failures or []:
        hit = match(f) if isinstance(f, dict) else None
        if hit is not None:
            out.append({"die": str(hit["die"]), "config": str(hit["config"]), "duty": _text(f.get("duty"), 60),
                        "kind": str(f.get("kind") or ""), "finished_at": f.get("finished_at")})
    return out


_stage_cache: Dict[Any, Any] = {}
_stage_lock = threading.Lock()
#: the stored side (duty stamps, passport) is read at most this often per (project, viewer): every open page polls
STAGE_TTL_S = 15.0


def _stored_side(project_id: str, project: dict, request: Request, user: dict) -> List[Dict[str, Any]]:
    key = (project_id, str((user or {}).get("email") or "").lower())
    with _stage_lock:
        hit = _stage_cache.get(key)
        if hit and time.time() - hit[0] < STAGE_TTL_S:
            return hit[1]
    try:
        from motor_ai_sim.development import stages
        data = stages.collect(project_id, project, request)
    except Exception as exc:                                # noqa: BLE001 - the stages are optional, the live rows are not
        log.debug("stages: stored side not read: %s", exc)
        data = []
    with _stage_lock:
        _stage_cache[key] = (time.time(), data)
        if len(_stage_cache) > 256:
            _stage_cache.pop(next(iter(_stage_cache)))
    return data


@router.get("")
def project_activity_route(project_id: str, request: Request, since: Optional[str] = None) -> Dict[str, Any]:
    """Live runs on the motors linked to this project (members and engineers; same access as the project motor list),
    where they stand in the server queue and when results can be expected, plus the five-stage progress of each variant.

    ``since`` (ISO, optional) = the viewer last visit: results that finished after it are "ready" (default: 24 h)."""
    user, project, _role = access.require_project(request, project_id)
    rows = project_activity(project, app_rows() + host_rows())
    extra = app_extra()
    from motor_ai_sim.development import stages
    derived = stages.derive(_stored_side(project_id, project, request, user), rows,
                            project_failures(project, extra.get("failures") or []), time.time(),
                            server=extra.get("server"), since=_parse_iso(since) if since else None)
    return {"project_id": project_id, "at": _iso(time.time()), "activity": rows,
            "summary": derived["summary"], "stages": derived["stages"]}
