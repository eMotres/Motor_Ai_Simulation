"""Fillet study: how rotor-side and stator-side fillet radii move the HONEST
(filtered, clean-mesh, 1/4-wedge) torque ripple and its cogging harmonics.

Baseline (current config): magnet_fill_radius=1.0, rotor_fill_r=0.0,
stator_fillet_r=3.5, stator_fillet_r1=0.5 -> T=29.46, ripple 24.36 %,
h12=2.17 (cogging dominates).

Runs one variant at a time via geo_override (config untouched).
"""
import json
import sys

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from motor_ai_sim.simulation.fem_solver_2d import em_transient_eval

VARIANTS = [
    # (label, override)
    ("magnet_fill 0.3", {"magnet_fill_radius": 0.3}),
    ("magnet_fill 0.6", {"magnet_fill_radius": 0.6}),
    ("rotor_fill_r 0.3", {"rotor_fill_r": 0.3}),
    ("stator_r 2.0", {"stator_fillet_r": 2.0}),
    ("stator_r 5.0", {"stator_fillet_r": 5.0}),
    ("stator_r1 0.2", {"stator_fillet_r1": 0.2}),
    ("stator_r1 1.0", {"stator_fillet_r1": 1.0}),
]

out = {}
for label, ov in VARIANTS:
    try:
        d = em_transient_eval(
            n_steps_per_period=120, n_periods=1.0, gamma_deg=32.0,
            I_phase_rms=100.0, mesh_size_mm=2.8, min_size_mm=0.3,
            outer_air_factor=1.2, gap_layers=3.0, n_sectors=4,
            coil_temp_c=120.0, pole_copy=True, torque_filter=True,
            structured_gap=True, iron_template=True, geo_mesh=True,
            geo_override=ov)
        x = np.asarray(d.get("T_em_raw_Nm") or d.get("T_em_Nm"), float)
        F = np.abs(np.fft.rfft(x - x.mean()) / x.size * 2.0)
        h = {k: round(float(F[k]), 3) for k in (6, 12, 18, 24, 30, 36) if k < F.size}
        out[label] = {"T": round(float(d["T_avg_Nm"]), 2),
                      "ripple": round(float(d["T_ripple_pct"]), 2),
                      "noise": d.get("T_noise_floor_pct"),
                      "Vpk": round(float(d["V_peak"]), 1), "h": h}
        print(f"{label}: T={out[label]['T']} ripple={out[label]['ripple']}% "
              f"h12={h.get(12)} h24={h.get(24)}", flush=True)
    except Exception as e:
        out[label] = {"error": str(e)[:200]}
        print(f"{label}: ERROR {e}", flush=True)

json.dump(out, open("_fillet_sweep.json", "w"), indent=1)
print("RESULT_JSON " + json.dumps(out))
