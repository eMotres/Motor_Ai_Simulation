"""The measurements that decide the Stage-A formulation.

Every study here compares the solver against ``exact.py`` and prints the error
NEXT TO the cost, because the only question Stage A actually has to answer is
"what accuracy per second per gigabyte", not "can it be made accurate".

Runnable:

    python -m motor_ai_sim.simulation.static3d.benchmarks --all
    python -m motor_ai_sim.simulation.static3d.benchmarks --sphere --fine
    python -m motor_ai_sim.simulation.static3d.benchmarks --truncation
    python -m motor_ai_sim.simulation.static3d.benchmarks --nedelec

The default sizes are the small ones the pytest suite uses.  ``--fine`` is the
publication-grade sweep; it is single-threaded by default (``MKL_NUM_THREADS=1``
unless already set) so it can share a machine with a running optimisation.
"""
from __future__ import annotations

import argparse
import math
import os
import time
from typing import Dict, List, Optional, Sequence

import numpy as np

from motor_ai_sim.simulation.static3d import exact
from motor_ai_sim.simulation.static3d.exact import MU0
from motor_ai_sim.simulation.static3d.meshes import (
    cylinder_in_box, cylinder_with_iron_ring, sphere_in_box,
)
from motor_ai_sim.simulation.static3d.solver import (
    Region, solve_static3d, solve_static3d_nonlinear,
)

M_MAG = 1.0e6            # A/m  ->  mu0*M = 1.257 T remanence, a real NdFeB level


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _table(rows: List[dict], cols: Sequence[str], title: str = "") -> str:
    if title:
        out = [title, "-" * len(title)]
    else:
        out = []
    w = {c: max(len(c), *(len(f"{r.get(c, '')}") for r in rows)) for c in cols}
    out.append("  ".join(c.rjust(w[c]) for c in cols))
    for r in rows:
        out.append("  ".join(f"{r.get(c, '')}".rjust(w[c]) for c in cols))
    return "\n".join(out)


def _rate(h: Sequence[float], e: Sequence[float]) -> List[Optional[float]]:
    """Observed convergence order between consecutive refinements."""
    out: List[Optional[float]] = [None]
    for i in range(1, len(h)):
        if e[i] > 0 and e[i - 1] > 0 and h[i] != h[i - 1]:
            out.append(math.log(e[i - 1] / e[i]) / math.log(h[i - 1] / h[i]))
        else:
            out.append(None)
    return out


def _fmt(x, n=3):
    return "-" if x is None else f"{x:.{n}f}"


# --------------------------------------------------------------------------
# (a) uniformly magnetised sphere
# --------------------------------------------------------------------------

def sphere_case(h: float, order: int, radius: float = 0.01,
                box_factor: float = 6.0, M: float = M_MAG) -> dict:
    """One sphere solve.  Returns the error/cost record.

    The exact interior field is uniform, (2/3) mu0 M along z, so the L2 and max
    errors below are measured against a CONSTANT — there is nowhere for a
    plausible-looking wrong answer to hide."""
    t0 = time.perf_counter()
    tm = sphere_in_box(radius=radius, box_factor=box_factor, h_magnet=h)
    t_mesh = time.perf_counter() - t0

    regs = [Region("air", tm.elements("air")),
            Region("magnet", tm.elements("magnet"), mu_r=1.0, M=(0.0, 0.0, M))]
    sol = solve_static3d(tm.mesh, regs, order=order)

    mag = tm.elements("magnet")
    Bref = exact.sphere_B_inside(M)

    def _norms(els):
        B, dx = sol.B_quadrature(els)
        Bex = np.zeros_like(B)
        Bex[2] = Bref
        d2 = ((B - Bex) ** 2).sum(axis=0)
        return (math.sqrt(float((d2 * dx).sum()) / float(dx.sum())) / Bref,
                math.sqrt(float(d2.max())) / Bref)

    l2, mx = _norms(mag)
    # Same norms over the CORE only (centroids inside 0.75 R).  The sphere the
    # solver actually sees is a faceted polyhedron, so the elements touching the
    # surface carry a geometry error that no element order can remove; the core
    # numbers are the ones that measure the FORMULATION.  Reporting only one of
    # the two would be a choice about which story to tell.
    cen = np.asarray(tm.mesh.p)[:, np.asarray(tm.mesh.t)[:, mag]].mean(axis=1)
    core = mag[np.linalg.norm(cen, axis=0) < 0.75 * radius]
    l2c, mxc = _norms(core) if core.size else (float("nan"), float("nan"))

    meanB = sol.region_mean_B("magnet")
    mean_err = abs(meanB[2] - exact.sphere_B_inside(M)) / exact.sphere_B_inside(M)

    # exterior spot check on the exact dipole, one radius outside the pole
    ppt = np.array([[0.0], [0.0], [2.0 * radius]])
    Bfem = sol.B_at_points(ppt)[:, 0]
    Bexact = exact.sphere_B(ppt, M, radius)[:, 0]
    ext_err = float(np.linalg.norm(Bfem - Bexact) / np.linalg.norm(Bexact))

    return dict(order=order, h=h, tets=tm.n_elements, ndofs=sol.ndofs,
                l2=l2, max=mx, l2_core=l2c, max_core=mxc,
                mean_err=mean_err, ext_err=ext_err,
                t_mesh=t_mesh, t_asm=sol.assemble_time, t_solve=sol.solve_time,
                vol_err=abs(tm.region_volume("magnet")
                            / (4.0 / 3.0 * math.pi * radius ** 3) - 1.0),
                solver=sol.solver)


def sphere_convergence(hs: Sequence[float], orders=(1, 2), radius=0.01,
                       box_factor=6.0, verbose=True) -> List[dict]:
    rows = []
    for order in orders:
        recs = [sphere_case(h, order, radius, box_factor) for h in hs]
        for r, q in zip(recs, _rate([r["h"] for r in recs],
                                    [r["l2"] for r in recs])):
            r["rate"] = q
        for r, q in zip(recs, _rate([r["h"] for r in recs],
                                    [r["l2_core"] for r in recs])):
            r["rate_core"] = q
        for r, q in zip(recs, _rate([r["h"] for r in recs],
                                    [r["mean_err"] for r in recs])):
            r["rate_mean"] = q
        rows += recs
    if verbose:
        pretty = [dict(P=r["order"], h_mm=f"{r['h']*1e3:.2f}",
                       tets=r["tets"], ndofs=r["ndofs"],
                       L2_pct=f"{100*r['l2']:.3f}", rate=_fmt(r.get("rate"), 2),
                       core_pct=f"{100*r['l2_core']:.3f}",
                       core_rate=_fmt(r.get("rate_core"), 2),
                       mean_pct=f"{100*r['mean_err']:.3f}",
                       ext_pct=f"{100*r['ext_err']:.3f}",
                       geom_pct=f"{100*r['vol_err']:.2f}",
                       t_asm=f"{r['t_asm']:.2f}", t_sol=f"{r['t_solve']:.2f}")
                  for r in rows]
        print(_table(pretty, ["P", "h_mm", "tets", "ndofs", "L2_pct", "rate",
                              "core_pct", "core_rate", "mean_pct", "ext_pct",
                              "geom_pct", "t_asm", "t_sol"],
                     "(a) uniformly magnetised sphere — B error inside vs exact "
                     "(2/3)mu0*M"))
        print(f"    solver: {rows[0]['solver']}")
        print("    L2_pct  : over the WHOLE magnet — includes the faceted-"
              "surface layer, so it is geometry limited.")
        print("    core_pct: over centroids inside 0.75R — measures the "
              "FORMULATION, free of the faceting.")
        print("    geom_pct: magnet volume error from the faceted sphere; the "
              "floor L2_pct converges to.")
    return rows


# --------------------------------------------------------------------------
# (b) finite cylinder — the end-effect benchmark
# --------------------------------------------------------------------------

def cylinder_case(h: float, order: int, radius: float = 0.005,
                  length: float = 0.02, box_factor: float = 6.0,
                  M: float = M_MAG, n_probe: int = 41) -> dict:
    t0 = time.perf_counter()
    tm = cylinder_in_box(radius=radius, length=length, box_factor=box_factor,
                         h_magnet=h)
    t_mesh = time.perf_counter() - t0
    regs = [Region("air", tm.elements("air")),
            Region("magnet", tm.elements("magnet"), mu_r=1.0, M=(0.0, 0.0, M))]
    sol = solve_static3d(tm.mesh, regs, order=order)

    # probe from the mid-plane out to 1.5 magnet-lengths past the end face
    z = np.linspace(0.0, 1.5 * length, n_probe)
    pts = np.zeros((3, z.size))
    pts[2] = z
    Bz = sol.B_at_points(pts)[2]
    Bz_ex = exact.cylinder_axis_Bz(z, M, radius, length)
    scale = abs(exact.cylinder_axis_Bz_mid(M, radius, length))
    err = np.abs(Bz - Bz_ex) / scale

    mid = float(sol.B_at_points(np.zeros((3, 1)))[2, 0])
    # the end face is a material interface, but B_z is the NORMAL component
    # there and normal B is continuous, so probing it is well posed
    end = float(sol.B_at_points(np.array([[0.0], [0.0], [0.5 * length]]))[2, 0])
    ratio_fem = end / mid
    ratio_ex = exact.cylinder_end_to_mid_ratio(radius, length)

    return dict(order=order, h=h, tets=tm.n_elements, ndofs=sol.ndofs,
                l2=float(np.sqrt((err ** 2).mean())), max=float(err.max()),
                mid=mid, mid_ex=exact.cylinder_axis_Bz_mid(M, radius, length),
                end=end, end_ex=exact.cylinder_axis_Bz_end(M, radius, length),
                ratio_fem=ratio_fem, ratio_ex=ratio_ex,
                ratio_err=abs(ratio_fem / ratio_ex - 1.0),
                t_mesh=t_mesh, t_asm=sol.assemble_time, t_solve=sol.solve_time,
                z=z, Bz=Bz, Bz_ex=Bz_ex, solver=sol.solver)


def cylinder_convergence(hs: Sequence[float], orders=(1, 2), radius=0.005,
                         length=0.02, box_factor=4.0, verbose=True
                         ) -> List[dict]:
    rows = []
    for order in orders:
        recs = [cylinder_case(h, order, radius, length, box_factor)
                for h in hs]
        for r, q in zip(recs, _rate([r["h"] for r in recs],
                                    [r["l2"] for r in recs])):
            r["rate"] = q
        rows += recs
    if verbose:
        pretty = [dict(P=r["order"], h_mm=f"{r['h']*1e3:.2f}", tets=r["tets"],
                       ndofs=r["ndofs"],
                       axis_L2_pct=f"{100*r['l2']:.3f}",
                       axis_max_pct=f"{100*r['max']:.3f}",
                       rate=_fmt(r.get("rate"), 2),
                       Bz_mid=f"{r['mid']:.4f}", Bz_end=f"{r['end']:.4f}",
                       spill_fem=f"{r['ratio_fem']:.4f}",
                       spill_err_pct=f"{100*r['ratio_err']:.3f}",
                       t_sol=f"{r['t_solve']:.2f}")
                  for r in rows]
        print(_table(pretty, ["P", "h_mm", "tets", "ndofs", "axis_L2_pct",
                              "axis_max_pct", "rate", "Bz_mid", "Bz_end",
                              "spill_fem", "spill_err_pct", "t_sol"],
                     "(b) finite cylinder — on-axis B_z vs the exact solenoid "
                     "formula"))
        r0 = rows[0]
        print(f"    exact  B_z(mid) = {r0['mid_ex']:.4f} T   "
              f"B_z(end) = {r0['end_ex']:.4f} T   "
              f"spill ratio = {r0['ratio_ex']:.4f}")
        print("    'spill' = B_z(end face)/B_z(mid-plane): the fraction a 2D "
              "solve would miss at the end.")
    return rows


# --------------------------------------------------------------------------
# (c) air-box truncation
# --------------------------------------------------------------------------

def truncation_study(box_factors=(2.0, 3.0, 4.0, 6.0, 8.0), order: int = 2,
                     radius: float = 0.01, h: float = 0.002,
                     reference_factor: float = 14.0, M: float = M_MAG,
                     verbose: bool = True) -> List[dict]:
    """How far out must the phi=0 box sit?

    Two errors are reported and they are NOT the same thing:

      * ``vs_exact`` — error against (2/3) mu0 M.  It contains the mesh
        discretisation error, which does not shrink as the box grows, so it
        bottoms out on a plateau.
      * ``vs_far``  — difference from the same mesh resolution with the box at
        ``reference_factor``.  Because the size field pins the near-magnet
        elements, this isolates TRUNCATION alone.  This is the number to size
        the motor air box with.

    Both boundary conditions are swept: phi = 0 (equipotential, pulls flux into
    the wall) and dphi/dn = 0 (flux-tight, pushes it away).  The truth is
    bracketed between them, so their spread is an error bar that costs one extra
    solve and needs no reference solution at all.
    """
    exact_B = exact.sphere_B_inside(M)

    def _run(bf, neumann):
        tm = sphere_in_box(radius=radius, box_factor=bf, h_magnet=h)
        regs = [Region("air", tm.elements("air")),
                Region("magnet", tm.elements("magnet"), mu_r=1.0,
                       M=(0.0, 0.0, M))]
        s = solve_static3d(tm.mesh, regs, order=order, neumann_outer=neumann)
        return float(s.region_mean_B("magnet")[2]), tm.n_elements, s.ndofs, s

    ref, _, _, _ = _run(reference_factor, False)

    rows = []
    for bf in box_factors:
        bd, tets, ndofs, s = _run(bf, False)
        bn, _, _, _ = _run(bf, True)
        rows.append(dict(box_factor=bf, tets=tets, ndofs=ndofs,
                         B_dirichlet=bd, B_neumann=bn,
                         vs_exact=abs(bd - exact_B) / exact_B,
                         vs_far=abs(bd - ref) / abs(ref),
                         bracket=abs(bn - bd) / abs(bd),
                         t_solve=s.solve_time))
    if verbose:
        pretty = [dict(box_R=f"{r['box_factor']:.1f}", tets=r["tets"],
                       ndofs=r["ndofs"],
                       vs_exact_pct=f"{100*r['vs_exact']:.3f}",
                       vs_far_pct=f"{100*r['vs_far']:.4f}",
                       bracket_pct=f"{100*r['bracket']:.4f}",
                       t_sol=f"{r['t_solve']:.2f}")
                  for r in rows]
        print(_table(pretty, ["box_R", "tets", "ndofs", "vs_exact_pct",
                              "vs_far_pct", "bracket_pct", "t_sol"],
                     f"(c) air-box truncation, P{order}, box half-width = "
                     f"box_R * magnet radius"))
        ok = [r for r in rows if r["vs_far"] < 0.005]
        if ok:
            print(f"    <0.5% truncation error from box_R >= "
                  f"{min(r['box_factor'] for r in ok):.1f}")
    return rows


# --------------------------------------------------------------------------
# (d) nonlinear iron smoke test
# --------------------------------------------------------------------------

def _project_bh(name: Optional[str] = None):
    """Fetch a real B-H curve from the project's materials database.

    READ-ONLY import: this is the same curve object the 2D solver saturates its
    iron with, so a 3D/2D disagreement can never be blamed on two curves."""
    from motor_ai_sim.materials import all_steels
    steels = all_steels()
    if name is None:
        name = "20SW1200" if "20SW1200" in steels else sorted(steels)[0]
    st = steels[name]
    return name, [tuple(p) for p in st.bh_curve]


def nonlinear_ring(order: int = 1, h: float = 0.0018, M: float = M_MAG,
                   steel: Optional[str] = None, verbose: bool = True) -> dict:
    """Magnetised cylinder inside a saturable iron ring; Picard to convergence.

    There is no exact solution here — this is a viability check with three
    falsifiable assertions:

      1. the constitutive residual |dH|/|H| falls to the tolerance,
      2. the converged mu_r really is the one the curve prescribes for the B
         that came out of the solve (the fixed point, not just a stop),
      3. the net flux through the closed outer boundary is ~0 — div B = 0 is
         satisfied by the recovered B, not just by the potential.
    """
    name, bh = _project_bh(steel)
    tm = cylinder_with_iron_ring(h_magnet=h)
    regs = [Region("air", tm.elements("air")),
            Region("magnet", tm.elements("magnet"), mu_r=1.0, M=(0.0, 0.0, M)),
            Region("iron", tm.elements("iron"), mu_r=1000.0, bh_curve=bh)]
    t0 = time.perf_counter()
    sol = solve_static3d_nonlinear(tm.mesh, regs, order=order, tol=5e-3,
                                   max_iter=50, damping=0.4, verbose=verbose)
    wall = time.perf_counter() - t0

    from motor_ai_sim.simulation.field_ops import _mu_r_from_bh_vec
    iron = tm.elements("iron")
    B = sol.B_elementwise()
    Bmag_iron = np.linalg.norm(B[:, iron], axis=0)
    mur = sol.mu_el[iron] / MU0
    mur_curve = np.maximum(_mu_r_from_bh_vec(bh, Bmag_iron), 1.0)
    fixed_point_err = float(np.max(np.abs(mur / mur_curve - 1.0)))

    # flux scale: the magnet's own cross-section flux, for a relative check
    mag = tm.elements("magnet")
    volm = sol.basis.dx[mag].sum()
    phi_scale = abs(float(np.linalg.norm(sol.region_mean_B("magnet")))
                    * math.pi * tm.meta["radius"] ** 2)
    flux = sol.boundary_flux()

    out = dict(steel=name, order=order, tets=tm.n_elements, ndofs=sol.ndofs,
               iron_tets=int(iron.size),
               iterations=sol.picard["iterations"],
               converged=bool(sol.picard["converged"]),
               history=sol.picard["history"],
               mur_min=float(mur.min()), mur_max=float(mur.max()),
               fixed_point_err=fixed_point_err,
               damping_final=sol.picard["damping_final"],
               B_iron_max=float(Bmag_iron.max()),
               B_iron_mean=float(Bmag_iron.mean()),
               boundary_flux=float(flux), flux_scale=float(phi_scale),
               flux_rel=abs(float(flux)) / max(phi_scale, 1e-30),
               wall=wall, magnet_volume=float(volm))
    if verbose:
        print("(d) nonlinear iron ring — Picard smoke test")
        print("-------------------------------------------")
        print(f"    steel            : {name} ({len(bh)} B-H points)")
        print(f"    mesh             : {out['tets']} tets "
              f"({out['iron_tets']} iron), P{order}, {out['ndofs']} dofs")
        print(f"    picard           : {out['iterations']} iterations, "
              f"converged={out['converged']}, {wall:.1f}s, "
              f"final damping {out['damping_final']:.3f}")
        print("    |dH|/|H| history : "
              + " ".join(f"{x:.2e}" for x in out["history"]))
        print(f"    iron mu_r        : {out['mur_min']:.1f} .. "
              f"{out['mur_max']:.1f}  (initial guess was 1000)")
        print(f"    fixed-point check: worst |mu_used/mu_curve(|B|) - 1| = "
              f"{out['fixed_point_err']:.2e}")
        print(f"    iron |B|         : mean {out['B_iron_mean']:.3f} T, "
              f"max {out['B_iron_max']:.3f} T")
        print(f"    closed-surface flux: {out['boundary_flux']:.3e} Wb "
              f"({100*out['flux_rel']:.4f}% of the magnet's own flux)")
    return out


# --------------------------------------------------------------------------
# (e) Stage-B lookahead: Nedelec edge elements
# --------------------------------------------------------------------------

def nedelec_probe(verbose: bool = True) -> dict:
    """Can the INSTALLED scikit-fem do a curl-curl (vector potential / edge
    element) formulation on tets?  Report only — Stage B decides."""
    import skfem
    out: Dict[str, object] = dict(skfem_version=skfem.__version__)
    try:
        from skfem import Basis, BilinearForm, LinearForm, MeshTet, asm, condense
        from skfem.helpers import curl, dot
        from skfem.element import ElementTetN0

        m = MeshTet().refined(2)
        e = ElementTetN0()
        basis = Basis(m, e)
        out["element"] = "ElementTetN0"
        out["ndofs"] = int(basis.N)
        out["nedges"] = int(m.edges.shape[1])
        out["dofs_are_edges"] = int(basis.N) == int(m.edges.shape[1])

        @BilinearForm
        def curlcurl(u, v, w):
            return dot(curl(u), curl(v)) + 1e-6 * dot(u, v)

        @LinearForm
        def src(v, w):
            return v[2]

        t0 = time.perf_counter()
        K = asm(curlcurl, basis)
        f = asm(src, basis)
        out["assemble_time"] = time.perf_counter() - t0
        out["nnz"] = int(K.nnz)
        out["symmetric"] = bool(abs(K - K.T).max() < 1e-10 * abs(K).max())

        D = basis.get_dofs().all()
        out["boundary_dofs"] = int(D.size)
        from scipy.sparse.linalg import spsolve
        A, b, x, I = condense(K, f, D=D)
        x[I] = spsolve(A.tocsc(), b)
        out["solved"] = bool(np.isfinite(x).all())
        out["curl_of_solution_ok"] = bool(
            np.isfinite(basis.interpolate(x).curl).all())
        out["higher_order_edge"] = hasattr(
            __import__("skfem.element", fromlist=["x"]), "ElementTetN1")
        out["status"] = "usable"
    except Exception as exc:                          # pragma: no cover
        out["status"] = f"FAILED: {type(exc).__name__}: {exc}"
    if verbose:
        print("(e) Stage-B lookahead — Nedelec edge elements")
        print("---------------------------------------------")
        for k, v in out.items():
            print(f"    {k:22s}: {v}")
    return out


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--sphere", action="store_true")
    ap.add_argument("--cylinder", action="store_true")
    ap.add_argument("--truncation", action="store_true")
    ap.add_argument("--nonlinear", action="store_true")
    ap.add_argument("--nedelec", action="store_true")
    ap.add_argument("--fine", action="store_true",
                    help="publication-grade sizes (minutes, not seconds)")
    ap.add_argument("--threads", type=int, default=1,
                    help="MKL threads; 1 keeps this out of a running "
                         "optimisation's way")
    args = ap.parse_args(argv)

    if args.threads:
        os.environ.setdefault("MKL_NUM_THREADS", str(args.threads))
        os.environ.setdefault("OMP_NUM_THREADS", str(args.threads))

    want = dict(sphere=args.sphere, cylinder=args.cylinder,
                truncation=args.truncation, nonlinear=args.nonlinear,
                nedelec=args.nedelec)
    if args.all or not any(want.values()):
        want = {k: True for k in want}

    if want["sphere"]:
        hs = (0.004, 0.0028, 0.002, 0.0014, 0.001) if args.fine \
            else (0.004, 0.0028, 0.002)
        sphere_convergence(hs)
        print()
    if want["cylinder"]:
        # NOTE the box factor: at 6 the far field alone is 3x the volume, and a
        # P2 solve at h = 1 mm runs into the hundreds of thousands of dofs for
        # no accuracy — 4 is already past the truncation plateau of study (c).
        hs = (0.0025, 0.0018, 0.0013, 0.001) if args.fine \
            else (0.0035, 0.0025, 0.0018)
        cylinder_convergence(hs, box_factor=4.0)
        print()
    if want["truncation"]:
        bfs = (2.0, 3.0, 4.0, 5.0, 6.0, 8.0) if args.fine \
            else (2.0, 3.0, 4.0, 6.0)
        truncation_study(bfs, order=2, h=0.002 if args.fine else 0.003,
                         reference_factor=14.0 if args.fine else 10.0)
        print()
    if want["nonlinear"]:
        nonlinear_ring(order=1, h=0.0012 if args.fine else 0.0018)
        print()
    if want["nedelec"]:
        nedelec_probe()
    return 0


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())
