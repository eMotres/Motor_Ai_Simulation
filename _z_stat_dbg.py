import _use40  # noqa
import math, numpy as np, logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s|%(message)s")
import motor_ai_sim.simulation.fem_solver_2d as F
from motor_ai_sim.cadquery_geometry import CadQueryMotor
m=CadQueryMotor()
polys=F._simplify_polys(m.get_2d_polygons(rotor_angle_deg=0.0), tol_mm=0.005, stator_fillet_mm=0.0, n_slip=1008, gap_layers=2, structured_gap=True, band_mode="merged")
ps,pr=F._split_polys_for_sliding_band(polys)
# inspect stator half out_band
ob=ps.get("out_band")
print("out_band type:", ob.geom_type if ob is not None else None)
def rr(g):
    xs=[]
    for sub in (g.geoms if hasattr(g,"geoms") else [g]):
        xs+=list(sub.exterior.coords)
        for h in sub.interiors: xs+=list(h.coords)
    return np.hypot([p[0] for p in xs],[p[1] for p in xs])
r=rr(ob); print(f"out_band r range {r.min():.4f}..{r.max():.4f}; pts near r_si (r<12.35): {int((r<12.35).sum())}")
st=ps.get("stator"); r2=rr(st); print(f"stator r range {r2.min():.4f}..{r2.max():.4f}")
