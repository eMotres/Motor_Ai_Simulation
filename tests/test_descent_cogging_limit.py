"""The opt-in cogging torque of the Descent (TASK_descent_cogging_limit.md): a hard cap cogging_max_nm and the
objective "min_cogging", both on ONE extra no-load solve per candidate geometry (0 A, 20 C, >= 120 steps).
A request that states neither is the run it always was."""
from __future__ import annotations

import threading

import pytest
from pydantic import ValidationError

from motor_ai_sim.routes import optimization as O


def _m(**kw):
    m = {"efficiency": 0.97, "torque_per_mass_Nm_kg": 4.0, "T_ripple_pct": 5.0, "T_avg_Nm": 41.0,
         "J_coil_A_per_mm2": 11.0, "V_line_peak_V": 500.0, "V_peak": 290.0, "THD_LL_pct": 8.0}
    m.update(kw)
    return m


def _req(**kw):
    body = {"variables": [{"name": "tooth_width", "min": 0.0, "max": 0.0}],
            "operating_point": {"current_a": 10.0, "gamma_deg": 5.0, "rpm": 3000.0},
            "max_iters": 2, "gap_layers": 1, "run_id": "cogging-limit-test"}
    body.update(kw)
    return O.DescentRequest(**body)


@pytest.fixture(autouse=True)
def _clean():
    saved = (dict(O._RUN_LIMITS), dict(O._COG_LIMIT))
    O._reset_run_limits()
    yield
    O._RUN_LIMITS.update(saved[0])
    O._COG_LIMIT.update(saved[1])


def _cost(m, base=None, mode="", cog_max=0.0):
    O._RUN_LIMITS["mode"] = mode
    O._COG_LIMIT.update(max=cog_max, steps=144)
    return O._descent_cost(m, base or _m(cogging_pp_Nm=2.0), 6.0, 1.0, 1.0, 1.0)


def test_a_request_without_the_new_fields_plans_and_costs_as_before():
    req = _req()
    assert req.cogging_max_nm == 0.0 and req.cogging_steps_per_period == 144
    plan = O.prepare_descent(req)
    assert "cogging_limit" not in plan and "cogging_limit" not in plan["response"]
    assert "cogging_limit" not in plan["init_state"]
    assert plan["run_limits"] == {"mode": "", "eff_min": 0.0, "j_max": 0.0, "vll_max": 0.0}
    O._reset_run_limits()
    assert not O._cogging_on()
    # with the cogging fields off a cogging value on the metrics changes nothing
    base = _m()
    assert O._descent_cost(_m(cogging_pp_Nm=50.0), base, 6.0, 1.0, 1.0, 1.0) == \
        O._descent_cost(_m(), base, 6.0, 1.0, 1.0, 1.0)


def test_the_cap_is_hard_and_its_overshoot_points_the_way():
    ok, _ = _cost(_m(cogging_pp_Nm=0.8), cog_max=1.0)
    over_small, _ = _cost(_m(cogging_pp_Nm=1.2, efficiency=0.99), cog_max=1.0)
    over_big, _ = _cost(_m(cogging_pp_Nm=3.0, efficiency=0.99), cog_max=1.0)
    assert ok < 900 < over_small < over_big


def test_a_cap_whose_cogging_is_missing_counts_as_broken():
    c, _ = _cost(_m(), cog_max=1.0)
    assert c > 900
    c, _ = _cost(_m(cogging_pp_Nm=float("nan")), cog_max=1.0)
    assert c > 900


def test_min_cogging_ranks_by_cogging_relative_to_the_start():
    lo, F_lo = _cost(_m(cogging_pp_Nm=0.5, efficiency=0.96), mode="min_cogging")
    hi, F_hi = _cost(_m(cogging_pp_Nm=1.5, efficiency=0.99), mode="min_cogging")
    assert lo < hi and F_lo > F_hi
    assert lo == pytest.approx(0.25) and F_lo == pytest.approx(-0.25)    # 0.5 / start 2.0


def test_min_cogging_keeps_the_efficiency_floor_and_never_takes_a_missing_value():
    O._RUN_LIMITS["eff_min"] = 0.97
    good, _ = _cost(_m(cogging_pp_Nm=1.9, efficiency=0.971), mode="min_cogging")
    low_eff, _ = _cost(_m(cogging_pp_Nm=0.1, efficiency=0.95), mode="min_cogging")
    assert good < 1.0 < 900 < low_eff
    missing, _ = _cost(_m(), mode="min_cogging")
    assert missing >= 1e6


def test_the_plan_carries_the_cogging_settings_and_begin_installs_then_clears_them():
    plan = O.prepare_descent(_req(objective="min_cogging", efficiency_min_pct=97.0, cogging_max_nm=1.5,
                                  cogging_steps_per_period=168))
    assert plan["run_limits"]["mode"] == "min_cogging"
    assert plan["cogging_limit"] == {"max": 1.5, "steps": 168}
    assert plan["response"]["cogging_limit"] == plan["init_state"]["cogging_limit"] == plan["cogging_limit"]
    saved = dict(O._descent_state)
    try:
        O.begin_descent_state(plan)
        assert O._COG_LIMIT == {"max": 1.5, "steps": 168} and O._cogging_on()
        O.begin_descent_state(O.prepare_descent(_req()))
        assert O._COG_LIMIT == {"max": 0.0, "steps": 0} and not O._cogging_on()
        assert O._RUN_LIMITS == {"mode": "", "eff_min": 0.0, "j_max": 0.0, "vll_max": 0.0}
    finally:
        O._descent_state.clear()
        O._descent_state.update(saved)


def test_the_cap_alone_also_measures_with_the_default_objective():
    plan = O.prepare_descent(_req(cogging_max_nm=1.0))
    assert plan["run_limits"]["mode"] == ""
    assert plan["cogging_limit"] == {"max": 1.0, "steps": 144}


def test_reset_clears_the_cogging_settings():
    O._COG_LIMIT.update(max=2.0, steps=144)
    O._reset_run_limits()
    assert O._COG_LIMIT == {"max": 0.0, "steps": 0}


@pytest.mark.parametrize("kw", [{"cogging_max_nm": -1.0}, {"cogging_steps_per_period": 48},
                                {"cogging_steps_per_period": 119}])
def test_a_negative_cap_or_fewer_than_120_steps_is_refused(kw):
    with pytest.raises(ValidationError):
        _req(**kw)


class _Solver:
    def __init__(self, res=None, ok=True):
        self.calls = []
        self.res = res if res is not None else {"T_ripple_pp_Nm": 0.9,
                                                "cogging_sampling_final_quality_sufficient": True}
        self.ok = ok

    def __call__(self, overrides, current_a, steps, coil_temp_c, **kw):
        self.calls.append((dict(overrides), current_a, steps, coil_temp_c, kw))
        return {"ok": self.ok, "res": dict(self.res), "error": None if self.ok else "boom"}


def _load_out():
    return {"ok": True, "res": {"T_em_Nm": 41.0}, "overrides": {"tooth_width": 23.3}}


def test_the_no_load_solve_is_0_A_20_C_and_once_per_geometry(monkeypatch):
    solver = _Solver()
    monkeypatch.setattr(O, "_subprocess_eval", solver)
    O._COG_LIMIT.update(max=0.0, steps=144)
    memo, lock = {}, threading.Lock()
    kw = {"mesh_size_mm": 1.6, "min_size_mm": 0.3, "n_sectors": 2, "gap_layers": 1}
    o1 = O._attach_cogging(_load_out(), {"tooth_width": 23.3, "gamma_deg": 3.6}, memo, lock, kw)
    o2 = O._attach_cogging(_load_out(), {"tooth_width": 23.3}, memo, lock, kw)   # rescale / final re-solve
    assert len(solver.calls) == 1
    geo, cur, steps, coil, ckw = solver.calls[0]
    assert geo == {"tooth_width": 23.3} and cur == 0.0 and steps == 144 and coil == 20.0
    assert ckw["magnet_temp_c"] == 20.0 and ckw["rotor_eddy"] is False and ckw["demag"] is False
    assert ckw["sampling_purpose"] == "cogging_quality" and ckw["ripple_pct_gate"] is False
    assert ckw["mesh_size_mm"] == 1.6 and ckw["n_sectors"] == 2 and ckw["gap_layers"] == 1
    for o in (o1, o2):
        assert o["res"]["cogging_pp_Nm"] == 0.9 and o["res"]["cogging_steps_per_period"] == 144
        assert "cogging_error" not in o["res"]


def test_off_means_no_extra_solve_and_an_unchanged_result(monkeypatch):
    solver = _Solver()
    monkeypatch.setattr(O, "_subprocess_eval", solver)
    out = O._attach_cogging(_load_out(), {"tooth_width": 23.3}, {}, threading.Lock(), {})
    assert solver.calls == [] and out == _load_out()


@pytest.mark.parametrize("solver,why", [
    (_Solver(res={"T_ripple_pp_Nm": 0.9, "cogging_sampling_final_quality_sufficient": False}), "quality"),
    (_Solver(ok=False), "failed"),
])
def test_a_cogging_value_below_final_quality_or_failed_is_not_kept(monkeypatch, solver, why):
    monkeypatch.setattr(O, "_subprocess_eval", solver)
    O._COG_LIMIT.update(max=1.0, steps=144)
    o = O._attach_cogging(_load_out(), {"tooth_width": 23.3}, {}, threading.Lock(), {})
    assert o["res"]["cogging_pp_Nm"] is None and why in o["res"]["cogging_error"]
    assert O._limit_violation(o["res"]) == 1.0


def test_a_failed_load_eval_gets_no_cogging_solve(monkeypatch):
    solver = _Solver()
    monkeypatch.setattr(O, "_subprocess_eval", solver)
    O._COG_LIMIT.update(max=1.0, steps=144)
    bad = {"ok": False, "error": "mesh"}
    assert O._attach_cogging(dict(bad), {"tooth_width": 1.0}, {}, threading.Lock(), {}) == bad
    assert solver.calls == []


def test_points_and_summaries_carry_cogging_only_when_measured():
    plain = {"ok": True, "res": _m(), "overrides": {"tooth_width": 23.3}}
    assert "cogging_pp" not in O._pt(plain, "cmaes")
    assert "cogging_pp_Nm" not in O._msum(_m())
    assert "cogging_pp_Nm" not in O._point_as_metrics(O._pt(plain, "cmaes"))
    meas = {"ok": True, "res": _m(cogging_pp_Nm=0.7, cogging_steps_per_period=144),
            "overrides": {"tooth_width": 23.3}}
    pt = O._pt(meas, "cmaes")
    assert pt["cogging_pp"] == 0.7
    assert O._point_as_metrics(pt)["cogging_pp_Nm"] == 0.7
    assert O._msum(meas["res"])["cogging_pp_Nm"] == 0.7
