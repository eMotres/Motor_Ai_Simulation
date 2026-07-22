"""Minimal REAL P1-vs-P2 no-load cogging comparison (controlled: identical mesh,
sources, Picard and BC — only the element order differs).  Uses the module's
solve_magnetostatics_fem for BOTH orders and _arkkio_torque_p2 (order-agnostic).
Uses the ORIGINAL per-magnet tags from build_mesh_from_polygons (the centroid
reclassify collapses magnets to generic tags that carry no Mx/My)."""
import sys, math, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import logging; logging.disable(logging.CRITICAL)
import numpy as np
from motor_ai_sim.simulation.fem_solver_2d import (
    _field2d_static_inputs, build_mesh_from_polygons,
    solve_magnetostatics_fem, _arkkio_torque_p2,
)

H = float(sys.argv[1]) if len(sys.argv) > 1 else 1.6
NANG = int(sys.argv[2]) if len(sys.argv) > 2 else 3
period = 360.0 / math.lcm(12, 14)                      # 4.2857 deg mech
angles = np.linspace(0.0, period, NANG, endpoint=False)
print(f"P1-vs-P2 no-load cogging: mesh={H}mm, {NANG} angles over {period:.3f}deg", flush=True)

T1, T2 = [], []
for a in angles:
    t0 = time.time()
    polys, mats, p = _field2d_static_inputs(a, gamma_deg=0.0, I_phase_rms=0.0)
    mesh, tags, clf = build_mesh_from_polygons(polys, a, H)
    tags = np.asarray(tags).astype(int)                # ORIGINAL per-magnet tags
    r_in, r_out, stack = float(p.r_rotor_out), float(p.r_stator_in), float(p.stack_length)
    A1, b1 = solve_magnetostatics_fem(mesh, tags, mats, element_order=1)
    t_p1 = _arkkio_torque_p2(mesh, A1, b1, r_in, r_out, stack)
    A2, b2 = solve_magnetostatics_fem(mesh, tags, mats, element_order=2)
    t_p2 = _arkkio_torque_p2(mesh, A2, b2, r_in, r_out, stack)
    T1.append(t_p1); T2.append(t_p2)
    print(f"theta={a:6.3f}  P1={t_p1:+.6e}  P2={t_p2:+.6e}  N.m   "
          f"tris={mesh.t.shape[1]} p1dofs={b1.N} p2dofs={b2.N} ({time.time()-t0:.1f}s)",
          flush=True)

T1, T2 = np.array(T1), np.array(T2)
if T1.size > 1:
    print(f"\nP1 cogging p-p = {(T1.max()-T1.min())*1e3:.4f} mN.m")
    print(f"P2 cogging p-p = {(T2.max()-T2.min())*1e3:.4f} mN.m")
