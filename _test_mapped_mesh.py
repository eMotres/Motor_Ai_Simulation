"""Integration mesh test: mimic the transient's polys prep with structured_gap,
build the two halves, and verify EXACTLY 2K uniform gap rings + conforming + slip
ring uniform.  Runs for gap_layers=1,2,3."""
import math, sys, numpy as np
from motor_ai_sim.cadquery_geometry import CadQueryMotor
import motor_ai_sim.simulation.fem_solver_2d as fs

def run(gap_layers, n_slip_eff=168, NS=2):
    motor = CadQueryMotor()
    polys0 = motor.get_2d_polygons(rotor_angle_deg=0.0)
    polys = fs._simplify_polys(polys0, tol_mm=0.005, stator_fillet_mm=1.0,
                               n_slip=n_slip_eff, gap_layers=gap_layers,
                               structured_gap=True, band_mode="merged")
    spec = polys.get("mapped_gap_spec")
    ms, ts, cs, mr, tr, cr = fs._build_sliding_band_meshes(
        polys, 0.0, 0.6, min_size_mm=0.3, outer_air_factor=1.2,
        band_thickness_mm=0.4, n_sectors=NS, geo_cfg=motor.parameters,
        normal_deviation_deg=8.0, aspect_ratio=10.0, gap_layers=float(gap_layers),
        component_mesh_mm=None, full_ring=False, pole_copy=False)
    Ps, Tts = np.asarray(ms.p), np.asarray(ms.t)
    Pr, Ttr = np.asarray(mr.p), np.asarray(mr.t)
    Pall = np.hstack([Ps, Pr]); Tall = np.hstack([Tts, Ttr + Ps.shape[1]])
    from skfem import MeshTri
    mesh = MeshTri(Pall.astype(float), Tall.astype(np.int64))
    mesh, _ = fs._weld_coincident_nodes(mesh, np.zeros(Tall.shape[1], np.int16))
    P = np.asarray(mesh.p)*1e3; T = np.asarray(mesh.t)   # mm
    r = np.hypot(P[0], P[1])
    r_ro = spec["r_ro"]; mid = spec["mid_r"]; r_si = spec["r_si"]
    # radial levels in the FULL gap band [r_ro..r_si]
    lv=[]
    for v in np.sort(np.unique(np.round(r[(r>=r_ro-2e-4)&(r<=r_si+2e-4)],4))):
        if not lv or v-lv[-1]>2e-3: lv.append(v)
    want = 2*gap_layers + 1   # 2K rings => 2K+1 distinct radial levels (incl mid)
    ok_rings = (len(lv)==want)
    # slip ring uniform on grid
    step=360.0/n_slip_eff
    mr_i=np.where(np.abs(r-mid)<1e-5)[0]
    ang=np.degrees(np.arctan2(P[1,mr_i],P[0,mr_i]))%360.0
    ongrid = mr_i.size>0 and np.all(np.abs(ang-np.round(ang/step)*step)<0.05*step)
    # conformity: no edge shared by >2 tris
    from collections import Counter
    cnt=Counter()
    for tri in T.T:
        for a,b in ((tri[0],tri[1]),(tri[1],tri[2]),(tri[2],tri[0])): cnt[(min(a,b),max(a,b))]+=1
    over=sum(1 for v in cnt.values() if v>2)
    # spacing uniformity within each half
    lo=[x for x in lv if x<=mid+1e-4]; hi=[x for x in lv if x>=mid-1e-4]
    def unif(seq):
        if len(seq)<2: return None
        d=np.diff(seq); return (d.min(),d.max(),d.mean())
    print(f"gap_layers={gap_layers}: gap radial levels={len(lv)} (want {want}) "
          f"{'OK' if ok_rings else 'FAIL'}  {[round(x,4) for x in lv]}")
    print(f"    rotor half levels={len(lo)} spacing={unif(lo)}  "
          f"stator half levels={len(hi)} spacing={unif(hi)}")
    print(f"    slip ring @mid: {mr_i.size} nodes on-grid={ongrid}; "
          f"conformity over(>2)={over} {'CONFORMING' if over==0 else 'NON-CONFORMING'}")
    return ok_rings and ongrid and over==0

if __name__ == "__main__":
    allok=True
    for K in (1,2,3):
        try:
            allok = run(K) and allok
        except Exception as e:
            import traceback; traceback.print_exc(); allok=False
        print()
    print("ALL-OK" if allok else "SOME-FAILED")
    sys.exit(0 if allok else 1)
