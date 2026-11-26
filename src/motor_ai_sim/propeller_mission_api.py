"""Logic behind ``/api/propellers/mission/*`` (Propellers > Mission): the routes are thin wrappers.

Validation (a bad input is a 422 with the field named), the motor picker (catalogue cards with their model kind), a background run with
progress and a stop flag, and the stored runs.  A run is a JSON file in the CALLER'S workspace (``<workspace>/propeller_designs/
mission_runs/<run_id>.run.json``, progress beside it while running); nothing here writes the shared catalog, a project or a motor.
The calculation itself is ``propeller_mission`` (pure functions).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from motor_ai_sim import propeller_mission as pm
from motor_ai_sim import propeller_s1 as s1
from motor_ai_sim import workspace
from motor_ai_sim.propeller_design_api import ApiProblem

RUN_SCHEMA = "propeller-mission-run-1"
RUN_ID_RX = re.compile(r"^msn-[0-9a-f]{10}$")
RUN_DIR = "mission_runs"
MAX_RUNNING = 2
MAX_RUN_FILES = 500

_LOCK = threading.Lock()
_LIVE: Dict[str, Dict[str, Any]] = {}          # run_id -> {"thread", "stop"} of the runs of THIS process


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
# Motors
# ---------------------------------------------------------------------------

def load_raw_model(die: str, config: str) -> Dict[str, Any]:
    """The raw S1 model dict of ``die`` / ``config`` and the sha256 of its file (``MissionError`` when there is none)."""
    try:
        m = s1.load_model(die, config)
    except s1.S1Error as exc:
        raise pm.MissionError(str(exc))
    data = Path(m["_path"]).read_bytes()
    return json.loads(data.decode("utf-8")), hashlib.sha256(data).hexdigest()      # type: ignore[return-value]


def _s1_extras(die: str, config: str) -> Dict[str, Any]:
    try:
        m = s1.load_model(die, config)
    except s1.S1Error as exc:
        return {"unusable": str(exc)}
    wind = m.get("winding") or {}
    return {"drives": [s1.SINE] + sorted((m.get("drives") or {}).keys()), "has_psi_map": bool(m.get("psi_map")),
            "winding": wind or None, "kv_bench_rpm_per_V": m.get("kv_bench_rpm_per_V"), "kv_model_rpm_per_V": pm.kv_of(m),
            "board_cap_A": s1.resolve_drive(m).get("I_cont_A"), "I_max_A": s1.limits_of(m)["I_max_A"]}


def motor_entries(cards: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The picker rows: every catalogue card with its model kind, then the S1 models no card carries the name of.  Read-only."""
    idx = pm.s1_name_index()
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
    rows.sort(key=lambda r: (str(r.get("die") or r.get("name") or "").casefold(), str(r.get("name") or "").casefold()))
    return rows


def options(cards: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Everything the page needs to start: motors, battery presets, objectives, defaults, the wire stock strips."""
    try:
        stock = pm.stock_rows()
    except Exception:                                                   # noqa: BLE001 - a missing stock table must not hide the page
        stock = []
    return {"motors": motor_entries(cards), "batteries": pm.CHEMISTRIES, "objectives": list(pm.OBJECTIVES), "defaults": pm.default_inputs(),
            "strips": stock, "note": pm.NOTE, "version": pm.MODEL_VERSION, "estimate_label": pm.ESTIMATE_LABEL}


def resolve_motor(cfg: Dict[str, Any], cards: List[Dict[str, Any]]):
    """``(raw model dict, info)`` of the chosen motor: a catalogue card (its S1 model when one exists, else an estimate built from the
    card, else a refusal with the reason) or a die / configuration with an S1 model."""
    m = cfg["motor"]
    if m.get("card_id"):
        card = next((c for c in cards if c.get("id") == m["card_id"]), None)
        if card is None:
            raise pm.MissionError("no catalogue motor %r (or it is not visible to you)" % m["card_id"])
        e = pm.card_entry(card, pm.s1_name_index())
        if e["kind"] == pm.KIND_S1:
            raw, sha = load_raw_model(e["die"], e["config"])
            return raw, {"kind": pm.KIND_S1, "card_id": e["id"], "name": e["name"], "die": e["die"], "config": e["config"], "model_sha256": sha}
        if e["kind"] == pm.KIND_ESTIMATE:
            raw, info = pm.estimate_model(card)
            return raw, {**info, "card_id": e["id"], "name": e["name"], "die": None, "config": None,
                         "model_sha256": hashlib.sha256(json.dumps(raw, sort_keys=True, default=str).encode()).hexdigest()}
        raise pm.MissionError("motor %r cannot be used: %s" % (e["name"], e["reason"]))
    raw, sha = load_raw_model(m["die"], m["config"])
    return raw, {"kind": pm.KIND_S1, "card_id": None, "name": "%s %s" % (m["die"], m["config"]), "die": m["die"], "config": m["config"], "model_sha256": sha}


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------

def validate(body: Any) -> Dict[str, Any]:
    try:
        return pm.validate_inputs(body)
    except pm.MissionError as exc:
        raise ApiProblem(422, str(exc))


def start_run(body: Any, cards: List[Dict[str, Any]], *, background: bool = True) -> Dict[str, Any]:
    """Validate, resolve the motor, check the size, then run in a thread (``background`` False runs inline: tests).  Returns
    ``{run_id, status, total}``."""
    cfg = validate(body)
    try:
        raw, motor = resolve_motor(cfg, cards)
        total = pm.count_candidates(cfg)
        if total > int(cfg["candidates"]["max_candidates"]):
            raise pm.MissionError("the inputs ask for %d candidates; the limit is %d (narrow the diameter window or the grid)"
                                  % (total, cfg["candidates"]["max_candidates"]))
        if total == 0:
            raise pm.MissionError("no candidate propeller in the diameter window")
        pm.MotorBank(raw, cfg, [])            # the winding data and the Kv mode are checked before the thread starts
    except pm.MissionError as exc:
        raise ApiProblem(422, str(exc))
    folder = _runs_dir()
    with _LOCK:
        if sum(1 for v in _LIVE.values() if v["thread"].is_alive()) >= MAX_RUNNING:
            raise ApiProblem(409, "%d mission runs are already running; wait for one to finish or stop it" % MAX_RUNNING)
        if folder.is_dir() and len(list(folder.glob("msn-*.run.json"))) >= MAX_RUN_FILES:
            raise ApiProblem(409, "too many stored mission runs (%d); delete some first" % MAX_RUN_FILES)
        run_id = "msn-" + uuid.uuid4().hex[:10]
        stop = threading.Event()
        t0 = time.time()
        head = {"schema": RUN_SCHEMA, "run_id": run_id, "created_utc": _utc(t0), "inputs": cfg, "motor": motor, "version": pm.MODEL_VERSION,
                "s1_schema": s1.SCHEMA}
        _write_json(folder / (run_id + ".progress.json"), {**head, "status": "running", "done": 0, "total": total, "line": "start", "updated_at": _utc()})
        th = threading.Thread(target=_worker, args=(run_id, folder, cfg, raw, head, stop, t0), name=run_id, daemon=True)
        _LIVE[run_id] = {"thread": th, "stop": stop}
    if background:
        th.start()
    else:
        th.run()
    return {"run_id": run_id, "status": "running" if background else get_run(run_id)["status"], "total": total}


def _worker(run_id: str, folder: Path, cfg: Dict[str, Any], raw: Dict[str, Any], head: Dict[str, Any], stop: threading.Event, t0: float) -> None:
    prog = folder / (run_id + ".progress.json")
    last = [0.0]

    def progress(done: int, total: int, line: str) -> None:
        if time.time() - last[0] > 0.5 or done >= total:
            last[0] = time.time()
            _write_json(prog, {**head, "status": "running", "done": done, "total": total, "line": line, "unit": "candidates", "updated_at": _utc()})
    try:
        res = pm.run_selection(cfg, raw, progress=progress, should_stop=stop.is_set)
        doc = {**head, "status": "stopped" if res.get("stopped") else "done", "elapsed_s": round(time.time() - t0, 1), "finished_utc": _utc(), "result": res}
    except Exception as exc:                                            # noqa: BLE001 - the run is stored as failed with the reason
        doc = {**head, "status": "failed", "elapsed_s": round(time.time() - t0, 1), "finished_utc": _utc(), "error": "%s: %s" % (type(exc).__name__, exc)}
    _write_json(folder / (run_id + ".run.json"), doc)
    try:
        prog.unlink()
    except OSError:
        pass
    with _LOCK:
        _LIVE.pop(run_id, None)


def stop_run(run_id: str) -> Dict[str, Any]:
    _check_id(run_id)
    with _LOCK:
        live = _LIVE.get(run_id)
    if not live:
        raise ApiProblem(404, "no running mission run %s" % run_id)
    live["stop"].set()
    return {"run_id": run_id, "stopping": True}


def _check_id(run_id: Any) -> None:
    if not isinstance(run_id, str) or not RUN_ID_RX.match(run_id):
        raise ApiProblem(422, "run_id must look like msn-<10 hex digits>")


def get_run(run_id: str) -> Dict[str, Any]:
    """The stored run (inputs, motor and model kind, results) or, while it runs, its progress."""
    _check_id(run_id)
    folder = _runs_dir()
    done = folder / (run_id + ".run.json")
    if done.is_file():
        return json.loads(done.read_text(encoding="utf-8"))
    prog = folder / (run_id + ".progress.json")
    if prog.is_file():
        j = json.loads(prog.read_text(encoding="utf-8"))
        if run_id not in _LIVE:
            j["status"] = "interrupted"
        return {k: v for k, v in j.items() if k != "result"}
    raise ApiProblem(404, "no stored mission run %s" % run_id)


def list_runs() -> Dict[str, Any]:
    """Stored and running mission runs, newest first (summary only)."""
    folder = _runs_dir()
    out: List[Dict[str, Any]] = []
    for f in sorted(folder.glob("msn-*.json")) if folder.is_dir() else []:
        try:
            j = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        res = j.get("result") or {}
        rows = res.get("results") or []
        top = rows[0] if rows and rows[0].get("passed") else None
        ref = (res.get("refined") or [None])[0]
        status = j.get("status")
        if f.name.endswith(".progress.json") and j.get("run_id") not in _LIVE:
            status = "interrupted"
        out.append({"run_id": j.get("run_id"), "created_utc": j.get("created_utc"), "status": status, "objective": (j.get("inputs") or {}).get("objective"),
                    "motor": {k: (j.get("motor") or {}).get(k) for k in ("name", "kind", "die", "config", "card_id")},
                    "done": j.get("done"), "total": j.get("total") or (res.get("counts") or {}).get("total"),
                    "best": ({"name": top.get("name"), "value": top.get("objective_value"), "turns": top.get("turns")} if top else None),
                    "refined": ({"value": ref.get("best_value"), "gain_pct": ref.get("gain_pct")} if ref else None), "error": j.get("error")})
    out.sort(key=lambda r: str(r.get("created_utc") or ""), reverse=True)
    return {"runs": out, "note": pm.NOTE}
