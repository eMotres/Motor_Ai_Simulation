"""The PWM DC-orbit solve on the real solver (30 mm regression fixture).

The synthetic proof is tests/test_dc_orbit.py; this is the FEM half:

* every settling period the solve reads is a WHOLE electrical period.  The CN
  rows summed over one telescope to  drift = Σ D·v·Δt − R·T·S·ī  and the ideal
  synchronous bridge applies zero volt-seconds per period, so the flux drift the
  solve measures must equal −R·T·S·(the period's trapezoidal DC).  A window one
  step too long — the handover step of the mixed schedule used to be one COARSE
  step (P + Δθ_f) — adds the orbit's own flux motion over that step and breaks
  the identity by orders of magnitude (measured: 1.1e-4 Wb against 6e-6);
* the reported window opens on the orbit: the old period-mean anchor left
  0.55 A of DC here (72 steps, 9 carriers), a free settle 0.025 A.
"""
from __future__ import annotations

import numpy as np
import pytest

from motor_ai_sim.material_context import set_request_materials
from motor_ai_sim.simulation.fem_solver_2d import fem_transient_sliding_band

from tests.test_physics_regression import (COMMON, CONNECTION, GEO_30MM,
                                           OVERRIDE, RPM)

_S = np.array([[1.0, -1.0], [1.0, 2.0]])
F_ELEC = RPM / 60.0 * 7.0          # 14 poles


@pytest.fixture(scope="module")
def pwm_run():
    kw = dict(COMMON, element_order=2, demag=False, I_phase_rms=60.0,
              gamma_deg=0.0, drive="pwm_voltage", v_phase_peak=7.0,
              v_delta_deg=10.0, v_bus=20.0,
              # 6 carriers x 8 steps: mixed schedule, 24 coarse steps, ratio 2
              f_switch=6.0 * F_ELEC, n_steps_per_period=48)
    set_request_materials(OVERRIDE)
    try:
        return fem_transient_sliding_band(geo_override=dict(GEO_30MM),
                                          rpm=RPM, connection=CONNECTION, **kw)
    finally:
        set_request_materials(None)


def test_every_solved_period_is_whole(pwm_run):
    orb = pwm_run["v_dc_orbit"]
    assert orb and orb["periods"], "the PWM run did not solve its DC orbit"
    R = float(pwm_run["R_phase_ohm"])
    T = 1.0 / F_ELEC
    for rec in orb["periods"]:
        drift = np.asarray(rec["drift_Wb"])
        dc = np.asarray(rec["dc_A"][:2])
        want = -R * T * (_S @ dc)
        # The identity is exact for the CN state; the logged DC's first
        # trapezoid term uses the SOLVED current of the frame before (the
        # first period has none, and a corrected state moves it), hence the
        # tolerance — which is still three orders tighter than a crooked
        # window's error.
        tol = 0.15 * float(np.max(np.abs(drift))) + 2e-7
        assert np.max(np.abs(drift - want)) < tol, (rec["frame"], drift, want)


def test_the_handover_step_is_on_the_fine_grid(pwm_run):
    ms = pwm_run["pwm"]["mixed_settle"]
    assert ms is not None and ms["coarse_steps_per_period"] == 24
    # two whole fine periods + the ONE fine frame that completes the last
    # coarse step (ratio 2)
    assert ms["fine_settle_frames"] == 2 * 48 + 1
    first_fine = [r for r in pwm_run["v_dc_orbit"]["periods"]
                  if r["resolution"] == "fine"][0]
    # the turn-on predictor was applied at the coarse -> fine boundary
    coarse = [r for r in pwm_run["v_dc_orbit"]["periods"]
              if r["resolution"] == "coarse"]
    assert "handover_prediction_Wb" in coarse[-1]
    assert first_fine["correction_Wb"] is not None


def test_the_window_opens_on_the_orbit(pwm_run):
    orb = pwm_run["v_dc_orbit"]
    assert orb["refused"] == 0
    assert orb["corrections"] == len(orb["periods"]) - 1
    assert orb["periods"][-1]["verdict"] == "free (verification)"
    assert abs(pwm_run["v_dc_residual_A"]) < 0.02, pwm_run["v_dc_residual_A"]
    assert pwm_run["v_dc_unconverged"] is False
