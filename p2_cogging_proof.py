"""Stage-1 standalone proof: P1 vs P2 no-load cogging torque.

For the 40 mm 12-slot/14-pole surface-PM motor at I = 0 (no load) we sweep the
rotor through ONE cogging period (4.2857 deg mech) and compute cogging torque(theta)
with BOTH P1 (linear) and P2 (quadratic) elements on the *same* remesh-per-angle
geometry, at two mesh densities.  P2 makes B = curl A linear per element instead of
piecewise-constant, so the Arkkio torque is smooth where P1 staircases / carries
broadband forbidden-order discretization noise.

Reuses the real solver machinery (all imported from fem_solver_2d):
  - mesh + material + operating-point:  _field2d_static_inputs, build_mesh_from_polygons
  - P1 field solve + torque:            solve_magnetostatics, _arkkio_torque
  - P2 field solve + torque (Stage 2):  solve_magnetostatics_p2, _arkkio_torque_p2

Run:
    python -u p2_cogging_proof.py --smoke                     # one angle, sanity
    python -u p2_cogging_proof.py --n 5 --coarse 2.0 --fine 1.4
"""
import sys, math, time, argparse
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import logging
logging.disable(logging.CRITICAL)

import numpy as np

from motor_ai_sim.simulation.fem_solver_2d import (
    _field2d_static_inputs, build_mesh_from_polygons,
    solve_magnetostatics, _arkkio_torque,
    solve_magnetostatics_p2, _arkkio_torque_p2,
)


def _build_case(rotor_angle_deg: float, mesh_size_mm: float):
    """Fresh conforming mesh + centroid-reclassified tags + materials at a rotor
    angle, with I = 0 (no-load cogging).  Mirrors fem_field2d (remesh-per-angle)."""
    polys, mats, p = _field2d_static_inputs(
        rotor_angle_deg, gamma_deg=0.0, I_phase_rms=0.0)     # I=0 -> no coil source
    mesh, cell_tags, classify_fn = build_mesh_from_polygons(
        polys, rotor_angle_deg, mesh_size_mm)
    c_m = mesh.p[:, mesh.t].mean(axis=1)                     # (2, n_tri) metres
    cell_tags = np.array(
        [classify_fn(c_m[0, i] * 1e3, c_m[1, i] * 1e3)
         for i in range(c_m.shape[1])], dtype=np.int32)
    return mesh, cell_tags, mats, p


def _gap_radii(p):
    return float(p.r_rotor_out), float(p.r_stator_in)


def _jitter(T):
    """Step-to-step jitter = RMS of the 2nd difference of T(theta) — the
    staircase / discretization-noise signature (0 on a perfectly smooth wave)."""
    T = np.asarray(T, float)
    if T.size < 3:
        return 0.0
    return float(np.sqrt(np.mean(np.diff(T, 2) ** 2)))


def run(n_angles, densities, smoke=False):
    period = 360.0 / math.lcm(12, 14)      # 4.2857 deg mech
    angles = np.array([0.0]) if smoke else np.linspace(0.0, period, n_angles,
                                                       endpoint=False)
    print(f"Cogging period = {period:.4f} deg mech; sampling {len(angles)} angles")
    print(f"Air-gap Arkkio band = [r_rotor_out, r_stator_in]")
    results = {}
    for h in densities:
        print(f"\n=== mesh_size = {h:.2f} mm ===")
        T1, T2 = [], []
        nnod = ntri = np2 = 0
        for a in angles:
            t0 = time.time()
            mesh, tags, mats, p = _build_case(a, h)
            r_in, r_out = _gap_radii(p)
            stack = float(p.stack_length)
            A1 = solve_magnetostatics(mesh, tags, mats)
            t1 = _arkkio_torque(mesh, A1, r_in, r_out, stack)
            A2, b2 = solve_magnetostatics_p2(mesh, tags, mats)
            t2 = _arkkio_torque_p2(mesh, A2, b2, r_in, r_out, stack)
            T1.append(t1); T2.append(t2)
            nnod, ntri, np2 = mesh.p.shape[1], mesh.t.shape[1], b2.N
            print(f"  theta={a:6.3f}  P1={t1:+.5e}  P2={t2:+.5e}  "
                  f"({time.time()-t0:4.1f}s)", flush=True)
        T1, T2 = np.array(T1), np.array(T2)
        pp1 = (T1.max() - T1.min()) if T1.size > 1 else 0.0
        pp2 = (T2.max() - T2.min()) if T2.size > 1 else 0.0
        results[h] = dict(T1=T1, T2=T2, pp1=pp1, pp2=pp2,
                          nnod=nnod, ntri=ntri, np2=np2)
        print(f"  nodes(P1)={nnod}  tris={ntri}  dofs(P2)={np2}")
        if not smoke:
            print(f"  P1 cogging p-p = {pp1*1e3:.4f} mN.m   "
                  f"jitter(d2 RMS) = {_jitter(T1)*1e3:.4f} mN.m")
            print(f"  P2 cogging p-p = {pp2*1e3:.4f} mN.m   "
                  f"jitter(d2 RMS) = {_jitter(T2)*1e3:.4f} mN.m")
    if len(densities) == 2 and not smoke:
        hc, hf = densities
        print("\n=== CONVERGENCE (coarse -> fine) ===")
        print(f"  P1 p-p: {results[hc]['pp1']*1e3:.4f} -> "
              f"{results[hf]['pp1']*1e3:.4f} mN.m  "
              f"(delta {abs(results[hf]['pp1']-results[hc]['pp1'])*1e3:.4f})")
        print(f"  P2 p-p: {results[hc]['pp2']*1e3:.4f} -> "
              f"{results[hf]['pp2']*1e3:.4f} mN.m  "
              f"(delta {abs(results[hf]['pp2']-results[hc]['pp2'])*1e3:.4f})")
    return angles, results


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--coarse", type=float, default=2.0)
    ap.add_argument("--fine", type=float, default=1.4)
    args = ap.parse_args()
    dens = [args.coarse] if args.smoke else [args.coarse, args.fine]
    t0 = time.time()
    run(args.n, dens, smoke=args.smoke)
    print(f"\nTotal {time.time()-t0:.1f}s")
