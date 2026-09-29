# -*- coding: utf-8 -*-
"""Optimizer speed-up A + B + C (owner 2026-09-24, «Запускай A + B + C»).

A. an optimizer candidate skips the no-load ψ_PM probe (its only consumers,
   chord Ld/Lq and the droop row, are not in refine_proc's result) — and ONLY a
   candidate, and only when its result is not persisted as the live machine;
B. a candidate reuses the run's BASELINE d-axis calibration when its geometry
   change keeps both mirror symmetries; a count / segment / unknown key makes it
   calibrate its own; the result says which;
C. the default worker count is min(8, physical cores available − 2).

Nothing here solves a field except one 2-frame coarse run for the summary test.
"""
from __future__ import annotations

import threading

import pytest

from motor_ai_sim.simulation import fem_solver_2d as F


# ═══════════════════════════════════════════════════════════════════════════
#  The candidate scope
# ═══════════════════════════════════════════════════════════════════════════

def test_scope_is_off_by_default_and_resets():
    assert F.optimizer_candidate_active() is False
    with F.optimizer_candidate_scope(True):
        assert F.optimizer_candidate_active() is True
        with F.optimizer_candidate_scope(False):
            assert F.optimizer_candidate_active() is False
        assert F.optimizer_candidate_active() is True
    assert F.optimizer_candidate_active() is False


def test_scope_does_not_leak_to_another_thread():
    """The API process runs Simulation solves on other threads: they must never
    inherit a candidate's shortcuts."""
    seen = []
    with F.optimizer_candidate_scope(True):
        t = threading.Thread(target=lambda: seen.append(F.optimizer_candidate_active()))
        t.start()
        t.join()
    assert seen == [False]


# ═══════════════════════════════════════════════════════════════════════════
#  B — the guard
# ═══════════════════════════════════════════════════════════════════════════

BASE = {"num_poles": 14, "num_slots": 12, "num_seg": 2, "num_poles_per_segment": 7,
        "num_slots_per_segment": 6, "angle_pole": 360 / 14, "angle_slot": 30.0,
        "magnet_fill_up": 0.4123, "magnet_height": 4.0, "tooth_width": 2.3,
        "air_gap": 0.2, "stator_diameter": 30.0, "rotor_outer_radius": 8.7}


@pytest.mark.parametrize("key,val", [
    ("magnet_fill_up", 0.45), ("magnet_height", 4.3), ("tooth_width", 2.1),
    ("air_gap", 0.25), ("stator_diameter", 32.0), ("rotor_outer_radius", 8.5),
    ("motor_length", 20.0), ("shaft_height", 3.0), ("wire_split", 2),
])
def test_dimension_changes_may_reuse_the_baseline(key, val):
    assert F.daxis_reuse_blockers(BASE, dict(BASE, **{key: val})) == []


@pytest.mark.parametrize("change,blocker", [
    ({"num_poles": 10, "angle_pole": 36.0}, "num_poles"),
    ({"num_slots": 24, "angle_slot": 15.0}, "num_slots"),
    ({"num_seg": 1}, "num_seg"),
    ({"num_poles_per_segment": 5}, "num_poles_per_segment"),
    ({"magnet_lamination_tan": 2.0}, "magnet_lamination_tan"),   # splits magnets in plane
    ({"rotor_skew_deg": 3.0}, "rotor_skew_deg"),                 # a key nobody listed
])
def test_topology_or_unknown_keys_force_own_calibration(change, blocker):
    assert blocker in F.daxis_reuse_blockers(BASE, dict(BASE, **change))


def test_float_noise_is_not_a_change():
    assert F.daxis_reuse_blockers(BASE, dict(BASE, angle_pole=360 / 14 + 1e-13)) == []


class _Stop(Exception):
    pass


@pytest.fixture
def fixture_machine(monkeypatch):
    """Pin the baseline machine to this suite's OWN geometry.

    The d-axis decision reads the baseline machine from ``get_config()``, and
    the solver's default sector count is 4.  Left on the live
    ``config/motor_config.yaml`` these tests broke whenever that file held a
    machine 4 does not divide (e.g. 12s/14p).  The fixture machine is the
    30 mm, 12-slot / 14-pole regression geometry, and ``_decide`` solves it on
    half the ring (n_sectors=2 — a true symmetry of 12/14).  Every other config
    section is the live one, unchanged.
    """
    from omegaconf import OmegaConf
    import motor_ai_sim.config as _mc
    from motor_ai_sim.simulation.geometry_2d import merge_geo_override
    from tests.test_physics_regression import GEO_30MM

    live = _mc.get_config()
    cfg = (OmegaConf.to_container(live, resolve=True)
           if OmegaConf.is_config(live) else dict(live))
    cfg["geometry"] = merge_geo_override(dict(cfg.get("geometry") or {}),
                                         dict(GEO_30MM))
    assert (cfg["geometry"]["num_slots"], cfg["geometry"]["num_poles"]) == (12, 14)
    monkeypatch.setattr(_mc, "get_config", lambda *a, **k: cfg)
    return cfg


@pytest.fixture
def daxis_probe(monkeypatch, fixture_machine):
    """Stop fem_transient_sliding_band right after the d-axis decision (before
    any mesh) and record every _resolve_daxis_shift call."""
    calls, used = [], []

    def fake_resolve(p, geo, wind, pole_pairs, geo_override, n_sectors, progress_cb=None):
        calls.append({"geo_override": geo_override, "geo": dict(geo)})
        return 60.0 if geo_override is None else 61.5

    def stop(*a, **k):
        used.append(k.get("daxis_deg"))
        raise _Stop()

    monkeypatch.setattr(F, "_resolve_daxis_shift", fake_resolve)
    monkeypatch.setattr(F, "_Excitation", stop)
    return calls, used


def _decide(ov):
    with pytest.raises(_Stop):
        F.fem_transient_sliding_band(geo_override=dict(ov), I_phase_rms=10.0,
                                     n_steps_per_period=2, element_order=2,
                                     n_sectors=2)   # a symmetry of the 12s/14p fixture


def _cfg_geo():
    from motor_ai_sim.config import get_config
    return dict(get_config().get("geometry") or {})


def test_standard_solve_calibrates_its_own_axis(daxis_probe):
    calls, used = daxis_probe
    g = _cfg_geo()
    _decide({"magnet_height": float(g["magnet_height"]) + 0.1})
    assert used == [61.5]
    assert len(calls) == 1 and calls[0]["geo_override"]


def test_candidate_with_a_dimension_change_uses_the_baseline(daxis_probe):
    calls, used = daxis_probe
    g = _cfg_geo()
    with F.optimizer_candidate_scope(True):
        _decide({"magnet_height": float(g["magnet_height"]) + 0.1})
    assert used == [60.0]
    # the one lookup was for the BASELINE (config) machine, not the candidate
    assert len(calls) == 1 and calls[0]["geo_override"] is None
    assert calls[0]["geo"]["magnet_height"] == pytest.approx(float(g["magnet_height"]))


def test_candidate_with_a_blocking_key_calibrates_its_own(daxis_probe):
    calls, used = daxis_probe
    with F.optimizer_candidate_scope(True):
        _decide({"rotor_skew_deg": 3.0})
    assert used == [61.5]
    assert len(calls) == 1 and calls[0]["geo_override"]


def test_inside_a_nested_calibration_the_candidate_path_is_off(daxis_probe):
    calls, used = daxis_probe
    g = _cfg_geo()
    F._DAXIS_TLS.calibrating = True
    try:
        with F.optimizer_candidate_scope(True):
            _decide({"magnet_height": float(g["magnet_height"]) + 0.1})
    finally:
        F._DAXIS_TLS.calibrating = False
    assert calls and calls[0]["geo_override"]      # not the baseline lookup


def test_baseline_helper_reports_its_decision(monkeypatch, fixture_machine):
    monkeypatch.setattr(F, "_resolve_daxis_shift", lambda *a, **k: 60.0)
    from motor_ai_sim.simulation.geometry_2d import merge_geo_override
    base = merge_geo_override(_cfg_geo(), None)
    d, info = F._baseline_daxis_for_candidate(dict(base, magnet_height=float(base["magnet_height"]) + 0.2),
                                              {}, 2)
    assert d == 60.0 and info["daxis_calibration"] == "baseline"
    d, info = F._baseline_daxis_for_candidate(dict(base, rotor_skew_deg=2.0), {}, 2)
    assert d is None and info["daxis_calibration"] == "own"
    assert "rotor_skew_deg" in info["daxis_reason"]


# ═══════════════════════════════════════════════════════════════════════════
#  A — the ψ_PM probe
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def tiny_run():
    """2 frames, coarse, pinned d-axis (no calibration): only its dq stamp is
    needed.  Seconds, not minutes."""
    from motor_ai_sim.material_context import set_request_materials
    from tests.test_physics_regression import CONNECTION, GEO_30MM, OVERRIDE, RPM
    set_request_materials(OVERRIDE)
    try:
        r = F.fem_transient_sliding_band(
            geo_override=dict(GEO_30MM), n_steps_per_period=2, n_periods=1.0,
            mesh_size_mm=1.4, min_size_mm=0.35, gap_layers=1.0, n_sectors=2,
            structured_gap=True, iron_template=True, geo_mesh=True,
            coil_temp_c=120.0, element_order=2, demag=False, eddy=False,
            rotor_eddy=False, I_phase_rms=60.0, gamma_deg=10.0, rpm=RPM,
            connection=CONNECTION, daxis_deg=60.0)
    finally:
        set_request_materials(None)
    return r, dict(GEO_30MM), OVERRIDE


def _summary(tiny_run, skip):
    from motor_ai_sim.material_context import set_request_materials
    from motor_ai_sim.routes.simulation import _build_transient_summary
    r, geo, ov = tiny_run
    set_request_materials(ov)
    try:
        return _build_transient_summary(r, I_phase_rms=60.0, gamma_deg=10.0,
                                        coil_temp_c=120.0, geo_override=geo,
                                        skip_psi_pm_probe=skip)
    finally:
        set_request_materials(None)


def test_candidate_summary_does_not_solve_psi_pm(tiny_run, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("ψ_PM probe ran for an optimizer candidate")
    monkeypatch.setattr(F, "noload_psi_pm", boom)
    s = _summary(tiny_run, skip=True)
    assert s["psi_pm_probe_skipped"] is True
    assert s["psi_pm_Wb"] is None and s["Ld_chord_mH"] is None
    assert "optimizer candidate" in s["dq_note"]
    # everything the optimizer ranks is still there
    for k in ("T_em_avg_Nm", "T_ripple_pct"):
        assert s[k] is not None


def test_standard_summary_still_solves_psi_pm(tiny_run, monkeypatch):
    calls = []

    def fake(*a, **k):
        calls.append(1)
        return 1.0e-3, 0.0
    monkeypatch.setattr(F, "noload_psi_pm", fake)
    s = _summary(tiny_run, skip=False)
    assert s["psi_pm_probe_skipped"] is False
    # the probe is asked for whenever the dq stamp passes its self-check
    note = s["dq_note"] or ""
    assert calls == [1] or "withheld" in note or "predates" in note, note


def test_the_route_skips_only_for_a_non_live_candidate():
    """The route passes skip only when the candidate scope is on AND the result
    is not persisted as the live machine (ledger / last transient)."""
    import inspect
    from motor_ai_sim.routes import simulation as S
    src = inspect.getsource(S.get_fem_transient)
    assert "skip_psi_pm_probe=bool(_opt_cand() and not _is_live_machine)" in src


def test_refine_proc_passes_the_flag_and_reports_the_axis():
    import inspect
    from motor_ai_sim.optimization import refine_proc as R
    sig = inspect.signature(R.run_one)
    assert sig.parameters["optimizer_candidate"].default is False     # standard by default
    src = inspect.getsource(R)
    for k in ('"daxis_source"', '"daxis_policy"', '"psi_pm_probe_skipped"',
              'spec.get("optimizer_candidate"'):
        assert k in src, k


@pytest.mark.parametrize("flag", [True, False])
def test_run_one_opens_the_scope_only_when_asked(monkeypatch, flag):
    """The scope is on exactly during the candidate's solve, and off after it.
    On the line that has ``sampling_purpose``, a STANDARD eval (final
    validation / Apply) is never a candidate, whatever the flag says."""
    import inspect
    from motor_ai_sim.optimization import refine_proc as R
    seen = []

    class _K:
        def run(self, cap, payload):
            seen.append(F.optimizer_candidate_active())
            return {"ok": False, "error": "stop here"}
    monkeypatch.setattr(R, "_kernel", lambda: _K())
    cases = [({}, flag)]
    if "sampling_purpose" in inspect.signature(R.run_one).parameters:
        cases.append(({"sampling_purpose": "standard"}, False))
    for extra, expect in cases:
        seen.clear()
        with pytest.raises(RuntimeError, match="stop here"):
            R.run_one({}, 10.0, 4, 120.0, optimizer_candidate=flag, **extra)
        assert seen == [expect], extra
        assert F.optimizer_candidate_active() is False


def test_subprocess_eval_sends_candidates_by_default():
    import inspect
    from motor_ai_sim.routes import optimization as O
    sig = inspect.signature(O._subprocess_eval)
    assert sig.parameters["optimizer_candidate"].default is True
    assert '"optimizer_candidate": bool(optimizer_candidate)' in inspect.getsource(O._subprocess_eval)


# ═══════════════════════════════════════════════════════════════════════════
#  C — worker count
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("physical,expect", [
    (12, 8),    # the workstation (Ryzen AI 9 HX 370): measured optimum 8
    (8, 6),     # the AX42 container (cpuset 0-11 = all 8 cores)
    (16, 8),    # capped: the memory channels, not the cores, are the limit
    (4, 2), (2, 2), (1, 2),
])
def test_worker_rule(monkeypatch, physical, expect):
    from motor_ai_sim.routes import optimization as O
    monkeypatch.delenv("FEM_SCAN_WORKERS", raising=False)
    monkeypatch.setattr(O, "_physical_cores_available", lambda: physical)
    assert O._scan_worker_count() == expect


def test_env_override_wins(monkeypatch):
    from motor_ai_sim.routes import optimization as O
    monkeypatch.setenv("FEM_SCAN_WORKERS", "11")
    monkeypatch.setattr(O, "_physical_cores_available", lambda: 12)
    assert O._scan_worker_count() == 11


def test_physical_cores_honour_affinity_and_quota(monkeypatch):
    """A Linux container: affinity 0-11 on an 8C/16T box whose SMT siblings
    are n and n+8 (the AX42's sysfs), quota 12 CPUs → 8 physical."""
    import builtins
    import io
    import os
    from motor_ai_sim.routes import optimization as O
    monkeypatch.setattr(os, "sched_getaffinity", lambda pid: set(range(12)), raising=False)
    real_open = builtins.open

    def fake_open(path, *a, **k):
        p = str(path).replace("\\", "/")
        if p.startswith("/sys/devices/system/cpu/cpu"):
            cpu = int(p.split("/cpu/cpu")[1].split("/")[0])
            if p.endswith("core_id"):
                return io.StringIO("%d\n" % (cpu % 8))
            return io.StringIO("0\n")
        if p == "/sys/fs/cgroup/cpu.max":
            return io.StringIO("1200000 100000\n")
        return real_open(path, *a, **k)
    monkeypatch.setattr(builtins, "open", fake_open)
    assert O._physical_cores_available() == 8
    monkeypatch.delenv("FEM_SCAN_WORKERS", raising=False)
    assert O._scan_worker_count() == 6
