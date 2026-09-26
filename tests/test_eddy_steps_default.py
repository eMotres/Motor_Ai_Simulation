"""72 steps per electrical period by default for eddy-current runs (owner 2026-09-26).

BDF2 reads the L155 magnet loss -4.3 % at 36 steps and -1 % at 72
(docs/EDDY_TIME_INTEGRATION_2026-09-25.md).  What is pinned here:

* an eddy-on request that names no step count solves 72 — the transient route,
  the coupled loop and its continuous-rating / 20 °C passes;
* a count the caller names is ALWAYS the count used (never replaced by the
  default), and a malformed one is refused by name;
* optimizer screening keeps its own count while the Simulation tab holds only
  the eddy default, and obeys a count the user picked there.

No FEM is solved.
"""
from __future__ import annotations

import copy
import inspect

import pytest
from fastapi import HTTPException

from motor_ai_sim.simulation import eddy_steps as ES


class TestResolver:
    def test_eddy_on_defaults_to_72(self):
        assert ES.EDDY_DEFAULT_STEPS_PER_PERIOD == 72
        assert ES.resolve_steps_per_period(None, eddy=True) == 72
        assert ES.resolve_steps_per_period("", eddy=True) == 72

    def test_eddy_off_keeps_the_route_default(self):
        assert ES.resolve_steps_per_period(None, eddy=False) == 60

    @pytest.mark.parametrize("asked", [1, 12, 36, 40, 64, 72, 96, 500])
    @pytest.mark.parametrize("eddy", [True, False])
    def test_a_named_count_is_always_honoured(self, asked, eddy):
        assert ES.resolve_steps_per_period(asked, eddy=eddy) == asked
        assert ES.resolve_steps_per_period(float(asked), eddy=eddy) == asked
        assert ES.resolve_steps_per_period(str(asked), eddy=eddy) == asked

    @pytest.mark.parametrize("bad", [0, -4, 1.5, "abc", True, float("nan"),
                                     float("inf"), [36]])
    def test_a_malformed_count_is_refused_loudly(self, bad):
        with pytest.raises(ValueError, match="n_steps_per_period"):
            ES.resolve_steps_per_period(bad, eddy=True)


class TestTransientRoute:
    def test_signature_leaves_the_count_unset_so_eddy_can_decide(self):
        from motor_ai_sim.routes.simulation import get_fem_transient
        p = inspect.signature(get_fem_transient).parameters["n_steps_per_period"]
        assert p.default is None

    def test_a_malformed_count_is_a_422_before_any_solve(self):
        from motor_ai_sim.routes.simulation import get_fem_transient
        with pytest.raises(HTTPException) as ei:
            get_fem_transient(n_steps_per_period=0, eddy=True)
        assert ei.value.status_code == 422
        assert "n_steps_per_period" in str(ei.value.detail)


class TestCoupledLoop:
    def test_absent_count_gets_the_eddy_default(self):
        from motor_ai_sim.routes.coupled import _with_eddy_steps
        assert _with_eddy_steps({})["n_steps_per_period"] == 72
        assert _with_eddy_steps({"n_steps_per_period": None})["n_steps_per_period"] == 72

    @pytest.mark.parametrize("asked", [2, 36, 40, 72, 144])
    def test_named_count_is_kept(self, asked):
        from motor_ai_sim.routes.coupled import _with_eddy_steps
        body = {"n_steps_per_period": asked, "I_phase_rms": 10.0}
        out = _with_eddy_steps(body)
        assert out["n_steps_per_period"] == asked
        assert body == {"n_steps_per_period": asked, "I_phase_rms": 10.0}  # not mutated

    def test_malformed_count_is_refused_by_name(self):
        from motor_ai_sim.routes.coupled import _with_eddy_steps
        with pytest.raises(HTTPException) as ei:
            _with_eddy_steps({"n_steps_per_period": 2.5})
        assert ei.value.status_code == 422
        assert ei.value.detail["invalid_parameters"] == [{"field": "n_steps_per_period"}]

    def test_the_pwm_inverter_frame_rule_is_not_touched(self):
        from motor_ai_sim.routes.coupled import _with_eddy_steps
        out = _with_eddy_steps({"inverter": {"n_steps_per_period": 352}})
        assert out["inverter"] == {"n_steps_per_period": 352}

    def test_the_entry_points_apply_it(self):
        from motor_ai_sim.routes import coupled as C
        for fn in (C.run, C.constants_20c, C.continuous_rating):
            src = inspect.getsource(inspect.unwrap(fn))
            assert "_with_eddy_steps(body)" in src, fn.__name__


class TestOptimizerScreening:
    """Screening keeps its own count while the tab holds only the eddy default."""

    def _plan_with(self, monkeypatch, **sim_over):
        from fastapi.testclient import TestClient
        from motor_ai_sim.api import app
        from motor_ai_sim.routes import optimization as O
        real = O.get_config()

        def fake():
            cfg = copy.deepcopy(real)
            cfg.setdefault("simulation", {}).update(sim_over)
            return cfg
        monkeypatch.setattr(O, "get_config", fake)
        r = TestClient(app).post("/api/optimization/auto/plan",
                                 json={"max_ripple_pct": 5.0, "budget_evals": 0})
        assert r.status_code == 200, r.text
        return r.json()["plan"]["eval"]

    def test_eddy_default_on_the_tab_screens_at_the_optimizers_own_count(self, monkeypatch):
        from motor_ai_sim.routes import optimization as O
        ev = self._plan_with(monkeypatch, steps_per_period=72,
                             steps_per_period_source="eddy_default")
        assert ev["steps_per_period"] == O.OPT_SCREEN_STEPS_PER_PERIOD == 40
        assert ev["steps_per_period_source"] == "optimizer"
        assert ev["steps_per_period_tab"] == 72

    @pytest.mark.parametrize("src", ["user", None])
    @pytest.mark.parametrize("asked", [24, 72, 96])
    def test_a_count_the_user_picked_is_obeyed(self, monkeypatch, src, asked):
        ev = self._plan_with(monkeypatch, steps_per_period=asked,
                             steps_per_period_source=src)
        assert ev["steps_per_period"] == asked
        assert ev["steps_per_period_source"] == "simulation_tab"


class TestConfigPatch:
    def test_source_is_validated(self):
        from pydantic import ValidationError
        from motor_ai_sim.routes.simulation import SimConfigPatch
        assert SimConfigPatch(steps_per_period_source="user").steps_per_period_source == "user"
        assert SimConfigPatch(steps_per_period_source="eddy_default") \
            .steps_per_period_source == "eddy_default"
        with pytest.raises(ValidationError):
            SimConfigPatch(steps_per_period_source="auto")
