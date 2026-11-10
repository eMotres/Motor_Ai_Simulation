"""Where one transient frame's time goes: assembly / reduction / PARDISO
symbolic / numeric / solve / the rest (sandbox only, read-only).

Wraps the solver's linear-algebra entry points with wall-clock timers
(inclusive and exclusive, a call stack, ~2 us per call) and runs one point
through the real route in-process, like ``fem_speed_identity.py``.  PARDISO's
phase 23 is issued as phase 22 + phase 33 so numeric factorisation and the
back-solve are timed apart (PARDISO's 23 is exactly that pair).

  python frame_cost_profile.py point.json out.json [ENV=VAL ...]

``point.json`` as in fem_speed_identity.py.  The output carries that script's
``scalars`` (so ``fem_speed_identity.py compare`` works on it) plus
``timers``: {name: [calls, inclusive_s, exclusive_s]}.
"""
from __future__ import annotations

import functools
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fem_speed_identity as F                              # noqa: E402

_T: dict = {}
_stack: list = []


def _timed(name, fn):
    @functools.wraps(fn)
    def w(*a, **k):
        t0 = time.perf_counter()
        _stack.append(0.0)
        try:
            return fn(*a, **k)
        finally:
            dt = time.perf_counter() - t0
            child = _stack.pop()
            if _stack:
                _stack[-1] += dt
            e = _T.setdefault(name, [0, 0.0, 0.0])
            e[0] += 1
            e[1] += dt
            e[2] += dt - child
    return w


def _patch(obj, attr, name):
    setattr(obj, attr, _timed(name, getattr(obj, attr)))


def install():
    import pypardiso
    from motor_ai_sim.simulation import (
        csr_scatter, fem_solver_2d, p2_drive, p2_nonlinear, p2_projection)
    N = p2_nonlinear.P2Nonlinear
    for a in ("Kpw", "tangent2", "asmK", "reduce", "_solve_spd",
              "_solve_reuse", "elemB", "nu_of", "pic2_sweeps"):
        _patch(N, a, "p2." + a)
    _patch(p2_nonlinear._Skeleton, "stiff", "asm.skel_stiff")
    _patch(p2_nonlinear._Skeleton, "tang", "asm.skel_tang")
    _patch(p2_nonlinear._Skeleton, "__init__", "asm.skel_init")
    for a, nm in (("stiff_data", "asm.stiff_data"), ("tang_data", "asm.tang_data"),
                  ("matvec", "asm.el_matvec")):
        if hasattr(p2_nonlinear._Skeleton, a):
            _patch(p2_nonlinear._Skeleton, a, nm)
    for a in ("kpw_el", "jac_el", "apply_K", "solve_plan"):
        if hasattr(N, a):
            _patch(N, a, "p2." + a)
    try:
        from motor_ai_sim.simulation import p2_frame_plan as fp
        _patch(fp.ReducedPlan, "__init__", "plan.build_run")
        _patch(fp.FrameSystem, "__init__", "plan.build_frame")
        _patch(fp.FrameSystem, "data", "plan.data")
        _patch(p2_drive, "_reduced_map", "plan.reduced_map")
        _patch(p2_drive.P2Drive, "_plan_system", "plan.system")
    except ImportError:
        pass
    _patch(csr_scatter.CsrScatter, "assemble", "asm.csr_scatter")
    _patch(p2_nonlinear, "sym_pattern", "spd.sym_pattern")
    _patch(p2_nonlinear, "_perm_analyse", "pardiso.phase11")
    _patch(p2_nonlinear, "_grad_at_quad", "asm.grad_at_quad")
    _patch(p2_nonlinear, "_mu_r_from_bh_vec", "asm.bh_curve")
    _patch(p2_drive.P2Drive, "eddy_solve", "eddy.solve")
    _patch(p2_drive.P2Drive, "ve_newton", "eddy.ve_newton")
    _patch(p2_drive.P2Drive, "v_newton", "drive.v_newton")
    _patch(p2_drive, "_bmat", "eddy.bmat")
    _patch(p2_projection.SlipProjection, "build", "proj.build")
    _patch(fem_solver_2d, "fem_transient_sliding_band", "fem.transient")

    S = pypardiso.PyPardisoSolver
    orig = S._call_pardiso

    def call(self, A, b):
        ph = int(self.phase)
        if ph == 23:
            t0 = time.perf_counter()
            self.set_phase(22)
            orig(self, A, b)
            t1 = time.perf_counter()
            self.set_phase(33)
            x = orig(self, A, b)
            t2 = time.perf_counter()
            self.set_phase(23)
            for nm, dt in (("pardiso.phase22", t1 - t0), ("pardiso.phase33", t2 - t1)):
                e = _T.setdefault(nm, [0, 0.0, 0.0])
                e[0] += 1; e[1] += dt; e[2] += dt
            if _stack:
                _stack[-1] += t2 - t0
            return x
        t0 = time.perf_counter()
        x = orig(self, A, b)
        dt = time.perf_counter() - t0
        e = _T.setdefault("pardiso.phase%d" % ph + "_raw", [0, 0.0, 0.0])
        e[0] += 1; e[1] += dt; e[2] += dt
        if _stack:
            _stack[-1] += dt
        return x
    S._call_pardiso = call


def main():
    point_path, out_path = sys.argv[1], sys.argv[2]
    env = dict(kv.split("=", 1) for kv in sys.argv[3:])
    for k, v in env.items():
        os.environ[k] = v
    install()
    point = json.load(open(point_path))
    params = (F._params_from_ledger(point["ledger"]) if "ledger" in point
              else dict(point["params"]))
    from motor_ai_sim import workspace as W
    from motor_ai_sim.routes import simulation as S
    ws = W.workspace_for_identity(point.get("identity", "vadim@motresres.com"))
    with W.use_workspace(ws):
        t0 = time.time()
        r = S.get_fem_transient(**params, fresh=True, ledger=False)
        wall = time.time() - t0
    if hasattr(r, "body"):
        r = json.loads(r.body)
    res = {"wall_s": wall, "env": env, "scalars": F._scalars(r),
           "linear_solver": r.get("linear_solver"),
           "solve_wall_s": r.get("solve_wall_s"),
           "n_frames_solved": r.get("n_frames_solved"),
           "eddy_warmup_frames": r.get("eddy_warmup_frames"),
           "demag_prepass_frames": r.get("demag_prepass_frames"),
           "timers": {k: [v[0], round(v[1], 3), round(v[2], 3)]
                      for k, v in sorted(_T.items(), key=lambda kv: -kv[1][2])}}
    json.dump(res, open(out_path, "w"), indent=1, default=str)
    print("DONE wall %.1f s, solve %s s, frames %s, linear %s" % (
        wall, res["solve_wall_s"], res["n_frames_solved"], res["linear_solver"]))
    print("%-24s %8s %10s %10s" % ("timer", "calls", "incl_s", "excl_s"))
    for k, (n, inc, exc) in res["timers"].items():
        print("%-24s %8d %10.1f %10.1f" % (k, n, inc, exc))


if __name__ == "__main__":
    main()
