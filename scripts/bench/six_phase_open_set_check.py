"""One six-phase set OPEN: payload-only check of a real machine, in-process.

Sandbox only.  No workspace, no ledger, no catalog: the solver is called
directly (``em_transient_eval``) with the payload's geometry and materials and a
PRIVATE config copy (``MOTOR_AI_SIM_CONFIG``) whose winding block is the
machine's six-phase one, so nothing a run makes is stored anywhere but the
output JSON.

  python six_phase_open_set_check.py <payload.json> <out.json> <case> [<case> ...]

``payload.json`` is a kernel-run payload of the machine (``geo``, ``mat``,
``connection``, mesh keys) — e.g. one the app's runner recorded.  Cases:

  healthy:<I>:<rpm>        both sets, I = terminal phase current
  open2:<I>:<rpm>          set 2 open, I = set 1's (its controller's) current

Environment: ``N_STEPS`` (default 24), ``GAMMA`` (2.8), ``T_MAG`` (66),
``T_COIL`` (118), ``N_PAR`` (2), ``LAYERS`` (1).
"""
from __future__ import annotations

import json
import math
import os
import sys
import time


def main(pl_path, out_path, cases):
    from motor_ai_sim.material_context import set_request_materials
    from motor_ai_sim.simulation.fem_solver_2d import em_transient_eval
    from motor_ai_sim.winding_sets import resolve_six_phase, with_open_set

    pl = json.load(open(pl_path))
    geo = json.loads(pl["geo"]) if isinstance(pl["geo"], str) else dict(pl["geo"])
    mat = json.loads(pl["mat"]) if isinstance(pl.get("mat"), str) else pl.get("mat")
    S, P = int(geo["num_slots"]), int(geo["num_poles"])
    spec = resolve_six_phase(
        {"phases": 6, "n_parallel": int(os.environ.get("N_PAR", 2))},
        num_slots=S, num_poles=P,
        single_layer=int(os.environ.get("LAYERS", 1)) == 1,
        star_delta=str(pl.get("star_delta") or "star"))
    if geo.get("stator_inner_radius") and geo.get("rotor_outer_radius"):
        r_gap = 0.5e-3 * (float(geo["stator_inner_radius"])
                          + float(geo["rotor_outer_radius"]))
    else:
        r_gap = ((float(geo["stator_diameter"]) / 2 - float(geo["core_thickness"])
                  - float(geo["slot_height"])) - 0.5 * float(geo["air_gap"])) * 1e-3
    out = {"payload": os.path.basename(pl_path), "slots": S, "poles": P,
           "slot_set": spec["slot_set"], "r_gap_m": r_gap, "cases": {}}
    for c in cases:
        kind, I, rpm = c.split(":")
        six = spec if kind == "healthy" else with_open_set(spec, int(kind[-1]))
        kw = dict(
            n_steps_per_period=int(os.environ.get("N_STEPS", 24)), n_periods=1.0,
            gamma_deg=float(os.environ.get("GAMMA", 2.8)), I_phase_rms=float(I),
            rpm=float(rpm), n_sectors=1, connection=str(pl.get("connection")),
            star_delta=str(pl.get("star_delta") or "star"),
            mesh_size_mm=float(pl["mesh_size_mm"]), min_size_mm=float(pl["min_size_mm"]),
            outer_air_factor=float(pl["outer_air_factor"]),
            gap_layers=float(pl.get("gap_layers") or 1.0),
            coil_temp_c=float(os.environ.get("T_COIL", 118.0)),
            magnet_temp_c=float(os.environ.get("T_MAG", 66.0)),
            end_winding_factor=float(pl.get("end_winding_factor") or 0.0),
            iron_template=True, geo_mesh=True, structured_gap=True,
            element_order=2, eddy=False, rotor_eddy=False, demag=False,
            inc_ldq=True, drive="current", geo_override=geo, six_phase=six)
        set_request_materials(mat)
        t0 = time.time()
        try:
            r = em_transient_eval(**kw)
        finally:
            set_request_materials(None)
        T = float(r["T_avg_Nm"])
        fb = dict(r.get("rotor_net_force") or {})
        for k in ("Fx_N", "Fy_N"):
            fb.pop(k, None)
        res = {
            "wall_s": round(time.time() - t0, 1),
            "I_phase_rms": float(I), "rpm": float(rpm),
            "T_avg_Nm": T, "T_ripple_pct": r.get("T_ripple_pct"),
            "T_ripple_pp_Nm": r.get("T_ripple_pp_Nm"),
            "V_peak_V": r.get("V_peak"),
            "P_cu_dc_W": r.get("P_cu_dc_W"),
            "P_cu_W": (sum(r["P_cu_W"]) / len(r["P_cu_W"])
                       if isinstance(r.get("P_cu_W"), list) else r.get("P_cu_W")),
            "P_fe_W": (sum(r["P_fe_W"]) / len(r["P_fe_W"])
                       if isinstance(r.get("P_fe_W"), list) else r.get("P_fe_W")),
            "R_phase_ohm": r.get("R_phase_ohm"),
            "rotor_net_force": fb,
            "F_hand_tangential_N": 2.0 / math.pi * abs(T) / r_gap,
            "six_phase": {k: v for k, v in (r.get("six_phase") or {}).items()
                          if k in ("degraded", "per_set_flux", "inductances",
                                   "sets_in_model", "source_sum_residual",
                                   "solve", "error")},
            "copper_heat_uniform_factor": r.get("copper_heat_uniform_factor"),
            "picard_converged": r.get("picard_converged"),
        }
        out["cases"][c] = res
        print("CASE", c, json.dumps({k: res[k] for k in (
            "wall_s", "T_avg_Nm", "T_ripple_pct", "V_peak_V", "P_cu_dc_W",
            "F_hand_tangential_N")}), json.dumps(fb.get("F_mean_mag_N")), flush=True)
        json.dump(out, open(out_path, "w"), indent=1, default=str)
    print("DONE", flush=True)


if __name__ == "__main__":
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2], sys.argv[3:])
