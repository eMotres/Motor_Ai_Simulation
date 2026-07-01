"""PROTOTYPE step 2: can we get exact-K transfinite gap sectors in the OCC kernel
(what build_mesh_from_polygons uses), conforming to an adjacent free-meshed 'iron'
ring, in ONE model?  Tests two things:
  (T1) OCC transfinite annulus sectors -> exact K layers (no fragment).
  (T2) gap sectors (transfinite) + inner iron disk (free) sharing the r_in circle,
       welded by OCC fragment -> conforming AND gap still exact K layers.
This decides whether Route-A-in-OCC is feasible (avoids a separate weld step)."""
import math, numpy as np, gmsh

def count_levels(P):
    r = np.hypot(P[0], P[1])
    lv = []
    for v in np.sort(np.unique(np.round(r[r > 1e-6], 4))):
        if not lv or v - lv[-1] > 1e-3:
            lv.append(v)
    return lv

def get_mesh():
    ntags, ncoords, _ = gmsh.model.mesh.getNodes()
    P = ncoords.reshape(-1, 3)[:, :2].T.copy()
    return P

def gap_sectors_occ(occ, r_in, r_out, seam_ang_deg, K, M):
    """Add OCC transfinite annulus sectors. Returns (surf_tags, arc_in_tags, arc_out_tags)."""
    c = occ.addPoint(0, 0, 0)
    def _pt(r, ad):
        a = math.radians(ad); return occ.addPoint(r*math.cos(a), r*math.sin(a), 0)
    inner = [_pt(r_in, a) for a in seam_ang_deg]
    outer = [_pt(r_out, a) for a in seam_ang_deg]
    surfs, ain, aout = [], [], []
    for s in range(len(seam_ang_deg)-1):
        ia = occ.addCircleArc(inner[s], c, inner[s+1])
        oa = occ.addCircleArc(outer[s], c, outer[s+1])
        r1 = occ.addLine(inner[s], outer[s]); r2 = occ.addLine(inner[s+1], outer[s+1])
        cl = occ.addCurveLoop([ia, r2, -oa, -r1]); sf = occ.addPlaneSurface([cl])
        surfs.append(sf); ain.append(ia); aout.append(oa)
        # store transfinite AFTER synchronize (mesh module needs synced curves)
    return surfs, inner, outer, ain, aout

# ---- T1: OCC transfinite, no fragment ----
def T1():
    gmsh.model.add("t1")
    occ = gmsh.model.occ
    r_in, r_out = 12.1, 12.2
    seg = 12; M = 20; sector_deg = 360.0
    seam = [sector_deg*s/seg for s in range(seg+1)]
    surfs, inner, outer, ain, aout = gap_sectors_occ(occ, r_in, r_out, seam, 0, M)
    occ.synchronize()
    for s in range(seg):
        # radial edges: need their tags; get from surface boundary
        pass
    # set transfinite on every curve by role via geometry
    for (d,ct) in gmsh.model.getEntities(1):
        # classify: arc (both endpoints same r) vs radial (endpoints differ in r)
        bnd = gmsh.model.getBoundary([(1,ct)], oriented=False, combined=False)
        rr = [math.hypot(*gmsh.model.getValue(0,pt,[])[:2]) for (_,pt) in bnd]
        if len(rr)<2: continue
        if abs(rr[0]-rr[1])<1e-6:  # arc
            gmsh.model.mesh.setTransfiniteCurve(ct, M+1)
        else:                       # radial
            gmsh.model.mesh.setTransfiniteCurve(ct, 1+1)  # K=1 -> 2 nodes
    for (d,sf) in gmsh.model.getEntities(2):
        try: gmsh.model.mesh.setTransfiniteSurface(sf)
        except Exception as e: print("  setTF surf failed", e)
    gmsh.model.mesh.generate(2)
    lv = count_levels(get_mesh())
    print(f"T1 OCC no-fragment K=1: levels={[round(x,4) for x in lv]} (want [12.1,12.2]) "
          f"{'OK' if len(lv)==2 else 'FAIL'}")
    gmsh.model.remove()

# ---- T2: gap transfinite sectors + inner free disk, fragment-welded ----
def T2(do_fragment):
    gmsh.model.add("t2")
    occ = gmsh.model.occ
    r_in, r_out = 12.1, 12.2; seg=12; M=20
    seam=[360.0*s/seg for s in range(seg+1)]
    surfs, inner, outer, ain, aout = gap_sectors_occ(occ, r_in, r_out, seam, 0, M)
    # inner "iron" disk sharing the r_in circle (reuse the SAME inner points+arcs)
    cdisk = occ.addPoint(0,0,0)
    diskloop = occ.addCurveLoop(ain)   # reuse gap inner arcs as the disk boundary
    disk = occ.addPlaneSurface([diskloop])
    occ.synchronize()
    tf_curves=[]
    for (d,ct) in gmsh.model.getEntities(1):
        bnd = gmsh.model.getBoundary([(1,ct)], oriented=False, combined=False)
        rr=[math.hypot(*gmsh.model.getValue(0,pt,[])[:2]) for (_,pt) in bnd]
        if len(rr)<2: continue
        if abs(rr[0]-rr[1])<1e-6 and abs(rr[0]-r_in)<1e-6:
            gmsh.model.mesh.setTransfiniteCurve(ct, M+1); tf_curves.append(ct)
        elif abs(rr[0]-rr[1])<1e-6 and abs(rr[0]-r_out)<1e-6:
            gmsh.model.mesh.setTransfiniteCurve(ct, M+1); tf_curves.append(ct)
        elif abs(rr[0]-rr[1])>1e-6:
            gmsh.model.mesh.setTransfiniteCurve(ct, 2); tf_curves.append(ct)
    for sf in surfs:
        try: gmsh.model.mesh.setTransfiniteSurface(sf)
        except Exception as e: print("  setTF surf failed", e)
    gmsh.option.setNumber("Mesh.MeshSizeMax", 0.5)
    gmsh.option.setNumber("Mesh.MeshSizeMin", 0.02)
    gmsh.model.mesh.generate(2)
    P = get_mesh(); lv = count_levels(P)
    gap_lv = [x for x in lv if x >= r_in-1e-4]
    print(f"T2 fragment={do_fragment}: gap levels>={r_in}: {[round(x,4) for x in gap_lv]} "
          f"({'has 12.1&12.2' if any(abs(x-12.1)<1e-3 for x in lv) and any(abs(x-12.2)<1e-3 for x in lv) else 'MISSING'}) "
          f"total levels={len(lv)}")
    gmsh.model.remove()

def T3(do_fragment):
    """REALISTIC: gap sectors with their OWN arcs, iron disk with SEPARATE arcs at
    the same r_in (as _shapely_to_occ would build them), then fragment to weld.
    Does fragment keep the gap transfinite (exact K) AND make it conforming?"""
    gmsh.model.add("t3")
    occ = gmsh.model.occ
    r_in, r_out = 12.1, 12.2; seg=12; M=20
    seam=[360.0*s/seg for s in range(seg+1)]
    surfs, inner, outer, ain, aout = gap_sectors_occ(occ, r_in, r_out, seam, 0, M)
    # SEPARATE iron disk: its own points + polyline approx of the r_in circle
    npoly = 120
    dp = [occ.addPoint(r_in*math.cos(2*math.pi*i/npoly), r_in*math.sin(2*math.pi*i/npoly), 0)
          for i in range(npoly)]
    dl = [occ.addLine(dp[i], dp[(i+1)%npoly]) for i in range(npoly)]
    disk = occ.addPlaneSurface([occ.addCurveLoop(dl)])
    occ.synchronize()
    surf_ids = list(surfs)
    if do_fragment:
        allsurf = [(2,s) for s in surfs] + [(2,disk)]
        out, omap = occ.fragment(allsurf, [])
        occ.synchronize()
        # gap surfaces after fragment = images of the original gap surfaces
        surf_ids = []
        for i,s in enumerate(surfs):
            for (dd,tt) in omap[i]:
                if dd==2: surf_ids.append(tt)
    # set transfinite on gap sector curves+surfaces (post-fragment)
    for sfid in surf_ids:
        cs = gmsh.model.getBoundary([(2,sfid)], oriented=False, combined=False)
        for (d,ct) in cs:
            bnd = gmsh.model.getBoundary([(1,ct)], oriented=False, combined=False)
            rr=[math.hypot(*gmsh.model.getValue(0,pt,[])[:2]) for (_,pt) in bnd]
            if len(rr)<2: continue
            if abs(rr[0]-rr[1])<1e-6: gmsh.model.mesh.setTransfiniteCurve(ct, M+1)
            else: gmsh.model.mesh.setTransfiniteCurve(ct, 2)
        try: gmsh.model.mesh.setTransfiniteSurface(sfid)
        except Exception as e: print("  setTF surf failed", e)
    gmsh.option.setNumber("Mesh.MeshSizeMax", 0.5); gmsh.option.setNumber("Mesh.MeshSizeMin", 0.02)
    gmsh.model.mesh.generate(2)
    P = get_mesh(); lv = count_levels(P)
    has = any(abs(x-12.1)<1e-3 for x in lv) and any(abs(x-12.2)<1e-3 for x in lv)
    # check the gap band has EXACTLY 2 levels between 12.1 and 12.2 inclusive
    gap_lv = [x for x in lv if 12.1-1e-4 <= x <= 12.2+1e-4]
    print(f"T3 fragment={do_fragment}: gap band levels={[round(x,4) for x in gap_lv]} "
          f"(want exactly [12.1,12.2]) {'OK' if gap_lv==sorted(gap_lv) and len(gap_lv)==2 and has else 'CHECK'}")
    gmsh.model.remove()

gmsh.initialize(); gmsh.option.setNumber("General.Terminal", 0)
T1()
T2(False)
T3(False)
T3(True)
gmsh.finalize(); print("DONE")
