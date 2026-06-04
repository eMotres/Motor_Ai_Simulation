"""Isolated FEM-transient evaluation of ONE candidate design.

Run as a subprocess (``python -m motor_ai_sim.optimization.refine_proc``) reading
a JSON spec on stdin and writing a JSON result on stdout.  Running each FEM
transient in its own process isolates the occasional LLVM/JIT crash in the FEM
stack from the API server — a crashed eval just yields a failed design, not a
dead backend.  It also evaluates the candidate via geo_override, so the global
config / Simulation state is never touched.
"""
from __future__ import annotations

import math
from typing import Dict, Any


def run_one(overrides: Dict[str, float], current_a: float, steps: int,
            coil_temp_c: float, n_periods: float = 1.0,
            gamma_deg: float = 0.0) -> Dict[str, Any]:
    """Run the sliding-band transient for one candidate and return mean
    performance metrics (torque, efficiency, ripple, losses, mass).

    ``steps`` is the number of FEM frames actually computed; ``n_periods`` is the
    fraction of the electrical period swept.  For a cheap ripple-amplitude probe
    the scan uses n_periods=1/6 (one 6·k ripple cycle) with ~6 frames; a full
    refine uses n_periods=1 with more frames."""
    import numpy as np
    from motor_ai_sim.simulation.fem_solver_2d import fem_transient_sliding_band
    from motor_ai_sim.optimization.design_eval import build_params, _masses
    from motor_ai_sim.config import get_config

    cfg = get_config()
    geo = {**dict(cfg.get("geometry", {})), **overrides}
    rpm = float(cfg.get("simulation", {}).get("rpm", 3950.0))
    omega = 2 * math.pi * rpm / 60.0

    # n_total ≈ n_steps_per_period · n_periods → pick n_steps_per_period so the
    # window holds exactly ``steps`` frames.
    nper = max(1e-3, float(n_periods))
    nspp = max(4, int(round(int(steps) / nper)))
    d = fem_transient_sliding_band(
        n_steps_per_period=nspp, n_periods=nper, gamma_deg=float(gamma_deg),
        I_phase_rms=float(current_a), mesh_size_mm=4.0, min_size_mm=0.3,
        n_sectors=4, coil_temp_c=float(coil_temp_c), geo_override=overrides)

    Tavg = float(d["T_avg_Nm"])
    cu = float(np.mean(d["P_cu_W"])); fe = float(np.mean(d["P_fe_W"]))
    mg = float(np.mean(d["P_mag_eddy_W"]))
    ploss = cu + fe + mg
    pmech = Tavg * omega
    eff = pmech / (pmech + ploss) if pmech > 0 else 0.0
    mass = float(_masses(build_params(geo), geo)["total"])
    return {
        "T_em_Nm": round(Tavg, 3), "efficiency": round(eff, 5),
        "torque_per_mass_Nm_kg": round(Tavg / mass, 4) if mass > 0 else 0.0,
        "T_ripple_pct": round(float(d["T_ripple_pct"]), 2),
        "P_loss_total_W": round(ploss, 1), "P_cu_W": round(cu, 1),
        "P_fe_W": round(fe, 1), "P_mag_W": round(mg, 1),
        "mass_total_kg": round(mass, 3), "V_peak": round(float(d["V_peak"]), 1),
    }


if __name__ == "__main__":
    import sys, json
    spec = json.loads(sys.stdin.read())
    try:
        res = run_one(spec["overrides"], spec["current_a"],
                      spec.get("steps", 40), spec.get("coil_temp_c", 120.0),
                      n_periods=spec.get("n_periods", 1.0),
                      gamma_deg=spec.get("gamma_deg", 0.0))
        sys.stdout.write("@@RESULT@@" + json.dumps({"ok": True, "res": res}))
    except Exception as e:  # noqa: BLE001
        sys.stdout.write("@@RESULT@@" + json.dumps({"ok": False, "error": str(e)}))
