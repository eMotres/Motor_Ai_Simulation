"""How long a Stage A sweep takes, estimated BEFORE it starts, so the catalogue does not start one that clearly cannot
finish inside its child's 7200 s timeout (coordinator 2026-11-25: the timeout stays).

The decision uses CENTRAL constants (medians of the measured solves) and refuses only when the central estimate is
above the timeout; the worst-case estimate (the largest measured constants) is kept beside it as information.

    dofs       = 1.40 x 3 x n_tri x layers
    t_central  = 150 s + 1.40e-4 s x dofs_c x (45 + 18 x (rows - 1))       layers_c = max(n_stack + n_cap, 12)
    t_worst    = 300 s + 2.10e-4 s x dofs_w x (46 x min(rows, 2) + 25 x (rows - min(rows, 2)))
                                                                              layers_w = n_stack + n_cap + 2
    fits       = t_central <= 7200 s

Evidence (payload-only us1 jobs 2026-11-18 .. 25, 2-4 cpus, 14 cold and 15 warm solves):

* dofs per element 1.40 (P2 / tets measured 1.375-1.40); elements = 3 x n_tri x layers exactly; layers measured 11-12
  at n_stack 4 / n_cap 6, 18 at 8 / 10, 22 at 12 / 10, 26 at 12 / 14.
* s per dof per Picard iteration: cold solves 8.1e-5 .. 2.06e-4, median 1.39e-4; warm 1.32e-4 median -> 1.40e-4.
* iterations: cold median 45 (42-46, the cap), warm median 18 (15-25); the reference row is cold, the others warm-start.
* overhead: the 2-D leg 10-115 s plus meshing.

On the seven measured runs, ``t_central > 7200`` classifies all seven right (no false refusal, no false accept):
pilot 400 mm 1 quick row 3 161 s / measured 3 122; 40 mm 1 quick row 3 413 / 1 730; 80 mm 3 default rows 6 530 / 6 668
and 6 539 / 5 885 (fit); 400 mm run A 8 281 / 11 491, run B 7 879 / 12 633, 40 mm full 8 961 / 11 569 (do not fit).
The worst-case constants would refuse two of the four that fit (the 80 mm 3-row runs).  The central rule under-reads long default-fidelity sweeps (0.62-0.77)
and over-reads the small quick one (1.97): a false accept costs a killed child and a 2-D passport, as before.

Catalogue plans (``preflight``), first that fits:

1. ``auto``: ``l_factors='auto'`` plus the 0.75 row (4 rows; 5 below the end zone) - the full agreement check;
2. ``three_row``: the old catalogue sweep (0.75, 1, 1.5) - 3 rows; the check then fits the 1.0 and 1.5 rows only, an
   EXACT two-row fit (``agreement_check.exact_fit``; a per-row k error dk becomes 5 x dk in eps) or NOT CHECKED when the
   1.0 row is in the end zone.  k_flux itself is formed exactly as before; only the verdict is weaker;
3. ``job``: the passport ships 2-D with the note below, and the full Stage A runs as a job (``scripts/stage_a_job.py``).
   The web route never submits a job: ``motres-agent-job`` is root-only, the coordinator (or a session with that
   right) submits it and adopts the result through ``POST /api/static3d/passport/adopt``.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional, Sequence

CATALOG_CHILD_TIMEOUT_S = 7200

# central (decides)
SEC_PER_DOF_ITER = 1.40e-4
ITER_COLD = 45
ITER_WARM = 18
OVERHEAD_S = 150.0
MIN_LAYERS = 12
# worst case (information only)
SEC_PER_DOF_ITER_WORST = 2.10e-4
ITER_COLD_WORST = 46
ITER_WARM_WORST = 25
OVERHEAD_S_WORST = 300.0
LAYER_MARGIN_WORST = 2

DOF_PER_ELEMENT = 1.40
TETS_PER_PRISM = 3

#: the old catalogue sweep, the fallback plan
THREE_ROW_FACTORS = (0.75, 1.0, 1.5)

JOB_NOTE = ("3-D passport: run as a job (too long for the catalogue's 7200 s; the coordinator submits it, "
            "the web route cannot)")


def estimate_stage_a(n_tri: int, rows: int, n_stack: int = 4, n_cap: int = 6,
                     timeout_s: float = CATALOG_CHILD_TIMEOUT_S) -> Dict[str, Any]:
    """``{seconds (central), seconds_worst, dofs, dofs_worst, fits, rows, layers, n_tri, budget_s, rule}``."""
    n_tri, rows = int(n_tri), int(rows)
    if n_tri <= 0 or rows <= 0:
        raise ValueError("n_tri and rows must be positive")
    layers = max(int(n_stack) + int(n_cap), MIN_LAYERS)
    layers_w = int(n_stack) + int(n_cap) + LAYER_MARGIN_WORST
    dofs = DOF_PER_ELEMENT * TETS_PER_PRISM * n_tri * layers
    dofs_w = DOF_PER_ELEMENT * TETS_PER_PRISM * n_tri * layers_w
    t = OVERHEAD_S + SEC_PER_DOF_ITER * dofs * (ITER_COLD + ITER_WARM * (rows - 1))
    cold_w = min(rows, 2)
    t_w = OVERHEAD_S_WORST + SEC_PER_DOF_ITER_WORST * dofs_w * (ITER_COLD_WORST * cold_w
                                                                  + ITER_WARM_WORST * (rows - cold_w))
    return {"n_tri": n_tri, "rows": rows, "layers": layers, "dofs": int(round(dofs)), "dofs_worst": int(round(dofs_w)),
            "seconds": float(t), "seconds_worst": float(t_w), "budget_s": float(timeout_s), "fits": bool(t <= timeout_s),
            "rule": "t = %g + %g * dofs * (%d + %d * (rows - 1)), dofs = %.2f * %d * n_tri * max(n_stack + n_cap, %d); "
                    "fits when t <= %g s" % (OVERHEAD_S, SEC_PER_DOF_ITER, ITER_COLD, ITER_WARM, DOF_PER_ELEMENT,
                                             TETS_PER_PRISM, MIN_LAYERS, timeout_s)}


def plan_catalogue(n_tri: int, auto_factors: Sequence[float], n_stack: int = 4, n_cap: int = 6,
                   timeout_s: float = CATALOG_CHILD_TIMEOUT_S) -> Dict[str, Any]:
    """The first catalogue plan that fits (module docstring): ``auto`` (``auto_factors``), ``three_row``, or ``job``.
    The answer is the chosen plan's estimate plus ``plan``, ``l_factors`` and ``estimates`` (every plan tried)."""
    tried = {}
    for plan, factors in (("auto", list(auto_factors)), ("three_row", list(THREE_ROW_FACTORS))):
        est = estimate_stage_a(n_tri, len(factors), n_stack, n_cap, timeout_s)
        tried[plan] = {k: est[k] for k in ("rows", "seconds", "seconds_worst", "fits")}
        if est["fits"]:
            return dict(est, plan=plan, l_factors=factors, estimates=tried)
    est = estimate_stage_a(n_tri, len(list(auto_factors)), n_stack, n_cap, timeout_s)
    return dict(est, fits=False, plan="job", l_factors=list(auto_factors), estimates=tried)


def preflight(geo_override: Optional[dict] = None, n_stack: int = 4, n_cap: int = 6,
              auto_extra: Sequence[float] = (0.75,), timeout_s: float = CATALOG_CHILD_TIMEOUT_S) -> Dict[str, Any]:
    """Build the run's 2-D cross-section exactly as ``run_stage_a`` does (no 3-D mesh, no solve) and choose the
    catalogue plan.  Runs in the catalogue CHILD process, never in the API."""
    from motor_ai_sim.simulation.static3d.end_effect import auto_l_factors
    from motor_ai_sim.simulation.static3d.motor_geometry import load_motor_section
    from motor_ai_sim.simulation.static3d.motor_mesh import build_section_mesh_2d, physical_mesh_sizes
    section = load_motor_section(geo_override=geo_override)
    phys = physical_mesh_sizes(section)
    h_gap = phys["h_gap"]
    h_solid = max(phys["h_solid"], h_gap)
    sect2d = build_section_mesh_2d(section, box_factor=4.0, h_gap=h_gap, h_solid=h_solid)
    tau = math.pi * float(section.mid_r_mm) / max(1, int(section.pole_pairs))
    factors = list(auto_l_factors(section.stack_mm, tau))
    for f in auto_extra:
        if not any(abs(float(f) - g) < 1e-9 for g in factors):
            factors.append(float(f))
    est = plan_catalogue(sect2d.n_tri, factors, n_stack, n_cap, timeout_s)
    est.update(stack_mm=section.stack_mm, pole_pitch_mm=tau)
    return est


def parse_child_output(stdout: str, returncode: int):
    """The catalogue child's answer: ``("ok", stage_a_dict)``, ``("job", estimate)`` (it refused the sweep as too long)
    or ``("fail", None)``."""
    import json
    lines = (stdout or "").splitlines()
    ok = next((ln for ln in lines if ln.startswith("PASSPORT3D:")), None)
    job = next((ln for ln in lines if ln.startswith("PASSPORT3D_JOB:")), None)
    if returncode == 0 and ok:
        return "ok", json.loads(ok[len("PASSPORT3D:"):])
    if returncode == 0 and job:
        return "job", json.loads(job[len("PASSPORT3D_JOB:"):])
    return "fail", None


def job_note_fields(estimate: Dict[str, Any]) -> Dict[str, Any]:
    """What the catalogue passport carries when its 3-D sweep goes to the job queue: ``end3d`` None (2-D), the note,
    and the estimate that decided it (central and worst case, every plan tried)."""
    return {"end3d": None, "end3d_note": JOB_NOTE,
            "end3d_estimate": {k: estimate.get(k) for k in ("seconds", "seconds_worst", "budget_s", "rows", "dofs",
                                                           "n_tri", "layers", "l_factors", "plan", "estimates")}}
