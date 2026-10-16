# -*- coding: utf-8 -*-
"""Six-phase winding: two in-phase 3-phase sets (owner 2026-09-28).

Pins
  * the winding bookkeeping (``winding_sets``): which paths form each set,
    and the LOUD refusals — odd / single path, paths that are not copies of
    each other, an unequal set;
  * the VSD projection on an inductance matrix whose answer is known;
  * the SOLVER: a six-phase run with balanced sine currents IS the 3-phase run
    with the same parallel paths (torque and flux linkage to solver
    precision), and the full-ring L_xy probe on the small 30 mm model;
  * the CONTROLLER: a six-phase winding selects two 3-phase inverters, each
    leg at the set's current, and takes the machine's own L_xy.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from motor_ai_sim.simulation.drive import inverse_park
from motor_ai_sim.winding_sets import (SixPhaseError, resolve_six_phase,
                                       vsd_basis)
from tests.test_controller import R_SYN_MOHM, _synth_request, lo, synth_dir  # noqa: F401


# ── bookkeeping and validation ────────────────────────────────────────────

def _six(S, P, npar, **kw):
    return resolve_six_phase({"phases": 6, "n_parallel": npar, **kw},
                             num_slots=S, num_poles=P, single_layer=True)


def test_three_phases_is_no_spec():
    assert resolve_six_phase({"phases": 3, "n_parallel": 2},
                             num_slots=12, num_poles=10) is None


def test_default_split_is_half_the_paths_in_phase():
    s = _six(12, 10, 2)
    assert s["set1_paths"] == [1] and s["set2_paths"] == [2]
    assert s["shift_deg"] == 0.0 and s["split"] == "parallel_paths"
    assert s["slot_set"] == [1] * 6 + [2] * 6
    assert s["neutrals"] == "isolated"
    s4 = resolve_six_phase({"phases": 6, "n_parallel": 4, "set1_paths": "1,3"},
                           num_slots=48, num_poles=8, single_layer=False)
    assert s4["set1_paths"] == [1, 3] and s4["set2_paths"] == [2, 4]
    assert s4["slot_set"][:12] == [1] * 12 and s4["slot_set"][12:24] == [2] * 12


@pytest.mark.parametrize("npar", [1, 3])
def test_odd_parallel_paths_are_refused(npar):
    with pytest.raises(SixPhaseError) as e:
        _six(12, 10, npar)
    assert "even number" in str(e.value)
    assert "phases" in e.value.fields


def test_paths_that_are_not_copies_are_refused():
    # 12 s / 10 p repeats every 6 slots: 4 sectors of 3 slots cut its coils
    with pytest.raises(SixPhaseError):
        _six(12, 10, 4)


def test_unequal_sets_are_refused():
    with pytest.raises(SixPhaseError) as e:
        resolve_six_phase({"phases": 6, "n_parallel": 4, "set1_paths": "1"},
                          num_slots=48, num_poles=8, single_layer=False)
    assert "exactly half" in str(e.value)
    with pytest.raises(SixPhaseError):
        resolve_six_phase({"phases": 6, "n_parallel": 4, "set1_paths": "1,7"},
                          num_slots=48, num_poles=8, single_layer=False)


def test_phases_must_be_three_or_six():
    with pytest.raises(SixPhaseError):
        resolve_six_phase({"phases": 5, "n_parallel": 2},
                          num_slots=12, num_poles=10)


# ── VSD projection on a known matrix ──────────────────────────────────────

def test_vsd_reads_common_and_differential_inductance():
    """Per-set synchronous self L1, set-to-set mutual M (both off the zero
    sequence): the air-gap plane reads L1 + M, the x-y plane L1 - M."""
    L1, M, th = 3.0e-4, 1.1e-4, 0.37
    Pz = np.eye(3) - np.ones((3, 3)) / 3.0
    L6 = np.block([[L1 * Pz, M * Pz], [M * Pz, L1 * Pz]])
    B = vsd_basis(th, inverse_park)
    E = np.column_stack([B["d"], B["q"], B["xy"]])
    assert np.allclose(E.T @ E, np.eye(4), atol=1e-12)
    assert B["d"] @ L6 @ B["d"] == pytest.approx(L1 + M, rel=1e-12)
    assert B["q"] @ L6 @ B["q"] == pytest.approx(L1 + M, rel=1e-12)
    assert np.allclose(B["xy"].T @ L6 @ B["xy"], (L1 - M) * np.eye(2), atol=1e-16)


# ── the solver, on the pinned 30 mm machine (12 s / 14 p) with 2 paths ────

RUN = dict(n_steps_per_period=4, n_periods=1.0, mesh_size_mm=1.4,
           min_size_mm=0.35, gap_layers=1.0, n_sectors=2, structured_gap=True,
           iron_template=True, geo_mesh=True, coil_temp_c=120.0,
           element_order=2, demag=False, eddy=False, rotor_eddy=False,
           I_phase_rms=60.0, gamma_deg=10.0, connection="2P")


@pytest.fixture(scope="module")
def spec30():
    return resolve_six_phase({"phases": 6, "n_parallel": 2},
                             num_slots=12, num_poles=14, single_layer=True)


def test_six_phase_balanced_equals_three_phase(spec30):
    from motor_ai_sim.material_context import set_request_materials
    from motor_ai_sim.simulation.fem_solver_2d import fem_transient_sliding_band
    from tests.test_physics_regression import GEO_30MM, OVERRIDE, RPM
    set_request_materials(OVERRIDE)
    try:
        a = fem_transient_sliding_band(geo_override=dict(GEO_30MM), rpm=RPM, **RUN)
        b = fem_transient_sliding_band(geo_override=dict(GEO_30MM), rpm=RPM,
                                       six_phase=spec30, **RUN)
    finally:
        set_request_materials(None)
    assert b["T_avg_Nm"] == pytest.approx(a["T_avg_Nm"], rel=1e-9)
    assert np.allclose(b["psi_A_Wb"], a["psi_A_Wb"], rtol=1e-9, atol=1e-15)
    six = b["six_phase"]
    assert six["source_sum_residual"] < 1e-12
    assert six["set1_paths"] == [1] and six["shift_deg"] == 0.0
    # the half-ring sector holds set 1 only: the x-y probe is not faked there
    assert six["xy_probe_valid"] is False and "inductances" not in six


def test_lxy_extraction_on_the_full_ring(spec30):
    from motor_ai_sim.material_context import set_request_materials
    from motor_ai_sim.simulation.fem_solver_2d import measure_six_phase_inductances
    from tests.test_physics_regression import GEO_30MM, OVERRIDE, RPM
    kw = {k: v for k, v in RUN.items()
          if k not in ("n_steps_per_period", "n_periods", "n_sectors",
                       "I_phase_rms", "gamma_deg", "eddy")}
    set_request_materials(OVERRIDE)
    try:
        blk = measure_six_phase_inductances(
            six_phase=spec30, I_phase_rms=60.0, gamma_deg=10.0, rpm=RPM,
            geo_override=dict(GEO_30MM), **kw)
    finally:
        set_request_materials(None)
    ind = blk["inductances"]
    assert blk["sets_in_model"] == [1, 2] and blk["probe"]["picard_converged"]
    # a set is half the paths: its air-gap-plane L_d is twice the machine's
    assert ind["Ld_set_mH"] == pytest.approx(2.0 * blk["probe"]["Ld_machine_mH"],
                                             rel=0.01)
    assert 0.0 < ind["Lxy_mH"] <= ind["Lxy_max_mH"]
    assert ind["Lxy_min_mH"] > 0.0
    assert ind["Lxy_pct_of_Ld"] == pytest.approx(
        100.0 * ind["Lxy_mH"] / ind["Ld_set_mH"], rel=1e-3)
    assert ind["reciprocity_pct"] < 1.0
    assert ind["samples"] == 4
    # the two sets are identical halves: identical per-set flux
    f1, f2 = blk["per_set_flux"]
    assert f1["psi1_A_Wb"] == pytest.approx(f2["psi1_A_Wb"], rel=1e-3)


# ── the controller ─────────────────────────────────────────────────────────

def _six_req(**kw):
    spec = resolve_six_phase({"phases": 6, "n_parallel": 2},
                             num_slots=12, num_poles=10, single_layer=True)
    return _synth_request(winding_n_parallel=2, _six_slot_set=spec["slot_set"],
                          **kw)


def test_six_phase_selects_two_inverters_at_the_set_current(synth_dir):
    out = lo.solve_controller(_six_req())
    topo = out["topology"]
    assert topo["preset"] == "two_3ph_sets"
    assert [b["phase_shift_deg"] for b in topo["bridges"]] == [0.0, 0.0]
    assert sorted(topo["bridges"][0]["coils"]) == [1, 2, 3]
    assert sorted(topo["bridges"][1]["coils"]) == [4, 5, 6]
    # 6 legs at I/2: half the conduction loss of one bridge at I
    one = lo.solve_controller(_synth_request(winding_n_parallel=2))
    assert out["losses"]["conduction_W"] == pytest.approx(
        6 * 50.0 ** 2 * R_SYN_MOHM * 1e-3, rel=1e-6)
    assert out["losses"]["conduction_W"] == pytest.approx(
        0.5 * one["losses"]["conduction_W"], rel=1e-6)


def test_ripple_takes_the_machines_own_lxy(synth_dir):
    out = lo.solve_controller(_six_req(
        modulation_index=0.8, power_factor=None, _ripple_ld_mH=0.1,
        _ripple_lq_mH=0.1, _six_lxy_pct=40.0))
    r = out["ripple"]
    assert r["status"] == "computed", r
    assert "EM record" in r["sources"]["l_xy"]
    # per-set L_d = 2 x the machine's (half the current, same voltage)
    assert r["l_xy_uH"] == pytest.approx(0.40 * 2.0 * 100.0, rel=1e-6)
    typed = lo.solve_controller(_six_req(
        modulation_index=0.8, power_factor=None, _ripple_ld_mH=0.1,
        _ripple_lq_mH=0.1, _six_lxy_pct=40.0, ripple_l_xy_uH=33.0))
    assert typed["ripple"]["l_xy_uH"] == pytest.approx(33.0)


# ── the winding route: stored, previewed, refused loudly ──────────────────

def test_winding_route_stores_and_refuses():
    from fastapi.testclient import TestClient
    from motor_ai_sim.api import app
    c = TestClient(app)
    assert c.patch("/api/winding/config", json={"connection": "2S-2P"}).status_code == 200
    r = c.patch("/api/winding/config", json={"phases": 6})
    assert r.status_code == 200, r.text
    g = c.get("/api/winding/config").json()
    assert g["phases"] == 6 and g["set_neutrals"] == "isolated"
    assert g["six_phase"]["set1_paths"] == [1] and g["six_phase"]["set2_paths"] == [2]
    # one path cannot be split: the connection change is refused, not stored
    r = c.patch("/api/winding/config", json={"connection": "4S"})
    assert r.status_code == 422 and "even number" in r.json()["detail"]
    assert c.get("/api/winding/config").json()["connection"] == "2S-2P"
    r = c.patch("/api/winding/config", json={"set1_paths": "1,2"})
    assert r.status_code == 422 and "exactly half" in r.json()["detail"]
    assert c.patch("/api/winding/config", json={"phases": 3}).status_code == 200
    assert c.patch("/api/winding/config", json={"connection": "4S"}).status_code == 200
