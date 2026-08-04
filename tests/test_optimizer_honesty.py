"""Honesty tests for the optimizer's two inputs: the FEM eval and the surrogate.

Part 1 — the optimizer must not score a candidate on a field that never
converged.  Part 2 — the analytic surrogate that seeds Pareto/DoE ranking must
read its topology and materials from the DESIGN, reproduce the pinned FEM at its
calibration anchor, and carry its own measured uncertainty.

``refine_proc.run_one`` is the ONE path every FEM eval takes — the Pareto search,
the DoE sweep and the descent all reach the solver through it (via
``routes/optimization._subprocess_eval``).  It read the solver's convergence
flags and dropped them on the floor, so a frame whose nonlinear solve never met
its tolerance still produced a torque, a ripple and an efficiency, and those
numbers could win a run.  These tests pin the gate: unconverged -> the eval
FAILS (same class as an infeasible build), converged -> the eval carries the
stamp so the result can be told apart from a pre-gate cache line.

The solver is faked here on purpose: the point under test is the bookkeeping in
run_one, and a real transient would make this a 10-minute test nobody runs.
"""
from __future__ import annotations

import types

import pytest

from motor_ai_sim.material_context import set_request_materials
from motor_ai_sim.optimization import refine_proc
from motor_ai_sim.routes.optimization import _eval_healthy

# Same 30 mm 12s14p spoke machine the physics regression pins, so this suite
# does not inherit whatever is in the working config either.
GEO = {
    "stator_diameter": 30.0, "slot_height": 4.3, "core_thickness": 1.5,
    "num_seg": 2, "num_slots_per_segment": 6, "num_poles_per_segment": 7,
    "air_gap": 0.2, "tooth_width": 2.6, "tooth2_width": 1.4, "cut_width": 1.5,
    "insulation_thickness": 0.05, "wire_width": 2.0, "wire_height": 0.5,
    "wire_spacing_x": 0.1, "wire_spacing_y": 0.1, "num_wires_per_slot": 6,
    "wire_split": 1, "slot_hs": 0.267, "magnet_height": 4.5,
    "rotor_house_height": 0.8, "shaft_height": 2.0, "magnet_fill_down": 0.9,
    "magnet_fill_up": 0.3, "magnet_fill_radius": 0.1, "magnet_up_gap": 0.1,
    "rotor_hole": 0.7, "magnet_down_height": 1.4, "magnet_lamination": 0,
    "stator_fillet_r": 1.2, "stator_fillet_r1": 0.0, "rotor_fill_r": 0.2,
    "motor_length": 10.0,
}

CFG = {"geometry": dict(GEO), "simulation": {"rpm": 3950.0, "demag": False},
       "winding": {"n_parallel": 1, "n_series": 1}, "mesh": {},
       "materials": {}, "magnet": {}, "rotor": {}, "stator": {}}


def _raw(converged: bool) -> dict:
    """A minimal finished-transient dict — only what run_one reads."""
    n = 12
    return {
        "n_steps": n,
        "T_avg_Nm": 0.4174, "T_ripple_pct": 0.53, "V_peak": 7.83,
        "P_cu_W": [91.2] * n, "P_fe_W": [3.12] * n,
        "P_mag_eddy_W": [0.0] * n, "P_shaft_eddy_W": [0.0] * n,
        "P_cu_ac_W": [0.0] * n, "P_cu_dc_W": 91.2,
        "P_elec_in_W": 260.0, "P_mech_avg_W": 165.0,
        "picard_converged": converged,
        "picard_unconverged_frames": ([] if converged else [3, 7]),
        "picard_resid_max": (1.2e-7 if converged else 4.5e-3),
        "picard_tol": 1e-4,
    }


@pytest.fixture()
def fake_solver(monkeypatch):
    """Point run_one at a fixed config and a fake kernel returning ``raw``."""
    import motor_ai_sim.config as _cfgmod

    def _install(raw: dict):
        monkeypatch.setattr(_cfgmod, "get_config", lambda *a, **k: CFG)
        fake = types.SimpleNamespace(
            run=lambda cap, args: {
                "ok": True, "result": types.SimpleNamespace(raw=raw)})
        monkeypatch.setattr(refine_proc, "_kernel", lambda: fake)
    return _install


def test_unconverged_frame_fails_the_eval(fake_solver):
    """An unconverged window must raise, and must name the frames."""
    fake_solver(_raw(converged=False))
    with pytest.raises(RuntimeError) as ei:
        refine_proc.run_one({}, current_a=60.0, steps=12, coil_temp_c=120.0)
    msg = str(ei.value)
    assert "unconverged FEM frames" in msg
    assert "[3, 7]" in msg          # the frame list travels with the failure
    assert "4.5e-03" in msg or "0.0045" in msg   # the residual it failed at


def test_converged_eval_carries_the_stamp(fake_solver):
    """A healthy eval still returns numbers, plus the convergence provenance."""
    fake_solver(_raw(converged=True))
    res = refine_proc.run_one({}, current_a=60.0, steps=12, coil_temp_c=120.0)
    assert res["T_em_Nm"] == pytest.approx(0.417, abs=1e-3)
    assert res["nonlinear_converged"] is True
    assert res["nonlinear_resid_max"] == pytest.approx(1.2e-7, rel=1e-6)
    assert res["nonlinear_tol"] == pytest.approx(1e-4, rel=1e-6)


# ── the Compare row and the Simulation card must use ONE convention ─────────
# Two numbers on every optimizer result were computed the way the Simulation
# card computed them BEFORE 2026-08-04, and the card moved (bef2ed2 KV,
# e08f4af efficiency) while this file could not be touched.  A design that is
# picked in Optimize and re-run in Simulation has to report the same η and the
# same KV, or the two tabs disagree about the machine they both just solved.

_OMEGA = 2.0 * 3.141592653589793 * CFG["simulation"]["rpm"] / 60.0


def test_efficiency_divides_by_every_loss_the_row_reports(fake_solver):
    """η = T·ω / (T·ω + Σ losses) — reproducible from the row by hand.

    It used to be P_mech_avg_W / P_elec_in_W.  The solved terminal power carries
    only the losses that live INSIDE the field solve, so it flattered every
    candidate by the post-processed remainder (analytic iron + end-winding
    copper) — and it flattered them UNEQUALLY, because that remainder is a
    different share of the total on a machine with more iron.
    """
    raw = _raw(converged=True)
    # separated on purpose: the old formula would read 0.95 here, the new one
    # ~0.65, so this cannot pass by coincidence.
    raw.update({"P_elec_in_W": 200.0, "P_mech_avg_W": 190.0})
    fake_solver(raw)
    res = refine_proc.run_one({}, current_a=60.0, steps=12, coil_temp_c=120.0)
    pmech = 0.4174 * _OMEGA
    ploss = 91.2 + 3.12                     # cu + fe (+ mag/shaft, both 0 here)
    assert res["efficiency"] == pytest.approx(pmech / (pmech + ploss), rel=1e-4)
    assert res["P_loss_total_W"] == pytest.approx(ploss, abs=0.05)
    # …and specifically NOT the old solved-terminal ratio.
    assert abs(res["efficiency"] - 190.0 / 200.0) > 0.2


def test_mech_power_is_torque_times_speed_and_the_balance_stays_as_a_diagnostic(
        fake_solver):
    """The row's power tile IS its torque tile times speed.

    η above is only reproducible from the row if the power in it is the same
    power η divided by.  The solver's energy-balanced P_mech_avg_W survives
    beside it, named for what it is."""
    fake_solver(_raw(converged=True))
    res = refine_proc.run_one({}, current_a=60.0, steps=12, coil_temp_c=120.0)
    pmech = 0.4174 * _OMEGA
    assert res["P_mech_W"] == pytest.approx(pmech, abs=0.05)
    assert res["P_mech_balance_W"] == pytest.approx(165.0, abs=0.05)
    assert res["P_elec_in_solved_W"] == pytest.approx(260.0, abs=0.05)
    # the row reproduces its own efficiency (to the rounding it is stored at)
    assert res["efficiency"] == pytest.approx(
        res["P_mech_W"] / (res["P_mech_W"] + res["P_loss_total_W"]), rel=5e-3)
    assert res["power_per_mass_W_kg"] == pytest.approx(
        res["P_mech_W"] / res["mass_total_kg"], rel=5e-3)


def test_kv_divides_by_the_line_voltage_PEAK_the_row_shows(fake_solver):
    """KV = rpm / V_line_peak — max/max, the convention of the cell beside it.

    It divided by the FUNDAMENTAL line-to-line RMS, which reads ~√2 higher and
    contradicted a by-hand rpm / V_LINE PEAK check off the same row.  With no
    per-phase waveforms in the result the line peak falls back to √3·V_peak,
    which is what this synthetic dict exercises."""
    fake_solver(_raw(converged=True))
    res = refine_proc.run_one({}, current_a=60.0, steps=12, coil_temp_c=120.0)
    v_line_peak = 7.83 * 3 ** 0.5
    assert res["V_line_peak_V"] == pytest.approx(v_line_peak, abs=0.05)
    assert res["KV_rpm_per_V_line"] == pytest.approx(
        CFG["simulation"]["rpm"] / v_line_peak, rel=1e-3)
    # the row reproduces its own KV (to the 0.1 V the peak is stored at)
    assert res["KV_rpm_per_V_line"] == pytest.approx(
        CFG["simulation"]["rpm"] / res["V_line_peak_V"], rel=5e-3)


def test_kv_uses_the_measured_line_waveform_when_the_run_carries_one(fake_solver):
    """The real path: triplens cancel line-to-line, so the measured |V_A−V_B|
    peak is BELOW √3·V_phase_peak and KV must be computed on the measured one."""
    import numpy as np
    n = 360                      # fine enough that the sampled peaks ARE the peaks
    t = np.arange(n) / n * 2 * np.pi
    raw = _raw(converged=True)
    # a phase waveform with a fat triplen — the case the √3 shortcut gets wrong
    va = 7.0 * np.sin(t) - 2.0 * np.sin(3 * t)
    vb = 7.0 * np.sin(t - 2 * np.pi / 3) - 2.0 * np.sin(3 * t)
    vc = 7.0 * np.sin(t + 2 * np.pi / 3) - 2.0 * np.sin(3 * t)
    raw.update({"V_A": va.tolist(), "V_B": vb.tolist(), "V_C": vc.tolist(),
                "V_peak": float(np.max(np.abs(va)))})
    fake_solver(raw)
    res = refine_proc.run_one({}, current_a=60.0, steps=12, coil_temp_c=120.0)
    want = float(max(np.max(np.abs(p)) for p in (va - vb, vb - vc, vc - va)))
    assert res["V_line_peak_V"] == pytest.approx(want, abs=0.05)
    assert want < 3 ** 0.5 * raw["V_peak"], "the triplen has to actually cancel"
    assert res["KV_rpm_per_V_line"] == pytest.approx(
        CFG["simulation"]["rpm"] / want, rel=1e-3)


def test_surrogate_reproduces_its_anchor():
    """The four calibration scalars are DERIVED from the pinned FEM cases, so at
    the anchor the surrogate must land ON the pins — not near them.

    What this catches: the calibration forces the anchor's magnet and steel,
    while evaluate_design resolves them from the request/config context — so if
    the material-resolution path breaks, or the two paths stop agreeing, the
    surrogate stops landing on the pin and this fails immediately.

    What it does NOT catch (stated so nobody reads more into a green tick): the
    scalars are k = pin / raw(anchor), so torque/iron/magnet/cogging reproduce
    the pin by construction. In particular it cannot detect that _ANCHOR_OP's
    rpm has drifted from the config rpm the baseline was generated at — the
    regression suite takes rpm from config, which is a fragility of that suite,
    not something this test can see. Independent accuracy is measured, not
    asserted: scripts/_surrogate_uncertainty.py."""
    from motor_ai_sim.optimization import design_eval as de

    set_request_materials({"assignment": {"magnet": de._ANCHOR_MAGNET},
                           "materials": {}})
    try:
        m = de.evaluate_design(dict(de._ANCHOR_GEO), dict(de._ANCHOR_WIND), {},
                               de._ANCHOR_OP["gamma_deg"],
                               de._ANCHOR_OP["current_a"],
                               de._ANCHOR_OP["rpm"],
                               coil_temp_c=de._ANCHOR_OP["coil_temp_c"])
    finally:
        set_request_materials(None)
    pins = de._anchor_pins()
    assert m.T_em_Nm == pytest.approx(pins["T_avg_Nm"], rel=1e-6)
    assert m.P_fe_W == pytest.approx(pins["P_fe_W"], rel=1e-6)
    assert m.P_mag_W == pytest.approx(pins["P_mag_W"], rel=1e-6)
    assert m.T_ripple_pct == pytest.approx(pins["T_ripple_pct"], rel=1e-4)
    # Copper is NOT calibrated — it shares the solver's formula — so its
    # agreement with the pin is an independent check that the shared physics
    # still lines up.  91.14 W pinned vs ~87.7 W here: 3.8 %, end-winding model.
    # (Both now size the conductor on the CAD polygons, so this gap is the
    # end-winding model alone — it is no longer partly a copper-area mismatch.)
    assert m.P_cu_W == pytest.approx(91.135, rel=0.05)


def test_surrogate_reads_topology_and_materials_from_the_design():
    """k_w and Br must come from the machine, not from a retired machine's literal."""
    from motor_ai_sim.optimization import design_eval as de

    # Star of slots on the real layout, cross-checked against published FSCW
    # winding factors.  The old literal was 0.933 (the double-layer value).
    assert de._winding_factor(12, 8, True) == pytest.approx(0.866, abs=1e-3)
    assert de._winding_factor(12, 14, True) == pytest.approx(0.966, abs=1e-3)
    assert de._winding_factor(12, 10, True) == pytest.approx(0.966, abs=1e-3)
    assert abs(de._winding_factor(12, 14, True) - 0.933) > 0.02

    set_request_materials({"assignment": {"magnet": de._ANCHOR_MAGNET},
                           "materials": {}})
    try:
        Br, mu_rec, sigma, src = de._magnet_props()
    finally:
        set_request_materials(None)
    assert src == de._ANCHOR_MAGNET
    assert Br == pytest.approx(1.19, abs=1e-6)   # the old literal was 1.23
    assert mu_rec == pytest.approx(1.05, abs=1e-3)
    assert sigma > 0


def test_surrogate_reports_its_own_uncertainty():
    """An absolute number from this model has to arrive with its error bar."""
    from motor_ai_sim.optimization import design_eval as de

    m = de.evaluate_design(dict(de._ANCHOR_GEO), dict(de._ANCHOR_WIND), {},
                           0.0, 60.0, 15000.0)
    assert m.cal_anchor and "12s14p" in m.cal_anchor
    assert "WARNING" not in m.cal_anchor          # anchor materials resolved
    assert m.T_em_uncertainty_pct > 0             # measured, not a sentinel
    assert m.T_ripple_uncertainty_pct > m.T_em_uncertainty_pct
    assert m.k_w > 0 and m.Br_T > 0 and m.material_source
    assert "T_em_uncertainty_pct" in m.to_dict()  # survives into the API payload


def test_surrogate_copper_uses_the_copper_the_cad_builds():
    """P_cu must be sized on the MEASURED conductor section, not the nominal
    rectangle the design asked for.

    ``wire_width x wire_height x num_wires_per_slot`` is design INTENT; the CAD
    clips the stack to what the slot can hold.  The surrogate used the intent, so
    it under-reported copper loss by the square of the shortfall — hardest on
    exactly the candidates an optimizer is drawn to, the ones that pack more
    copper in than fits.  The solver has measured the polygons since 293de43;
    this is the same measurement on the same helper.

    The anchor is the control (its CAD delivers the nominal section to 3 ulp, so
    the number must not move at all) and the over-packed slot is the case that
    used to lie.
    """
    from motor_ai_sim.optimization import design_eval as de

    nom = lambda g: (12 * float(g["num_wires_per_slot"])
                     * float(g["wire_width"]) * 1e-3
                     * float(g["wire_height"]) * 1e-3)

    # Control: 6 wires fit, so measured == nominal and nothing moves.
    anchor = dict(de._ANCHOR_GEO)
    a6 = de._measured_copper_area_m2(anchor)
    assert a6 == pytest.approx(nom(anchor), rel=1e-12)

    # Over-packed: ask for 12 wires in the same slot.  The CAD returns the SAME
    # copper it returned for 6 — the extra six wires do not exist.
    packed = dict(de._ANCHOR_GEO, num_wires_per_slot=12)
    a12 = de._measured_copper_area_m2(packed)
    assert a12 == pytest.approx(a6, rel=1e-9)
    assert a12 == pytest.approx(0.5 * nom(packed), rel=1e-9)

    # P = N^2 rho L k_end I^2 / A_cu: same A_cu, twice the turns → 4x the loss.
    # The nominal rectangle would have reported 2x, i.e. half the real number.
    m6 = de.evaluate_design(anchor, dict(de._ANCHOR_WIND), {}, 0.0, 60.0, 15000.0,
                            coil_temp_c=120.0)
    m12 = de.evaluate_design(packed, dict(de._ANCHOR_WIND), {}, 0.0, 60.0, 15000.0,
                             coil_temp_c=120.0)
    assert m12.P_cu_W == pytest.approx(4.0 * m6.P_cu_W, rel=1e-9)

    # Provenance travels with the number, and into the API payload.
    assert "CAD polygons" in m12.copper_area_source
    assert "copper_area_source" in m12.to_dict()


def test_eval_cache_rejects_an_unconverged_result():
    """The sweep cache must neither store nor replay an unconverged eval."""
    assert _eval_healthy({"ok": True, "res": {"T_em_Nm": 0.42,
                                              "nonlinear_converged": True}})
    assert not _eval_healthy({"ok": True, "res": {"T_em_Nm": 0.42,
                                                  "nonlinear_converged": False}})
    # Pre-gate lines have no stamp: kept (unverified), not rejected.
    assert _eval_healthy({"ok": True, "res": {"T_em_Nm": 0.42}})
    assert not _eval_healthy({"ok": True, "res": {"T_em_Nm": float("nan")}})
