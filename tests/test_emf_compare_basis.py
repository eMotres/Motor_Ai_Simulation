"""Free-run EMF comparison: the magnet-temperature basis and the voltage basis (EMF audit 2026-11-07).

Temperature, in order of preference (``compare.emf_temperature_basis``):
(a) a stored no-load point within 10 K of the bench magnet temperature is compared directly (speed scaling only);
(b) else the EMF-to-Br elasticity fitted from this configuration's own stored no-load points at different temperatures;
(c) else the old Br scaling (elasticity 1), labelled "temperature-scaled (approximate)".
The rows and groups say which one was used.

Voltage basis: the EMF-slope row compares the simulation on the bench's own basis (fundamental, rms x sqrt(2), waveform
peak); Kv and Kt stay on the fundamental and say when the bench Kv is not.

SYNTHETIC numbers only.
"""
from __future__ import annotations

import math

import pytest

from motor_ai_sim import kv_kt as K
from motor_ai_sim.motor_tests import compare as C
from motor_ai_sim.motor_tests import comparison, model_source, models

#: a linear Br(T) card set: Br(20) = 1.40, Br(150) = 1.24
BR_PTS = [(20.0, 1.40), (150.0, 1.24)]
E_ELASTICITY = 0.2          # how this synthetic machine's EMF follows Br


def _br(t):
    return C.br_at(BR_PTS, t)


def _ref(duty, rpm, temp, e_per_rpm, fund=True):
    return {"duty": duty, "rpm": rpm, "magnet_temp_c": temp, "v_ll_peak": 1.03 * e_per_rpm * rpm,
            "v_ll_rms": e_per_rpm * rpm / math.sqrt(2.0), "v_ll_fund_peak": e_per_rpm * rpm if fund else None}


def _machine_e(temp):
    """V_LL fundamental per rpm of the synthetic machine at a magnet temperature: E ~ Br ** 0.2."""
    return 0.17 * (_br(temp) / _br(50.0)) ** E_ELASTICITY


# ── compare.emf_temperature_basis, each branch ────────────────────────────────────────────────

def test_a_a_stored_point_within_10_k_is_compared_directly():
    refs = [_ref("nl 50", 3000, 50.0, _machine_e(50.0)), _ref("nl 120", 3000, 120.0, _machine_e(120.0))]
    tb = C.emf_temperature_basis(3000, refs, BR_PTS, 58.0)
    assert tb["basis"] == C.TEMP_STORED_POINT and tb["factor"] == 1.0
    assert tb["ref"]["duty"] == "nl 50" and tb["model_temp_c"] == 50.0
    # 12 K away is not "close": a 62 C bench is not compared directly with the 50 C point
    assert C.emf_temperature_basis(3000, refs, BR_PTS, 62.0)["basis"] == C.TEMP_FITTED
    # among close points the nearest temperature wins over the nearest speed
    refs2 = [_ref("nl 50 @2000", 2000, 50.0, 0.17), _ref("nl 45 @3000", 3000, 45.0, 0.17)]
    assert C.emf_temperature_basis(3000, refs2, BR_PTS, 51.0)["ref"]["duty"] == "nl 50 @2000"


def test_b_the_elasticity_is_fitted_from_the_configurations_own_points():
    refs = [_ref("nl 50", 3000, 50.0, _machine_e(50.0)), _ref("nl 120", 2000, 120.0, _machine_e(120.0))]
    tb = C.emf_temperature_basis(3000, refs, BR_PTS, 80.0)
    assert tb["basis"] == C.TEMP_FITTED
    assert tb["elasticity"] == pytest.approx(E_ELASTICITY, abs=1e-9)
    assert tb["ref"]["duty"] == "nl 50"                                              # the point nearest in speed
    assert tb["factor"] == pytest.approx((_br(80.0) / _br(50.0)) ** E_ELASTICITY)
    emf = C.emf_model_at(3000, tb["ref"], tb["factor"])
    assert emf["fund"] == pytest.approx(_machine_e(80.0) * 3000)                    # the machine's own EMF at 80 C
    assert tb["fit"]["n"] == 2 and tb["fit"]["span_k"] == pytest.approx(70.0)


def test_b_an_unphysical_fit_is_refused_and_falls_back_to_c():
    # EMF falling as Br rises: a broken pair of stored points, not a machine property
    refs = [_ref("nl 50", 3000, 50.0, 0.17), _ref("nl 120", 2000, 120.0, 0.19)]
    tb = C.emf_temperature_basis(3000, refs, BR_PTS, 80.0)
    assert tb["basis"] == C.TEMP_BR_SCALED and tb["fit"]["accepted"] is False and tb["fit"]["elasticity"] < 0
    # two points less than 10 K apart are not a fit either
    close = [_ref("nl 50", 3000, 50.0, 0.17), _ref("nl 55", 2000, 55.0, 0.169)]
    assert C.emf_temperature_basis(3000, close, BR_PTS, 90.0)["basis"] == C.TEMP_BR_SCALED


def test_c_one_far_point_keeps_the_br_scaling_and_is_labelled_approximate():
    refs = [_ref("nl 150", 3000, 150.0, 0.15)]
    tb = C.emf_temperature_basis(3000, refs, BR_PTS, 25.0)
    assert tb["basis"] == C.TEMP_BR_SCALED
    assert tb["factor"] == pytest.approx(_br(25.0) / _br(150.0)) == pytest.approx(tb["br_ratio"])
    assert tb["elasticity"] is None and tb["fit"] is None


def test_no_bench_temperature_and_no_stored_point():
    refs = [_ref("nl 150", 3000, 150.0, 0.15)]
    tb = C.emf_temperature_basis(3000, refs, BR_PTS, None)
    assert tb["basis"] == C.TEMP_NO_BENCH_T and tb["factor"] == 1.0
    assert C.emf_temperature_basis(3000, [], BR_PTS, 50.0) is None


# ── the same through the free-run comparison ──────────────────────────────────────────────────

LIB = {"magnet": {"GRADE_20C": {"grade": "GRADE", "temperature_c": 20, "Br": 1.40},
                  "GRADE_150C": {"grade": "GRADE", "temperature_c": 150, "Br": 1.24}}}


def _model(points, end3d=None):
    duties = []
    for name, rpm, temp, e in points:
        s = {"rpm": rpm, "I_phase_rms_A": 0.0, "V_line_peak_V": 1.03 * e * rpm, "V1_LL_V": e * rpm,
             "V_line_rms_V": e * rpm / math.sqrt(2.0), "magnet_temp_used_c": temp}
        duties.append({"name": name, "rpm": rpm, "current_arms": 0, "summary": s})
    cfg = {"winding": {"star_delta": "star"}, "materials": {"magnet": "GRADE_20C"}, "duties": duties}
    return model_source.from_docs({}, cfg, {}, LIB, end3d=end3d)


def _rec(temp, rpms=(3000, 4000), **cols):
    cols = cols or {"v_ll_fund_pk_v": lambda n: 0.172 * n}
    rows = [{"rpm": n, **{k: f(n) for k, f in cols.items()}} for n in rpms]
    return models.normalize({"kind": "free_run", "date": "2026-01-15", "conditions": {"magnet_temp_c": temp},
                             "rows": rows}, who="eng@example.test")


def _groups(model, rec):
    return {g["key"]: g for g in comparison.compare_record(rec, model, "D", "C")["groups"]}


def _rows(group):
    return {r["key"]: r for r in group["rows"]}


def test_the_groups_say_which_temperature_basis_was_used():
    pts = [("nl 50", 3000, 50.0, 0.170), ("nl 120", 2000, 120.0, 0.165)]
    m = _model(pts, end3d={"k_flux": 0.95})
    br = m["magnet"]["points"]
    # (a) at another temperature within 10 K: the stored point, compared directly, and the basis names it
    a = _groups(m, _rec(56.0))
    row = a["emf_peak"]["rows"][0] if "emf_peak" in a else None
    kv = _rows(a["kv_kt"])
    assert kv["kv_slope"]["model"] == pytest.approx(170.0 * 0.95)                   # 0.170 V/rpm, not scaled
    assert a["kv_kt"]["basis_key"] == "kv_3d_pt" and a["kv_kt"]["basis_params"]["sim"] == "50"
    assert "stored no-load point at 50 C" in a["kv_kt"]["basis"]
    assert row is None                                                              # no Vpp/2 read: no EMF peak group
    # (a) exactly at the stored temperature: the old wording, no suffix
    assert _groups(m, _rec(50.0))["kv_kt"]["basis_key"] == "kv_3d"
    # (b) between the two points: the fitted elasticity, named with its value
    b = _groups(m, _rec(85.0))
    el = math.log(0.165 / 0.170) / math.log(C.br_at(br, 120.0) / C.br_at(br, 50.0))
    assert b["kv_kt"]["basis_key"] == "kv_3d_fit" and b["kv_kt"]["basis_params"]["el"] == "%.2f" % el
    f = (C.br_at(br, 85.0) / C.br_at(br, 50.0)) ** el
    assert _rows(b["kv_kt"])["kv_slope"]["model"] == pytest.approx(170.0 * f * 0.95)
    # (c) one stored point only, far from the bench: Br scaling, approximate
    m1 = _model([("nl 150", 3000, 150.0, 0.15)])
    c = _groups(m1, _rec(25.0, v_ll_pp_v=lambda n: 2 * 0.172 * n))
    assert c["emf_peak"]["basis_key"] == "emf_2d_ts" and c["kv_kt"]["basis_key"] == "kv_2d_ts"
    assert "temperature-scaled with Br (approximate)" in c["emf_peak"]["basis"]
    pk = c["emf_peak"]["rows"][0]
    assert pk["extra"]["emf_temp_basis"] == "br_scaled" and "approximate" in pk["detail"]
    brm = m1["magnet"]["points"]
    assert pk["model"] == pytest.approx(1.03 * 0.15 * 3000 * C.br_at(brm, 25.0) / C.br_at(brm, 150.0))


def test_the_emf_slope_row_follows_the_bench_basis_and_kv_stays_on_the_fundamental():
    m = _model([("nl 50", 3000, 50.0, 0.170)])
    # a bench that read only Vpp / 2: the waveform peak on both sides of the EMF-slope row
    pp = _rows(_groups(m, _rec(50.0, v_ll_pp_v=lambda n: 2 * 0.180 * n))["kv_kt"])
    assert pp["kv_slope"]["measured"] == pytest.approx(180.0)
    assert pp["kv_slope"]["model"] == pytest.approx(1.03 * 170.0)                    # the model's waveform peak
    assert pp["kv_slope"]["extra"]["model_basis"] == "waveform peak" and pp["kv_slope"]["extra"]["basis_mixed"] is False
    # ... Kv and Kt stay on the fundamental and say that the bench Kv is not
    assert pp["kv"]["model"] == pytest.approx(1000.0 / 170.0)
    assert pp["kt"]["model"] == pytest.approx(K.kt_from_kv(1000.0 / 170.0))
    assert pp["kv"]["extra"]["bench_not_fundamental"] is True and pp["kv"]["extra"]["bench_basis"] == "pp"
    assert "not the fundamental" in pp["kv"]["detail"] and "not the fundamental" in pp["kt"]["detail"]
    # a bench that read rms: rms x sqrt(2) on both sides
    rms = _rows(_groups(m, _rec(50.0, v_ll_rms_v=lambda n: 0.170 * n / math.sqrt(2.0)))["kv_kt"])
    assert rms["kv_slope"]["model"] == pytest.approx(170.0) and rms["kv_slope"]["extra"]["model_basis"] == "rms x sqrt(2)"
    # the fundamental: no note
    fund = _rows(_groups(m, _rec(50.0))["kv_kt"])
    assert fund["kv"]["extra"]["bench_not_fundamental"] is False and "not the fundamental" not in fund["kv"]["detail"]


def test_a_model_without_the_benchs_basis_says_the_two_are_mixed():
    s = {"rpm": 3000, "I_phase_rms_A": 0.0, "V_line_peak_V": 1.03 * 510.0, "V1_LL_V": 510.0, "magnet_temp_used_c": 50.0}
    cfg = {"winding": {"star_delta": "star"}, "materials": {"magnet": "GRADE_20C"},
           "duties": [{"name": "nl", "rpm": 3000, "current_arms": 0, "summary": s}]}       # no rms stored
    m = model_source.from_docs({}, cfg, {}, LIB)
    rows = _rows(_groups(m, _rec(50.0, v_ll_rms_v=lambda n: 0.170 * n / math.sqrt(2.0)))["kv_kt"])
    assert rows["kv_slope"]["extra"]["basis_mixed"] is True
    assert rows["kv_slope"]["model"] == pytest.approx(170.0)                           # the fundamental, said so
    assert "different bases" in rows["kv_slope"]["detail"]


def test_the_report_table_label_says_the_temperature_basis():
    from motor_ai_sim.motor_tests import report as R
    g = {"label": "EMF line-line peak (Vpp / 2)"}
    assert R._group_label({**g, "basis_key": "emf_3d"}) == g["label"]
    assert "approximate" in R._group_label({**g, "basis_key": "emf_3d_ts"})
    assert "0.20" in R._group_label({**g, "basis_key": "kv_2d_fit", "basis_params": {"el": "0.20"}})
    assert "50 C" in R._group_label({**g, "basis_key": "emf_2d_pt", "basis_params": {"sim": "50"}})
