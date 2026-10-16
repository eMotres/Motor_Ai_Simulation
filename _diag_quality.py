"""Tag histogram + triangle-quality stats for ns=1 vs ns=4. Find where shaft
cells went and how many slivers the full-disk mesh has. Pure numeric."""
import numpy as np, math
from motor_ai_sim.simulation.fem_solver_2d import (
    build_mesh_from_polygons, _simplify_polys,
    DOM_AIR, DOM_STATOR, DOM_COIL, DOM_AIRGAP, DOM_ROTOR, DOM_SHAFT,
    DOM_BAND, DOM_OUTER, DOM_MAG_BASE, DOM_COIL_BASE)
from motor_ai_sim.cadquery_geometry import CadQueryMotor

NAME = {DOM_AIR: "air", DOM_STATOR: "stator", DOM_COIL: "coil",
        DOM_AIRGAP: "airgap", DOM_ROTOR: "rotor", DOM_SHAFT: "shaft",
        DOM_BAND: "band", DOM_OUTER: "outer"}


def tri_quality(P, T):
    v = P[:, T]                                   # (2,3,ntri)
    a = np.hypot(*(v[:, 1] - v[:, 0]))
    b = np.hypot(*(v[:, 2] - v[:, 1]))
    c = np.hypot(*(v[:, 0] - v[:, 2]))
    s = (a + b + c) / 2
    area = np.sqrt(np.maximum(s * (s - a) * (s - b) * (s - c), 0))
    q = 4 * math.sqrt(3) * area / np.maximum(a * a + b * b + c * c, 1e-30)
    return area, q


for ns in (4, 1):
    m = CadQueryMotor()
    po = m.get_2d_polygons(rotor_angle_deg=0.0)
    po = _simplify_polys(po, tol_mm=0.005, stator_fillet_mm=0.0)
    mesh, ct, cf = build_mesh_from_polygons(
        po, 0.0, 4.0, min_size_mm=0.3, outer_air_factor=1.3,
        motion_band=True, band_thickness_mm=0.4, n_sectors=ns,
        geo_cfg=m.parameters)
    ct = ct.astype(np.int32)
    area, q = tri_quality(mesh.p, mesh.t)
    mag = int(((ct >= DOM_MAG_BASE) & (ct < DOM_COIL_BASE)).sum())
    coil = int((ct >= DOM_COIL_BASE).sum())
    print("=== ns=%d   tris=%d   nodes=%d ===" % (ns, ct.size, mesh.p.shape[1]), flush=True)
    parts = []
    for tag in (DOM_AIR, DOM_STATOR, DOM_AIRGAP, DOM_ROTOR, DOM_SHAFT, DOM_BAND, DOM_OUTER):
        parts.append("%s=%d" % (NAME[tag], int((ct == tag).sum())))
    parts.append("magnets=%d" % mag); parts.append("coils=%d" % coil)
    print("   " + "  ".join(parts), flush=True)
    print("   area mm2: min=%.2e med=%.3f max=%.3f | quality min=%.4f med=%.3f | slivers q<0.1: %d (%.1f%%)"
          % (area.min(), np.median(area), area.max(), q.min(), np.median(q),
             int((q < 0.1).sum()), 100 * (q < 0.1).mean()), flush=True)
