"""Drive the FEM transient from an EXTERNAL controller, in ~30 lines.

``fem_transient_sliding_band`` takes its excitation from a pluggable SOURCE
object (``simulation/excitation.py``).  The five built-in drives are just five
implementations of that interface, so anything else that can answer "what mean
voltage do I apply over this step, given the last step's measured currents" is
a first-class excitation too — a co-simulated controller, a measured inverter
log, a hardware model-in-the-loop rig.

This demo is the smallest interesting one: a synchronous-frame PI current
regulator closing on i_q, reading the PREVIOUS converged step's phase currents
out of the :class:`Feedback` (which is exactly the one-sample delay a real DSP
has) and returning the per-step mean phase voltages the Crank-Nicolson circuit
integrates.  Everything else — saturation, the real non-sinusoidal back-EMF,
the eddy reaction — is in the loop, because the FEM solver is unchanged.

Solver-direct on purpose: no HTTP route, no config write, warm cache off.

    python scripts/excitation_demo.py
"""
from __future__ import annotations

import math
import os
import sys

os.environ.setdefault("SB_NO_WARM_CACHE", "1")     # never touch config/

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from motor_ai_sim.material_context import set_request_materials
from motor_ai_sim.simulation.drive import inverse_park, park
from motor_ai_sim.simulation.excitation import Feedback, SettlePolicy
from motor_ai_sim.simulation.fem_solver_2d import fem_transient_sliding_band


# ── THE SOURCE ───────────────────────────────────────────────────────────────
class PiCurrentController:
    """Synchronous-frame PI on (i_d, i_q), applying phase voltages."""

    kind = "V"                 # the currents are circuit UNKNOWNS, solved with the field
    name = "pi_foc_demo"
    rms_from_series = True     # copper loss comes from the SOLVED current, not a config rms
    v_bus = 48.0

    def __init__(self, pole_pairs, daxis_deg, iq_ref_A, kp=0.02, ki=400.0,
                 v_limit=12.0):
        self.pole_pairs, self.daxis_deg = int(pole_pairs), float(daxis_deg)
        self.iq_ref, self.kp, self.ki, self.v_lim = iq_ref_A, kp, ki, v_limit
        self._xd = self._xq = 0.0                    # integrator state

    def _theta(self, theta_deg):
        # −90° puts park's q on THIS solver's torque axis: its excitation is
        # i_A = I·cos(θ_e + γ) with γ = 0 the q-axis (drive.Excitation), which
        # is park's D component at θ_e.  Rotate the controller frame and iq_ref
        # means the same thing γ does everywhere else in the app.
        return math.radians(theta_deg * self.pole_pairs + self.daxis_deg - 90.0)

    def _dq_command(self, theta_deg, i_abc, dt_s):
        the = self._theta(theta_deg)
        i_d, i_q = park(i_abc["A"], i_abc["B"], i_abc["C"], the) if i_abc else (0.0, 0.0)
        self._xd = _clip(self._xd + self.ki * (0.0 - i_d) * dt_s, self.v_lim)
        self._xq = _clip(self._xq + self.ki * (self.iq_ref - i_q) * dt_s, self.v_lim)
        v_d = _clip(self.kp * (0.0 - i_d) + self._xd, self.v_lim)
        v_q = _clip(self.kp * (self.iq_ref - i_q) + self._xq, self.v_lim)
        return dict(zip("ABC", inverse_park(v_d, v_q, the)))

    def mean_over(self, fb: Feedback):               # ← the one required signal
        return self._dq_command(0.5 * (fb.theta_deg + fb.theta_prev_deg),
                                fb.i_abc, max(fb.dt_s, 1e-12))

    def fundamental(self, theta_deg):                # smooth reference, no state kick
        return dict(zip("ABC", inverse_park(self._xd, self._xq,
                                            self._theta(theta_deg))))

    def nominal_currents(self, theta_deg):           # what build_materials sizes on
        return dict(zip("ABC", inverse_park(0.0, self.iq_ref,
                                            self._theta(theta_deg))))

    def settle_policy(self):
        return SettlePolicy(periods_static=2, aitken=True)

    def describe(self, ctx=None):
        return {"name": self.name, "series": "V",
                "quantity": "PI-regulated phase voltage [V]",
                "v_phase_peak_V": float(self.v_lim), "v_delta_deg": 0.0,
                "pwm": None, "custom_current": None, "bldc": None}


def _clip(x, lim):
    return max(-lim, min(lim, x))


# ── RUN IT ON THE REGRESSION SUITE'S MACHINE ─────────────────────────────────
def main() -> int:
    from tests.test_physics_regression import (
        COMMON, CONNECTION, GEO_30MM, OVERRIDE, RPM)

    pole_pairs = int(GEO_30MM["num_poles_per_segment"])   # 7
    src = PiCurrentController(pole_pairs=pole_pairs, daxis_deg=60.0,
                              iq_ref_A=60.0 * math.sqrt(2))
    kw = dict(COMMON)
    kw.update(element_order=2, demag=False, n_steps_per_period=12,
              n_periods=1.0, I_phase_rms=60.0, gamma_deg=0.0)
    set_request_materials(OVERRIDE)
    try:
        d = fem_transient_sliding_band(geo_override=dict(GEO_30MM), rpm=RPM,
                                       connection=CONNECTION,
                                       excitation=src, **kw)
    finally:
        set_request_materials(None)

    diag = d.get("v_drive_diag") or {}
    print("drive reported      :", d.get("drive"))
    print("T_avg               : %.6f Nm" % float(d.get("T_avg_Nm") or 0.0))
    print("I_phase solved      : %.3f A rms" % float(d.get("I_phase_rms_solved_A") or 0.0))
    print("circuit resid (max) : %.3e V" % max((diag.get("resid") or [0.0])))
    print("DC residual         : %s A (phase %s, tol %s A, unconverged %s)"
          % (d.get("v_dc_residual_A"), d.get("v_dc_residual_phase"),
             d.get("v_dc_residual_tol_A"), d.get("v_dc_unconverged")))
    print("applied V_A (first 4): %s"
          % [round(v, 4) for v in (d.get("excitation") or {}).get("A", [])[:4]])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
