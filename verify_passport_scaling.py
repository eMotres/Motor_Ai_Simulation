"""De-risk the 'instant analytical passport': does FEM torque/EMF actually scale
~linearly with turns (N) and lamination length (L)? If yes, the simple-tuner can
compute T/V/P from ONE base FEM by scaling — no FEM per tweak.
"""
import json
from motor_ai_sim.config import get_config
from motor_ai_sim.routes.simulation import get_fem_transient

g = get_config()["geometry"]
N0 = float(g["num_wires_per_slot"]); L0 = float(g["motor_length"])
wire_keys = {k: g[k] for k in g if "wire" in k.lower()}
print(f"base N(num_wires_per_slot)={N0}  L(motor_length)={L0}")
print(f"wire-related geometry keys: {wire_keys}")


def run(ov=None):
    d = get_fem_transient(n_steps_per_period=6, n_periods=1.0, gamma_deg=28.0,
                          I_phase_rms=110.0, mesh_size_mm=6.0, n_sectors=4,
                          sliding_band=True, rotor_eddy=False,
                          geo=json.dumps(ov) if ov else None)
    return (float(d.get("T_avg_Nm") or 0), float(d.get("V_peak") or 0),
            float(d.get("R_phase_ohm") or 0))


T0, V0, R0 = run(None)
T1, V1, R1 = run({"num_wires_per_slot": round(N0 * 1.5)})   # ×1.5 turns
T2, V2, R2 = run({"motor_length": round(L0 * 1.5, 2)})       # ×1.5 length
print(f"\nbase    : T={T0:.2f}  V={V0:.1f}  R={R0:.4f}")
print(f"x1.5 N  : T={T1:.2f} (T/T0={T1/T0:.3f}, expect ~1.5)  V={V1:.1f} (V/V0={V1/V0:.3f})  R={R1:.4f} (R/R0={R1/R0:.3f}, expect ~2.25=N^2)")
print(f"x1.5 L  : T={T2:.2f} (T/T0={T2/T0:.3f}, expect ~1.5)  V={V2:.1f} (V/V0={V2/V0:.3f})  R={R2:.4f} (R/R0={R2/R0:.3f}, expect ~1.5)")
