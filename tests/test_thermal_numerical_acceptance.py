"""A thermal map is accepted only if it is finite, its boundary loop settled and its heat balance closes
(Astra thermal audit 2026-11-21, F02)."""
from __future__ import annotations

from motor_ai_sim.routes.thermal import numerical_acceptance_of as acc

OK = dict(n_nonfinite=0, iterating=True, passes=2, last_move_c=0.1, tol_c=0.5,
          closures={"rotor_heat_split": 0.0, "stator_heat_split": 1e-9}, generated_w=300.0)


def test_a_settled_finite_closed_map_is_accepted():
    a = acc(**OK)
    assert a["accepted"] is True and a["reasons"] == [] and a["finite"] is True


def test_a_nonfinite_solve_is_named_not_accepted():
    a = acc(**{**OK, "n_nonfinite": 672})
    assert a["accepted"] is False and a["finite"] is False and "672 non-finite" in a["reasons"][0]


def test_the_pass_cap_with_a_moving_boundary_is_not_convergence():
    a = acc(**{**OK, "passes": 4, "last_move_c": 3.2})
    assert a["accepted"] is False and "still moving 3.20 K" in a["reasons"][0]
    assert acc(**{**OK, "iterating": False, "last_move_c": 9.0})["accepted"] is True        # one solve, nothing iterated


def test_an_open_heat_balance_is_named_with_its_tolerance():
    a = acc(**{**OK, "closures": {"rotor_heat_split": 0.0, "stator_heat_split": 54.0}})
    assert a["accepted"] is False and "stator_heat_split does not close: 54.00 W" in a["reasons"][0]
    assert acc(**{**OK, "closures": {"rotor_heat_split": 2.9, "stator_heat_split": 0.0}})["accepted"] is True   # 1 % of 300 W = 3 W


def test_a_nan_or_missing_diagnostic_fails_closed():
    nan = float("nan")
    a = acc(**{**OK, "last_move_c": nan})
    assert a["accepted"] is False and "boundary move is missing" in a["reasons"][0] and a["last_pass_move_c"] is None
    a = acc(**{**OK, "closures": {"rotor_heat_split": nan, "stator_heat_split": 0.0}})
    assert a["accepted"] is False and "rotor_heat_split is missing or not finite" in a["reasons"][0]
    a = acc(**{**OK, "closures": {"rotor_heat_split": None, "stator_heat_split": 0.0}})
    assert a["accepted"] is False
    assert acc(**{**OK, "generated_w": nan})["accepted"] is False
    assert acc(**{**OK, "generated_w": None})["accepted"] is False


def test_consumers_refuse_an_unaccepted_map_and_pass_the_rest():
    import pytest
    from fastapi import HTTPException
    from motor_ai_sim.routes.thermal import refuse_unaccepted_map as gate
    bad = {"ok": False, "numerical_acceptance": acc(**{**OK, "passes": 4, "last_move_c": 3.2})}
    with pytest.raises(HTTPException) as e:
        gate(bad)
    assert e.value.status_code == 422 and "still moving 3.20 K" in e.value.detail
    with pytest.raises(HTTPException):
        gate({"ok": False})
    gate({"ok": True, "numerical_acceptance": acc(**OK)})
    gate({"T_node": [1.0]})            # a stubbed answer without a verdict is not judged
    gate(None)


def test_the_coupled_solve_wrapper_refuses_an_unaccepted_map(monkeypatch):
    import pytest
    from fastapi import HTTPException
    from motor_ai_sim.routes import coupled as cp, thermal as th
    bad = {"ok": False, "numerical_acceptance": acc(**{**OK, "n_nonfinite": 3})}
    monkeypatch.setattr(th, "solve_thermal_field", lambda **kw: bad)
    with pytest.raises(HTTPException) as e:
        cp._thermal_solve({}, {}, coil_temp_c=100.0, magnet_temp_c=None, rpm=1000.0)
    assert e.value.status_code == 422 and "non-finite" in e.value.detail
