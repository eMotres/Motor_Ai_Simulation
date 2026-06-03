"""Pareto design search over geometry + operating-point variables.

Strategy: Latin-Hypercube sample the variable box, evaluate every candidate with
the fast in-memory surrogate (``design_eval.evaluate_design``), then extract the
non-dominated (Pareto) front for the two maximize objectives — torque density
(N·m/kg) and efficiency.  Because each evaluation is ~0.1 ms, thousands of
samples finish in a couple of seconds.  Fully deterministic (seeded LHS), and
totally isolated from the FEM/simulation state.
"""
from __future__ import annotations

import copy
from typing import Dict, Any, List

import numpy as np
from scipy.stats import qmc

from .design_eval import evaluate_design, DesignMetrics

# Operating-point variable names live in the operating dict, not geometry.
_OPERATING_VARS = {"gamma_deg", "current_a", "rpm"}


def _objectives(m: DesignMetrics) -> tuple[float, float]:
    """The two MAXIMIZE objectives: (torque density, efficiency)."""
    return (m.torque_per_mass_Nm_kg, m.efficiency)


def _pareto_front(points: List[tuple[float, float]]) -> List[int]:
    """Indices of non-dominated points (both objectives maximized).

    Point i is dominated if some j is >= in both objectives and > in at least
    one.  O(n²) — fine for a few thousand points.
    """
    n = len(points)
    dominated = [False] * n
    for i in range(n):
        if dominated[i]:
            continue
        ai, bi = points[i]
        for j in range(n):
            if i == j or dominated[j]:
                continue
            aj, bj = points[j]
            if (aj >= ai and bj >= bi) and (aj > ai or bj > bi):
                dominated[i] = True
                break
    return [i for i in range(n) if not dominated[i]]


def run_pareto_search(
    base_geo: Dict[str, Any],
    wind: Dict[str, Any],
    sim: Dict[str, Any],
    variables: List[Dict[str, Any]],
    operating: Dict[str, Any],
    n_samples: int = 2000,
    coil_temp_c: float = 120.0,
    seed: int = 12345,
) -> Dict[str, Any]:
    """Run the Pareto search.

    Parameters
    ----------
    base_geo : full baseline geometry dict (candidate overrides overlay this)
    wind, sim: winding / simulation config dicts (for n_series/n_parallel, rpm…)
    variables: [{name, min, max}]  — name is a geometry key OR 'gamma_deg' /
               'current_a' (operating-point variables)
    operating: {gamma_deg, current_a, rpm}  fixed operating point; any variable
               with the same name overrides its fixed value per sample
    n_samples: LHS sample count
    seed     : LHS seed (determinism)
    """
    variables = [v for v in variables if v.get("name")]
    if not variables:
        # Nothing to vary → just return the baseline point.
        base = _eval_one(base_geo, wind, sim, operating, {}, coil_temp_c)
        return {
            "points": [base.to_dict()] if base.feasible else [],
            "pareto_indices": [0] if base.feasible else [],
            "baseline": base.to_dict(),
            "n_total": 1, "n_feasible": int(base.feasible),
            "variables": [], "objective": "pareto_torque_density_vs_efficiency",
        }

    dim = len(variables)
    lo = np.array([float(v["min"]) for v in variables])
    hi = np.array([float(v["max"]) for v in variables])
    sampler = qmc.LatinHypercube(d=dim, seed=int(seed))
    unit = sampler.random(n=int(n_samples))           # (n, dim) in [0,1]
    samples = qmc.scale(unit, lo, hi)                 # scaled to bounds

    points_out: List[Dict[str, Any]] = []
    obj_pairs: List[tuple[float, float]] = []
    feas_index_map: List[int] = []                    # idx into points_out

    for row in samples:
        overrides = {variables[k]["name"]: float(row[k]) for k in range(dim)}
        m = _eval_one(base_geo, wind, sim, operating, overrides, coil_temp_c)
        d = m.to_dict()
        points_out.append(d)
        if m.feasible and m.mass_total_kg > 0 and m.T_em_Nm > 0:
            obj_pairs.append(_objectives(m))
            feas_index_map.append(len(points_out) - 1)

    # Pareto front among feasible points, mapped back to points_out indices.
    front_local = _pareto_front(obj_pairs) if obj_pairs else []
    pareto_indices = [feas_index_map[i] for i in front_local]
    # sort the front by efficiency for a tidy curve
    pareto_indices.sort(key=lambda i: points_out[i]["efficiency"])

    base = _eval_one(base_geo, wind, sim, operating, {}, coil_temp_c)
    return {
        "points": points_out,
        "pareto_indices": pareto_indices,
        "baseline": base.to_dict(),
        "n_total": len(points_out),
        "n_feasible": len(feas_index_map),
        "variables": [{"name": v["name"], "min": float(v["min"]),
                       "max": float(v["max"])} for v in variables],
        "objective": "pareto_torque_density_vs_efficiency",
    }


def _eval_one(base_geo, wind, sim, operating, overrides, coil_temp_c) -> DesignMetrics:
    """Overlay overrides on baseline geo + operating point, then evaluate."""
    geo = copy.deepcopy(base_geo)
    gamma = float(operating.get("gamma_deg", 0.0))
    current = float(operating.get("current_a", sim.get("max_current", 85.0)))
    rpm = float(operating.get("rpm", sim.get("rpm", 3950.0)))
    geo_over: Dict[str, float] = {}
    for name, val in overrides.items():
        if name == "gamma_deg":
            gamma = float(val)
        elif name == "current_a":
            current = float(val)
        elif name == "rpm":
            rpm = float(val)
        else:
            geo[name] = float(val)
            geo_over[name] = float(val)
    # record the operating overrides too, so the UI can show them
    record = dict(geo_over)
    if "gamma_deg" in overrides:
        record["gamma_deg"] = gamma
    if "current_a" in overrides:
        record["current_a"] = current
    if "rpm" in overrides:
        record["rpm"] = rpm
    return evaluate_design(geo, wind, sim, gamma, current, rpm,
                           coil_temp_c=coil_temp_c, overrides=record)
