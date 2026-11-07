"""Tests -> "Compute it": create the missing bench point as a TEST duty and solve it (admin only; owner 2026-11-02).

The compare table offers an action on a row the model has no point for (``comparison._create_duty``).  Until now the web
posted that body to ``POST /api/family/duty``, which refused it (no ``gamma_deg``) and, had it been accepted, only added a
duty and solved nothing.  ``POST /api/motor-tests/{die}/{config}/compute`` does the whole job:

1. validates the actions (a TEST duty only, positive rpm / current, a current angle that comes from the configuration's own
   stored duties when the action carries none), drops the repeats of one point (rpm rounded to 10, current to 0.5 A);
2. writes each point as a duty of the configuration with ``kind: "test"`` - never a preset, a default duty or a public
   card row (``duty_kind``) - through the very function the duty API uses (``family._upsert_duty``);
3. queues the standard electromagnetic solve of each point on the job queue (``jobs.queue().submit``, not blocking), the
   one ``Run`` uses;
4. when a solve finishes, files its summary on the duty exactly as a Simulation save does, so the comparison
   (``model_source``) reads it and the delta fills in.

The solve runs in a sandbox workspace made from the stored configuration (the way an agent's draft is solved,
``agent_designs``), so the administrator's open machine, the live last-run stores and the motor card / passport are never
touched; nothing is recorded under any duty but the test duty itself.  The temperatures need the coupled solve and are not
part of this click.

The route is the compute worker's (``roles.WORKER_PATTERNS``): the shared catalog is read-only in the API process.
"""
from __future__ import annotations

import functools
import logging
import math
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from motor_ai_sim.motor_tests import comparison
from motor_ai_sim.motor_tests.compare import num, summary_magnet_temp_c

log = logging.getLogger(__name__)

#: The job kind (``GET /api/jobs`` shows it).
JOB_KIND = "motor_tests.compute"
#: Points one call may create (a load sweep of a whole bench day is a few dozen).
MAX_POINTS = 100
MODES = ("motor", "generator")


class _Point(dict):
    """One validated point: ``name, mode, rpm, current_arms, gamma_deg`` (+ ``gamma_source``)."""


def prepare(die: str, config: str, actions: Any, model: Optional[Dict[str, Any]],
            existing: Optional[List[Dict[str, Any]]] = None) -> Tuple[List[_Point], List[Dict[str, Any]]]:
    """``(points, skipped)`` from the posted actions.

    ``actions`` = the ``action`` objects of the compare table (``{"kind": "create_duty", "body": {...}}``).  A point is
    refused with 422 when it is malformed; a repeat of an earlier point, or a name that is an ORDINARY duty of the
    configuration (a test point must never overwrite one), is skipped and reported."""
    if not isinstance(actions, list) or not actions:
        raise HTTPException(422, detail="actions: a non-empty list of compare-table actions is expected")
    if len(actions) > MAX_POINTS * 4:
        raise HTTPException(422, detail="actions: too many (limit %d points per call)" % MAX_POINTS)
    by_name = {str(d.get("name")): d for d in existing or [] if isinstance(d, dict)}
    from motor_ai_sim.duty_kind import is_test_duty
    points: List[_Point] = []
    skipped: List[Dict[str, Any]] = []
    seen_keys, seen_names = set(), set()
    for a in actions:
        body = a.get("body") if isinstance(a, dict) and a.get("kind") == "create_duty" else None
        duty = body.get("duty") if isinstance(body, dict) else None
        if not isinstance(duty, dict):
            raise HTTPException(422, detail="actions: each item is {kind: 'create_duty', body: {die, config, duty}}")
        if str(body.get("die") or "") != die or str(body.get("config") or "") != config:
            raise HTTPException(422, detail="actions: the point belongs to another motor than the one in the URL")
        if str(duty.get("kind") or "test").strip().lower() != "test":
            raise HTTPException(422, detail="actions: only a test point (kind 'test') can be computed here")
        name = str(duty.get("name") or "").strip()[:60]
        rpm, cur = num(duty.get("rpm")), num(duty.get("current_arms"))
        if not name or rpm is None or cur is None or rpm <= 0 or cur <= 0:
            raise HTTPException(422, detail="actions: a point needs a name and a positive rpm and current_arms")
        mode = str(duty.get("mode") or "motor")
        if mode not in MODES:
            raise HTTPException(422, detail="actions: mode must be 'motor' or 'generator'")
        gamma, src = num(duty.get("gamma_deg")), "as sent"
        if gamma is None:
            gamma, src = comparison.gamma_for(model, cur)
        if not math.isfinite(gamma) or abs(gamma) > 360.0:
            raise HTTPException(422, detail="actions: gamma_deg must be a number within +-360")
        key = comparison.action_key(rpm, cur)
        if key in seen_keys or name in seen_names:
            skipped.append({"name": name, "reason": "repeat"})
            continue
        old = by_name.get(name)
        if old is not None and not is_test_duty(old):
            skipped.append({"name": name, "reason": "an ordinary duty of this configuration has that name"})
            continue
        seen_keys.add(key)
        seen_names.add(name)
        points.append(_Point(name=name, mode=mode, rpm=round(rpm, 1), current_arms=round(cur, 2),
                             gamma_deg=round(float(gamma), 2), gamma_source=src))
    if len(points) > MAX_POINTS:
        raise HTTPException(422, detail="actions: %d points, the limit is %d per call" % (len(points), MAX_POINTS))
    return points, skipped


# ---------------------------------------------------------------------------
# the solve (a queue worker thread, in the submitter's workspace)
# ---------------------------------------------------------------------------

def _reference_duty(duties: List[Dict[str, Any]], current: float) -> Optional[Dict[str, Any]]:
    """The ordinary stored duty nearest in current: its mesh fidelity, copper / magnet temperature and material picks are
    the standard the new point is solved with (the same fidelity as the points it is compared beside)."""
    best, best_err = None, None
    for d in duties:
        if not isinstance(d, dict) or str(d.get("kind") or "") == "test":
            continue
        c = num(d.get("current_arms"))
        if not c or c <= 0 or not (isinstance(d.get("mesh"), dict) or isinstance(d.get("summary"), dict)):
            continue
        err = abs(math.log(max(current, 0.1) / c))
        if best_err is None or err < best_err:
            best, best_err = d, err
    return best


def _config_doc(pay: Dict[str, Any], point: Dict[str, Any], ref: Optional[Dict[str, Any]], ws) -> Path:
    """Write ``<sandbox>/motor_config.yaml``: the stored configuration's machine at the point (read only template)."""
    import yaml
    from motor_ai_sim import config as _cfg
    from motor_ai_sim import workspace as _wsm
    from motor_ai_sim.routes import family as _fam
    src = Path(str(_wsm.config_file()))
    tpl = yaml.safe_load(src.read_text(encoding="utf-8")) if src.is_file() else {}
    tpl = tpl if isinstance(tpl, dict) else {}
    geo = dict(tpl.get("geometry") or {})
    geo.update(pay.get("geometry") or {})
    wind = dict(tpl.get("winding") or {})
    wind.update(pay.get("winding") or {})
    mats = dict(pay.get("materials") or tpl.get("materials") or {})
    mats.update({k: v for k, v in ((ref or {}).get("materials") or {}).items() if v})
    sim = dict(tpl.get("simulation") or {})
    cur, rpm, gamma = float(point["current_arms"]), float(point["rpm"]), float(point["gamma_deg"])
    sim.update(current_a=cur, max_current=cur * math.sqrt(2.0), rpm=rpm, gamma_deg=gamma, phase_offset_deg=gamma,
               mode=point.get("mode") or "motor", drive="current", connection=wind.get("connection"),
               star_delta=(pay.get("sim") or {}).get("star_delta") or "star")
    poles = int(geo.get("num_poles") or 0)
    if poles:
        sim["frequency"] = round(rpm * poles / 120.0, 4)
    for k in ("daxis_deg", "end_winding_factor"):
        v = (pay.get("sim") or {}).get(k)
        if v is not None:
            sim[k] = v
    mesh = dict(tpl.get("mesh") or {})
    mesh.update(_fam.duty_mesh_patch(ref))
    doc = dict(tpl)
    doc.update(geometry=geo, winding=wind, simulation=sim, mesh=mesh, materials=mats, parts=pay.get("parts") or {})
    cf = Path(str(ws.config_file))
    cf.parent.mkdir(parents=True, exist_ok=True)
    cf.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
    _cfg.clear_config_cache(cf)
    return cf


def _solve_kwargs(point: Dict[str, Any], pay: Dict[str, Any], ref: Optional[Dict[str, Any]], mesh: Dict[str, Any],
                  run_id: str) -> Dict[str, Any]:
    """The arguments of the Simulation tab's Run (``lib/emRunPayload``) for this point."""
    wind = pay.get("winding") or {}
    sim = pay.get("sim") or {}
    kw: Dict[str, Any] = {
        "I_phase_rms": float(point["current_arms"]), "rpm": float(point["rpm"]), "gamma_deg": float(point["gamma_deg"]),
        "mode": point.get("mode") or "motor", "run_id": run_id, "ledger": False,
        # the panel's constants: coupled eddy solve, field-based rotor losses, demagnetisation on
        "eddy": True, "rotor_eddy": True, "demag": True, "n_periods": 1,
    }
    if wind.get("n_parallel"):
        kw["n_parallel"] = int(wind["n_parallel"])
    if wind.get("connection"):
        kw["connection"] = wind["connection"]
    kw["star_delta"] = sim.get("star_delta") or "star"
    if sim.get("daxis_deg") is not None:
        kw["daxis_deg"] = float(sim["daxis_deg"])
    if sim.get("end_winding_factor") is not None:
        kw["end_winding_factor"] = float(sim["end_winding_factor"])
    for k in ("mesh_size_mm", "min_size_mm", "outer_air_factor", "gap_layers", "n_sectors"):
        if mesh.get(k) is not None:
            kw[k] = int(mesh[k]) if k == "n_sectors" else float(mesh[k])
    s = (ref or {}).get("summary") if isinstance((ref or {}).get("summary"), dict) else {}
    coil = num(s.get("coil_temp_C"))
    if coil is not None:
        kw["coil_temp_c"] = coil
    mag = summary_magnet_temp_c(s)
    if mag is not None:
        kw["magnet_temp_c"] = mag
    return kw


def _solve(die: str, config: str, point: Dict[str, Any], w: Dict[str, Any], tmp: Path, run_id: str):
    """Run the standard EM solve of the stored point in a sandbox workspace; ``(answer, ref_duty_mesh_block)``."""
    from motor_ai_sim import run_recording as _rr
    from motor_ai_sim import workspace as _wsm
    from motor_ai_sim.routes import family as _fam
    from motor_ai_sim.routes import simulation as _sim
    auth = (w or {}).get("authorization")
    pay = _fam.payload(die, config, duty=point["name"], authorization=auth)
    doc = _fam._load_yaml(_fam._cfg_file(die, config), "configuration")
    ref = _reference_duty(doc.get("duties") or [], float(point["current_arms"]))
    user_ws = _wsm.workspace()
    sb = _wsm.Workspace(id=f"{user_ws.id}.mtc.{run_id}", email=user_ws.email, root=tmp, shared_root=user_ws.shared_root)
    with _wsm.use_workspace(sb):
        _config_doc(pay, point, ref, sb)
        mesh = _fam.duty_mesh_patch(ref)
        kw = _solve_kwargs(point, pay, ref, mesh, run_id)
        with _rr.no_record():
            answer = _sim.get_fem_transient(**kw)
    return answer, (dict(ref["mesh"]) if ref and isinstance(ref.get("mesh"), dict) else None)


def _save(die: str, config: str, point: Dict[str, Any], answer: Any, mesh_block: Optional[Dict[str, Any]],
          w: Dict[str, Any]) -> None:
    """File the solved summary on the test duty, the way a Simulation save does (``family._upsert_duty``)."""
    from motor_ai_sim.routes import family as _fam
    summary = answer.get("summary") if isinstance(answer, dict) else None
    if not isinstance(summary, dict) or not summary:
        raise RuntimeError("the solver returned no summary for %r" % point["name"])
    spec = _fam.DutySpec(name=point["name"], mode=point.get("mode") or "motor", current_arms=float(point["current_arms"]),
                         rpm=float(point["rpm"]), gamma_deg=float(point["gamma_deg"]), kind="test",
                         summary=summary, mesh=mesh_block)
    _fam._upsert_duty(_fam.DutyCreate(die=die, config=config, duty=spec), w)


def run_point(die: str, config: str, point: Dict[str, Any], w: Dict[str, Any], run_id: str) -> Dict[str, Any]:
    """The queued work of one point (a queue worker thread, in the submitter's workspace)."""
    tmp = Path(tempfile.mkdtemp(prefix="motor_tests_compute_"))
    try:
        answer, mesh_block = _solve(die, config, point, w, tmp, run_id)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    _save(die, config, point, answer, mesh_block, w)
    log.info("motor tests: computed '%s/%s/%s'", die, config, point["name"])
    return {"ok": True, "duty": point["name"]}


# ---------------------------------------------------------------------------
# the route's body
# ---------------------------------------------------------------------------

def create_and_queue(die: str, config: str, points: List[_Point], w: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Write each point as a test duty and queue its solve; one result row per point (``run_id`` to poll
    ``GET /api/jobs``).  A point that cannot be written is reported, the others go on."""
    from motor_ai_sim import jobs as _jobs
    from motor_ai_sim.routes import family as _fam
    _jobs.require_solve_allowed()
    _jobs.require_agreement_at_simulation()
    out: List[Dict[str, Any]] = []
    for pt in points:
        row: Dict[str, Any] = {"duty": pt["name"], "gamma_deg": pt["gamma_deg"], "gamma_source": pt.get("gamma_source")}
        try:
            spec = _fam.DutySpec(name=pt["name"], mode=pt["mode"], current_arms=pt["current_arms"], rpm=pt["rpm"],
                                 gamma_deg=pt["gamma_deg"], kind="test")
            _fam._upsert_duty(_fam.DutyCreate(die=die, config=config, duty=spec), w)
        except HTTPException as e:
            row.update(state="refused", error=str(e.detail))
            out.append(row)
            continue
        rec = _jobs.make_record(JOB_KIND, priority=_jobs.Priority.DUTY,
                                body={"die": die, "config": config, "duty": pt["name"]})
        row["run_id"] = rec.run_id
        work = functools.partial(run_point, die, config, dict(pt), w, rec.run_id)
        try:
            _jobs.queue().submit(rec, work, block=False)
        except _jobs.JobAccepted as acc:
            row.update(state="queued", position=acc.record.position)
        except (_jobs.ComputeNotHere, _jobs.WorkerDraining) as e:
            row.update(state="refused", error=str(e))
        else:                                                           # pragma: no cover - block=False always parks
            row.update(state="done")
        out.append(row)
    return out
