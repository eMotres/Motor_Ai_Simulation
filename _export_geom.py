"""Export the REAL motor geometry (CadQuery/Shapely polygons) to a flat JSON the
WSL PINN trainer can read WITHOUT shapely/cadquery — so the PINN samples
collocation points inside the SAME polygons the FEM solves (trapezoid spoke
magnets + real coils + T-tooth stator), not the crude MotorDomains2D sectors.

Mirrors the FEM source model (fem_solver_2d.build_materials):
  * magnet:  M = ±M_mag · tangent_CCW(centroid)   (tangential, sign per polarity)
  * coil:    (phase, direction) via centroid-angle → slot_idx → winding layout

Run on Windows (has shapely + cadquery):
  python _export_geom.py            → writes pinn_geom.json  (metres)
"""
import os, sys, json, math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
import numpy as np

from motor_ai_sim.routes.simulation import _get_motor_geom        # cached real polys (mm)
from motor_ai_sim.simulation.geometry_2d import params_from_config, build_winding_layout
from motor_ai_sim.simulation.solver_2d import SimConfig

S = 1.0e-3   # mm → m
gp  = params_from_config()
cfg = SimConfig.from_motor_config()
polys = _get_motor_geom(0.0)


def poly_to_rings(poly):
    """Shapely Polygon/MultiPolygon → [{ext:[[x,y]..], holes:[[..],..]}] in metres."""
    out = []
    if poly is None:
        return out
    for g in list(getattr(poly, "geoms", [poly])):
        if getattr(g, "is_empty", True):
            continue
        try:
            ext = [[float(x) * S, float(y) * S] for x, y in g.exterior.coords]
        except Exception:
            continue
        holes = [[[float(x) * S, float(y) * S] for x, y in h.coords] for h in g.interiors]
        out.append({"ext": ext, "holes": holes})
    return out


# ── magnets ──────────────────────────────────────────────────────────────────
magnets = []
for mp, pol in polys.get("magnets", []):
    if mp is None or mp.is_empty:
        continue
    cx, cy = float(mp.centroid.x) * S, float(mp.centroid.y) * S
    magnets.append({"rings": poly_to_rings(mp), "polarity": int(pol),
                    "cx": cx, "cy": cy, "area_m2": float(mp.area) * S * S})

# ── coils: (phase, direction) via centroid angle → slot_idx → winding layout ──
winding = build_winding_layout(gp.num_slots, gp.num_poles // 2)
slot_pitch = 360.0 / gp.num_slots
half_pitch = slot_pitch * 0.5
coils = []
for cp in polys.get("coils", []):
    if cp is None or cp.is_empty:
        continue
    cx, cy = float(cp.centroid.x), float(cp.centroid.y)
    ang = math.degrees(math.atan2(cy, cx)) % 360.0
    slot_idx = int((ang - half_pitch) / slot_pitch + 0.5) % gp.num_slots
    phase, direction = winding[slot_idx]
    coils.append({"rings": poly_to_rings(cp), "phase": phase, "direction": int(direction),
                  "slot_idx": slot_idx, "cx": cx * S, "cy": cy * S,
                  "area_m2": float(cp.area) * S * S})

geom = {
    "units": "meters",
    "magnets": magnets,
    "coils": coils,
    "stator": poly_to_rings(polys.get("stator")),
    "rotor":  poly_to_rings(polys.get("rotor")),
    "shaft":  poly_to_rings(polys.get("shaft")),
    "air_gap": poly_to_rings(polys.get("air_gap")),
    "meta": {
        "num_slots": gp.num_slots, "num_poles": gp.num_poles,
        "n_wires": gp.num_wires_per_slot,
        "Br": float(cfg.Br_magnet),
        "mu_r_fe": float(cfg.mu_r_stator),
        "r_shaft_in": gp.r_shaft_in, "r_rotor_in": gp.r_rotor_in,
        "r_rotor_out": gp.r_rotor_out, "r_stator_in": gp.r_stator_in,
        "r_stator_out": gp.r_stator_out,
        "sigma_cu": gp.sigma_cu, "sigma_mag": gp.sigma_mag,
    },
}

out = os.path.join(os.path.dirname(__file__), "pinn_geom.json")
with open(out, "w") as f:
    json.dump(geom, f)

# ── summary ──
def in_q1(cx, cy):
    a = math.degrees(math.atan2(cy, cx))
    return -1e-6 <= a <= 90 + 1e-6

print(f"wrote {out}")
print(f"magnets: {len(magnets)} total, {sum(in_q1(m['cx'], m['cy']) for m in magnets)} in 90° sector")
print(f"coils:   {len(coils)} total, {sum(in_q1(c['cx'], c['cy']) for c in coils)} in 90° sector")
print(f"Br={geom['meta']['Br']}  n_wires={geom['meta']['n_wires']}")
ph = {}
for c in coils:
    if in_q1(c['cx'], c['cy']):
        ph[f"{c['phase']}{'+' if c['direction']>0 else '-'}"] = ph.get(f"{c['phase']}{'+' if c['direction']>0 else '-'}", 0) + 1
print("sector coil phases:", ph)
print("magnet[0] area_m2=%.3e  rings=%d verts=%d" % (
    magnets[0]['area_m2'], len(magnets[0]['rings']), len(magnets[0]['rings'][0]['ext'])))
