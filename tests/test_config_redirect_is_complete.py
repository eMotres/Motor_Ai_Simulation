"""``MOTOR_AI_SIM_CONFIG`` must redirect EVERY reader of the motor config.

The redirect exists because a test run once replaced the user's live 150 mm
CIANO28 with a fixture while they were working (config.py, 2026-08-06), and
``tests/conftest.py`` points it at a throwaway copy before anything is imported.
Its promise is absolute: *a test must never be able to reach the machine the
user has loaded.*

``simulation/geometry_2d.py`` broke that promise for the one call that matters
most.  ``params_from_config`` is where a transient gets its ``MotorDomainParams``
whenever the request carries no ``geo_override`` — i.e. every ordinary Run — and
its ``cfg_path`` default was a module constant pinned to the repo's own
``config/motor_config.yaml``, bound at import and therefore immune to the env
var.  A redirected process meshed the machine it was given and took its POLE
COUNT from the user's live config.

That cost a 93-minute Ø200 PWM run on 2026-09-15: CAD and mesh at 12 slots /
10 poles, ``p.num_poles`` at 28, so ``f_elec = rpm * num_poles // 2 / 60`` came
out 4666.67 Hz instead of 1666.67 Hz at 20 000 rpm.  Every PWM settle window and
period-mean DC anchor was sized on that period and the run died at the DC gate
with -175.114 A left in phase A.

The same hole was open on the WRITE side, which is worse: a reader taking the
wrong pole count wastes a run, a writer taking the wrong path EDITS THE USER'S
MACHINE.  ``PATCH /api/simulation/config`` rebuilt its path as
``Path(__file__)…/config/motor_config.yaml`` and so rewrote the real operating
point whatever the redirect said — and twelve more stores under ``config/``
(``api._CONFIG_PATH``'s four handlers, both catalog paths, the saved-simulation
and sweep stores, the presets history, the static-3D cache and the five
sweep/eval datasets) were pinned the same way.  The second half of this file
pins the fix: with the redirect in force NOTHING under the repo's own
``config/`` may be opened for writing.

The reading tests do not write and do not solve; the writing tests write only
inside the pytest sandbox, and every write attempt outside it is intercepted
before it reaches the disk.
"""
from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path

import pytest
import yaml


def _write_machine(path: Path, *, num_seg, poles_per_seg, slots_per_seg,
                   num_poles, num_slots):
    """A copy of the real config with a DIFFERENT pole/slot count."""
    from motor_ai_sim.config import DEFAULT_CONFIG_PATH
    cfg = yaml.safe_load(io.open(str(DEFAULT_CONFIG_PATH), encoding="utf-8"))
    g = cfg["geometry"]
    g["num_seg"] = num_seg
    g["num_poles_per_segment"] = poles_per_seg
    g["num_slots_per_segment"] = slots_per_seg
    g["num_poles"] = num_poles
    g["num_slots"] = num_slots
    io.open(str(path), "w", encoding="utf-8").write(
        yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True))
    return num_poles


def test_params_from_config_follows_the_redirect(tmp_path, monkeypatch):
    """THE REGRESSION.  Redirect the env var, and the params must follow."""
    import motor_ai_sim.config as cfgmod
    from motor_ai_sim.simulation.geometry_2d import params_from_config

    other = tmp_path / "motor_config.yaml"
    want = _write_machine(other, num_seg=2, poles_per_seg=5, slots_per_seg=6,
                          num_poles=10, num_slots=12)
    # `DEFAULT_CONFIG_PATH` is resolved at import from the env var; the module
    # attribute is what every honest reader consults, so move that.
    monkeypatch.setattr(cfgmod, "DEFAULT_CONFIG_PATH", other, raising=True)

    p = params_from_config()
    assert int(p.num_poles) == want, (
        "params_from_config read a different file from DEFAULT_CONFIG_PATH — "
        "the redirect is not complete and a redirected process is solving the "
        "user's machine")
    assert int(p.num_slots) == 12


def test_the_default_argument_is_not_bound_at_import(tmp_path, monkeypatch):
    """The specific trap: a mutable default captured once.

    `cfg_path` must default to None and be resolved per call.  A default of
    `_CFG_PATH` would make the test above pass only by accident on a process
    that happened to import late.
    """
    import inspect
    from motor_ai_sim.simulation import geometry_2d as g2

    sig = inspect.signature(g2.params_from_config)
    assert sig.parameters["cfg_path"].default is None
    sig2 = inspect.signature(g2.MotorDomains2D.from_config)
    assert sig2.parameters["cfg_path"].default is None


def test_an_explicit_path_still_wins(tmp_path, monkeypatch):
    """The per-request channel is untouched: a caller that names a file gets it."""
    import motor_ai_sim.config as cfgmod
    from motor_ai_sim.simulation.geometry_2d import params_from_config

    redirected = tmp_path / "redirected.yaml"
    explicit = tmp_path / "explicit.yaml"
    _write_machine(redirected, num_seg=2, poles_per_seg=5, slots_per_seg=6,
                   num_poles=10, num_slots=12)
    _write_machine(explicit, num_seg=4, poles_per_seg=7, slots_per_seg=6,
                   num_poles=28, num_slots=24)
    monkeypatch.setattr(cfgmod, "DEFAULT_CONFIG_PATH", redirected, raising=True)

    assert int(params_from_config().num_poles) == 10
    assert int(params_from_config(explicit).num_poles) == 28


def test_f_elec_is_what_the_redirected_machine_says(tmp_path, monkeypatch):
    """The exact arithmetic of the 2026-09-15 failure, both directions.

    ``fem_solver_2d`` derives the electrical frequency as
    ``rpm * (p.num_poles // 2) / 60`` — so a pole count from the wrong file is a
    fundamental from the wrong machine, and every PWM settle window with it.
    """
    import motor_ai_sim.config as cfgmod
    from motor_ai_sim.simulation.geometry_2d import params_from_config

    ten = tmp_path / "ten.yaml"
    twentyeight = tmp_path / "twentyeight.yaml"
    _write_machine(ten, num_seg=2, poles_per_seg=5, slots_per_seg=6,
                   num_poles=10, num_slots=12)
    _write_machine(twentyeight, num_seg=4, poles_per_seg=7, slots_per_seg=6,
                   num_poles=28, num_slots=24)

    def f_elec(p, rpm):
        return rpm * (int(p.num_poles) // 2) / 60.0

    monkeypatch.setattr(cfgmod, "DEFAULT_CONFIG_PATH", ten, raising=True)
    assert f_elec(params_from_config(), 20000.0) == pytest.approx(1666.67, abs=0.01)
    monkeypatch.setattr(cfgmod, "DEFAULT_CONFIG_PATH", twentyeight, raising=True)
    assert f_elec(params_from_config(), 20000.0) == pytest.approx(4666.67, abs=0.01)


# ─────────────────────────────────────────────────────────────────────────────
#  THE WRITE SIDE.  Nothing may write under the repo's own config/.
# ─────────────────────────────────────────────────────────────────────────────

#: The real folder — the one holding the machine the user has loaded.  Every
#: assertion below is "not this", never "not some particular file in this".
REPO_CONFIG = Path(__file__).resolve().parents[1] / "config"

#: The small, high-stakes stores get a content hash as well as a stat; the rest
#: of config/ is ~265 MB of cached fields and BH exports, so those are pinned by
#: (mtime_ns, size) only.  A writer that rewrites a file byte-identically still
#: moves its mtime, so nothing escapes through the cheap half.
_HASHED = ("motor_config.yaml", "motor_presets.json", "motor_catalog.json",
           "sweep_config.json", "saved_simulations.json", "user_motors.json",
           ".panel_settings.json", ".family_context.json", ".last_transient.json")


def _snapshot(root: Path) -> dict:
    """{path: (mtime_ns, size, sha1-or-None)} for every file under `root`."""
    out: dict = {}
    if not root.exists():
        return out
    for p in root.rglob("*"):
        try:
            if not p.is_file():
                continue
            st = p.stat()
            h = (hashlib.sha1(p.read_bytes()).hexdigest()
                 if p.name in _HASHED else None)
            out[str(p)] = (st.st_mtime_ns, st.st_size, h)
        except OSError:                     # vanished mid-walk → record the fact
            out[str(p)] = None
    return out


class _WriteTrap:
    """Refuse — and remember — every attempt to write inside the repo's config/.

    A snapshot alone can only say "a byte moved"; it cannot name the caller, and
    a background solver run editing ``config/`` from another process would make
    it lie in both directions.  This intercepts the write itself, so a failure
    names the exact call and the offending write never reaches the disk.
    """

    def __init__(self) -> None:
        self.hits: list = []

    def _check(self, path, how: str) -> None:
        try:
            p = Path(os.fspath(path)).resolve()
        except (TypeError, ValueError, OSError):
            return
        try:
            p.relative_to(REPO_CONFIG)
        except ValueError:
            return
        self.hits.append("%s %s" % (how, p))
        raise AssertionError(
            "%s %s — a writer bypassed MOTOR_AI_SIM_CONFIG and reached the "
            "folder holding the machine the user has loaded" % (how, p))

    def __enter__(self):
        import builtins
        import io as _io
        self._saved: list = []

        def _wrap_open(mod, name):
            orig = getattr(mod, name)

            def guarded(file, mode="r", *a, **kw):
                if any(c in str(mode) for c in "wax+"):
                    self._check(file, "open(%r)" % (mode,))
                return orig(file, mode, *a, **kw)

            self._saved.append((mod, name, orig))
            setattr(mod, name, guarded)

        _wrap_open(builtins, "open")
        _wrap_open(_io, "open")             # pathlib.Path.open goes through this

        for _name in ("replace", "rename", "remove", "unlink", "mkdir",
                      "makedirs", "rmdir"):
            _orig = getattr(os, _name)

            def guarded(*a, _orig=_orig, _n=_name, **kw):
                if a:
                    self._check(a[0], _n)
                    if _n in ("replace", "rename") and len(a) > 1:
                        self._check(a[1], _n)
                return _orig(*a, **kw)

            self._saved.append((os, _name, _orig))
            setattr(os, _name, guarded)
        return self

    def __exit__(self, *exc):
        for mod, name, orig in self._saved:
            setattr(mod, name, orig)
        return False


@pytest.fixture
def untouched_repo_config():
    """Fail unless the repo's config/ is byte-for-byte what it was, and unless
    every write that tried to land there was caught in the act."""
    before = _snapshot(REPO_CONFIG)
    trap = _WriteTrap()
    with trap:
        yield trap
    after = _snapshot(REPO_CONFIG)
    changed = sorted(k for k in set(before) | set(after)
                     if before.get(k) != after.get(k))
    assert not trap.hits, (
        "a writer reached the user's config folder:\n  " + "\n  ".join(trap.hits))
    assert not changed, (
        "files under the repo's config/ changed while a redirected writer ran:\n"
        "  " + "\n  ".join(changed) + "\n"
        "The write trap saw nothing, so this was NOT one of the writers under "
        "test — check whether the backend or a background solver run was "
        "editing config/ during the suite, and rerun with the app idle.")


def _sandbox_config_dir() -> Path:
    from motor_ai_sim.config import DEFAULT_CONFIG_PATH
    return Path(str(DEFAULT_CONFIG_PATH)).parent


def test_the_redirect_is_actually_in_force():
    """Guard for the guards: if conftest ever stops redirecting, every assertion
    below would pass by describing the user's own folder."""
    assert _sandbox_config_dir().resolve() != REPO_CONFIG, (
        "MOTOR_AI_SIM_CONFIG is pointing at the repo's own config/ — the whole "
        "suite is running against the machine the user has loaded")


def _every_store_path():
    """(label, path) for every store this project keeps beside the config.

    Each is either written directly, or is the READER whose writer is redirected
    — a pair that disagrees is the same bug one direction over (presets.py's
    ``.last_transient.json`` reader stamped a saved card with the torque of
    whatever machine the OTHER process had just solved).
    """
    from motor_ai_sim import api as api_mod, audit as audit_mod
    from motor_ai_sim import duty_results, sessions, users
    from motor_ai_sim.optimization import doe, surrogate
    from motor_ai_sim.routes import (catalog, family, fusion, geometry,
                                     my_motors, optimization, panel_settings,
                                     presets, saved_sims, static3d, sweep_config)

    return [
        ("api._CONFIG_PATH", api_mod._CONFIG_PATH),
        ("audit._HISTORY_DIR", audit_mod._HISTORY_DIR),
        ("routes.catalog._CATALOG_PATH", catalog._CATALOG_PATH),
        ("routes.presets._CONFIG_PATH", presets._CONFIG_PATH),
        ("routes.presets._CATALOG_PATH", presets._CATALOG_PATH),
        ("routes.presets._PRESETS_PATH", presets._PRESETS_PATH),
        ("routes.saved_sims._STORE", saved_sims._STORE),
        ("routes.sweep_config._STORE", sweep_config._STORE),
        ("routes.static3d._CACHE_DIR", static3d._CACHE_DIR),
        ("routes.optimization._scan_store_path()", optimization._scan_store_path()),
        ("routes.optimization._eval_cache_path()", optimization._eval_cache_path()),
        ("routes.optimization._eval_rate_path()", optimization._eval_rate_path()),
        ("routes.optimization._descent_store_path()", optimization._descent_store_path()),
        ("routes.optimization._dataset_path()", optimization._dataset_path()),
        ("optimization.doe.doe_path()", doe.doe_path()),
        ("optimization.surrogate.dataset_path()", surrogate.dataset_path()),
        ("routes.panel_settings._store_path()", panel_settings._store_path()),
        ("routes.family._CTX_FILE", family._CTX_FILE),
        ("routes.family._DIES_DIR", family._DIES_DIR),
        ("routes.geometry._MESH_DISK_DIR", geometry._MESH_DISK_DIR),
        ("routes.fusion._MAP_FILE", fusion._MAP_FILE),
        ("routes.my_motors._STORE", my_motors._STORE),
        ("duty_results.store_path()", duty_results.store_path()),
        ("sessions._SESSIONS_FILE", sessions._SESSIONS_FILE),
        ("users._USERS_FILE", users._USERS_FILE),
    ]


@pytest.mark.parametrize("label", [lbl for lbl, _ in _every_store_path()])
def test_no_store_resolves_inside_the_users_config_folder(label):
    """Static half: not one of them may even NAME a path under the repo's own
    config/.  That is what a hardcoded ``Path(__file__)…/config`` looks like from
    the outside, and catching it at import beats catching it after the write."""
    path = dict(_every_store_path())[label]
    p = Path(str(path)).resolve()
    assert not str(p).startswith(str(REPO_CONFIG) + os.sep) and p != REPO_CONFIG, (
        "%s resolves to %s, inside the user's own config folder — "
        "MOTOR_AI_SIM_CONFIG does not move it" % (label, p))


def test_patch_simulation_config_writes_the_redirected_file(untouched_repo_config):
    """THE REGRESSION, write side.

    ``PATCH /api/simulation/config`` built its path from ``Path(__file__)`` and
    so rewrote ``simulation:`` — current, rpm, load angle, drive — in the real
    ``config/motor_config.yaml``, whatever the redirect said."""
    from fastapi.testclient import TestClient
    from motor_ai_sim.api import app
    from motor_ai_sim.config import DEFAULT_CONFIG_PATH, clear_config_cache

    live = Path(str(DEFAULT_CONFIG_PATH))
    keep = live.read_bytes()
    try:
        before = yaml.safe_load(live.read_text(encoding="utf-8"))["simulation"]
        want = float(before.get("rpm", 1000)) + 7.0
        r = TestClient(app).patch("/api/simulation/config", json={"rpm": want})
        assert r.status_code == 200, r.text
        after = yaml.safe_load(live.read_text(encoding="utf-8"))["simulation"]
        assert float(after["rpm"]) == pytest.approx(want), (
            "the PATCH did not land in the REDIRECTED config — it went "
            "somewhere else, which is the bug")
    finally:
        live.write_bytes(keep)
        clear_config_cache()


def test_the_api_config_writers_stay_inside_the_sandbox(untouched_repo_config):
    """``api._CONFIG_PATH``'s handlers: materials, part states, mesh config.

    All three read-modify-write the motor config, and all three did it through a
    path bound at import to the repo's own file."""
    from fastapi.testclient import TestClient
    from motor_ai_sim.api import app
    from motor_ai_sim.config import DEFAULT_CONFIG_PATH, clear_config_cache

    live = Path(str(DEFAULT_CONFIG_PATH))
    keep = live.read_bytes()
    cfg = yaml.safe_load(live.read_text(encoding="utf-8"))
    client = TestClient(app)
    try:
        # Re-assert what is already there: a no-op edit still exercises the path.
        mat = (cfg.get("materials") or {}).get("stator_core")
        if mat:
            assert client.patch("/api/materials",
                                json={"part": "stator_core",
                                      "material": str(mat)}).status_code == 200
        state = (cfg.get("parts") or {}).get("shaft") or "included"
        assert client.patch("/api/parts",
                            json={"part": "shaft",
                                  "state": str(state)}).status_code == 200
        n_rad = int((cfg.get("mesh") or {}).get("n_radial") or 10)
        assert client.patch("/api/mesh/config",
                            json={"n_radial": n_rad}).status_code == 200
    finally:
        live.write_bytes(keep)
        clear_config_cache()


def test_the_sidecar_stores_stay_inside_the_sandbox(untouched_repo_config):
    """The small JSON stores that were pinned to the repo's config/: the
    saved-simulation library, the sweep panel, and the presets history."""
    from motor_ai_sim import audit as audit_mod
    from motor_ai_sim.routes import saved_sims, sweep_config

    saved_sims._save_all(saved_sims._load_all())
    sweep_config._save_all(sweep_config._load_all())

    # snapshot_presets copies the presets file into `.presets_history/` and
    # evicts everything past _KEEP — on the real folder that is a test run
    # throwing away the user's own recovery copies.
    audit_mod.snapshot_presets(Path(str(saved_sims._STORE)), note="redirect_test")


def test_the_sweep_and_eval_stores_stay_inside_the_sandbox(untouched_repo_config):
    """``routes/optimization``'s four persisters — the restored sweep chart, the
    eval cache, the eval-rate history and the descent snapshot.  Each one
    overwrote the record belonging to the machine the user has open."""
    from motor_ai_sim.routes import optimization as opt

    opt._save_last_scan({"variables": [], "points": []})
    opt._store_eval("redirect-test-key", {"T_em_Nm": 1.0})
    opt._save_eval_rate()
    opt._save_descent_state()
