"""The simulation of a project variant as five STAGES, with their state and progress (owner 2026-10-27).

«разбить моделирование на этапы и показывать их выполнение, чтобы был виден прогресс» - a customer sees, per variant,

    1 Electromagnetic points - 2 Thermal - 3 Mechanics - 4 3-D end effect - 5 Report

each with a state (``not_started | queued | computing | done | failed``), counts ("3 of 4 points"), a fraction that fills
its segment of the bar, a place in the queue and an estimate.  It is NOT a new pipeline: every field is derived from what
exists -

* what is already computed and stored: the variant's duty results (``duty_results`` stamps), the duties' saved
  electromagnetic results, the Stage A passport (``end3d_store`` through ``model_source.resolve_end3d``);
* the live runs of the project's motors (``activity.project_activity`` rows, with the queue position and ETA that the
  compute worker added) and the host status file rows;
* the recent failures of the queue (``jobs.recent_failures``);
* the staff flag ``report_reviewed`` on the project link.

:func:`derive` is pure (synthetic inputs in, JSON out); :func:`collect` reads the stored side for a request.

Customer-safe: counts, states, fractions, times - never a run id, an owner, a path or another project's motor.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

STAGE_IDS = ("em", "thermal", "mechanics", "end3d", "report")

#: stored duty-result kinds that answer each per-duty stage
THERMAL_KINDS = ("thermal", "coupled", "coupled_limit", "duty_cycle", "continuous_rating")
MECH_KINDS = ("rotor_stress", "modes", "critical_speeds")

#: a failure older than this is history, not a stage's state
FAILURE_WINDOW_S = 6 * 3600.0
#: without the viewer's last visit, a result is "ready" for this long after it finished
READY_WINDOW_S = 24 * 3600.0

_CONF = ("low", "medium", "high")


def stage_of_kind(kind: str) -> Optional[str]:
    """Which stage a registry run kind belongs to (``None`` = not part of the five: design search, host jobs, ...)."""
    k = str(kind or "").strip().lower()
    if k in ("transient", "cogging"):
        return "em"
    if k.startswith("static3d"):
        return "end3d"
    if k.startswith("mechanical"):
        return "mechanics"
    if k.startswith("coupled") or k.startswith("thermal"):
        return "thermal"
    return None


def _ts(value: Any) -> Optional[float]:
    """Epoch seconds of an ISO stamp (a naive stamp is taken as UTC), or ``None``."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) if value > 0 else None
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


def _iso(epoch: Optional[float]) -> Optional[str]:
    if epoch is None:
        return None
    try:
        return datetime.fromtimestamp(float(epoch), tz=timezone.utc).isoformat(timespec="seconds")
    except (OverflowError, OSError, ValueError):
        return None


def _key(value: Any) -> str:
    return str(value or "").strip().casefold()


def _min_conf(levels: List[str]) -> str:
    return _CONF[min(_CONF.index(x) if x in _CONF else 0 for x in levels)] if levels else "low"


# ── one stage ────────────────────────────────────────────────────────────────

def _stage_eta(rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The estimate of a stage = when its last live run is expected to end (unknown if any of them is unknown)."""
    if not rows:
        return None
    etas = [r.get("eta") if isinstance(r.get("eta"), dict) else None for r in rows]
    finishes = [e.get("expected_finish") if e else None for e in etas]
    starts = [e.get("expected_start") for e in etas if e and e.get("expected_start")]
    if any(f is None for f in finishes):
        return {"expected_start": min(starts) if starts else None, "expected_finish": None, "confidence": "low", "basis": "no_history"}
    return {"expected_start": min(starts) if starts else None, "expected_finish": max(finishes),
            "confidence": _min_conf([str(e.get("confidence") or "low") for e in etas if e]),
            "basis": "progress" if all((e or {}).get("basis") == "progress" for e in etas) else "history"}


def _frac(value: Any) -> float:
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _unit_stage(stage_id: str, units: List[Dict[str, Any]], live: List[Dict[str, Any]], failed: bool) -> Dict[str, Any]:
    """A stage made of per-duty units.  ``units``: ``[{name, done, at}]``; ``live``: this stage's live rows."""
    total = len(units)
    by_duty: Dict[str, List[Dict[str, Any]]] = {}
    loose: List[Dict[str, Any]] = []
    for row in live:
        d = _key(row.get("duty"))
        if d and any(_key(u["name"]) == d for u in units):
            by_duty.setdefault(d, []).append(row)
        else:
            loose.append(row)
    running = [r for r in live if r.get("state") == "running"]
    queued = [r for r in live if r.get("state") == "queued"]
    n_done = sum(1 for u in units if u["done"])
    detail: List[Dict[str, Any]] = []
    partial = 0.0
    counted_done = 0
    for u in units:
        rows = by_duty.get(_key(u["name"]), [])
        run = next((r for r in rows if r.get("state") == "running"), None)
        que = next((r for r in rows if r.get("state") == "queued"), None)
        if run is not None:
            state, p = "computing", _frac(run.get("progress"))
        elif que is not None:
            state, p = "queued", 0.0
        elif u["done"]:
            state, p = "done", 1.0
            counted_done += 1
        else:
            state, p = "not_started", 0.0
        partial += p
        detail.append({"duty": u["name"], "state": state, "progress": round(p, 3) if state == "computing" else None})
    # runs that name no duty of this variant (or none): they work on whatever is still open
    loose_running = [r for r in loose if r.get("state") == "running"]
    if loose_running and total > n_done:
        partial += _frac(loose_running[0].get("progress"))           # one open unit's worth
    fraction = (partial / total) if total else 0.0
    fraction = round(min(1.0, fraction), 3)
    if running:
        state = "computing"
    elif queued:
        state = "queued"
    elif failed:
        state = "failed"
    elif total and counted_done == total:
        state = "done"
    elif counted_done:
        state = "partial"
    else:
        state = "not_started"
    # the stamp of the last finished unit
    stamps = [_ts(u.get("at")) for u in units if u["done"] and _ts(u.get("at")) is not None]
    out: Dict[str, Any] = {"id": stage_id, "state": state, "done": counted_done, "total": total, "fraction": fraction,
                           "position": None, "ahead": None, "eta": None, "finished_at": _iso(max(stamps)) if stamps else None,
                           "detail": detail}
    if queued:
        out["position"] = min(int(r.get("position") or 0) for r in queued) or None
        ahead = [r.get("ahead") for r in queued if isinstance(r.get("ahead"), int)]
        out["ahead"] = min(ahead) if ahead else None
    if running or queued:
        out["eta"] = _stage_eta(running + queued)
    return out


def _single_stage(stage_id: str, done: bool, at: Optional[str], live: List[Dict[str, Any]], failed: bool,
                  note: Optional[str] = None) -> Dict[str, Any]:
    running = [r for r in live if r.get("state") == "running"]
    queued = [r for r in live if r.get("state") == "queued"]
    if running:
        state, fraction = "computing", _frac(running[0].get("progress"))
    elif queued:
        state, fraction = "queued", 0.0
    elif failed and not done:
        state, fraction = "failed", 0.0
    elif done:
        state, fraction = "done", 1.0
    else:
        state, fraction = "not_started", 0.0
    out: Dict[str, Any] = {"id": stage_id, "state": state, "done": 1 if (done and state == "done") else 0, "total": 1,
                           "fraction": round(fraction, 3), "position": None, "ahead": None, "eta": None,
                           "finished_at": _iso(_ts(at)) if (done and at) else None, "detail": []}
    if queued:
        out["position"] = min(int(r.get("position") or 0) for r in queued) or None
        ahead = [r.get("ahead") for r in queued if isinstance(r.get("ahead"), int)]
        out["ahead"] = min(ahead) if ahead else None
    if running or queued:
        out["eta"] = _stage_eta(running + queued)
    if note:
        out["note"] = note
    return out


# ── one variant ──────────────────────────────────────────────────────────────

def derive_motor(motor: Dict[str, Any], rows: List[Dict[str, Any]], failures: List[Dict[str, Any]], now: float) -> Dict[str, Any]:
    """The stages of ONE variant.

    ``motor``: ``{die, config, duties: [{name, em_done, em_at}], stored: {duty: {kind: {computed_at}}}, end3d: bool,
    end3d_at, report_reviewed: {at}|None}``.  ``rows``: live rows of THIS variant; ``failures``: recent failures of it
    (``{kind, duty, finished_at}``).
    """
    duties = [d for d in (motor.get("duties") or []) if isinstance(d, dict) and d.get("name")]
    stored = motor.get("stored") if isinstance(motor.get("stored"), dict) else {}

    def kinds_at(name: str, kinds) -> Optional[str]:
        entry = stored.get(name) if isinstance(stored.get(name), dict) else {}
        stamps = [(_ts((entry.get(k) or {}).get("computed_at") or (entry.get(k) or {}).get("recorded_at")), k)
                  for k in kinds if isinstance(entry.get(k), dict)]
        if not stamps:
            return None
        stamp = max(s for s, _ in stamps if s is not None) if any(s is not None for s, _ in stamps) else None
        return _iso(stamp) if stamp is not None else "present"

    live_by: Dict[str, List[Dict[str, Any]]] = {i: [] for i in STAGE_IDS}
    for r in rows:
        sid = stage_of_kind(str(r.get("kind") or ""))
        if sid:
            live_by[sid].append(r)
    failed_by: Dict[str, float] = {}
    for f in failures:
        sid = stage_of_kind(str(f.get("kind") or ""))
        t = _ts(f.get("finished_at"))
        if sid and t is not None and now - t <= FAILURE_WINDOW_S:
            failed_by[sid] = max(failed_by.get(sid, 0.0), t)

    def failed(sid: str, last_done: Optional[str]) -> bool:
        t = failed_by.get(sid)
        if t is None:
            return False
        done_t = _ts(last_done)
        return done_t is None or t > done_t

    em_units = [{"name": str(d["name"]), "done": bool(d.get("em_done")), "at": d.get("em_at")} for d in duties]
    th_units, me_units = [], []
    for d in duties:
        name = str(d["name"])
        t_at, m_at = kinds_at(name, THERMAL_KINDS), kinds_at(name, MECH_KINDS)
        th_units.append({"name": name, "done": t_at is not None, "at": t_at if t_at != "present" else None})
        me_units.append({"name": name, "done": m_at is not None, "at": m_at if m_at != "present" else None})

    def last(units) -> Optional[str]:
        stamps = [_ts(u.get("at")) for u in units if u["done"] and _ts(u.get("at")) is not None]
        return _iso(max(stamps)) if stamps else None

    stages = [
        _unit_stage("em", em_units, live_by["em"], failed("em", last(em_units))),
        _unit_stage("thermal", th_units, live_by["thermal"], failed("thermal", last(th_units))),
        _unit_stage("mechanics", me_units, live_by["mechanics"], failed("mechanics", last(me_units))),
        _single_stage("end3d", bool(motor.get("end3d")), motor.get("end3d_at"), live_by["end3d"],
                      failed("end3d", motor.get("end3d_at"))),
    ]
    reviewed = motor.get("report_reviewed") if isinstance(motor.get("report_reviewed"), dict) else None
    rep = _single_stage("report", reviewed is not None, (reviewed or {}).get("at"), [], False)
    if reviewed is None and all(s["state"] == "done" for s in stages[:4]):
        rep["note"] = "awaiting_review"
    stages.append(rep)
    live_rows = list(rows)
    running = [r for r in live_rows if r.get("state") == "running"]
    queued = [r for r in live_rows if r.get("state") == "queued"]
    percent = round(100.0 * sum(float(s["fraction"]) for s in stages) / len(stages))
    current = next((i + 1 for i, s in enumerate(stages) if s["state"] != "done"), len(stages))
    all_done = all(s["state"] == "done" for s in stages)
    eta = _stage_eta([r for r in live_rows if r.get("state") in ("running", "queued")])
    last_done = [_ts(s.get("finished_at")) for s in stages if s.get("finished_at")]
    return {"die": motor.get("die"), "config": motor.get("config"), "stages": stages, "percent": percent,
            "stage_index": current, "stage_count": len(stages), "complete": all_done,
            "runs_running": len(running), "runs_queued": len(queued), "eta": eta,
            "last_finished_at": _iso(max(t for t in last_done if t is not None)) if any(t is not None for t in last_done) else None}


# ── the project ──────────────────────────────────────────────────────────────

def derive(motors: List[Dict[str, Any]], rows: List[Dict[str, Any]], failures: List[Dict[str, Any]], now: float,
           server: Optional[Dict[str, Any]] = None, since: Optional[float] = None) -> Dict[str, Any]:
    """``{"stages": [per variant], "summary": {...}}`` for a project.

    ``rows`` are ALL live rows of the project (including host rows, which belong to no stage but count in the summary).
    """
    per_motor: List[Dict[str, Any]] = []
    for m in motors:
        mk = (_key(m.get("die")), _key(m.get("config")))
        mine = [r for r in rows if (_key(r.get("die")), _key(r.get("config"))) == mk]
        fails = [f for f in failures if (_key(f.get("die")), _key(f.get("config"))) == mk]
        per_motor.append(derive_motor(m, mine, fails, now))
    running = [r for r in rows if r.get("state") == "running"]
    queued = [r for r in rows if r.get("state") == "queued"]
    last = [t for t in (_ts(p.get("last_finished_at")) for p in per_motor) if t is not None]
    last_finished = max(last) if last else None
    ready_after = since if since is not None else now - READY_WINDOW_S
    if running:
        state = "computing"
    elif queued:
        state = "queued"
    elif last_finished is not None and last_finished > ready_after:
        state = "results_ready"
    else:
        state = "idle"
    eta = _stage_eta(running + queued)
    # host-file rows carry only ``eta_min``: a minute count the scheduler itself gave
    summary: Dict[str, Any] = {"state": state, "runs_running": len(running), "runs_queued": len(queued),
                               "expected_all_done": (eta or {}).get("expected_finish"),
                               "confidence": (eta or {}).get("confidence"), "basis": (eta or {}).get("basis"),
                               "last_finished_at": _iso(last_finished)}
    if server:
        summary["server"] = {"running": int(server.get("running") or 0), "queued": int(server.get("queued") or 0)}
    return {"stages": per_motor, "summary": summary}


# ── the stored side, read for one request ────────────────────────────────────

def collect(project_id: str, project: dict, request: Any) -> List[Dict[str, Any]]:
    """The stored facts of the project's CURRENT variants, through the same gates as the project motor list.

    A variant the caller may not open yields an entry without duties (its live runs still show, its stored side does not).
    """
    from pathlib import Path
    import yaml
    from motor_ai_sim.development import motors as M

    from motor_ai_sim.routes import family
    out: List[Dict[str, Any]] = []
    index = (M._index().get("motors") or {})
    ident = M._identity(request)
    authorization = request.headers.get("authorization")
    entries_cache: list = []
    for link in project.get("linked_motors", []):
        if not isinstance(link, dict) or not link.get("die") or not link.get("config"):
            continue
        die, config = str(link["die"]), str(link["config"])
        if str(link.get("status") or "current") == "trial":
            continue
        row = index.get(M._key(die, config))
        if not isinstance(row, dict) or row.get("project_id") != project_id:
            continue
        reviewed = link.get("report_reviewed") if isinstance(link.get("report_reviewed"), dict) else None
        item: Dict[str, Any] = {"die": die, "config": config, "duties": [], "stored": {}, "end3d": False, "end3d_at": None,
                                "report_reviewed": reviewed}
        try:
            open_die = M._open_die_name(die, ident, family, entries_cache)
            family._require_die_access(open_die, authorization, config)
            cfg_path = Path(str(family._cfg_file(open_die, config))).resolve(strict=True)
            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        except Exception:                                   # noqa: BLE001 - not openable by this caller
            out.append(item)
            continue
        try:
            from motor_ai_sim import duty_results
            stored = duty_results.get(open_die, config) or {}
        except Exception:                                   # noqa: BLE001
            stored = {}
        item["stored"] = {str(d): {str(k): {"computed_at": (v.get("computed_at") or v.get("recorded_at"))}
                                   for k, v in kinds.items() if isinstance(v, dict)}
                          for d, kinds in stored.items() if isinstance(kinds, dict)}
        for duty in cfg.get("duties") or []:
            if not isinstance(duty, dict) or not duty.get("name"):
                continue
            name = str(duty["name"])
            kinds = stored.get(name) if isinstance(stored.get(name), dict) else {}
            saved = bool(duty.get("result") or duty.get("summary"))
            run_at = (kinds.get("em_run") or {}).get("computed_at") if isinstance(kinds.get("em_run"), dict) else None
            ptr = kinds.get("em") if isinstance(kinds.get("em"), dict) else {}
            em_at = run_at or ptr.get("saved_at") or ptr.get("computed_at")
            item["duties"].append({"name": name, "em_done": bool(saved or run_at), "em_at": em_at})
        try:
            from motor_ai_sim.motor_tests.model_source import resolve_end3d
            try:
                die_doc = family._load_yaml(family._die_file(open_die), "die")
            except Exception:                               # noqa: BLE001
                die_doc = {}
            e3 = resolve_end3d(die_doc, cfg)
            if isinstance(e3, dict) and not e3.get("inherited"):
                item["end3d"] = True
                item["end3d_at"] = e3.get("adopted_utc") or e3.get("generated_utc")
        except Exception:                                   # noqa: BLE001 - no passport is the normal state
            log.debug("stages: no passport for %s/%s", die, config, exc_info=True)
        out.append(item)
    return out
