"""Build + FEM-evaluate the 40 mm, 12-slot / 14-pole motor toward the target table.

Targets (continuous): 600 W, 0.48 N·m, 12000 rpm, ~91.8% eff, Iph 35 A, ripple ~9%,
mass ~0.12 kg. Materials: F45SH_120C magnets, 20SW1200 steel, air. Wire 2.5x0.6.

ACHIEVED (inrunner realization, MTPA gamma=-42, 35 A, 12000 rpm, n_steps=16/mesh0.6):
  T = 0.455 N*m (95% of target) | P_mech = 572 W (95%) | eff = 91.4% (target 91.8%)
  V_ph_peak = 9.5 V (fits 18-25 V bus). Tuned vs spec: 8 turns (not 7), air_gap 0.25,
  magnet_fill_up 0.80 to recover torque the app's inrunner can't get from the picture-1
  external-coil layout. See design_runs/MOTOR_RESULTS.md.

Usage:  PYTHONPATH=src python design_runs/motor1_40mm.py [I] [rpm] [gamma]
Writes the geometry/materials/winding/sim into config/motor_config.yaml (the active
config is backed up to config/_active_backup.yaml on first run) and runs one FEM eval.
"""
from __future__ import annotations
import json, sys
from pathlib import Path
import yaml

from motor_ai_sim.config import DEFAULT_CONFIG_PATH, clear_config_cache, get_config

CFG = Path(DEFAULT_CONFIG_PATH)
BACKUP = CFG.parent / "_active_backup.yaml"

I = float(sys.argv[1]) if len(sys.argv) > 1 else 35.0
RPM = float(sys.argv[2]) if len(sys.argv) > 2 else 12000.0
GAMMA = float(sys.argv[3]) if len(sys.argv) > 3 else -42.0  # MTPA for this geometry

raw = yaml.safe_load(CFG.read_text(encoding="utf-8"))
if not BACKUP.exists():
    BACKUP.write_text(yaml.dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print(f"backed up active config -> {BACKUP.name}")

# ── 40 mm geometry: 12 slots / 14 poles (seg 2 x 6 slots x 7 poles) ──────────
raw["geometry"].update(dict(
    stator_diameter=40, num_seg=2, num_slots_per_segment=6, num_poles_per_segment=7,
    core_thickness=2.0, slot_height=5.0, air_gap=0.25,
    tooth_width=3.0, tooth2_width=1.8, cut_width=1.0, insulation_thickness=0.2,
    wire_width=2.5, wire_height=0.6, wire_spacing_x=0.1, wire_spacing_y=0.13,
    num_wires_per_slot=8,
    magnet_height=5.0, rotor_house_height=1.0, shaft_height=2.0,
    magnet_fill_down=0.9, magnet_fill_up=0.80, magnet_fill_radius=1.0, magnet_up_gap=0.7,
    rotor_hole=0.6, magnet_down_height=1.0,
    stator_fillet_r=0.0, stator_fillet_r1=0.0, rotor_fill_r=0.2,  # no slot-corner rounding
    motor_length=12,
))
raw["materials"].update(dict(stator_core="20SW1200", rotor_core="20SW1200", magnet="F45SH_120C"))
raw["winding"].update(dict(n_coils_per_phase=2, connection="2S", n_parallel=1, n_series=2, layout=""))
raw["simulation"].update(dict(rpm=RPM, max_current=I))

CFG.write_text(yaml.dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
clear_config_cache()

# ── validate geometry build ───────────────────────────────────────────────────
from motor_ai_sim.cadquery_geometry import CadQueryMotor
geo = dict(get_config()["geometry"])
motor = CadQueryMotor(); motor.set_parameters(geo)
mesh = motor.get_2d_mesh_data()
print("geometry OK. components:", list(mesh.keys()))
from motor_ai_sim.services.geometry_service import get_current_geometry
p = get_current_geometry(reload=True)
print(f"radii: stator_OR={p.stator_outer_radius:.2f} stator_IR={p.stator_inner_radius:.2f} "
      f"rotor_OR={p.rotor_outer_radius:.2f} rotor_IR={p.rotor_inner_radius:.2f} shaft={p.shaft_radius:.2f} "
      f"slots={p.num_slots} poles={p.num_poles}")

# ── FEM evaluate ──────────────────────────────────────────────────────────────
from motor_ai_sim.routes.simulation import get_fem_transient
res = get_fem_transient(n_steps_per_period=8, n_periods=1.0, gamma_deg=GAMMA, I_phase_rms=I,
                        mesh_size_mm=0.8, n_sectors=2, sliding_band=True, rotor_eddy=True)
sa = res.get("summary") or {}
T = float(res.get("T_avg_Nm") or 0.0)
import math
omega = RPM * 2 * math.pi / 60.0
Pmech = T * omega
keys = ["T_avg_Nm", "ripple_pct", "P_cu_W", "P_fe_W", "P_mag_eddy_W", "P_shaft_eddy_W",
        "efficiency", "V_peak", "V_phase_peak_V", "mass_kg", "current_density_A_mm2"]
print("I=%.0fA rpm=%.0f gamma=%.0f  ->  T=%.3f Nm  Pmech=%.0f W" % (I, RPM, GAMMA, T, Pmech))
flat = {**res, **sa}
print({k: (round(flat[k], 3) if isinstance(flat.get(k), (int, float)) else flat.get(k)) for k in keys if k in flat})
