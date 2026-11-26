"""The existing ``propeller_optimize.optimize`` is unchanged by the Mission PR: every deterministic field of two existing inputs, compared with
the result of the BASE code (``origin/pre-migration-freeze-2026-09-15`` before the Mission commits), stored in
``tests/data/propeller_optimize_golden_mission_pr.json``.  Excluded: the run id, the elapsed time and the store path (the only values that
differ from run to run).  Floats compare to a relative 1e-12 (the same machine code gives the same bits; the margin only covers another
libm), everything else exactly.  The inputs: the family design with the mfoil tables and Prandtl-Glauert, the 12 in seed, op L12, the real
blade-element evaluator (no callback)."""
from __future__ import annotations

import json
import math
from pathlib import Path

from motor_ai_sim import propeller_design as pd
from motor_ai_sim import propeller_optimize as po

GOLDEN = Path(__file__).resolve().parent / "data" / "propeller_optimize_golden_mission_pr.json"


def _same(a, b, path="result"):
    if isinstance(a, float) or isinstance(b, float):
        assert isinstance(a, (int, float)) and isinstance(b, (int, float)), (path, a, b)
        assert math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-300), (path, a, b)
    elif isinstance(a, dict):
        assert isinstance(b, dict) and set(a) == set(b), (path, sorted(set(a) ^ set(b)))
        for k in a:
            _same(a[k], b[k], path + "." + str(k))
    elif isinstance(a, (list, tuple)):
        assert isinstance(b, (list, tuple)) and len(a) == len(b), (path, len(a), len(b))
        for i, (x, y) in enumerate(zip(a, b)):
            _same(x, y, "%s[%d]" % (path, i))
    else:
        assert a == b, (path, a, b)


def test_existing_optimize_inputs_give_the_base_result():
    gold = json.loads(GOLDEN.read_text(encoding="utf-8"))
    d = pd.with_compressibility(pd.with_polar_source(pd.load_design(), "mfoil_table"), "prandtl_glauert")
    base = pd.seed_vector(d, 12, 2)
    assert set(gold) == {"default_objectives", "two_objectives_flight_off"}
    for name, case in gold.items():
        r = po.optimize(d, base, "L12", **case["kwargs"])
        for k in ("run_id", "elapsed_s", "stored"):
            r.pop(k, None)
        got = json.loads(json.dumps(r, default=str))                  # the same JSON normalisation the golden file went through
        _same(case["result"], got, name)
