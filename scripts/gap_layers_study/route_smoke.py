"""End-to-end check of the Coulomb default and the measured gap rule through
the Simulation route (routes.simulation.get_fem_transient), solver-direct in a
sandbox.  usage: python route_smoke.py --inp /work/in --machine d40 --out /work/out/x
          [--gl 1] [--steps 36] [--eddy] [--torque-method hybrid_maxwell_ac]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from coul_common import setup  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inp", required=True)
    ap.add_argument("--machine", required=True)
    ap.add_argument("--duty", default="rated")
    ap.add_argument("--gl", type=float, default=1.0)
    ap.add_argument("--steps", type=int, default=36)
    ap.add_argument("--eddy", action="store_true")
    ap.add_argument("--torque-method", default=None)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    import logging
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    kw, info = setup(a.inp, a.machine, a.duty)
    from motor_ai_sim.routes import simulation as sim
    args = dict(n_steps_per_period=a.steps, n_periods=1.0,
                gamma_deg=kw["gamma_deg"], I_phase_rms=info["I_terminal_rms"],
                rpm=kw["rpm"], mesh_size_mm=kw["mesh_size_mm"], min_size_mm=kw["min_size_mm"],
                outer_air_factor=kw["outer_air_factor"], gap_layers=a.gl,
                n_sectors=kw["n_sectors"], coil_temp_c=kw["coil_temp_c"],
                magnet_temp_c=kw["magnet_temp_c"], eddy=bool(a.eddy),
                rotor_eddy=bool(a.eddy), demag=False, structured_gap=True,
                iron_template=True, geo_mesh=True, star_delta=info["star_delta"],
                geo=json.dumps(kw["geo_override"]), ledger=False, fresh=True)
    if a.torque_method:
        args["torque_method"] = a.torque_method
    t0 = time.perf_counter()
    r = sim.get_fem_transient(**args)
    s = r.get("summary") or {}
    keep = {k: s.get(k) for k in ("torque_method", "T_mean_method", "T_em_avg_Nm",
                                   "T_ripple_pct", "T_avg_coulomb_Nm", "ripple_mesh_limited",
                                   "ripple_self_check_rel", "gap_refinement",
                                   "gap_layers_note", "summary_shape_v")}
    keep.update(wall_s=time.perf_counter() - t0, args={k: v for k, v in args.items() if k != "geo"},
                result_torque_method=r.get("torque_method"),
                result_gap_refinement=r.get("gap_refinement"),
                steps=r.get("n_steps_per_period"), ring=r.get("slip_nodes_per_period"))
    with open(a.out + ".json", "w") as f:
        json.dump(keep, f, default=str)
    print(json.dumps(keep, default=str))


if __name__ == "__main__":
    main()
