"""One solver-direct run through em_transient_eval, recording every torque.

usage: python coul_run.py --inp /work/in --machine d40 --duty rated --out /work/out/x
          [--steps 36] [--periods 1] [--eddy] [--demag] [--noload]
          [--gl G] [--mesh-scale S] [--ring R] [--torque-method coulomb]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coul_common import install_coulomb_timer, scale_mesh, setup  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inp", required=True)
    ap.add_argument("--machine", required=True)
    ap.add_argument("--duty", default="rated")
    ap.add_argument("--steps", type=int, default=36)
    ap.add_argument("--periods", type=float, default=1.0)
    ap.add_argument("--eddy", action="store_true")
    ap.add_argument("--demag", action="store_true")
    ap.add_argument("--noload", action="store_true")
    ap.add_argument("--gl", type=float, default=None)
    ap.add_argument("--mesh-scale", type=float, default=1.0)
    ap.add_argument("--ring", type=int, default=0)
    ap.add_argument("--torque-method", default=None)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    if args.ring:
        os.environ["SB_SLIP_PER_PERIOD"] = str(args.ring)
    import logging
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    kw, info = setup(args.inp, args.machine, args.duty)
    kw = scale_mesh(kw, args.mesh_scale)
    if args.gl is not None:
        kw["gap_layers"] = float(args.gl)
    if args.noload:
        kw["I_phase_rms"] = 0.0
    kw.update(n_steps_per_period=int(args.steps), n_periods=float(args.periods),
              rotor_eddy=bool(args.eddy), eddy=bool(args.eddy), demag=bool(args.demag))
    if args.torque_method:
        kw["torque_method"] = args.torque_method
    try:
        stats = install_coulomb_timer()
    except ImportError:                      # base code: no Coulomb module
        stats = {"calls": 0, "seconds": 0.0}
    from motor_ai_sim.simulation import fem_solver_2d as fs
    mx = {"calls": 0, "seconds": 0.0}
    orig = fs._prepare_arkkio_torque_p2

    meshes = []

    def factory(*a, **k):
        f = orig(*a, **k)
        try:
            if not getattr(fs._DAXIS_TLS, "calibrating", False):
                meshes.append({"n_elem": int(a[0].t.shape[1]), "n_nodes": int(a[0].p.shape[1]),
                               "n_dof_p2": int(a[1].N)})
        except Exception:
            pass

        def timed(A):
            t0 = time.perf_counter(); v = f(A)
            mx["seconds"] += time.perf_counter() - t0; mx["calls"] += 1
            return v
        return timed
    fs._prepare_arkkio_torque_p2 = factory
    t0 = time.perf_counter()
    r = fs.em_transient_eval(**kw)
    wall = time.perf_counter() - t0
    keep = {k: r.get(k) for k in (
        "T_em_Nm", "T_em_maxwell_Nm", "T_avg_Nm", "T_avg_maxwell_Nm", "T_ripple_pct",
        "T_ripple_pp_Nm", "torque_method", "T_mean_method", "n_steps_per_period",
        "n_steps", "n_frames_solved", "slip_nodes_per_period", "rotor_angle_deg",
        "T_harm_order", "T_harm_amp", "torque_method_diagnostics", "T_coulomb_series",
        "T_avg_coulomb_Nm", "T_ripple_pp_coulomb", "coulomb_torque", "solve_wall_s",
        "I_A", "I_B", "I_C", "psi_A_Wb", "psi_B_Wb", "psi_C_Wb", "n_periods",
        "mesh_build_events", "T_em_virtual_work_diagnostic_Nm",
        "T_avg_virtual_work_diagnostic_Nm", "virtual_work_diagnostics")}
    for k, v in r.items():            # every scalar (loss totals, settle verdicts, ...)
        if k not in keep and (v is None or isinstance(v, (bool, int, float, str))):
            keep[k] = v
    for k in ("P_cu_W", "P_fe_W", "P_mag_eddy_W", "P_shaft_eddy_W", "P_sleeve_eddy_W",
              "eddy_settle", "loss_model", "rotor_eddy_window"):
        if k in r and k not in keep:
            keep[k] = r[k]
    keep.update(machine=args.machine, duty=args.duty, args=vars(args), info=info,
                wall_s=wall, coulomb_eval=stats, maxwell_eval=mx, meshes=meshes,
                kwargs={k: v for k, v in kw.items() if k != "geo_override"})
    with open(args.out + ".json", "w") as f:
        json.dump(keep, f, default=lambda o: (o.tolist() if hasattr(o, "tolist") else str(o)))
    ct = r.get("coulomb_torque") or {}
    print(json.dumps({"machine": args.machine, "T_avg": r.get("T_avg_Nm"),
                      "T_mx": r.get("T_avg_maxwell_Nm"), "T_coul": r.get("T_avg_coulomb_Nm"),
                      "pp_rep": r.get("T_ripple_pp_Nm"), "pp_coul": r.get("T_ripple_pp_coulomb"),
                      "selfcheck": ct.get("layer_self_check"), "method": r.get("torque_method"),
                      "coul_unavail": ct.get("unavailable_reason"), "wall_s": wall,
                      "coul_s": stats, "mx_s": mx}, default=str))


if __name__ == "__main__":
    main()
