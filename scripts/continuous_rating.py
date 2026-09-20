#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Continuous (S1) rating of the LOADED machine under several cooling conditions.

    python scripts/continuous_rating.py --conditions conditions.json \
        --current 63.64 --rpm 10000 --gamma 10 --coil-temp 120 \
        --steps 48 --mesh 1.0 --min-size 0.3 --outer-air 1.2 --sectors 2 \
        --component-mesh '{"coil_rel": 0.5}' --json out.json

In process, through the very route the API serves
(``POST /api/coupled/continuous_rating``) — same code, same answer, no HTTP and
no server.  It starts NO electromagnetic solve: the operating point selects the
run this workspace already holds, and a point with no run behind it is refused
by name.  Point ``MOTOR_AI_SIM_CONFIG`` at a sandbox copy of ``config/`` to run
it without touching a live machine.

``--conditions`` is a JSON list of ``{"label": …, …cooling…}``; every cooling key
left out falls back to the loaded duty's own saved thermal setup, so
``{"label": "20 m/s", "air_speed_mps": 20}`` means exactly that.  With no file
the six conditions of :data:`DEFAULT_CONDITIONS` are used.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List

#: A sensible spread when the caller names none: still air, three air speeds,
#: a jacket and a bolted joint.  Labels are what the table prints.
DEFAULT_CONDITIONS: List[Dict[str, Any]] = [
    {"label": "still air + radiation (robotics)", "cooling_mode": "robotics",
     "bore_mode": "still", "emissivity": 0.9, "end_faces": "still",
     "end_face_sides": 2, "frame": "housed"},
    {"label": "forced air 10 m/s", "cooling_mode": "air", "air_speed_mps": 10.0,
     "bore_mode": "none"},
    {"label": "forced air 20 m/s", "cooling_mode": "air", "air_speed_mps": 20.0,
     "bore_mode": "none"},
    {"label": "liquid jacket, water 25 C, 2 L/min", "cooling_mode": "liquid",
     "fluid": "water", "fluid_temp_in_c": 25.0, "flow_lpm": 2.0,
     "bore_mode": "none"},
]


def _fmt(v: Any, spec: str = "%.1f", dash: str = "—") -> str:
    try:
        if v is None:
            return dash
        return spec % float(v)
    except (TypeError, ValueError):
        return dash


def table(out: Dict[str, Any]) -> str:
    """The rows a human reads: one line per condition."""
    head = ("%-34s %10s %7s %-9s %9s %9s %9s %10s %7s %6s %4s"
            % ("condition", "I_cont A", "s*", "limited by", "winding C",
               "magnet C", "T N*m", "P_shaft W", "eta", "fit %", "FEM"))
    lines = [head, "-" * len(head)]
    flagged = False
    for r in out.get("conditions") or ():
        if not r.get("ok"):
            lines.append("%-34s  REFUSED: %s"
                         % (str(r.get("label"))[:34],
                            (r.get("refusal") or {}).get("error", "")[:110]))
            continue
        p = r.get("power") or {}
        t = r.get("temperatures_c") or {}
        net = r.get("network") or {}
        lines.append("%-34s %10s %7s %-9s %9s %9s %9s %10s %7s %6s %4s" % (
            (str(r.get("label"))[:32] + (" !" if not r.get("trustworthy", True)
                                         else ""))[:34],
            _fmt(r.get("I_cont_A_rms"), "%.2f"),
            _fmt(r.get("s"), "%.3f"),
            str(r.get("limiting_part") or "—")[:9],
            _fmt(t.get("winding")), _fmt(t.get("magnet")),
            _fmt(p.get("T_em_Nm"), "%.3f"),
            _fmt(p.get("P_shaft_W"), "%.0f"),
            _fmt(p.get("eta_shaft"), "%.4f"),
            _fmt(net.get("worst_residual_pct_of_losses"), "%.2f"),
            str(r.get("n_thermal_fem_solves") or "—")))
        flagged = flagged or not r.get("trustworthy", True)
    if flagged:
        lines.append("")
        lines.append("!  the 2-D thermal map of this cooling is NOT MONOTONE "
                     "in the copper loss — the row is the first pass and not a "
                     "rating; see that row's notes")
    return "\n".join(lines)


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--conditions", help="JSON file with the conditions list")
    ap.add_argument("--current", type=float, required=True,
                    help="the reference run's phase current, A rms")
    ap.add_argument("--rpm", type=float, required=True)
    ap.add_argument("--gamma", type=float, default=0.0)
    ap.add_argument("--coil-temp", type=float, default=120.0)
    ap.add_argument("--magnet-temp", type=float, default=None)
    ap.add_argument("--steps", type=int, default=48,
                    help="frames per electrical period of the reference run")
    ap.add_argument("--periods", type=float, default=1.0)
    ap.add_argument("--mesh", type=float, default=3.0)
    ap.add_argument("--min-size", type=float, default=0.3)
    ap.add_argument("--outer-air", type=float, default=1.3)
    ap.add_argument("--sectors", type=int, default=1)
    ap.add_argument("--component-mesh", default="")
    ap.add_argument("--mode", default="motor", choices=("motor", "generator"))
    ap.add_argument("--winding-limit-c", type=float, default=None)
    ap.add_argument("--magnet-limit-c", type=float, default=None)
    ap.add_argument("--record", action="store_true",
                    help="file the table under the loaded duty "
                         "(default: nothing is written)")
    ap.add_argument("--json", help="write the whole answer here")
    a = ap.parse_args(argv)

    conds = (json.loads(open(a.conditions, encoding="utf-8").read())
             if a.conditions else DEFAULT_CONDITIONS)

    from motor_ai_sim.routes.coupled import continuous_rating

    body: Dict[str, Any] = {
        "conditions": conds, "reference": "last_run",
        "I_phase_rms": a.current, "rpm": a.rpm, "gamma_deg": a.gamma,
        "coil_temp_c": a.coil_temp, "n_steps_per_period": a.steps,
        "n_periods": a.periods, "mesh_size_mm": a.mesh,
        "min_size_mm": a.min_size, "outer_air_factor": a.outer_air,
        "n_sectors": a.sectors, "component_mesh": a.component_mesh,
        "mode": a.mode, "record": bool(a.record),
    }
    for key, val in (("magnet_temp_c", a.magnet_temp),
                     ("winding_limit_c", a.winding_limit_c),
                     ("magnet_limit_c", a.magnet_limit_c)):
        if val is not None:
            body[key] = val

    out = continuous_rating(body=body)
    print(table(out))
    ref = ((out.get("conditions") or [{}])[0] or {}).get("reference") or {}
    if ref:
        print("\nreference run: %s A rms, %s rpm, gamma %s deg, coil %s C "
              "(%s, %s)" % (ref.get("I_phase_rms_A"), ref.get("rpm"),
                            ref.get("gamma_deg"), ref.get("coil_ref_c"),
                            ref.get("computed_at"), ref.get("geo_fingerprint")))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=1, default=float)
        print("written to %s" % a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
