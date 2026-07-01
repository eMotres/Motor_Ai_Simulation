"""Prototype: ONE sliding-band half (rotor: r_ro -> mid) with route-A gap cells,
sector-clipped to a wedge. Verify EXACTLY K uniform rings + uniform mid ring on
the global slip grid (n_slip_eff nodes over 360deg).

Rotor half owns r_ro -> mid, K radial layers. in_band = inner air disk EXCLUDING
the gap (a hole at r_ro). Gap cells fill r_ro..mid as OCC surfaces in the SAME
fragment. Then set gap cells transfinite (arcs M+1, radial 2) -> exact rings.
"""
import gmsh, math, numpy as np

def build_half(r_ro, mid, K, n_slip_eff, n_sectors, S_full, label):
    """Build rotor half wedge. S_full = angular cells over FULL 360deg.
    Per wedge: S = S_full/n_sectors sectors, M = (n_slip_eff/n_sectors)/S divisions."""
    gmsh.initialize([], interruptible=False)
    gmsh.option.setNumber("General.Terminal", 0)
    gmsh.option.setNumber("Mesh.MeshSizeMin", 0.02)
    gmsh.option.setNumber("Mesh.MeshSizeMax", 2.0)
    gmsh.option.setNumber("Mesh.Algorithm", 6)
    gmsh.option.setNumber("Geometry.Tolerance", 1e-5)
    gmsh.option.setNumber("Geometry.ToleranceBoolean", 1e-3)
    gmsh.model.add("half")
    occ = gmsh.model.occ
    Phi = 2.0*math.pi/n_sectors
    slip_wedge = n_slip_eff // n_sectors           # mid nodes in this wedge
    S = S_full // n_sectors                          # sectors in this wedge
    assert slip_wedge % S == 0, (slip_wedge, S)
    M = slip_wedge // S                               # divisions per cell arc
    print(f"[{label}] Phi={math.degrees(Phi):.1f}deg S={S} M={M} S*M={S*M} (want slip_wedge={slip_wedge})")

    # --- rotor iron: a wedge disk of radius r_ro (a pie slice) ---
    # Build as a plane surface: center + arc r_ro from 0..Phi + two radial edges.
    def P(r,a): return occ.addPoint(r*math.cos(a), r*math.sin(a), 0)
    c = occ.addPoint(0,0,0)
    # iron pie: 0 -> r_ro
    pa = P(r_ro, 0.0); pb = P(r_ro, Phi)
    arc_iron = occ.addCircleArc(pa, c, pb)
    e0 = occ.addLine(c, pa); e1 = occ.addLine(pb, c)
    iron = occ.addPlaneSurface([occ.addCurveLoop([e0, arc_iron, e1])])

    # --- gap cells: r_ro..mid, K radial layers x S angular, over [0,Phi] ---
    radii = np.linspace(r_ro, mid, K+1)
    cells=[]
    for ir in range(K):
        ra, rb = radii[ir], radii[ir+1]
        for s in range(S):
            a1 = Phi*s/S; a2 = Phi*(s+1)/S
            p1,p2,p3,p4 = P(ra,a1),P(ra,a2),P(rb,a2),P(rb,a1)
            ain=occ.addCircleArc(p1,c,p2); aout=occ.addCircleArc(p4,c,p3)
            l2=occ.addLine(p2,p3); l1=occ.addLine(p1,p4)
            cells.append(occ.addPlaneSurface([occ.addCurveLoop([ain,l2,-aout,-l1])]))

    alls=[(2,iron)]+[(2,x) for x in cells]
    occ.fragment(alls,[]); occ.synchronize()

    # --- identify gap cells (centroid radius in (r_ro,mid)) and set transfinite ---
    ncell=0
    for (d,surf) in gmsh.model.getEntities(2):
        com=occ.getCenterOfMass(2,surf); rr=math.hypot(com[0],com[1])
        if r_ro+1e-4 < rr < mid-1e-4:
            ncell+=1
            for (cd,cv) in gmsh.model.getBoundary([(d,surf)],oriented=False):
                b=gmsh.model.getBoundary([(cd,cv)],oriented=False)
                rs=[math.hypot(*gmsh.model.getValue(0,pt,[])[:2]) for (pdim,pt) in b]
                if len(rs)==2:
                    gmsh.model.mesh.setTransfiniteCurve(cv, M+1 if abs(rs[0]-rs[1])<1e-4 else 2)
            try: gmsh.model.mesh.setTransfiniteSurface(surf)
            except Exception as e: print("TF surf fail:",e)
    print(f"[{label}] gap cells found={ncell} (want {K*S})")

    gmsh.model.mesh.generate(2)

    # --- verify: gap radial levels + mid ring on the global grid ---
    nt,nc,_=gmsh.model.mesh.getNodes(); xyz=nc.reshape(-1,3)
    r=np.hypot(xyz[:,0],xyz[:,1]); ang=np.degrees(np.arctan2(xyz[:,1],xyz[:,0]))%360.0
    gap=r[(r>r_ro-1e-3)&(r<mid+1e-3)]; lv=[]
    for v in np.sort(np.unique(np.round(gap,4))):
        if not lv or v-lv[-1]>1e-3: lv.append(round(float(v),4))
    # mid ring nodes: radius ~ mid, snapped to global grid step
    step = 360.0/n_slip_eff
    midmask = np.abs(r-mid) < 1e-4
    mang = ang[midmask]
    kg = np.round(mang/step)
    on_grid = np.abs(mang - kg*step) < 0.05*step
    n_on_grid = int(on_grid.sum()); n_total_mid = int(midmask.sum())
    et,en=gmsh.model.mesh.getElementsByType(2)
    print(f"[{label}] gap radial LEVELS={len(lv)} (want {K+1}): {lv}")
    print(f"[{label}] mid ring: {n_total_mid} nodes at r=mid, {n_on_grid} ON global grid (want {slip_wedge}+1 endpoints)")
    print(f"[{label}] total tris={len(en)//3}, nodes={len(nt)}")
    # degenerate check
    tri = en.reshape(-1,3)
    print(f"[{label}] OK")
    gmsh.finalize()
    return len(lv), n_on_grid

if __name__=="__main__":
    r_ro, r_si = 12.1, 12.3
    mid = 0.5*(r_ro+r_si)
    n_slip_eff = 1680
    n_sectors = 2          # _stitch_full_half builds each half at n_sectors=2
    # S_full must divide n_slip_eff and n_slip_eff/n_sectors divisible by S_full/n_sectors
    # pick S_full=120 -> per wedge S=60, slip_wedge=840, M=14
    S_full = 120
    for K in (1,2,3):
        print(f"\n=== K={K} (gap_layers per half) ===")
        build_half(r_ro, mid, K, n_slip_eff, n_sectors, S_full, f"rotor K={K}")
