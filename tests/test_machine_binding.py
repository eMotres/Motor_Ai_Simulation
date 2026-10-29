"""A run belongs to the machine it STARTED on (owner 2026-10-29).

A 16-point sweep of an L100 machine was running when another motor (G2-L40) was
loaded.  Seven of the remaining ten points were solved on the NEW machine and
appended to ``.opt_dataset.jsonl`` as ``ok``, the sweep record was stamped with
the machine loaded at the END, and an L100 transient came back labelled with the
new machine's mechanical losses.  These tests pin the three halves of the fix:

* ``POST /api/family/activate`` refuses (409 ``job_running``) while a job runs in
  the workspace, unless the request says it may cancel it;
* a sweep stops dispatching when its machine changes, writes nothing computed
  after the change as ``ok``, and is stamped with the machine it started on;
* post-processing labels of a run come from the run's own machine.

No field is solved: the eval subprocess is the stand-in from
``test_workspace_workers``.  Everything is written under ``tmp_path``.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path

import pytest
import yaml
from fastapi import HTTPException

from motor_ai_sim import jobs as J
from motor_ai_sim import machine_binding as MB
from motor_ai_sim import run_target as RT
from motor_ai_sim.routes import family as fam
from tests.test_workspace_workers import (  # noqa: F401 - fixtures
    _FakeChild, _keep_fake_solve_times_out_of_the_cost_window,
    layered_two_machines)

# ─────────────────────────────────────────────────────────────────────────────
#  MachineBinding
# ─────────────────────────────────────────────────────────────────────────────


def _ctx(die, cfg):
    return {"die": die, "config": cfg}


def test_binding_trips_when_another_machine_is_loaded(monkeypatch):
    now = {"v": _ctx("CIANO56", "L100")}
    monkeypatch.setattr(MB, "loaded_context", lambda: dict(now["v"]))
    b = MB.MachineBinding(None, what="FEM scan")
    assert b.changed() is None
    now["v"] = _ctx("CILN28", "G2-L40")
    why = b.changed()
    assert why and why.startswith(MB.REASON_PREFIX) and "CILN28" in why
    # sticky: loading the first machine back does not un-mix the run
    now["v"] = _ctx("CIANO56", "L100")
    assert b.changed() == why


def test_binding_ignores_a_release_and_a_reload_of_the_same_machine(monkeypatch):
    now = {"v": _ctx("CIANO56", "L100")}
    monkeypatch.setattr(MB, "loaded_context", lambda: dict(now["v"]))
    b = MB.MachineBinding(None)
    now["v"] = {}                                  # released, not replaced
    assert b.changed() is None
    now["v"] = {"die": "ciano56", "config": "L100", "duty": "rated"}
    assert b.changed() is None                     # same machine, other duty / case


def test_binding_compares_fingerprints_with_the_swept_keys_left_out(monkeypatch):
    monkeypatch.setattr(MB, "loaded_context", lambda: {})
    live = {"geo": {"wires": 11, "wire_height": 0.5}}

    def fp(keys):
        return json.dumps({k: v for k, v in sorted(live["geo"].items())
                           if k not in keys})

    b = MB.MachineBinding(fp, what="FEM scan")
    assert b.prime([("wire_height",)]) is None
    live["geo"]["wire_height"] = 0.9               # a swept key: allowed to differ
    assert b.changed(("wire_height",)) is None
    live["geo"]["wires"] = 14                      # the unsaved edit of the incident
    assert "edited" in b.changed(("wire_height",))


def test_a_run_that_waited_in_the_queue_notices_the_change_at_prime(monkeypatch):
    live = {"fp": "A"}
    monkeypatch.setattr(MB, "loaded_context", lambda: {})
    b = MB.MachineBinding(lambda keys: live["fp"])
    live["fp"] = "B"                               # edited between "Run" and the first point
    assert b.prime([()]) is not None


# ─────────────────────────────────────────────────────────────────────────────
#  The run's own machine in post-processing
# ─────────────────────────────────────────────────────────────────────────────


def test_a_job_reads_the_machine_it_was_registered_for(layered_two_machines):
    from motor_ai_sim.workspace import use_workspace

    a, _proc = layered_two_machines
    ctx_file = Path(str(a.root)) / ".family_context.json"
    ctx_file.write_text(json.dumps(_ctx("CIANO56", "L100")), encoding="utf-8")
    seen = {}

    def work():
        # the owner loads another motor while the run is in flight
        ctx_file.write_text(json.dumps(_ctx("CILN28", "G2-L40")), encoding="utf-8")
        seen["inside"] = RT.run_machine()
        return 1

    with use_workspace(a):
        J.run_job("transient", work, block=True)
        after = RT.run_machine()
    assert seen["inside"] == {"die": "CIANO56", "config": "L100"}
    assert after == {"die": "CILN28", "config": "G2-L40"}      # outside a job: as loaded


# ─────────────────────────────────────────────────────────────────────────────
#  POST /api/family/activate
# ─────────────────────────────────────────────────────────────────────────────

DIE_A, DIE_B = "TESTDIE 100", "TESTDIE 40"


@pytest.fixture
def dies(monkeypatch, tmp_path):
    root = tmp_path / "dies"
    for die in (DIE_A, DIE_B):
        (root / die).mkdir(parents=True)
        (root / die / "die.yaml").write_text(yaml.safe_dump({
            "name": die, "locked": True,
            "geometry": {"stator_diameter": 40.0, "num_seg": 2,
                         "num_slots_per_segment": 6, "num_poles_per_segment": 7},
        }, sort_keys=False), encoding="utf-8")
        (root / die / "L12.yaml").write_text(yaml.safe_dump({
            "name": "L12", "die": die, "geometry_overrides": {"motor_length": 12},
            "duties": [{"name": "rated", "mode": "motor", "rpm": 13000.0}],
        }, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(fam, "_DIES_DIR", root)
    monkeypatch.setattr(fam, "_CTX_FILE", tmp_path / ".family_context.json")
    fam._ctx_file().write_text(json.dumps(
        {"die": DIE_A, "config": "L12", "duty": "rated"}), encoding="utf-8")
    return root


_BUSY = [{"run_id": "scan-1", "kind": "optimizer.scan", "state": "running",
          "target": {"die": DIE_A, "config": "L12"}, "elapsed_s": 120.0}]


def test_loading_another_machine_with_a_job_running_is_a_409_with_a_code(
        dies, monkeypatch):
    monkeypatch.setattr(fam, "_jobs_running_here", lambda: list(_BUSY))
    with pytest.raises(HTTPException) as e:
        fam.activate(fam.Activate(die=DIE_B, config="L12", duty="rated"), _w={})
    assert e.value.status_code == 409
    assert e.value.detail["code"] == "job_running"
    assert e.value.detail["jobs"][0]["run_id"] == "scan-1"
    # nothing was loaded
    assert json.loads(fam._ctx_file().read_text(encoding="utf-8"))["die"] == DIE_A


def test_the_dialogs_load_without_saving_cancels_first_then_loads(dies, monkeypatch):
    monkeypatch.setattr(fam, "_jobs_running_here", lambda: list(_BUSY))
    cancelled = []
    monkeypatch.setattr(fam, "_cancel_jobs_here",
                        lambda jobs, wait_s=10.0: cancelled.extend(jobs) or [])
    out = fam.activate(fam.Activate(die=DIE_B, config="L12", duty="rated",
                                    cancel_running=True), _w={})
    assert out["ok"] is True
    assert out["cancelled_jobs"] == [{"run_id": "scan-1", "kind": "optimizer.scan"}]
    assert [j["run_id"] for j in cancelled] == ["scan-1"]
    assert json.loads(fam._ctx_file().read_text(encoding="utf-8"))["die"] == DIE_B


def test_cancel_running_is_what_the_dialog_calls_before_it_saves(dies, monkeypatch):
    monkeypatch.setattr(fam, "_jobs_running_here", lambda: list(_BUSY))
    seen = []
    monkeypatch.setattr(fam, "_cancel_jobs_here",
                        lambda jobs, wait_s=10.0: seen.extend(jobs) or [])
    out = fam.cancel_running(_w={})
    assert out == {"ok": True, "still_running": [],
                   "cancelled": [{"run_id": "scan-1", "kind": "optimizer.scan"}]}
    assert [j["run_id"] for j in seen] == ["scan-1"]
    monkeypatch.setattr(fam, "_jobs_running_here", lambda: [])
    assert fam.cancel_running(_w={})["cancelled"] == []


def test_nothing_running_or_the_same_machine_asks_nothing(dies, monkeypatch):
    monkeypatch.setattr(fam, "_jobs_running_here", lambda: [])
    assert fam.activate(fam.Activate(die=DIE_B, config="L12"), _w={})["ok"] is True
    # the same die / configuration / duty again moves nothing: no question even
    # with a job running
    fam._ctx_file().write_text(json.dumps(
        {"die": DIE_A, "config": "L12", "duty": "rated"}), encoding="utf-8")
    monkeypatch.setattr(fam, "_jobs_running_here", lambda: list(_BUSY))
    assert fam.activate(fam.Activate(die=DIE_A, config="L12", duty="rated"),
                        _w={})["ok"] is True


def test_the_real_queue_reports_a_running_job_of_this_workspace(dies):
    release = threading.Event()
    started = threading.Event()

    def work():
        started.set()
        release.wait(10)

    q = J.reset_queue(J.InProcessQueue(workers=2, field_limit=2))
    t = threading.Thread(target=lambda: J.run_job("transient", work, block=True))
    t.start()
    try:
        assert started.wait(5)
        rows = fam._jobs_running_here()
        assert [r["kind"] for r in rows] == ["transient"]
        with pytest.raises(HTTPException) as e:
            fam.activate(fam.Activate(die=DIE_B, config="L12"), _w={})
        assert e.value.detail["code"] == "job_running"
    finally:
        release.set()
        t.join(5)
        J.reset_queue(J.InProcessQueue(workers=2, field_limit=2))
    assert fam._jobs_running_here() == []
    assert q is not None


# ─────────────────────────────────────────────────────────────────────────────
#  The sweep
# ─────────────────────────────────────────────────────────────────────────────


class _LoadsAnotherMotorMidSweep(_FakeChild):
    """The owner presses ▶ on another motor while the SECOND point is solving."""

    n = 0
    ctx_file: Path = Path()

    def communicate(self, input=None, timeout=None):   # noqa: A002
        type(self).n += 1
        if type(self).n == 2:
            type(self).ctx_file.write_text(
                json.dumps(_ctx("CILN28", "G2-L40")), encoding="utf-8")
        return super().communicate(input=input, timeout=timeout)


def _rows(path: Path):
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip()]


def test_a_sweep_stops_when_its_machine_changes_and_writes_nothing_after_as_ok(
        layered_two_machines, monkeypatch):
    from motor_ai_sim.routes import optimization as opt
    from motor_ai_sim.workspace import use_workspace

    a, _proc = layered_two_machines
    root = Path(str(a.root))
    ctx_file = root / ".family_context.json"
    ctx_file.write_text(json.dumps(_ctx("CIANO56", "L100")), encoding="utf-8")
    _LoadsAnotherMotorMidSweep.n = 0
    _LoadsAnotherMotorMidSweep.ctx_file = ctx_file
    _LoadsAnotherMotorMidSweep.spawns = []
    monkeypatch.setattr(subprocess, "Popen", _LoadsAnotherMotorMidSweep, raising=True)
    monkeypatch.setattr(opt, "_record_eval_seconds", lambda *x, **k: None)
    monkeypatch.setattr(opt, "_EVAL_CACHE", {})
    monkeypatch.setenv("FEM_SCAN_WORKERS", "1")

    with use_workspace(a):
        start_fp = opt._config_fingerprint()
        with opt._scan_lock:
            opt._scan_state.update({"running": True, "done": 0, "total": 0,
                                    "result": None, "points": [], "run_id": "sweep_mix",
                                    "error": None, "cancel": False, "cached": 0,
                                    "source_changed": None})
        opt._scan_worker(
            [{"name": "wire_height", "min": 0.4, "max": 0.7,
              "mode": "sweep", "step": 0.1}],
            [{"gamma_deg": 10.0, "current_a": 63.6396, "rpm": 13000.0}],
            6, 120.0, 100.0, 5, 12345, "sweep_mix",
            mesh_size_mm=1.0, min_size_mm=0.3, n_sectors=2, element_order=2)
        with opt._scan_lock:
            result = dict(opt._scan_state["result"])

    # only ONE point was ever solved after the machine changed (the one in flight);
    # the other two were never dispatched
    assert len(_LoadsAnotherMotorMidSweep.spawns) == 2
    ds = _rows(root / ".opt_dataset.jsonl")
    ok = [r for r in ds if r["status"] == "ok"]
    bad = [r for r in ds if r["status"] != "ok"]
    assert len(ok) == 1                                     # the point BEFORE the load
    assert len(bad) == 1 and "source config changed" in bad[0]["error"]
    # ...filed under the machine it was meant for, not the one loaded when it failed
    assert bad[0]["cfg_fp"] == start_fp
    cache = _rows(root / ".scan_cache.jsonl")
    assert len([x for x in cache if "v" in x]) == 1         # one servable hit only

    # the record: every point accounted for, failed ones carry the reason
    assert result["n_total_points"] == 4 and result["n_built"] == 1
    failed = [p for p in result["points"] if not p["feasible"]]
    assert len(failed) == 3
    assert all(p["source_changed"] and "source config changed" in p["error"]
               for p in failed)
    assert result["source_changed"].startswith(MB.REASON_PREFIX)
    # ...and it is stamped with the machine it STARTED on, not the one at the end
    assert result["machine"]["die"] == "CIANO56" and result["machine"]["config"] == "L100"
    assert result["scan_params"]["cfg_fp"] == start_fp
    # a stopped sweep must not be auto-resumed after a restart onto another machine
    from motor_ai_sim import sweep_journal as SJ
    with use_workspace(a):
        j = SJ.load_sweep_journal(str(a.root))
    assert j is None or j.state != "running"


def test_a_sweep_on_an_unchanged_machine_is_untouched(layered_two_machines, monkeypatch):
    from motor_ai_sim.routes import optimization as opt
    from motor_ai_sim.workspace import use_workspace

    a, _proc = layered_two_machines
    root = Path(str(a.root))
    (root / ".family_context.json").write_text(
        json.dumps(_ctx("CIANO56", "L100")), encoding="utf-8")
    _FakeChild.spawns = []
    monkeypatch.setattr(subprocess, "Popen", _FakeChild, raising=True)
    monkeypatch.setattr(opt, "_record_eval_seconds", lambda *x, **k: None)
    monkeypatch.setattr(opt, "_EVAL_CACHE", {})
    monkeypatch.setenv("FEM_SCAN_WORKERS", "1")
    with use_workspace(a):
        with opt._scan_lock:
            opt._scan_state.update({"running": True, "done": 0, "total": 0,
                                    "result": None, "points": [], "run_id": "sweep_ok",
                                    "error": None, "cancel": False, "cached": 0,
                                    "source_changed": None})
        opt._scan_worker(
            [{"name": "wire_height", "min": 0.4, "max": 0.6,
              "mode": "sweep", "step": 0.1}],
            [{"gamma_deg": 10.0, "current_a": 63.6396, "rpm": 13000.0}],
            6, 120.0, 100.0, 5, 12345, "sweep_ok",
            mesh_size_mm=1.0, min_size_mm=0.3, n_sectors=2, element_order=2)
        with opt._scan_lock:
            result = dict(opt._scan_state["result"])
    assert result["n_built"] == 3 and "source_changed" not in result
    assert result["machine"]["die"] == "CIANO56"
    assert [r["status"] for r in _rows(root / ".opt_dataset.jsonl")] == ["ok"] * 3
