"""Free-run EMF comparison: the magnet-temperature basis and the voltage basis (EMF audit 2026-11-07).

Temperature (``compare.emf_temperature_basis``, owner 2026-11-18, model-vs-bench investigation): a row is compared ONLY
when the bench magnet temperature is known and a stored no-load point lies within 10 K of it; that point is compared directly
(speed scaling only). Otherwise the row is "not comparable: magnet temperature unknown / no model point at this temperature"
and carries no model value - never scaled with Br, a fitted elasticity, or Br^2 (the no-load loss).

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
    far = C.emf_temperature_basis(3000, refs, BR_PTS, 62.0)
    assert far["basis"] == C.TEMP_NO_MODEL_T and far["comparable"] is False and "no model point at this temperature" in far["reason"]
    # among close points the nearest temperature wins over the nearest speed
    refs2 = [_ref("nl 50 @2000", 2000, 50.0, 0.17), _ref("nl 45 @3000", 3000, 45.0, 0.17)]
    assert C.emf_temperature_basis(3000, refs2, BR_PTS, 51.0)["ref"]["duty"] == "nl 50 @2000"


def test_between_two_stored_points_is_not_comparable_and_nothing_is_fitted_or_scaled():
    refs = [_ref("nl 50", 3000, 50.0, _machine_e(50.0)), _ref("nl 120", 2000, 120.0, _machine_e(120.0))]
    tb = C.emf_temperature_basis(3000, refs, BR_PTS, 80.0)
    assert tb["basis"] == C.TEMP_NO_MODEL_T and tb["comparable"] is False
    assert tb["factor"] == 1.0 and tb["elasticity"] is None and tb["fit"] is None and tb["br_ratio"] == 1.0
    assert "50, 120" in tb["reason"]


def test_one_far_point_is_not_comparable():
    refs = [_ref("nl 150", 3000, 150.0, 0.15)]
    tb = C.emf_temperature_basis(3000, refs, BR_PTS, 25.0)
    assert tb["basis"] == C.TEMP_NO_MODEL_T and tb["comparable"] is False and tb["factor"] == 1.0


def test_no_bench_temperature_and_no_stored_point():
    refs = [_ref("nl 150", 3000, 150.0, 0.15)]
    tb = C.emf_temperature_basis(3000, refs, BR_PTS, None)
    assert tb["basis"] == C.TEMP_NO_BENCH_T and tb["factor"] == 1.0 and tb["comparable"] is False
    assert tb["reason"] == "not comparable: magnet temperature unknown"
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
    # between the two points (35 K from both): not comparable, the Kv row has no model value and says why
    b = _groups(m, _rec(85.0, v_ll_pp_v=lambda n: 2 * 0.172 * n))
    pk = b["emf_peak"]["rows"][0]
    assert pk["model"] is None and pk["extra"]["not_comparable"] == "no_model_temp" and "no model point at this temperature" in pk["detail"]
    assert _rows(b["kv_kt"])["kv_slope"]["model"] is None
    # one stored point only, far from the bench: the same, no Br scaling
    m1 = _model([("nl 150", 3000, 150.0, 0.15)])
    c = _groups(m1, _rec(25.0, v_ll_pp_v=lambda n: 2 * 0.172 * n))
    assert all(r["model"] is None and r["extra"]["not_comparable"] == "no_model_temp" for r in c["emf_peak"]["rows"])
    # a bench record WITHOUT a magnet temperature: not comparable, "magnet temperature unknown"
    rec = models.normalize({"kind": "free_run", "date": "2026-01-15", "conditions": {"ambient_c": 21.0},
                            "rows": [{"rpm": 3000, "v_ll_pp_v": 2 * 0.172 * 3000, "temp_c": 40.0}]}, who="eng@example.test")
    u = _groups(m1, rec)
    r0 = u["emf_peak"]["rows"][0]
    assert r0["model"] is None and r0["extra"]["not_comparable"] == "no_bench_temp" and "magnet temperature unknown" in r0["detail"]
    assert r0["extra"]["magnet_temp_source"] == "unknown"


def test_the_noload_loss_is_compared_directly_or_not_at_all():
    s = {"rpm": 3000, "I_phase_rms_A": 0.0, "V_line_peak_V": 510.0, "V1_LL_V": 500.0, "V_line_rms_V": 360.0,
         "magnet_temp_used_c": 50.0, "P_core_W": 10.0}
    cfg = {"winding": {"star_delta": "star"}, "materials": {"magnet": "GRADE_20C"},
           "duties": [{"name": "nl", "rpm": 3000, "current_arms": 0, "summary": s}]}
    m = model_source.from_docs({}, cfg, {}, LIB)
    rec = lambda t: models.normalize({"kind": "free_run", "date": "2026-01-15", "conditions": {"magnet_temp_c": t},
                                      "rows": [{"rpm": 3000, "shaft_power_w": 12.0}]}, who="eng@example.test")
    near = _rows(_groups(m, rec(55.0))["loss"])["loss"]
    assert near["model"] is not None and near["extra"]["br_ratio"] == 1.0           # not x (Br ratio)^2
    far = _rows(_groups(m, rec(120.0))["loss"])["loss"]
    assert far["model"] is None and far["extra"]["not_comparable"] == "no_model_temp"


def test_a_model_point_without_a_stamp_takes_the_saved_setting_not_the_cards_label():
    s = {"rpm": 3000, "I_phase_rms_A": 0.0, "V_line_peak_V": 510.0, "V1_LL_V": 500.0}      # no magnet_temp_used_c
    duty = {"name": "nl", "rpm": 3000, "current_arms": 0, "summary": dict(s), "mesh": {"sim.magnetTempC": 65.0}}
    cfg = {"winding": {"star_delta": "star"}, "materials": {"magnet": "GRADE_150C"}, "duties": [duty]}
    assert model_source.from_docs({}, cfg, {}, LIB)["noload"][0]["magnet_temp_c"] == 65.0
    duty.pop("mesh")                                                                      # no setting either: unknown, not 150 C
    assert model_source.from_docs({}, cfg, {}, LIB)["noload"][0]["magnet_temp_c"] is None


def test_a_saved_mesh_setting_is_not_borrowed_by_a_different_em_run():
    """Astra review of #767: no saved summary, a newer em_run without a temperature stamp, and an OLD mesh setting of
    80 C on the duty - the run's temperature stays unknown (the setting belongs to the saved snapshot, not to this run)."""
    numbers = {"rpm": 3000, "I_phase_rms_A": 0.0, "V_line_peak_V": 510.0, "V1_LL_V": 500.0}
    duty = {"name": "nl", "rpm": 3000, "current_arms": 0, "mesh": {"sim.magnetTempC": 80.0}}
    cfg = {"winding": {"star_delta": "star"}, "materials": {"magnet": "GRADE_150C"}, "duties": [duty]}
    stored = {"nl": {"em_run": {"numbers": dict(numbers)}}}
    m = model_source.from_docs({}, cfg, stored, LIB)
    assert m["noload"][0]["magnet_temp_c"] is None
    duty["summary"] = dict(numbers)                       # the saved snapshot of that duty: its own setting applies
    assert model_source.from_docs({}, cfg, stored, LIB)["noload"][0]["magnet_temp_c"] == 80.0
    stored["nl"]["em_run"]["numbers"]["magnet_temp_used_c"] = 55.0                 # a stamp on the run always wins
    duty.pop("summary")
    assert model_source.from_docs({}, cfg, stored, LIB)["noload"][0]["magnet_temp_c"] == 55.0


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
    assert "50 C" in R._group_label({**g, "basis_key": "emf_2d_pt", "basis_params": {"sim": "50"}})
