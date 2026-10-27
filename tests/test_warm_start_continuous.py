"""The warm start of a peak pull when the configuration names no "rated" duty (owner 2026-10-27).

An eVTOL's emergency peak follows cruise, so the pull starts from the continuous duty's converged state.  Under test:
``routes.coupled._rated_state_c`` takes a "rated…" duty first; otherwise a CONTINUOUS duty (saved as one, or whose coupled
record holds its current), the one with the hottest stored winding; never a short-time duty, never the duty being solved.
"""
from __future__ import annotations

import pytest

from motor_ai_sim.routes import coupled as RC


def _map(w):
    return {"components": {"winding": {"avg": w}, "stator": {"avg": w - 10}, "rotor": {"avg": w - 20},
                           "magnet": {"avg": w - 25}}}


HOLDS = {"time_to_limit": {"within_limits": True}}
SHORT = {"time_to_limit": {"within_limits": False, "starts": {"cold": {"time_to_limit_s": 47.0,
                                                                        "limiting_part": "winding"}},
                           "limits_c": {"winding": 200.0}}}


@pytest.fixture()
def store(monkeypatch):
    recs = {}
    from motor_ai_sim import duty_results as DR
    monkeypatch.setattr(DR, "get", lambda die, cfg: recs)
    return recs


def test_a_continuous_duty_is_the_warm_start_when_no_rated_one(store):
    store.update({"cruise": {"thermal": _map(157.0), "coupled": HOLDS},
                  "peak": {"thermal": _map(200.0), "coupled": SHORT}})
    duties = [{"name": "cruise"}, {"name": "peak"}]
    state, name, why = RC._rated_state_c("D", "C", duties, "peak")
    assert name == "cruise" and state["winding"] == 157.0 and state["magnet"] == 132.0
    assert "cruise" in why


def test_the_hottest_continuous_duty_wins_and_short_time_ones_never(store):
    store.update({"cruise": {"thermal": _map(140.0), "coupled": HOLDS},
                  "takeoff": {"thermal": _map(165.0)},                      # saved as continuous, no coupled record
                  "landing": {"thermal": _map(190.0), "coupled": SHORT},    # short-time: not a warm start
                  "peak": {"thermal": _map(200.0), "coupled": SHORT}})
    duties = [{"name": "cruise"}, {"name": "takeoff", "duty_class": "continuous"}, {"name": "landing"},
              {"name": "peak"}]
    _state, name, _why = RC._rated_state_c("D", "C", duties, "peak")
    assert name == "takeoff"


def test_a_rated_duty_still_comes_first(store):
    store.update({"rated": {"thermal": _map(120.0), "coupled": HOLDS},
                  "cruise": {"thermal": _map(157.0), "coupled": HOLDS}})
    _state, name, _why = RC._rated_state_c("D", "C", [{"name": "cruise"}, {"name": "rated"}, {"name": "peak"}], "peak")
    assert name == "rated"


def test_no_continuous_duty_says_why(store):
    store.update({"peak": {"thermal": _map(200.0), "coupled": SHORT}})
    state, name, why = RC._rated_state_c("D", "C", [{"name": "peak"}, {"name": "boost"}], "peak")
    assert state == {} and name == "" and "no rated or continuous duty" in why


def test_the_solved_duty_is_never_its_own_warm_start(store):
    store.update({"cruise": {"thermal": _map(157.0), "coupled": HOLDS}})
    state, name, _why = RC._rated_state_c("D", "C", [{"name": "cruise"}], "cruise")
    assert state == {} and name == ""
