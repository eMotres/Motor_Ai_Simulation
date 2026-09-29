"""Tune the 40 mm inrunner toward 0.48 N·m: try geometry variants (overlaid via the
`geo` per-request override, so the global config is NOT mutated) at the MTPA load
angle and report torque + efficiency for each. Keeps the spec wire (2.5x0.6, 7 turns).

Usage:  PYTHONPATH=src python design_runs/tune_40mm.py [I] [rpm] [gamma]
"""
from __future__ import annotations
import json, math, sys
from motor_ai_sim.routes.simulation import get_fem_transient

I = float(sys.argv[1]) if len(sys.argv) > 1 else 35.0
RPM = float(sys.argv[2]) if len(sys.argv) > 2 else 12000.0
GAMMA = float(sys.argv[3]) if len(sys.argv) > 3 else -47.0
omega = RPM * 2 * math.pi / 60.0

# variants: overlays on the active 40 mm config (air_gap 0.5, magnet_fill_up 0.467, magnet_height 5)
VARIANTS = {
    "gap0.30+mag0.7":      {"air_gap": 0.30, "magnet_fill_up": 0.70},
    "gap0.25+mag0.7":      {"air_gap": 0.25, "magnet_fill_up": 0.70},
    "gap0.25+mag0.8":      {"air_gap": 0.25, "magnet_fill_up": 0.80},
    "gap0.30+mag0.85":     {"air_gap": 0.30, "magnet_fill_up": 0.85},
    "gap0.25+mag0.8+8t":   {"air_gap": 0.25, "magnet_fill_up": 0.80, "num_wires_per_slot": 8},
    "gap0.25+mag0.85+8t":  {"air_gap": 0.25, "magnet_fill_up": 0.85, "num_wires_per_slot": 8},
}

best = None
for name, ov in VARIANTS.items():
    geo = json.dumps(ov) if ov else None
    try:
        res = get_fem_transient(n_steps_per_period=6, n_periods=1.0, gamma_deg=GAMMA,
                                I_phase_rms=I, mesh_size_mm=0.8, n_sectors=2,
                                sliding_band=True, rotor_eddy=False, geo=geo)
        sa = res.get("summary") or {}
        T = float(res.get("T_avg_Nm") or 0.0)
        eff = sa.get("efficiency", res.get("efficiency"))
        rip = sa.get("ripple_pct")
        Pm = T * omega
        e = eff if isinstance(eff, (int, float)) else 0.0
        print(f"  {name:22s}  T={T:7.4f} Nm  Pmech={Pm:7.1f} W  eff={e:.4f}  ripple={rip}")
        if best is None or T > best[1]:
            best = (name, T, e, Pm)
    except Exception as ex:
        print(f"  {name:22s}  FAILED: {type(ex).__name__}: {str(ex)[:80]}")

if best:
    print(f"BEST: {best[0]}  T={best[1]:.4f} Nm  Pmech={best[3]:.1f} W  eff={best[2]:.4f}  (target 0.48 Nm / 600 W / 0.918)")
