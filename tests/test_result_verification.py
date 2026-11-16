"""Guard against storing a transient whose own gap-ring checks fail (audit 2026-11-16, CIANO28 80_12 / L12-F475)."""
from motor_ai_sim.result_verification import assess


def _payload(rel_scale, mean_diff, resid=None, t_avg=-0.12332, pp=0.10896):
    p = {"coulomb_torque": {"T_avg_coulomb_Nm": t_avg, "T_ripple_pp_coulomb": pp,
                            "layer_self_check": {"rel_to_ripple_scale": rel_scale, "mean_diff_Nm": mean_diff}}}
    if resid is not None:
        p["power_balance"] = {"residual_rel": resid}
    return p


def test_stored_80mm_water_1_0_is_rejected():
    # the values stored on 2026-11-12: ring check 101.6 % of the ripple scale, 89.8 % of the mean, power residual 18 %
    v = assess({"ripple_self_check_rel": 1.0160872900879139}, _payload(1.0160872900879139, -0.029204416392105654, 0.1803))
    assert v["status"] == "reject" and v["verified"] is False and v["reasons"]


def test_resaved_duty_is_flagged_not_rejected():
    v = assess({"ripple_self_check_rel": 0.073}, _payload(0.073, 0.002, t_avg=-0.104, pp=0.048))
    assert v["status"] == "warn" and v["verified"] is False


def test_good_run_is_verified():
    v = assess({"ripple_self_check_rel": 0.012}, _payload(0.012, 0.001, 0.02))
    assert v["status"] == "ok" and v["verified"] is True and not v["reasons"]


def test_missing_checks_are_unchecked_not_passed():
    v = assess({"T_avg_Nm": 1.0, "ripple_self_check_rel": None}, {"coulomb_torque": {}})
    assert v["verified"] is True and "ring_disagreement_rel_to_ripple_scale" in v["unchecked"]


def test_not_a_transient_is_not_judged():
    assert assess({"T_avg_Nm": 1.0}, None)["verified"] is None


def test_summary_alone_is_enough():
    assert assess({"ripple_self_check_rel": 0.9})["status"] == "reject"


def test_mean_torque_disagreement_alone_rejects():
    v = assess({"ripple_self_check_rel": 0.2}, _payload(0.2, 0.05, t_avg=-0.2, pp=0.1))
    assert v["status"] == "reject"


def _split(rel_legacy, dc, ac_rel, t_avg=-0.1013, pp=0.0479):
    p = _payload(rel_legacy, dc, t_avg=t_avg, pp=pp)
    p["coulomb_torque"]["layer_self_check"]["rel_ac_to_ripple_scale"] = ac_rel
    return p


def test_dc_offset_alone_at_1_and_2_rows_is_not_flagged():
    # 80 mm die, Astra audit: 1 row ring check 11.3 % (DC 0.00527, centred 0.82 %), 2 rows 7.1 % (DC 0.00334, 0.40 %)
    assert assess({"ripple_self_check_rel": 0.1135}, _split(0.1135, 0.00527, 0.0082))["status"] == "ok"
    assert assess({"ripple_self_check_rel": 0.0713}, _split(0.0713, 0.00334, 0.0040))["status"] == "ok"


def test_waveform_disagreement_still_flags_and_rejects():
    assert assess({"ripple_self_check_rel": 0.2}, _split(0.2, 0.001, 0.2))["status"] == "warn"
    assert assess({"ripple_self_check_rel": 0.9}, _split(0.9, 0.001, 0.9))["status"] == "reject"


def test_large_dc_offset_does_not_warn_but_a_gross_one_rejects():
    assert assess({"ripple_self_check_rel": 0.3}, _split(0.3, 0.015, 0.01))["status"] == "ok"
    assert assess({"ripple_self_check_rel": 0.3}, _split(0.3, 0.03, 0.01))["status"] == "reject"
