"""The opt-in ripple objective and hard limits of the Descent (TASK_optimiser_ripple_objective.md,
items 1-4): objective "min_ripple", efficiency floor, coil current-density cap, line-line peak voltage cap.
A request that states none of them is the cost it always was."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from motor_ai_sim.routes import optimization as O


def _m(**kw):
    m = {"efficiency": 0.93, "torque_per_mass_Nm_kg": 5.0, "T_ripple_pct": 4.0, "T_avg_Nm": 3.0,
         "J_coil_A_per_mm2": 12.0, "V_line_peak_V": 40.0, "V_peak": 23.0, "THD_LL_pct": 2.0}
    m.update(kw)
    return m


def _req(**kw):
    body = {"variables": [{"name": "tooth_width", "min": 0.0, "max": 0.0}],
            "operating_point": {"current_a": 10.0, "gamma_deg": 5.0, "rpm": 3000.0},
            "max_iters": 2, "gap_layers": 1, "run_id": "ripple-obj-test"}
    body.update(kw)
    return O.DescentRequest(**body)


@pytest.fixture(autouse=True)
def _clean():
    saved = dict(O._RUN_LIMITS)
    pen = (dict(O._RIPPLE_PEN_LAM), dict(O._THD_PEN))
    O._reset_run_limits()
    yield
    O._RUN_LIMITS.update(saved)
    O._RIPPLE_PEN_LAM.update(pen[0])
    O._THD_PEN.update(pen[1])


def _cost(m, base=None, **limits):
    O._RUN_LIMITS.update(limits)
    return O._descent_cost(m, base or _m(), 5.0, 1.0, 1.0, 1.0)


def test_a_request_without_the_new_fields_is_the_cost_it_always_was():
    base = _m()
    legacy = O._descent_cost(_m(efficiency=0.95, torque_per_mass_Nm_kg=5.5), base, 5.0, 1.0, 1.0, 1.0)
    eff, td = 0.95 / 0.93, 5.5 / 5.0
    assert legacy == (-(eff * td), eff * td)
    bl = dict(base, _bline={"w_td": 0.3, "w_eff": 0.7, "td_a": 5.0, "eff_a": 0.93, "norm": 1.0})
    assert O._descent_cost(_m(efficiency=0.95), bl, 5.0, 1.0, 1.0, 1.0)[1] == pytest.approx(0.7 * 0.02)
    plan = O.prepare_descent(_req())
    assert plan["run_limits"] == {"mode": "", "eff_min": 0.0, "j_max": 0.0, "vll_max": 0.0}
    assert "objective_limits" not in plan["init_state"] and "objective_limits" not in plan["response"]


def test_min_ripple_ranks_by_the_ripple_alone():
    lo, hi = _m(T_ripple_pct=2.0), _m(T_ripple_pct=6.0, efficiency=0.99)
    c_lo, F_lo = _cost(lo, mode="min_ripple")
    c_hi, F_hi = _cost(hi, mode="min_ripple")
    assert c_lo < c_hi and F_lo > F_hi
    assert c_lo == pytest.approx(0.02) and F_lo == pytest.approx(-0.02)


def test_min_ripple_with_an_undefined_ripple_is_never_a_good_design():
    c, _ = _cost(_m(T_ripple_pct=None), mode="min_ripple")
    assert c >= 1e6


@pytest.mark.parametrize("limits,bad", [
    ({"eff_min": 0.94}, {"efficiency": 0.90}),
    ({"j_max": 10.0}, {"J_coil_A_per_mm2": 14.0}),
    ({"vll_max": 35.0}, {"V_line_peak_V": 41.0}),
])
def test_a_broken_hard_limit_never_beats_a_feasible_design(limits, bad):
    good = _m(T_ripple_pct=9.0, efficiency=0.95, J_coil_A_per_mm2=9.0, V_line_peak_V=30.0)
    broken = _m(T_ripple_pct=0.5, **bad)
    cg, _ = _cost(good, mode="min_ripple", **limits)
    cb, _ = _cost(broken, mode="min_ripple", **limits)
    assert cg < 0.2 < 900 < cb


def test_the_overshoot_still_points_the_search_the_right_way():
    limits = {"mode": "min_ripple", "vll_max": 35.0}
    c1, _ = _cost(_m(V_line_peak_V=45.0), **limits)
    c2, _ = _cost(_m(V_line_peak_V=38.0), **limits)
    assert c2 < c1


def test_a_limit_that_is_on_and_whose_metric_is_missing_counts_as_broken():
    m = _m()
    m.pop("J_coil_A_per_mm2")
    assert O._limit_violation(m) == 0.0
    O._RUN_LIMITS["j_max"] = 20.0
    assert O._limit_violation(m) == 1.0
    O._RUN_LIMITS["j_max"] = 0.0
    O._RUN_LIMITS["eff_min"] = 0.9
    assert O._limit_violation(_m(efficiency=float("nan"))) == 1.0


def test_the_limits_also_hold_for_the_default_objective():
    c_ok, _ = _cost(_m(), j_max=20.0)
    c_bad, _ = _cost(_m(J_coil_A_per_mm2=30.0), j_max=20.0)
    assert c_bad > c_ok + 900


def test_the_plan_carries_the_limits_and_begin_installs_then_clears_them():
    plan = O.prepare_descent(_req(objective="min_ripple", efficiency_min_pct=94.0, j_max_a_mm2=20.0,
                                  v_ll_peak_max_v=48.0))
    assert plan["run_limits"] == {"mode": "min_ripple", "eff_min": 0.94, "j_max": 20.0, "vll_max": 48.0}
    assert plan["response"]["objective_limits"] == plan["run_limits"]
    assert plan["init_state"]["objective_limits"] == plan["run_limits"]
    saved = dict(O._descent_state)
    try:
        O.begin_descent_state(plan)
        assert O._RUN_LIMITS == plan["run_limits"]
        O.begin_descent_state(O.prepare_descent(_req()))
        assert O._RUN_LIMITS == {"mode": "", "eff_min": 0.0, "j_max": 0.0, "vll_max": 0.0}
    finally:
        O._descent_state.clear()
        O._descent_state.update(saved)


@pytest.mark.parametrize("field", ["efficiency_min_pct", "j_max_a_mm2", "v_ll_peak_max_v"])
def test_a_negative_limit_is_refused(field):
    with pytest.raises(ValidationError):
        _req(**{field: -1.0})
