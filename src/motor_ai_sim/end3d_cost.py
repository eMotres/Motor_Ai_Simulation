"""How long a Stage A sweep takes, estimated BEFORE it starts, so the catalogue does not start one it cannot finish.

The catalogue passport builds its 3-D end-effect factor in a child process with a hard 7200 s timeout (coordinator
2026-11-25: the timeout stays).  A sweep that cannot finish inside it is not started: the passport ships 2-D with the
note "3-D passport: run as a job" and the full Stage A runs through the job queue (``scripts/stage_a_job.py``), its
result adopted through ``POST /api/static3d/passport/adopt``.

The rule (constants and their evidence; every measurement is a payload-only us1 job of 2026-11-18 .. 25, 2-4 cpus):

    dofs  = DOF_PER_ELEMENT x TETS_PER_PRISM x n_tri x layers        layers = n_stack + n_cap + LAYER_MARGIN
    t     = OVERHEAD_S + SEC_PER_DOF_ITER x dofs x (ITER_COLD x n_cold + ITER_WARM x n_warm)
            n_cold = min(rows, 2)  (the reference, and the longest row: a longer stack may add axial layers and then
                                    starts cold - run A's 180 mm row did)
            n_warm = rows - n_cold
    fits  = t <= BUDGET_SHARE x 7200 s

* ``n_tri`` is the 2-D cross-section mesh of the run (cheap to build; ``preflight`` builds it the way run_stage_a does).
* DOF_PER_ELEMENT 1.40: P2 dofs / tets measured 1.375-1.40 (478 075 / 341 424 ... 860 535 / 625 944).
* TETS_PER_PRISM 3 and layers: elements = 3 x n_tri x layers exactly; layers measured 11-12 at n_stack 4 / n_cap 6,
  18 at 8 / 10, 22 at 12 / 10, 26 at 12 / 14  ->  n_stack + n_cap (+ LAYER_MARGIN 2).
* SEC_PER_DOF_ITER 2.1e-4 s: the LARGEST of 14 measured cold and warm solves (8.1e-5 .. 2.06e-4 s per dof per Picard
  iteration; 2-4 cpus, a shared server).
* ITER_COLD 46 (cold solves stop at 42-46 iterations, the cap) and ITER_WARM 25 (warm-started rows 15-25).
* OVERHEAD_S 300: the 2-D leg (10-115 s measured), meshing and start-up.
* BUDGET_SHARE 0.8: 20 % of the timeout kept free on top of the worst-case constants.

Checked against the seven measured runs, the rule over-predicts every one (1.5-3.1 x): the 400 mm pilot (1 quick row)
4 917 s estimated / 3 122 s measured; run A (3 default rows) 19 873 / 11 491; run B (2 rows, n_stack 12) 18 769 /
12 633; the 40 mm full run 21 510 / 11 569; its 1-row quick proof 5 303 / 1 730; 80 mm 3-row runs 15 659 / 6 668 and
15 680 / 5 885.  Consequence: a 4-row quick catalogue sweep (auto + the 0.75 row) fits only below about 3 600
cross-section triangles (5 rows: 3 100; 1 row: 11 200); the measured machines (7 400 - 11 700) go to the job queue.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional, Sequence

CATALOG_CHILD_TIMEOUT_S = 7200
BUDGET_SHARE = 0.8
SEC_PER_DOF_ITER = 2.1e-4
ITER_COLD = 46
ITER_WARM = 25
DOF_PER_ELEMENT = 1.40
TETS_PER_PRISM = 3
LAYER_MARGIN = 2
OVERHEAD_S = 300.0

JOB_NOTE = "3-D passport: run as a job"


def estimate_stage_a(n_tri: int, rows: int, n_stack: int = 4, n_cap: int = 6,
                     timeout_s: float = CATALOG_CHILD_TIMEOUT_S) -> Dict[str, Any]:
    """The rule of the module docstring: ``{dofs, seconds, budget_s, fits, rows, layers, n_tri}``."""
    n_tri, rows = int(n_tri), int(rows)
    if n_tri <= 0 or rows <= 0:
        raise ValueError("n_tri and rows must be positive")
    layers = int(n_stack) + int(n_cap) + LAYER_MARGIN
    dofs = DOF_PER_ELEMENT * TETS_PER_PRISM * n_tri * layers
    n_cold = min(rows, 2)
    n_warm = rows - n_cold
    seconds = OVERHEAD_S + SEC_PER_DOF_ITER * dofs * (ITER_COLD * n_cold + ITER_WARM * n_warm)
    budget = BUDGET_SHARE * float(timeout_s)
    return {"n_tri": n_tri, "rows": rows, "layers": layers, "dofs": int(round(dofs)),
            "seconds": float(seconds), "budget_s": budget, "fits": bool(seconds <= budget),
            "rule": "t = %g + %g * dofs * (%d * cold + %d * warm), dofs = %.2f * %d * n_tri * (n_stack + n_cap + %d)"
                    % (OVERHEAD_S, SEC_PER_DOF_ITER, ITER_COLD, ITER_WARM, DOF_PER_ELEMENT, TETS_PER_PRISM,
                       LAYER_MARGIN)}


def preflight(geo_override: Optional[dict] = None, n_stack: int = 4, n_cap: int = 6,
              auto_extra: Sequence[float] = (0.75,), timeout_s: float = CATALOG_CHILD_TIMEOUT_S) -> Dict[str, Any]:
    """Build the run's 2-D cross-section exactly as ``run_stage_a`` does (no 3-D mesh, no solve) and estimate the
    ``l_factors='auto'`` sweep plus ``auto_extra``.  Runs in the catalogue CHILD process, never in the API."""
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
    est = estimate_stage_a(sect2d.n_tri, len(factors), n_stack, n_cap, timeout_s)
    est.update(stack_mm=section.stack_mm, pole_pitch_mm=tau, l_factors=factors)
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
    and the estimate that decided it."""
    return {"end3d": None, "end3d_note": JOB_NOTE,
            "end3d_estimate": {k: estimate.get(k) for k in ("seconds", "budget_s", "rows", "dofs", "n_tri", "layers",
                                                           "l_factors")}}
