"""Round 3 regressions for safe-cache ownership, provenance, and OFF isolation."""
from __future__ import annotations

import os
import ast
import copy
from pathlib import Path
import subprocess
from types import SimpleNamespace

import numpy as np
import pytest

from tests.test_speedup_cache_publication import _cache, _load_candidate_solver, _set_cache_path
from tests.test_safe_warm_cache import _complete_cache


@pytest.fixture
def solver():
    candidate_solver = (Path(__file__).resolve().parents[1] / "src" /
                        "motor_ai_sim" / "simulation" / "fem_solver_2d.py")
    return _load_candidate_solver(str(candidate_solver))


@pytest.mark.parametrize("safe_enabled", (False, True))
def test_writer_relinquishes_descriptor_after_fdopen_even_when_save_fails(
        tmp_path, monkeypatch, solver, safe_enabled):
    if safe_enabled:
        monkeypatch.setenv("SB_SAFE_WARM_CACHE", "1")
    else:
        monkeypatch.delenv("SB_SAFE_WARM_CACHE", raising=False)
    _set_cache_path(solver, tmp_path / "warm.npz", monkeypatch)
    original_fdopen = solver._os_sb.fdopen
    unrelated = tmp_path / "unrelated.bin"
    reused = {}

    class ReuseOnClose:
        def __init__(self, wrapped):
            self.wrapped = wrapped
        def __getattr__(self, name):
            return getattr(self.wrapped, name)
        def __enter__(self):
            return self
        def __exit__(self, typ, value, tb):
            old_fd = self.wrapped.fileno()
            self.wrapped.close()
            reused["old_fd"] = old_fd
            reused["fd"] = os.open(unrelated, os.O_CREAT | os.O_RDWR)

    monkeypatch.setattr(solver._os_sb, "fdopen",
                        lambda fd, mode: ReuseOnClose(original_fdopen(fd, mode)))
    monkeypatch.setattr(np, "savez",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("serialize")))
    solver._warm_cache_store(_cache(1.0))
    try:
        assert reused["fd"] == reused["old_fd"]
        os.fstat(reused["fd"])
    finally:
        try:
            os.close(reused["fd"])
        except OSError:
            pass
    assert not list(tmp_path.glob("*.tmp*"))


def test_writer_fdopen_failure_closes_untransferred_descriptor(
        tmp_path, monkeypatch, solver):
    monkeypatch.setenv("SB_SAFE_WARM_CACHE", "1")
    _set_cache_path(solver, tmp_path / "warm.npz", monkeypatch)
    captured = {}
    original_mkstemp = __import__("tempfile").mkstemp
    def remember(*args, **kwargs):
        fd, name = original_mkstemp(*args, **kwargs)
        captured["fd"] = fd
        return fd, name
    monkeypatch.setattr(__import__("tempfile"), "mkstemp", remember)
    monkeypatch.setattr(solver._os_sb, "fdopen",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("fdopen")))
    solver._warm_cache_store(_cache(2.0))
    with pytest.raises(OSError):
        os.fstat(captured["fd"])
    assert not list(tmp_path.glob("*.tmp*"))


@pytest.mark.parametrize("failure_stage", ("flush", "replace"))
def test_writer_post_transfer_failures_do_not_close_reused_descriptor(
        tmp_path, monkeypatch, solver, failure_stage):
    monkeypatch.setenv("SB_SAFE_WARM_CACHE", "1")
    _set_cache_path(solver, tmp_path / "warm.npz", monkeypatch)
    original_fdopen = solver._os_sb.fdopen
    unrelated = tmp_path / (failure_stage + ".bin")
    reused = {}
    class Wrapper:
        def __init__(self, wrapped): self.wrapped = wrapped
        def __getattr__(self, name): return getattr(self.wrapped, name)
        def __enter__(self): return self
        def flush(self):
            if failure_stage == "flush":
                raise OSError("injected flush failure")
            return self.wrapped.flush()
        def __exit__(self, typ, value, tb):
            old_fd = self.wrapped.fileno()
            self.wrapped.close()
            reused["old_fd"] = old_fd
            reused["fd"] = os.open(unrelated, os.O_CREAT | os.O_RDWR)
    monkeypatch.setattr(solver._os_sb, "fdopen",
                        lambda fd, mode: Wrapper(original_fdopen(fd, mode)))
    if failure_stage == "replace":
        monkeypatch.setattr(solver._os_sb, "replace",
                            lambda *a, **k: (_ for _ in ()).throw(OSError("replace")))
    solver._warm_cache_store(_cache(2.5))
    try:
        assert reused["fd"] == reused["old_fd"]
        os.fstat(reused["fd"])
    finally:
        try:
            os.close(reused["fd"])
        except OSError:
            pass
    assert not list(tmp_path.glob("*.tmp*"))


SAFE_ONLY = (
    "safe_schema", "safe_identity", "dof_region", "edcon_fp",
    "eddy_complete", "demag_complete", "demag_history_json", "br_mat",
    "Ib_prev_net", "Ib_prev_imposed", "Ib_prev2_net", "Ib_prev2_imposed",
)


@pytest.mark.parametrize("hostile_key", SAFE_ONLY)
def test_off_never_indexes_any_safe_only_archive_member(
        tmp_path, monkeypatch, solver, hostile_key):
    monkeypatch.delenv("SB_SAFE_WARM_CACHE", raising=False)
    destination = tmp_path / "legacy.npz"
    payload = _cache(3.0)
    np.savez(destination, doflocs=payload["doflocs"], A=payload["A"],
             Ued=payload["Ued"], solid=payload["solid"], nspp=8, npd=1.03,
             conn="np1", temp=23.0, mscale=1.0, I=12.0, rpm=13.0,
             gam=14.0, geo_fp="g", nspp_req=8, nsect=2, A2=payload["A2"],
             **{hostile_key: np.array([object()], dtype=object)})
    monkeypatch.setattr(solver, "_warm_cache_path", lambda: destination)
    solver._SB_WARM_CACHE["last"] = None
    real_load = np.load
    accessed = []
    class Spy:
        def __init__(self, wrapped): self.wrapped = wrapped
        @property
        def files(self): return self.wrapped.files
        def __getitem__(self, key):
            accessed.append(key)
            if key in SAFE_ONLY:
                raise AssertionError("OFF indexed safe-only member %s" % key)
            return self.wrapped[key]
        def __enter__(self): return self
        def __exit__(self, *args): self.wrapped.close()
    monkeypatch.setattr(np, "load", lambda *a, **k: Spy(real_load(*a, **k)))
    loaded = solver._warm_cache_load(disk_only=True)
    assert loaded is not None
    assert hostile_key not in accessed


def test_off_writer_never_reads_safe_only_cache_fields(tmp_path, monkeypatch, solver):
    monkeypatch.delenv("SB_SAFE_WARM_CACHE", raising=False)
    _set_cache_path(solver, tmp_path / "legacy.npz", monkeypatch)
    safe_names = set(SAFE_ONLY) | {"br_mat", "demag_history", "Ib_prev", "Ib_prev2"}
    class Audited(dict):
        def get(self, key, default=None):
            if key in safe_names:
                raise AssertionError("OFF writer read safe-only field %s" % key)
            return super().get(key, default)
        def __getitem__(self, key):
            if key in safe_names:
                raise AssertionError("OFF writer indexed safe-only field %s" % key)
            return super().__getitem__(key)
    legacy = _cache(3.5)
    legacy.pop("safe_schema")
    solver._warm_cache_store(Audited(legacy))
    assert (tmp_path / "legacy.npz").is_file()


class _Mesh:
    def __init__(self, points, triangles):
        self.p = np.asarray(points, float).T
        self.t = np.asarray(triangles, int).T


def test_resolved_boundary_digest_tracks_regions_but_ignores_internal_triangulation(solver):
    square = [(0, 0), (1, 0), (1, 1), (0, 1)]
    diagonal_a = _Mesh(square, [(0, 1, 2), (0, 2, 3)])
    diagonal_b = _Mesh(square, [(0, 1, 3), (1, 2, 3)])
    base = solver._safe_physical_boundary_digest(diagonal_a, [10, 10], gap_regions=(1, 2))
    assert solver._safe_physical_boundary_digest(diagonal_b, [10, 10], gap_regions=(1, 2)) == base
    assert solver._safe_physical_boundary_digest(diagonal_a, [10, 11], gap_regions=(1, 2)) != base
    # Production passes the mesher's actual tag -> element-index mapping.
    assert solver._safe_physical_boundary_digest(
        diagonal_a, {10: np.array([0, 1])}, gap_regions=(1, 2)) == base
    changed_map = solver._safe_physical_boundary_digest(
        diagonal_a, {10: np.array([0]), 11: np.array([1])}, gap_regions=(1, 2))
    assert changed_map != base
    with pytest.raises(ValueError, match="incomplete"):
        solver._safe_physical_boundary_digest(
            diagonal_a, {10: np.array([0])}, gap_regions=(1, 2))
    # The implementation explicitly omits only interfaces whose two resolved
    # labels are both in the declared gap/band set.
    source = Path(solver.__file__).read_text(encoding="utf-8")
    assert "set(pair) <= gap_regions" in source


def test_production_safe_key_binds_fillet_parameters_and_resolved_boundaries(solver):
    source = Path(solver.__file__).read_text(encoding="utf-8")
    key_start = source.index("_safe_identity = _safe_identity_digest({")
    key_end = source.index("\n            })", key_start)
    expression = source[key_start:key_end]
    for required in ("stator_fillet_mm", "outer_air_factor",
                     "resolved_motor_parameters", "_safe_physical_boundary_digest",
                     'half["s"]["cells"]', 'half["r"]["cells"]'):
        assert required in expression
    accept = source[source.index("_warm_seed_accept(", key_end):]
    assert "need_br=bool(demag)" in accept[:1000]


def test_real_accept_gate_rejects_changed_geometry_identity_with_br_unchanged(
        monkeypatch, solver):
    monkeypatch.setenv("SB_SAFE_WARM_CACHE", "1")
    meta, cache = _complete_cache()
    assert solver._warm_seed_accept(cache, meta, 60.0, 15000.0, 4.0,
                                    n_sectors=2, need_br=True) == (True, "")
    changed = dict(cache, safe_identity=solver._safe_identity_digest({
        "stator_fillet_mm": 0.4, "nonconducting_iron_tag": 99,
        "br_unchanged": np.asarray(cache["br_val"]).tolist()}))
    ok, reason = solver._warm_seed_accept(
        changed, meta, 60.0, 15000.0, 4.0, n_sectors=2, need_br=True)
    assert not ok and "identity" in reason


def test_actual_production_identity_and_accept_gate_track_resolved_iron(solver, monkeypatch):
    tree = ast.parse(Path(solver.__file__).read_text(encoding="utf-8"))
    assignment = next(node for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "_safe_identity" for t in node.targets)
        and isinstance(node.value, ast.Call))
    mesh = _Mesh([(0, 0), (1, 0), (0, 1),       # conductor
                  (2, 0), (3, 0), (2, 1),       # nonconducting iron
                  (4, 0), (5, 0), (5, 1), (4, 1)],  # gap belt
                 [(0, 1, 2), (3, 4, 5), (6, 7, 8), (6, 8, 9)])
    env = dict(_safe_identity_digest=solver._safe_identity_digest,
        _safe_physical_boundary_digest=solver._safe_physical_boundary_digest,
        _safe_mesh_entity_digest=solver._safe_mesh_entity_digest,
        _SAFE_WARM_CACHE_SCHEMA=solver._SAFE_WARM_CACHE_SCHEMA,
        DOM_AIRGAP=solver.DOM_AIRGAP, DOM_BAND=solver.DOM_BAND,
        geo={"wire_parallel": 2}, _geo_fp="same", motor=SimpleNamespace(parameters={"x": 1}),
        mats_full={}, matr0={}, _noload_material_fingerprint=lambda: "same",
        coil_temp_c=80., magnet_temp_c=120., I_phase_rms=60., gamma_deg=4.,
        daxis_eff=10., rpm=900., rotor_angle0_deg=0., _src=SimpleNamespace(),
        star_delta="star", six_phase=None, demag=True, rotor_eddy=True, frozen_nu=False,
        n_parallel=1, connection="4S", n_steps_per_period=24, n_periods=1.,
        _eddy_method="march", _os_sb=os, n_sectors=2, _bc_sign=-1, element_order=2,
        _safe_s_tags=[200], _safe_r_tags=[100], _safe_m_tags=[100],
        stator_fillet_mm=0., outer_air_factor=1.3,
        half={"s": {"mesh": mesh, "cells": {200: np.array([0]),
                                               solver.DOM_STATOR: np.array([1]),
                                               solver.DOM_AIRGAP: np.array([2, 3])}},
              "r": {"mesh": copy.deepcopy(mesh),
                    "cells": {100: np.array([0]),
                              solver.DOM_ROTOR: np.array([1]),
                              solver.DOM_AIRGAP: np.array([2, 3])}}})
    def actual_key():
        exec(compile(ast.fix_missing_locations(ast.Module([assignment], type_ignores=[])),
                     "production-key", "exec"), env)
        return env["_safe_identity"]
    base = actual_key()
    conductor_digest = solver._safe_mesh_entity_digest(mesh, env["half"]["s"]["cells"], [200])
    magnet_digest = solver._safe_mesh_entity_digest(
        env["half"]["r"]["mesh"], env["half"]["r"]["cells"], [100])
    variants = {}
    components = {}
    def capture(name):
        variants[name] = actual_key()
        components[name] = (
            solver._safe_mesh_entity_digest(mesh, env["half"]["s"]["cells"], [200]),
            solver._safe_mesh_entity_digest(env["half"]["r"]["mesh"],
                                            env["half"]["r"]["cells"], [100]))
    env["stator_fillet_mm"] = .4; capture("fillet"); env["stator_fillet_mm"] = 0.
    env["half"]["s"]["mesh"].p[0, 4] = 3.1; capture("iron_boundary"); env["half"]["s"]["mesh"].p[0, 4] = 3.
    cells = env["half"]["s"]["cells"]
    cells[99] = cells.pop(solver.DOM_STATOR); capture("material_tag"); cells[solver.DOM_STATOR] = cells.pop(99)
    env["outer_air_factor"] = 1.4; capture("outer_air"); env["outer_air_factor"] = 1.3
    env["motor"].parameters["x"] = 2; capture("motor_parameters"); env["motor"].parameters["x"] = 1
    assert all(value != base for value in variants.values())
    assert all(value == (conductor_digest, magnet_digest)
               for value in components.values())
    # True gap-only internal retriangulation preserves the physical exterior.
    env["half"]["s"]["mesh"].t[:, 2:] = np.array([[6, 7], [7, 8], [9, 9]])
    assert actual_key() == base
    env["half"]["s"]["mesh"].t[:, 2:] = np.array([[6, 6], [7, 8], [8, 9]])
    monkeypatch.setenv("SB_SAFE_WARM_CACHE", "1")
    meta, cache = _complete_cache()
    cache = dict(cache, safe_identity=base)
    for changed in variants.values():
        ok, reason = solver._warm_seed_accept(cache, meta, 60., 15000., 4.,
                                               n_sectors=2, need_br=True,
                                               safe_identity=changed)
        assert not ok and "identity" in reason
    assert solver._warm_seed_accept(cache, meta, 60., 15000., 4.,
                                    n_sectors=2, need_br=True,
                                    safe_identity=base) == (True, "")


@pytest.mark.parametrize("hostile_key", SAFE_ONLY)
def test_actual_head_differential_off_loader_with_complete_hostile_safe_payload(
        tmp_path, monkeypatch, solver, hostile_key):
    monkeypatch.delenv("SB_SAFE_WARM_CACHE", raising=False)
    path = "src/motor_ai_sim/simulation/fem_solver_2d.py"
    head_tree = ast.parse(subprocess.check_output(["git", "show", "HEAD:" + path],
                                                  cwd=Path.cwd()).decode())
    head_fn = next(n for n in head_tree.body
                   if isinstance(n, ast.FunctionDef) and n.name == "_warm_cache_load")
    head_env = dict(vars(solver), _SB_WARM_CACHE={})
    exec(compile(ast.Module([head_fn], type_ignores=[]), path, "exec"), head_env)
    base = _cache(4.0)
    payload = dict(doflocs=base["doflocs"], A=base["A"], Ued=base["Ued"],
        solid=base["solid"], nspp=8, npd=1.04, conn="np1", temp=24., mscale=1.,
        I=13., rpm=14., gam=15., geo_fp="g", nspp_req=8, nsect=2,
        A2=base["A2"], br_val=np.array([.9]), br_cen=np.array([[0., 0.]]),
        br_tag=np.array([1]), solid_grp_cu=np.array([.2]), ts="bdf2",
        safe_schema=solver._SAFE_WARM_CACHE_SCHEMA, safe_identity="id",
        dof_region=np.array([0, 0, 1, 1]), edcon_fp="ed", eddy_complete=True,
        demag_complete=True, demag_history_json="{}", br_mat=np.array(["m"]),
        Ib_prev_net=np.ones(2), Ib_prev_imposed=np.ones(2),
        Ib_prev2_net=np.ones(2), Ib_prev2_imposed=np.ones(2))
    payload[hostile_key] = np.array([{"corrupt": hostile_key}], dtype=object)
    archive = tmp_path / (hostile_key + ".npz")
    np.savez(archive, **payload)
    head_env["_warm_cache_path"] = lambda: archive
    monkeypatch.setattr(solver, "_warm_cache_path", lambda: archive)
    solver._SB_WARM_CACHE["last"] = None
    head = head_env["_warm_cache_load"](disk_only=True)
    current = solver._warm_cache_load(disk_only=True)
    assert head is not None and current is not None
    def same(left, right):
        assert set(left) == set(right)
        for key in left:
            if isinstance(left[key], dict):
                same(left[key], right[key])
            elif isinstance(left[key], np.ndarray):
                assert left[key].dtype == right[key].dtype
                assert left[key].shape == right[key].shape
                assert left[key].tobytes() == right[key].tobytes()
            else:
                assert left[key] == right[key]
    same(current, head)
