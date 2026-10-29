"""Round-2 regressions for the independent speed-up review findings."""
from concurrent.futures import ThreadPoolExecutor
import ast
import inspect
from pathlib import Path
import subprocess

import numpy as np
import pytest

from motor_ai_sim.simulation import fem_solver_2d as solver


def _legacy_cache():
    return {
        "meta": {"nspp": 24, "npd": 1.0, "conn": "np2", "temp": 80.0,
                 "mscale": 1.0, "ts": "bdf2"},
        "doflocs": np.array([[0.0, 0.0], [1.0, 1.0]]),
        "A": np.array([1.0, 2.0]), "A2": np.array([0.5, 1.5]),
        "Ued": np.array([0.0]), "solid": np.array([2.0]),
        "I": 10.0, "rpm": 1000.0, "gam": 0.0, "geo_fp": "g",
        "nspp_req": 24, "nsect": 1,
    }


def test_safe_disk_cache_is_legacy_readable_when_switch_turns_off(tmp_path, monkeypatch):
    monkeypatch.setattr(solver, "_warm_cache_path", lambda: tmp_path / "warm.npz")
    cache = _legacy_cache()
    cache.update({"safe_schema": solver._SAFE_WARM_CACHE_SCHEMA,
                  "safe_identity": "safe-id", "eddy_complete": True,
                  "dof_region": np.array([0, 1]), "edcon_fp": "circuit",
                  "Ib_prev": (np.array([0.0]), np.array([0.0])),
                  "Ib_prev2": (np.array([0.0]), np.array([0.0]))})
    monkeypatch.setenv("SB_SAFE_WARM_CACHE", "1")
    solver._SB_WARM_CACHE["last"] = None
    solver._warm_cache_store(cache)
    solver._SB_WARM_CACHE["last"] = None
    monkeypatch.delenv("SB_SAFE_WARM_CACHE", raising=False)
    loaded = solver._warm_cache_load(disk_only=True)
    assert loaded["meta"] == _legacy_cache()["meta"]
    assert "safe_identity" not in loaded["meta"]
    assert solver._warm_seed_accept(loaded, loaded["meta"], 10.0, 1000.0,
                                    0.0, n_sectors=1)[0]


def test_safe_memory_cache_is_legacy_readable_when_switch_turns_off(monkeypatch):
    cache = _legacy_cache()
    cache["meta"]["safe_identity"] = "safe-id"
    cache.update({"safe_schema": solver._SAFE_WARM_CACHE_SCHEMA,
                  "safe_identity": "safe-id", "meta": dict(cache["meta"])})
    monkeypatch.delenv("SB_SAFE_WARM_CACHE", raising=False)
    solver._SB_WARM_CACHE["last"] = cache
    loaded = solver._warm_cache_load()
    assert loaded["meta"] == _legacy_cache()["meta"]
    assert cache["meta"]["safe_identity"] == "safe-id"
    assert loaded is not cache
    assert solver._warm_seed_accept(loaded, loaded["meta"], 10.0, 1000.0,
                                    0.0, n_sectors=1)[0]


def test_same_process_publications_use_unique_exact_temp_files(tmp_path, monkeypatch):
    monkeypatch.setattr(solver, "_warm_cache_path", lambda: tmp_path / "warm.npz")
    monkeypatch.delenv("SB_SAFE_WARM_CACHE", raising=False)
    caches = []
    for value in range(8):
        cache = _legacy_cache()
        cache["A"] = np.array([float(value), float(value + 1)])
        caches.append(cache)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(solver._warm_cache_store, caches))
    solver._SB_WARM_CACHE["last"] = None
    loaded = solver._warm_cache_load(disk_only=True)
    assert loaded is not None and np.all(np.isfinite(loaded["A"]))
    assert not list(tmp_path.glob("*.tmp*"))


def test_resolved_circuit_digest_rejects_path_bonding_mirror_and_iunit(monkeypatch):
    row = {"key": "cu", "tag": 10, "tags": [10], "signs": [1.0],
           "phase": "A", "slot": 0, "coil": 0, "Iunit": 2.0, "S": 3.0}
    path = {"paths": [[(0, 1.0)]], "group": [0], "rep": [0], "n_group": 1}
    base = solver._safe_circuit_identity([row], path,
                                         {"strand_bonding": "series", "wire_split": 2})
    changed = []
    changed.append(solver._safe_circuit_identity([dict(row, Iunit=2.1)], path,
                                                  {"strand_bonding": "series", "wire_split": 2}))
    changed.append(solver._safe_circuit_identity([row], None,
                                                  {"strand_bonding": "transposed", "wire_split": 2}))
    changed.append(solver._safe_circuit_identity([row], dict(path, paths=[[(0, -1.0)]]),
                                                  {"strand_bonding": "series", "wire_split": 2}))
    monkeypatch.setenv("SB_STRAND_MIRROR", "1")
    changed.append(solver._safe_circuit_identity([row], path,
                                                  {"strand_bonding": "series", "wire_split": 2}))
    assert all(value != base for value in changed)
    monkeypatch.delenv("SB_STRAND_MIRROR", raising=False)
    monkeypatch.delenv("SB_EDDY_LOSS_AT", raising=False)
    default_mid = solver._safe_circuit_identity([row], path, {"strand_bonding": "series"})
    monkeypatch.setenv("SB_EDDY_LOSS_AT", "step")
    explicit_step = solver._safe_circuit_identity([row], path, {"strand_bonding": "series"})
    assert default_mid != explicit_step
    source = inspect.getsource(solver._fem_transient_sliding_band_once)
    assert source.index('if _wc.get("edcon_fp") != _safe_edcon_fp') < \
           source.index('_br_glob[_mag_idx] = _br_candidate',
                        source.index('if _wc.get("edcon_fp") != _safe_edcon_fp'))


def test_head_differential_off_stash_publishes_and_consumes_a2(tmp_path, monkeypatch):
    path = "src/motor_ai_sim/simulation/fem_solver_2d.py"
    root = Path.cwd()
    trees = {
        "head": ast.parse(subprocess.check_output(["git", "show", "HEAD:" + path],
                                                   cwd=root).decode()),
        "candidate": ast.parse(inspect.getsource(solver)),
    }
    out = {}
    for label, tree in trees.items():
        node = next(n for n in ast.walk(tree) if isinstance(n, ast.If)
                    and any(isinstance(x, ast.Assign) and any(
                        isinstance(t, ast.Name) and t.id == "_wc_A2" for t in x.targets)
                            for x in n.body))
        env = {"_eddy_bdf2": True, "_vdrive": False, "k": 8, "n_total": 12,
               "_warm_cache_disabled": lambda: False,
               "_safe_warm_cache_enabled": lambda: False,
               "A2": np.array([1.0, 2.0]), "_wc_A2": None, "np": np}
        exec(compile(ast.Module([node], type_ignores=[]), path, "exec"), env)
        out[label] = env["_wc_A2"]
    np.testing.assert_array_equal(out["candidate"], out["head"])
    monkeypatch.delenv("SB_SAFE_WARM_CACHE", raising=False)
    monkeypatch.setattr(solver, "_warm_cache_path", lambda: tmp_path / "warm.npz")
    cache = _legacy_cache()
    cache["A2"] = out["candidate"]
    solver._SB_WARM_CACHE["last"] = None
    solver._warm_cache_store(cache)
    solver._SB_WARM_CACHE["last"] = None
    published = solver._warm_cache_load(disk_only=True)
    from scipy.spatial import cKDTree
    target = np.array([[0.0, 0.0], [1.0, 1.0]])
    widx = cKDTree(published["doflocs"]).query(target, k=1)[1]
    consumed_a2 = np.asarray(published["A2"], float)[widx]
    np.testing.assert_array_equal(consumed_a2, out["head"])


def test_bundle_validation_precedes_pending_or_live_field_installation():
    source = inspect.getsource(solver._fem_transient_sliding_band_once)
    start = source.index("if _safe_warm_cache_enabled():", source.index("_wc = _wc_seed"))
    end = source.index("else:", source.index("log.info(\"P2 safe warm cache: image-aware", start))
    block = source[start:end]
    assert block.index("_safe_validate_current_history") < block.index("_warm_seed_pending =")
    assert "_rollback_safe_br_seed" in source
    for cleared in ("_safe_cold_seed_bundle", "_Ib_prev = _cold", "_A2_prev = _cold",
                    "_warm_seed_pending = None", "_Ib_seed_restore = None"):
        assert cleared in source


def _execute_actual_rejected_overlay(static_info, ib_restore):
    tree = ast.parse(inspect.getsource(solver._fem_transient_sliding_band_once))
    rollback = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef)
                    and n.name == "_rollback_safe_br_seed")
    # Execute the real nested function as a module function.  Its nonlocals are
    # the synthetic integration state below, represented as globals here.
    names = []
    body = []
    for node in rollback.body:
        if isinstance(node, ast.Nonlocal):
            names.extend(node.names)
        else:
            body.append(node)
    rollback.body = [ast.Global(names=names)] + body
    overlay = next(n for n in ast.walk(tree) if isinstance(n, ast.If)
                   and ast.unparse(n.test) ==
                   "_safe_warm_cache_enabled() and _warm_seed_pending is not None")
    class Driver:
        f_mag = "seed-source"
    env = dict(np=np, log=solver.log, _safe_warm_cache_enabled=lambda: True,
               _safe_validate_current_history=solver._safe_validate_current_history,
               _safe_cold_seed_bundle=solver._safe_cold_seed_bundle,
               _safe_restore_br_bundle=lambda original, current, **kw:
                   current.__setitem__(slice(None), original),
               asm=lambda *a, **k: "cold-source", _msrc=object(),
               b2=object(), b2_0=type("B", (), {"interpolate": lambda s, x: x})(),
               _drv=Driver(), _br_seed_original=np.ones(2),
               _br_glob=np.array([0.5, 0.6]), _dm_seeded=True,
               _dm_seed_from={"I": 1}, _dm_inherited_history={"bad": True},
               _mx_all=np.array([0., 0., 7., 8.]), _my_all=np.array([0., 0., 9., 10.]),
               nst=2, _Mx_glob=np.ones(2), _My_glob=np.ones(2), magnet_scale=1.,
               f_mag2="seed-source", _warm_ref=np.ones(2), _warm_ref_grp={"x": 1},
               _warm_seed_pending=(np.arange(4.), np.ones(4, bool),
                                   np.arange(4.) + 1., np.ones(4, bool)),
               _Ib_seed_restore=ib_restore, _safe_br_pending=(object(),),
               _warm_seeded=True, _Aed_prev=np.full(4, 8.),
               _Aed_prev2=np.full(4, 7.), _hed_prev=0.1,
               _Ib_prev=np.ones(2), _Ib_prev2=np.ones(2), _Ued=np.ones(2),
               _A2_prev=np.ones(4), _nu_conv2=np.ones(2), nu_all2=np.ones(2),
               nu_base2=np.array([3., 4.]), N2=4, _ed_con=[{}, {}],
               _eddy_bdf2=True, _static_seed_info=static_info, dt=.1)
    exec(compile(ast.fix_missing_locations(ast.Module([rollback, overlay],
                                                       type_ignores=[])),
                 "candidate-overlay", "exec"), env)
    return env


@pytest.mark.parametrize("static_info, ib_restore", [
    ({"converged": True}, ((np.ones(1), np.ones(1)),
                           (np.ones(1), np.ones(1)))),  # late wrong conductor count
    ({"converged": False}, ((np.ones(2), np.ones(2)),
                            (np.ones(2), np.ones(2)))), # failed static fill
    (None, ((np.ones(2), np.ones(2)),
            (np.ones(2), np.ones(2)))),                # disabled static fill
])
def test_actual_overlay_rejection_rolls_back_complete_bundle(static_info, ib_restore):
    env = _execute_actual_rejected_overlay(static_info, ib_restore)
    np.testing.assert_array_equal(env["_br_glob"], np.ones(2))
    np.testing.assert_array_equal(env["_Aed_prev"], np.zeros(4))
    np.testing.assert_array_equal(env["_Ued"], np.zeros(2))
    np.testing.assert_array_equal(env["nu_all2"], [3., 4.])
    assert env["_drv"].f_mag == "cold-source"
    assert env["_warm_seed_pending"] is None
    assert env["_Ib_seed_restore"] is None
    assert env["_safe_br_pending"] is None
    assert not env["_warm_seeded"]
    for key in ("_Aed_prev2", "_hed_prev", "_Ib_prev", "_Ib_prev2",
                "_A2_prev", "_nu_conv2", "_dm_seed_from",
                "_dm_inherited_history", "_warm_ref", "_static_seed_info"):
        assert env[key] is None


def test_actual_seeded_newton_retry_resets_start_before_continue():
    tree = ast.parse(inspect.getsource(solver._fem_transient_sliding_band_once))
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.If)
                and "not _eok and k < 0" in ast.unparse(n.test))
    env = dict(_eok=False, k=-1, _warm_seeded=True,
               _seed_cold_retry_done=False, log=solver.log, _nit=3, _res=.1,
               _rollback_safe_br_seed=lambda reason: None,
               _safe_warm_cache_enabled=lambda: True, _dm_freeze_warm=False,
               _warm_ref=np.ones(1), _warm_ref_grp={"x": 1}, N2=3,
               np=np, _Aed_prev=np.ones(3), _Aed_prev2=np.ones(3),
               _hed_prev=.1, _Ib_prev=np.ones(1), _Ib_prev2=np.ones(1),
               _Ued=np.ones(1), _ed_con=[{}], _A_start=np.full(3, 9.),
               _nu_start=np.full(2, 8.), nu_base2=np.array([3., 4.]))
    # Replace the terminal continue, which is only valid inside the solver loop.
    body = [n for n in node.body if not isinstance(n, ast.Continue)]
    exec(compile(ast.fix_missing_locations(ast.Module(body, type_ignores=[])),
                 "retry-boundary", "exec"), env)
    np.testing.assert_array_equal(env["_A_start"], np.zeros(3))
    np.testing.assert_array_equal(env["_nu_start"], [3., 4.])


def test_off_publication_keeps_legacy_a2_stash_and_demag_shape():
    source = inspect.getsource(solver._fem_transient_sliding_band_once)
    assert "if (_eddy_bdf2 and not _vdrive" in source
    assert '**({"inherited_prepass_periods"' in source
    assert 'if _safe_warm_cache_enabled() else {})' in source
    assert '"magnet (sweep mode)"' in source


def test_head_differential_off_demag_image_payload_is_identical():
    path = "src/motor_ai_sim/simulation/fem_solver_2d.py"
    root = Path.cwd()
    trees = [ast.parse(subprocess.check_output(["git", "show", "HEAD:" + path],
                                               cwd=root).decode()),
             ast.parse(inspect.getsource(solver))]
    records = []
    for tree in trees:
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "_dm_image_check")
        env = {"_dm_image": lambda: (None, 7, {}), "_dm_seeded": False,
               "_dm_inherited_history": None, "Dict": dict, "Any": object,
               "_safe_warm_cache_enabled": lambda: False}
        exec(compile(ast.Module([fn], type_ignores=[]), path, "exec"), env)
        records.append(env["_dm_image_check"](7)[0])
    assert records[1] == records[0]
    assert "inherited_pre_pass_periods" not in records[1]


def test_head_differential_legacy_br_mutation_and_log_failure_semantics():
    path = "src/motor_ai_sim/simulation/fem_solver_2d.py"
    trees = [ast.parse(subprocess.check_output(["git", "show", "HEAD:" + path],
                                               cwd=Path.cwd()).decode()),
             ast.parse(inspect.getsource(solver))]
    states = []
    class FailingLog:
        @staticmethod
        def info(*args, **kwargs):
            raise RuntimeError("injected log failure")
    for index, tree in enumerate(trees):
        candidates = [n for n in ast.walk(tree) if isinstance(n, ast.If)
                      and "_proj is not None" in ast.unparse(n.test)
                      and any(isinstance(x, ast.Assign) and any(
                          isinstance(t, ast.Name) and t.id == "_dm_seeded"
                          for t in x.targets) for x in n.body)]
        node = candidates[0]
        env = dict(_proj=(np.array([0.8, 0.9]), 2),
                   _safe_warm_cache_enabled=lambda: False,
                   _br_glob=np.ones(2), _mag_idx=np.array([0, 1]),
                   magnet_scale=1.0, np=np, _dm_seeded=False,
                   _dm_seed_from=None, _wc_seed={"I": 2., "gam": 3., "rpm": 4.,
                                                  "geo_fp": "g"}, _geo_fp="g",
                   log=FailingLog())
        with pytest.raises(RuntimeError, match="injected log failure"):
            exec(compile(ast.fix_missing_locations(ast.Module([node], type_ignores=[])),
                         path, "exec"), env)
        states.append((env["_br_glob"].copy(), env["_dm_seeded"],
                       dict(env["_dm_seed_from"])))
    np.testing.assert_array_equal(states[1][0], states[0][0])
    assert states[1][1:] == states[0][1:]


def test_probe_failure_and_final_ring_verdict_are_independent():
    source = inspect.getsource(solver._solve_with_gap_probes)
    probe = source.index("if probe_failure:")
    final = source.index("if final_eps is None", probe)
    assert "elif final_eps" not in source[probe:final + 30]


@pytest.mark.parametrize("final_eps, expected", [
    (0.2, "full transient ring disagreement"),
    (None, "full transient ring check unavailable"),
])
def test_probe_failure_reports_final_ring_verdict_too(monkeypatch, final_eps, expected):
    from tests.test_gap_probe import _enable_probe, _result

    def fake(**kw):
        if solver._GAP_PROBE_CTX.get():
            raise RuntimeError("injected probe failure")
        return _result(kw, kw["gap_layers"], final_eps)

    monkeypatch.setattr(solver, "fem_transient_sliding_band", fake)
    _enable_probe(monkeypatch)
    out = solver._solve_with_gap_refinement(
        {"gap_layers": 1.0, "sampling_purpose": "standard"}, gap_refine=True)
    assert "probe failed" in out["gap_layers_note"]
    assert expected in out["gap_layers_note"]
