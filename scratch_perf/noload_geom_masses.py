"""Step 1 of the measured-no-load decomposition: rotor mass (bearing load) and
the lamination cut-edge geometry (punching-degradation attribution).

Reads the ACTIVE config (150 mm 24s/28p, B15AHV950M) and the same CadQuery
polygons the mesher receives.  Writes JSON to scratch_perf/noload_geom.json.
"""
from __future__ import annotations
import json, math, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

from motor_ai_sim.config import get_config
from motor_ai_sim.simulation.geometry_2d import params_from_config
from motor_ai_sim.masses import compute_masses
from motor_ai_sim.cadquery_geometry import CadQueryMotor

cfg = get_config()
geo = dict(cfg["geometry"])
p = params_from_config()
m = compute_masses(p, geo)

out = {"geometry": geo, "masses": {k: v for k, v in m.items()
                                   if isinstance(v, (int, float, str))}}
out["masses"]["MAT"] = m["MAT"]
out["masses"]["RHO"] = m["RHO"]

# ── rotating mass = rotor iron + magnets + shaft ────────────────────────────
m_rot = m["rotor"] + m["mag"] + m["shaft"]
out["rotating_mass_kg"] = m_rot
out["rotating_parts_kg"] = {"rotor_iron": m["rotor"], "magnets": m["mag"],
                            "shaft": m["shaft"]}

# ── lamination cut-edge geometry ────────────────────────────────────────────
# Cut-edge damage: punching leaves a degraded zone of depth d_dam inward from
# EVERY cut edge.  Degraded AREA fraction of the lamination ≈ (perimeter x
# d_dam) / area.  So the number that matters is perimeter/area [1/m] of the
# actual stator + rotor polygons.
motor = CadQueryMotor()
motor.set_parameters(dict(geo))
polys = motor.get_2d_polygons(rotor_angle_deg=0.0)


def _walk(obj):
    if obj is None:
        return
    if isinstance(obj, (list, tuple)):
        for o in obj:
            yield from _walk(o)
        return
    yield obj


def _peri_area(obj):
    """(perimeter [mm], area [mm^2]) of a shapely polygon / multipolygon."""
    P = A = 0.0
    for o in _walk(obj):
        try:
            A += float(o.area)
        except Exception:
            continue
        try:
            P += float(o.length)          # shapely: exterior + interior rings
        except Exception:
            pass
    return P, A


n_sec = int(geo.get("num_seg", 1) or 1)
ns = int(geo.get("num_slots", 24))
npo = int(geo.get("num_poles", 28))

edge = {}
for name, key in (("stator", "stator"), ("rotor", "rotor")):
    P, A = _peri_area(polys.get(key))
    edge[name] = {"perimeter_mm": P, "area_mm2": A,
                  "peri_over_area_per_mm": (P / A) if A > 0 else 0.0,
                  "hydraulic_width_mm": (2.0 * A / P) if P > 0 else 0.0}

# tooth-width reference: the degraded fraction of a tooth of width w with
# damage depth d on both flanks is 2d/w.
edge["tooth_width_mm"] = float(geo.get("tooth_width", 0.0))
edge["tooth2_width_mm"] = float(geo.get("tooth2_width", 0.0))
edge["stack_length_mm"] = float(geo.get("motor_length", 0.0))
edge["n_slots"] = ns
edge["n_poles"] = npo
edge["polygon_sector_slots"] = None

# degraded-area fraction for a range of literature damage depths
for part in ("stator", "rotor"):
    poa = edge[part]["peri_over_area_per_mm"]
    edge[part]["degraded_area_fraction"] = {
        f"d={d}mm": min(1.0, poa * d) for d in (0.1, 0.2, 0.3, 0.5, 1.0)}

out["lamination_edges"] = edge

# ── air-gap / rotor dimensions for the windage model ───────────────────────
out["dims"] = {
    "r_rotor_out_m": p.r_rotor_out, "r_stator_in_m": p.r_stator_in,
    "air_gap_m": p.r_stator_in - p.r_rotor_out,
    "stack_length_m": p.stack_length,
    "r_rotor_in_m": p.r_rotor_in, "r_shaft_in_m": p.r_shaft_in,
    "num_slots": ns, "num_poles": npo,
}

(ROOT / "scratch_perf" / "noload_geom.json").write_text(json.dumps(out, indent=2))
print(json.dumps({k: out[k] for k in
                  ("rotating_mass_kg", "rotating_parts_kg", "dims")}, indent=2))
print(json.dumps(out["lamination_edges"], indent=2))
print("area_source:", m["area_source"])
print("masses kg:", {k: round(m[k], 4) for k in
                     ("stator", "rotor", "mag", "cu", "shaft", "active", "total")})
