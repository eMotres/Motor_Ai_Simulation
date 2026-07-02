import _use40  # noqa
import numpy as np, math
import motor_ai_sim.simulation.fem_solver_2d as F
from motor_ai_sim.cadquery_geometry import CadQueryMotor
def q(P,T):
    a=P[:,T[0]];b=P[:,T[1]];c=P[:,T[2]]
    L0=np.hypot(*(b-a));L1=np.hypot(*(c-b));L2=np.hypot(*(a-c))
    def ang(la,lb,lc):
        v=(lb**2+lc**2-la**2)/(2*lb*lc+1e-30);return np.degrees(np.arccos(np.clip(v,-1,1)))
    return np.minimum(np.minimum(ang(L1,L0,L2),ang(L2,L0,L1)),ang(L0,L1,L2))
m=CadQueryMotor()
polys=F._simplify_polys(m.get_2d_polygons(0.0),tol_mm=0.005,stator_fillet_mm=0.0,band_mode="merged",structured_gap=False)
polys_s,polys_r=F._split_polys_for_sliding_band(polys)
ps=dict(polys_s); ps["air_outer"]=ps.pop("out_band")
ins=float(m.parameters["insulation_thickness"]); dy=float(m.parameters["wire_spacing_y"])
minsz=max(0.02,min(ins,dy)/2)
common=dict(rotor_angle_deg=0.0,mesh_size_mm=1.5,min_size_mm=minsz,normal_deviation_deg=8.0,
    aspect_ratio=10.0,outer_air_factor=1.3,motion_band=False,band_thickness_mm=0.4,gap_layers=2.0,
    geo_cfg=m.parameters,add_background_air=False,slip_transfinite_r=polys.get("mid_r_mm"),
    extra_transfinite_radii=[],component_mesh_mm={"stator":2.0,"rotor":2.0,"magnet":2.0},structured_slot=True)
# raw n_sectors=2
mesh2,tags2,_=F.build_mesh_from_polygons(ps,n_sectors=2,rotational_period_deg=None,**common)
P=np.asarray(mesh2.p)*1000.0;T=np.asarray(mesh2.t);tg=np.asarray(tags2)
mn=q(P,T);blk=(tg==9)|(tg==10)|(tg>=200);mb=mn[blk]
print(f"RAW n_sectors=2: block min={mb.min():.2f} <15={int((mb<15).sum())} tris={T.shape[1]}")
# stitched full
meshF,tagsF,_=F._stitch_full_half(ps,F.DOM_OUTER,dict(common,rotational_period_deg=None))
P=np.asarray(meshF.p)*1000.0;T=np.asarray(meshF.t);tg=np.asarray(tagsF)
mn=q(P,T);blk=(tg==9)|(tg==10)|(tg>=200);mb=mn[blk]
bi=np.where(blk)[0]; w=bi[np.argmin(mn[bi])]
cx=P[0,T[:,w]].mean();cy=P[1,T[:,w]].mean()
print(f"STITCHED full: block min={mb.min():.2f} <15={int((mb<15).sum())} tris={T.shape[1]} "
      f"worst@({cx:.2f},{cy:.2f}) ang={math.degrees(math.atan2(cy,cx)):.1f}")
