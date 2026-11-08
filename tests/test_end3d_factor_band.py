"""A stored 3-D / end-effect factor above 1.0 is applied, not dropped (EMF audit 2026-11-07).

A bench EMF that reads higher than the model (a 40 mm-stack test motor: +8.5 % over the simulation) gives a flux factor
above 1; so does flux linked beyond the stack.  Before this fix five readers kept k <= 1.0 only and SILENTLY printed the
uncorrected Kv, Kt, voltage gates and projection numbers for such a factor, while other readers of the same run (the
3-D voltage column, the torque factor up to 1.2) applied it.  Now every reader takes the one band
``end3d_factors.FACTOR_BAND`` = (0.5, 1.5); a value outside it, or not finite, is refused and the refusal is logged.

Also: the Kt row of a bench comparison is formed from the EMF slope, so it is the no-load Kt = 11.695 / Kv and carries
the flux factor of Kv (owner 2026-11-05), not the measured torque factor k_T (a load-point quantity).

SYNTHETIC numbers only.
"""
from __future__ import annotations

import logging
import math

import pytest

from motor_ai_sim import end3d_factors as F
from motor_ai_sim import kv_kt as K

K_HIGH = 1.085          # a factor above 1: the bench EMF is 8.5 % over the model
BAD = (1.6, 0.4, 0.5, 1.5, float("nan"), float("inf"), -1.0, "abc")


# ── the one band ───────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("k", (0.51, 0.9502, 1.0, K_HIGH, 1.2, 1.3, 1.49))
def test_a_plausible_factor_either_side_of_one_is_accepted(k):
    assert F.accepted_factor(k) == pytest.approx(k)


@pytest.mark.parametrize("k", BAD)
def test_an_implausible_or_broken_factor_is_refused_and_logged(k, caplog):
    with caplog.at_level(logging.WARNING, logger="motor_ai_sim.end3d_factors"):
        assert F.accepted_factor(k, what="k_flux") is None
    assert any("k_flux" in r.getMessage() and "not applied" in r.getMessage() for r in caplog.records)


def test_no_factor_is_not_an_error(caplog):
    with caplog.at_level(logging.WARNING, logger="motor_ai_sim.end3d_factors"):
        assert F.accepted_factor(None) is None
    assert not caplog.records


def test_the_torque_factor_takes_the_same_band():
    assert F.torque_factor({"k_flux": 1.3}) == (1.3, F.BASIS_FLUX)
    assert F.torque_factor({"k_flux": 0.95, "k_T": 1.3}) == (1.3, F.BASIS_MEASURED)
    assert F.torque_factor({"k_flux": 1.6}) == (None, F.BASIS_2D)


# ── kv_kt.noload_constants ─────────────────────────────────────────────────────────────────────

def test_noload_constants_apply_a_flux_factor_above_one():
    c = K.noload_constants({"KV_noload_rpm_per_V_line": 6.02}, k_flux=K_HIGH)
    assert c["kv"] == pytest.approx(6.02 / K_HIGH)
    assert c["kt"] == pytest.approx(K.kt_from_kv(6.02 / K_HIGH))
    # refused: the 2-D constants, never a made-up correction
    assert K.noload_constants({"KV_noload_rpm_per_V_line": 6.02}, k_flux=1.6)["kv"] == pytest.approx(6.02)


# ── motor_tests.model_source.factors_from_end3d ────────────────────────────────────────────────

def test_the_comparison_keeps_a_flux_factor_above_one_and_names_a_refused_one():
    from motor_ai_sim.motor_tests import model_source as MS
    f = MS.factors_from_end3d({"k_flux": K_HIGH})
    assert f["k_flux"] == pytest.approx(K_HIGH) and f["k_torque"] == pytest.approx(K_HIGH)
    assert f["torque_basis"] == F.BASIS_FLUX and "k_flux_rejected" not in f
    bad = MS.factors_from_end3d({"k_flux": 1.6})
    assert bad["k_flux"] is None and bad["k_torque"] is None and bad["k_flux_rejected"] == 1.6
    assert "k_flux_rejected" not in MS.factors_from_end3d(None)


# ── report._warning_context: the voltage gates and the runaway speed ───────────────────────────

def _ctx(k):
    from motor_ai_sim import report as R
    col = {"duty": "peak", "d": {"rpm": 20000.0}, "result": {}, "res": {},
           "em": {"V_line_peak_V": 500.0, "V1_LL_V": 520.0, "KV_noload_rpm_per_V_line": 40.0,
                  "end3d": {"k_flux": k}}}
    return R._warning_context(col, mats={}, batt={"v_min": 600.0, "v_max": 700.0}, brg=None,
                              max_speed_rpm=20000.0, mag_lim=180.0, mag_note="", ins_lim=200.0,
                              ins_note="", cold_k=1.0, cold_note="")


def test_the_voltage_gates_carry_a_flux_factor_above_one():
    ctx = _ctx(K_HIGH)
    assert ctx["k_3d"] == pytest.approx(K_HIGH)
    assert ctx["v_line_peak_v"] == pytest.approx(500.0 * K_HIGH)
    assert ctx["v_line_fund_v"] == pytest.approx(520.0 * K_HIGH)
    # the no-load speed at the pack maximum: KV / k_3d, like every other flux-proportional number
    assert ctx["runaway_vmax_rpm"] == pytest.approx(40.0 / K_HIGH * 700.0)


def test_a_refused_factor_leaves_the_2d_values():
    ctx = _ctx(1.6)
    assert ctx["k_3d"] is None and ctx["v_line_peak_v"] == pytest.approx(500.0)
    assert ctx["runaway_vmax_rpm"] == pytest.approx(40.0 * 700.0)


def test_cold_torque_constants_take_the_same_band():
    from motor_ai_sim import report as R
    c = {"two_d": {"Kt_Nm_per_Arms": 2.0, "Km_Nm_sqrtW": 1.0}, "k_3d": 1.3, "point": {"star_delta": "star"}}
    assert R.cold_torque_constants(c)["kt_line_Nm_per_A"] == pytest.approx(2.0 * 1.3)


# ── development.motors: the project comparison table ───────────────────────────────────────────

def test_the_project_table_applies_a_flux_factor_above_one():
    from motor_ai_sim.development.motors import _comparison_projection
    duty = {"result": {"T_em_avg_Nm": 1000.0, "P_mech_W": 90000.0, "efficiency_pct": 96.0,
                       "V_phase_peak_V": 300.0, "V_line_peak_V": 520.0},
            "summary": {"end3d": {"k_flux": K_HIGH}}}
    c = _comparison_projection(duty, {})
    assert c["k_3d"] == pytest.approx(K_HIGH)
    assert c["v_line_peak_v"] == pytest.approx(520.0 * K_HIGH) and c["v_line_peak_v_2d"] == 520.0
    assert c["torque_nm"] == pytest.approx(1000.0 * K_HIGH)


# ── bench comparison: Kt follows Kv ────────────────────────────────────────────────────────────

def test_the_bench_comparison_kt_is_formed_from_the_corrected_kv():
    from motor_ai_sim.motor_tests import comparison, model_source, models
    lib = {"magnet": {"GRADE_20C": {"grade": "GRADE", "temperature_c": 20, "Br": 1.40}}}
    s = {"rpm": 3000, "I_phase_rms_A": 0.0, "V_line_peak_V": 466.2, "V1_LL_V": 466.2,
         "V_line_rms_V": 466.2 / math.sqrt(2.0), "magnet_temp_used_c": 50.0}
    cfg = {"winding": {"star_delta": "star"}, "materials": {"magnet": "GRADE_20C"},
           "duties": [{"name": "no-load 3000", "rpm": 3000, "current_arms": 0, "summary": s}]}
    model = model_source.from_docs({}, cfg, {}, lib, end3d={"k_flux": 0.95, "k_T": 0.98})
    rec = models.normalize({"kind": "free_run", "date": "2026-01-15", "conditions": {"magnet_temp_c": 50.0},
                            "rows": [{"rpm": n, "v_ll_fund_pk_v": 0.17 * n} for n in (1000, 2000, 3000, 4000)]},
                           who="eng@example.test")
    groups = {g["key"]: g for g in comparison.compare_record(rec, model, "D", "C")["groups"]}
    rows = {r["key"]: r for r in groups["kv_kt"]["rows"]}
    kv_model = rows["kv"]["model"]
    assert kv_model == pytest.approx(1000.0 / 155.4 / 0.95)
    assert rows["kt"]["model"] == pytest.approx(K.kt_from_kv(kv_model))          # Kt = 11.695 / Kv, one pair
    assert rows["kt"]["measured"] == pytest.approx(K.kt_from_kv(rows["kv"]["measured"]))
    # the measured k_T is still carried as provenance, not applied to the no-load constant
    assert rows["kt"]["extra"]["end3d"]["k_torque"] == pytest.approx(0.98)
