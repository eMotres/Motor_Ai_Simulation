"""Profile the FEM at increasing mesh resolution:
  1) scaling table  mesh_size → vertices / triangles / wall-time / torque
  2) stage breakdown (mesh gen / assembly / linear solve) via cProfile
so we can see whether the LINEAR SOLVE is the bottleneck (→ worth a GPU/CuPy
sparse solver) or whether mesh+assembly dominate (→ GPU solve won't help much)."""
import os, sys, time, cProfile, pstats, io
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
from motor_ai_sim.simulation.fem_solver_2d import fem_solve_for_sim


def run(ms, mn):
    t0 = time.time()
    r = fem_solve_for_sim(rotor_angle_deg=0.0, gamma_deg=0.0,
                          mesh_size_mm=ms, min_size_mm=mn, n_sectors=4)
    return time.time() - t0, int(r["n_vertices"]), int(r["n_triangles"]), float(r.get("T_em_Nm", 0))


# warm-up (exclude one-time imports / gmsh init / cold caches)
_ = run(4.0, 0.5)
print("warm-up done\n")

print(f"{'mesh_mm':>8} {'min_mm':>7} {'verts':>9} {'tris':>10} {'DOF~':>9} {'time_s':>8} {'torque':>8}")
grid = [(4.0, 0.5), (2.5, 0.35), (1.6, 0.25), (1.1, 0.18), (0.8, 0.13)]
for ms, mn in grid:
    try:
        dt, nv, nt, T = run(ms, mn)
        print(f"{ms:8.2f} {mn:7.2f} {nv:9d} {nt:10d} {nv:9d} {dt:8.2f} {T:8.2f}", flush=True)
    except Exception as e:
        print(f"{ms:8.2f}  FAILED: {e}", flush=True)

# ── stage breakdown at a medium-fine mesh ────────────────────────────────────
print("\n=== cProfile @ mesh=1.1mm (cumulative top) ===")
pr = cProfile.Profile(); pr.enable()
run(1.1, 0.18)
pr.disable()
s = io.StringIO()
pstats.Stats(pr, stream=s).sort_stats("cumulative").print_stats(30)
KEYS = ("gmsh", "generate", "build_mesh", "build_mesh_from", "asm", "assemble",
        "bilinear", "linearform", "condense", "solve", "spsolve", "splu",
        "factor", "lu", "csr", "coo", "tocsr", "fem_solve_for_sim", "get_2d_polygons",
        "cadquery", "interp", "torque", "loss")
for line in s.getvalue().splitlines():
    ll = line.lower()
    if any(k in ll for k in KEYS):
        print(line)

print("\n=== cProfile @ mesh=1.1mm (tottime top — heaviest leaves) ===")
s2 = io.StringIO()
pstats.Stats(pr, stream=s2).sort_stats("tottime").print_stats(15)
print("\n".join(s2.getvalue().splitlines()[:25]))
