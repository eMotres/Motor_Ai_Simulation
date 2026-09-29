"""Undefined percentage must never look like a quiet optimized design."""

import json
import ast
from pathlib import Path

from motor_ai_sim.routes import optimization as O


def test_undefined_ripple_is_not_eligible_and_is_bad_for_scoring(monkeypatch):
    metrics = {"T_ripple_pct": None, "torque_per_mass_Nm_kg": 10.,
               "efficiency": .9, "V_peak": 50.}
    point = O._point_from_eval({"ok": True, "res": metrics}, {}, 0., 0, 0, 5.)
    assert point["feasible"] is True
    assert point["eligible"] is False
    assert point["T_ripple_pct"] is None
    monkeypatch.setitem(O._RIPPLE_PEN_LAM, "v", 0.)
    cost, score = O._descent_cost(metrics, {"efficiency": .9,
                                          "torque_per_mass_Nm_kg": 10.},
                                  5., 1., 1., 0.)
    assert cost > 100000 and score > 0
    json.dumps(point, allow_nan=False)


def test_undefined_ripple_ramps_penalty_without_nonfinite_event(monkeypatch):
    monkeypatch.setitem(O._RIPPLE_PEN_LAM, "v0", 1.)
    monkeypatch.setitem(O._RIPPLE_PEN_LAM, "v", 1.)
    event = O._ripple_ramp_step({"T_ripple_pct": None}, 5., 1)
    assert event["ripple"] is None
    assert event["to"] > event["from"]
    json.dumps(event, allow_nan=False)


def test_refine_payload_preserves_undefined_percent_and_absolute_ripple():
    path = (Path(__file__).resolve().parents[1] / "src/motor_ai_sim/optimization/refine_proc.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    payload = next(node for node in ast.walk(tree) if isinstance(node, ast.Dict)
                   and any(isinstance(key, ast.Constant) and key.value == "T_ripple_pct"
                           for key in node.keys)
                   and any(isinstance(key, ast.Constant) and key.value == "T_ripple_pp_Nm"
                           for key in node.keys))
    selected = {key.value: compile(ast.Expression(value), str(path), "eval")
                for key, value in zip(payload.keys, payload.values)
                if isinstance(key, ast.Constant) and key.value in
                ("T_ripple_pct", "T_ripple_pp_Nm")}
    d = {"T_ripple_pct": None, "T_ripple_pp_Nm": .3}
    result = {key: eval(expr, {}, {"d": d}) for key, expr in selected.items()}
    assert result == d
