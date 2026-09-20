"""The WORKER threads of a request — the 2026-09-20 production incident.

WHAT HAPPENED
=============
The owner ran a 6-point ``wire_height`` sweep (0.4 / 0.5 / 0.6 × two currents)
in his own workspace on emotres.com, on his Ø50 machine — slot_height 7.5,
insulation 0.06, 9 wires per slot, so wire_height fits up to 0.72.  All six
points came back

    wire_height = 0.4 does not fit — the bound here is 0.3356

which is the bound of the Ø85 STARTER machine the api process is pointed at
(``MOTOR_AI_SIM_CONFIG=/srv/motres/identity/motor_config.yaml``: slot 7.4,
insulation 0.05, 18 wires, spacing 0.07).  Not one of the six was ever solved.

WHY
===
``_scan_worker`` runs in a thread that IS bound (``workspace.bind``), so the
sweep's own in-process grid gate read the right machine — but it dispatches
every eval through ``ThreadPoolExecutor.submit``, and a pool thread's context
is EMPTY: ``concurrent.futures`` copies no ``ContextVar`` (unlike Starlette's
threadpool, which is why a sync route handler was never affected).  In that
thread ``workspace()`` fell back to the process workspace, so ``_base_eval_env``
stamped the eval subprocess with the starter machine's config path and the
subprocess answered about a motor nobody asked about.  Same class as the
2026-09-15 import-bound config path and the Stage 3 ``bind()`` for threads, one
layer further out.

WHAT THIS FILE PINS
===================
* the mechanism — a plain pool loses the workspace, ``WorkspaceThreadPoolExecutor``
  carries the submitter's WHOLE context (workspace, caller, ``?mat=`` override);
* the behaviour — a sweep run in workspace A is evaluated on A's machine, at A's
  bound, with no point skipped and every write landing in A's folder;
* and that with ``WORKSPACES_ROOT`` unset none of it moved.

Nothing here solves a field: the eval subprocess is replaced by a stand-in that
does the one thing the real one did wrong (resolve the machine from its own
``MOTOR_AI_SIM_CONFIG`` and judge the candidate against it).  Every write lands
in ``tmp_path``.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml


_IDENT = {"Bearer A": "alice@example.com", "Bearer B": "bob@example.com"}

#: A's machine — the owner's Ø50, whose wire_height bound is 0.72.
_MACHINE_A = {"stator_diameter": 50.0, "slot_height": 7.5,
              "insulation_thickness": 0.06, "num_wires_per_slot": 9,
              "wire_spacing_y": 0.1, "wire_height": 0.5, "wire_width": 3.0}
#: The PROCESS machine — the Ø85 starter, whose bound is 0.3356.
_MACHINE_PROCESS = {"stator_diameter": 85.0, "slot_height": 7.4,
                    "insulation_thickness": 0.05, "num_wires_per_slot": 18,
                    "wire_spacing_y": 0.07, "wire_height": 0.3,
                    "wire_width": 3.5}

_BOUND_A = 0.72
_BOUND_PROCESS = 0.3356


def _write_machine(path: Path, geometry: dict) -> None:
    """A copy of the suite's reference machine with ``geometry`` overwritten."""
    from motor_ai_sim.config import DEFAULT_CONFIG_PATH

    cfg = yaml.safe_load(io.open(str(DEFAULT_CONFIG_PATH), encoding="utf-8"))
    cfg["geometry"].update(geometry)
    path.parent.mkdir(parents=True, exist_ok=True)
    io.open(str(path), "w", encoding="utf-8").write(
        yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True))


@pytest.fixture
def layered_two_machines(tmp_path, monkeypatch):
    """The production shape: process config = Ø85 starter, workspace A = Ø50.

    ``DEFAULT_CONFIG_PATH`` is MOVED rather than the suite's own copy edited, so
    the process workspace follows it exactly as it does on the server, where
    ``MOTOR_AI_SIM_CONFIG`` names ``identity/motor_config.yaml`` and the
    workspaces live beside it.
    """
    from motor_ai_sim import config as _config
    from motor_ai_sim import workspace as ws
    import motor_ai_sim.auth as auth

    proc_cfg = tmp_path / "identity" / "motor_config.yaml"
    _write_machine(proc_cfg, _MACHINE_PROCESS)
    monkeypatch.setattr(_config, "DEFAULT_CONFIG_PATH", proc_cfg, raising=True)
    monkeypatch.setenv("WORKSPACES_ROOT", str(tmp_path / "workspaces"))

    def _fake_identity(authorization=None):
        email = _IDENT.get(authorization if isinstance(authorization, str) else "")
        return {"id": email or auth.ANON_OWNER, "is_admin": False}

    monkeypatch.setattr(auth, "caller_identity", _fake_identity, raising=True)

    a = ws.workspace_for_identity(_IDENT["Bearer A"])
    _write_machine(Path(str(a.config_file)), _MACHINE_A)
    _config.clear_config_cache()
    yield a, proc_cfg
    _config.clear_config_cache()


def test_the_fixture_reproduces_the_two_bounds(layered_two_machines):
    """Sanity: the two machines really do disagree about wire_height 0.4."""
    from motor_ai_sim.config import get_config
    from motor_ai_sim.geometry_constraints import bounds, violation_message
    from motor_ai_sim.workspace import use_workspace

    a, _proc = layered_two_machines
    with use_workspace(a):
        geo = {**dict(get_config().get("geometry", {})), "wire_height": 0.4}
        assert bounds(geo)["wire_height"]["bound"] == pytest.approx(_BOUND_A)
        assert violation_message(geo) is None
    geo = {**dict(get_config().get("geometry", {})), "wire_height": 0.4}
    assert bounds(geo)["wire_height"]["bound"] == pytest.approx(_BOUND_PROCESS,
                                                               abs=1e-4)
    assert "0.3356" in (violation_message(geo) or "")


# ─────────────────────────────────────────────────────────────────────────────
#  The mechanism
# ─────────────────────────────────────────────────────────────────────────────

def test_a_plain_thread_pool_loses_the_workspace(layered_two_machines):
    """The hole itself, pinned so nobody "simplifies" the executor back.

    If this ever fails because ``concurrent.futures`` grew context propagation,
    ``WorkspaceThreadPoolExecutor`` has become redundant — until then a plain
    pool answers with the PROCESS machine no matter who submitted to it.
    """
    import concurrent.futures as cf
    from motor_ai_sim.workspace import use_workspace, workspace

    a, _proc = layered_two_machines
    with use_workspace(a):
        with cf.ThreadPoolExecutor(max_workers=1) as ex:
            assert ex.submit(lambda: workspace().id).result() != a.id


def test_the_workspace_pool_carries_the_submitters_whole_context(
        layered_two_machines):
    """Workspace, caller AND the ``?mat=`` override — a pool thread is an
    extension of the request that submitted to it."""
    from motor_ai_sim.config import config_path
    from motor_ai_sim.material_context import (get_request_materials,
                                               set_request_materials)
    from motor_ai_sim.workspace import (WorkspaceThreadPoolExecutor, caller,
                                        use_caller, use_workspace, workspace)

    a, _proc = layered_two_machines
    mats = {"assignment": {"magnet": "N52UH_150C"}, "materials": {}}

    def _probe(_i):
        return (workspace().id, str(config_path()),
                (caller() or {}).get("id"), get_request_materials())

    with use_workspace(a), use_caller({"id": _IDENT["Bearer A"]}):
        set_request_materials(mats)
        try:
            with WorkspaceThreadPoolExecutor(max_workers=4) as ex:
                rows = list(ex.map(_probe, range(8)))
        finally:
            set_request_materials(None)
    for ws_id, cfg, who, seen_mats in rows:
        assert ws_id == a.id
        assert Path(cfg) == Path(str(a.config_file))
        assert who == _IDENT["Bearer A"]
        assert seen_mats == mats, "the ?mat= assignment did not reach the worker"


def test_the_eval_env_built_inside_a_pool_worker_names_the_callers_machine(
        layered_two_machines):
    """The exact seam that broke: ``_base_eval_env`` run from a pool thread."""
    from motor_ai_sim.routes import optimization as opt
    from motor_ai_sim.workspace import (WorkspaceThreadPoolExecutor,
                                        use_workspace)

    a, _proc = layered_two_machines
    with use_workspace(a):
        with WorkspaceThreadPoolExecutor(max_workers=3) as ex:
            envs = list(ex.map(lambda _i: opt._eval_env_for(None), range(6)))
    for env in envs:
        assert Path(env["MOTOR_AI_SIM_CONFIG"]) == Path(str(a.config_file))
        # The workspace ROOT travels with it: a bare interpreter rebuilds its
        # process workspace from this path's parent, which is how all 25 disk
        # stores follow the caller into the subprocess.
        assert Path(env["MOTOR_AI_SIM_CONFIG"]).parent == Path(str(a.root))
        assert env["WORKSPACES_ROOT"], "layering must stay on in the child"


# ─────────────────────────────────────────────────────────────────────────────
#  The behaviour: a real sweep, a stand-in child
# ─────────────────────────────────────────────────────────────────────────────

class _FakeChild:
    """Stands in for ``python -m motor_ai_sim.optimization.refine_proc``.

    It does the ONE thing the real child did wrong here: resolve the machine
    from its own ``MOTOR_AI_SIM_CONFIG`` and judge the candidate's geometry
    against it — ``refine_proc.run_one``'s first fifteen lines.  No mesh, no
    FEM, but the production failure is reproduced end to end, so this test fails
    with the owner's own message before the fix and passes after it.
    """

    #: (env, overrides) of every eval spawned, in order.
    spawns: list = []

    def __init__(self, argv, env=None, **kw):
        self.argv = list(argv)
        self.env = dict(env or {})
        self.pid = id(self)
        self.returncode = 0

    def communicate(self, input=None, timeout=None):   # noqa: A002 — stdlib name
        from motor_ai_sim.geometry_constraints import violation_message

        spec = json.loads(input)
        type(self).spawns.append((self.env, spec["overrides"]))
        cfg = yaml.safe_load(io.open(self.env.get("MOTOR_AI_SIM_CONFIG", ""),
                                     encoding="utf-8"))
        geo = {**dict(cfg.get("geometry") or {}), **spec["overrides"]}
        why = violation_message(geo)
        if why:
            payload = {"ok": False, "error": why}
        else:
            payload = {"ok": True, "res": {
                "T_em_Nm": 1.0 + float(spec["overrides"].get("wire_height", 0.0)),
                "efficiency": 0.95, "torque_per_mass_Nm_kg": 1.0,
                "T_ripple_pct": 2.0, "P_loss_total_W": 100.0,
                "mass_total_kg": 1.0, "V_peak": 100.0,
                # What machine the worker actually built, for the assertions.
                "slot_height_seen": float(geo["slot_height"]),
                "num_wires_seen": int(geo["num_wires_per_slot"]),
            }}
        return ("@@RESULT@@" + json.dumps(payload), "")

    def poll(self):
        return self.returncode

    def kill(self):
        pass


def _run_sweep(run_id: str) -> list:
    """``_scan_worker`` exactly as the route drives it, minus the outer thread.

    The route wraps this in ``threading.Thread(target=workspace.bind(...))``;
    here the caller's ``use_workspace`` block is that binding, which leaves the
    POOL inside ``_scan_worker`` as the only thing under test.
    """
    from motor_ai_sim.routes import optimization as opt

    with opt._scan_lock:
        opt._scan_state.update({"running": True, "done": 0, "total": 0,
                                "result": None, "points": [], "run_id": run_id,
                                "error": None, "cancel": False, "cached": 0})
    opt._scan_worker(
        [{"name": "wire_height", "min": 0.4, "max": 0.6,
          "mode": "sweep", "step": 0.1}],
        [{"gamma_deg": 10.0, "current_a": 63.6396, "rpm": 13000.0}],
        6, 120.0, 100.0, 3, 12345, run_id,
        mesh_size_mm=1.0, min_size_mm=0.3, n_sectors=2, element_order=2)
    with opt._scan_lock:
        return list(opt._scan_state["points"])


def test_a_sweep_in_a_workspace_is_evaluated_on_that_workspaces_machine(
        layered_two_machines, monkeypatch):
    """THE regression test for sweep_6_1789908279491.

    Three wire_height values that fit A's slot and none of the process one's.
    Before the fix every eval subprocess was spawned with the process config and
    all three came back "the bound here is 0.3356".
    """
    from motor_ai_sim.workspace import use_workspace

    a, proc_cfg = layered_two_machines
    _FakeChild.spawns = []
    monkeypatch.setattr(subprocess, "Popen", _FakeChild, raising=True)

    with use_workspace(a):
        points = _run_sweep("sweep_ws_regression")

    assert len(points) == 3, points
    for pt in points:
        wh = pt["overrides"]["wire_height"]
        assert not pt.get("geometry_rejected"), (
            "the in-process grid gate rejected %s against the wrong machine: %s"
            % (wh, pt.get("error")))
        assert pt["feasible"], "wire_height %s was skipped: %s" % (
            wh, pt.get("error"))
        # The worker really did build A's machine, not the starter's.
        assert pt["slot_height_seen"] == pytest.approx(_MACHINE_A["slot_height"])
        assert pt["num_wires_seen"] == _MACHINE_A["num_wires_per_slot"]
    assert "0.3356" not in json.dumps(points), (
        "a point was judged against the process machine's slot")

    # …and every subprocess was SPAWNED with A's config path, which is the only
    # channel a bare interpreter has.
    assert len(_FakeChild.spawns) == 3
    for env, ov in _FakeChild.spawns:
        assert Path(env["MOTOR_AI_SIM_CONFIG"]) == Path(str(a.config_file))
        assert Path(env["MOTOR_AI_SIM_CONFIG"]) != Path(str(proc_cfg))
        assert 0.4 <= float(ov["wire_height"]) <= 0.6

    # The pool worker's WRITES landed in A's workspace too — the eval cache and
    # the chart — and never in the process one.
    assert (Path(str(a.root)) / ".scan_cache.jsonl").is_file()
    assert (Path(str(a.root)) / ".last_scan.json").is_file()
    assert not (Path(str(proc_cfg)).parent / ".scan_cache.jsonl").exists()
    assert not (Path(str(proc_cfg)).parent / ".last_scan.json").exists()


def test_single_user_mode_is_unchanged(tmp_path, monkeypatch):
    """No ``WORKSPACES_ROOT``: the pool, the eval env and the sweep are what
    they were — the promise at the top of ``workspace.py``."""
    from motor_ai_sim import config as _config
    from motor_ai_sim.routes import optimization as opt
    from motor_ai_sim.workspace import (PROCESS_WS_ID,
                                        WorkspaceThreadPoolExecutor, workspace)

    assert not os.environ.get("WORKSPACES_ROOT"), (
        "this test describes the single-user case")
    proc_cfg = tmp_path / "solo" / "motor_config.yaml"
    _write_machine(proc_cfg, _MACHINE_A)
    monkeypatch.setattr(_config, "DEFAULT_CONFIG_PATH", proc_cfg, raising=True)
    _config.clear_config_cache()
    try:
        with WorkspaceThreadPoolExecutor(max_workers=2) as ex:
            rows = list(ex.map(
                lambda _i: (workspace().id, str(_config.config_path()),
                            opt._eval_env_for(None)["MOTOR_AI_SIM_CONFIG"]),
                range(4)))
        for ws_id, cfg, env_cfg in rows:
            assert ws_id == PROCESS_WS_ID
            assert Path(cfg) == proc_cfg and Path(env_cfg) == proc_cfg

        _FakeChild.spawns = []
        monkeypatch.setattr(subprocess, "Popen", _FakeChild, raising=True)
        points = _run_sweep("sweep_solo")
        assert len(points) == 3 and all(p["feasible"] for p in points), points
        for env, _ov in _FakeChild.spawns:
            assert Path(env["MOTOR_AI_SIM_CONFIG"]) == proc_cfg
    finally:
        _config.clear_config_cache()
