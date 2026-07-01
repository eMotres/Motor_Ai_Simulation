"""Check the slip ring at mid=12.2 for each structured half: are there nodes,
and do they land on the uniform 2*pi*j/n_slip grid?  n_slip=1008."""
import _use40  # noqa
import math, numpy as np, logging
logging.basicConfig(level=logging.WARNING)
import motor_ai_sim.simulation.fem_solver_2d as F
from motor_ai_sim.cadquery_geometry import CadQueryMotor

m = CadQueryMotor()
polys = F._simplify_polys(m.get_2d_polygons(rotor_angle_deg=0.0), tol_mm=0.005,
                          structured_gap=True, gap_layers=2, n_slip=1008)
polys_s, polys_r = F._split_polys_for_sliding_band(polys)
mid = 12.2
n_slip = 1008
kw = dict(mesh_size_mm=1.5, min_size_mm=0.25, normal_deviation_deg=6.0,
          aspect_ratio=10.0, gap_layers=2)

for name, ph in (("ROTOR", polys_r), ("STATOR", polys_s)):
    mesh, tags, clf = F.build_mesh_from_polygons(ph, n_sectors=2, **kw)
    P = np.asarray(mesh.p) * 1000.0   # m -> mm
    r = np.hypot(P[0], P[1])
    sel = np.abs(r - mid) < 1e-3
    ang = np.degrees(np.arctan2(P[1, sel], P[0, sel])) % 360.0
    step = 360.0 / n_slip
    kg = np.round(ang / step)
    on_grid = np.abs(ang - kg * step) < (0.05 * step)
    print(f"\n{name}: nodes at r~{mid}: {int(sel.sum())}, on-grid: {int(on_grid.sum())}")
    if sel.sum():
        print(f"   r range at mid-band: {r[sel].min():.5f}..{r[sel].max():.5f}")
        # radial levels near mid
        rr = r[(r > mid - 0.06) & (r < mid + 0.06)]
        lv = []
        for v in np.sort(np.unique(np.round(rr, 4))):
            if not lv or v - lv[-1] > 2e-3:
                lv.append(round(float(v), 4))
        print(f"   radial levels near mid: {lv}")
