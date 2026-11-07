"""The project TIMELINE the customer sees (owner 2026-11-03): coarse lanes per iteration, coordinated with the production of
the project's motor.  Pure (no I/O): the stored document's shape and validation, seeding from the cost estimate, the PLAN,
the FORECAST and the customer payload.  Storage, the production reading and the routes live in ``timeline_routes``.

Stored document (``<project>/timeline/timeline.json``, admin-edited, optimistic ``revision``)::

    {"version": 1, "revision": n, "production": {"die", "config"} | null,
     "iterations": [{"index": 1, "start_date": "YYYY-MM-DD" | null,
                     "lanes": {"tooling": LANE, "motor": LANE, "free_run": LANE, "dyno": LANE, "propeller": LANE?,
                               "shipping": LANE}}],
     "baseline": {"frozen_at", "frozen_by", "revision", "iterations": [{"index", "lanes": {lane: {"start", "end"}}}]} | null}

    LANE = {"plan_weeks": float | null, "start": date | null, "end": date | null, "done_date": date | null, "note": "one EN line"}

``production`` is the admin's pick of the linked motor whose production drives the forecast; ``null`` = the project's first
``current`` linked motor that has a production.  ``propeller`` exists only when the admin adds it or the iteration's bench
(cost estimate) is ``propeller`` / ``both``.

PLAN (dependency rules, kept simple on purpose):

* nothing has a date until the iteration's ``start_date`` is set (the admin sets it, e.g. when the payment arrives); the
  durations are still shown;
* Tooling and Motor manufacturing both start at the iteration start (they overlap); the Motor ends no earlier than Tooling
  (the motor waits for its tooling);
* Free-run, Dyno, Propeller (when present) and Shipping follow in sequence: each starts when the previous lane ends;
* a lane's ``end`` = start + ``plan_weeks`` (``None`` counts as 0); a manual ``start`` can only DELAY a lane, a manual ``end``
  sets its end (ignored when it falls before the start).
* The admin may FREEZE the plan as the BASELINE; from then on the customer's "plan" bars (and "late") are the baseline, the
  live plan only feeds the forecast.

FORECAST (computed on every read, ``today`` given):

* Tooling and Motor of the PRODUCTION ITERATION (the first iteration whose lane is not done) follow the production of the
  motor (``list_progress``): the latest expected date over the parts (the received date of a received part; for an open
  part the supplier's confirmed date, else its due date, else the requested date, else ordered + lead time).  When every
  part is received the lane is done on the last received date.  While an open part has no date the forecast is never
  earlier than the plan.  No part with a date: the forecast is the plan;
* a lane with a ``done_date`` ends on it; an unfinished lane never ends before ``today``;
* later lanes keep their planned duration and shift with the forecast of the lane before them.

STATUS per lane: ``done`` (a done date, every part received, or a 0-week lane already passed) / ``late`` (forecast end
after the plan end; an unfinished lane past its plan end is late because its forecast never ends before today) /
``in_progress`` (started) / ``planned``.
READINESS: Tooling and Motor of the production iteration only, the % of parts received.

The customer payload (``customer_view``) is a WHITELIST: per iteration and lane the lane id, weeks, plan and forecast
dates, status, readiness % and the public note; plus today, the motor's display name and the baseline date.  Never a part
name, a supplier, a price, a BOM row, a factory contact or the production's die / config."""
from __future__ import annotations

import datetime
import re
from typing import Any, Dict, Iterable, List, Optional

VERSION = 1
LANES = ("tooling", "motor", "free_run", "dyno", "propeller", "shipping")
OPTIONAL_LANES = ("propeller",)
SEQUENCE = ("free_run", "dyno", "propeller", "shipping")
#: lanes whose forecast follows the production of the motor: lane -> procurement list ("" = motor BOM)
PRODUCTION_LANES = {"tooling": "tooling", "motor": ""}
STATUSES = ("planned", "in_progress", "done", "late")
FREE_RUN_WEEKS = 1.0
PROPELLER_WEEKS = 1.0
MAX_WEEKS = 104.0
MAX_ITERATIONS = 10
MAX_NOTE = 120
LANE_FIELDS = ("plan_weeks", "start", "end", "done_date", "note")

CUSTOMER_KEYS = ("today", "motor", "baseline_on", "iterations")
CUSTOMER_ITERATION_KEYS = ("index", "start_date", "lanes")
CUSTOMER_LANE_KEYS = ("lane", "weeks", "plan_start", "plan_end", "forecast_start", "forecast_end", "status", "readiness_pct",
                      "note")

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class TimelineError(ValueError):
    """A malformed timeline payload (-> HTTP 422)."""


# ---------------------------------------------------------------------------------------------------------------------
# shape and validation
# ---------------------------------------------------------------------------------------------------------------------

def _date(v: Any, what: str) -> Optional[str]:
    if v is None or v == "":
        return None
    s = str(v).strip()[:10] if isinstance(v, str) else None
    if not s or not _DATE.match(s):
        raise TimelineError("%s: a date YYYY-MM-DD expected" % what)
    try:
        datetime.date.fromisoformat(s)
    except ValueError:
        raise TimelineError("%s: not a valid date" % what) from None
    return s


def _weeks(v: Any, what: str) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise TimelineError("%s: a number of weeks expected" % what) from None
    if x != x or x < 0 or x > MAX_WEEKS:
        raise TimelineError("%s: 0..%d weeks" % (what, int(MAX_WEEKS)))
    return round(x, 2)


def _note(v: Any) -> str:
    """One short line (no line breaks)."""
    return " ".join(str(v or "").split())[:MAX_NOTE]


def blank_lane(weeks: Optional[float] = None) -> dict:
    return {"plan_weeks": weeks, "start": None, "end": None, "done_date": None, "note": ""}


def normalize_lane(raw: Any, what: str) -> dict:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise TimelineError("%s: an object expected" % what)
    lane = {"plan_weeks": _weeks(raw.get("plan_weeks"), what + ".plan_weeks"), "start": _date(raw.get("start"), what + ".start"),
            "end": _date(raw.get("end"), what + ".end"), "done_date": _date(raw.get("done_date"), what + ".done_date"),
            "note": _note(raw.get("note"))}
    if lane["start"] and lane["end"] and lane["end"] < lane["start"]:
        raise TimelineError("%s: end before start" % what)
    return lane


def new_iteration(index: int, *, propeller: bool = False) -> dict:
    lanes = {lid: blank_lane() for lid in LANES if lid not in OPTIONAL_LANES}
    lanes["free_run"]["plan_weeks"] = FREE_RUN_WEEKS
    if propeller:
        lanes["propeller"] = blank_lane(PROPELLER_WEEKS)
    return {"index": index, "start_date": None, "lanes": lanes}


def normalize_iteration(raw: Any, index: int) -> dict:
    if not isinstance(raw, dict):
        raise TimelineError("iteration %d: an object expected" % index)
    lanes_raw = raw.get("lanes") or {}
    if not isinstance(lanes_raw, dict):
        raise TimelineError("iteration %d: lanes must be an object" % index)
    unknown = sorted(set(lanes_raw) - set(LANES))
    if unknown:
        raise TimelineError("iteration %d: unknown lane(s) %s" % (index, ", ".join(map(str, unknown))))
    lanes = {}
    for lid in LANES:
        if lid in OPTIONAL_LANES and lanes_raw.get(lid) is None:
            continue
        lanes[lid] = normalize_lane(lanes_raw.get(lid), "iteration %d %s" % (index, lid))
    return {"index": index, "start_date": _date(raw.get("start_date"), "iteration %d start_date" % index), "lanes": lanes}


def _production_ref(raw: Any) -> Optional[dict]:
    if raw is None or raw == {}:
        return None
    if not isinstance(raw, dict) or not str(raw.get("die") or "").strip() or not str(raw.get("config") or "").strip():
        raise TimelineError("production: {die, config} or null expected")
    return {"die": str(raw["die"]).strip()[:200], "config": str(raw["config"]).strip()[:200]}


def normalize_timeline(raw: Any) -> dict:
    """The editable part of a timeline (``production`` + ``iterations``); the baseline is never taken from a request.
    Iterations are renumbered 1..n in the order given."""
    if not isinstance(raw, dict):
        raise TimelineError("a timeline object expected")
    iterations = raw.get("iterations") or []
    if not isinstance(iterations, list):
        raise TimelineError("iterations must be a list")
    if len(iterations) > MAX_ITERATIONS:
        raise TimelineError("at most %d iterations" % MAX_ITERATIONS)
    return {"version": VERSION, "production": _production_ref(raw.get("production")),
            "iterations": [normalize_iteration(it, i + 1) for i, it in enumerate(iterations)]}


def _normalize_baseline(raw: Any) -> Optional[dict]:
    if not isinstance(raw, dict) or not isinstance(raw.get("iterations"), list):
        return None
    out = []
    for it in raw["iterations"]:
        if not isinstance(it, dict):
            continue
        lanes = {}
        for lid, lane in (it.get("lanes") or {}).items():
            if lid in LANES and isinstance(lane, dict):
                try:
                    lanes[lid] = {"start": _date(lane.get("start"), "baseline"), "end": _date(lane.get("end"), "baseline")}
                except TimelineError:
                    continue
        try:
            out.append({"index": int(it.get("index")), "lanes": lanes})
        except (TypeError, ValueError):
            continue
    return {"frozen_at": str(raw.get("frozen_at") or ""), "frozen_by": str(raw.get("frozen_by") or ""),
            "revision": raw.get("revision"), "iterations": out}


def from_stored(stored: Any) -> Optional[dict]:
    """The stored document normalised (``None`` when there is none or it cannot be read)."""
    if not isinstance(stored, dict) or stored.get("version") != VERSION:
        return None
    try:
        doc = normalize_timeline(stored)
    except TimelineError:
        return None
    doc["revision"] = int(stored.get("revision") or 0)
    doc["baseline"] = _normalize_baseline(stored.get("baseline"))
    for k in ("updated_at", "updated_by"):
        if stored.get(k):
            doc[k] = str(stored[k])
    return doc


def empty_timeline() -> dict:
    return {"version": VERSION, "production": None, "iterations": [], "baseline": None, "revision": 0}


# ---------------------------------------------------------------------------------------------------------------------
# seeding from the cost estimate
# ---------------------------------------------------------------------------------------------------------------------

def _est_weeks(it: dict, line: str) -> Optional[float]:
    w = ((it.get("lines") or {}).get(line) or {}).get("weeks")
    try:
        return None if w is None else round(float(w), 2)
    except (TypeError, ValueError):
        return None


def seed_from_estimate(estimate: dict, previous: Optional[dict] = None) -> dict:
    """The iterations and plan weeks of the cost estimate: tooling <- tooling, motor <- manufacturing, dyno <- testing (bench
    dyno / both; 0 without a dyno), propeller <- testing (bench propeller; 1 week next to a dyno for ``both``), shipping <-
    shipping, free-run 1 week.  The start dates, done dates, manual start / end and notes of an iteration already in
    ``previous`` are kept; its plan weeks are replaced."""
    prev_iters = {it["index"]: it for it in (previous or {}).get("iterations") or []}
    out = []
    for i, est_it in enumerate(estimate.get("iterations") or []):
        index = i + 1
        bench = str(est_it.get("bench") or "none")
        propeller = bench in ("propeller", "both")
        it = new_iteration(index, propeller=propeller)
        testing = _est_weeks(est_it, "testing")
        weeks = {"tooling": _est_weeks(est_it, "tooling"), "motor": _est_weeks(est_it, "manufacturing"),
                 "free_run": FREE_RUN_WEEKS, "dyno": testing if bench in ("dyno", "both") else 0.0,
                 "shipping": _est_weeks(est_it, "shipping")}
        if propeller:
            weeks["propeller"] = testing if bench == "propeller" else PROPELLER_WEEKS
        old = prev_iters.get(index)
        if old:
            it["start_date"] = old.get("start_date")
        for lid, lane in it["lanes"].items():
            lane["plan_weeks"] = weeks.get(lid)
            if old and lid in old["lanes"]:
                for k in ("start", "end", "done_date", "note"):
                    lane[k] = old["lanes"][lid].get(k)
        out.append(it)
    return {"version": VERSION, "production": (previous or {}).get("production"), "iterations": out}


# ---------------------------------------------------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------------------------------------------------

def _d(s: Optional[str]) -> Optional[datetime.date]:
    return datetime.date.fromisoformat(s) if s else None


def _iso(d: Optional[datetime.date]) -> Optional[str]:
    return d.isoformat() if d else None


def _days(weeks: Optional[float]) -> int:
    return int(round(float(weeks or 0) * 7))


def lane_order(it: dict) -> List[str]:
    return [lid for lid in LANES if lid in it["lanes"]]


def plan_iteration(it: dict) -> Dict[str, dict]:
    """``{lane: {"start", "end"}}`` (ISO dates, ``None`` without an iteration start date) by the rules in the module doc."""
    out: Dict[str, dict] = {}
    start = _d(it.get("start_date"))
    lanes = it["lanes"]
    if start is None:
        return {lid: {"start": None, "end": None} for lid in lane_order(it)}

    def span(lane: dict, earliest: datetime.date, *, free_start: bool = False) -> tuple:
        s = _d(lane.get("start"))
        s = (s or earliest) if free_start else max(s or earliest, earliest)
        e = _d(lane.get("end"))
        if e is None or e < s:
            e = s + datetime.timedelta(days=_days(lane.get("plan_weeks")))
        return s, e

    ts, te = span(lanes["tooling"], start, free_start=True)
    ms, me = span(lanes["motor"], start, free_start=True)
    me = max(me, te)                                     # the motor waits for its tooling
    out["tooling"] = {"start": _iso(ts), "end": _iso(te)}
    out["motor"] = {"start": _iso(ms), "end": _iso(me)}
    prev = me
    for lid in SEQUENCE:
        if lid not in lanes:
            continue
        s, e = span(lanes[lid], prev)
        out[lid] = {"start": _iso(s), "end": _iso(e)}
        prev = e
    return out


def plan(doc: dict) -> List[Dict[str, dict]]:
    return [plan_iteration(it) for it in doc.get("iterations") or []]


def baseline_from(doc: dict, actor: str, now: str) -> dict:
    """The current live plan frozen as the baseline."""
    return {"frozen_at": now, "frozen_by": actor, "revision": doc.get("revision"),
            "iterations": [{"index": it["index"], "lanes": p} for it, p in zip(doc.get("iterations") or [], plan(doc))]}


# ---------------------------------------------------------------------------------------------------------------------
# production progress and forecast
# ---------------------------------------------------------------------------------------------------------------------

def _row_date(row: dict) -> Optional[str]:
    """The date a part is (expected to be) at the factory: received -> its received date; open -> the supplier's
    confirmed date, else due, else requested, else ordered + lead time.  ``None`` = no date."""
    def ok(v: Any) -> Optional[str]:
        s = str(v or "").strip()[:10]
        if not _DATE.match(s):
            return None
        try:
            datetime.date.fromisoformat(s)
        except ValueError:
            return None
        return s
    if row.get("received"):
        return ok(row.get("received_on"))
    for k in ("confirmed_on", "due_on", "requested_on"):
        if ok(row.get(k)):
            return ok(row.get(k))
    ordered = ok(row.get("ordered_on"))
    if ordered:
        try:
            lead = max(0, int(row.get("lead_time_days") or 0))
        except (TypeError, ValueError):
            lead = 0
        return (datetime.date.fromisoformat(ordered) + datetime.timedelta(days=lead)).isoformat()
    return None


def list_progress(rows: Iterable[dict]) -> Optional[dict]:
    """The progress of one procurement list (motor BOM or tooling) from its rows ``{received: bool, received_on,
    confirmed_on, due_on, requested_on, ordered_on, lead_time_days}``: ``{"end", "all_received", "dated_all",
    "readiness_pct", "parts"}``; ``None`` for a list without parts."""
    rows = [r for r in rows if isinstance(r, dict)]
    if not rows:
        return None
    dates = [_row_date(r) for r in rows]
    received = sum(1 for r in rows if r.get("received"))
    known = [d for d in dates if d]
    open_undated = any(d is None and not r.get("received") for r, d in zip(rows, dates))
    return {"end": max(known) if known else None, "all_received": received == len(rows),
            "dated_all": not open_undated, "readiness_pct": round(received / len(rows) * 100, 1), "parts": len(rows)}


def _production_iteration(doc: dict, lid: str) -> Optional[int]:
    """The index of the first iteration whose ``lid`` lane is not done: the production of the motor drives that one."""
    for it in doc.get("iterations") or []:
        lane = it["lanes"].get(lid)
        if lane is not None and not lane.get("done_date"):
            return it["index"]
    return None


def forecast_iteration(it: dict, planned: Dict[str, dict], today: str, progress: Optional[Dict[str, Optional[dict]]] = None) -> Dict[str, dict]:
    """``{lane: {"start", "end", "done", "readiness_pct"}}`` - see FORECAST in the module doc.  ``progress`` maps "tooling" /
    "motor" to a ``list_progress`` result when this is the production iteration of that lane."""
    progress = progress or {}
    out: Dict[str, dict] = {}
    t = _d(today)
    lanes = it["lanes"]
    if planned.get("tooling", {}).get("start") is None:
        for lid in lane_order(it):
            prog = progress.get(lid)
            out[lid] = {"start": None, "end": None, "done": bool(lanes[lid].get("done_date") or (prog or {}).get("all_received")),
                        "readiness_pct": (prog or {}).get("readiness_pct")}
        return out

    def finish(lid: str, start: datetime.date, plan_end: datetime.date, prog: Optional[dict]) -> tuple:
        lane = lanes[lid]
        done = _d(lane.get("done_date"))
        if done:
            return min(start, done), done, True
        if prog and prog.get("all_received"):
            end = _d(prog["end"]) if prog.get("end") else min(plan_end, t)      # received without a date: done by today
            return min(start, end), end, True
        end = plan_end
        if prog and prog.get("end"):
            end = _d(prog["end"]) if prog.get("dated_all") else max(_d(prog["end"]), plan_end)
        if _days(lane.get("plan_weeks")) > 0 or prog:
            end = max(end, t)                            # an unfinished lane never ends in the past
        return start, max(end, start), False

    for lid in ("tooling", "motor"):
        p = planned[lid]
        s, e, done = finish(lid, _d(p["start"]), _d(p["end"]), progress.get(lid))
        if lid == "motor" and not done:
            e = max(e, _d(out["tooling"]["end"]))         # the motor waits for its tooling
        out[lid] = {"start": _iso(s), "end": _iso(e), "done": done, "readiness_pct": (progress.get(lid) or {}).get("readiness_pct")}
    prev = _d(out["motor"]["end"])
    for lid in SEQUENCE:
        if lid not in lanes:
            continue
        p = planned[lid]
        ps, pe = _d(p["start"]), _d(p["end"])
        manual = _d(lanes[lid].get("start"))
        s = max(manual, prev) if manual else prev
        s, e, done = finish(lid, s, s + (pe - ps), None)
        out[lid] = {"start": _iso(s), "end": _iso(e), "done": done, "readiness_pct": None}
        prev = e
    return out


def status_of(fc: dict, plan_end: Optional[str], today: str) -> str:
    if fc.get("done"):
        return "done"
    if not fc.get("end") or not plan_end:
        return "planned"
    if fc.get("start") == fc["end"] and fc["end"] <= today:
        return "done"                                    # a lane of 0 weeks (no dyno in this iteration) once passed
    if fc["end"] > plan_end:
        return "late"
    if fc.get("start") and fc["start"] <= today:
        return "in_progress"
    return "planned"


def _baseline_lane(doc: dict, index: int, lid: str) -> Optional[dict]:
    for it in (doc.get("baseline") or {}).get("iterations") or []:
        if it.get("index") == index:
            lane = (it.get("lanes") or {}).get(lid)
            return lane if isinstance(lane, dict) and lane.get("end") else None
    return None


def compute(doc: dict, today: str, progress: Optional[Dict[str, Optional[dict]]] = None) -> List[dict]:
    """Every iteration with its lanes: live plan, customer plan (the baseline when frozen), forecast and status.
    ``progress`` = ``{"tooling": list_progress | None, "motor": list_progress | None}`` of the linked production."""
    progress = progress or {}
    drivers = {lid: _production_iteration(doc, lid) for lid in PRODUCTION_LANES}
    out = []
    for it in doc.get("iterations") or []:
        planned = plan_iteration(it)
        prog = {lid: progress.get(lid) for lid, idx in drivers.items() if idx == it["index"]}
        fc = forecast_iteration(it, planned, today, prog)
        lanes = []
        for lid in lane_order(it):
            base = _baseline_lane(doc, it["index"], lid)
            shown = base or planned[lid]
            lanes.append({"lane": lid, "weeks": it["lanes"][lid].get("plan_weeks"),
                          "plan_start": shown.get("start"), "plan_end": shown.get("end"),
                          "live_start": planned[lid]["start"], "live_end": planned[lid]["end"],
                          "forecast_start": fc[lid]["start"], "forecast_end": fc[lid]["end"],
                          "status": status_of(fc[lid], shown.get("end"), today),
                          "readiness_pct": fc[lid]["readiness_pct"], "note": it["lanes"][lid].get("note") or "",
                          "done_date": it["lanes"][lid].get("done_date"), "from_production": lid in prog and prog[lid] is not None})
        out.append({"index": it["index"], "start_date": it.get("start_date"), "lanes": lanes})
    return out


def customer_view(doc: dict, today: str, progress: Optional[Dict[str, Optional[dict]]] = None,
                  motor: Optional[dict] = None) -> dict:
    """The WHITELISTED payload of ``GET /api/projects/{id}/timeline`` (see the module doc)."""
    iterations = [{"index": it["index"], "start_date": it["start_date"],
                   "lanes": [{k: lane[k] for k in CUSTOMER_LANE_KEYS} for lane in it["lanes"]]}
                  for it in compute(doc, today, progress)]
    title = None
    if isinstance(motor, dict) and (motor.get("en") or motor.get("zh")):
        title = {"en": str(motor.get("en") or ""), "zh": str(motor.get("zh") or "")}
    baseline = doc.get("baseline") or {}
    view = {"today": today, "motor": title, "baseline_on": (str(baseline.get("frozen_at") or "")[:10] or None),
            "iterations": iterations}
    assert tuple(view) == CUSTOMER_KEYS
    return view


# ---------------------------------------------------------------------------------------------------------------------
# what changed (audit)
# ---------------------------------------------------------------------------------------------------------------------

def diff_paths(old: Optional[dict], new: dict) -> List[str]:
    """The edited fields as paths (``iterations[1].lanes.motor.plan_weeks``), for the audit."""
    paths: List[str] = []
    if (old or {}).get("production") != new.get("production"):
        paths.append("production")
    old_iters = {it["index"]: it for it in (old or {}).get("iterations") or []}
    new_iters = {it["index"]: it for it in new.get("iterations") or []}
    for idx in sorted(set(old_iters) | set(new_iters)):
        a, b = old_iters.get(idx), new_iters.get(idx)
        if a is None or b is None:
            paths.append("iterations[%d]%s" % (idx, " added" if a is None else " removed"))
            continue
        if a.get("start_date") != b.get("start_date"):
            paths.append("iterations[%d].start_date" % idx)
        for lid in LANES:
            la, lb = a["lanes"].get(lid), b["lanes"].get(lid)
            if la is None and lb is None:
                continue
            if la is None or lb is None:
                paths.append("iterations[%d].lanes.%s%s" % (idx, lid, " added" if la is None else " removed"))
                continue
            for k in LANE_FIELDS:
                if la.get(k) != lb.get(k):
                    paths.append("iterations[%d].lanes.%s.%s" % (idx, lid, k))
    return paths
