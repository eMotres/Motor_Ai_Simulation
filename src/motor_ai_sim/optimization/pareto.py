"""Pareto-dominance reporting NEXT TO the scalar objective F.

WHY THIS EXISTS
───────────────
The optimizer's objective is the signed perpendicular distance above the
current-only baseline line (``objective="baseline_line"`` — a standing rule, not
a setting).  On the CIANO 40 mm machine that metric was measured (tests/
test_points_cloud_and_scoring.py) to be, numerically, almost pure efficiency:

    normal = (n_td, n_eff) = (0.002012, 0.999998)
    exchange rate: 1 pp of efficiency = 4.97 Nm/kg = 49 % of the machine's
    whole torque density,
    and ripple BELOW the gate earns exactly zero — 12.30 % → 2.48 % adds
    0.000000 to F.

So a run can report "F ≤ 0, nothing better found" while its own cloud holds
designs that are better on every axis an engineer judges by.  Both statements
are true; they answer different questions.  F stays exactly as it is — the user
keeps it — and this module answers the OTHER question, in the same payload:

    is there a candidate that is at least as good as the design we started
    from on torque, on ripple AND on efficiency?

THE TRAP THIS MODULE IS BUILT AROUND
────────────────────────────────────
On 2026-08-05 a report of "31 better designs" had to be retracted: it was read
off ``config/.opt_dataset.jsonl``, the CROSS-RUN accumulator, which holds evals
from many currents and gammas.  A design solved at 78.49 A / γ 10° is a
different machine problem than the same geometry at 91.92 A / γ 16° — comparing
them is meaningless.  Hence the structural rule enforced here:

    ONLY candidates from the run's OWN points array, and only those measured at
    the run's OWN operating point, may enter either set.  Everything else is
    counted (``n_other_op``) and flagged False — never silently mixed in.

TOLERANCE
─────────
The FEM eval is bit-reproducible on this machine (determinism probe), so the
only uncertainty left in a comparison is the ROUNDING applied when the metric
was stored: torque to 1e-3 N·m, efficiency to 1e-5, ripple to 1e-2 pp, torque
density to 1e-4 Nm/kg.  The tolerances below are half of that last stored digit
— i.e. two values that print identically count as a tie, and anything that
differs in the stored digits is a real difference.  They are NOT noise margins;
there is no noise to absorb.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

# Half an ULP of the stored value — see the module docstring.
TOL: Dict[str, float] = {
    "torque": 5e-4,     # N·m      (stored to 1e-3)
    "td":     5e-5,     # N·m/kg   (stored to 1e-4)
    "ripple": 5e-3,     # %-points (stored to 1e-2)
    "eff":    5e-6,     # fraction (stored to 1e-5) = 5e-4 pp
}

# Points that are not candidates: A (the design we started from — it IS the
# reference) and B (the same geometry at +10 % current — a DIFFERENT operating
# point, which the op filter would reject anyway).
ANCHOR_KINDS = ("baseline", "baseline_bump")

# Operating-point identity.  Current is compared relatively (the worker may
# auto-adjust it to hit a target torque, and it travels as a float through JSON);
# γ absolutely, in degrees.
OP_CURRENT_RTOL = 1e-4      # 0.01 % — 91.92 A vs 101.12 A (+10 %) never match
OP_GAMMA_ATOL = 1e-6


def _f(v: Any) -> Optional[float]:
    """Finite float, or None.  NaN/Inf/None/'' all collapse to None."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def resolve_op(state: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """The operating point a run's candidates were all measured at.

    Order of authority: the auto plan's own operating point → the baseline
    metrics' solved current → the baseline POINT in the cloud.  γ additionally
    falls back to the run's MTPA result, which is where a run's γ is decided.
    """
    auto = state.get("auto") if isinstance(state.get("auto"), dict) else {}
    op = auto.get("operating_point") if isinstance(auto.get("operating_point"), dict) else {}
    base = state.get("baseline") if isinstance(state.get("baseline"), dict) else {}
    pts = state.get("points") if isinstance(state.get("points"), list) else []
    base_pt = next((p for p in pts
                    if isinstance(p, dict) and p.get("kind") == "baseline"), {}) or {}

    cur = _f(op.get("current_a"))
    if cur is None:
        cur = _f(base.get("current_a"))
    if cur is None:
        cur = _f(base_pt.get("current_a"))

    gam = _f(op.get("gamma_deg"))
    if gam is None:
        gam = _f(state.get("mtpa_gamma_deg"))
    if gam is None:
        gam = _f(base_pt.get("gamma_deg"))
    return {"current_a": cur, "gamma_deg": gam}


def same_operating_point(pt: Dict[str, Any], op: Dict[str, Optional[float]]) -> bool:
    """Was this point measured at the run's operating point?

    A point with NO current recorded is NOT assumed to belong — we cannot prove
    it and this is the exact place the retracted "31 better designs" came from.
    A point with no γ recorded DOES belong: γ lives in the run's operating point,
    not in the geometry overrides, so it is simply not stamped on every point
    (the MTPA sweep, the one thing that varies γ, is never published to the
    cloud at all).
    """
    i_run = _f(op.get("current_a"))
    i_pt = _f(pt.get("current_a"))
    if i_run is None:
        return i_pt is None          # a run with no known current: nothing to split by
    if i_pt is None:
        return False
    if abs(i_pt - i_run) > OP_CURRENT_RTOL * max(abs(i_run), 1e-9):
        return False
    g_run = _f(op.get("gamma_deg"))
    g_pt = _f(pt.get("gamma_deg"))
    if g_run is None or g_pt is None:
        return True                  # γ not stamped on the point — see docstring
    return abs(g_pt - g_run) <= OP_GAMMA_ATOL


def axes_of_point(p: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """(torque, torque-density, ripple, efficiency) of a cloud point.

    torque and torque density differ only by mass, so both are reported; when a
    point carries only one of them and its mass, the other is derived.
    """
    t = _f(p.get("torque"))
    td = _f(p.get("td"))
    m = _f(p.get("mass"))
    if t is None and td is not None and m is not None:
        t = td * m
    if td is None and t is not None and m not in (None, 0.0):
        td = t / m
    return {"torque": t, "td": td,
            "ripple": _f(p.get("ripple")), "eff": _f(p.get("eff"))}


def axes_of_metrics(m: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """Same four axes off a metrics dict (`_msum` / a raw FEM result)."""
    if not isinstance(m, dict):
        return {"torque": None, "td": None, "ripple": None, "eff": None}
    t = _f(m.get("T_em_Nm"))
    td = _f(m.get("torque_per_mass"))
    if td is None:
        td = _f(m.get("torque_per_mass_Nm_kg"))
    mass = _f(m.get("mass_total_kg"))
    if t is None and td is not None and mass is not None:
        t = td * mass
    if td is None and t is not None and mass not in (None, 0.0):
        td = t / mass
    return {"torque": t, "td": td, "ripple": _f(m.get("T_ripple_pct")),
            "eff": _f(m.get("efficiency"))}


def _keys(axis: str) -> Sequence[str]:
    return (axis, "ripple", "eff")


def _complete(a: Dict[str, Optional[float]], axis: str) -> bool:
    return all(a.get(k) is not None for k in _keys(axis))


def dominates(cand: Dict[str, Optional[float]], ref: Dict[str, Optional[float]],
              axis: str = "torque", tol: Optional[Dict[str, float]] = None) -> bool:
    """Does `cand` beat `ref` on ALL THREE axes the user judges by?

    "Beats" = not worse anywhere (torque ≥, ripple ≤, efficiency ≥, each within
    the stored-digit tolerance) AND better than the tolerance on at least one.
    The strict part matters: without it a re-evaluation of the reference design
    itself would be counted as an improvement, and the whole point of this
    number is to answer "did we find anything BETTER".
    """
    tol = tol or TOL
    if not (_complete(cand, axis) and _complete(ref, axis)):
        return False
    t_ax, t_r, t_e = tol[axis], tol["ripple"], tol["eff"]
    not_worse = (cand[axis] >= ref[axis] - t_ax
                 and cand["ripple"] <= ref["ripple"] + t_r
                 and cand["eff"] >= ref["eff"] - t_e)
    if not not_worse:
        return False
    return (cand[axis] > ref[axis] + t_ax
            or cand["ripple"] < ref["ripple"] - t_r
            or cand["eff"] > ref["eff"] + t_e)


def front_mask(rows: List[Dict[str, Optional[float]]], axis: str = "torque",
               tol: Optional[Dict[str, float]] = None) -> List[bool]:
    """The non-dominated set: row i is on the front when no other row dominates
    it (same predicate as above).  Rows missing an axis are off the front.

    O(n²) with a numpy fast path; the cloud is capped at 4000 points, which is
    ~16 M comparisons — a fraction of a second vectorised, and real runs are
    two orders of magnitude smaller.
    """
    tol = tol or TOL
    n = len(rows)
    ok = [_complete(r, axis) for r in rows]
    out = [False] * n
    idx = [i for i in range(n) if ok[i]]
    if not idx:
        return out
    t_ax, t_r, t_e = tol[axis], tol["ripple"], tol["eff"]
    A = [float(rows[i][axis]) for i in idx]
    R = [float(rows[i]["ripple"]) for i in idx]
    E = [float(rows[i]["eff"]) for i in idx]
    try:
        import numpy as _np
    except Exception:                                        # noqa: BLE001
        _np = None
    if _np is not None:
        a = _np.asarray(A); r = _np.asarray(R); e = _np.asarray(E)
        for k in range(len(idx)):
            nw = ((a >= a[k] - t_ax) & (r <= r[k] + t_r) & (e >= e[k] - t_e))
            st = ((a > a[k] + t_ax) | (r < r[k] - t_r) | (e > e[k] + t_e))
            dominated = bool(_np.any(nw & st))
            out[idx[k]] = not dominated
        return out
    for k in range(len(idx)):                                # pragma: no cover
        dominated = any(
            (A[j] >= A[k] - t_ax and R[j] <= R[k] + t_r and E[j] >= E[k] - t_e)
            and (A[j] > A[k] + t_ax or R[j] < R[k] - t_r or E[j] > E[k] + t_e)
            for j in range(len(idx)) if j != k)
        out[idx[k]] = not dominated
    return out


def report(points: List[Dict[str, Any]], baseline: Dict[str, Any],
           op: Dict[str, Optional[float]],
           tol: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """Annotate a run's own cloud and summarise it.

    Returns ``{"points": [...annotated copies...], "summary": {...}}``.  Every
    point gains four booleans — ``dominates`` / ``pareto_front`` (torque axis)
    and ``dominates_td`` / ``pareto_front_td`` (torque-density axis) — plus
    ``op_match``.  A point from another operating point, an anchor, or a point
    without the metrics to compare gets False on all four: those are the
    invariants that make a repeat of the retracted cross-run report impossible.
    """
    tol = tol or TOL
    base_ax = axes_of_metrics(baseline if isinstance(baseline, dict) else {})
    out: List[Dict[str, Any]] = []
    cand_i: List[int] = []
    n_other_op = 0
    for i, p in enumerate(points or []):
        q = dict(p) if isinstance(p, dict) else {}
        is_anchor = q.get("kind") in ANCHOR_KINDS
        match = (not is_anchor) and same_operating_point(q, op)
        if not is_anchor and not match:
            n_other_op += 1
        q["op_match"] = bool(match)
        q["dominates"] = False
        q["dominates_td"] = False
        q["pareto_front"] = False
        q["pareto_front_td"] = False
        out.append(q)
        if match:
            cand_i.append(i)

    axes = [axes_of_point(out[i]) for i in cand_i]
    n_no_torque = sum(1 for a in axes if a.get("torque") is None)

    for a, i in zip(axes, cand_i):
        out[i]["dominates"] = dominates(a, base_ax, "torque", tol)
        out[i]["dominates_td"] = dominates(a, base_ax, "td", tol)
    for axis, key in (("torque", "pareto_front"), ("td", "pareto_front_td")):
        for on, i in zip(front_mask(axes, axis, tol), cand_i):
            out[i][key] = bool(on)

    summary = {
        "axis": "torque",
        "operating_point": {"current_a": op.get("current_a"),
                            "gamma_deg": op.get("gamma_deg")},
        "tolerance": dict(tol),
        "baseline": dict(base_ax),
        "n_points": len(out),
        "n_candidates": len(cand_i),
        "n_other_op": n_other_op,
        "n_no_torque": n_no_torque,
        "n_dominating": sum(1 for i in cand_i if out[i]["dominates"]),
        "n_front": sum(1 for i in cand_i if out[i]["pareto_front"]),
        "n_dominating_td": sum(1 for i in cand_i if out[i]["dominates_td"]),
        "n_front_td": sum(1 for i in cand_i if out[i]["pareto_front_td"]),
    }
    return {"points": out, "summary": summary}
