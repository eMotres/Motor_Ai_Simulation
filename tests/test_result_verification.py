"""Guard against storing a transient whose own gap-ring checks fail (audit 2026-11-16, CIANO28 80_12 / L12-F475)."""
from motor_ai_sim.result_verification import assess


def _payload(rel_scale, rel_mean, resid=None):
    p = {"coulomb_torque": {"layer_self_check": {"rel_to_ripple_scale": rel_scale, "rel_to_mean": rel_mean}}}
    if resid is not None:
        p["power_balance"] = {"residual_rel": resid}
    return p


def test_stored_80mm_water_1_0_is_rejected():
    # the values stored on 2026-11-12: ring check 101.6 % of the ripple scale, 89.8 % of the mean, power residual 18 %
    v = assess({"ripple_self_check_rel": 1.0160872900879139}, _payload(1.0160872900879139, 0.8977940553836707, 0.1803))
    assert v["status"] == "reject" and v["verified"] is False and v["reasons"]


def test_resaved_duty_is_flagged_not_rejected():
    v = assess({"ripple_self_check_rel": 0.073}, _payload(0.073, 0.03))
    assert v["status"] == "warn" and v["verified"] is False


def test_good_run_is_verified():
    v = assess({"ripple_self_check_rel": 0.012}, _payload(0.012, 0.01, 0.02))
    assert v["status"] == "ok" and v["verified"] is True and not v["reasons"]


def test_missing_checks_are_unchecked_not_passed():
    v = assess({"T_avg_Nm": 1.0, "ripple_self_check_rel": None}, {"coulomb_torque": {}})
    assert v["verified"] is True and "ring_disagreement_rel_to_ripple_scale" in v["unchecked"]


def test_not_a_transient_is_not_judged():
    assert assess({"T_avg_Nm": 1.0}, None)["verified"] is None


def test_summary_alone_is_enough():
    assert assess({"ripple_self_check_rel": 0.9})["status"] == "reject"
