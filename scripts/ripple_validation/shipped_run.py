"""The duty as the product runs it (coupled eddy, rotor eddy, demag per duty,
ripple-grade 108 requested steps), for context next to the magnetostatic
references of ripple_ref.py.  Sandbox only, solver-direct.

usage: python shipped_run.py --inp /work/in --machine d40 --duty rated --out /work/out/x
          [--steps 108] [--no-eddy] [--demag 0|1] [--ring 0]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ripple_ref import compose, DUTY_NAMES  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inp", required=True)
    ap.add_argument("--machine", required=True)
    ap.add_argument("--duty", default="rated")
    ap.add_argument("--steps", type=int, default=108)
    ap.add_argument("--no-eddy", action="store_true")
    ap.add_argument("--demag", type=int, default=-1, help="-1 = the duty's setting")
    ap.add_argument("--ring", type=int, default=0)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    if args.ring:
        os.environ["SB_SLIP_PER_PERIOD"] = str(args.ring)
    import logging
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    d, y, geo, duty, mats, st = compose(args.inp, args.machine, args.duty)
    cfg_path = os.environ["MOTOR_AI_SIM_CONFIG"]
    base = yaml.safe_load(open(cfg_path, encoding="utf-8"))
    base["geometry"].update(geo)
    base.setdefault("materials", {}).update(mats)
    base["winding"] = dict(y.get("winding") or {})
    base["parts"] = {"shaft": "included"}
    wnd = y.get("winding") or {}
    sd = str(wnd.get("star_delta") or duty.get("star_delta") or st.get("sim.starDelta") or "star")
    base.setdefault("simulation", {}).update(
        {"rpm": float(duty["rpm"]), "gamma_deg": float(duty["gamma_deg"]), "star_delta": sd})
    with open(cfg_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(base, f, allow_unicode=True, sort_keys=False)
    from motor_ai_sim.simulation import fem_solver_2d as fs
    I_term = float(duty["current_arms"])
    I_wind = I_term / (math.sqrt(3.0) if sd == "delta" else 1.0)
    cm = {k: float(v) for k, v in dict(st.get("mesh.componentMesh") or {}).items()}
    mt = st.get("sim.magnetTempC")
    eddy = not args.no_eddy
    demag = bool(st.get("sim.demag", False)) if args.demag < 0 else bool(args.demag)
    kw = dict(
        n_steps_per_period=int(args.steps), n_periods=1.0,
        gamma_deg=float(duty["gamma_deg"]), I_phase_rms=I_wind, rpm=float(duty["rpm"]),
        n_parallel=int(wnd.get("n_parallel") or 1), connection=wnd.get("connection"),
        star_delta=sd,
        daxis_deg=(float(d["daxis_deg"]) if d.get("daxis_deg") is not None else None),
        mesh_size_mm=float(st.get("mesh.meshSize", 4)),
        min_size_mm=float(st.get("mesh.minSize", 0.3)),
        outer_air_factor=float(st.get("mesh.outerAir", 1.2)),
        gap_layers=float(st.get("mesh.gapLayers", 1)),
        n_sectors=int(st.get("mesh.nSectors", 2)),
        stator_fillet_mm=0.0,
        coil_temp_c=float(st.get("sim.coilTemp", 120)),
        magnet_temp_c=(float(mt) if mt not in (None, "") else None),
        end_winding_factor=float(st.get("sim.endWinding", 0) or 0),
        rotor_eddy=eddy, demag=demag,
        pole_copy=bool(st.get("mesh.poleCopy", False)),
        iron_template=bool(st.get("mesh.ironTemplate", True)),
        geo_mesh=bool(st.get("mesh.geoMesh", True)),
        structured_gap=bool(st.get("mesh.structuredGap", True)),
        component_mesh_mm=cm, geo_override=dict(geo),
        drive="current", element_order=2, eddy=eddy)
    t0 = time.perf_counter()
    r = fs.em_transient_eval(**kw)
    wall = time.perf_counter() - t0
    keep = {}
    for k in ("T_em_Nm", "T_em_maxwell_Nm", "T_avg_Nm", "T_avg_maxwell_Nm", "T_ripple_pct",
              "T_ripple_pp_Nm", "torque_method", "torque_mean_source", "n_steps_per_period",
              "n_steps_per_period_requested", "steps_snapped", "rotor_angle_deg",
              "T_harm_order", "T_harm_amp", "cogging_sampling", "Br_kept_mean",
              "demag_Br_mean", "torque_method_diagnostics"):
        if k in r:
            keep[k] = r[k]
    keep.update(machine=args.machine, duty=args.duty, eddy=eddy, demag=demag,
                steps_requested=args.steps, ring=args.ring, wall_s=wall,
                I_winding_rms=I_wind, all_keys=sorted(r.keys()))
    with open(args.out + ".json", "w") as f:
        json.dump(keep, f, default=lambda o: (o.tolist() if hasattr(o, "tolist") else str(o)))
    print(json.dumps({k: keep.get(k) for k in ("machine", "duty", "T_avg_Nm", "T_ripple_pct",
                                                "torque_method", "n_steps_per_period", "wall_s")},
                     default=str))


if __name__ == "__main__":
    main()
