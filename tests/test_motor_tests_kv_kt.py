"""Tests section: Kv and Kt from the slope of the free-run EMF, the magnet-temperature read, the flagged row, the 3-D
provenance.

SYNTHETIC inputs only (no motor's bench data in the public repository).  The slopes of the table below are generic
V_LL-peak-per-1000-rpm figures; the expected constants are computed by hand from the definitions in
``compare.kv_ke_kt``:

    Kv      = 1000 / slope            (rpm per V of line-line PEAK; the only Kv - never an rms one)
    Ke      = slope / 1000 * (30 / pi) / sqrt(3) / sqrt(2)      (phase rms EMF per mechanical rad/s)
    Kt      = 3 * Ke                  (N m per line A rms)
"""
from __future__ import annotations

import math

import pytest

from motor_ai_sim.motor_tests import compare as C
from motor_ai_sim.motor_tests import comparison, model_source, models

# slope [V_LL peak / 1000 rpm] -> (Kv, Ke, Kt); the rows of the brief of 2026-11-04 in generic form
SLOPES = [
    (172.4, 5.80, 0.672, 2.016),            # a bench, all rows
    (174.4, 5.73, 0.680, 2.040),            # the same bench without its low row
    (155.4, 6.44, 0.606, 1.817),            # a 2-D model
    (155.4 * 0.9502, 6.77, 0.576, 1.727),   # that model x a 3-D flux factor
]


@pytest.mark.parametrize("slope,kv,ke,kt", SLOPES)
def test_constants_from_the_slope(slope, kv, ke, kt):
    k = C.kv_ke_kt(slope)
    assert k["kv"] == pytest.approx(kv, abs=0.006)
    assert not any("rms" in key for key in k)                         # one Kv only
    assert k["ke"] == pytest.approx(ke, abs=0.0006)
    assert k["kt"] == pytest.approx(kt, abs=0.0015)
    assert k["kt"] == pytest.approx(3.0 * k["ke"])                    # Kt = 3 Ke, exactly


def test_kt_is_the_apps_torque_constant_read_from_the_cards_kv():
    """The app's Kt = T / I_rms with T = 1.5 p psi i_q (peak) and its Kv = 60 / (2 pi p sqrt(3) psi): the constants this
    comparison forms from an EMF slope must be the same two numbers."""
    from motor_ai_sim.passport_v1.card import kv_rpm_per_V_line
    for psi, pp in ((0.0123, 7), (0.0456, 10), (0.002, 4)):
        kv = kv_rpm_per_V_line(psi, pp)
        k = C.kv_ke_kt(1000.0 / kv)                                  # the slope that Kv stands for
        assert k["kv"] == pytest.approx(kv)
        assert k["kt"] == pytest.approx(1.5 * pp * psi * math.sqrt(2.0))      # T / I_rms, gamma = 0


def test_the_slope_is_fitted_through_the_origin_and_one_point_is_not_a_slope():
    pts = [(n, 0.1724 * n) for n in (1000, 2000, 3036, 4000)]
    f = C.slope_through_origin(pts)
    assert f["slope"] * 1000 == pytest.approx(172.4) and f["rmse_pct"] < 1e-9 and f["n"] == 4
    assert C.slope_through_origin([(3000, 517.2)]) is None
    assert C.slope_through_origin([(0, 1.0), (3000, 517.2)]) is None       # a row at standstill is not a point
    # noisy rows: least squares, not a mean of ratios
    noisy = [(1000, 170.0), (2000, 350.0), (4000, 690.0)]
    expect = sum(n * v for n, v in noisy) / sum(n * n for n, _ in noisy)
    assert C.slope_through_origin(noisy)["slope"] == pytest.approx(expect)


def test_a_row_far_from_the_others_is_flagged_never_removed():
    pts = [(1000, 174.4), (2000, 348.8), (3036, 0.96 * 174.4 * 3.036), (4000, 697.6), (5000, 872.0)]
    out = C.flag_slope_outliers(pts)
    assert [o["flagged"] for o in out] == [False, False, True, False, False]
    assert out[2]["dev_pct"] == pytest.approx(-4.0)
    assert len(out) == len(pts)                                            # every row stays
    # three rows are too few for "the others" to mean anything
    assert not any(o["flagged"] for o in C.flag_slope_outliers(pts[:3]))
    # a clean sweep flags nothing
    assert not any(o["flagged"] for o in C.flag_slope_outliers([(n, 0.1744 * n) for n in range(1000, 6000, 1000)]))


# ── the magnet temperature a stored run was solved at ───────────────────────────────────────────

def test_summary_magnet_temperature_reads_the_runs_own_stamp_first():
    assert C.summary_magnet_temp_c({"magnet_temp_used_c": 50.0, "magnet_temp_c": 150.0}) == 50.0
    assert C.summary_magnet_temp_c({"magnet_temp_c": 80.0}) == 80.0
    assert C.summary_magnet_temp_c({"magnet_temp_C": 70.0}) == 70.0
    assert C.summary_magnet_temp_c({"magnet_temp_used_c": 0.0}) == 0.0     # 0 C is a temperature
    assert C.summary_magnet_temp_c({"magnet_temp_used_c": "n/a"}) is None
    assert C.summary_magnet_temp_c({}) is None and C.summary_magnet_temp_c(None) is None


LIB = {"magnet": {"GRADE_20C": {"grade": "GRADE", "temperature_c": 20, "Br": 1.40},
                  "GRADE_100C": {"grade": "GRADE", "temperature_c": 100, "Br": 1.30},
                  "GRADE_150C": {"grade": "GRADE", "temperature_c": 150, "Br": 1.20}}}
# Br(50) = 1.35 - 0.0125 * ... on the 20-100 C line: 1.40 - 0.1 * 30 / 80 = 1.3625; Br(65) = 1.40 - 0.1 * 45 / 80 = 1.34375
BR_50, BR_65, BR_150 = 1.3625, 1.34375, 1.20


def _cfg(noload_summary, *, star_delta="star"):
    return {"winding": {"star_delta": star_delta}, "materials": {"magnet": "GRADE_150C"}, "duties": [
        {"name": "no-load 3000", "rpm": 3000, "current_arms": 0, "summary": noload_summary}]}


def test_a_noload_point_solved_at_50_c_is_not_read_as_the_cards_150_c():
    s = {"rpm": 3000, "I_phase_rms_A": 0.0, "V_line_peak_V": 466.2, "V_line_rms_V": 466.2 / math.sqrt(2),
         "magnet_temp_used_c": 50.0, "magnet_temp_source": "typed", "magnet_card_temp_c": 150.0}
    m = model_source.from_docs({}, _cfg(s), {}, LIB)
    assert m["noload"][0]["magnet_temp_c"] == 50.0
    rows = [{"rpm": n, "v_ll_pp_v": 2 * 0.1554 * n * BR_65 / BR_50} for n in (1000, 2000, 3000, 4000)]
    rec = models.normalize({"kind": "free_run", "date": "2026-01-15", "conditions": {"magnet_temp_c": 65.0}, "rows": rows},
                           who="eng@example.test")
    g = {x["key"]: x for x in comparison.compare_record(rec, m, "D", "C")["groups"]}
    row = [r for r in g["emf_peak"]["rows"] if r["params"]["rpm"] == 3000][0]
    assert row["extra"]["br_ratio"] == pytest.approx(BR_65 / BR_50)       # 0.986, not Br(65) / Br(150) = 1.12
    assert row["model"] == pytest.approx(466.2 * BR_65 / BR_50) and row["delta_pct"] == pytest.approx(0.0, abs=1e-9)
    # the old spellings still count, and the card is the last resort
    s2 = {k: v for k, v in s.items() if k != "magnet_temp_used_c"}
    assert model_source.from_docs({}, _cfg({**s2, "magnet_temp_c": 80.0}), {}, LIB)["noload"][0]["magnet_temp_c"] == 80.0
    assert model_source.from_docs({}, _cfg(s2), {}, LIB)["noload"][0]["magnet_temp_c"] == 150.0


def test_compute_it_copies_the_reference_runs_magnet_temperature_from_the_same_stamp():
    from motor_ai_sim.motor_tests import compute
    ref = {"summary": {"magnet_temp_used_c": 50.0, "coil_temp_C": 80.0}}
    kw = compute._solve_kwargs({"name": "p", "rpm": 3000, "current_arms": 0.1, "gamma_deg": 0.0, "mode": "motor"},
                               {"simulation": {}, "winding": {}}, ref, {}, "run1")
    assert kw["magnet_temp_c"] == 50.0 and kw["coil_temp_c"] == 80.0


# ── the free-run comparison ─────────────────────────────────────────────────────────────────────

BENCH_SLOPE = 174.4          # V_LL peak per 1000 rpm, the rows without the low one
MODEL_SLOPE = 155.4          # the no-load duty, 65 C
BENCH_RPM = (1000, 2000, 3036, 4000, 5000, 6000)


def _noload(*, end3d=None, star_delta="star", temp=65.0, stored=None):
    s = {"rpm": 3000, "I_phase_rms_A": 0.0, "V_line_peak_V": MODEL_SLOPE * 3.0,
         "V_line_rms_V": MODEL_SLOPE * 3.0 / math.sqrt(2.0), "magnet_temp_used_c": temp}
    if stored:
        s["end3d"] = stored
    return model_source.from_docs({}, _cfg(s, star_delta=star_delta), {}, LIB, end3d=end3d)


def _bench(low_row=True, rms=True):
    rows = []
    for n in BENCH_RPM:
        k = 0.96 if (low_row and n == 3036) else 1.0
        row = {"rpm": n, "v_ll_pp_v": 2 * BENCH_SLOPE / 1000.0 * n * k}
        if rms:
            row["v_ll_rms_v"] = BENCH_SLOPE / 1000.0 * n * k / math.sqrt(2.0)
        rows.append(row)
    return models.normalize({"kind": "free_run", "date": "2026-01-15", "conditions": {"magnet_temp_c": 65.0}, "rows": rows},
                            who="eng@example.test")


def _groups(model, rec=None):
    out = comparison.compare_record(rec or _bench(), model, "D", "C")
    return {g["key"]: g for g in out["groups"]}


def _by_key(group):
    return {r["key"]: r for r in group["rows"]}


def _lsq(pts):
    return sum(n * v for n, v in pts) / sum(n * n for n, _ in pts)


def test_kv_kt_rows_bench_model_2d_and_3d_each_with_its_difference():
    g = _groups(_noload(end3d={"k_flux": 0.9502}))
    rows = _by_key(g["kv_kt"])
    assert list(rows) == ["kv_slope_2d", "kv_slope_3d", "kv_2d", "kv_3d", "kt_2d", "kt_3d"]       # one Kv, no rms Kv
    # all six bench rows, the low one included
    pts = [(n, (0.96 if n == 3036 else 1.0) * BENCH_SLOPE / 1000.0 * n) for n in BENCH_RPM]
    s_all = 1000 * _lsq(pts)
    b = C.kv_ke_kt(s_all)
    assert rows["kv_slope_2d"]["measured"] == pytest.approx(s_all)
    assert rows["kv_2d"]["measured"] == pytest.approx(b["kv"]) and rows["kt_2d"]["measured"] == pytest.approx(b["kt"])
    # model 2-D: the stored no-load duty at the bench magnet temperature (65 C = the duty's own, ratio 1)
    assert rows["kv_slope_2d"]["model"] == pytest.approx(MODEL_SLOPE)
    m2 = C.kv_ke_kt(MODEL_SLOPE)
    assert rows["kv_2d"]["model"] == pytest.approx(m2["kv"]) and rows["kt_2d"]["model"] == pytest.approx(m2["kt"])
    assert rows["kv_2d"]["delta_pct"] == pytest.approx(100 * (m2["kv"] / b["kv"] - 1))
    # model 3-D: the flux factor on the EMF slope, Kv divides by it, Kt multiplies (k_T absent -> the flux factor)
    assert rows["kv_slope_3d"]["model"] == pytest.approx(MODEL_SLOPE * 0.9502)
    assert rows["kv_3d"]["model"] == pytest.approx(m2["kv"] / 0.9502)
    assert rows["kt_3d"]["model"] == pytest.approx(m2["kt"] * 0.9502)
    assert rows["kt_3d"]["delta_pct"] == pytest.approx(100 * (m2["kt"] * 0.9502 / b["kt"] - 1))
    assert rows["kt_2d"]["unit"] == "N·m/A" and rows["kv_2d"]["unit"] == "rpm/V"
    assert rows["kt_3d"]["tip"] == "kv_kt" and g["kv_kt"]["tip"] == "kv_kt"
    assert "kv_kt" in __import__("motor_ai_sim.motor_tests.compare", fromlist=["TIPS"]).TIPS


def test_the_low_bench_row_is_flagged_on_its_emf_row_and_the_slope_is_reported_both_ways():
    g = _groups(_noload(end3d={"k_flux": 0.9502}))
    # the EMF row says it is in the slope and how far it sits from the others
    emf = {r["params"]["rpm"]: r for r in g["emf_peak"]["rows"]}
    assert emf[3036]["extra"]["slope_flagged"] is True and emf[3036]["extra"]["in_slope"] is True
    assert emf[3036]["extra"]["slope_dev_pct"] == pytest.approx(-4.0)
    assert "flagged" in emf[3036]["detail"]
    assert all(emf[n]["extra"]["slope_flagged"] is False and emf[n]["extra"]["in_slope"] for n in emf if n != 3036)
    # off by default: group 1 has ALL six rows; group 2 repeats the fit without the flagged one
    all_rows, ex = _by_key(g["kv_kt"]), _by_key(g["kv_kt_ex"])
    assert all_rows["kv_2d"]["extra"]["n_rows"] == 6 and all_rows["kv_2d"]["extra"]["excluded_rpm"] == []
    assert ex["kv_2d"]["extra"]["n_rows"] == 5 and ex["kv_2d"]["extra"]["excluded_rpm"] == [3036.0]
    assert "3036" in g["kv_kt_ex"]["label"] and g["kv_kt_ex"]["params"] == {"rpm": "3036"}
    assert ex["kv_slope_2d"]["measured"] == pytest.approx(BENCH_SLOPE) and ex["kt_2d"]["measured"] == pytest.approx(C.kv_ke_kt(BENCH_SLOPE)["kt"])
    assert all_rows["kv_slope_2d"]["measured"] < BENCH_SLOPE                       # the low row pulls the slope down
    # the model is fitted over the same rows (here it is exactly proportional to speed)
    assert all_rows["kv_slope_2d"]["model"] == pytest.approx(MODEL_SLOPE) == pytest.approx(ex["kv_slope_2d"]["model"])
    assert "Kv" in g["kv_kt"]["label"] and g["kv_kt"]["basis_key"] == "kv_3d" and g["kv_kt_ex"]["basis_key"] == "kv_ex_3d"


def test_a_clean_sweep_gets_one_group_and_no_flag():
    g = _groups(_noload(), _bench(low_row=False))
    assert "kv_kt_ex" not in g and "kv_kt" in g
    assert not any(r["extra"]["slope_flagged"] for r in g["emf_peak"]["rows"])
    assert _by_key(g["kv_kt"])["kv_slope_2d"]["measured"] == pytest.approx(BENCH_SLOPE)


def test_without_a_passport_only_the_2d_rows_and_they_say_so():
    g = _groups(_noload())
    assert list(_by_key(g["kv_kt"])) == ["kv_slope_2d", "kv_2d", "kt_2d"]
    assert g["kv_kt"]["basis_key"] == "kv_2d" and g["kv_kt"]["basis"].endswith("2-D, no 3-D passport")


def test_the_measured_torque_factor_goes_on_kt_and_is_named():
    g = _groups(_noload(end3d={"k_flux": 0.95, "k_T": 0.98}))
    rows = _by_key(g["kv_kt"])
    assert rows["kt_3d"]["model"] == pytest.approx(C.kv_ke_kt(MODEL_SLOPE)["kt"] * 0.98)
    assert "measured k_T" in rows["kt_3d"]["detail"]
    assert rows["kv_3d"]["model"] == pytest.approx(C.kv_ke_kt(MODEL_SLOPE)["kv"] / 0.95)
    assert rows["kt_3d"]["extra"]["end3d"]["k_torque"] == pytest.approx(0.98)


def test_the_3d_factor_provenance_is_carried_inherited_stored_or_current():
    cur = _by_key(_groups(_noload(end3d={"k_flux": 0.9502, "adopted_utc": "2026-10-02T10:00:00Z"}))["kv_kt"])["kv_3d"]
    p = cur["extra"]["end3d"]
    assert p["inherited"] is False and p["stale"] is False and p["current"] is True and p["date"] == "2026-10-02"
    inh = _by_key(_groups(_noload(end3d={"k_flux": 0.9502, "inherited": True,
                                          "inherited_note": "coefficient of an EARLIER geometry"}))["kv_kt"])["kv_3d"]
    assert inh["extra"]["end3d"]["inherited"] is True and inh["extra"]["end3d"]["stale"] is True
    assert "earlier geometry" in inh["detail"] and inh["extra"]["end3d"]["inherited_note"].startswith("coefficient")
    stored = _by_key(_groups(_noload(stored={"k_flux": 0.9502}))["kv_kt"])["kv_3d"]
    assert stored["extra"]["end3d"]["current"] is False and stored["extra"]["end3d"]["stale"] is True
    assert "stored with the duty" in stored["detail"]
    # nothing is recomputed: the factor is the one given
    assert inh["extra"]["k_3d"] == 0.9502


def test_delta_connection_names_the_per_winding_factor():
    star = _by_key(_groups(_noload())["kv_kt"])["kt_2d"]["extra"]
    delta = _by_key(_groups(_noload(star_delta="delta"))["kv_kt"])["kt_2d"]["extra"]
    assert star["connection"] == "star" and star["winding_a_factor"] == 1.0
    assert delta["connection"] == "delta" and delta["winding_a_factor"] == pytest.approx(math.sqrt(3.0))


def test_the_bench_constants_stand_without_a_model_point():
    m = model_source.from_docs({}, {"winding": {"star_delta": "star"}, "duties": []}, {}, LIB)
    g = _groups(m)
    rows = _by_key(g["kv_kt"])
    assert rows["kv_2d"]["measured"] is not None and rows["kv_2d"]["model"] is None
    assert rows["kv_2d"]["status"] == "no_model" and rows["kt_2d"]["delta_pct"] is None


def test_one_row_or_no_voltage_gives_no_kv_group():
    one = models.normalize({"kind": "free_run", "date": "2026-01-15", "rows": [{"rpm": 3000, "v_ll_pp_v": 900.0}]},
                           who="eng@example.test")
    assert "kv_kt" not in _groups(_noload(), one)
    power_only = models.normalize({"kind": "free_run", "date": "2026-01-15",
                                   "rows": [{"rpm": n, "shaft_power_w": 10.0 * n} for n in (1000, 2000, 3000)]},
                                  who="eng@example.test")
    assert "kv_kt" not in _groups(_noload(), power_only)


def test_kv_is_formed_from_the_peak_even_when_only_rms_was_read():
    rec = models.normalize({"kind": "free_run", "date": "2026-01-15", "conditions": {"magnet_temp_c": 65.0},
                            "rows": [{"rpm": n, "v_ll_rms_v": BENCH_SLOPE / 1000.0 * n / math.sqrt(2.0)}
                                     for n in (1000, 2000, 3000, 4000)]}, who="eng@example.test")
    r = _by_key(_groups(_noload(), rec)["kv_kt"])["kv_2d"]
    assert r["measured"] == pytest.approx(C.kv_ke_kt(BENCH_SLOPE)["kv"])        # rpm per V of PEAK, not of rms
    assert all("rms" not in k for k in _by_key(_groups(_noload(), rec)["kv_kt"]))


def test_the_report_rows_carry_the_new_groups_without_changes():
    from motor_ai_sim.motor_tests import report as R
    rec = _bench()
    cmp_ = comparison.compare_record(rec, _noload(end3d={"k_flux": 0.9502}), "D", "C")
    labels = [r["label"] for g in cmp_["groups"] for r in g["rows"] if g["key"].startswith("kv_kt")]
    assert "Kv (rpm per V line-line peak), model 3-D" in labels and "Kt per A rms phase current, model 2-D" in labels
    assert not any("rms" in l and "Kv" in l for l in labels)
    assert R._fmt(5.8) == "5.80"


# ── the Bearings tab: model values, the measured total on its own ───────────────────────────────

def _bearing_model(cards=("6010-2RZ/HC5C3", "618/8-2Z")):
    from motor_ai_sim.motor_tests import model_source as MS
    cfg = {"bearings": {"A": {"card": cards[0]}, "B": {"card": cards[1]}}}
    m = _noload()
    m["mech"] = MS.mech_function(cfg, {}, 1.5)
    m["has_bearings"] = True
    return m


def test_bearing_groups_are_the_models_values_per_end_and_the_measured_total_stands_alone():
    from motor_ai_sim import bearings as B
    rows = [{"rpm": n, "shaft_power_w": 100.0 + 0.1 * n, "v_ll_pp_v": 2 * BENCH_SLOPE / 1000.0 * n} for n in (1000, 3000, 5000)]
    rec = models.normalize({"kind": "free_run", "date": "2026-01-15", "conditions": {"ambient_c": 25.0}, "rows": rows},
                           who="eng@example.test")
    g = _groups(_bearing_model(), rec)
    assert {"bearing_a", "bearing_b", "bearing_sum", "bearing_measured", "bearing_share"} <= set(g)
    a, b_ = _by_key_rows(g["bearing_a"], 3000), _by_key_rows(g["bearing_b"], 3000)
    # both ends come straight from machine_bearing_losses, each with its own card
    res = B.machine_bearing_losses({"A": {"card": "6010-2RZ/HC5C3"}, "B": {"card": "618/8-2Z"}}, rpm=3000, temp_c=25.0,
                                   rotor_mass_kg=1.5)
    assert a["model"] == pytest.approx(res["bearings"][0]["P_W"]) and b_["model"] == pytest.approx(res["bearings"][1]["P_W"])
    assert g["bearing_a"]["params"]["card"] == "6010-2RZ/HC5C3" and g["bearing_b"]["params"]["card"] == "618/8-2Z"
    assert "model" in g["bearing_a"]["label"] and g["bearing_a"]["basis_key"] == "bearings_model"
    assert _by_key_rows(g["bearing_sum"], 3000)["model"] == pytest.approx(res["P_bearings_W"])
    # model rows carry no measurement and no delta; the measured total carries no model and no delta
    for grp in ("bearing_a", "bearing_b", "bearing_sum", "bearing_share"):
        assert all(r["measured"] is None and r["delta_pct"] is None for r in g[grp]["rows"])
    assert all(r["model"] is None and r["delta_pct"] is None for r in g["bearing_measured"]["rows"])
    assert g["bearing_measured"]["basis_key"] == "bearings_measured" and g["bearing_share"]["basis_key"] == "bearings_share"


def _by_key_rows(group, rpm):
    return [r for r in group["rows"] if r["params"]["rpm"] == rpm][0]


def test_bearing_share_is_the_model_over_the_measured_total_and_needs_that_total():
    rows = [{"rpm": 3000, "shaft_power_w": 600.0, "v_ll_pp_v": 900.0}, {"rpm": 4000, "v_ll_pp_v": 1200.0}]
    rec = models.normalize({"kind": "free_run", "date": "2026-01-15", "conditions": {"ambient_c": 25.0}, "rows": rows},
                           who="eng@example.test")
    g = _groups(_bearing_model(), rec)
    pb = _by_key_rows(g["bearing_sum"], 3000)["model"]
    assert _by_key_rows(g["bearing_share"], 3000)["model"] == pytest.approx(100.0 * pb / 600.0)
    assert [r["params"]["rpm"] for r in g["bearing_share"]["rows"]] == [3000]          # no measured total at 4000: no share
    assert [r["params"]["rpm"] for r in g["bearing_measured"]["rows"]] == [3000]
    assert len(g["bearing_sum"]["rows"]) == 2


def test_no_bearing_assignment_gives_the_empty_state_and_no_number():
    g = _groups(_noload(), _bench(low_row=False))
    assert "bearing_a" not in g and "bearing_sum" not in g and "bearing_share" not in g
    row = g["bearings_none"]["rows"][0]
    assert row["key"] == "no_bearings" and row["model"] is None and row["status"] == "no_model"


def test_every_group_a_free_run_test_emits_has_a_tab():
    """The web maps a group key to a tab (``tableModel.tabOfGroup``); an unknown key falls back to the Free-run tab so it
    cannot vanish.  This list is what the server emits today: a NEW group key fails here, which is the prompt to decide its
    tab (and to add it to the mapping test in ``tableModel.test.mjs``) instead of letting it land in Free-run by accident."""
    rows = [{"rpm": n, "shaft_power_w": 100.0 + 0.1 * n, "v_ll_pp_v": 2 * BENCH_SLOPE / 1000.0 * n,
             "v_ll_rms_v": BENCH_SLOPE / 1000.0 * n / math.sqrt(2.0)} for n in (1000, 2000, 3000, 4000, 5000)]
    rec = models.normalize({"kind": "free_run", "date": "2026-01-15", "conditions": {"ambient_c": 25.0}, "rows": rows},
                           who="eng@example.test")
    emitted = set(_groups(_bearing_model(), rec)) | set(_groups(_noload(), rec)) | set(_groups(_bearing_model(), _bench()))
    empty = set(_groups(model_source.from_docs({}, {"duties": []}, {}, LIB), rec))
    known = {"emf_peak", "emf_rms", "kv_kt", "kv_kt_ex", "loss", "fit", "no_model",
             "bearing_a", "bearing_b", "bearing_sum", "bearing_measured", "bearing_share", "bearings_none"}
    assert (emitted | empty) <= known, (emitted | empty) - known
