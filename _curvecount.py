# Instrument the real build: monkeypatch to record, per slot-cell fragment tag,
# the number of boundary curves and whether corners are collinear.
import _use40  # noqa
import numpy as np, math, types
import motor_ai_sim.simulation.fem_solver_2d as F
from motor_ai_sim.cadquery_geometry import CadQueryMotor
import gmsh

# Patch setTransfiniteSurface to log cells whose 4 "corners" are nearly collinear.
_orig_tf = gmsh.model.mesh.setTransfiniteSurface
stats = {"total":0, "collinear":0, "curves_gt4_before":0}
def patched_tf(surf, arrangement="Left", cornerTags=[]):
    stats["total"] += 1
    if cornerTags and len(cornerTags) == 4:
        xy = [gmsh.model.getValue(0, int(p), [])[:2] for p in cornerTags]
        # check if any 3 consecutive corners are collinear (mid-edge point chained as corner)
        for i in range(4):
            a=xy[i]; b=xy[(i+1)%4]; c=xy[(i+2)%4]
            cross=abs((b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]))
            La=math.hypot(b[0]-a[0],b[1]-a[1]); Lb=math.hypot(c[0]-b[0],c[1]-b[1])
            if La>1e-9 and Lb>1e-9 and cross/(La*Lb) < 0.05:  # sin(angle)<0.05 => ~<3deg
                stats["collinear"] += 1
                break
    return _orig_tf(surf, arrangement, cornerTags)
gmsh.model.mesh.setTransfiniteSurface = patched_tf

m = CadQueryMotor()
polys = F._simplify_polys(m.get_2d_polygons(0.0), tol_mm=0.005, stator_fillet_mm=0.0, band_mode="merged")
ps = dict(polys); ps["air_outer"] = ps.pop("out_band")
ins=float(m.parameters["insulation_thickness"]); dy=float(m.parameters["wire_spacing_y"])
mesh,tags,_=F.build_mesh_from_polygons(ps,0.0,1.5,min_size_mm=max(0.02,min(ins,dy)/2),
    normal_deviation_deg=8.0,aspect_ratio=10.0,geo_cfg=m.parameters,outer_air_factor=1.3,
    motion_band=False,gap_layers=2.0,n_sectors=6,add_background_air=False,structured_slot=True)
print("TF surfaces:", stats["total"], "with ~collinear corners:", stats["collinear"])
