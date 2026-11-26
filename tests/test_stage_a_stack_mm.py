"""A Stage A passport carries the stack length it was solved at, at the top level (where the store entry and the length-aware
lookup read it), and the adopt route fills a missing one from the passport's own machine block / reference row."""
from __future__ import annotations

import inspect

import pytest

from motor_ai_sim import end3d_store
from motor_ai_sim.end3d_verdict import stack_mm_of_stage_a
from motor_ai_sim.routes import simulation as S
from motor_ai_sim.routes import static3d as R3
from motor_ai_sim.simulation.static3d import end_effect

DESC = "24s/28p OD 200"


def _sa(**over):
    sa = {"geometry_fingerprint": "fp1", "k_flux": 0.95, "k_flux_self": 0.95, "generated_utc": "2026-11-25T00:00:00Z",
          "machine": {"num_slots": 24, "num_poles": 28, "stator_od_mm": 200.0, "stack_mm": 40.0},
          "l_stack_curve": [{"factor": 0.75, "stack_mm": 30.0, "k_flux": 0.94, "k_flux_self": 0.94},
                            {"factor": 1.0, "stack_mm": 40.0, "k_flux": 0.95, "k_flux_self": 0.95}],
          "two_d": None, "model": {}}
    sa.update(over)
    return sa


def test_run_stage_a_writes_the_stack_length_at_the_top_level():
    src = inspect.getsource(end_effect.run_stage_a)
    assert "stack_mm=section.stack_mm," in src and src.index("stack_mm=section.stack_mm,") < src.index("machine=dict(")


def test_the_stack_length_helper_prefers_the_results_own_then_the_machine_then_the_reference_row():
    assert stack_mm_of_stage_a(_sa(stack_mm=55.0)) == 55.0
    assert stack_mm_of_stage_a(_sa()) == 40.0
    sa = _sa()
    del sa["machine"]["stack_mm"]
    assert stack_mm_of_stage_a(sa) == 40.0                                  # the factor-1 row
    sa["l_stack_curve"] = [{"factor": 0.75, "stack_mm": 30.0}]
    assert stack_mm_of_stage_a(sa) is None                                  # never a guess from another row
    assert stack_mm_of_stage_a({"stack_mm": "x", "machine": None}) is None and stack_mm_of_stage_a(None) is None
    assert stack_mm_of_stage_a(_sa(stack_mm=-3.0)) == 40.0


def test_adopt_fills_a_missing_stack_length_and_keeps_every_k_flux_value():
    with_all = R3.adopt_entry(_sa())[1]
    sa = _sa()
    del sa["machine"]["stack_mm"]
    filled = R3.adopt_entry(sa)[1]
    assert with_all["stack_mm"] == 40.0 and filled["stack_mm"] == 40.0
    assert filled["k_flux"] == with_all["k_flux"] == 0.95 and filled["k_flux_vs_L"] == with_all["k_flux_vs_L"]
    top = R3.adopt_entry(_sa(stack_mm=40.0))[1]
    assert {k: v for k, v in top.items() if k not in ("adopted_utc",)} == {k: v for k, v in with_all.items() if k not in ("adopted_utc",)}


def test_a_stage_a_shaped_entry_is_a_curve_point_for_another_length(monkeypatch):
    # an entry written from the Stage A output: stack_mm at the top level, as run_stage_a now writes it
    entry = {"k_flux": 0.95, "machine": DESC, "adopted_utc": "2026-11-25T00:00:00Z", "stack_mm": 40.0, "k_flux_vs_L": None}
    monkeypatch.setattr(end3d_store, "load", lambda: {"fpA": dict(entry)})
    monkeypatch.setattr(S, "_machine_desc", lambda geo=None: DESC)
    monkeypatch.setattr(S, "_geo_stack_mm", lambda geo=None: 41.5)              # another length of the same machine, within 5 %
    got = S._end3d_lookup("fpB", {})
    assert got and got["k_flux"] == 0.95 and got["inherited"] and got["inherited_stack_basis"] == "point"
    # the same entry WITHOUT the length is no curve point at all (the defect)
    noL = dict(entry)
    noL["stack_mm"] = None
    monkeypatch.setattr(end3d_store, "load", lambda: {"fpA": noL})
    assert S._end3d_lookup("fpB", {}) is None
