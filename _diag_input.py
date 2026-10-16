"""Compare the INPUT geometry fed to the mesher for n_sectors = 4, 2, 1.
Replicates build_mesh_from_polygons' prep (air-rename + clip) and reports
validity, area, vertex counts, and PAIRWISE OVERLAPS for each case."""
import math
from shapely.ops import unary_union
from motor_ai_sim.simulation.fem_solver_2d import _simplify_polys, _clip_polys_to_sector
from motor_ai_sim.cadquery_geometry import CadQueryMotor


def nverts(g):
    if g is None or g.is_empty:
        return 0
    gs = g.geoms if hasattr(g, "geoms") else [g]
    n = 0
    for s in gs:
        if getattr(s, "geom_type", "") == "Polygon":
            n += len(s.exterior.coords) + sum(len(h.coords) for h in s.interiors)
    return n


def ngeoms(g):
    if g is None or g.is_empty:
        return 0
    return len(g.geoms) if hasattr(g, "geoms") else 1


def prep(n_sectors):
    m = CadQueryMotor()
    po = m.get_2d_polygons(rotor_angle_deg=0.0)
    po = _simplify_polys(po, tol_mm=0.005, stator_fillet_mm=0.0)
    po = dict(po)
    if po.get("in_band") is not None and po.get("out_band") is not None:
        po["air_gap"] = po.pop("in_band")
        po["air_outer"] = po.pop("out_band")
        po.pop("airgap_band", None)
        po.pop("air_background", None)
    if n_sectors > 1:
        po = _clip_polys_to_sector(po, n_sectors=n_sectors)
    return po


for ns in (4, 2, 1):
    po = prep(ns)
    mg = unary_union([g for g, _ in po.get("magnets", []) if g is not None]) if po.get("magnets") else None
    cl = unary_union([g for g in po.get("coils", []) if g is not None]) if po.get("coils") else None
    named = {"stator": po.get("stator"), "rotor": po.get("rotor"),
             "shaft": po.get("shaft"), "air_gap": po.get("air_gap"),
             "air_outer": po.get("air_outer"), "magnets": mg, "coils": cl}
    named = {k: v for k, v in named.items() if v is not None and not v.is_empty}
    print("=== n_sectors=%d ===" % ns, flush=True)
    for k, g in named.items():
        print("   %-9s valid=%s area=%9.2f nverts=%4d ngeoms=%d"
              % (k, g.is_valid, g.area, nverts(g), ngeoms(g)), flush=True)
    keys = list(named)
    ov_found = False
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            try:
                ov = named[keys[i]].intersection(named[keys[j]]).area
            except Exception:
                ov = -1
            if ov > 1e-3:
                print("   OVERLAP %-9s ^ %-9s = %.3f mm2" % (keys[i], keys[j], ov), flush=True)
                ov_found = True
    if not ov_found:
        print("   (no pairwise overlaps > 1e-3 mm2)", flush=True)
