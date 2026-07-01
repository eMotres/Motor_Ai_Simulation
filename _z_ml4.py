import _use40  # noqa
import math, numpy as np
import motor_ai_sim.simulation.fem_solver_2d as F
from motor_ai_sim.cadquery_geometry import CadQueryMotor
m=CadQueryMotor()
polys=m.get_2d_polygons(rotor_angle_deg=0.0)
print("has in_band:", polys.get("in_band") is not None, " out_band:", polys.get("out_band") is not None)
print("_do_struct would be:", True, " (structured_gap=True)")
print("_SB_STRUCTURED_GAP env:", F._SB_STRUCTURED_GAP)
print("_SB_STRUCTURED_STRIPS env:", F._SB_STRUCTURED_STRIPS if hasattr(F,'_SB_STRUCTURED_STRIPS') else 'n/a')
# estimate r_ro/r_si like the code
polys=F._simplify_polys(polys, tol_mm=0.005)  # simplified (no struct) to get solids
rs=[]
if polys.get("rotor") is not None: rs.append(polys["rotor"])
if polys.get("shaft") is not None: rs.append(polys["shaft"])
rs+=[mm for mm,_p in polys.get("magnets",[]) if mm is not None]
rro=0
for g in rs:
    xy=(g.exterior.coords if hasattr(g,"exterior") else g.geoms[0].exterior.coords)
    rro=max(rro,max(math.hypot(x,y) for x,y in xy))
print("est r_ro:", rro)
sg=polys.get("stator"); sg=sg.geoms[0] if hasattr(sg,"geoms") else sg
rsi=min(min(math.hypot(x,y) for x,y in intr.coords) for intr in sg.interiors)
print("est r_si:", rsi, " gap:", rsi-rro)
