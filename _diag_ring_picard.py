"""Does tightening the ring's saturation Picard close the ripple gap vs the wedge?

Hypothesis (PARITY_FINDINGS_band_mode.md): the full ring's ~1% broadband
torque noise comes from the per-frame Picard converging to slightly
asymmetric saturation states (rel tol 2%, fixed iteration count) — the noise
damps the coherent h24/h36 cogging orders, understating ripple vs the
spectrally clean 1/4 wedge.

Test: ns=1, structured gap, steps=120, nonlinear_iterations 14 (default)
vs 28.  If noise floor drops and filtered ripple rises toward the wedge's
22.25 % → confirmed; the wedge is the honest ripple reference.
Usage: python _diag_ring_picard.py
"""
import json
import sys

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from motor_ai_sim.simulation.fem_solver_2d import fem_transient_sliding_band

out = {}
for n_pic in (14, 28):
    d = fem_transient_sliding_band(
        n_steps_per_period=120, n_periods=1.0, gamma_deg=28.0,
        I_phase_rms=100.0, mesh_size_mm=4.0, min_size_mm=0.3,
        outer_air_factor=1.2, gap_layers=3.0, n_sectors=-1,
        coil_temp_c=120.0, pole_copy=True, torque_filter=True,
        structured_gap=True, iron_template=False, geo_mesh=False,
        nonlinear_iterations=n_pic)
    x = np.asarray(d.get("T_em_raw_Nm") or d.get("T_em_Nm"), float)
    F = np.abs(np.fft.rfft(x - x.mean()) / x.size * 2.0)
    out[n_pic] = {
        "T_avg": d.get("T_avg_Nm"), "ripple": d.get("T_ripple_pct"),
        "ripple_raw": d.get("T_ripple_raw_pct"),
        "noise": d.get("T_noise_floor_pct"), "Vpk": d.get("V_peak"),
        "h6k": {k: round(float(F[k]), 3) for k in (6, 12, 18, 24, 30, 36) if k < F.size},
    }
    print(f"n_pic={n_pic}: T={out[n_pic]['T_avg']:.3f} "
          f"ripple={out[n_pic]['ripple']:.2f} noise={out[n_pic]['noise']} "
          f"h6k={out[n_pic]['h6k']}")

json.dump(out, open("_ring_picard.json", "w"))
print("RESULT_JSON " + json.dumps(out))
