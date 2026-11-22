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
