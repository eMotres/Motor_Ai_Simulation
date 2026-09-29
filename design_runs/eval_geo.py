"""FEM-evaluate one geometry override on a motor, sweeping gamma to find its MTPA.
Reusable final-confirmation tool. Writes the motor's final.yaml into config first,
overlays the override, sweeps gamma, prints the best (T, eff, ripple, V).

Usage:  PYTHONPATH=src python design_runs/eval_geo.py FINAL.yaml '<json overrides>' I rpm g0 g1 step [mesh] [nsec]
"""
from __future__ import annotations
import sys, json, math
from pathlib import Path
import yaml
from motor_ai_sim.config import DEFAULT_CONFIG_PATH, clear_config_cache
from motor_ai_sim.optimization.refine_proc import run_one

FINAL = sys.argv[1]
OVER = json.loads(sys.argv[2]) if sys.argv[2] not in ("", "{}") else {}
CUR = float(sys.argv[3]); RPM = float(sys.argv[4])
G0 = float(sys.argv[5]); G1 = float(sys.argv[6]); STEP = float(sys.argv[7])
MESH = float(sys.argv[8]) if len(sys.argv) > 8 else 0.7
NSEC = int(sys.argv[9]) if len(sys.argv) > 9 else 2

ROOT = Path(__file__).parent.parent
CFG = Path(DEFAULT_CONFIG_PATH)
raw = yaml.safe_load((ROOT / "design_runs" / FINAL).read_text(encoding="utf-8"))
raw["simulation"]["rpm"] = RPM; raw["simulation"]["max_current"] = CUR
CFG.write_text(yaml.dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
clear_config_cache()

print(f"override: {OVER}")
omega = RPM * 2 * math.pi / 60.0
best = None
g = G0
while g <= G1 + 1e-6:
    try:
        r = run_one(OVER, CUR, steps=10, coil_temp_c=120.0, n_periods=1.0, gamma_deg=g,
                    mesh_size_mm=MESH, min_size_mm=0.3, n_sectors=NSEC, rotor_eddy=True, gap_layers=1.0)
        print(f"  g={g:6.1f}  T={r['T_em_Nm']:.4f}  eff={r['efficiency']:.4f}  rip={r['T_ripple_pct']:.1f}%  "
              f"Pcu={r['P_cu_W']:.0f} Pfe={r['P_fe_W']:.0f}  V={r['V_peak']:.1f}")
        if best is None or r["T_em_Nm"] > best[1]["T_em_Nm"]:
            best = (g, r)
    except Exception as e:
        print(f"  g={g:6.1f}  FAILED: {str(e)[:70]}")
    g += STEP
if best:
    r = best[1]
    print(f"MTPA g={best[0]:.1f}  T={r['T_em_Nm']:.4f} Nm  P={r['T_em_Nm']*omega:.0f} W  "
          f"eff={r['efficiency']:.4f}  rip={r['T_ripple_pct']:.1f}%  V={r['V_peak']:.1f}")
