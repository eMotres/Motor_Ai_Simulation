"""Saved optimisation runs of one die / configuration.

A finished descent or auto run (the app's ``_descent_state``) is kept as ONE json file beside the motor it was made
on: ``<die folder>/optimization_runs/<configuration>/<run_id>.json``.  The die folder moves as a whole when the die is
renamed or duplicated, so the runs follow it (nothing in the file carries the die name).

The file is the app's own run state, not a new format: ``{"schema": 1, "run_id", "kind", "saved_at", "source",
"state": <the _descent_state as the worker wrote it>, "request": <optional, what started it>}``.  The Optimization tab
opens ``state`` into the chart it already renders; the machine fingerprint inside ``state`` is checked by the existing
re-check / Apply path against the machine that is live at that moment, exactly as for the run of the last session.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA = 1
DIRNAME = "optimization_runs"
MAX_BYTES = 40_000_000
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,95}$")


class RunRejected(ValueError):
    """The run cannot be stored; the message names why."""


def runs_dir(die_dir: Path, config: str) -> Path:
    return Path(die_dir) / DIRNAME / str(config)


def check_run_id(run_id: Any) -> str:
    rid = str(run_id or "").strip()
    if not _ID_RE.match(rid):
        raise RunRejected("run_id %r is not usable as a file name (letters, digits, '_.-', up to 96)" % (run_id,))
    return rid


def _finite(x: Any) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v and abs(v) != float("inf") else None


def validate_state(state: Any) -> Dict[str, Any]:
    """Shape check of a ``_descent_state``: reject what the tab could not render, never repair it."""
    if not isinstance(state, dict):
        raise RunRejected("the run state is not an object")
    hist, pts, var = state.get("history"), state.get("points"), state.get("variables")
    if not (isinstance(hist, list) and hist) and not (isinstance(pts, list) and pts):
        raise RunRejected("the run has neither history nor points: nothing to show")
    if not isinstance(var, list) or not var:
        raise RunRejected("the run has no variables list")
    if state.get("running"):
        raise RunRejected("the run is still marked running; import a finished state")
    return state


def summarize(state: Dict[str, Any]) -> Dict[str, Any]:
    best = state.get("best") if isinstance(state.get("best"), dict) else {}
    auto = state.get("auto") if isinstance(state.get("auto"), dict) else {}
    return {
        "kind": "auto" if auto else "descent",
        "n_evals": state.get("n_evals"),
        "iter": state.get("iter"),
        "variables": [str(v.get("name")) for v in (state.get("variables") or []) if isinstance(v, dict)],
        "best_cost": _finite(best.get("cost")),
        "converged": state.get("converged"),
        "final_validation_status": state.get("final_validation_status"),
        "has_machine_fp": bool(state.get("machine_fp")),
        "gap_layers": _finite((state.get("eval_params") or {}).get("gap_layers")),
        "error": state.get("error"),
    }


def designations(state: Dict[str, Any], die: str, config: str) -> Dict[str, Any]:
    """The standard motor designation of the run's base motor and of EACH evaluated candidate (owner 2026-11-17: an
    optimisation run whose candidates differ in turns shows the right name per candidate).  A candidate's name is its
    ``overrides`` applied to the motor's stored files at import time; the run state itself is not touched.  ``{}`` when
    nothing can be made - a stamp never fails an import."""
    try:
        from motor_ai_sim import motor_designation as MD
        base = MD.stamp(die, config).get("designation")
        points = []
        for p in state.get("points") or []:
            ov = p.get("overrides") if isinstance(p, dict) else None
            points.append(MD.stamp(die, config, geometry=ov).get("designation") if isinstance(ov, dict) and ov else base)
        return {"base": base, "points": points}
    except Exception:                                       # noqa: BLE001
        return {}


def build_record(state: Dict[str, Any], run_id: Optional[str] = None, request: Any = None,
                 source: Optional[str] = None, *, die: Optional[str] = None, config: Optional[str] = None) -> Dict[str, Any]:
    validate_state(state)
    rid = check_run_id(run_id or state.get("run_id"))
    rec = {"schema": SCHEMA, "run_id": rid, **summarize(state),
           "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "source": (str(source)[:200] if source else None), "state": state}
    if request is not None:
        rec["request"] = request
    if die and config:
        rec["designations"] = designations(state, die, config)
    if len(json.dumps(rec, default=str).encode("utf-8")) > MAX_BYTES:
        raise RunRejected("the run is larger than %d MB" % (MAX_BYTES // 1_000_000))
    return rec


def write_run(die_dir: Path, config: str, rec: Dict[str, Any], overwrite: bool = False) -> Path:
    d = runs_dir(die_dir, config)
    d.mkdir(parents=True, exist_ok=True)
    p = d / (check_run_id(rec["run_id"]) + ".json")
    if p.exists() and not overwrite:
        raise FileExistsError(str(p.name))
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rec, default=str), encoding="utf-8")
    tmp.replace(p)
    return p


def list_runs(*die_dirs: Path, config: str) -> List[Dict[str, Any]]:
    """Index entries (no state), newest first; earlier directories win on a repeated run_id."""
    seen: Dict[str, Dict[str, Any]] = {}
    for dd in die_dirs:
        d = runs_dir(dd, config)
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.json")):
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(rec, dict) or rec.get("schema") != SCHEMA:
                continue
            seen.setdefault(f.stem, {k: v for k, v in rec.items() if k not in ("state", "request")})
    return sorted(seen.values(), key=lambda r: str(r.get("saved_at")), reverse=True)


def read_run(*die_dirs: Path, config: str, run_id: str) -> Optional[Dict[str, Any]]:
    rid = check_run_id(run_id)
    for dd in die_dirs:
        p = runs_dir(dd, config) / (rid + ".json")
        if p.is_file():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
    return None
