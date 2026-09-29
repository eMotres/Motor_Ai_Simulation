"""Parity on the GEO (CDT, real fillets) pipeline: ns=1 vs ns=4, structured gap.

The geo tile builds both from bit-identical slot/pole cells — expect the
tightest parity of all pipelines.  Usage: python _cmp_geo_parity.py [steps]
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
        structured_gap=True, iron_template=True, geo_mesh=True)
    x = np.asarray(d.get("T_em_raw_Nm") or d.get("T_em_Nm"), float)
    F = np.abs(np.fft.rfft(x - x.mean()) / x.size * 2.0)
    out[ns] = {"T_avg": d.get("T_avg_Nm"), "ripple": d.get("T_ripple_pct"),
               "noise": d.get("T_noise_floor_pct"), "Vpk": d.get("V_peak"),
               "h6k": {k: round(float(F[k]), 3) for k in (6, 12, 18, 24, 30, 36) if k < F.size}}
    print(f"ns={ns}: T={out[ns]['T_avg']:.3f} ripple={out[ns]['ripple']:.2f} "
          f"noise={out[ns]['noise']} Vpk={out[ns]['Vpk']:.1f} h6k={out[ns]['h6k']}")

json.dump(out, open("_geo_parity.json", "w"))
print("RESULT_JSON " + json.dumps(out))
