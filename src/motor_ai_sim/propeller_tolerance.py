"""Tolerance robustness of the own-propeller candidates — plan section 9.5, first bullet (docs/PROPELLER_MODULE_PLAN_2026-11-15.md).

A candidate is re-evaluated with the shape perturbed inside a tolerance: chord (+- fraction), twist (+- degrees), thickness ratio
(+- fraction) and the trailing edge thickened to a minimum (mm).  The set is the corner cases (each variable group at its + and - extreme,
all-plus, all-minus, the trailing edge at its minimum) plus a small SEEDED random sample.  Every perturbed vector is clamped to the design
bounds of ``own_family.yaml`` and repaired to a non-increasing twist, so it is a valid candidate (the clamping is recorded per case).
Each case goes through ``propeller_design.evaluate_candidate`` (blade-element model, generic polars, not validated).  The reported
performance is the worst case and the spread over nominal + all cases, not the ideal value.

THE TOLERANCE VALUES ARE PLACEHOLDERS: they are not measured, not guaranteed by any factory, and every result says so
(``tolerance_source``).  The real values are a factory question (ISO 20457 class or the shop's own, plan 9.6 question 7).
A case that fails (invalid vector, no positive torque, ...) is listed under ``failed`` with its reason; it is never dropped silently.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from motor_ai_sim import propeller_airfoil as af
from motor_ai_sim import propeller_design as pd

TOLERANCE_SOURCE = "placeholder - factory question (ISO 20457 class or the shop's own)"
BANNER = pd.BANNER

#: metric -> (path in the evaluation result, True when a higher value is better).  Shaft power: lower is read as better here (same torque
#: limit, less power drawn); "worst" for it is the highest value.
METRICS: Dict[str, Tuple[Tuple[str, ...], bool]] = {
    "thrust_N": (("thrust_N",), True),
    "shaft_power_W": (("shaft_power_W",), False),
    "thrust_per_shaft_W": (("objectives", "thrust_per_shaft_W"), True),
    "cooling_air_axial_ms": (("objectives", "cooling_air_axial_ms"), True),
}



@dataclass(frozen=True)
class Tolerance:
    """+- tolerances.  The defaults are PLACEHOLDERS (see the module docstring), not a factory statement."""
    chord_frac: float = 0.02              # +- fraction of the chord at every control point
    twist_deg: float = 1.0                # +- degrees at every control point
    thickness_frac: float = 0.05          # +- fraction of the thickness ratio at every station
    te_min_mm: float = 0.30               # trailing edge thickened to at least this (mm)

    @property
    def source(self) -> str:                  # fixed on purpose: no caller can relabel a placeholder as measured
        return TOLERANCE_SOURCE

    def __post_init__(self):
        for n in ("chord_frac", "twist_deg", "thickness_frac", "te_min_mm"):
            v = getattr(self, n)
            if not (isinstance(v, (int, float)) and np.isfinite(v) and v >= 0):
                raise pd.DesignError("tolerance %s must be a finite number >= 0" % n)
        if self.chord_frac >= 1 or self.thickness_frac >= 1:
            raise pd.DesignError("chord / thickness tolerance fractions must be below 1")


@dataclass(frozen=True)
class Perturbation:
    name: str
    kind: str                              # "corner" | "random"
    vec: Dict[str, Any]
    thickness_scale: float = 1.0
    te_min_mm: Optional[float] = None
    clamped: Tuple[str, ...] = ()          # design variables moved back to a design bound
    repaired: bool = False                 # twist ordering repaired after the clamp / random draw


def _clip(x: float, lo: float, hi: float) -> float:
    return min(max(x, lo), hi)


def _shift(d: pd.Design, vec: Dict[str, Any], s_chord: float, s_twist: float, chord_f: Optional[Dict[str, float]] = None,
           twist_d: Optional[Dict[str, float]] = None) -> Tuple[Dict[str, Any], Tuple[str, ...], bool]:
    """Chord x (1 + s_chord) and twist + s_twist per control point (explicit per-point values override), clamped to the bounds."""
    b = d.raw["bounds"]
    out = dict(vec)
    clamped: List[str] = []
    for r in d.control:
        for kind, key in (("chord_R", "chord_over_R"), ("twist_deg", "twist_deg")):
            n = pd._cname(kind, r)
            lo, hi = b[key]
            if kind == "chord_R":
                v = float(vec[n]) * (1.0 + (chord_f[n] if chord_f else s_chord))
            else:
                v = float(vec[n]) + (twist_d[n] if twist_d else s_twist)
            c = _clip(v, lo, hi)
            if c != v:
                clamped.append(n)
            out[n] = c
    repaired = False
    tn = [pd._cname("twist_deg", r) for r in d.control]
    for i in range(len(tn) - 1):                       # twist must not increase outward (check_candidate rule)
        if out[tn[i + 1]] > out[tn[i]]:
            out[tn[i + 1]] = out[tn[i]]
            repaired = True
    return out, tuple(clamped), repaired


def perturbations(vec: Dict[str, Any], tol: Tolerance, n_random: int = 6, seed: int = 0, d: Optional[pd.Design] = None) -> List[Perturbation]:
    """Corner cases (9) then ``n_random`` seeded random cases.  The nominal vector is NOT in the list.

    Corners: chord+, chord-, twist+, twist-, thickness+, thickness-, trailing edge at ``te_min_mm``, all_plus (chord, twist, thickness
    all at +), all_minus (all at -).  Random: every control point's chord and twist and a blade-wide thickness scale drawn uniformly in
    the tolerance, from ``numpy.random.RandomState(seed)`` (same seed, same list)."""
    d = d or pd.load_design()
    out: List[Perturbation] = []

    def add(name, s_c, s_t, s_th, te=None):
        v, cl, rep = _shift(d, vec, s_c * tol.chord_frac, s_t * tol.twist_deg)
        out.append(Perturbation(name, "corner", v, 1.0 + s_th * tol.thickness_frac, te, cl, rep))

    add("chord+", 1, 0, 0)
    add("chord-", -1, 0, 0)
    add("twist+", 0, 1, 0)
    add("twist-", 0, -1, 0)
    add("thickness+", 0, 0, 1)
    add("thickness-", 0, 0, -1)
    add("trailing_edge_min", 0, 0, 0, te=tol.te_min_mm)
    add("all_plus", 1, 1, 1)
    add("all_minus", -1, -1, -1)
    rs = np.random.RandomState(int(seed))
    for i in range(int(n_random)):
        cf = {pd._cname("chord_R", r): float(rs.uniform(-1, 1)) * tol.chord_frac for r in d.control}
        td = {pd._cname("twist_deg", r): float(rs.uniform(-1, 1)) * tol.twist_deg for r in d.control}
        th = 1.0 + float(rs.uniform(-1, 1)) * tol.thickness_frac
        v, cl, rep = _shift(d, vec, 0.0, 0.0, cf, td)
        out.append(Perturbation("random_%d" % i, "random", v, th, None, cl, rep))
    return out


def _with_te_min(d: pd.Design, te_min_mm: Optional[float]) -> pd.Design:
    """The design with the trailing-edge minimum raised to ``te_min_mm`` (never lowered below a limit the profile already has)."""
    if te_min_mm is None:
        return d
    cur = d.manufacturing.value("te_thickness_min_mm")
    val = te_min_mm if cur is None else max(cur, te_min_mm)
    lim = af.Limit(float(val), "mm", TOLERANCE_SOURCE, None)
    limits = dict(d.manufacturing.limits)
    limits["te_thickness_min_mm"] = lim
    return dataclasses.replace(d, manufacturing=af.ManufacturingProfile(d.manufacturing.process, limits))


def _get(res: Dict[str, Any], path: Tuple[str, ...]) -> Optional[float]:
    v: Any = res
    for k in path:
        v = v.get(k) if isinstance(v, dict) else None
    return None if v is None else float(v)


def _summary(values: Dict[str, Optional[float]], higher_better: bool) -> Dict[str, Any]:
    """``values``: case name -> value (None = the model has no value).  Worst / best over the cases that have one."""
    have = {k: v for k, v in values.items() if v is not None}
    if not have:
        return {"nominal": values.get("nominal"), "worst": None, "best": None, "spread": None, "worst_case": None, "best_case": None,
                "n_values": 0, "higher_is_better": higher_better}
    lo = min(have, key=lambda k: have[k])
    hi = max(have, key=lambda k: have[k])
    worst_k, best_k = (lo, hi) if higher_better else (hi, lo)
    return {"nominal": values.get("nominal"), "worst": have[worst_k], "best": have[best_k], "spread": have[hi] - have[lo],
            "worst_case": worst_k, "best_case": best_k, "n_values": len(have), "higher_is_better": higher_better}


def evaluate_robust(d: pd.Design, vec: Dict[str, Any], op_name: str, tol: Optional[Tolerance] = None, *, n_random: int = 6, seed: int = 0,
                    v_inf: float = 0.0, **eval_kwargs: Any) -> Dict[str, Any]:
    """Nominal + every perturbation through ``propeller_design.evaluate_candidate`` (deterministic for a fixed ``seed``).

    Raises ``DesignError`` if the NOMINAL candidate cannot be evaluated.  A perturbed case that fails is listed in ``failed``
    (``name``, ``kind``, ``error``) and counted in ``n_failed``; the worst / best / spread are over nominal + the cases that succeeded,
    and ``complete`` is False when any case failed."""
    tol = tol or Tolerance()
    nominal = pd.evaluate_candidate(d, vec, op_name, v_inf=v_inf, **eval_kwargs)
    cases: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    for p in perturbations(vec, tol, n_random=n_random, seed=seed, d=d):
        try:
            res = pd.evaluate_candidate(_with_te_min(d, p.te_min_mm), p.vec, op_name, v_inf=v_inf, thickness_scale=p.thickness_scale,
                                        **eval_kwargs)       # review 2026-11-24 (9a): explicit, no patched module function
        except Exception as exc:                                    # reported, never dropped
            failed.append({"name": p.name, "kind": p.kind, "error": "%s: %s" % (type(exc).__name__, exc)})
            continue
        cases.append({"name": p.name, "kind": p.kind, "thickness_scale": p.thickness_scale, "te_min_mm": p.te_min_mm,
                      "clamped": list(p.clamped), "twist_repaired": p.repaired,
                      "flags": res["flags"], "constraints_met": res["constraints_met"], "rpm": res["operating"]["rpm"],
                      **{m: _get(res, path) for m, (path, _) in METRICS.items()}})
    metrics: Dict[str, Any] = {}
    for m, (path, hb) in METRICS.items():
        vals: Dict[str, Optional[float]] = {"nominal": _get(nominal, path)}
        vals.update({c["name"]: c[m] for c in cases})
        metrics[m] = _summary(vals, hb)
    return {
        "tolerance_source": tol.source,
        "tolerance": {"chord_frac": tol.chord_frac, "twist_deg": tol.twist_deg, "thickness_frac": tol.thickness_frac, "te_min_mm": tol.te_min_mm},
        "operating_point": op_name, "seed": int(seed), "n_random": int(n_random),
        "n_cases": len(cases) + len(failed), "n_failed": len(failed), "complete": not failed, "failed": failed,
        "nominal": nominal, "cases": cases, "metrics": metrics,
        "banner": BANNER, "estimated": True, "validated_against": None,
        "note": "worst case and spread over nominal + the perturbed cases; tolerance values are placeholders, not a factory guarantee",
        # review 2026-11-24 (9b, 9c): what the numbers can and cannot show
        "worst_is_sampled": True, "sampling": "%d global corners + %d random draws over %d design variables: a sample, not a bound"
                                              % (len(cases) + len(failed) - int(n_random), int(n_random), len([k for k in vec if "@" in k])),
        "insensitive": (["thickness", "trailing_edge_min"] if pd.polar_source(d) in ("mfoil_table", "xfoil_table", "measured") else []),
        "insensitive_reason": ("the section polars are tables of the nominal airfoil: thickness and trailing-edge changes do not reach the "
                               "aerodynamics in this model" if pd.polar_source(d) in ("mfoil_table", "xfoil_table", "measured") else None),
    }
