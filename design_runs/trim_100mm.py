"""Find the current that gives the 6.0 N·m target for each 100mm candidate, and
report efficiency + voltage there. Higher torque-per-amp (saturation relieved by
wider teeth + bigger magnet) => lower current for 6 N·m => less copper loss =>
higher efficiency — but bigger magnet raises back-EMF, so V must stay <= the
baseline's ~38 (14S bus). Sweeps current at fixed gamma; interpolate to 6.0 N·m.

Usage:  PYTHONPATH=src python design_runs/trim_100mm.py gamma
"""
from __future__ import annotations
import sys, math
from pathlib import Path
import yaml
from motor_ai_sim.config import DEFAULT_CONFIG_PATH, clear_config_cache
from motor_ai_sim.optimization.refine_proc import run_one

GAMMA = float(sys.argv[1]) if len(sys.argv) > 1 else -35.0
RPM = 3800.0
omega = RPM * 2 * math.pi / 60.0
TARGET_T = 6.0

ROOT = Path(__file__).parent.parent
CFG = Path(DEFAULT_CONFIG_PATH)
raw = yaml.safe_load((ROOT / "design_runs" / "motor_100mm_final.yaml").read_text(encoding="utf-8"))
raw["simulation"]["rpm"] = RPM
CFG.write_text(yaml.dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
clear_config_cache()

# voltage-constrained: WIDE TEETH (relieve saturation, no extra back-EMF) +
# magnet near baseline (10.7) to keep V <= baseline ~35 (verified 14S-OK).
CANDS = {
    "D_t7.0_mag10.7": {"tooth_width": 7.0, "tooth2_width": 2.6, "magnet_height": 10.7,
                       "core_thickness": 3.3, "slot_height": 9.0, "magnet_fill_up": 0.5, "magnet_up_gap": 1.2},
    "E_t7.5_mag10.7": {"tooth_width": 7.5, "tooth2_width": 2.6, "magnet_height": 10.7,
                       "core_thickness": 3.3, "slot_height": 9.2, "magnet_fill_up": 0.5, "magnet_up_gap": 1.2},
    "F_t7.0_mag11.0": {"tooth_width": 7.0, "tooth2_width": 2.6, "magnet_height": 11.0,
                       "core_thickness": 3.3, "slot_height": 9.0, "magnet_fill_up": 0.5, "magnet_up_gap": 1.2},
}
CURRENTS = [34.0, 42.0]   # two points; torque ~linear in I -> interpolate to 6.0


def ev(ov, I):
    return run_one(ov, I, steps=8, coil_temp_c=120.0, n_periods=1.0, gamma_deg=GAMMA,
                   mesh_size_mm=1.2, min_size_mm=0.3, n_sectors=4, rotor_eddy=True, gap_layers=1.0)


for name, ov in CANDS.items():
    pts = []
    for I in CURRENTS:
        try:
            r = ev(ov, I)
            pts.append((I, r["T_em_Nm"], r["efficiency"], r["V_peak"], r["P_cu_W"], r["T_ripple_pct"]))
            print(f"  {name:18s} I={I:.0f}  T={r['T_em_Nm']:.3f}  eff={r['efficiency']:.4f}  V={r['V_peak']:.1f}  Pcu={r['P_cu_W']:.0f}  rip={r['T_ripple_pct']:.1f}%")
        except Exception as e:
            print(f"  {name:18s} I={I:.0f}  FAILED: {str(e)[:60]}")
    if len(pts) == 2:
        (I1, T1, e1, V1, _, _), (I2, T2, e2, V2, _, _) = pts
        if T2 != T1:
            f = (TARGET_T - T1) / (T2 - T1)
            I6 = I1 + f * (I2 - I1); e6 = e1 + f * (e2 - e1); V6 = V1 + f * (V2 - V1)
            print(f"  -> {name:18s} @6.0 N·m:  I≈{I6:.1f} A  eff≈{e6:.4f}  V≈{V6:.1f}  (P_mech={TARGET_T*omega:.0f} W)\n")
