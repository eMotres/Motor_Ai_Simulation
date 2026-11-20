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
