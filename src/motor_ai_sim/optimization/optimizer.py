"""Pareto design search — two operating points per geometry, with ripple filter.

For each sampled GEOMETRY the surrogate is evaluated at every operating point
(typically two currents I1 and I2), producing one Pareto point per (geometry,
operating-point).  The two points of a geometry are linked by a *segment* so the
UI can show how that design slides through Pareto space as the load changes.

A cogging/slot-ripple estimate (``T_ripple_pct``) gates the selection: points
whose ripple exceeds ``ripple_max_pct`` are marked not-eligible and excluded
from the Pareto front.

Latin-Hypercube sampling over the geometry box; fully deterministic (seeded);
totally isolated from the FEM/simulation state.
"""
from __future__ import annotations

import copy
from typing import Dict, Any, List

import numpy as np
from scipy.stats import qmc

from .design_eval import evaluate_design, DesignMetrics

# These names are operating-point coordinates, not geometry keys.
_OPERATING_VARS = {"gamma_deg", "current_a", "rpm"}


def _objectives(m: DesignMetrics) -> tuple[float, float]:
    """The two MAXIMIZE objectives: (torque density, efficiency)."""
    return (m.torque_per_mass_Nm_kg, m.efficiency)


def _pareto_front(points: List[tuple[float, float]]) -> List[int]:
    """Indices of non-dominated points (both objectives maximized)."""
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


def _eval(base_geo, wind, sim, geo_over, gamma, current, rpm,
          coil_temp_c) -> DesignMetrics:
    geo = copy.deepcopy(base_geo)
    rec: Dict[str, float] = {}
    for name, val in geo_over.items():
        if name == "gamma_deg":
            gamma = float(val)
            rec["gamma_deg"] = gamma
        else:
            geo[name] = float(val)
            rec[name] = float(val)
    return evaluate_design(geo, wind, sim, gamma, current, rpm,
                           coil_temp_c=coil_temp_c, overrides=rec)


def run_pareto_search(
    base_geo: Dict[str, Any],
    wind: Dict[str, Any],
    sim: Dict[str, Any],
    variables: List[Dict[str, Any]],
    operating_points: List[Dict[str, Any]],
    n_samples: int = 600,
    ripple_max_pct: float = 100.0,
    coil_temp_c: float = 120.0,
    seed: int = 12345,
) -> Dict[str, Any]:
    """Run the search.

    variables       : [{name,min,max}] — GEOMETRY keys (and optionally gamma_deg).
                      current_a / rpm come from operating_points, NOT here.
    operating_points: [{gamma_deg?, current_a, rpm}, ...] (usually two).
    ripple_max_pct  : designs with T_ripple_pct above this are excluded from the
                      front (still returned, flagged eligible=False).
    """
    # geometry/gamma design variables only
    gvars = [v for v in variables if v.get("name") and v["name"] not in {"current_a", "rpm"}]
    ops = operating_points or [{"gamma_deg": 0.0,
                                "current_a": float(sim.get("max_current", 85.0)),
                                "rpm": float(sim.get("rpm", 3950.0))}]

    points: List[Dict[str, Any]] = []
    segments: List[List[int]] = []
    obj_pairs: List[tuple[float, float]] = []
    elig_index_map: List[int] = []

    def emit(m: DesignMetrics) -> int:
        d = m.to_dict()
        eligible = bool(m.feasible and m.mass_total_kg > 0 and m.T_em_Nm > 0
                        and m.T_ripple_pct <= ripple_max_pct)
        d["eligible"] = eligible
        idx = len(points)
        points.append(d)
        if eligible:
            obj_pairs.append(_objectives(m))
            elig_index_map.append(idx)
        return idx

    if not gvars:
        # No geometry variation → just the operating points on the baseline.
        for op in ops:
            emit(_eval(base_geo, wind, sim, {}, op.get("gamma_deg", 0.0),
                       op.get("current_a", 85.0), op.get("rpm", 3950.0), coil_temp_c))
    else:
        dim = len(gvars)
        lo = np.array([float(v["min"]) for v in gvars])
        hi = np.array([float(v["max"]) for v in gvars])
        n_geom = max(20, int(n_samples))
        unit = qmc.LatinHypercube(d=dim, seed=int(seed)).random(n=n_geom)
        samples = qmc.scale(unit, lo, hi)

        def _snap(val, k):
            # 'sweep' variables take only discrete grid values (min : step : max);
            # 'optimize' variables stay continuous.
            v = gvars[k]
            if v.get("mode") == "sweep" and float(v.get("step", 0)) > 0:
                lo_k, st = float(v["min"]), float(v["step"])
                snapped = lo_k + round((val - lo_k) / st) * st
                return min(max(snapped, lo_k), float(v["max"]))
            return val

        for row in samples:
            geo_over = {gvars[k]["name"]: _snap(float(row[k]), k) for k in range(dim)}
            idxs = []
            for op in ops:
                m = _eval(base_geo, wind, sim, geo_over,
                          op.get("gamma_deg", 0.0), op.get("current_a", 85.0),
                          op.get("rpm", 3950.0), coil_temp_c)
                idxs.append(emit(m))
            if len(idxs) == 2:
                segments.append(idxs)

    front_local = _pareto_front(obj_pairs) if obj_pairs else []
    pareto_indices = [elig_index_map[i] for i in front_local]
    pareto_indices.sort(key=lambda i: points[i]["efficiency"])

    base = _eval(base_geo, wind, sim, {}, ops[0].get("gamma_deg", 0.0),
                 ops[0].get("current_a", 85.0), ops[0].get("rpm", 3950.0), coil_temp_c)

    return {
        "points": points,
        "segments": segments,
        "pareto_indices": pareto_indices,
        "baseline": base.to_dict(),
        "n_total_points": len(points),
        "n_eligible_points": len(elig_index_map),
        "n_geometries": len(segments) if segments else (1 if not gvars else 0),
        "variables": [{"name": v["name"], "min": float(v["min"]),
                       "max": float(v["max"])} for v in gvars],
        "operating_points": ops,
        "ripple_max_pct": float(ripple_max_pct),
        "objective": "pareto_torque_density_vs_efficiency",
    }
