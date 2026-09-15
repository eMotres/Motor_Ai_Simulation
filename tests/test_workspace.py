"""The per-request workspace resolver — migration Stage 1.

TWO PROMISES, AND THIS FILE IS BOTH OF THEM
===========================================

**1. With ``WORKSPACES_ROOT`` unset, nothing moved.**  Twenty-five disk stores
were audited on 2026-09-15 (``tests/test_config_redirect_is_complete.py``) and
every one of them was ``Path(DEFAULT_CONFIG_PATH).parent / <name>``.  Stage 1
re-points all of them at ``workspace.root()``, which with no workspace set *is*
that expression.  The first test below pins that equality store by store: the
live single-user server keeps reading and writing the very same files, and a
regression here is a regression in the most expensive direction there is (the
2026-08-06 incident: a run replaced the machine the user had loaded).

**2. With it set, two callers cannot see each other's machine.**  This is the
risk §7.1 of the migration plan names first — *a missed reader silently serves
the owner's machine to a customer*.  The concurrency test runs two identities
through the real middleware at the same time, many times over, and asserts that
neither thread ever names the other's file, that a write by A never reaches B,
and that the parsed-config cache (now keyed by resolved path) hands each thread
its own pole count rather than whichever of them parsed last.

The subprocess test is the same promise one process boundary out: an optimizer
eval is a bare interpreter whose ONLY channel is ``MOTOR_AI_SIM_CONFIG``, and
``_EVAL_ENV`` used to be captured from ``os.environ`` at import — so every eval
would have solved the owner's machine while the caller waited (plan item 30).

Nothing here solves a field, and every write lands in ``tmp_path``.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest
import yaml


# ─────────────────────────────────────────────────────────────────────────────
#  1.  WORKSPACES_ROOT unset -> byte-identical to yesterday
# ─────────────────────────────────────────────────────────────────────────────

def _stores_today():
    """(label, resolved, what it was before Stage 1) for every workspace store.

    The third element is spelled out LONGHAND on purpose — as
    ``Path(DEFAULT_CONFIG_PATH).parent / …``, the literal expression the module
    used to hold — so this test cannot pass by agreeing with the new code about
    a mistake.
    """
    from motor_ai_sim import api as api_mod, audit as audit_mod
    from motor_ai_sim import duty_fields, duty_results
    from motor_ai_sim.config import DEFAULT_CONFIG_PATH, config_path
    from motor_ai_sim.optimization import doe, surrogate
    from motor_ai_sim.routes import (catalog, coupled, family, fusion, geometry,
                                     mechanical, my_motors, optimization,
                                     panel_settings, presets, saved_sims,
                                     simulation, static3d, sweep_config, thermal)
    from motor_ai_sim.simulation import fem_solver_2d, geometry_2d

    here = Path(str(DEFAULT_CONFIG_PATH)).parent
    return [
        ("config.config_path()", config_path(), Path(str(DEFAULT_CONFIG_PATH))),
        ("geometry_2d._cfg_path()", geometry_2d._cfg_path(),
         Path(str(DEFAULT_CONFIG_PATH))),
        ("api._CONFIG_PATH", api_mod._CONFIG_PATH, Path(str(DEFAULT_CONFIG_PATH))),
        ("audit._HISTORY_DIR", audit_mod._HISTORY_DIR, here / ".presets_history"),
        ("catalog._CATALOG_PATH", catalog._CATALOG_PATH, here / "motor_catalog.json"),
        ("presets._CONFIG_PATH", presets._CONFIG_PATH, Path(str(DEFAULT_CONFIG_PATH))),
        ("presets._CATALOG_PATH", presets._CATALOG_PATH, here / "motor_catalog.json"),
        ("saved_sims._STORE", saved_sims._STORE, here / "saved_simulations.json"),
        ("sweep_config._STORE", sweep_config._STORE, here / "sweep_config.json"),
        ("static3d._CACHE_DIR", static3d._CACHE_DIR, here / ".static3d_cache"),
        ("optimization._scan_store_path()", optimization._scan_store_path(),
         here / ".last_scan.json"),
        ("optimization._eval_cache_path()", optimization._eval_cache_path(),
         here / ".scan_cache.jsonl"),
        ("optimization._descent_store_path()", optimization._descent_store_path(),
         here / ".last_descent.json"),
        ("optimization._dataset_path()", optimization._dataset_path(),
         here / ".opt_dataset.jsonl"),
        ("doe.doe_path()", doe.doe_path(), here / ".doe_dataset.jsonl"),
        ("surrogate.dataset_path()", surrogate.dataset_path(),
         here / ".opt_dataset.jsonl"),
        ("panel_settings._store_path()", panel_settings._store_path(),
         here / ".panel_settings.json"),
        ("family._CTX_FILE", family._CTX_FILE, here / ".family_context.json"),
        ("family._DIES_DIR", family._DIES_DIR, here / "dies"),
        ("geometry._MESH_DISK_DIR", geometry._MESH_DISK_DIR, here / ".mesh_cache"),
        ("fusion._MAP_FILE", fusion._MAP_FILE, here / "fusion_param_map.yaml"),
        ("my_motors._STORE", my_motors._STORE, here / "user_motors.json"),
        ("duty_results.store_path()", duty_results.store_path(),
         here / ".duty_results.json"),
        ("duty_fields._config_dir()", duty_fields._config_dir(), here),
        ("thermal._last_store_path()", thermal._last_store_path(),
         here / ".last_thermal.pkl"),
        ("thermal._loss_maps_path()", thermal._loss_maps_path(),
         here / ".thermal_loss_maps.pkl"),
        ("mechanical._last_store_path()", mechanical._last_store_path(),
         here / ".last_mechanical.pkl"),
        ("coupled._last_store_path()", coupled._last_store_path(),
         here / ".last_coupled.json"),
        ("simulation._transient_store_path()", simulation._transient_store_path(),
         here / ".last_transient.json"),
        ("simulation._ledger_dir()", simulation._ledger_dir(), here / ".run_ledger"),
        ("simulation._bench_cache_path()", simulation._bench_cache_path(),
         here / ".bench_ldq.json"),
        ("fem_solver_2d._warm_cache_path()", fem_solver_2d._warm_cache_path(),
         here / ".warm_cache.npz"),
        ("fem_solver_2d._daxis_disk_path()", fem_solver_2d._daxis_disk_path(),
         here / ".daxis_cache.json"),
    ]


@pytest.mark.parametrize("label", [lbl for lbl, _, _ in _stores_today()])
def test_with_no_workspaces_root_every_store_is_where_it_always_was(label):
    """THE BYTE-IDENTITY GATE.  Stage 1 may not move one file on this machine."""
    assert not os.environ.get("WORKSPACES_ROOT"), (
        "this test describes the single-user case; WORKSPACES_ROOT must not be "
        "set in the environment running the suite")
    _, got, want = next(r for r in _stores_today() if r[0] == label)
    assert Path(str(got)).resolve() == Path(str(want)).resolve(), (
        "%s moved: %s, was %s" % (label, got, want))


def test_the_process_workspace_follows_a_monkeypatched_default_config_path(tmp_path,
                                                                          monkeypatch):
    """The suite moves ``config.DEFAULT_CONFIG_PATH`` by monkeypatch, and every
    honest reader must follow — that is the 2026-09-15 ``params_from_config``
    regression.  A workspace snapshotted at import would reintroduce it."""
    import motor_ai_sim.config as cfgmod
    from motor_ai_sim import workspace as ws

    other = tmp_path / "elsewhere" / "motor_config.yaml"
    other.parent.mkdir(parents=True)
    other.write_text("geometry: {}\n", encoding="utf-8")
    monkeypatch.setattr(cfgmod, "DEFAULT_CONFIG_PATH", other, raising=True)

    assert ws.root() == other.parent
    assert Path(str(ws.config_file())) == other
    # A redirect that names a file, not a folder: the filename must survive.
    named = tmp_path / "ten.yaml"
    named.write_text("geometry: {}\n", encoding="utf-8")
    monkeypatch.setattr(cfgmod, "DEFAULT_CONFIG_PATH", named, raising=True)
    assert Path(str(cfgmod.config_path())) == named


def test_workspace_ids_are_stable_and_case_folded():
    from motor_ai_sim import workspace as ws

    assert ws.workspace_id("Vadim@Example.com") == ws.workspace_id("vadim@example.com")
    assert len(ws.workspace_id("a@b.c")) == 16
    assert ws.workspace_id("a@b.c") != ws.workspace_id("d@b.c")


def test_an_identity_resolves_to_the_process_workspace_while_the_env_is_unset():
    """NOTHING changes on this machine until ``WORKSPACES_ROOT`` exists."""
    from motor_ai_sim import workspace as ws

    assert ws.workspaces_root() is None
    assert ws.workspace_for_identity("someone@example.com") is ws.process_workspace()
    assert ws.workspace_for_request("Bearer whatever") is ws.process_workspace()


# ─────────────────────────────────────────────────────────────────────────────
#  2.  WORKSPACES_ROOT set -> two identities, two machines
# ─────────────────────────────────────────────────────────────────────────────

_IDENT = {"Bearer A": "alice@example.com", "Bearer B": "bob@example.com"}


def _seed(ws_obj, num_poles: int, num_slots: int) -> None:
    """Give a workspace its own machine, differing where it is most visible."""
    from motor_ai_sim.config import DEFAULT_CONFIG_PATH

    cfg = yaml.safe_load(io.open(str(DEFAULT_CONFIG_PATH), encoding="utf-8"))
    g = cfg["geometry"]
    g["num_seg"] = 1
    g["num_poles_per_segment"] = num_poles
    g["num_slots_per_segment"] = num_slots
    g["num_poles"] = num_poles
    g["num_slots"] = num_slots
    Path(str(ws_obj.config_file)).parent.mkdir(parents=True, exist_ok=True)
    io.open(str(ws_obj.config_file), "w", encoding="utf-8").write(
        yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True))


@pytest.fixture
def two_workspaces(tmp_path, monkeypatch):
    """``WORKSPACES_ROOT`` on, ``caller_identity`` stubbed, A and B seeded."""
    from motor_ai_sim import workspace as ws
    import motor_ai_sim.auth as auth
    from motor_ai_sim.config import clear_config_cache

    monkeypatch.setenv("WORKSPACES_ROOT", str(tmp_path / "workspaces"))

    def _fake_identity(authorization=None):
        email = _IDENT.get(authorization if isinstance(authorization, str) else "")
        if email:
            return {"id": email, "is_admin": False}
        return {"id": auth.ANON_OWNER, "is_admin": False}

    monkeypatch.setattr(auth, "caller_identity", _fake_identity, raising=True)

    a = ws.workspace_for_identity(_IDENT["Bearer A"])
    b = ws.workspace_for_identity(_IDENT["Bearer B"])
    assert a.root != b.root
    _seed(a, 10, 12)
    _seed(b, 28, 24)
    clear_config_cache()
    yield a, b
    clear_config_cache()


def test_ensure_layout_seeds_a_new_workspace_from_the_process_machine(tmp_path,
                                                                     monkeypatch):
    """A brand-new user must open a WORKING machine, not a 404."""
    from motor_ai_sim import workspace as ws
    from motor_ai_sim.config import DEFAULT_CONFIG_PATH

    monkeypatch.setenv("WORKSPACES_ROOT", str(tmp_path / "workspaces"))
    fresh = ws.workspace_for_identity("brand-new@example.com")
    assert Path(str(fresh.config_file)).is_file()
    assert (Path(str(fresh.config_file)).read_bytes()
            == Path(str(DEFAULT_CONFIG_PATH)).read_bytes())


def _probe() -> dict:
    """What a solve-side call stack sees.  Deliberately reaches THROUGH the API
    layer: the config resolver, a sidecar store, and the solver's own
    ``params_from_config`` — the exact reader that leaked on 2026-09-15."""
    from motor_ai_sim import duty_results
    from motor_ai_sim.config import config_path
    from motor_ai_sim.routes import thermal
    from motor_ai_sim.simulation.geometry_2d import params_from_config

    return {
        "config": str(config_path()),
        "duty": str(duty_results.store_path()),
        "thermal_last": str(thermal._last_store_path()),
        "num_poles": int(params_from_config().num_poles),
    }


@pytest.fixture
def probe_app():
    """A minimal app carrying the REAL resolver and a SYNC handler.

    Sync on purpose: Starlette runs a ``def`` endpoint in a threadpool worker,
    and the whole design rests on the request's context being copied into that
    worker (the guarantee ``material_context`` already leans on).  An ``async``
    handler would prove nothing about the case every solve route is.
    """
    from fastapi import FastAPI
    from motor_ai_sim.workspace import install_workspace_resolver, workspace

    app = FastAPI()
    install_workspace_resolver(app)

    @app.get("/probe")
    def probe():                     # noqa: ANN202 — sync BY DESIGN, see above
        out = _probe()
        out["ws_id"] = workspace().id
        out["thread"] = threading.current_thread().name
        return out

    return app


def test_the_contextvar_reaches_a_sync_handler_and_a_nested_solver_helper(
        two_workspaces, probe_app):
    from fastapi.testclient import TestClient

    a, b = two_workspaces
    client = TestClient(probe_app)

    ra = client.get("/probe", headers={"Authorization": "Bearer A"}).json()
    rb = client.get("/probe", headers={"Authorization": "Bearer B"}).json()
    anon = client.get("/probe").json()

    assert ra["ws_id"] == a.id and rb["ws_id"] == b.id
    assert Path(ra["config"]) == Path(str(a.config_file))
    assert Path(rb["config"]) == Path(str(b.config_file))
    # The nested solver-side reader — not a route, not a dependency, just the
    # next frame down — took the SAME machine.
    assert ra["num_poles"] == 10 and rb["num_poles"] == 28
    # No identity -> the process config, byte for byte the single-user answer.
    from motor_ai_sim.config import DEFAULT_CONFIG_PATH
    assert Path(anon["config"]) == Path(str(DEFAULT_CONFIG_PATH))


def test_two_concurrent_requests_never_see_each_others_machine(two_workspaces,
                                                               probe_app):
    """The plan's risk §7.1, made executable.

    Twenty-five interleaved round trips per identity, through the real
    middleware, in two threads.  Every answer must name the caller's own files
    and the caller's own pole count — including the parsed-config cache, which
    was a SINGLE slot before Stage 1 and would have handed whichever machine
    parsed last to whoever asked next.
    """
    from fastapi.testclient import TestClient

    a, b = two_workspaces
    results = {"A": [], "B": []}
    errors = []
    start = threading.Barrier(2)

    def hammer(tag: str, hdr: str):
        try:
            client = TestClient(probe_app)
            start.wait(timeout=10)
            for _ in range(25):
                r = client.get("/probe", headers={"Authorization": hdr})
                assert r.status_code == 200, r.text
                results[tag].append(r.json())
        except Exception as exc:                        # noqa: BLE001
            errors.append("%s: %r" % (tag, exc))

    ta = threading.Thread(target=hammer, args=("A", "Bearer A"), name="wsA")
    tb = threading.Thread(target=hammer, args=("B", "Bearer B"), name="wsB")
    ta.start(); tb.start(); ta.join(60); tb.join(60)

    assert not errors, errors
    assert len(results["A"]) == 25 and len(results["B"]) == 25

    for tag, ws_obj, poles in (("A", a, 10), ("B", b, 28)):
        other = b if tag == "A" else a
        for row in results[tag]:
            assert row["ws_id"] == ws_obj.id
            assert Path(row["config"]) == Path(str(ws_obj.config_file))
            assert Path(row["duty"]).parent == Path(str(ws_obj.root))
            assert Path(row["thermal_last"]).parent == Path(str(ws_obj.root))
            assert str(other.root) not in json.dumps(row), (
                "%s was handed a path inside the OTHER workspace: %s" % (tag, row))
            assert row["num_poles"] == poles, (
                "%s read the other machine's pole count — the parsed-config "
                "cache is not keyed by path" % tag)


def test_a_write_by_A_is_invisible_to_B(two_workspaces):
    """The real app, the real middleware, and the writer the redirect exists for.

    ``PATCH /api/simulation/config`` rewrites ``simulation:`` in the live machine.
    Signed in as A it must land in A's file and touch neither B's nor the
    process's.
    """
    from fastapi.testclient import TestClient
    from motor_ai_sim.api import app
    from motor_ai_sim.config import DEFAULT_CONFIG_PATH, clear_config_cache

    a, b = two_workspaces
    a_file, b_file = Path(str(a.config_file)), Path(str(b.config_file))
    process_file = Path(str(DEFAULT_CONFIG_PATH))
    b_before = b_file.read_bytes()
    process_before = process_file.read_bytes()

    want = float(yaml.safe_load(a_file.read_text(encoding="utf-8"))
                 ["simulation"].get("rpm", 1000)) + 13.0
    r = TestClient(app).patch("/api/simulation/config", json={"rpm": want},
                              headers={"Authorization": "Bearer A"})
    assert r.status_code == 200, r.text
    clear_config_cache()

    after_a = yaml.safe_load(a_file.read_text(encoding="utf-8"))["simulation"]
    assert float(after_a["rpm"]) == pytest.approx(want)
    assert b_file.read_bytes() == b_before, "A's PATCH reached B's machine"
    assert process_file.read_bytes() == process_before, (
        "A's PATCH reached the process config — on a server that is the OWNER's "
        "machine, which is exactly the incident this migration exists to prevent")
    # And B still reads its own numbers afterwards.
    from motor_ai_sim.workspace import use_workspace
    with use_workspace(b):
        assert _probe()["num_poles"] == 28


def test_the_resolver_is_installed_on_the_real_app():
    """A middleware that exists but is not mounted protects nobody."""
    from motor_ai_sim.api import app
    from motor_ai_sim.workspace import WorkspaceMiddleware

    assert any(m.cls is WorkspaceMiddleware for m in app.user_middleware), (
        "WorkspaceMiddleware is not in the app's middleware stack")


# ─────────────────────────────────────────────────────────────────────────────
#  3.  Out of the process: eval subprocesses and scripts
# ─────────────────────────────────────────────────────────────────────────────

def test_the_optimizer_eval_env_carries_the_callers_workspace(two_workspaces):
    """Plan item 30.  ``_EVAL_ENV`` was captured from ``os.environ`` at import,
    so every ``refine_proc`` eval would have solved whatever machine the SERVER
    was started on — the owner's — no matter who asked."""
    from motor_ai_sim.routes import optimization as opt
    from motor_ai_sim.workspace import use_workspace

    a, b = two_workspaces
    for ws_obj in (a, b):
        with use_workspace(ws_obj):
            env = opt._eval_env_for(None)
            assert Path(env["MOTOR_AI_SIM_CONFIG"]) == Path(str(ws_obj.config_file))
            # The thread pinning and the sweep seed flag are unchanged.
            assert env["OMP_NUM_THREADS"] == "1"
            assert env["SB_SEED_FROM_PREVIOUS"] == "1"
            multi = opt._eval_env_for(4)
            assert multi["OMP_NUM_THREADS"] == "4"
            assert Path(multi["MOTOR_AI_SIM_CONFIG"]) == Path(str(ws_obj.config_file))
    # Fresh dicts, never one shared mutable one.
    assert opt._eval_env_for(None) is not opt._eval_env_for(None)


def test_a_subprocess_with_no_identity_uses_the_process_config(tmp_path):
    """``refine_proc`` and every script: no request, no ContextVar, just the env.

    Run with ``WORKSPACES_ROOT`` SET and no identity at all — the case an eval
    subprocess on the server is in — the answer must still be the file
    ``MOTOR_AI_SIM_CONFIG`` names.
    """
    from motor_ai_sim.config import DEFAULT_CONFIG_PATH

    env = dict(os.environ)
    env["WORKSPACES_ROOT"] = str(tmp_path / "workspaces")
    env["MOTOR_AI_SIM_CONFIG"] = str(DEFAULT_CONFIG_PATH)
    env["MOTOR_AI_NO_FILE_LOG"] = "1"
    out = subprocess.run(
        [sys.executable, "-c",
         "from motor_ai_sim.config import config_path;"
         "from motor_ai_sim.workspace import root;"
         "print(config_path());print(root())"],
        env=env, capture_output=True, text=True, timeout=180)
    assert out.returncode == 0, out.stderr
    lines = [ln.strip() for ln in out.stdout.strip().splitlines() if ln.strip()]
    assert Path(lines[-2]) == Path(str(DEFAULT_CONFIG_PATH))
    assert Path(lines[-1]) == Path(str(DEFAULT_CONFIG_PATH)).parent
