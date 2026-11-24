"""Multi-objective optimiser of the own propeller — stage P4 of docs/PROPELLER_MODULE_PLAN_2026-11-15.md.

NSGA-II (fast non-dominated sorting with constrained domination, crowding distance, SBX crossover, polynomial mutation) over the design
variables of ``propeller_design``.  The result is a PARETO FRONT of the objectives, never one weighted number:

* ``thrust_per_shaft_W``   — maximise (objective 1);
* ``cooling_air_axial_ms`` — maximise (objective 2: mean axial speed at the disc in the motor band; ``None`` = the model has no value, ranked worst);
* ``flight_thrust_N``      — maximise, only with ``flight=True`` (owner 2026-11-19: forward flight at the operating point's ``flight_speed_ms``;
  the candidate is evaluated at the rated rpm and must take the rated torque there, ``propeller_design.FLIGHT_TORQUE_TOL``, so the thrust is
  compared at the rated power).
  Opt-in ``chord_max_step``: the chord must rise to one maximum and fall to the tip, neighbouring control points within that step
  (``propeller_design.chord_shape_violation``) — without it a jagged chord can win on the model alone.

Either one alone is allowed (``objectives=[...]``).  Constraints: the candidate's own validity rules (bounds, manufacturing limits; a
candidate that cannot be repaired is infeasible and is not evaluated), the motor torque limit (met by construction of the operating speed), the tip
Mach limit (selectable, ``enforce_tip_mach``; the limit may be overridden and stays unconfirmed) and solver convergence, and — opt-in, ``structure_material`` — the root safety factor of ``propeller_structure`` (plan section 9.5: a failed check is a
hard constraint).  Without ``structure_material`` nothing changes: same candidates, same records, same numbers.

Every evaluation is appended to ``<store_dir>/<run_id>.jsonl`` in one record format (``propeller-eval-1``) shared with later tools (CFD, the thrust
stand) so the fidelities can be compared.  The run is reproducible: a seed fixes every random draw.  All values are blade-element model values
with estimated polars and are not validated.
"""
from __future__ import annotations

import copy
import dataclasses
import json
import math
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

import numpy as np

from motor_ai_sim import propeller_design as pd

OBJECTIVES = ("thrust_per_shaft_W", "cooling_air_axial_ms", "flight_thrust_N")
DEFAULT_OBJECTIVES = ("thrust_per_shaft_W", "cooling_air_axial_ms")
FREE_GROUPS = ("outer", "inner", "airfoil")
EVAL_SCHEMA = "propeller-eval-1"
MAX_POP, MAX_GEN, MAX_EVALS = 20, 10, 200
_ETA_C, _ETA_M = 15.0, 20.0
_NONE_OBJ = -1e6                       # an objective the model has no value for ranks below every real value


class OptimizeError(pd.DesignError):
    pass


@dataclasses.dataclass
class Individual:
    x: np.ndarray                                  # continuous genes in [0, 1]
    cat: List[int]                                 # airfoil option index per free zone
    vector: Dict[str, Any] = dataclasses.field(default_factory=dict)
    objs: Optional[List[Optional[float]]] = None   # as returned (None = no value)
    viol: float = 0.0
    record: Optional[Dict[str, Any]] = None
    generation: int = 0
    index: int = -1
    rank: int = 0
    crowd: float = 0.0
    note: str = ""
    exact: Optional[Dict[str, Any]] = None         # the start design: used as given, not re-decoded (no float round trip)


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------

def _free_names(d: pd.Design, base: Dict[str, Any], free: Sequence[str]) -> List[Dict[str, Any]]:
    rows = [r for r in pd.variables_table(d, base) if not r.get("options") and r["group"] in free and r["group"] in ("inner", "outer")]
    return rows


def _decode(d: pd.Design, base: Dict[str, Any], rows: List[Dict[str, Any]], zones: List[str], x: np.ndarray, cat: List[int]) -> Dict[str, Any]:
    v = dict(base)
    for g, r in zip(x, rows):
        v[r["name"]] = float(r["lo"] + float(np.clip(g, 0.0, 1.0)) * (r["hi"] - r["lo"]))
    for z, c in zip(zones, cat):
        opts = d.raw["airfoil_options"][z]
        v["airfoil_" + z] = opts[int(c) % len(opts)]
    return _repair(d, v, {r["name"] for r in rows})


def _repair(d: pd.Design, v: Dict[str, Any], free_names: Optional[set] = None) -> Dict[str, Any]:
    """Make the vector satisfy the cheap structural rules: twist never increases outward, cut-out between hub and the inner zone.

    Only the names in ``free_names`` are changed (review 2026-11-24, 5a: a fixed outer twist was overwritten when only the inner group was
    free); ``None`` = every name.  A conflict with a fixed value is left for ``check_candidate`` to refuse."""
    prev = None
    for r in d.control:
        n = pd._cname("twist_deg", r)
        if prev is not None and v[n] > prev and (free_names is None or n in free_names):
            v[n] = prev
        prev = v[n]
    if free_names is None or "root_cutout_mm" in free_names:   # follow-up: a fixed cut-out is not rewritten either
        R_mm = float(v["diameter_in"]) * 25.4 / 2.0
        v["root_cutout_mm"] = min(max(v["root_cutout_mm"], v["hub_radius_mm"]), float(d.raw["inner_group_max_r_over_R"]) * R_mm)
    return v


# ---------------------------------------------------------------------------
# NSGA-II parts
# ---------------------------------------------------------------------------

def _key(ind: Individual) -> tuple:
    return (ind.viol, tuple(_NONE_OBJ if o is None else o for o in (ind.objs or [])))


def _min_objs(ind: Individual) -> List[float]:
    return [-(_NONE_OBJ if o is None else o) for o in (ind.objs or [])]


def _dominates(a: Individual, b: Individual) -> bool:
    if a.viol > 0 or b.viol > 0:
        if a.viol == 0:
            return True
        if b.viol == 0:
            return False
        return a.viol < b.viol
    ma, mb = _min_objs(a), _min_objs(b)
    return all(x <= y for x, y in zip(ma, mb)) and any(x < y for x, y in zip(ma, mb))


def _sort(pop: List[Individual]) -> List[List[Individual]]:
    n = len(pop)
    dom_by = [0] * n
    dominates: List[List[int]] = [[] for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i != j and _dominates(pop[i], pop[j]):
                dominates[i].append(j)
            elif i != j and _dominates(pop[j], pop[i]):
                dom_by[i] += 1
    fronts: List[List[int]] = [[i for i in range(n) if dom_by[i] == 0]]
    while fronts[-1]:
        nxt = []
        for i in fronts[-1]:
            for j in dominates[i]:
                dom_by[j] -= 1
                if dom_by[j] == 0:
                    nxt.append(j)
        fronts.append(nxt)
    out = []
    for k, f in enumerate(fronts[:-1]):
        for i in f:
            pop[i].rank = k
        _crowding([pop[i] for i in f])
        out.append([pop[i] for i in f])
    return out


def _crowding(front: List[Individual]) -> None:
    for ind in front:
        ind.crowd = 0.0
    if len(front) <= 2:
        for ind in front:
            ind.crowd = math.inf
        return
    m = len(_min_objs(front[0]))
    for k in range(m):
        order = sorted(front, key=lambda i: _min_objs(i)[k])
        lo, hi = _min_objs(order[0])[k], _min_objs(order[-1])[k]
        order[0].crowd = order[-1].crowd = math.inf
        if hi - lo <= 0:
            continue
        for a in range(1, len(order) - 1):
            order[a].crowd += (_min_objs(order[a + 1])[k] - _min_objs(order[a - 1])[k]) / (hi - lo)


def _tournament(pop: List[Individual], rng: np.random.Generator) -> Individual:
    a, b = pop[int(rng.integers(len(pop)))], pop[int(rng.integers(len(pop)))]
    if a.rank != b.rank:
        return a if a.rank < b.rank else b
    return a if a.crowd >= b.crowd else b


def _sbx(p1: np.ndarray, p2: np.ndarray, rng: np.random.Generator):
    c1, c2 = p1.copy(), p2.copy()
    for i in range(len(p1)):
        if rng.random() < 0.5 and abs(p1[i] - p2[i]) > 1e-12:
            u = rng.random()
            beta = (2 * u) ** (1 / (_ETA_C + 1)) if u <= 0.5 else (1 / (2 * (1 - u))) ** (1 / (_ETA_C + 1))
            c1[i] = 0.5 * ((1 + beta) * p1[i] + (1 - beta) * p2[i])
            c2[i] = 0.5 * ((1 - beta) * p1[i] + (1 + beta) * p2[i])
    return np.clip(c1, 0, 1), np.clip(c2, 0, 1)


def _mutate(x: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    y = x.copy()
    pm = 1.0 / max(len(x), 1)
    for i in range(len(y)):
        if rng.random() < pm:
            u = rng.random()
            delta = (2 * u) ** (1 / (_ETA_M + 1)) - 1 if u < 0.5 else 1 - (2 * (1 - u)) ** (1 / (_ETA_M + 1))
            y[i] = y[i] + delta
    return np.clip(y, 0, 1)


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

#: evaluator constraints with a graded violation below; every other constraint the evaluator reports counts 1 when false
_GRADED = ("torque_matches_rated", "tip_mach_within_limit")


def _violation(rec: Dict[str, Any], enforce_tip_mach: bool, limit: float) -> float:
    """One feasibility policy (pre-merge review 2026-11-22, H3): a candidate is feasible exactly when this is 0, i.e. every constraint
    the evaluator reports holds, except the tip-Mach limit when the run does not enforce it (listed in ``constraints_disabled``)."""
    v = 0.0
    c = rec.get("constraints", {})
    for k, ok in c.items():
        if not ok and k not in _GRADED:
            v += 1.0
    if not c.get("torque_matches_rated", True):           # flight mode: graded by how far the torque at the rated rpm is from the limit
        v += max(1.0, abs((rec.get("flight") or {}).get("torque_error_vs_limit") or 1.0) / pd.FLIGHT_TORQUE_TOL)
    if enforce_tip_mach and rec.get("tip_mach", 0.0) > limit:
        v += (rec["tip_mach"] - limit) / limit
    return v


def _structure_violation(design: pd.Design, vec: Dict[str, Any], rec: Dict[str, Any], material: str, sf_min: float) -> float:
    """Root safety factor of the candidate (``propeller_structure.root_stress`` at 1.2 x the operating rpm, the module's labelled
    assumption) written into the record as ``structure``; returns the violation: 0 at or above ``sf_min``, the relative shortfall below it,
    1 when the check itself cannot run (an unchecked candidate is not accepted)."""
    from motor_ai_sim import propeller_structure as ps
    try:
        o = rec.get("operating") or {}                       # review 2026-11-24 (5e): the loads at the run's own air speed and density
        s = ps.root_stress(pd.candidate_to_propeller2(design, vec), float(o["rpm"]), material,
                           rho_air=float(o.get("rho_kg_m3", 1.225)), v_inf=float(o.get("v_inf_ms") or 0.0))
        ok = bool(s.get("load_valid", False))
        sf = float(s["safety_factor"]) if ok else None
    except Exception as exc:                                 # noqa: BLE001 - the reason goes into the record
        rec["structure"] = {"material": material, "error": "%s: %s" % (type(exc).__name__, exc), "validated": False}
        return 1.0
    rec["structure"] = {"material": material, "safety_factor": sf, "required": sf_min, "passes": bool(ok and sf >= sf_min),
                        "load_valid": ok, "load_problems": s.get("load_problems", []), "check": "every-station static screening",
                        "validated": False, "overspeed_assumption": s.get("overspeed_assumption")}
    if not ok:                                           # review 2026-11-21 H1: failed loads make the candidate infeasible
        return 1.0
    return 0.0 if sf >= sf_min else (sf_min - sf) / sf_min


def _feasible(i: "Individual") -> bool:
    """The one feasibility test used for the front AND the serialized ``constraints_met`` (review H3)."""
    return bool(i.record is not None and i.viol == 0 and i.objs and all(o is not None for o in i.objs))



def _best_feasible(evaluations: Sequence["Individual"], objectives: Sequence[str]) -> Dict[str, Any]:
    """Per objective: the feasible evaluation that is best on it (index, value, rpm, thrust, power, tip Mach), for the run list."""
    out: Dict[str, Any] = {}
    feas = [i for i in evaluations if _feasible(i)]
    for k, name in enumerate(objectives):
        if not feas:
            out[name] = None
            continue
        i = max(feas, key=lambda e: e.objs[k])                 # every objective is maximised
        r = i.record or {}
        out[name] = {"index": i.index, "value": i.objs[k], "rpm": (r.get("operating") or {}).get("rpm"), "thrust_N": r.get("thrust_N"),
                     "shaft_power_W": r.get("shaft_power_W"), "tip_mach": r.get("tip_mach")}
    return out

RUN_SCHEMA = "propeller-opt-run-1"


def optimize(design: pd.Design, base: Dict[str, Any], op: str, *, objectives: Sequence[str] = DEFAULT_OBJECTIVES, pop: int = 12, generations: int = 6,
             seed: int = 1, free: Sequence[str] = FREE_GROUPS, enforce_tip_mach: bool = True, tip_mach_limit: Optional[float] = None,
             evaluate: Optional[Callable[[pd.Design, Dict[str, Any], str], Dict[str, Any]]] = None,
             store_dir: Optional[Path] = None, structure_material: Optional[str] = None,
             min_safety_factor: Optional[float] = None, flight: bool = False, chord_max_step: Optional[float] = None,
             motor_check: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Run NSGA-II from ``base`` (diameter and blade count stay as given).  Returns the evaluations, the front (indices) and the settings."""
    t0 = time.time()
    objectives = list(objectives)
    if not objectives or any(o not in OBJECTIVES for o in objectives) or len(set(objectives)) != len(objectives):
        raise OptimizeError("objectives must be a non-empty subset of %s" % list(OBJECTIVES))
    if not (isinstance(pop, int) and 4 <= pop <= MAX_POP) or not (isinstance(generations, int) and 1 <= generations <= MAX_GEN):
        raise OptimizeError("pop must be 4..%d and generations 1..%d" % (MAX_POP, MAX_GEN))
    if pop * (generations + 1) > MAX_EVALS:
        raise OptimizeError("at most %d evaluations per run (pop x (generations + 1))" % MAX_EVALS)
    if op not in design.operating_points:
        raise OptimizeError("unknown operating point %r; known: %s" % (op, sorted(design.operating_points)))
    if "flight_thrust_N" in objectives and not flight:
        raise OptimizeError("flight_thrust_N needs flight=True")
    if chord_max_step is not None and not (isinstance(chord_max_step, (int, float)) and not isinstance(chord_max_step, bool) and 0.01 <= chord_max_step <= 0.3):
        raise OptimizeError("chord_max_step must be a number between 0.01 and 0.3 (chord/R)")
    if flight and design.operating_points[op].flight_speed_ms is None:
        raise OptimizeError("operating point %r has no flight_speed_ms" % op)
    unknown = [g for g in free if g not in FREE_GROUPS]
    if unknown:                                          # review 2026-11-24 (5d): a misspelt group is refused, not dropped
        raise OptimizeError("unknown group(s) %s; known: %s" % (unknown, list(FREE_GROUPS)))
    free = list(free)
    if not free:
        raise OptimizeError("choose at least one group to vary: %s" % list(FREE_GROUPS))
    bad = pd.check_candidate(design, base)
    if bad:
        raise OptimizeError("the starting design is not valid: %s" % "; ".join("%s %s" % (b["rule"], b["name"]) for b in bad))
    if tip_mach_limit is not None:
        if not (isinstance(tip_mach_limit, (int, float)) and 0.1 <= tip_mach_limit <= 1.2):
            raise OptimizeError("tip_mach_limit must be between 0.1 and 1.2")
        raw = copy.deepcopy(design.raw)
        raw["tip_mach_limit"] = dict(raw["tip_mach_limit"], value=float(tip_mach_limit), confirmed_by=None)
        design = dataclasses.replace(design, raw=raw)
    limit = design.tip_mach_limit
    sf_min: Optional[float] = None
    if structure_material is not None:
        from motor_ai_sim import propeller_structure as ps
        try:
            ps.get_material(structure_material)
        except Exception as exc:                             # noqa: BLE001 - an unknown material is a named refusal
            raise OptimizeError("structure_material: %s" % exc)
        sf_min = ps.SAFETY_FACTOR if min_safety_factor is None else float(min_safety_factor)
        if not (isinstance(min_safety_factor, (int, float, type(None))) and not isinstance(min_safety_factor, bool)
                and ps.SAFETY_FACTOR <= sf_min <= 10.0):      # owner rule: mechanics at SF 3 minimum (review H2: no lower setting)
            raise OptimizeError("min_safety_factor must be a number between %g and 10" % ps.SAFETY_FACTOR)
    elif min_safety_factor is not None:
        raise OptimizeError("min_safety_factor needs structure_material")
    evalf = evaluate or (lambda dz, v, o: pd.evaluate_candidate(dz, v, o, flight=flight))
    rows = _free_names(design, base, free)
    zones = [z for z in ("root", "mid", "tip")] if "airfoil" in free else []
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2 ** 32:   # review 2026-11-24 (10b)
        raise OptimizeError("seed must be an integer between 0 and 2^32 - 1")
    rng = np.random.default_rng(int(seed))
    n_cont = len(rows)
    run_id = "opt-" + uuid.uuid4().hex[:10]
    out_file = None
    if store_dir is not None:
        Path(store_dir).mkdir(parents=True, exist_ok=True)
        out_file = Path(store_dir) / (run_id + ".jsonl")
    evaluations: List[Individual] = []
    cache: Dict[str, Individual] = {}

    def evaluate_ind(ind: Individual, generation: int) -> Individual:
        ind.vector = dict(ind.exact) if ind.exact is not None else _decode(design, base, rows, zones, ind.x, ind.cat)
        if ind.exact is None:                              # review 2026-11-24 (5a): the repaired values go back into the genes
            for i, r in enumerate(rows):
                ind.x[i] = float(np.clip((float(ind.vector[r["name"]]) - r["lo"]) / (r["hi"] - r["lo"]), 0.0, 1.0))
        key = json.dumps(ind.vector, sort_keys=True, default=str)
        ind.generation = generation
        if key in cache:                                   # an identical design is not evaluated twice
            src = cache[key]                               # review 2026-11-24 (5b): it points at its original and stays out of the population
            ind.objs, ind.viol, ind.record, ind.note, ind.index = src.objs, src.viol, src.record, "duplicate", src.index
            return ind
        viol = pd.check_candidate(design, ind.vector)
        if viol:
            ind.objs = [None] * len(objectives)
            ind.viol = 1.0 + 0.1 * len(viol)
            ind.note = "invalid: " + ", ".join(sorted({v["rule"] for v in viol}))
        else:
            try:
                rec = evalf(design, ind.vector, op)
            except pd.DesignError as exc:
                ind.objs, ind.viol, ind.note = [None] * len(objectives), 2.0, "evaluation failed: %s" % exc
            else:
                ind.record = rec
                ind.objs = [rec["objectives"].get(o) for o in objectives]
                ind.viol = _violation(rec, enforce_tip_mach, limit)
                missing = [o for o, val in zip(objectives, ind.objs) if val is None]
                if missing:                                # review 2026-11-24 (5c): a requested objective without a value is not feasible
                    ind.viol += 1.0
                    ind.note = "objective_unavailable: " + ", ".join(missing)
                if sf_min is not None:
                    ind.viol += _structure_violation(design, ind.vector, rec, structure_material, sf_min)
                if chord_max_step is not None:              # opt-in makeable chord shape (one maximum, bounded steps)
                    ind.viol += pd.chord_shape_violation(design, ind.vector, float(chord_max_step))
        ind.index = len(evaluations)
        evaluations.append(ind)
        cache[key] = ind
        if out_file is not None:
            with out_file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"schema": EVAL_SCHEMA, "run_id": run_id, "index": ind.index, "generation": generation, "tool": "bemt-1",
                                     "operating_point": op, "vector": ind.vector, "objectives": dict(zip(objectives, ind.objs or [])),
                                     "violation": ind.viol, "note": ind.note, "record": ind.record, "estimated": True,
                                     "validated_against": None}, default=str) + "\n")
        return ind

    def random_ind() -> Individual:
        return Individual(x=rng.random(n_cont), cat=[int(rng.integers(len(design.raw["airfoil_options"][z]))) for z in zones])

    # initial population: the start design itself, then random designs
    start = Individual(x=np.array([(float(base[r["name"]]) - r["lo"]) / (r["hi"] - r["lo"]) for r in rows]),
                       cat=[list(design.raw["airfoil_options"][z]).index(base["airfoil_" + z]) for z in zones], exact=dict(base))
    popn = [evaluate_ind(start, 0)] + [evaluate_ind(random_ind(), 0) for _ in range(pop - 1)]
    _sort(popn)
    for g in range(1, generations + 1):
        kids: List[Individual] = []
        while len(kids) < pop:
            a, b = _tournament(popn, rng), _tournament(popn, rng)
            if rng.random() < 0.9:
                c1, c2 = _sbx(a.x, b.x, rng)
            else:
                c1, c2 = a.x.copy(), b.x.copy()
            for cx, pa, pb in ((c1, a, b), (c2, b, a)):
                cat = [(pa.cat[i] if rng.random() < 0.5 else pb.cat[i]) for i in range(len(zones))]
                cat = [int(rng.integers(len(design.raw["airfoil_options"][z]))) if rng.random() < 0.1 else c for z, c in zip(zones, cat)]
                kids.append(Individual(x=_mutate(cx, rng), cat=cat))
                if len(kids) >= pop:
                    break
        kids = [evaluate_ind(k, g) for k in kids]
        merged = popn + [k for k in kids if k.note != "duplicate"]
        fronts = _sort(merged)
        nxt: List[Individual] = []
        for f in fronts:
            if len(nxt) + len(f) <= pop:
                nxt += f
            else:
                nxt += sorted(f, key=lambda i: -i.crowd)[: pop - len(nxt)]
                break
        popn = nxt
        _sort(popn)

    fronts_all = _sort(list(evaluations))
    front = sorted(i.index for i in (fronts_all[0] if fronts_all else []) if _feasible(i))
    requirements = {                                     # review H2: what this run enforced and what it did not
        "flight": bool(flight), "polar_source": pd.polar_source(design), "compressibility": design.raw.get("polars_compressibility") or "off",
        "tip_mach_limit": limit if enforce_tip_mach else "off",
        "structure": ({"material": structure_material, "min_safety_factor": sf_min, "check": "every-station static screening"}
                      if sf_min is not None else "off"),
        "chord_max_step": float(chord_max_step) if chord_max_step is not None else "off",
    }
    not_checked = (["structure"] if sf_min is None else []) + (["chord_shape"] if chord_max_step is None else []) + \
        (["tip_mach"] if not enforce_tip_mach else []) + ["full_structure"] + \
        ([] if (motor_check or {}).get("status") == "ok" else ["motor_envelope"])
    o = design.operating_points[op]
    operating = {"name": op, "rpm": o.rated_rpm_ref, "torque_limit_Nm": o.torque_limit_Nm,
                 "shaft_power_W": o.torque_limit_Nm * o.rated_rpm_ref * math.pi / 30.0, "flight_speed_ms": o.flight_speed_ms if flight else None,
                 "override": (design.raw.get("operating_override") or {}).get(op), "motor_check": motor_check}
    result = {
        "run_id": run_id, "banner": pd.BANNER, "operating_point": op, "objectives": objectives, "front": front,
        "settings": {"pop": pop, "generations": generations, "seed": int(seed), "free": free, "enforce_tip_mach": enforce_tip_mach,
                     "tip_mach_limit": limit, **({"structure_material": structure_material, "min_safety_factor": sf_min} if sf_min is not None else {}),
                     "tip_mach_limit_confirmed": bool(design.raw["tip_mach_limit"].get("confirmed_by")),
                     "diameter_in": base["diameter_in"], "blades": base["blades"],
                     "requirements": requirements, "constraints_disabled": [] if enforce_tip_mach else ["tip_mach_within_limit"],
                     "qualified": False, "not_checked": not_checked, "operating": operating},
        "evaluations": [{"index": i.index, "generation": i.generation, "vector": i.vector,
                         "objectives": dict(zip(objectives, i.objs or [])), "violation": i.viol, "note": i.note,
                         "design_id": (i.record or {}).get("design_id"), "rpm": (i.record or {}).get("operating", {}).get("rpm"),
                         "tip_mach": (i.record or {}).get("tip_mach"), "constraints_met": _feasible(i),
                         "flags": (i.record or {}).get("flags", []),
                         **({"safety_factor": ((i.record or {}).get("structure") or {}).get("safety_factor"),
                            "structure_check": "every-station static screening (placeholder material, not a qualification)"} if sf_min is not None else {})}
                        for i in evaluations],
        "elapsed_s": round(time.time() - t0, 2), "stored": str(out_file) if out_file else None, "estimated": True, "validated_against": None,
    }
    if out_file is not None:                             # the run's settings beside its records: the Designer lists stored runs from these
        out_file.with_suffix(".run.json").write_text(json.dumps(
            {"schema": RUN_SCHEMA, "run_id": run_id, "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t0)),
             "operating_point": op, "objectives": objectives, "settings": result["settings"], "front": front,
             "n_evaluations": len(evaluations), "n_feasible": sum(1 for i in evaluations if _feasible(i)), "elapsed_s": result["elapsed_s"],
             "best": _best_feasible(evaluations, objectives)},
            default=str, indent=1), encoding="utf-8")
    return result
