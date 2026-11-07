"""Aggregate a cProfile dump from profile_fem_run.py into stage/module tables.

  python summarize_profile.py prof.pstats [--top 40] [--json out.json]

Prints (1) own time (tottime) summed per module group, (2) the top functions
by own time, (3) cumulative time of the solver's named stages.  Own time is
disjoint, so the module table adds up to the profiled total.
"""
from __future__ import annotations

import argparse
import json
import pstats
from collections import defaultdict

GROUPS = (
    ("pypardiso / MKL", ("pypardiso",)),
    ("scipy.sparse (construction, products, slicing)", ("scipy/sparse",)),
    ("scipy.sparse.linalg (SuperLU etc.)", ("scipy/sparse/linalg",)),
    ("skfem (assembly, bases)", ("skfem",)),
    ("numpy", ("numpy",)),
    ("mesher / geometry (motor_ai_sim)", ("mesher.py", "geo_mesh", "geometry_2d", "iron_template",
                                         "moving_band", "sb_domains", "rotor_window")),
    ("triangle / gmsh / shapely", ("triangle", "gmsh", "shapely")),
    ("P2 nonlinear kernels", ("p2_nonlinear.py",)),
    ("P2 drive (eddy / voltage Newton)", ("p2_drive.py",)),
    ("projection / sliding band", ("p2_projection.py",)),
    ("losses / postproc", ("losses.py", "postproc.py", "postprocess.py", "sb_postproc.py",
                           "torque_", "demag.py", "field_ops.py", "conductor_skin.py")),
    ("fem_solver_2d body", ("fem_solver_2d.py",)),
    ("eddy_solver_2d (cross-check)", ("eddy_solver_2d.py",)),
)

STAGES = (
    "_build_sliding_band_meshes", "_calibrate_daxis", "_resolve_daxis_shift",
    "noload_psi_pm", "noload_incremental_ldq", "frozen_permeability_ldq",
    "build_materials", "solve_ff", "_solve_reuse", "Kpw", "tangent2", "asmK", "elemB",
    "pic2_sweeps", "eddy_solve", "eddy_static_state", "ve_newton", "v_newton",
    "rotor_eddy_solver_bc", "_iron_p2", "_psi2", "asm", "bmat", "_call_pardiso",
)


def group_of(fname: str) -> str:
    f = fname.replace("\\", "/")
    # most specific first: scipy.sparse.linalg before scipy.sparse
    if "scipy/sparse/linalg" in f:
        return "scipy.sparse.linalg (SuperLU etc.)"
    for name, keys in GROUPS:
        if name.startswith("scipy.sparse.linalg"):
            continue
        if any(k in f for k in keys):
            return name
    if f.startswith("~") or f == "~":
        return "builtins / C calls"
    return "other python"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pstats")
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--json")
    a = ap.parse_args()
    st = pstats.Stats(a.pstats)
    total = st.total_tt
    groups = defaultdict(float)
    rows = []
    stages = defaultdict(lambda: [0.0, 0])
    for (fname, line, func), (cc, nc, tt, ct, callers) in st.stats.items():
        g = group_of(fname)
        if fname == "~":
            # C builtins: attribute sparse/numpy C calls by name
            fl = func.lower()
            if "pardiso" in fl:
                g = "pypardiso / MKL"
            elif "_sparsetools" in fl or "scipy.sparse" in fl:
                g = "scipy.sparse (construction, products, slicing)"
            elif "superlu" in fl or "_superlu" in fl:
                g = "scipy.sparse.linalg (SuperLU etc.)"
            elif "numpy" in fl or "ndarray" in fl or "ufunc" in fl:
                g = "numpy"
            else:
                g = "builtins / C calls"
        groups[g] += tt
        rows.append((tt, ct, nc, f"{fname.split('/')[-1]}:{line}:{func}"))
        if func in STAGES:
            stages[func][0] = max(stages[func][0], ct)
            stages[func][1] += nc
    print(f"profiled total {total:.2f} s")
    print("\n== own time by module group ==")
    for g, t in sorted(groups.items(), key=lambda z: -z[1]):
        print(f"{t:10.2f} s {100 * t / total:6.1f} %  {g}")
    print(f"\n== top {a.top} functions by own time ==")
    for tt, ct, nc, name in sorted(rows, reverse=True)[:a.top]:
        print(f"{tt:9.2f} {ct:9.2f} {nc:9d}  {name}")
    print("\n== named stages (cumulative, may overlap) ==")
    for k, (ct, nc) in sorted(stages.items(), key=lambda z: -z[1][0]):
        print(f"{ct:9.2f} s {100 * ct / total:6.1f} % {nc:8d} calls  {k}")
    if a.json:
        json.dump(dict(total=total, groups=dict(groups),
                       top=[dict(tt=r[0], ct=r[1], nc=r[2], name=r[3])
                            for r in sorted(rows, reverse=True)[:a.top]],
                       stages={k: dict(ct=v[0], nc=v[1]) for k, v in stages.items()}),
                  open(a.json, "w"), indent=1)


if __name__ == "__main__":
    main()
