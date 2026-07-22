"""Minimal real P1-vs-P2 comparison: 3 rotor angles, one coarse mesh.
Prints each angle's torque the instant it's computed (unbuffered)."""
import sys, math, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import logging; logging.disable(logging.CRITICAL)
import numpy as np
from motor_ai_sim.simulation.fem_solver_2d import (
    _field2d_static_inputs, build_mesh_from_polygons,
    solve_magnetostatics, _arkkio_torque,
    solve_magnetostatics_p2, _arkkio_torque_p2,
)

H = 3.0                                   # coarse mesh (mm) — speed over accuracy
period = 360.0 / math.lcm(12, 14)         # 4.2857 deg mech
angles = np.linspace(0.0, period, 3, endpoint=False)   # 3 angles across one period
print(f"P1-vs-P2 quick: mesh={H}mm, angles={np.round(angles,3).tolist()}", flush=True)

T1, T2 = [], []
for a in angles:
    t0 = time.time()
    polys, mats, p = _field2d_static_inputs(a, gamma_deg=0.0, I_phase_rms=0.0)
    mesh, tags, clf = build_mesh_from_polygons(polys, a, H)
    c = mesh.p[:, mesh.t].mean(axis=1)
    tags = np.array([clf(c[0, i]*1e3, c[1, i]*1e3) for i in range(c.shape[1])], dtype=np.int32)
    r_in, r_out, stack = float(p.r_rotor_out), float(p.r_stator_in), float(p.stack_length)
    A1 = solve_magnetostatics(mesh, tags, mats)
    t_p1 = _arkkio_torque(mesh, A1, r_in, r_out, stack)
    A2, b2 = solve_magnetostatics_p2(mesh, tags, mats)
    t_p2 = _arkkio_torque_p2(mesh, A2, b2, r_in, r_out, stack)
    T1.append(t_p1); T2.append(t_p2)
    print(f"theta={a:6.3f}  P1={t_p1:+.6e}  P2={t_p2:+.6e}  "
          f"nodes={mesh.p.shape[1]} tris={mesh.t.shape[1]} p2dofs={b2.N} "
          f"({time.time()-t0:.1f}s)", flush=True)

T1, T2 = np.array(T1), np.array(T2)
print(f"\nP1 torque: min={T1.min():+.4e} max={T1.max():+.4e} p-p={ (T1.max()-T1.min())*1e3:.4f} mN.m")
print(f"P2 torque: min={T2.min():+.4e} max={T2.max():+.4e} p-p={ (T2.max()-T2.min())*1e3:.4f} mN.m")
