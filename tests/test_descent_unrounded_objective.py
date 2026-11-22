"""Opt-in `unrounded_objective` of the Descent (TASK_mesh_gap_stage3.md item 3): the candidate's objective figures before the rounding
of the point record travel next to the rounded ones, and the cost is formed from them. Off = the run it always was."""
from __future__ import annotations

import inspect

import pytest

from motor_ai_sim.routes import optimization as O
from test_mesh_presets import _machine


@pytest.fixture(autouse=True)
def _clean():
    O._reset_run_limits()
    yield
    O._reset_run_limits()


@pytest.fixture
def machine(monkeypatch):
    cfg = {"geometry": _machine(12.0, 12, 10)}
    monkeypatch.setattr(O, "get_config", lambda *a, **k: cfg)


def _m(td_raw, eff_raw):
    """A metrics dict as the point record has it (rounded to 4 / 5 places) plus the unrounded block."""
    return {"torque_per_mass_Nm_kg": round(td_raw, 4), "efficiency": round(eff_raw, 5), "T_ripple_pct": 2.0, "V_peak": 10.0,
            "unrounded": {"torque_per_mass_Nm_kg": td_raw, "efficiency": eff_raw}}


BASE = {"torque_per_mass_Nm_kg": 5.0, "efficiency": 0.9, "_bline": {"w_td": 1.0, "w_eff": 100.0, "td_a": 5.0, "eff_a": 0.9, "norm": 1.0}}


def test_off_the_cost_reads_the_rounded_figures_exactly_as_before():
    a, b = _m(5.000041, 0.900004), _m(5.000049, 0.900004)           # the same rounded figures (5.0000 / 0.90000)
    assert O._descent_cost(a, BASE, 5.0, 1.0, 1.0, 0.0) == O._descent_cost(b, BASE, 5.0, 1.0, 1.0, 0.0)


def test_on_a_close_ranking_is_told_apart_by_the_unrounded_figures():
    a, b = _m(5.000041, 0.900004), _m(5.000049, 0.900004)
    O._UNROUNDED["on"] = True
    ca, cb = O._descent_cost(a, BASE, 5.0, 1.0, 1.0, 0.0), O._descent_cost(b, BASE, 5.0, 1.0, 1.0, 0.0)
    assert cb[0] < ca[0] and cb[1] > ca[1]                           # b has the higher torque density: a lower cost
    # a metrics dict WITHOUT the block (an old cache entry) is read as it is
    plain = {k: v for k, v in a.items() if k != "unrounded"}
    assert O._descent_cost(plain, BASE, 5.0, 1.0, 1.0, 0.0) == O._descent_cost(plain, BASE, 5.0, 1.0, 1.0, 0.0)


def test_the_plan_carries_the_flag_only_when_asked_and_begin_and_reset_handle_it(machine):
    var = [{"name": "tooth_width", "min": 1.0, "max": 1.2, "step": 0.05}]
    off = O.prepare_descent(O.DescentRequest(variables=var))
    assert "unrounded_objective" not in off and "unrounded_objective" not in off["response"] and "unrounded_objective" not in off["init_state"]
    on = O.prepare_descent(O.DescentRequest(variables=var, unrounded_objective=True))
    assert on["unrounded_objective"] is True and on["response"]["unrounded_objective"] is True
    O.begin_descent_state(on)
    assert O._UNROUNDED["on"] is True
    O.begin_descent_state(off)
    assert O._UNROUNDED["on"] is False                               # an older run's never stays
    O._UNROUNDED["on"] = True
    O._reset_run_limits()
    assert O._UNROUNDED["on"] is False


def test_the_block_reaches_the_summaries_and_points_only_when_present():
    m = _m(5.1, 0.93)
    assert O._msum(m)["unrounded"] == m["unrounded"]
    assert "unrounded" not in O._msum({k: v for k, v in m.items() if k != "unrounded"})


def test_run_one_and_the_subprocess_spec_export_it_only_on_request():
    from motor_ai_sim.optimization import refine_proc as R
    assert inspect.signature(R.run_one).parameters["export_unrounded"].default is False
    assert inspect.signature(O._subprocess_eval).parameters["export_unrounded"].default is False
    src = inspect.getsource(O._subprocess_eval)
    assert '"export_unrounded": True} if export_unrounded else {}' in src


# --- the final shortlist and the baseline line read the unrounded figures when the flag is on (Astra pre-merge, #791) ---

def _std(x, current):
    return {"ok": True, "res": {"T_em_Nm": 1.0, "efficiency": 0.9, "torque_per_mass_Nm_kg": 5.0, "T_ripple_pct": 2.0,
                                "current_a": current, "nonlinear_converged": True, "cogging_sampling_purpose": "standard",
                                "cogging_sampling_final_quality_sufficient": True, "eddy_settled": True}}


def _pt(g, td_raw):
    m = _m(td_raw, 0.900004)
    return {"kind": "cmaes", "overrides": {"g": g}, "current_a": 10.0, "td": m["torque_per_mass_Nm_kg"], "eff": m["efficiency"],
            "ripple": 2.0, "v_peak": 10.0, "thd": 1.0, "unrounded": m["unrounded"]}


def _shortlisted(flag):
    O._UNROUNDED["on"] = flag
    calls = []

    def evaluate(x, current):
        calls.append(dict(x))
        return _std(x, current)

    points = [_pt(1, 5.000041), _pt(2, 5.000049)]                    # the same rounded figures, 2 is the better one
    O._finalize_standard_shortlist(points=points, best_x={"g": 9}, best_metrics={"current_a": 10.0}, coarse_base=BASE,
                                   base_x={"g": 0}, base_current=10.0, bump_pct=10.0, evaluate=evaluate, limit=2,
                                   score=lambda m, b: O._descent_cost(m, b, 5.0, 1.0, 1.0, 0.0))
    return [c["g"] for c in calls[2:]]                                # after baselines A and B


def test_the_point_record_keeps_the_block_for_the_shortlist():
    pt = _pt(1, 5.1)
    assert O._point_as_metrics(pt)["unrounded"] == pt["unrounded"]
    assert "unrounded" not in O._point_as_metrics({k: v for k, v in pt.items() if k != "unrounded"})


def test_on_the_shortlist_is_ranked_on_the_unrounded_figures():
    assert 2 in _shortlisted(True) and 1 not in _shortlisted(True)


def test_off_the_shortlist_is_ranked_as_it_always_was():
    off = _shortlisted(False)                                         # tie on the rounded figures: the first point wins, as before
    assert 1 in off and 2 not in off


def test_the_baseline_line_reads_the_unrounded_figures_only_when_on():
    a, b = _m(5.000041, 0.900004), _m(5.00104, 0.8999)
    off = O._make_bline(a, b, 10.0)
    O._UNROUNDED["on"] = True
    on = O._make_bline(a, b, 10.0)
    assert off["td_a"] == 5.0 and on["td_a"] == 5.000041
    assert on["eff_a"] == 0.900004 and off["eff_a"] == 0.9


# --- the shared state never reports an option the run did not ask for ---

def _plan(machine_unused=None, **kw):
    var = [{"name": "tooth_width", "min": 1.0, "max": 1.2, "step": 0.05}]
    return O.prepare_descent(O.DescentRequest(variables=var, **kw))


def test_an_on_run_followed_by_an_off_run_leaves_no_flag_in_the_state(machine):
    O.begin_descent_state(_plan(unrounded_objective=True))
    assert O._descent_state["unrounded_objective"] is True
    O.begin_descent_state(_plan())
    assert "unrounded_objective" not in O._descent_state and O._UNROUNDED["on"] is False


def test_a_descent_followed_by_auto_or_screen_clears_the_state_flag(machine):
    for _ in ("auto", "screen"):
        O.begin_descent_state(_plan(unrounded_objective=True))
        O._reset_run_limits()
        with O._descent_lock:
            O._forget_run_opt_state()
        assert "unrounded_objective" not in O._descent_state and O._UNROUNDED["on"] is False
    src = inspect.getsource(O.auto_start) + inspect.getsource(O)
    assert src.count("_forget_run_opt_state()") >= 4                  # begin, auto_start, auto worker, screen worker


def test_a_restart_does_not_revive_the_flag_of_the_process(machine):
    O.begin_descent_state(_plan(unrounded_objective=True))
    O._UNROUNDED["on"] = False                                        # a fresh process: the module default
    O.begin_descent_state(_plan())
    assert "unrounded_objective" not in O._descent_state
