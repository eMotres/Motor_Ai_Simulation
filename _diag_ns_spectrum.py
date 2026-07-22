"""Dump torque series for ns=1 vs ns=4 (structured gap) and compare spectra.

Calls em_transient_eval directly (raw sbres with T series).
Usage: python _diag_ns_spectrum.py [steps]
Writes _ns_spectrum.json and prints per-order amplitudes.
"""
import json
import sys

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from motor_ai_sim.simulation.fem_solver_2d import em_transient_eval

STEPS = int(sys.argv[1]) if len(sys.argv) > 1 else 120
out = {}
for ns in (1, 4):
    d = em_transient_eval(
        n_steps_per_period=STEPS, n_periods=1.0, gamma_deg=28.0,
        I_phase_rms=100.0, mesh_size_mm=4.0, min_size_mm=0.3,
        outer_air_factor=1.2, gap_layers=3.0, n_sectors=ns,
        coil_temp_c=120.0, pole_copy=True, torque_filter=True,
        structured_gap=True, iron_template=False, geo_mesh=False)
    out[ns] = {"T_raw": d.get("T_em_raw_Nm") or d.get("T_em_Nm"),
               "T_avg": d.get("T_avg_Nm"),
               "ripple": d.get("T_ripple_pct"),
               "ripple_raw": d.get("T_ripple_raw_pct"),
               "noise": d.get("T_noise_floor_pct"),
               "Vpk": d.get("V_peak")}

with open("_ns_spectrum.json", "w") as f:
    json.dump(out, f)

print(f"{'order':>5} {'ns=1 amp':>10} {'ns=4 amp':>10}   (Nm, one electrical period)")
spec = {}
for ns in (1, 4):
    x = np.asarray(out[ns]["T_raw"], float)
    spec[ns] = np.abs(np.fft.rfft(x - x.mean()) / x.size * 2.0)
n_show = min(len(spec[1]), len(spec[4]), 37)
for k in range(1, n_show):
    a1, a4 = spec[1][k], spec[4][k]
    mark = "  <-- 6k" if k % 6 == 0 else ""
    if max(a1, a4) > 0.01:
        print(f"{k:>5} {a1:>10.3f} {a4:>10.3f}{mark}")
for ns in (1, 4):
    o = out[ns]
    print(f"ns={ns}: T_avg={o['T_avg']:.3f} ripple={o['ripple']} "
          f"raw={o['ripple_raw']} noise={o['noise']} Vpk={o['Vpk']}")
