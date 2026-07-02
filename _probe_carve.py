import _use40  # noqa
import numpy as np, math
import motor_ai_sim.simulation.fem_solver_2d as F
from motor_ai_sim.cadquery_geometry import CadQueryMotor

def run(carve):
    # monkeypatch footprint to None to disable carve
    orig = F._slot_block_footprint
    if not carve:
        F._slot_block_footprint = lambda gc: None
    m = CadQueryMotor()
    polys = F._simplify_polys(m.get_2d_polygons(0.0), tol_mm=0.005, stator_fillet_mm=0.0, band_mode="merged")
    ps = dict(polys); ps["air_outer"] = ps.pop("out_band")
    ins = float(m.parameters["insulation_thickness"]); dy = float(m.parameters["wire_spacing_y"])
    mesh, tags, _ = F.build_mesh_from_polygons(ps, 0.0, 1.5, min_size_mm=max(0.02, min(ins, dy)/2),
        normal_deviation_deg=8.0, aspect_ratio=10.0, geo_cfg=m.parameters, outer_air_factor=1.3,
        motion_band=False, gap_layers=2.0, n_sectors=6, add_background_air=False, structured_slot=True)
    F._slot_block_footprint = orig
    P = np.asarray(mesh.p)*1000.0; T = np.asarray(mesh.t); tags = np.asarray(tags)
    a=P[:,T[0]];b=P[:,T[1]];c=P[:,T[2]]
    L0=np.hypot(*(b-a));L1=np.hypot(*(c-b));L2=np.hypot(*(a-c))
    def ang(la,lb,lc):
        v=(lb**2+lc**2-la**2)/(2*lb*lc+1e-30);return np.degrees(np.arccos(np.clip(v,-1,1)))
    mn=np.minimum(np.minimum(ang(L1,L0,L2),ang(L2,L0,L1)),ang(L0,L1,L2))
    blk=(tags==9)|(tags==10)|(tags>=200)
    m2 = mn[blk]
    print(f"carve={carve}: block tris={int(blk.sum())} min_ang={m2.min():.2f} "
          f"<5={int((m2<5).sum())} <10={int((m2<10).sum())} <15={int((m2<15).sum())}")

run(True)
run(False)
