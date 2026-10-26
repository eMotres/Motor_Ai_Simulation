"""Tests section: every conversion between a bench measurement and the model value of the same quantity.

The numbers are the bench data of a 200 mm stator (2026-10-18 / 10-21 / 10-26) and the expectations the owner checked by
hand: fitted Ld ~ 42.8 uH, Lq ~ 61.6 uH; R model 16.62 + 0.67 (leads) = 17.29 vs 18.03 mOhm (-4.1 %).
"""
from __future__ import annotations

import math

import pytest

from motor_ai_sim.motor_tests import compare as C
from motor_ai_sim.motor_tests import comparison, model_source, models

R_PAIRS_MOHM = (18.13, 18.03, 17.94)            # stator without rotor, 28.1 C
L_PAIRS_UH = (113.1, 85.6, 114.4)               # assembled, 1-2 / 3-2 / 1-3
LEADS_7AWG = {"count": 3, "length_mm": 200, "awg": 7}
FREE_RUN = [(613, 75), (812, 111), (1031, 155), (1204, 194), (1426, 251), (1620, 309), (1871, 364), (2039, 426),
            (2201, 482), (2434, 577), (2579, 617), (2808, 690), (3021, 784), (3211, 840), (3448, 891), (3570, 975),
            (3819, 1072), (4238, 1286)]
#: a model R_phase whose star line-to-line value at 28.1 C is 16.62 mOhm
R_PHASE_20C = 8.31e-3 / ((234.5 + 28.1) / (234.5 + 20.0))


# ── resistance ──────────────────────────────────────────────────────────────────────────────

def test_copper_temperature_factor_is_234_5_plus_t():
    assert C.cu_temp_factor(20.0, 20.0) == 1.0
    assert C.cu_temp_factor(120.0, 20.0) == pytest.approx((234.5 + 120) / 254.5)
    # alpha = 0.00393 1/K about 20 C
    assert (C.cu_temp_factor(21.0, 20.0) - 1.0) == pytest.approx(0.00393, rel=1e-3)


def test_lead_cables_two_7awg_200mm_add_0_67_mohm_at_28c():
    assert C.awg_area_mm2(7) == pytest.approx(10.55, abs=0.01)
    one = C.lead_resistance_ohm(200, awg=7, temp_c=28.1)
    assert 2 * one * 1000 == pytest.approx(0.67, abs=0.005)
    assert C.lead_resistance_ohm(200, area_mm2=10.55, temp_c=28.1) == pytest.approx(one, rel=1e-3)
    assert C.lead_resistance_ohm(200) is None                    # no section -> no number, never zero


def test_resistance_model_16_62_plus_leads_is_17_29_vs_18_03_minus_4_1_pct():
    res = C.resistance_compare([v / 1000 for v in R_PAIRS_MOHM], 28.1, r_phase_ohm=R_PHASE_20C, coil_temp_c=20.0,
                               connection="star", leads=LEADS_7AWG)
    assert res["winding_ohm"] * 1000 == pytest.approx(16.62, abs=0.005)
    assert res["leads_ohm"] * 1000 == pytest.approx(0.67, abs=0.005)
    assert res["model_ohm"] * 1000 == pytest.approx(17.29, abs=0.01)
    assert res["measured_ohm"] * 1000 == pytest.approx(18.03, abs=0.005)
    assert res["delta_pct"] == pytest.approx(-4.1, abs=0.05)
    assert res["remaining_ohm"] * 1000 == pytest.approx(0.74, abs=0.01)
    assert "phase board" in res["not_modelled"]


def test_resistance_delta_connection_is_two_thirds_of_the_phase():
    star = C.resistance_compare([1.0], 20.0, r_phase_ohm=1.0, coil_temp_c=20.0, connection="star")
    delta = C.resistance_compare([1.0], 20.0, r_phase_ohm=1.0, coil_temp_c=20.0, connection="delta")
    assert star["model_ohm"] == pytest.approx(2.0)
    assert delta["model_ohm"] == pytest.approx(2.0 / 3.0)


# ── inductance ──────────────────────────────────────────────────────────────────────────────

def test_ld_lq_fit_from_three_pairs():
    fit = C.ldq_fit_from_pairs(L_PAIRS_UH)
    assert fit["sum"] == pytest.approx(104.37, abs=0.01)          # Ld + Lq
    assert fit["amp"] == pytest.approx(18.78, abs=0.01)           # Lq - Ld
    assert fit["ld"] == pytest.approx(42.8, abs=0.05)
    assert fit["lq"] == pytest.approx(61.6, abs=0.05)
    assert C.ldq_fit_from_pairs([1, 2]) is None


def test_ld_lq_fit_recovers_a_synthetic_machine_at_any_rotor_angle():
    ld, lq = 40.0, 65.0
    for phi in (0.0, 0.3, 1.1, 2.0):
        pairs = [(ld + lq) - (lq - ld) * math.cos(2 * (k * 2 * math.pi / 3) + phi) for k in range(3)]
        fit = C.ldq_fit_from_pairs(pairs)
        assert fit["ld"] == pytest.approx(ld, abs=1e-9) and fit["lq"] == pytest.approx(lq, abs=1e-9)


def test_model_ldq_line_uses_the_star_equivalent_in_delta():
    assert C.model_ldq_line(0.03, 0.06, "star") == (0.03, 0.06)
    assert C.model_ldq_line(0.03, 0.06, "delta") == pytest.approx((0.01, 0.02))


# ── EMF and Br ──────────────────────────────────────────────────────────────────────────────

CARDS = [(20.0, 1.43), (100.0, 1.2927), (150.0, 1.2069)]


def test_br_interpolates_between_cards_and_extrapolates_with_the_end_slope():
    assert C.br_at(CARDS, 20.0) == pytest.approx(1.43)
    assert C.br_at(CARDS, 60.0) == pytest.approx(1.43 + (1.2927 - 1.43) * 0.5)
    assert C.br_at(CARDS, 125.0) == pytest.approx(1.2927 + (1.2069 - 1.2927) * 0.5)
    assert C.br_at(CARDS, 0.0) == pytest.approx(1.43 - (1.2927 - 1.43) / 80 * 20)
    assert C.br_at([(150.0, 1.2)], 20.0) == 1.2                    # one card: no temperature information
    assert C.br_ratio(CARDS, None, 150.0) == 1.0


def test_emf_scales_with_speed_and_br():
    ref = {"rpm": 1000.0, "v_ll_peak": 100.0, "v_ll_rms": 70.0}
    ratio = C.br_ratio(CARDS, 60.0, 150.0)
    assert ratio == pytest.approx(1.36135 / 1.2069, rel=1e-4)
    emf = C.emf_model_at(2000.0, ref, ratio)
    assert emf["peak"] == pytest.approx(200.0 * ratio)
    assert emf["rms"] == pytest.approx(140.0 * ratio)


# ── no-load loss ────────────────────────────────────────────────────────────────────────────

def test_noload_loss_scales_each_term_by_its_own_speed_law():
    ref = {"rpm": 1000.0, "hyst": 10.0, "eddy": 5.0, "excess": 2.0, "p_solid": 1.0}
    out = C.noload_em_loss_at(2000.0, ref)
    assert out["core"] == pytest.approx(10 * 2 + 5 * 4 + 2 * 2 ** 1.5)
    assert out["solid"] == pytest.approx(4.0)
    hot = C.noload_em_loss_at(1000.0, ref, ratio=1.1)
    assert hot["em"] == pytest.approx((17.0 + 1.0) * 1.21)        # loss ~ B^2
    assert C.noload_em_loss_at(2000.0, {"rpm": 1000.0, "p_core": 8.0})["core"] == pytest.approx(8 * 2 ** 1.5)


def test_loss_fit_a_n_plus_b_n2():
    exact = [(n, 50.0 * n / 1000 + 30.0 * (n / 1000) ** 2) for n in (500, 1000, 2000, 3000, 4000)]
    fit = C.fit_an_bn2(exact)
    assert fit["a"] == pytest.approx(50.0) and fit["b"] == pytest.approx(30.0) and fit["rmse"] < 1e-9
    bench = C.fit_an_bn2(FREE_RUN)
    assert bench["a"] == pytest.approx(121.7, abs=0.5)            # W/krpm
    assert bench["b"] == pytest.approx(42.7, abs=0.5)             # W/krpm^2
    assert bench["rmse"] < 20.0
    assert C.fit_an_bn2([(1000, 1.0)]) is None


# ── loads ───────────────────────────────────────────────────────────────────────────────────

def test_a_load_point_matches_a_duty_only_within_2_pct_speed_and_3_pct_current():
    duties = [{"name": "a", "rpm": 3000.0, "current": 100.0}, {"name": "b", "rpm": 4000.0, "current": 100.0}]
    assert C.match_duty(3050, 102, duties)["name"] == "a"
    assert C.match_duty(3070, 100, duties) is None                # 2.3 % speed
    assert C.match_duty(3000, 104, duties) is None                # 4 % current
    assert C.match_duty(None, 100, duties) is None


# ── one record against a model built from documents ───────────────────────────────────────

def _cfg_doc():
    return {"winding": {"star_delta": "star"}, "materials": {"magnet": "N52UH_150C"}, "duties": [
        {"name": "rated", "rpm": 3000, "current_arms": 100, "summary": {
            "rpm": 3000, "I_phase_rms_A": 100, "R_phase_ohm": R_PHASE_20C, "coil_temp_C": 20.0,
            "Ld_mH": 0.045, "Lq_mH": 0.060, "T_em_avg_Nm": 30.0, "efficiency": 0.95, "mass_total_kg": 9.2,
            "V_line_peak_V": 400.0}},
        {"name": "no-load 3000", "rpm": 3000, "current_arms": 0, "summary": {
            "rpm": 3000, "I_phase_rms_A": 0.0, "V_line_peak_V": 360.0, "V_line_rms_V": 250.0,
            "P_core_terms": {"stator": {"hysteresis_W": 200.0, "eddy_W": 100.0, "excess_W": 50.0},
                             "rotor": {"hysteresis_W": 10.0, "eddy_W": 5.0, "excess_W": 2.0}},
            "P_core_W": 367.0, "P_solid_W": 40.0, "P_mag_W": 30.0}},
    ]}


LIB = {"magnet": {"N52UH_20C": {"grade": "N52UH", "temperature_c": 20, "Br": 1.43},
                  "N52UH_100C": {"grade": "N52UH", "temperature_c": 100, "Br": 1.2927},
                  "N52UH_150C": {"grade": "N52UH", "temperature_c": 150, "Br": 1.2069}}}


def _model(mech=None):
    return model_source.from_docs({}, _cfg_doc(), {}, LIB, mech=mech)


def test_model_source_reads_r_ldq_noload_and_the_magnet_grade():
    m = _model()
    assert m["r_ref"]["duty"] == "rated" and m["r_ref"]["coil_temp_c"] == 20.0
    assert m["l_ref"]["duty"] == "rated"                         # the no-load duty stores no Ld/Lq here
    assert [r["duty"] for r in m["noload"]] == ["no-load 3000"]
    nl = m["noload"][0]
    assert nl["hyst"] == 210.0 and nl["eddy"] == 105.0 and nl["excess"] == 52.0 and nl["p_solid"] == 40.0
    assert m["magnet"]["grade"] == "N52UH" and len(m["magnet"]["points"]) == 3
    assert nl["magnet_temp_c"] == 150.0                          # the card's own temperature
    assert m["mass_kg"] == 9.2


def _record(kind, **kw):
    base = {"kind": kind, "date": "2026-10-18"}
    base.update(kw)
    return models.normalize(base, who="eng@example.test")


def test_parameters_record_resistance_and_inductance_rows():
    rec = _record("parameters", conditions={"rotor": "in"}, leads=LEADS_7AWG,
                  resistance={"temp_c": 28.1, "pairs": [{"pair": p, "value_mohm": v}
                                                        for p, v in zip(("1-2", "2-3", "1-3"), R_PAIRS_MOHM)]},
                  inductance={"freq_hz": 1000, "pairs": [{"pair": p, "value_uh": v}
                                                         for p, v in zip(("1-2", "3-2", "1-3"), L_PAIRS_UH)]},
                  weight=[{"what": "motor as photographed", "mass_g": 9400}])
    out = comparison.compare_record(rec, _model(), "D", "C")
    groups = {g["key"]: g for g in out["groups"]}
    r = groups["resistance"]["rows"][0]
    assert r["key"] == "r_ll_mean" and r["model"] == pytest.approx(17.29, abs=0.01)
    assert r["delta_pct"] == pytest.approx(-4.1, abs=0.05) and r["extra"]["leads_mohm"] == pytest.approx(0.67, abs=0.005)
    lrows = {x["key"]: x for x in groups["inductance"]["rows"]}
    assert lrows["l_sum"]["model"] == pytest.approx(105.0) and lrows["l_amp"]["model"] == pytest.approx(15.0)
    assert lrows["l_d"]["measured"] == pytest.approx(42.8, abs=0.05) and lrows["l_q"]["measured"] == pytest.approx(61.6, abs=0.05)
    assert lrows["l_sum"]["extra"]["high_freq"] is True          # 1 kHz vs a low-frequency model: flagged
    w = groups["mass"]["rows"][0]
    assert w["measured"] == 9400 and w["model"] == pytest.approx(9200.0)


def test_rotor_out_inductance_without_a_stator_only_run_is_no_model_point():
    rec = _record("parameters", conditions={"rotor": "out"},
                  inductance={"pairs": [{"pair": "1-2", "value_uh": 140.7}, {"pair": "2-3", "value_uh": 141.4},
                                        {"pair": "1-3", "value_uh": 141.4}]})
    row = comparison.compare_record(rec, _model(), "D", "C")["groups"][0]["rows"][0]
    assert row["key"] == "l_mean_rotor_out" and row["model"] is None and row["status"] == "no_model"


def test_free_run_emf_and_loss_with_bearings_windage_and_unexplained_remainder():
    def mech(rpm, temp):
        return {"bearings_w": 0.01 * rpm, "windage_w": 1e-6 * rpm ** 2}
    mech.has_bearings = True
    rec = _record("free_run", conditions={"ambient_c": 25.0, "magnet_temp_c": 60.0},
                  rows=[{"rpm": 1500, "shaft_power_w": 300, "v_ll_pp_v": 400, "v_ll_rms_v": 140},
                        {"rpm": 3000, "shaft_power_w": 900, "v_ll_pp_v": 800, "v_ll_rms_v": 280}])
    out = comparison.compare_record(rec, _model(mech), "D", "C")
    g = {x["key"]: x for x in out["groups"]}
    ratio = C.br_ratio(LIB_POINTS, 60.0, 150.0)
    e = g["emf_peak"]["rows"][1]
    assert e["measured"] == 400.0 and e["model"] == pytest.approx(360.0 * ratio)
    loss = g["loss"]["rows"][1]
    em = (210 + 105 + 52 + 40) * ratio ** 2
    assert loss["extra"]["em_w"] == pytest.approx(em)
    assert loss["model"] == pytest.approx(em + 30.0 + 9.0)
    assert loss["extra"]["unexplained_w"] == pytest.approx(900 - (em + 39.0))
    assert out["fit"]["measured"] is not None and out["fit"]["model"] is not None


LIB_POINTS = [(20.0, 1.43), (100.0, 1.2927), (150.0, 1.2069)]


def test_free_run_without_a_noload_duty_offers_to_create_one():
    doc = _cfg_doc()
    doc["duties"] = doc["duties"][:1]
    m = model_source.from_docs({}, doc, {}, LIB)
    rec = _record("free_run", rows=[{"rpm": 2000, "v_ll_pp_v": 500}, {"rpm": 5074, "v_ll_pp_v": 616}])
    out = comparison.compare_record(rec, m, "D", "C")
    first = out["groups"][0]["rows"][0]
    assert first["status"] == "no_model"
    body = first["action"]["body"]
    assert body["die"] == "D" and body["duty"]["rpm"] == 5074 and body["duty"]["current_arms"] == 0


def test_load_rows_match_a_duty_or_offer_to_create_it():
    rec = _record("load", rows=[{"rpm": 3030, "i_phase_a": 101, "torque_nm": 29.0, "eff_pct": 94.0},
                                {"rpm": 1000, "i_phase_a": 50, "torque_nm": 10.0}])
    out = comparison.compare_record(rec, _model(), "D", "C")
    hit, miss = out["groups"]
    t = hit["rows"][0]
    assert t["key"] == "torque" and t["model"] == 30.0 and t["delta_pct"] == pytest.approx((30 - 29) / 29 * 100)
    assert hit["rows"][1]["model"] == pytest.approx(95.0)
    assert miss["rows"][0]["status"] == "no_model"
    assert miss["rows"][0]["action"]["body"]["duty"]["rpm"] == 1000.0


# ── validation is loud ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("body, msg", [
    ({"kind": "parameters", "date": "2026-13-01", "weight": [{"mass_g": 1}]}, "date"),
    ({"kind": "parameters", "date": "2026-10-18"}, "at least one"),
    ({"kind": "free_run", "date": "2026-10-18", "rows": [{"rpm": "abc"}]}, "not a number"),
    ({"kind": "free_run", "date": "2026-10-18", "rows": [{"rpm": -5}]}, "outside"),
    ({"kind": "load", "date": "2026-10-18", "rows": []}, "at least one row"),
    ({"kind": "x", "date": "2026-10-18"}, "kind"),
    ({"kind": "parameters", "date": "2026-10-18", "resistance": {"pairs": [{"pair": "1 2 3", "value_mohm": 1}]}},
     "terminal pair"),
    ({"kind": "parameters", "date": "2026-10-18", "weight": [{"mass_g": 1}], "leads": {"length_mm": 200}}, "AWG"),
])
def test_bad_input_is_refused_with_the_field_named(body, msg):
    with pytest.raises(models.TestInputError, match=msg):
        models.normalize(body, who="x@example.test")
