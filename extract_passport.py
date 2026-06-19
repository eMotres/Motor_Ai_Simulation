"""Extract a full analytical PASSPORT of the active reference motor from FEM, so
the simple-tuner (Compare / Configurator tab) can scale it INSTANTLY — no FEM.

Three transient solves of the CURRENT config:
  A) loaded  (I0, gamma, eddy ON) -> T0, R0(@L0), Pfe0 (iron), Pmag0 (magnet), mass0
  B) no-load (I=0)                -> Vemf0_peak (pure back-EMF; terminal V = EMF, no IR)
  C) loaded at 1.5*L0 (cheap)     -> R(@1.5L0): split R = R_active(prop L) + R_end(const)

Prints a Passport JSON matching the interface in web/src/lib/motorScaling.ts.
Run:  uv run python extract_passport.py
"""
import json
from motor_ai_sim.config import get_config
from motor_ai_sim.routes.simulation import get_fem_transient

cfg  = get_config()
g    = cfg["geometry"]
wind = cfg.get("winding", {}) or {}

N0     = float(g["num_wires_per_slot"])
L0     = float(g["motor_length"])
wireH0 = float(g.get("wire_height", 0.0))
nP0    = float(wind.get("n_parallel", 2))
I0     = 110.0
GAMMA  = 28.0

print(f"reference config: N0={N0} L0={L0}mm wireH0={wireH0}mm n_parallel(nP0)={nP0}")
print(f"  poles={g.get('num_poles')} slots={g.get('num_slots')} "
      f"stator_OR={g.get('stator_outer_radius')} air_gap={g.get('air_gap')}")


def run(I, length=None, eddy=False, steps=12):
    ov = {}
    if length is not None:
        ov["motor_length"] = round(length, 3)
    return get_fem_transient(
        n_steps_per_period=steps, n_periods=1.0, gamma_deg=GAMMA,
        I_phase_rms=I, mesh_size_mm=5.0, n_sectors=4,
        sliding_band=True, rotor_eddy=eddy,
        geo=json.dumps(ov) if ov else None,
    )


print("\n[A] loaded (I0, eddy ON) ...")
A = run(I0, eddy=True, steps=12)
print("[B] no-load (I=0) ...")
B = run(0.0, eddy=False, steps=12)
print("[C] loaded @1.5*L0 (R split) ...")
C = run(I0, length=L0 * 1.5, eddy=False, steps=4)

sa    = A["summary"]
T0    = float(A["T_avg_Nm"])
R_A   = float(A["R_phase_ohm"])
R_C   = float(C["R_phase_ohm"])
Vemf0 = float(B["V_peak"])
rpm0  = float(A["rpm"])

# R(L) = R_active*(L/L0) + R_end ;  R_C is at 1.5*L0  ->  R_C-R_A = 0.5*R_active
R_active    = 2.0 * (R_C - R_A)
R_end       = max(0.0, R_A - R_active)
endWindFrac = min(1.0, max(0.0, R_end / R_A)) if R_A > 0 else 0.0

passport = {
    "N0": N0, "L0_mm": L0, "wireH0_mm": wireH0,
    "I0_A": I0, "rpm0": round(rpm0), "nP0": nP0,
    "T0_Nm": round(T0, 3),
    "Vemf0_peak_V": round(Vemf0, 2),
    "Vload0_peak_V": round(float(sa.get("V_phase_peak_V") or 0.0), 2),
    "R0_ohm": round(R_A, 5),
    "endWindFrac": round(endWindFrac, 3),
    "Pfe0_W": float(sa["P_core_W"]),
    "Pmag0_W": float(sa["P_solid_W"]),
    "mass0_kg": float(sa["mass_total_kg"]),
}

print("\n--- diagnostics ---")
print(f"R(L0)={R_A:.5f}  R(1.5L0)={R_C:.5f}  -> R_active={R_active:.5f} "
      f"R_end={R_end:.5f}  endWindFrac={endWindFrac:.3f}")
print(f"T0={T0:.2f} Nm   Vemf0_peak={Vemf0:.1f} V   rpm0={rpm0:.0f}")
print(f"Pfe0={sa['P_core_W']} W   Pmag0={sa['P_solid_W']} W   mass0={sa['mass_total_kg']} kg")
print(f"loaded summary: eff={sa.get('efficiency')}  Vph_peak={sa.get('V_phase_peak_V')} "
      f"P_mech={sa.get('P_mech_W')} W  ripple={sa.get('T_ripple_pct')}%")
print("\n--- PASSPORT JSON (paste into web/src/lib/referencePassports.ts) ---")
print(json.dumps(passport, indent=2))
