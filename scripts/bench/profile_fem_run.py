"""Stage-1 profiler for the 2-D P2 FEM solver (solver-direct, no API).

Runs one saved duty through ``em_transient_eval`` with the production code
UNCHANGED.  Timing hooks are installed by wrapping functions at run time; the
solver never sees a different matrix or a different code path.

What it records
  * wall time per instrumented primitive (inclusive and exclusive): mesh build,
    d-axis calibration, every linear solve (``P2Nonlinear.solve_ff``) split by
    the solver path that asked for it, nonlinear re-assembly (Kpw, tangent2,
    asmK, elemB), bordered-eddy Newton bodies, voltage Newtons;
  * one row per linear solve: time stamp, caller, n, nnz, rhs columns, whether
    PARDISO re-ran its analysis (pattern change), duration;
  * the PARDISO settings in use (mtype, iparm) and factor statistics
    (iparm[14..17]: peak memory, nnz(L+U), Mflop) on captured solves;
  * optionally a cProfile of the whole run (``--cprofile``), aggregated per
    module afterwards by ``summarize_profile.py``;
  * optionally real matrices (``--dump DIR``): for each caller class the first
    and one later system, with the right-hand side and the CPU FP64 solution,
    as scipy .npz and MatrixMarket.

Usage (inside a sandbox; never the live API or config/motor_config.yaml):
  MOTOR_AI_SIM_CONFIG=<copy>/motor_config.yaml PYTHONPATH=<src> \
  python profile_fem_run.py --dies <dies dir> --case l155_rated --mode eddy \
      --out res.json [--cprofile prof.pstats] [--dump DIR --dump-stop 2] \
      [--mesh-scale 1.0] [--steps N] [--nper P]

Modes: ``static`` = magnetostatic sweep (eddy off, rotor eddy off, demag as the
duty says); ``eddy`` = transient eddy (BDF2, warm-up + periodic accelerator,
exactly as the duty runs); ``dump`` = like eddy but stops after the requested
matrices are captured (used for the mesh-refinement exports).

Licence: same as the repository.  Needs only numpy/scipy/pyyaml + the repo.
"""
from __future__ import annotations

import argparse
import cProfile
import json
import math
import os
import sys
import time
from collections import defaultdict

import yaml

CASES = {
    "l155_rated": dict(die="CIANO10 200 opt", cfg="L155 motor", duty="rated 1x9 mm"),
    "l13_rated": dict(die="CIANO28 85 20SW1200", cfg="L13", duty="rated"),
    "d40_rated": dict(die="CIANO14 40 new", cfg="L12", duty="rated"),
}

# Callers of P2Nonlinear.solve_ff, innermost first.  The first match on the
# stack names the solve.
_CALLERS = ("eddy_solve", "ve_newton", "v_newton", "v_picard",
            "eddy_static_state", "pic2_sweeps", "frozen_permeability_ldq",
            "fem_transient_sliding_band")
_CONTEXT = ("_calibrate_daxis", "noload_psi_pm", "noload_incremental_ldq",
            "rotor_eddy_solver_bc")


class _Stop(BaseException):
    """BaseException so the solver's own `except Exception` cannot swallow it."""


# --------------------------------------------------------------------- timers
class Timers:
    def __init__(self):
        self.incl = defaultdict(float)
        self.excl = defaultdict(float)
        self.calls = defaultdict(int)
        self.stack = []          # [name, t0, child_time]

    def wrap(self, owner, attr, name=None):
        fn = getattr(owner, attr)
        name = name or f"{getattr(owner, '__name__', owner)}.{attr}"
        T = self

        def wrapped(*a, **k):
            T.stack.append([name, time.perf_counter(), 0.0])
            try:
                return fn(*a, **k)
            finally:
                n, t0, ch = T.stack.pop()
                dt = time.perf_counter() - t0
                T.incl[n] += dt
                T.excl[n] += dt - ch
                T.calls[n] += 1
                if T.stack:
                    T.stack[-1][2] += dt
        wrapped.__wrapped__ = fn
        setattr(owner, attr, wrapped)
        return fn


def _classify():
    f = sys._getframe(2)
    caller, ctx = None, "main"
    while f is not None:
        nm = f.f_code.co_name
        if caller is None and nm in _CALLERS:
            caller = nm
        if nm in _CONTEXT:
            ctx = nm
            break
        f = f.f_back
    return caller or "other", ctx


def _sym_rel(A):
    import scipy.sparse.linalg as sla
    D = (A - A.T).tocsr()
    na = sla.norm(A)
    return float(sla.norm(D) / na) if na > 0 else 0.0


# ---------------------------------------------------------------- compose run
def compose(dies, case):
    from motor_ai_sim.routes.family import _ABSENT_MEANS, _ABSENT_MATERIALS
    c = CASES[case]
    d = yaml.safe_load(open(os.path.join(dies, c["die"], "die.yaml"), encoding="utf-8"))
    y = yaml.safe_load(open(os.path.join(dies, c["die"], c["cfg"] + ".yaml"), encoding="utf-8"))
    geo = dict(d.get("geometry") or {})
    geo.update({k: v for k, v in (y.get("geometry_overrides") or {}).items() if v is not None})
    for k, v in _ABSENT_MEANS.items():
        if geo.get(k) is None:
            geo[k] = v
    ns = float(geo["num_seg"])
    pps, sps = float(geo["num_poles_per_segment"]), float(geo["num_slots_per_segment"])
    geo["num_poles"] = int(round(ns * pps)); geo["angle_pole"] = 360.0 / (ns * pps)
    geo["num_slots"] = int(round(ns * sps)); geo["angle_slot"] = 360.0 / (ns * sps)
    duty = next(x for x in y["duties"] if x["name"] == c["duty"])
    mats = {k: v for k, v in (y.get("materials") or {}).items() if v}
    for k, v in (duty.get("materials") or {}).items():
        if v and not mats.get(k):
            mats[k] = v
    for k, v in _ABSENT_MATERIALS.items():
        if not mats.get(k):
            mats[k] = v
    runs = duty.get("runs") or {}
    st = {}
    st.update(((runs.get("current") or {}).get("settings")) or duty.get("mesh") or {})
    return c, d, y, geo, duty, mats, st


def build_kwargs(args, c, d, y, geo, duty, st, sd):
    wnd = y.get("winding") or {}
    I_term = float(duty["current_arms"])
    I_wind = I_term / (math.sqrt(3.0) if sd == "delta" else 1.0)
    s = float(args.mesh_scale)
    cm = {}
    for k, v in dict(st.get("mesh.componentMesh") or {}).items():
        try:
            cm[k] = float(v) if "rel" in k else float(v) * s
        except (TypeError, ValueError):
            cm[k] = v
    mt = st.get("sim.magnetTempC")
    demag = bool(st.get("sim.demag", False))
    eddy = args.mode != "static"
    return dict(
        n_steps_per_period=int(args.steps or st.get("sim.stepsPP", 36)),
        n_periods=float(args.nper),
        gamma_deg=float(duty["gamma_deg"]), I_phase_rms=I_wind, rpm=float(duty["rpm"]),
        n_parallel=int(wnd.get("n_parallel") or 1), connection=wnd.get("connection"),
        star_delta=sd,
        daxis_deg=(float(d["daxis_deg"]) if d.get("daxis_deg") is not None else None),
        mesh_size_mm=float(st.get("mesh.meshSize", 4)) * s,
        min_size_mm=float(st.get("mesh.minSize", 0.3)) * s,
        outer_air_factor=float(st.get("mesh.outerAir", 1.2)),
        gap_layers=float(st.get("mesh.gapLayers", 1)),
        n_sectors=int(st.get("mesh.nSectors", 2)),
        coil_temp_c=float(st.get("sim.coilTemp", 120)),
        magnet_temp_c=(float(mt) if mt not in (None, "") else None),
        end_winding_factor=float(st.get("sim.endWinding", 0) or 0),
        rotor_eddy=eddy, demag=demag,
        pole_copy=bool(st.get("mesh.poleCopy", False)),
        iron_template=bool(st.get("mesh.ironTemplate", True)),
        geo_mesh=bool(st.get("mesh.geoMesh", True)),
        structured_gap=bool(st.get("mesh.structuredGap", True)),
        component_mesh_mm=cm, geo_override=dict(geo),
        drive="current", inc_ldq=True, eddy=eddy)


# ------------------------------------------------------------------- main run
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dies", required=True)
    ap.add_argument("--case", required=True, choices=sorted(CASES))
    ap.add_argument("--mode", required=True, choices=("static", "eddy", "dump"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--cprofile")
    ap.add_argument("--dump")
    ap.add_argument("--dump-stop", type=int, default=0,
                    help="dump mode: stop after this many eddy captures")
    ap.add_argument("--dump-stop-caller", default="eddy_solve",
                    help="caller whose captures count for --dump-stop "
                         "(fem_transient_sliding_band = magnetostatic Newton)")
    ap.add_argument("--dump-later", type=int, default=25,
                    help="capture the n-th solve of each caller as the 'later' system")
    ap.add_argument("--mesh-scale", type=float, default=1.0)
    ap.add_argument("--steps", type=int, default=0)
    ap.add_argument("--nper", type=float, default=1.0)
    ap.add_argument("--backend", default="pardiso",
                    help="bench only: route every P2 linear solve through a "
                         "gpu_backends.py backend (e.g. cudss_fp64, cudss_mixed)")
    args = ap.parse_args()

    import logging
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")

    c, d, y, geo, duty, mats, st = compose(args.dies, args.case)
    cfg_path = os.environ["MOTOR_AI_SIM_CONFIG"]
    base = yaml.safe_load(open(cfg_path, encoding="utf-8"))
    base["geometry"].update(geo)
    base["materials"].update(mats)
    base["winding"] = dict(y.get("winding") or {})
    base["parts"] = dict(y.get("parts") or base.get("parts") or {})
    wnd = y.get("winding") or {}
    sd = str(wnd.get("star_delta") or duty.get("star_delta") or st.get("sim.starDelta") or "star")
    base["simulation"].update({"rpm": float(duty["rpm"]), "gamma_deg": float(duty["gamma_deg"]),
                               "star_delta": sd})
    with open(cfg_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(base, f, allow_unicode=True, sort_keys=False)
    from motor_ai_sim.config import clear_config_cache
    clear_config_cache()

    import numpy as np
    import scipy.sparse as sp
    import motor_ai_sim
    from motor_ai_sim.simulation import fem_solver_2d as fs
    from motor_ai_sim.simulation import p2_nonlinear as p2n
    from motor_ai_sim.simulation import p2_drive as p2d
    from motor_ai_sim.simulation import mesher as msh

    T = Timers()
    # mesh: fem_solver_2d re-exports the mesher function by name
    orig_mesh = msh._build_sliding_band_meshes
    T.wrap(fs, "_build_sliding_band_meshes", "mesh.build_sliding_band_meshes")
    for owner, attr in ((fs, "_calibrate_daxis"), (fs, "_resolve_daxis_shift"),
                        (fs, "noload_psi_pm"), (fs, "noload_incremental_ldq"),
                        (fs, "frozen_permeability_ldq"), (fs, "build_materials")):
        if hasattr(owner, attr):
            T.wrap(owner, attr, f"fs.{attr}")
    P2 = p2n.P2Nonlinear
    for attr in ("Kpw", "tangent2", "asmK", "elemB", "pic2_sweeps"):
        T.wrap(P2, attr, f"P2Nonlinear.{attr}")
    D = p2d.P2Drive
    for attr in ("eddy_solve", "ve_newton", "v_newton", "v_picard",
                 "eddy_static_state", "eddy_ops"):
        if hasattr(D, attr):
            T.wrap(D, attr, f"P2Drive.{attr}")
    try:
        from motor_ai_sim.simulation import eddy_solver_2d as es
        if hasattr(es, "rotor_eddy_solver_bc"):
            T.wrap(es, "rotor_eddy_solver_bc", "eddy_solver_2d.rotor_eddy_solver_bc")
        if hasattr(fs, "rotor_eddy_solver_bc"):
            T.wrap(fs, "rotor_eddy_solver_bc", "eddy_solver_2d.rotor_eddy_solver_bc")
    except Exception:
        pass

    # linear solves -------------------------------------------------------
    solves = []
    per_caller = defaultdict(int)
    captures = []
    stop_after = int(args.dump_stop) if args.dump else 0
    t_start = [None]
    pardiso_info = {}
    orig_solve = P2.solve_ff
    if args.backend != "pardiso":
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from gpu_backends import make_backend
        _bes = {}

        def orig_solve(self, Mff, rhs):          # noqa: F811  (bench override)
            be = _bes.get(id(self))
            if be is None:
                be = _bes[id(self)] = make_backend(args.backend)
            return be.solve_reuse(Mff, rhs)
    dump_dir = args.dump
    if dump_dir:
        os.makedirs(dump_dir, exist_ok=True)

    def solve_ff(self, Mff, rhs):
        caller, ctx = _classify()
        key = f"{ctx}:{caller}"
        per_caller[key] += 1
        k = per_caller[key]
        ana0 = self.pardiso_analyses
        t0 = time.perf_counter()
        x = orig_solve(self, Mff, rhs)
        dt = time.perf_counter() - t0
        nrhs = 1 if np.ndim(rhs) == 1 else int(np.shape(rhs)[1])
        row = dict(t=round(t0 - t_start[0], 4), who=key, n=int(Mff.shape[0]),
                   nnz=int(Mff.nnz), nrhs=nrhs, ana=int(self.pardiso_analyses - ana0),
                   dt=round(dt, 5), backend=(args.backend if args.backend != "pardiso" else
                            ("pardiso" if self._pardiso is not None else "superlu")))
        s = self._pardiso
        if args.backend != "pardiso":
            s = None
        if s is not None and not pardiso_info:
            pardiso_info.update(mtype=int(getattr(s, "mtype", -1)),
                                iparm=[int(v) for v in s.iparm[:64]],
                                size_limit_storage=getattr(s, "size_limit_storage", None))
        if s is not None:
            row["peak_kb"] = int(s.iparm[14]); row["perm_kb"] = int(s.iparm[15])
            row["fac_kb"] = int(s.iparm[16]); row["nnz_lu"] = int(s.iparm[17])
            row["mflop"] = int(s.iparm[18])
        solves.append(row)
        if dump_dir and (k == 1 or k == int(args.dump_later)) and ctx == "main":
            tag = f"{args.case}_s{args.mesh_scale:g}_{caller}_{'first' if k == 1 else 'later'}"
            A = sp.csr_matrix(Mff)
            b = np.asarray(rhs, dtype=float)
            xx = np.asarray(x, dtype=float)
            sp.save_npz(os.path.join(dump_dir, tag + "_A.npz"), A, compressed=True)
            np.savez_compressed(os.path.join(dump_dir, tag + "_bx.npz"), b=b, x=xx)
            try:
                import scipy.io as sio
                sio.mmwrite(os.path.join(dump_dir, tag + "_A.mtx"), A, precision=17)
                sio.mmwrite(os.path.join(dump_dir, tag + "_b.mtx"),
                            b.reshape(b.shape[0], -1), precision=17)
            except Exception as e:  # noqa
                print("mmwrite failed", e, file=sys.stderr)
            r = b - A @ xx
            meta = dict(tag=tag, case=args.case, mesh_scale=args.mesh_scale,
                        caller=caller, call_index=k, n=int(A.shape[0]), nnz=int(A.nnz),
                        nrhs=nrhs, sym_rel=_sym_rel(A),
                        diag_min=float(A.diagonal().min()), diag_max=float(A.diagonal().max()),
                        n_diag_le0=int((A.diagonal() <= 0).sum()),
                        cpu_rel_residual=float(np.linalg.norm(r) / max(np.linalg.norm(b), 1e-300)),
                        pardiso=row)
            with open(os.path.join(dump_dir, tag + "_meta.json"), "w") as f:
                json.dump(meta, f, indent=1)
            captures.append(meta)
            if stop_after and sum(1 for m in captures if m["caller"] == args.dump_stop_caller) >= stop_after:
                raise _Stop()
        return x

    P2.solve_ff = solve_ff

    kw = build_kwargs(args, c, d, y, geo, duty, st, sd)
    prof = cProfile.Profile() if args.cprofile else None
    t_start[0] = time.perf_counter()
    stopped = False
    r = {}
    try:
        if prof:
            prof.enable()
        r = fs.em_transient_eval(**kw)
    except _Stop:
        stopped = True
    finally:
        if prof:
            prof.disable()
            prof.dump_stats(args.cprofile)
    wall = time.perf_counter() - t_start[0]

    # --------------------------------------------------------------- report
    def f(k):
        v = r.get(k) if isinstance(r, dict) else None
        try:
            return None if v is None else float(v)
        except Exception:
            return None

    by = defaultdict(lambda: dict(n_calls=0, t=0.0, analyses=0, nmax=0, nnzmax=0, nrhs=0))
    for s_ in solves:
        b = by[s_["who"]]
        b["n_calls"] += 1; b["t"] += s_["dt"]; b["analyses"] += s_["ana"]
        b["nmax"] = max(b["nmax"], s_["n"]); b["nnzmax"] = max(b["nnzmax"], s_["nnz"])
        b["nrhs"] += s_["nrhs"]
    Tm = np.asarray((r or {}).get("T_em_Nm") or [], float)
    scal = {}
    for k_, v_ in (r or {}).items():
        if isinstance(v_, (int, float)) and not isinstance(v_, bool):
            scal[k_] = float(v_)
        elif isinstance(v_, dict):
            for k2, v2 in v_.items():
                if isinstance(v2, (int, float)) and not isinstance(v2, bool):
                    scal[f"{k_}.{k2}"] = float(v2)
    series = {}
    for k_ in ("T_em_Nm", "psi_A", "psi_B", "psi_C", "V_A", "I_A", "P_mag_eddy_W",
               "P_shaft_eddy_W"):
        v_ = (r or {}).get(k_)
        try:
            series[k_] = [float(z) for z in v_]
        except Exception:
            pass
    res = dict(
        backend=args.backend, all_scalars=scal, series=series,
        case=args.case, mode=args.mode, mesh_scale=args.mesh_scale, stopped=stopped,
        module=motor_ai_sim.__file__, wall_s=round(wall, 3),
        threads={k: os.environ.get(k) for k in ("MKL_NUM_THREADS", "OMP_NUM_THREADS")},
        env={k: v for k, v in os.environ.items() if k.startswith(("SB_", "MOTOR_AI_SIM_GEO"))},
        kwargs={k: v for k, v in kw.items() if k != "geo_override"},
        timers={k: dict(incl=round(T.incl[k], 3), excl=round(T.excl[k], 3), calls=T.calls[k])
                for k in sorted(T.incl, key=lambda z: -T.incl[z])},
        solve_ff_by_caller={k: dict(v, t=round(v["t"], 3)) for k, v in by.items()},
        solve_ff_total_s=round(sum(s_["dt"] for s_ in solves), 3),
        n_solves=len(solves), pardiso=pardiso_info, captures=captures,
        results=dict(
            n_frames_reported=int(Tm.size), n_frames_solved=(r or {}).get("n_frames_solved"),
            n_steps_per_period=(r or {}).get("n_steps_per_period"),
            eddy_warmup_frames=(r or {}).get("eddy_warmup_frames"),
            demag_prepass_frames=(r or {}).get("demag_prepass_frames"),
            eddy_settled=(r or {}).get("eddy_settled"), eddy_capped=(r or {}).get("eddy_capped"),
            eddy_time_scheme=(r or {}).get("eddy_time_scheme"),
            picard_iters_mean=(r or {}).get("picard_iters_mean"),
            picard_iters_max=(r or {}).get("picard_iters_max"),
            solve_wall_s=(r or {}).get("solve_wall_s"),
            T_avg_Nm=f("T_avg_Nm"), T_ripple_pct=f("T_ripple_pct"), V_peak=f("V_peak"),
            P_mag_solve_W=f("P_mag_solve_W"), P_shaft_solve_W=f("P_shaft_solve_W"),
            P_sleeve_solve_W=f("P_sleeve_solve_W"), P_cu_ac_W=f("P_cu_ac_W"),
            P_fe_W=f("P_fe_W"), P_loss_total_avg_W=f("P_loss_total_avg_W")),
    )
    with open(args.out, "w") as fo:
        json.dump(res, fo, indent=1, default=str)
    with open(os.path.splitext(args.out)[0] + "_solves.json", "w") as fo:
        json.dump(solves, fo)
    print(json.dumps(dict(case=args.case, mode=args.mode, wall_s=res["wall_s"],
                          solve_ff_total_s=res["solve_ff_total_s"], n_solves=len(solves),
                          frames=res["results"]["n_frames_solved"],
                          T_avg=res["results"]["T_avg_Nm"]), default=str))
    _ = orig_mesh


if __name__ == "__main__":
    main()
