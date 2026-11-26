"""Logic behind ``/api/propellers/mission/*`` (Propellers > Mission): the routes are thin wrappers.

Validation (a bad input is a 422 with the field named), the motor picker (catalogue cards with their model kind, only what the caller may
see), the run as a job of the EXISTING queue (``jobs.run_job`` kind ``propeller_mission``: caller / workspace context, per-user limit,
drain checks, cancel with the owner check), the durable state a poll reads (the job record and the run file) and the stored runs.

A run is a JSON file in the CALLER'S workspace (``<workspace>/propeller_designs/mission_runs/<run_id>.run.json``; ``.progress.json``
while it runs; the optimiser's own record of every stage-2 evaluation in ``<run_id>.opt/``).  The last ``KEEP_RUNS`` runs of a workspace
are kept, older ones are removed when a run is written.  Nothing here writes the shared catalogue, a project or a motor.

No handler is registered for the kind (``jobs.HANDLERS``): a run needs the caller's catalogue access and workspace files, which a
remote node or the other region's server does not have, so it must never be shipped away by the BYO / region routers.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from motor_ai_sim import jobs as _J
from motor_ai_sim import motor_access as ma
from motor_ai_sim import propeller_mission as pm
from motor_ai_sim import propeller_s1 as s1
from motor_ai_sim import workspace
from motor_ai_sim.propeller_design_api import ApiProblem

log = logging.getLogger(__name__)

RUN_SCHEMA = "propeller-mission-run-2"
RUN_KIND = "propeller_mission"
RUN_ID_RX = re.compile(r"^msn-[0-9a-f]{10,12}$")
RUN_DIR = "mission_runs"
KEEP_RUNS = 100
#: The fields of a picker row that leave the server (everything else a model file holds - psi / loss grids, thermal geometry, the file path
#: - stays inside).  A test pins this list.
ENTRY_FIELDS = frozenset({"id", "name", "diameter_mm", "die", "config", "kind", "reason", "label", "kv_rpm_per_V", "R_phase_ohm", "pole_pairs",
                          "mass_kg", "I_peak_A", "drives", "has_psi_map", "winding", "kv_bench_rpm_per_V", "kv_model_rpm_per_V", "board_cap_A",
                          "I_max_A", "unusable"})


def _runs_dir() -> Path:
    return workspace.root() / "propeller_designs" / RUN_DIR


def _write_json(path: Path, doc: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex[:6])
    tmp.write_text(json.dumps(doc, default=str), encoding="utf-8")
    os.replace(tmp, path)


def _utc(t: Optional[float] = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t if t is not None else time.time()))


# ---------------------------------------------------------------------------
# Motors (what the caller may see)
# ---------------------------------------------------------------------------

def _visible_index(access: Dict[str, Any]) -> Dict[str, Any]:
    """The S1 models of ``pm.s1_name_index`` the caller may read (``motor_access.may_see_config``): a hidden model is as good as missing."""
    return {k: v for k, v in pm.s1_name_index().items() if ma.may_see_config(access, v[0], v[1])}


def load_raw_model(die: str, config: str, access: Dict[str, Any]):
    """``(raw S1 model dict, sha256 of its file)``.  A configuration the caller may not see answers exactly like a missing one."""
    if not ma.may_see_config(access, die, config):
        raise pm.MissionError("no propeller S1 model for %s %s" % (die, config))
    try:
        m = s1.load_model(die, config)
    except s1.S1Error as exc:
        raise pm.MissionError(str(exc))
    data = Path(m["_path"]).read_bytes()
    return json.loads(data.decode("utf-8")), hashlib.sha256(data).hexdigest()


def _s1_extras(die: str, config: str) -> Dict[str, Any]:
    try:
        m = s1.load_model(die, config)
    except s1.S1Error as exc:
        return {"unusable": str(exc)}
    return {"drives": [s1.SINE] + sorted((m.get("drives") or {}).keys()), "has_psi_map": bool(m.get("psi_map")),
            "winding": m.get("winding") or None, "kv_bench_rpm_per_V": m.get("kv_bench_rpm_per_V"), "kv_model_rpm_per_V": pm.kv_of(m),
            "board_cap_A": s1.resolve_drive(m).get("I_cont_A"), "I_max_A": s1.limits_of(m)["I_max_A"]}


def motor_entries(cards: List[Dict[str, Any]], access: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The picker rows: every catalogue card with its model kind, then the S1 models no card carries the name of - all of them only for
    what the caller may see.  Read-only; the row fields are the ``ENTRY_FIELDS`` allow-list."""
    idx = _visible_index(access)
    rows: List[Dict[str, Any]] = []
    used = set()
    for c in cards:
        e = pm.card_entry(c, idx)
        if e["kind"] == pm.KIND_S1:
            used.add((e["die"], e["config"]))
            e.update(_s1_extras(e["die"], e["config"]))
        rows.append(e)
    for key, (die, cfg) in sorted(idx.items()):
        if (die, cfg) in used:
            continue
        rows.append({"id": None, "name": "%s %s" % (die, cfg), "diameter_mm": None, "die": die, "config": cfg, "kind": pm.KIND_S1, "reason": None,
                     **_s1_extras(die, cfg)})
    rows = [{k: v for k, v in r.items() if k in ENTRY_FIELDS} for r in rows]
    rows.sort(key=lambda r: (str(r.get("die") or r.get("name") or "").casefold(), str(r.get("name") or "").casefold()))
    return rows


def options(cards: List[Dict[str, Any]], access: Dict[str, Any]) -> Dict[str, Any]:
    """Everything the page needs to start: motors, battery presets, objectives, defaults, the wire stock strips."""
    try:
        stock = pm.stock_rows()
    except Exception:                                                   # noqa: BLE001 - a missing stock table must not hide the page
        stock = []
    return {"motors": motor_entries(cards, access), "batteries": pm.CHEMISTRIES, "objectives": list(pm.OBJECTIVES), "defaults": pm.default_inputs(),
            "strips": stock, "note": pm.NOTE, "version": pm.MODEL_VERSION, "estimate_label": pm.ESTIMATE_LABEL}


def resolve_motor(cfg: Dict[str, Any], cards: List[Dict[str, Any]], access: Dict[str, Any]):
    """``(raw model dict, info)`` of the chosen motor: a catalogue card (its S1 model when one exists AND the caller may see it, else an
    estimate built from the card, else a refusal with the reason) or a die / configuration with an S1 model the caller may see."""
    m = cfg["motor"]
    if m.get("card_id"):
        card = next((c for c in cards if c.get("id") == m["card_id"]), None)
        if card is None:
            raise pm.MissionError("no catalogue motor %r (or it is not visible to you)" % m["card_id"])
        e = pm.card_entry(card, _visible_index(access))
        if e["kind"] == pm.KIND_S1:
            raw, sha = load_raw_model(e["die"], e["config"], access)
            return raw, {"kind": pm.KIND_S1, "card_id": e["id"], "name": e["name"], "die": e["die"], "config": e["config"], "model_sha256": sha}
        if e["kind"] == pm.KIND_ESTIMATE:
            raw, info = pm.estimate_model(card)
            return raw, {**info, "card_id": e["id"], "name": e["name"], "die": None, "config": None,
                         "model_sha256": hashlib.sha256(json.dumps(raw, sort_keys=True, default=str).encode()).hexdigest()}
        raise pm.MissionError("motor %r cannot be used: %s" % (e["name"], e["reason"]))
    raw, sha = load_raw_model(m["die"], m["config"], access)
    return raw, {"kind": pm.KIND_S1, "card_id": None, "name": "%s %s" % (m["die"], m["config"]), "die": m["die"], "config": m["config"], "model_sha256": sha}


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------

def validate(body: Any) -> Dict[str, Any]:
    try:
        return pm.validate_inputs(body)
    except pm.MissionError as exc:
        raise ApiProblem(422, str(exc))


def _check_id(run_id: Any) -> None:
    if not isinstance(run_id, str) or not RUN_ID_RX.match(run_id):
        raise ApiProblem(422, "run_id must look like msn-<10 to 12 hex digits>")


def _run_files(folder: Path, run_id: str) -> List[Path]:
    return [folder / (run_id + ".run.json"), folder / (run_id + ".progress.json")]


def _remove_run(folder: Path, run_id: str) -> None:
    for f in _run_files(folder, run_id):
        try:
            f.unlink()
        except OSError:
            pass
    shutil.rmtree(folder / (run_id + ".opt"), ignore_errors=True)


def _terminal_runs(folder: Path) -> List[Tuple[float, str]]:
    """``(mtime, run_id)`` of every ENDED run of the folder, one per run id: a ``.run.json``, or a progress-only record whose effective state
    is terminal (stopped / interrupted / failed: cancelled while queued, a restart, a failed terminal write).  Active jobs are excluded."""
    seen: Dict[str, float] = {}
    q = _J.queue()

    def active(rid: str) -> bool:                 # a job still queued or running (e.g. between its terminal write and its release)
        rec = q.status(rid)
        return rec is not None and str(rec.state) in ("queued", "running")
    for f in folder.glob("msn-*.run.json"):
        rid = f.name[: -len(".run.json")]
        if not active(rid):
            seen[rid] = f.stat().st_mtime
    for f in folder.glob("msn-*.progress.json"):
        rid = f.name[: -len(".progress.json")]
        if rid in seen or active(rid) or (folder / (rid + ".run.json")).exists():
            continue
        try:
            st = str(json.loads(f.read_text(encoding="utf-8")).get("status"))
        except (OSError, ValueError):
            st = "failed"
        if _effective(rid, st) in ("stopped", "interrupted", "failed"):
            seen[rid] = f.stat().st_mtime
    return sorted((t, rid) for rid, t in seen.items())


def prune(folder: Path, keep: int = KEEP_RUNS) -> int:
    """Retention: keep the newest ``keep`` ENDED runs of the workspace (stored runs and terminal progress-only records, review #922 A),
    remove the oldest (files and optimiser records).  A queued or running job is never counted or removed.  Returns how many were removed."""
    if not folder.is_dir():
        return 0
    ended = _terminal_runs(folder)
    old = ended[: max(0, len(ended) - int(keep))]
    for _, rid in old:
        _remove_run(folder, rid)
    return len(old)


def start_run(body: Any, cards: List[Dict[str, Any]], access: Dict[str, Any], *, background: bool = True) -> Dict[str, Any]:
    """Validate, resolve the motor (access-checked), check the size, then submit the run as a job of the queue (kind ``propeller_mission``).
    Returns ``{run_id, status, total, position}``.  ``background`` False runs it on the calling thread once admitted (tests).
    The queue's own refusals (``ComputeNotHere``, ``WorkerDraining``) and the solve policy propagate to the route."""
    cfg = validate(body)
    try:
        raw, motor = resolve_motor(cfg, cards, access)
        total = pm.count_candidates(cfg)
        if total > int(cfg["candidates"]["max_candidates"]):
            raise pm.MissionError("the inputs ask for %d candidates; the limit is %d (narrow the diameter window or the grid)"
                                  % (total, cfg["candidates"]["max_candidates"]))
        if total == 0:
            raise pm.MissionError("no candidate propeller in the diameter window")
        pm.MotorBank(raw, cfg, [])            # the winding data and the Kv mode are checked before the job is queued
    except pm.MissionError as exc:
        raise ApiProblem(422, str(exc))
    _J.require_solve_allowed()
    _J.require_agreement_at_simulation()
    folder = _runs_dir()                      # no pruning before admission: a refused job must not cost stored history (review #922 A)
    run_id = _J.new_run_id("msn")
    head = {"schema": RUN_SCHEMA, "run_id": run_id, "created_utc": _utc(), "inputs": cfg, "motor": motor, "version": pm.MODEL_VERSION, "s1_schema": s1.SCHEMA}
    _write_json(folder / (run_id + ".progress.json"), {**head, "status": "queued", "done": 0, "total": total, "line": "queued", "updated_at": _utc()})

    def work() -> None:
        _work(run_id, folder, cfg, raw, head, total)

    try:
        _J.run_job(RUN_KIND, work, run_id=run_id, body={"objective": cfg["objective"], "motor": motor.get("name"), "kind": motor.get("kind")}, block=(not background))
    except _J.JobAccepted as acc:
        return {"run_id": run_id, "status": "queued", "total": total, "position": acc.record.position}
    except BaseException:
        _remove_run(folder, run_id)           # refused (draining, cancelled while waiting): nothing is left behind
        raise
    return {"run_id": run_id, "status": get_run(run_id)["status"], "total": total, "position": 0}


def _work(run_id: str, folder: Path, cfg: Dict[str, Any], raw: Dict[str, Any], head: Dict[str, Any], total: int) -> None:
    """The job body (runs on a queue worker, in the caller's workspace): the selection with progress, a stop check inside every loop, the
    terminal file in ``finally``-safe order (file first, then the progress file is removed, then retention)."""
    prog = folder / (run_id + ".progress.json")
    last = [0.0]
    t0 = time.time()

    def progress(done: int, tot: int, line: str) -> None:
        if time.time() - last[0] > 0.5 or done >= tot:
            last[0] = time.time()
            _write_json(prog, {**head, "status": "running", "done": done, "total": tot, "line": line, "unit": "candidates", "updated_at": _utc()})
    progress(0, total, "start")
    doc: Dict[str, Any]
    try:
        res = pm.run_selection(cfg, raw, progress=progress, should_stop=lambda: _J.is_cancelled(run_id), store_dir=folder / (run_id + ".opt"))
        doc = {**head, "status": "stopped" if res.get("stopped") else "done", "elapsed_s": round(time.time() - t0, 1), "finished_utc": _utc(), "result": res}
    except Exception as exc:                                            # noqa: BLE001 - the run is stored as failed with the reason
        doc = {**head, "status": "failed", "elapsed_s": round(time.time() - t0, 1), "finished_utc": _utc(), "error": "%s: %s" % (type(exc).__name__, exc)}
    try:
        _write_json(folder / (run_id + ".run.json"), doc)
    except Exception as exc:                                            # noqa: BLE001 - keep the run discoverable: the progress file says why
        log.warning("mission %s: terminal write failed", run_id, exc_info=True)
        try:
            _write_json(prog, {**head, "status": "failed", "done": None, "total": total, "line": "terminal write failed",
                               "error": "terminal write failed: %s: %s" % (type(exc).__name__, exc), "updated_at": _utc()})
        except Exception:                                               # noqa: BLE001
            pass
        return
    try:
        prog.unlink()                                                   # only after the terminal file is in place
    except OSError:
        pass
    prune(folder, KEEP_RUNS)


def _effective(run_id: str, file_status: str) -> str:
    """The state a poll shows: the run file's own terminal state, else the queue's record (queued / running / cancelled while it waited /
    interrupted by a restart / failed)."""
    if file_status in ("done", "stopped", "failed"):
        return file_status
    rec = _J.queue().status(run_id)
    if rec is None:
        return "interrupted"
    st = str(rec.state)
    return {"queued": "queued", "running": "running", "cancelled": "stopped", "interrupted": "interrupted", "failed": "failed", "done": "interrupted"}.get(st, st)


def get_run(run_id: str) -> Dict[str, Any]:
    """The stored run (inputs, motor and model kind, results) or, while it waits / runs, its state and progress.  Only the caller's own
    workspace is read: a foreign or missing id is the same 404."""
    _check_id(run_id)
    folder = _runs_dir()
    done = folder / (run_id + ".run.json")
    if done.is_file():
        return json.loads(done.read_text(encoding="utf-8"))
    prog = folder / (run_id + ".progress.json")
    if prog.is_file():
        j = json.loads(prog.read_text(encoding="utf-8"))
        j["status"] = _effective(run_id, str(j.get("status")))
        rec = _J.queue().status(run_id)
        if rec is not None and rec.state == "queued":
            j["position"] = rec.position
        return {k: v for k, v in j.items() if k != "result"}
    raise ApiProblem(404, "no stored mission run %s" % run_id)


def stop_run(run_id: str) -> Dict[str, Any]:
    """Stop = the queue's cancel (with its owner check).  Only a run of the caller's own workspace is found; a run that already ended is
    answered, not changed."""
    run = get_run(run_id)                        # 404 for a foreign or missing id
    if run.get("status") not in ("queued", "running"):
        return {"run_id": run_id, "stopping": False, "status": run.get("status")}
    try:
        _J.cancel_run(run_id)
    except _J.NotOwner:
        raise ApiProblem(404, "no stored mission run %s" % run_id)
    return {"run_id": run_id, "stopping": True, "status": "stopping"}


def delete_run(run_id: str) -> Dict[str, Any]:
    """Remove a finished run of the caller's workspace (files and the optimiser's records).  A run that is queued or running is refused."""
    run = get_run(run_id)
    if run.get("status") in ("queued", "running"):
        raise ApiProblem(409, "the run is still %s: stop it first" % run.get("status"))
    _remove_run(_runs_dir(), run_id)
    return {"run_id": run_id, "deleted": True}


def list_runs() -> Dict[str, Any]:
    """Stored and running mission runs of the caller's workspace, newest first (summary only)."""
    folder = _runs_dir()
    out: List[Dict[str, Any]] = []
    files = sorted(folder.glob("msn-*.json")) if folder.is_dir() else []
    stored = {f.name[: -len(".run.json")] for f in files if f.name.endswith(".run.json")}
    for f in files:
        if f.name.endswith(".progress.json") and f.name[: -len(".progress.json")] in stored:
            continue                                                    # one row per run: the stored run wins
        try:
            j = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        res = j.get("result") or {}
        rows = res.get("results") or []
        top = rows[0] if rows and rows[0].get("passed") else None
        ref = (res.get("refined") or [None])[0]
        out.append({"run_id": j.get("run_id"), "created_utc": j.get("created_utc"), "status": _effective(str(j.get("run_id")), str(j.get("status"))),
                    "objective": (j.get("inputs") or {}).get("objective"),
                    "motor": {k: (j.get("motor") or {}).get(k) for k in ("name", "kind", "die", "config", "card_id")},
                    "done": j.get("done"), "total": j.get("total") or (res.get("counts") or {}).get("total"),
                    "best": ({"name": top.get("name"), "value": top.get("objective_value"), "turns": top.get("turns")} if top else None),
                    "refined": ({"value": ref.get("best_value"), "gain_pct": ref.get("gain_pct")} if ref else None), "error": j.get("error")})
    out.sort(key=lambda r: str(r.get("created_utc") or ""), reverse=True)
    return {"runs": out, "note": pm.NOTE, "keep": KEEP_RUNS}
