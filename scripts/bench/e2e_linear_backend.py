"""End-to-end cost of the linear-solver backend on one solver-direct run.

Runs one gap-study case (scripts/gap_layers_study: the saved rated duties of
Ø40 L12, L13 and L155 motor, imposed current, Coulomb torque, gap layers per
side as given) through ``em_transient_eval`` with the production code, and
records what the backend changes:

* wall time; time inside the linear solver (every ``LinearSolver.solve`` /
  ``factor`` / ``solve_factored`` call, so PARDISO and CHOLMOD/MUMPS are timed
  at the same boundary) and its share of the wall;
* frames solved, linear-solve counts, the solver's own ``describe()``;
* peak RSS of the process (``ru_maxrss``);
* the engineering answer: Coulomb mean torque, ripple, total loss, and the
  per-frame torque series (for the relative differences between backends).

The backend comes from the environment (``SB_LINEAR_BACKEND``), exactly as in
production.  usage (inside a throwaway container, never the live API):

  MOTOR_AI_SIM_CONFIG=<copy> PYTHONPATH=<src> SB_LINEAR_BACKEND=open \\
  python e2e_linear_backend.py --inp <gap-study inputs> --machine l155 \\
      --gl 1 --steps 72 --eddy --demag --out /work/out/l155_open

Licence: same as the repository.
"""
from __future__ import annotations

import argparse
import json
import os
import resource
import sys
import time


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inp", required=True)
    ap.add_argument("--machine", required=True)
    ap.add_argument("--duty", default="rated")
    ap.add_argument("--steps", type=int, default=36)
    ap.add_argument("--periods", type=float, default=1.0)
    ap.add_argument("--eddy", action="store_true")
    ap.add_argument("--demag", action="store_true")
    ap.add_argument("--gl", type=float, default=None)
    ap.add_argument("--study", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "gap_layers_study"))
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    sys.path.insert(0, os.path.abspath(args.study))
    from coul_common import setup  # noqa: E402

    import logging
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    kw, info = setup(args.inp, args.machine, args.duty)
    if args.gl is not None:
        kw["gap_layers"] = float(args.gl)
    kw.update(n_steps_per_period=int(args.steps), n_periods=float(args.periods),
              rotor_eddy=bool(args.eddy), eddy=bool(args.eddy),
              demag=bool(args.demag))

    import numpy as np
    from motor_ai_sim.simulation import linear_backend as LB
    from motor_ai_sim.simulation import fem_solver_2d as fs

    import threading
    acc = {"seconds": 0.0, "calls": 0}
    solvers = []
    tl = threading.local()
    lock = threading.Lock()

    def timed(fn):
        def w(self, *a, **k):
            d = getattr(tl, "depth", 0)
            tl.depth = d + 1
            t0 = time.perf_counter()
            try:
                return fn(self, *a, **k)
            finally:
                tl.depth = d
                if d == 0:
                    with lock:            # summed over threads (TDM pools)
                        acc["seconds"] += time.perf_counter() - t0
                        acc["calls"] += 1
        return w
    for name in ("solve", "factor", "solve_factored"):
        setattr(LB.LinearSolver, name, timed(getattr(LB.LinearSolver, name)))
    init = LB.LinearSolver.__init__

    def init_w(self, *a, **k):
        init(self, *a, **k)
        solvers.append(self)
    LB.LinearSolver.__init__ = init_w

    t0 = time.perf_counter()
    r = fs.em_transient_eval(**kw)
    wall = time.perf_counter() - t0
    peak_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    main_ls = max(solvers, key=lambda s: s.solves) if solvers else None

    def f(k):
        v = r.get(k)
        try:
            return None if v is None else float(v)
        except (TypeError, ValueError):
            return None
    out = dict(
        machine=args.machine, args=vars(args), backend_env=os.environ.get(
            "SB_LINEAR_BACKEND", "auto"),
        backend=(main_ls.backend if main_ls else None),
        wall_s=wall, linear_s=acc["seconds"], linear_calls=acc["calls"],
        linear_share=acc["seconds"] / wall if wall else None,
        peak_rss_MB=peak_kb / 1024.0,
        n_solvers=len(solvers),
        solver_counts=[dict(backend=s.backend, solves=s.solves,
                            factorizations=s.factorizations,
                            spd_analyses=s.spd_analyses, lu_analyses=s.lu_analyses,
                            seconds=s.factor_seconds(), notes=s.notes)
                       for s in solvers if s.solves],
        linear_solver=r.get("linear_solver"),
        n_frames_solved=r.get("n_frames_solved"),
        eddy_warmup_frames=r.get("eddy_warmup_frames"),
        eddy_settled=r.get("eddy_settled"),
        torque_method=r.get("torque_method"),
        T_avg_Nm=f("T_avg_Nm"), T_avg_coulomb_Nm=f("T_avg_coulomb_Nm"),
        T_ripple_pct=f("T_ripple_pct"), T_ripple_pp_Nm=f("T_ripple_pp_Nm"),
        P_loss_total_avg_W=f("P_loss_total_avg_W"),
        P_fe_avg_W=f("P_fe_avg_W"), P_mag_solve_W=f("P_mag_solve_W"),
        P_cu_ac_solve_W=f("P_cu_ac_solve_W"), V_peak=f("V_peak"),
        T_em_Nm=[float(x) for x in (r.get("T_em_Nm") or [])],
        T_coulomb_series=[float(x) for x in (r.get("T_coulomb_series") or [])],
        P_loss_total_W=[float(x) for x in np.atleast_1d(r.get("P_loss_total_W") or [])],
        threads={k: os.environ.get(k) for k in ("OMP_NUM_THREADS", "MKL_NUM_THREADS",
                                                "OPENBLAS_NUM_THREADS")},
    )
    with open(args.out + ".json", "w") as fo:
        json.dump(out, fo, indent=1, default=str)
    print(json.dumps({k: out[k] for k in ("machine", "backend", "wall_s", "linear_s",
                                          "linear_share", "peak_rss_MB",
                                          "n_frames_solved", "T_avg_Nm",
                                          "T_ripple_pct", "P_loss_total_avg_W")},
                     default=str))


if __name__ == "__main__":
    main()
