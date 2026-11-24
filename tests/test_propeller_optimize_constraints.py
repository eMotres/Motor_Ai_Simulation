"""P4b of the propeller module: the OPT-IN structural hard constraint of the optimiser (plan 9.5).  Without ``structure_material`` the run
is the one it always was (same candidates, same records); with it a candidate below the safety factor is infeasible, and the safety factor
of every candidate is written into its record.  The structural kernel is replaced by a cheap stand-in here (it has its own tests)."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from motor_ai_sim import propeller_design as pd
from motor_ai_sim import propeller_optimize as po
from motor_ai_sim import propeller_structure as ps

from test_propeller_optimize import D, seed, stub


def run(**kw):
    kw.setdefault("evaluate", stub)
    kw.setdefault("pop", 8)
    kw.setdefault("generations", 3)
    return po.optimize(D, seed(), "L12", **kw)


class FakeStructure:
    """root_stress stand-in: the safety factor falls as chord_R@0.2 grows (a thin root is the strong one in this toy)."""

    def __init__(self, fail=False):
        self.fail, self.orig_rs, self.orig_c2p = fail, ps.root_stress, pd.candidate_to_propeller2

    def __enter__(self):
        outer = self

        def c2p(d, vec):
            p2 = dict(outer.orig_c2p(d, vec))          # the real build (check_candidate needs it) plus the toy's one number
            p2["_chord"] = vec["chord_R@0.2"]
            return p2

        def rs(p2, rpm, material, *a, **k):
            if outer.fail:
                raise ps.StructureError("no section")
            return {"safety_factor": 6.0 - 20.0 * p2["_chord"], "overspeed_assumption": "x", "load_valid": True}
        ps.root_stress, pd.candidate_to_propeller2 = rs, c2p
        return self

    def __exit__(self, *a):
        ps.root_stress, pd.candidate_to_propeller2 = self.orig_rs, self.orig_c2p


def test_without_a_material_the_run_is_unchanged():
    a, b = run(seed=7), run(seed=7)
    ka = [(e["vector"], e["objectives"], e["violation"]) for e in a["evaluations"]]
    kb = [(e["vector"], e["objectives"], e["violation"]) for e in b["evaluations"]]
    assert ka == kb
    assert "structure_material" not in a["settings"]
    assert all("safety_factor" not in e for e in a["evaluations"])


def test_a_material_marks_candidates_below_the_factor_infeasible_and_records_the_factor():
    with FakeStructure():
        out = run(seed=7, structure_material="pa6_gf30", min_safety_factor=3.0)
    assert out["settings"]["structure_material"] == "pa6_gf30" and out["settings"]["min_safety_factor"] == 3.0
    sfs = [e["safety_factor"] for e in out["evaluations"] if e["note"] != "duplicate" and e["safety_factor"] is not None]
    assert sfs and min(sfs) < 3.0 < max(sfs)                  # the toy spans the limit, so the test means something
    for e in out["evaluations"]:
        if e["safety_factor"] is not None and e["safety_factor"] < 3.0:
            assert e["violation"] > 0 and not e["constraints_met"]
    for i in out["front"]:
        assert out["evaluations"][i]["safety_factor"] >= 3.0  # the front holds only candidates that pass


def test_the_default_factor_is_the_modules_three_and_the_limit_can_be_raised():
    with FakeStructure():
        base = run(seed=7, structure_material="pa6_gf30")
        strict = run(seed=7, structure_material="pa6_gf30", min_safety_factor=5.0)
    assert base["settings"]["min_safety_factor"] == ps.SAFETY_FACTOR == 3.0
    assert len(strict["front"]) <= len(base["front"])


def test_a_check_that_cannot_run_is_a_violation_not_a_pass():
    with FakeStructure(fail=True):
        out = run(seed=7, structure_material="pa6_gf30")
    assert out["front"] == []
    assert all(e["violation"] >= 1.0 for e in out["evaluations"] if e["note"] != "duplicate")


def test_the_record_stored_on_disk_carries_the_structure_block():
    d = tempfile.mkdtemp()
    with FakeStructure():
        out = run(seed=7, structure_material="pa6_gf30", store_dir=Path(d))
    rows = [json.loads(x) for x in Path(out["stored"]).read_text().splitlines()]
    assert all("structure" in r["record"] and r["record"]["structure"]["validated"] is False for r in rows if r["record"])


def test_bad_inputs_are_refused_with_a_name():
    with pytest.raises(po.OptimizeError, match="structure_material"):
        run(structure_material="unobtainium")
    with pytest.raises(po.OptimizeError, match="needs structure_material"):
        run(min_safety_factor=3.0)
    for low in (0.5, 1.0, 2.9):                               # owner rule SF 3; no lower setting (pre-merge review 2026-11-22, H2)
        with pytest.raises(po.OptimizeError, match="between 3 and 10"):
            run(structure_material="pa6_gf30", min_safety_factor=low)


def test_a_structure_result_without_valid_loads_is_infeasible():
    # pre-merge review 2026-11-22 H1: a check that does not say its loads were valid is not accepted
    class NoLoadFlag(FakeStructure):
        def __enter__(self):
            super().__enter__()
            fake = ps.root_stress
            ps.root_stress = lambda *a, **k: {k2: v for k2, v in fake(*a, **k).items() if k2 != "load_valid"}
            return self
    with NoLoadFlag():
        out = run(seed=7, structure_material="pa6_gf30")
    assert out["front"] == [] and all(e["violation"] >= 1.0 for e in out["evaluations"] if e["note"] != "duplicate")
