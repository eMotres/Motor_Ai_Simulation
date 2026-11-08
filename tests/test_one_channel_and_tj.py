# -*- coding: utf-8 -*-
"""One channel of a two-set winding stored as 3 phases, and the junction
temperature of the coupled (Stage 2) controller (2026-11-08).

Defect 1: a machine with two independent 3-phase sets, each on its own
inverter channel, kept in its configuration as ONE 3-phase winding with the
sets as parallel paths (``2S-2P``), could not be run with one channel off:
``six_phase_open_set`` answered "needs a six-phase winding".  The sets are now
read from the parallel paths for that request (nothing is written), with the
same bookkeeping a stored 6-phase winding uses.

Defect 2: the coupled controller dropped R_G,off, L_sigma, the switching
source and R_spread that the Controller tab solves with, so Stage 2 and
Stage 1 of the same duty disagreed on switching loss and T_j.

No field solve here: the solver's own open-set physics is pinned in
``test_six_phase_open_set.py``.
"""
from __future__ import annotations

import math

import pytest

from motor_ai_sim.winding_sets import (SixPhaseError, resolve_six_phase,
                                       set_current_scale,
                                       sets_from_parallel_paths, with_open_set)

# a 24-slot / 28-pole single-layer machine, 4 coils per phase, 2S-2P: the
# configuration form of a 150 kW machine with two winding sets
S, P = 24, 28
W_2S2P = {"connection": "2S-2P", "n_parallel": 2, "n_series": 2,
          "n_coils_per_phase": 4, "layers": 1}


# ── defect 1: the schema / mapping ─────────────────────────────────────────

def test_paths_of_a_3phase_winding_map_to_the_two_sets():
    spec = sets_from_parallel_paths(W_2S2P, num_slots=S, num_poles=P)
    assert spec["phases"] == 6 and spec["mapping"] == "parallel_paths_of_3phase"
    assert spec["set1_paths"] == [1] and spec["set2_paths"] == [2]
    # path k = the coils of stator sector k: set 1 = slots 1-12, set 2 = 13-24
    assert spec["slot_set"] == [1] * 12 + [2] * 12
    assert "2 parallel paths" in spec["note"] and "set 1 = path(s) 1" in spec["note"]
    # the same sets a stored 6-phase winding of this machine resolves to
    stored6 = resolve_six_phase(dict(W_2S2P, phases=6), num_slots=S, num_poles=P)
    for k in ("slot_set", "set1_paths", "set2_paths", "unit_set", "n_parallel"):
        assert spec[k] == stored6[k], k
    # the input block is not modified
    assert "phases" not in W_2S2P


def test_four_paths_split_two_per_set():
    # 6-slot sectors alternate polarity (a reversed path), still copies
    spec = sets_from_parallel_paths({"connection": "1S-4P", "n_parallel": 4},
                                    num_slots=S, num_poles=P)
    assert spec["set1_paths"] == [1, 2] and spec["paths_per_set"] == 2
    assert spec["slot_set"] == [1] * 12 + [2] * 12


def test_set1_paths_override_and_neutrals_are_kept():
    spec = sets_from_parallel_paths(dict(W_2S2P, set1_paths="2", set_neutrals="common"),
                                    num_slots=S, num_poles=P, star_delta="delta")
    assert spec["set1_paths"] == [2] and spec["slot_set"] == [2] * 12 + [1] * 12
    assert spec["neutrals"] == "common" and spec["set_connection"] == "delta"


@pytest.mark.parametrize("w, why", [
    ({"connection": "4S", "n_parallel": 1}, "1 parallel path"),
    ({"connection": "3P", "n_parallel": 3}, "even number"),
    ({"phases": 5, "n_parallel": 2}, "phases must be 3 or 6"),
])
def test_paths_that_are_not_two_identical_sets_are_refused(w, why):
    with pytest.raises(SixPhaseError) as ei:
        sets_from_parallel_paths(w, num_slots=S, num_poles=P)
    assert why in str(ei.value)
    if "phases must" not in why:
        assert "one set open needs two identical 3-phase sets" in str(ei.value)
        assert "six_phase_open_set" in ei.value.fields


# ── defect 1: the current assignment and the loss accounting ───────────────

def test_open_set_current_assignment_per_slot():
    """The solver multiplies every coil's source by the factor of its slot's
    set (``fem_solver_2d``: ``_six_slot_w = [scale[s-1] for s in slot_set]``).
    One channel off: the active half of the stator carries twice its balanced
    share of I_phase_rms (= the active channel's own current), the open half
    nothing, and the total ampere-turns are those of the active set alone."""
    spec = with_open_set(sets_from_parallel_paths(W_2S2P, num_slots=S, num_poles=P), 2)
    a = set_current_scale(spec)
    assert a == (2.0, 0.0) and spec["active_set"] == 1 and spec["open_set"] == 2
    w = [a[s - 1] for s in spec["slot_set"]]
    assert w == [2.0] * 12 + [0.0] * 12
    # slot current = w * I_phase / n_par per conductor: I_set in the active
    # half, zero in the open half; the balanced machine at I_phase = 2 I_set
    # puts I_set in EVERY slot, so the MMF of one channel is half the healthy
    i_set = 116.0
    n_par = spec["n_parallel"]
    i_slot = [x * i_set / n_par for x in w]
    assert i_slot[:12] == [i_set] * 12 and i_slot[12:] == [0.0] * 12
    assert sum(i_slot) == pytest.approx(0.5 * sum([2 * i_set / n_par] * S))


def test_open_set_copper_and_thermal_basis_on_a_two_path_winding():
    """The degraded block of the solver, on synthetic frames: DC copper of the
    run is the active set's alone (half the healthy I^2R at the same channel
    current), and the thermal model gets every slot at the active set's
    density (2 x the true copper) — the conservative side, stated."""
    import numpy as np
    from motor_ai_sim.simulation.fem_solver_2d import _six_degraded_block
    n, npar, R = 36, 2, 0.02
    i_set = 116.0
    # the block's set current is a_s * n_par/2 * branch: branch peak for I_set rms
    ib = math.sqrt(2) * i_set / (0.5 * npar * 2.0)
    th = np.linspace(0, 2 * math.pi, n, endpoint=False)
    IA, IB, IC = (ib * np.cos(th - k * 2 * math.pi / 3) for k in range(3))
    psi = [(t, 0.3 * math.cos(t), 0.25 * math.cos(t),
            0.3 * math.cos(t - 2.0944), 0.25 * math.cos(t - 2.0944),
            0.3 * math.cos(t + 2.0944), 0.25 * math.cos(t + 2.0944)) for t in th]
    p_dc_run = 3 * i_set ** 2 * (2 * R)       # the active set, R_set = 2 R_phase
    blk = _six_degraded_block(
        scale=(2.0, 0.0), six_psi=psi, IA=IA, IB=IB, IC=IC,
        dts=np.full(n, 1e-5), n_parallel=npar, R_phase_bal=R, f_elec=933.3,
        set_connection="star", P_cu_dc=p_dc_run, P_cu_total=p_dc_run,
        P_ac_set={1: 0.0, 2: 0.0}, eddy=False)
    s1, s2 = blk["sets"]
    assert s1["I_phase_rms_A"] == pytest.approx(i_set, rel=1e-6)
    assert s2["I_phase_rms_A"] == 0.0 and s2["open"]
    assert s1["P_cu_dc_W"] == pytest.approx(p_dc_run, rel=1e-6)
    assert s2["P_cu_dc_W"] == 0.0
    # healthy at the same channel current: 2 sets x 3 x I_set^2 x 2R = 2 x the run
    p_healthy = 2 * p_dc_run
    assert s1["P_cu_dc_W"] == pytest.approx(0.5 * p_healthy)
    assert blk["P_cu_heat_uniform_W"] == pytest.approx(2 * p_dc_run)
    assert blk["copper_heat_uniform_factor"] == pytest.approx(2.0)
    assert "conservative" in blk["copper_heat_basis"]
    # the open set's voltage is its EMF: omega * psi of set 2
    assert s2["V1_phase_peak_V"] == pytest.approx(2 * math.pi * 933.3 * 0.25, rel=2e-3)


# ── defect 1: the route ────────────────────────────────────────────────────

def _live(monkeypatch, winding):
    import motor_ai_sim.config as mc
    from motor_ai_sim.routes import simulation as sim
    cfg = {"geometry": {"num_slots": S, "num_poles": P}, "winding": dict(winding)}
    monkeypatch.setattr(mc, "get_config", lambda *a, **k: cfg)
    monkeypatch.setattr(sim, "_effective_star_delta", lambda v=None: v or "star")
    return sim, cfg


def test_route_reads_the_sets_from_the_stored_paths(monkeypatch):
    sim, cfg = _live(monkeypatch, W_2S2P)
    assert sim._effective_six_phase() is None        # still a 3-phase machine
    spec = sim._six_phase_for_open_set()
    assert spec["mapping"] == "parallel_paths_of_3phase"
    assert spec["slot_set"] == [1] * 12 + [2] * 12
    assert "phases" not in cfg["winding"]               # nothing written
    # the request's own path count wins, as everywhere in the route
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        sim._six_phase_for_open_set(n_parallel=1)
    assert ei.value.status_code == 422
    assert "one set open needs two identical 3-phase sets" in str(ei.value.detail)


def test_route_refuses_a_single_path_winding(monkeypatch):
    from fastapi import HTTPException
    sim, _ = _live(monkeypatch, {"connection": "4S", "n_parallel": 1, "layers": 1})
    with pytest.raises(HTTPException) as ei:
        sim._six_phase_for_open_set()
    assert ei.value.status_code == 422 and "parallel path" in str(ei.value.detail)


# ── defect 2: the coupled controller carries the whole switching set-up ────

def _settings(ctl):
    from motor_ai_sim.routes import coupled as cp
    return cp._controller_settings({"controller": ctl}, rpm=850.0,
                                   inverter={"f_carrier_hz": 8000.0, "v_dc_V": 750.0})


def test_coupled_settings_carry_r_g_off_l_sigma_source_and_spread():
    s = _settings({"device": "IMCQ120R007M2H", "r_g_ext_ohm": 20.0,
                   "r_g_off_ext_ohm": 15.0, "l_sigma_nH": 15.0,
                   "switching_source": "Datasheet", "r_spread_k_w": 0.01})
    assert s["r_g_ext_ohm"] == 20.0 and s["r_g_off_ext_ohm"] == 15.0
    assert s["l_sigma_nH"] == 15.0 and s["switching_source"] == "datasheet"
    assert s["r_spread_k_w"] == 0.01
    assert s["sources"]["r_g_off_ext_ohm"] == "the request"


def test_coupled_settings_defaults_are_unchanged():
    s = _settings({"device": "IMCQ120R004M2H"})
    assert s["r_g_ext_ohm"] is None and s["r_g_off_ext_ohm"] is None
    assert s["l_sigma_nH"] is None and s["switching_source"] is None
    assert s["r_spread_k_w"] == 0.0


@pytest.mark.parametrize("bad", [
    {"switching_source": "lookup"}, {"r_g_off_ext_ohm": -1.0},
    {"l_sigma_nH": 0.0}, {"r_spread_k_w": "x"}])
def test_coupled_settings_refuse_bad_switching_inputs(bad):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        _settings(dict({"device": "IMCQ120R004M2H"}, **bad))
    assert ei.value.status_code == 422


def _loop(monkeypatch, **cfg_over):
    import motor_ai_sim.config as mc
    from motor_ai_sim.routes.coupled import _ControllerLoop
    monkeypatch.setattr(mc, "get_config", lambda *a, **k: {
        "geometry": {"num_slots": S, "num_poles": P},
        "winding": {"layers": 1, "n_parallel": 1}})
    cfg = {"device": "IMCQ120R004M2H", "topology": "one_3ph",
           "devices_parallel": 1, "dead_time_us": 0.5, "v_gs_on_V": 18.0,
           "v_gs_off_V": 0.0, "r_g_ext_ohm": None, "e_oss_policy": "included_in_eon",
           "set_split": "series_split", "h_bridge_modulation": "unipolar",
           "cooling": {"mode": "liquid", "t_in_c": 40.0}, "r_tim_k_w": 0.09,
           "t_j_start_c": 100.0, "notes": [], "sources": {}}
    cfg.update(cfg_over)
    return _ControllerLoop(cfg, inverter={"v_dc_V": 750.0, "f_carrier_hz": 8000.0,
                                          "target_I_phase_rms_A": 257.2},
                           star_delta="star", rpm=850.0, pole_pairs=14)


EM_TAKEOFF = {"summary": {"I1_phase_rms_A": 257.2, "efficiency_shaft": 0.89,
                          "P_loss_total_incl_mech_W": 8600.0, "T_avg_Nm": 780.0},
              "I_phase_rms_solved_A": 257.2,
              "pwm": {"modulation_index": 0.55}}


def test_coupled_request_matches_the_controller_tab_solve(monkeypatch):
    """Stage 2 hands solve_controller the same switching set-up Stage 1 uses:
    the T_j of the coupled pass equals a direct solve with those inputs."""
    from motor_ai_sim.inverter.losses import solve_controller
    lp = _loop(monkeypatch, r_g_ext_ohm=10.0, r_g_off_ext_ohm=10.0,
               switching_source="datasheet", r_spread_k_w=0.0)
    req = lp._solve_request(EM_TAKEOFF)
    assert req["r_g_off_ext_ohm"] == 10.0 and req["switching_source"] == "datasheet"
    assert req["r_spread_k_w"] == 0.0 and "l_sigma_nH" in req
    direct = solve_controller(dict(req))
    lp2 = _loop(monkeypatch, r_g_ext_ohm=10.0, r_g_off_ext_ohm=10.0,
                switching_source="datasheet", r_spread_k_w=0.0)
    lp2.step(EM_TAKEOFF, it=1)
    assert lp2.solve["thermal"]["t_j_max_c"] == pytest.approx(
        direct["thermal"]["t_j_max_c"], abs=0.05)


def test_r_g_off_reaches_the_switching_loss(monkeypatch):
    """A 20 / 15 ohm board is not a 20 / 20 ohm board: the off resistor the
    coupled path used to drop moves the switching loss."""
    from motor_ai_sim.inverter.losses import solve_controller
    lp = _loop(monkeypatch, r_g_ext_ohm=20.0, r_g_off_ext_ohm=5.0,
               switching_source="datasheet")
    req = lp._solve_request(EM_TAKEOFF)
    a = solve_controller(dict(req))["losses"]["switching_W"]
    b = solve_controller(dict(req, r_g_off_ext_ohm=None))["losses"]["switching_W"]
    # datasheet curves: E_off grows 0.215 / ohm of R_G, E_on 0.148 / ohm —
    # the datasheet path takes ONE R_G for both (r_g_off only selects a SPICE
    # set), so on that basis the two must agree; on the SPICE basis they differ
    assert a == pytest.approx(b, rel=1e-9)
    sp = dict(req, switching_source="spice")
    a_s = solve_controller(dict(sp))["losses"]["switching_W"]
    b_s = solve_controller(dict(sp, r_g_off_ext_ohm=None))["losses"]["switching_W"]
    assert a_s < b_s


# ── defect 2: the reconciliation, pinned ───────────────────────────────────

def _takeoff(i_a, *, r_g, rtim=0.09, tin=40.0, spice_static=False, monkeypatch=None):
    from motor_ai_sim.inverter import devices as D
    from motor_ai_sim.inverter.losses import solve_controller
    if spice_static:
        # the conduction basis before 2026-11-03 (17402894): the SPICE static
        # table, unchecked against the datasheet R_DS(on)
        monkeypatch.setattr(D.DeviceCard, "static_conduction_check",
                            lambda self, t, v: None)
    req = {"standalone": True, "v_dc_V": 750.0, "i_phase_rms_A": i_a,
           "f_elec_hz": 198.3, "f_carrier_hz": 8000.0, "modulation_index": 0.75,
           "power_factor": 0.85, "pwm_modulation": "svpwm",
           "device": "IMCQ120R004M2H", "devices_parallel": 1,
           "r_tim_k_w": rtim, "r_spread_k_w": 0.0,
           "cooling": {"mode": "liquid", "t_in_c": tin}}
    if r_g is not None:
        req.update({"r_g_ext_ohm": r_g, "r_g_off_ext_ohm": r_g})
    return solve_controller(req)


def test_old_notes_are_reproduced_by_their_inputs(monkeypatch):
    """The 129 C of the old take-off notes (258 A, 40 C inlet, R 0.09) is the
    app's own model with R_G 10 / 10 ohm AND the SPICE static conduction the
    app used before 2026-11-03; the recent 95 C is the same model at the
    datasheet's own 2.3 ohm with the datasheet R_DS(on)."""
    old = _takeoff(257.2, r_g=10.0, spice_static=True, monkeypatch=monkeypatch)
    assert old["thermal"]["t_j_max_c"] == pytest.approx(129.1, abs=1.0)
    monkeypatch.undo()
    recent = _takeoff(257.2, r_g=None)
    assert recent["thermal"]["t_j_max_c"] == pytest.approx(94.9, abs=1.0)
    rev_a = _takeoff(257.2, r_g=10.0)
    assert rev_a["thermal"]["t_j_max_c"] == pytest.approx(116.1, abs=1.0)
    assert rev_a["losses"]["conduction_source"] == "datasheet"
    # power factor and modulation index do not move a synchronous-rectifying
    # MOSFET bridge's losses in this model
    from motor_ai_sim.inverter.losses import solve_controller
    req = {"standalone": True, "v_dc_V": 750.0, "i_phase_rms_A": 257.2,
           "f_elec_hz": 198.3, "f_carrier_hz": 8000.0, "modulation_index": 0.45,
           "power_factor": 0.6, "pwm_modulation": "svpwm",
           "device": "IMCQ120R004M2H", "devices_parallel": 1, "r_tim_k_w": 0.09,
           "r_g_ext_ohm": 10.0, "r_g_off_ext_ohm": 10.0,
           "cooling": {"mode": "liquid", "t_in_c": 40.0}}
    assert solve_controller(req)["thermal"]["t_j_max_c"] == pytest.approx(
        rev_a["thermal"]["t_j_max_c"], abs=0.05)
