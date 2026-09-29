"""Sweep load angle gamma on the CURRENT config; report T + efficiency. Finds MTPA.
Usage: PYTHONPATH=src python design_runs/sweep_gamma.py I rpm g0 g1 step [mesh] [n_sectors]
"""
import sys, math
from motor_ai_sim.routes.simulation import get_fem_transient

I = float(sys.argv[1]) if len(sys.argv) > 1 else 35.0
RPM = float(sys.argv[2]) if len(sys.argv) > 2 else 12000.0
g0 = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0
g1 = float(sys.argv[4]) if len(sys.argv) > 4 else 60.0
step = float(sys.argv[5]) if len(sys.argv) > 5 else 15.0
MESH = float(sys.argv[6]) if len(sys.argv) > 6 else 0.8
NSEC = int(sys.argv[7]) if len(sys.argv) > 7 else 2

omega = RPM * 2 * math.pi / 60.0
best = None
g = g0
while g <= g1 + 1e-6:
    res = get_fem_transient(n_steps_per_period=6, n_periods=1.0, gamma_deg=g, I_phase_rms=I,
                            mesh_size_mm=MESH, n_sectors=NSEC, sliding_band=True, rotor_eddy=False)
    sa = res.get("summary") or {}
    T = float(res.get("T_avg_Nm") or 0.0)
    eff = sa.get("efficiency", res.get("efficiency"))
    V = res.get("V_phase_peak_V") or res.get("V_peak")
    Pm = T * omega
    e = eff if isinstance(eff, (int, float)) else 0.0
    vs = f"{V:5.1f}" if isinstance(V, (int, float)) else str(V)
    print(f"  gamma={g:6.1f}  T={T:7.4f} Nm  Pmech={Pm:7.1f} W  eff={e:.4f}  Vpk={vs} V")
    if best is None or T > best[1]:
        best = (g, T, eff, Pm)
    g += step
print(f"MTPA: gamma={best[0]:.1f}  T={best[1]:.4f} Nm  Pmech={best[3]:.1f} W  eff={best[2]}")
