"""Measure how far the analytic surrogate is from the FEM, and report it.

``optimization/design_eval`` anchors four scalars on ONE pinned FEM point.  That
makes it exact there and says nothing about anywhere else — so the surrogate has
to carry a MEASURED uncertainty, not a comfortable silence.  This script produces
that measurement:

  1. take the calibration anchor (the 30 mm 12s14p machine of
     tests/physics_baseline.json p2_load),
  2. perturb one geometry knob at a time, over the knobs the optimizer actually
     sweeps,
  3. run the FULL P2 sliding-band transient on each perturbed geometry, with the
     EXACT p2_load recipe (same steps, mesh, sectors, magnet) so the result is
     comparable to the pin,
  4. evaluate the surrogate on the same geometry and operating point,
  5. report the relative error distribution for torque and ripple.

Run it after re-anchoring, and paste the printed block into
``design_eval._UNCERTAINTY``::

    python scripts/_surrogate_uncertainty.py

It takes a few minutes per point (a real transient each), so it is a deliberate
measurement, not something the test suite runs.
"""
from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from motor_ai_sim.material_context import set_request_materials          # noqa: E402
from motor_ai_sim.optimization.design_eval import (                      # noqa: E402
    _ANCHOR_GEO, _ANCHOR_MAGNET, _ANCHOR_OP, _ANCHOR_WIND, evaluate_design,
)
from motor_ai_sim.simulation.fem_solver_2d import fem_transient_sliding_band  # noqa: E402

# The p2_load recipe, verbatim from tests/test_physics_regression.py COMMON.
FEM_KW = dict(
    n_steps_per_period=12, n_periods=1.0, mesh_size_mm=1.4, min_size_mm=0.35,
    gap_layers=1.0, n_sectors=2, structured_gap=True, iron_template=True,
    geo_mesh=True, coil_temp_c=120.0, rotor_eddy=False,
    element_order=2, demag=False, I_phase_rms=60.0, gamma_deg=0.0,
)

# One knob at a time, in the direction the optimizer explores.  Modest steps:
# the point is to measure the surrogate's TREND error near the anchor, and a
# perturbation big enough to change the topology would measure something else.
PERTURBATIONS = [
    ("tooth_width",        2.2),   # anchor 2.6
    ("tooth_width",        3.0),
    ("magnet_height",      3.8),   # anchor 4.5
    ("magnet_height",      5.2),
    ("slot_height",        3.8),   # anchor 4.3
    ("air_gap",            0.3),   # anchor 0.2
    ("magnet_fill_down",   0.78),  # anchor 0.9
    ("num_wires_per_slot", 5),     # anchor 6
]


def _fem(geo: dict) -> dict:
    set_request_materials({"assignment": {"magnet": _ANCHOR_MAGNET}, "materials": {}})
    try:
        d = fem_transient_sliding_band(geo_override=dict(geo), **FEM_KW)
    finally:
        set_request_materials(None)
    return {"T_avg_Nm": float(d["T_avg_Nm"]),
            "T_ripple_pct": float(d.get("T_ripple_pct", 0.0)),
            "converged": bool(d.get("picard_converged", True))}


def _surrogate(geo: dict) -> dict:
    set_request_materials({"assignment": {"magnet": _ANCHOR_MAGNET}, "materials": {}})
    try:
        m = evaluate_design(dict(geo), dict(_ANCHOR_WIND), {},
                            _ANCHOR_OP["gamma_deg"], _ANCHOR_OP["current_a"],
                            _ANCHOR_OP["rpm"], coil_temp_c=_ANCHOR_OP["coil_temp_c"])
    finally:
        set_request_materials(None)
    return {"T_avg_Nm": m.T_em_Nm, "T_ripple_pct": m.T_ripple_pct}


def main() -> None:
    rows = []
    for key, val in PERTURBATIONS:
        geo = dict(_ANCHOR_GEO)
        geo[key] = val
        t0 = time.time()
        try:
            fem = _fem(geo)
        except Exception as e:                                  # noqa: BLE001
            print(f"  {key}={val}: FEM FAILED ({e})", flush=True)
            continue
        if not fem["converged"]:
            print(f"  {key}={val}: FEM window not converged — skipped", flush=True)
            continue
        sur = _surrogate(geo)
        eT = (sur["T_avg_Nm"] - fem["T_avg_Nm"]) / fem["T_avg_Nm"] * 100.0
        eR = ((sur["T_ripple_pct"] - fem["T_ripple_pct"]) / fem["T_ripple_pct"] * 100.0
              if fem["T_ripple_pct"] > 1e-6 else float("nan"))
        rows.append({"knob": f"{key}={val}", "T_fem": fem["T_avg_Nm"],
                     "T_sur": sur["T_avg_Nm"], "T_err_pct": eT,
                     "R_fem": fem["T_ripple_pct"], "R_sur": sur["T_ripple_pct"],
                     "R_err_pct": eR})
        print(f"  {key}={val}: T {fem['T_avg_Nm']:.4f} vs {sur['T_avg_Nm']:.4f} "
              f"({eT:+.1f} %), ripple {fem['T_ripple_pct']:.3f} vs "
              f"{sur['T_ripple_pct']:.3f} ({eR:+.0f} %)  [{time.time()-t0:.0f} s]",
              flush=True)

    def _p90(vals):
        v = sorted(abs(x) for x in vals if not math.isnan(x))
        if not v:
            return -1.0
        return v[min(len(v) - 1, int(math.ceil(0.9 * len(v)) - 1))]

    out = {"T_em_pct": round(_p90([r["T_err_pct"] for r in rows]), 1),
           "T_ripple_pct": round(_p90([r["R_err_pct"] for r in rows]), 1),
           "n_points": len(rows)}
    print("\npaste into design_eval._UNCERTAINTY:")
    print(json.dumps(out, indent=4))
    Path(__file__).with_name("_surrogate_uncertainty_result.json").write_text(
        json.dumps({"summary": out, "rows": rows}, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
