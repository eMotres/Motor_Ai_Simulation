"""PROTOTYPE step 1: build a transfinite gap annulus as a standalone skfem MeshTri
and verify it yields EXACTLY K uniform radial layers with a uniform node ring on
the 2*pi*j/N_slip grid at BOTH inner and outer radii.

Config motor half-disk: n_sectors=2 -> the gap covers a 180-deg wedge.
Rotor half-gap: r_ro=12.1 -> mid=12.2.  Stator half-gap: mid=12.2 -> r_si=12.299.
"""
import math, numpy as np, gmsh

def build_tf_gap(r_in, r_out, K, N_slip, sector_deg, seg_sectors):
    """Transfinite annulus wedge [0, sector_deg] deg, r_in..r_out, K radial layers.

    The angular node grid is 2*pi*j/N_slip (the slip grid).  The wedge spans
    sector_deg degrees => it holds M_total = N_slip*sector_deg/360 angular
    intervals, i.e. M_total+1 nodes from angle 0 to sector_deg.  We split the
    wedge into `seg_sectors` transfinite sectors each carrying M = M_total/seg_sectors
    angular divisions.  Returns (P[2,n] mm, T[3,m]).
    """
    step = 360.0 / N_slip                       # deg per slip node
    M_total = int(round(sector_deg / step))     # angular intervals over the wedge
    assert abs(M_total * step - sector_deg) < 1e-9, (M_total, step, sector_deg)
    assert M_total % seg_sectors == 0, (M_total, seg_sectors)
    M = M_total // seg_sectors                  # angular divisions per sector

    gmsh.model.add(f"gap_{r_in}_{r_out}_{K}")
    geo = gmsh.model.geo
    c = geo.addPoint(0, 0, 0)
    # seam angles (deg) at sector boundaries — all on the slip grid
    seam_ang = [ (sector_deg * s / seg_sectors) for s in range(seg_sectors + 1) ]
    def _pt(r, ang_deg):
        a = math.radians(ang_deg)
        return geo.addPoint(r * math.cos(a), r * math.sin(a), 0)
    inner = [_pt(r_in, a) for a in seam_ang]
    outer = [_pt(r_out, a) for a in seam_ang]
    for s in range(seg_sectors):
        ia = geo.addCircleArc(inner[s], c, inner[s + 1])
        oa = geo.addCircleArc(outer[s], c, outer[s + 1])
        r1 = geo.addLine(inner[s], outer[s])
        r2 = geo.addLine(inner[s + 1], outer[s + 1])
        cl = geo.addCurveLoop([ia, r2, -oa, -r1])
        surf = geo.addPlaneSurface([cl])
        geo.mesh.setTransfiniteCurve(ia, M + 1)
        geo.mesh.setTransfiniteCurve(oa, M + 1)
        geo.mesh.setTransfiniteCurve(r1, K + 1)
        geo.mesh.setTransfiniteCurve(r2, K + 1)
        geo.mesh.setTransfiniteSurface(surf)
    geo.synchronize()
    gmsh.model.mesh.generate(2)
    _, nc, _ = gmsh.model.mesh.getNodes()
    P = nc.reshape(-1, 3)[:, :2].T.copy()
    _, etn = gmsh.model.mesh.getElementsByType(2)
    # node tags -> compact index
    ntags, ncoords, _ = gmsh.model.mesh.getNodes()
    tag2idx = {int(t): i for i, t in enumerate(ntags)}
    T = np.array([tag2idx[int(t)] for t in etn], int).reshape(-1, 3).T
    Pfull = ncoords.reshape(-1, 3)[:, :2].T.copy()
    gmsh.model.remove()
    return Pfull, T

def analyze(P, T, r_in, r_out, K, N_slip, sector_deg, label):
    r = np.hypot(P[0], P[1])
    # distinct radial levels (ignore the isolated origin/center point r~0)
    lv = []
    for v in np.sort(np.unique(np.round(r[r > 1e-6], 4))):
        if not lv or v - lv[-1] > 1e-3:
            lv.append(v)
    step = 360.0 / N_slip
    # inner ring node angles
    def ring_angles(rr):
        idx = np.where(np.abs(r - rr) < 1e-5)[0]
        ang = np.degrees(np.arctan2(P[1, idx], P[0, idx])) % 360.0
        return np.sort(ang)
    ai = ring_angles(r_in); ao = ring_angles(r_out)
    on_grid_i = np.all(np.abs(ai - np.round(ai / step) * step) < 1e-4 * step + 1e-6)
    on_grid_o = np.all(np.abs(ao - np.round(ao / step) * step) < 1e-4 * step + 1e-6)
    exp_layers = K + 1
    ok_layers = (len(lv) == exp_layers)
    print(f"[{label}] K={K}: radial levels={len(lv)} (want {exp_layers}) "
          f"{'OK' if ok_layers else 'FAIL'}  levels={[round(x,4) for x in lv]}")
    print(f"    tris={T.shape[1]}  inner ring nodes={ai.size} (grid={on_grid_i})  "
          f"outer ring nodes={ao.size} (grid={on_grid_o})")
    # layer spacing uniformity
    if len(lv) >= 2:
        d = np.diff(lv)
        print(f"    layer spacing: {[round(x,5) for x in d]}  (want ~{(r_out-r_in)/K:.5f})")
    return ok_layers and on_grid_i and on_grid_o

gmsh.initialize(); gmsh.option.setNumber("General.Terminal", 0)
# config motor
r_ro, mid, r_si = 12.1, 12.2, 12.29907506319128
sector_deg = 180.0                     # n_sectors=2 half disk
# pick N_slip like the transient does: pole_pairs=7, per_period=24*ceil(...); use 336 (=7*48)
# For prototype, any N_slip divisible so that N_slip*sector/360 is integer & divisible by seg.
# transient uses n_slip_eff = pole_pairs * slip_per_period. Half disk => M_total = n_slip/2.
# Take a representative n_slip_eff; e.g. 7*168=1176 -> half=588. Use seg_sectors that divides 588.
for N_slip in (1176,):
    M_total_half = int(round(sector_deg / (360.0 / N_slip)))
    print(f"N_slip={N_slip}  M_total over 180deg = {M_total_half}")
    # choose seg_sectors dividing M_total_half, aim ~ one seam per few deg but keep aspect ok
    # 588 = 2^2 * 3 * 7^2 -> divisors include 12,14,28,42,84...
    seg = 84
    for K in (1, 2, 3):
        allok = True
        for (ri, ro, lab) in ((r_ro, mid, "rotor"), (mid, r_si, "stator")):
            P, T = build_tf_gap(ri, ro, K, N_slip, sector_deg, seg)
            ok = analyze(P, T, ri, ro, K, N_slip, sector_deg, lab)
            allok = allok and ok
        print(f"  => gap_layers={K}: total gap rings across both halves = {2*K}  "
              f"{'ALL-OK' if allok else 'PROBLEM'}\n")
gmsh.finalize(); print("DONE")
