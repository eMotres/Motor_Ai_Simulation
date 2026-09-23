"""Execute production payload expressions, including nullable legacy noise."""
import ast
import json
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def payload_expressions():
    root = Path(__file__).resolve().parents[1] / "src" / "motor_ai_sim"
    output = {}
    for name, path in (("transient", root / "simulation" / "fem_solver_2d.py"),
                       ("summary", root / "routes" / "simulation.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        dictionaries = [node for node in ast.walk(tree) if isinstance(node, ast.Dict)
                        and any(isinstance(key, ast.Constant)
                                and key.value == "T_noise_floor_pct" for key in node.keys)]
        assert len(dictionaries) == 1
        output[name] = {key.value: compile(ast.Expression(value), str(path), "eval")
                        for key, value in zip(dictionaries[0].keys, dictionaries[0].values)
                        if isinstance(key, ast.Constant) and key.value in (
                            "T_noise_floor_pct", "torque_filter_applied", "T_ripple_filt_pct",
                            "T_em_filt_Nm", "T_avg_maxwell_Nm", "T_ripple_pct",
                            "T_ripple_raw_pct",
                            "T_ripple_pp_Nm", "T_ripple_raw_pp_Nm",
                            "T_ripple_pct_available", "T_ripple_pct_reason")}
    return output


def test_transient_legacy_aliases_are_raw_without_noise_claim(payload_expressions):
    namespace = {"_T_report": [9., 10., 11.], "Trip_raw": 20.,
                 "T_maxwell_avg": 1.234567890123e-5, "T_ripple_pp": 2.}
    actual = {key: eval(value, {}, namespace)
              for key, value in payload_expressions["transient"].items()}
    assert actual == {"T_em_filt_Nm": [9., 10., 11.], "T_ripple_filt_pct": 20.,
                      "T_noise_floor_pct": None, "torque_filter_applied": False,
                      "T_avg_maxwell_Nm": 1.234567890123e-5,
                      "T_ripple_pct": 20., "T_ripple_raw_pct": 20.,
                      "T_ripple_pp_Nm": 2.,
                      "T_ripple_raw_pp_Nm": 2., "T_ripple_pct_available": True,
                      "T_ripple_pct_reason": None}


@pytest.mark.parametrize("old_noise", [None, 12.5])
def test_summary_accepts_nullable_noise_and_does_not_reuse_old_filtered_ripple(
        payload_expressions, old_noise):
    namespace = {"sbres": {"T_ripple_raw_pct": 20., "T_ripple_pct": 20.,
                            "T_ripple_filt_pct": 0., "T_noise_floor_pct": old_noise,
                            "T_ripple_pp_Nm": 2., "T_ripple_raw_pp_Nm": 2.,
                            "T_ripple_pct_available": True, "T_ripple_pct_reason": None},
                 "_summary_ripple_pct": lambda v: None if v is None else round(abs(float(v)), 1)}
    actual = {key: eval(value, {}, namespace)
              for key, value in payload_expressions["summary"].items()}
    assert actual == {"T_ripple_filt_pct": 20., "T_noise_floor_pct": None,
                      "torque_filter_applied": False, "T_ripple_pct": 20.,
                      "T_ripple_raw_pct": 20.,
                      "T_ripple_pp_Nm": 2., "T_ripple_raw_pp_Nm": 2.,
                      "T_ripple_pct_available": True, "T_ripple_pct_reason": None}


def test_zero_mean_payload_keeps_absolute_ripple_and_nullable_percent(payload_expressions):
    transient = {"_T_report": [.2, -.1, -.1], "Trip_raw": None,
                 "T_ripple_pp": .3, "T_maxwell_avg": 0.}
    raw = {key: eval(expr, {}, transient)
           for key, expr in payload_expressions["transient"].items()}
    assert raw["T_ripple_pct"] is None
    assert raw["T_ripple_pp_Nm"] == .3
    assert raw["T_ripple_pct_available"] is False
    summary = {"sbres": raw,
               "_summary_ripple_pct": lambda v: None if v is None else round(abs(float(v)), 1)}
    card = {key: eval(expr, {}, summary)
            for key, expr in payload_expressions["summary"].items()}
    assert card["T_ripple_pct"] is None
    assert card["T_ripple_raw_pct"] is None
    assert card["T_ripple_pp_Nm"] == .3
    json.dumps(card, allow_nan=False)
