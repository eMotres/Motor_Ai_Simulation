# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Astra re-audit #2 R8 repro, flipped: the route budget counts the bearing-node friction.

This test parses and evaluates the arithmetic expressions in ``routes/thermal``;
it never imports or invokes the thermal route or either solver.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path


ROUTE = (Path(__file__).resolve().parents[1] / "src" / "motor_ai_sim"
         / "routes" / "thermal.py")


def _function_tree() -> ast.FunctionDef:
    tree = ast.parse(ROUTE.read_text(encoding="utf-8"), filename=str(ROUTE))
    return next(node for node in tree.body
                if isinstance(node, ast.FunctionDef)
                and node.name == "solve_thermal_field")


def _assigned(name: str) -> ast.expr:
    for node in ast.walk(_function_tree()):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == name):
            return node.value
    raise AssertionError("assignment %r not found in %s" % (name, ROUTE))


def _dict_value(assignment: str, key: str) -> ast.expr:
    value = _assigned(assignment)
    assert isinstance(value, ast.Dict), assignment
    for k, v in zip(value.keys, value.values):
        if isinstance(k, ast.Constant) and k.value == key:
            return v
    raise AssertionError("%s[%r] not found" % (assignment, key))


def _eval(expr: ast.expr, values: dict) -> object:
    return eval(compile(ast.Expression(expr), str(ROUTE), "eval"),
                {"round": round, "float": float}, values)


def test_bearing_node_watts_are_in_the_route_whole_system_budget():
    """R8, flipped: the element budget stays 10 W, the whole solved system reports 12 W."""
    values = {
        "_p_brg_in": 2.0,
        "_brg_modelled": False,
        "_brg_at_nodes": True,
        "_p_wind_gap": 0.0,
        "_wind_modelled": False,
        "_p_wind_faces": 0.0,
    }
    p_into = _eval(_dict_value("mech_losses", "P_mech_into_map_W"), values)
    mech_losses = {"P_mech_into_map_W": p_into}
    p_mech_in = _eval(_assigned("_p_mech_in"), {"mech_losses": mech_losses})

    budget_values = {
        "_gen_w": 10.0,              # element-domain q integral
        "_p_brg_in": 2.0,            # lumped bearing-node sources
        "_brg_modelled": False,       # closed shaft: no element deposition
        "_p_brg_dropped": 0.0,        # correctly recognized as solved
        "_p_wind_gap": 0.0,
        "_wind_modelled": False,
        "_p_wind_dropped": 0.0,
        "_p_wind_faces": 0.0,
        "_p_mech_in": p_mech_in,
        "_brg_at_nodes": True,
        "_p_node_gen_w": _eval(_assigned("_p_node_gen_w"), values),
        "_bn_sources": {"to_rotor_W": 1.1, "to_stator_W": 0.6, "to_air_W": 0.3},
    }
    reported = {
        key: _eval(_dict_value("heat_budget", key), budget_values)
        for key in ("losses_W", "bearing_friction_W", "mech_loss_in_map_W",
                    "losses_at_bearing_nodes_W", "losses_total_W")
    }
    evidence = {
        "element_domain_generated_W": 10.0,
        "bearing_node_generated_W": 2.0,
        "whole_solved_system_generated_W": 12.0,
        "mech_losses_P_mech_into_map_W": p_into,
        "reported": reported,
        "omitted_from_losses_W": 12.0 - reported["losses_W"],
    }
    assert p_into == 0.0                     # the ELEMENT-domain map: unchanged
    assert reported == {
        "losses_W": 10.0,
        "bearing_friction_W": 2.0,
        "mech_loss_in_map_W": 0.0,
        "losses_at_bearing_nodes_W": 2.0,
        "losses_total_W": 12.0,
    }
    assert 12.0 - reported["losses_total_W"] == 0.0
