"""The optimizer must not score a candidate on a field that never converged.

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


def test_eval_cache_rejects_an_unconverged_result():
    """The sweep cache must neither store nor replay an unconverged eval."""
    assert _eval_healthy({"ok": True, "res": {"T_em_Nm": 0.42,
                                              "nonlinear_converged": True}})
    assert not _eval_healthy({"ok": True, "res": {"T_em_Nm": 0.42,
                                                  "nonlinear_converged": False}})
    # Pre-gate lines have no stamp: kept (unverified), not rejected.
    assert _eval_healthy({"ok": True, "res": {"T_em_Nm": 0.42}})
    assert not _eval_healthy({"ok": True, "res": {"T_em_Nm": float("nan")}})
