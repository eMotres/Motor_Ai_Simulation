"""P4 of the propeller module: the two-objective optimiser (propeller_optimize.py).

The algorithm is tested on a cheap analytic stand-in for the evaluation (a trade-off between the two objectives driven by a few design
variables, plus a tip-Mach-like constraint) so the properties of the optimiser are checked in milliseconds; two tests then run the real blade-element
evaluation on a tiny budget.  No assertion claims a good or validated propeller.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from motor_ai_sim import propeller_design as pd
from motor_ai_sim import propeller_optimize as po

D = pd.load_design()


def seed(diam=10, blades=2):
    return pd.seed_vector(D, diam, blades)


def stub(dz, v, op):
    """f1 falls and f2 rises with chord_R@0.2 (a trade-off line); both fall away from twist 20 at 0.45 R; tip Mach grows with chord_R@1."""
    pen = (v["twist_deg@0.45"] - 20.0) ** 2 / 100.0
    c = v["chord_R@0.2"]
    f1 = 1.0 - 2.0 * c - pen
    f2 = 5.0 + 20.0 * c - pen
    mach = 0.4 + 2.0 * v["chord_R@1"]
    return {"design_id": "stub", "objectives": {"thrust_per_shaft_W": f1, "cooling_air_axial_ms": f2}, "tip_mach": mach,
            "constraints": {"torque_within_limit": True, "tip_mach_within_limit": mach <= dz.tip_mach_limit, "bemt_converged": True},
            "constraints_met": mach <= dz.tip_mach_limit, "operating": {"rpm": 1.0}, "flags": []}


def run(**kw):
    kw.setdefault("evaluate", stub)
    return po.optimize(D, kw.pop("base", seed()), "L12", **kw)


def dominated(a, b, names):
    return all(a[n] >= b[n] for n in names) and any(a[n] > b[n] for n in names)


def test_same_seed_same_run_different_seed_different_run():
    a, b, c = run(seed=3, pop=8, generations=4), run(seed=3, pop=8, generations=4), run(seed=4, pop=8, generations=4)
    assert [e["vector"] for e in a["evaluations"]] == [e["vector"] for e in b["evaluations"]] and a["front"] == b["front"]
    assert [e["vector"] for e in a["evaluations"]] != [e["vector"] for e in c["evaluations"]]
    assert a["run_id"] != b["run_id"]


def test_every_evaluated_design_is_valid_and_within_the_budget():
    r = run(pop=8, generations=4)
    assert 8 <= len(r["evaluations"]) <= 8 * 5
    for e in r["evaluations"]:
        if e["note"].startswith("invalid"):
            continue
        assert pd.check_candidate(D, e["vector"]) == [], e["vector"]
    assert r["evaluations"][0]["vector"] == seed()                                           # the start design is individual 0
    assert r["settings"]["tip_mach_limit_confirmed"] is True and r["estimated"] is True and r["validated_against"] is None
    assert r["banner"] == pd.BANNER


def test_the_front_is_non_dominated_feasible_and_improves_on_the_start():
    r = run(pop=12, generations=8, seed=5)
    ev, names = r["evaluations"], r["objectives"]
    front = [ev[i] for i in r["front"]]
    assert front and all(e["violation"] == 0 and e["constraints_met"] for e in front)
    fo = [e["objectives"] for e in front]
    for i, a in enumerate(fo):
        for j, b in enumerate(fo):
            assert i == j or not dominated(a, b, names)
    # nobody feasible in the whole run dominates a front member
    feas = [e["objectives"] for e in ev if e["violation"] == 0 and all(v is not None for v in e["objectives"].values())]
    for a in fo:
        assert not any(dominated(b, a, names) for b in feas)
    # elitism: the best efficiency of the whole run is at least the best of generation 0
    g0 = max(e["objectives"][names[0]] for e in ev if e["generation"] == 0 and e["violation"] == 0)
    assert max(e["objectives"][names[0]] for e in front) >= g0 - 1e-12
    assert len(front) >= 3                                                                    # a trade-off line, not one point


def test_the_constraint_is_respected_and_can_be_relaxed():
    tight = run(pop=12, generations=6, seed=2)
    assert all(e["tip_mach"] <= D.tip_mach_limit + 1e-12 for e in (tight["evaluations"][i] for i in tight["front"]))
    loose = run(pop=12, generations=6, seed=2, enforce_tip_mach=False)
    assert max(e["tip_mach"] for e in loose["evaluations"] if e["tip_mach"] is not None) > D.tip_mach_limit                             # the limit no longer binds
    over = run(pop=12, generations=6, seed=2, tip_mach_limit=0.5)
    assert over["settings"]["tip_mach_limit"] == 0.5 and over["settings"]["tip_mach_limit_confirmed"] is False
    assert all(over["evaluations"][i]["tip_mach"] <= 0.5 + 1e-12 for i in over["front"])


def test_fixed_variables_stay_fixed_and_free_groups_are_selectable():
    r = run(pop=8, generations=3, free=["outer"])
    inner = ["chord_R@0.2", "twist_deg@0.2", "chord_R@0.3", "twist_deg@0.3", "hub_radius_mm", "root_cutout_mm"]
    for e in r["evaluations"]:
        for n in inner + ["airfoil_root", "airfoil_mid", "airfoil_tip", "blades", "diameter_in"]:
            assert e["vector"][n] == seed()[n], n
    assert any(e["vector"]["chord_R@0.65"] != seed()["chord_R@0.65"] for e in r["evaluations"][1:])
    air = run(pop=8, generations=3, free=["airfoil"])
    assert {e["vector"]["airfoil_tip"] for e in air["evaluations"]} <= set(D.raw["airfoil_options"]["tip"])
    assert all(e["vector"]["chord_R@0.45"] == seed()["chord_R@0.45"] for e in air["evaluations"])


def test_a_single_objective_is_allowed_and_the_objective_list_is_checked():
    r = run(pop=8, generations=4, objectives=["thrust_per_shaft_W"])
    assert r["objectives"] == ["thrust_per_shaft_W"] and r["front"]
    best = max(e["objectives"]["thrust_per_shaft_W"] for e in r["evaluations"] if e["violation"] == 0)
    assert all(r["evaluations"][i]["objectives"]["thrust_per_shaft_W"] == pytest.approx(best) for i in r["front"])
    for bad in ([], ["x"], ["thrust_per_shaft_W", "thrust_per_shaft_W"]):
        with pytest.raises(po.OptimizeError):
            run(objectives=bad)


def test_settings_are_validated():
    for kw in ({"pop": 3}, {"pop": 25}, {"generations": 0}, {"generations": 11}, {"pop": 20, "generations": 10}, {"free": []}, {"free": ["nope"]},
               {"tip_mach_limit": 5.0}):
        with pytest.raises(po.OptimizeError):
            run(**kw)
    with pytest.raises(po.OptimizeError):
        po.optimize(D, seed(), "L99", evaluate=stub)
    bad = seed()
    bad["blades"] = 5
    with pytest.raises(po.OptimizeError):
        run(base=bad)


def test_failed_evaluations_are_infeasible_not_fatal():
    def flaky(dz, v, op):
        if v["chord_R@0.45"] > 0.2:
            raise pd.DesignError("boom")
        return stub(dz, v, op)

    r = run(evaluate=flaky, pop=10, generations=4, seed=7)
    failed = [e for e in r["evaluations"] if e["note"].startswith("evaluation failed")]
    assert failed and all(e["violation"] >= 2.0 and e["objectives"]["thrust_per_shaft_W"] is None for e in failed)
    assert all(r["evaluations"][i]["note"] == "" for i in r["front"])


def test_every_evaluation_is_stored_in_the_shared_record_format():
    tmp = Path(tempfile.mkdtemp())
    r = run(pop=6, generations=2, store_dir=tmp)
    lines = [json.loads(x) for x in Path(r["stored"]).read_text().splitlines()]
    assert len(lines) == len(r["evaluations"]) and Path(r["stored"]).parent == tmp
    first = lines[0]
    assert first["schema"] == "propeller-eval-1" and first["run_id"] == r["run_id"] and first["tool"] == "bemt-1"
    assert first["estimated"] is True and first["validated_against"] is None and first["operating_point"] == "L12"
    assert set(first["objectives"]) == {"thrust_per_shaft_W", "cooling_air_axial_ms"} and first["vector"] == seed()
    assert [x["index"] for x in lines] == list(range(len(lines)))


def test_real_blade_element_run_on_a_tiny_budget():
    r = po.optimize(D, seed(11, 3), "L12", pop=4, generations=1, seed=1)             # the 11 in 3-blade seed is inside the tip-Mach limit
    assert r["front"] and len(r["evaluations"]) >= 4
    for e in r["evaluations"]:
        if e["design_id"]:
            assert e["rpm"] and e["tip_mach"] > 0 and "root_flow_not_validated" in e["flags"]
    best = r["evaluations"][r["front"][0]]
    assert best["objectives"]["thrust_per_shaft_W"] > 0 and best["constraints_met"]
    # the start design's own evaluation is reproduced exactly by the optimiser's individual 0
    ref = pd.evaluate_candidate(D, seed(11, 3), "L12")
    assert r["evaluations"][0]["objectives"]["thrust_per_shaft_W"] == ref["objectives"]["thrust_per_shaft_W"]
