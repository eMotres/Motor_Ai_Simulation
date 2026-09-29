"""Check coil-current assignment consistency across the 3 symmetry modes.
For the sector reductions to match the full disk, the 24-slot winding layout
must be: ANTI-periodic over 6 slots (n=4 sector, 7 poles=odd → anti-periodic BC)
and PERIODIC over 12 slots (n=2 sector, 14 poles=even → periodic BC).
Then dump the actual per-coil (angle→slot_idx→phase,dir) each mode applies."""
import math
from motor_ai_sim.simulation.geometry_2d import params_from_config, MotorDomains2D
from motor_ai_sim.cadquery_geometry import CadQueryMotor
from motor_ai_sim.simulation.fem_solver_2d import _simplify_polys, _clip_polys_to_sector

p = params_from_config()
d = MotorDomains2D(p)
L = d.winding_layout
n_slot = len(L)
print("n_slot =", n_slot, " poles =", p.num_poles)
print("layout:", " ".join("%s%s" % (ph, '+' if s > 0 else '-') for ph, s in L))


def chk_anti(L, step):
    n = len(L)
    bad = [i for i in range(n)
           if L[i][0] != L[(i + step) % n][0] or L[(i + step) % n][1] != -L[i][1]]
    return bad


def chk_per(L, step):
    n = len(L)
    bad = [i for i in range(n) if L[i] != L[(i + step) % n]]
    return bad


print("\n--- WINDING SYMMETRY (physics requirement) ---")
print("anti-periodic over 6  (REQUIRED for n=4):", "OK" if not chk_anti(L, 6) else "FAIL @ %s" % chk_anti(L, 6))
print("periodic      over 12 (REQUIRED for n=2):", "OK" if not chk_per(L, 12) else "FAIL @ %s" % chk_per(L, 12))
print("(diag) periodic over 6 :", "yes" if not chk_per(L, 6) else "no")
print("(diag) anti over 12    :", "yes" if not chk_anti(L, 12) else "no")

slot_pitch = 360.0 / n_slot
half = slot_pitch * 0.5


def slot_of(ang):
    if ang < 0:
        ang += 360.0
    return int((ang - half) / slot_pitch + 0.5) % n_slot


def dump(polys, label):
    coils = polys.get("coils", [])
    rows = []
    for cp in coils:
        if cp is None or getattr(cp, "is_empty", True):
            continue
        cx, cy = cp.centroid.x, cp.centroid.y
        ang = math.degrees(math.atan2(cy, cx))
        si = slot_of(ang)
        ph, dr = L[si]
        rows.append((ang if ang >= 0 else ang + 360, si, ph, dr))
    rows.sort()
    print("\n== %s : %d coil polygons ==" % (label, len(rows)))
    line = "  ".join("%5.1f:%s%s" % (a, ph, '+' if dr > 0 else '-') for a, si, ph, dr in rows)
    print(" ", line)
    # net current sign per phase (sum of directions) — should be 0 for balanced
    from collections import defaultdict
    net = defaultdict(int)
    for a, si, ph, dr in rows:
        net[ph] += dr
    print("  net direction sum per phase:", dict(net))


m = CadQueryMotor()
po_full = _simplify_polys(m.get_2d_polygons(rotor_angle_deg=0.0), tol_mm=0.005)
dump(po_full, "FULL (n=1)")
po2 = _clip_polys_to_sector(_simplify_polys(m.get_2d_polygons(rotor_angle_deg=0.0), tol_mm=0.005), n_sectors=2)
dump(po2, "1/2 (n=2)")
po4 = _clip_polys_to_sector(_simplify_polys(m.get_2d_polygons(rotor_angle_deg=0.0), tol_mm=0.005), n_sectors=4)
dump(po4, "1/4 (n=4)")
print("\nDONE")
