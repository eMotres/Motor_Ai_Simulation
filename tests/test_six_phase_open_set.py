# -*- coding: utf-8 -*-
"""Six-phase winding with ONE SET OPEN (degraded operation, Erium 2026-10-31).

Pins
  * the bookkeeping: ``set_current_scale`` / ``with_open_set`` and their
    refusals — I_phase_rms of an open-set run is the ACTIVE set's current;
  * the NET-FORCE evaluator (Coulomb virtual work of a translation) against
    the force on a line current in a uniform field — sign and magnitude — and
    the torque evaluator on the same field (r x F);
  * the per-set arithmetic (current, resistance, copper, thermal basis);
  * the SOLVER on the pinned 30 mm machine (12 s / 14 p, 2 paths, set 1 =
    slots 1-6): a sector is refused, a voltage drive is refused, an explicit
    {1: 1, 2: 1} is the balanced run bit for bit, and on the full ring the
    open set carries no current, the copper is the active set's alone, the
    torque is about half and the rotor feels a net pull the healthy machine
    does not.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from motor_ai_sim.winding_sets import (SixPhaseError, is_degraded,
                                       resolve_six_phase, set_current_scale,
                                       with_open_set)


@pytest.fixture(scope="module")
def spec30():
    return resolve_six_phase({"phases": 6, "n_parallel": 2},
                             num_slots=12, num_poles=14, single_layer=True)


# ── bookkeeping ───────────────────────────────────────────────────────────

def test_balanced_spec_has_unit_scale(spec30):
    assert set_current_scale(spec30) == (1.0, 1.0)
    assert set_current_scale(None) == (1.0, 1.0)
    assert not is_degraded(spec30)
    assert not is_degraded(dict(spec30, set_current_scale={1: 1, "2": 1.0}))


def test_open_set_means_the_active_set_carries_i_phase(spec30):
    s = with_open_set(spec30, 2)
    assert set_current_scale(s) == (2.0, 0.0)
    assert s["open_set"] == 2 and s["active_set"] == 1
    assert "set 1" in s["I_phase_rms_meaning"]
    assert is_degraded(s)
    # JSON round trip (string keys) reads the same
    assert set_current_scale({"set_current_scale": {"1": 0, "2": 2}}) == (0.0, 2.0)
    assert with_open_set(spec30, "1")["set_current_scale"] == {2: 2.0, 1: 0.0}


@pytest.mark.parametrize("bad", [
    {1: -1.0}, {3: 1.0}, {1: float("nan")}, {1: 0.0, 2: 0.0}, {"x": 1}, [1, 0]])
def test_bad_scales_are_refused(bad):
    with pytest.raises(SixPhaseError):
        set_current_scale({"set_current_scale": bad})


def test_open_set_needs_six_phases_and_a_real_set(spec30):
    with pytest.raises(SixPhaseError):
        with_open_set(None, 2)
    for v in (0, 3, "a", None):
        with pytest.raises(SixPhaseError):
            with_open_set(spec30, v)


# ── the net-force evaluator, on a field whose answer is known ────────────

def test_force_on_a_line_current_in_a_uniform_field():
    """A wire carrying I (+z) at (x0, y0) inside a uniform B0·ŷ: the force on
    everything inside the layer is I·L·ẑ×B0ŷ = −I·L·B0 x̂ (the wire's own
    field exerts none on itself), and the torque about the axis is
    x0·Fy − y0·Fx = y0·I·L·B0."""
    from skfem import Basis, ElementTriP2, MeshTri
    from motor_ai_sim.simulation.virtual_work_torque import (
        MU0, build_layer, prepare_coulomb_force, prepare_coulomb_torque,
        radial_phi)
    mesh = MeshTri.init_circle(6)                 # unit disk, ~4k elements
    el = ElementTriP2()
    b = Basis(mesh, el)
    I, B0, L = 50.0, 0.7, 0.03
    x0, y0 = 0.11, 0.073
    X, Y = b.doflocs
    A = -B0 * X - MU0 * I / (2 * math.pi) * np.log(np.hypot(X - x0, Y - y0))
    r = np.hypot(mesh.p[0], mesh.p[1])
    lay = build_layer(mesh.p, mesh.t, radial_phi(r, 0.35, 0.7),
                      np.ones(mesh.t.shape[1], bool))
    fx, fy = prepare_coulomb_force(mesh, el, lay, L)(A)
    T = prepare_coulomb_torque(mesh, el, lay, L)(A)
    assert fx == pytest.approx(-I * L * B0, rel=2e-3)
    assert abs(fy) < 2e-3 * I * L * B0
    assert T == pytest.approx(y0 * I * L * B0, rel=5e-3)


def test_net_force_block_sector_and_full_ring():
    from motor_ai_sim.simulation.fem_solver_2d import _net_force_block
    assert _net_force_block([], full_ring=False)["available"] is False
    blk = _net_force_block([(3.0, 4.0, 5.0), (3.0, 4.0, 5.0)], full_ring=True)
    assert blk["available"] and blk["F_mean_mag_N"] == pytest.approx(5.0)
    assert blk["F_mean_angle_deg"] == pytest.approx(math.degrees(math.atan2(4, 3)), abs=0.01)
    assert blk["self_check_rel"] == pytest.approx(0.0)
    assert _net_force_block([None], full_ring=True)["available"] is False


def test_per_set_block_arithmetic():
    """Synthetic sine frames: set 1 at 2× the branch share (one set open), set
    2 open.  Current, resistance, copper split and the thermal basis."""
    from motor_ai_sim.simulation.fem_solver_2d import _six_degraded_block
    n, npar, ib, R = 24, 2, 10.0, 0.05            # branch amplitude 10 A
    th = np.linspace(0, 2 * math.pi, n, endpoint=False)
    IA = ib * np.cos(th); IB = ib * np.cos(th - 2 * math.pi / 3)
    IC = ib * np.cos(th + 2 * math.pi / 3)
    psi = [(t, 0.1 * math.cos(t), 0.08 * math.cos(t),
            0.1 * math.cos(t - 2.0944), 0.08 * math.cos(t - 2.0944),
            0.1 * math.cos(t + 2.0944), 0.08 * math.cos(t + 2.0944)) for t in th]
    blk = _six_degraded_block(
        scale=(2.0, 0.0), six_psi=psi, IA=IA, IB=IB, IC=IC,
        dts=np.full(n, 1e-4), n_parallel=npar, R_phase_bal=R, f_elec=100.0,
        set_connection="star", P_cu_dc=200.0, P_cu_total=230.0,
        P_ac_set={1: 20.0, 2: 10.0}, eddy=False)
    s1, s2 = blk["sets"]
    # set phase current = 2 × (npar/2) × branch = 20 A peak → 14.142 A rms
    assert s1["I_phase_rms_A"] == pytest.approx(20.0 / math.sqrt(2), rel=1e-6)
    assert s2["I_phase_rms_A"] == 0.0 and s2["open"] and not s1["open"]
    assert s1["R_phase_ohm"] == pytest.approx(2 * R)
    # P_cu_dc of the run (k = 2) is set 1's alone; set 2 carries no DC copper
    assert s1["P_cu_dc_W"] == pytest.approx(200.0) and s2["P_cu_dc_W"] == 0.0
    # every slot at set 1's density: 2 × (200 + 20)
    assert blk["P_cu_heat_uniform_W"] == pytest.approx(440.0)
    assert blk["copper_heat_uniform_factor"] == pytest.approx(440.0 / 230.0)
    # open set: pure EMF, ω·ψ1 = 2π·100·0.08
    assert s2["V1_phase_peak_V"] == pytest.approx(2 * math.pi * 100 * 0.08, rel=1e-6)
    assert s2["V1_LL_peak_V"] == pytest.approx(math.sqrt(3) * s2["V1_phase_peak_V"], rel=1e-3)
    assert s1["V_LL_peak_V"] > s1["V_phase_peak_V"]


# ── the solver, on the pinned 30 mm machine (12 s / 14 p) with 2 paths ────

RUN = dict(n_steps_per_period=6, n_periods=1.0, mesh_size_mm=1.4,
           min_size_mm=0.35, gap_layers=1.0, structured_gap=True,
           iron_template=True, geo_mesh=True, coil_temp_c=120.0,
           element_order=2, demag=False, eddy=False, rotor_eddy=False,
           gamma_deg=10.0, connection="2P")


def _solve(**kw):
    from motor_ai_sim.material_context import set_request_materials
    from motor_ai_sim.simulation.fem_solver_2d import fem_transient_sliding_band
    from tests.test_physics_regression import GEO_30MM, OVERRIDE, RPM
    set_request_materials(OVERRIDE)
    try:
        return fem_transient_sliding_band(geo_override=dict(GEO_30MM), rpm=RPM,
                                          **dict(RUN, **kw))
    finally:
        set_request_materials(None)


def test_open_set_refuses_a_sector_and_a_voltage_drive(spec30):
    with pytest.raises(ValueError, match="full-ring"):
        _solve(n_sectors=2, I_phase_rms=30.0, six_phase=with_open_set(spec30, 2))
    with pytest.raises(ValueError, match="imposed current"):
        _solve(n_sectors=1, I_phase_rms=30.0, six_phase=with_open_set(spec30, 2),
               drive="voltage", v_phase_peak=10.0)


def test_unit_scale_is_the_balanced_run_bit_for_bit(spec30):
    a = _solve(n_sectors=2, I_phase_rms=60.0, six_phase=spec30)
    b = _solve(n_sectors=2, I_phase_rms=60.0,
               six_phase=dict(spec30, set_current_scale={1: 1.0, 2: 1.0}))
    assert b["T_avg_Nm"] == a["T_avg_Nm"]
    assert b["psi_A_Wb"] == a["psi_A_Wb"]
    assert b["P_cu_dc_W"] == a["P_cu_dc_W"]
    assert "degraded" not in b["six_phase"]


@pytest.fixture(scope="module")
def ring_pair(spec30):
    """Healthy at 60 A terminal (30 A per set) and set 2 open with set 1 at
    its own 30 A — the same controller current, one controller lost."""
    bal = _solve(n_sectors=1, I_phase_rms=60.0, six_phase=spec30)
    deg = _solve(n_sectors=1, I_phase_rms=30.0, six_phase=with_open_set(spec30, 2))
    return bal, deg


def test_open_set_currents_and_copper(ring_pair):
    bal, deg = ring_pair
    d = deg["six_phase"]["degraded"]
    s1, s2 = d["sets"]
    assert s1["I_phase_rms_A"] == pytest.approx(30.0, rel=1e-3)
    assert s2["I_phase_rms_A"] == 0.0 and s2["open"]
    # the active set alone at 30 A in half the paths: half the healthy I²R
    assert deg["P_cu_dc_W"] == pytest.approx(0.5 * bal["P_cu_dc_W"], rel=1e-9)
    assert s1["P_cu_dc_W"] == pytest.approx(deg["P_cu_dc_W"], rel=1e-6)
    assert d["copper_heat_uniform_factor"] > 1.5
    assert deg["copper_heat_uniform_factor"] == d["copper_heat_uniform_factor"]


def test_open_set_torque_voltage_and_pull(ring_pair):
    bal, deg = ring_pair
    ratio = deg["T_avg_Nm"] / bal["T_avg_Nm"]
    assert 0.4 < ratio < 0.6, ratio
    d = deg["six_phase"]["degraded"]
    s1, s2 = d["sets"]
    # the open set still sees the magnets and set 1's field: a real EMF
    assert s2["V1_phase_peak_V"] > 0.3 * s1["V1_phase_peak_V"]
    fb, fd = bal["rotor_net_force"], deg["rotor_net_force"]
    assert fb["available"] and fd["available"]
    # healthy machine: no net pull; half the bore loaded: a pull of the order
    # of the tangential (2/π)·T/r_gap
    assert fd["F_mean_mag_N"] > 20.0 * max(fb["F_mean_mag_N"], 1e-6)
    assert fd["self_check_rel"] < 0.05
