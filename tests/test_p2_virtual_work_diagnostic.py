"""Small, no-FEM guards for the uncertified virtual-work torque diagnostic."""

import ast
from pathlib import Path

import numpy as np

from motor_ai_sim.simulation.fem_solver_2d import (
    _p2_virtual_work_ineligible_reason,
    _p2_virtual_work_torque,
)


class _PointwiseOperator:
    calls = 0

    def Kpw(self, field):
        self.calls += 1
        return np.diag([3.0, 4.0]), None


class _Motion:
    calls = 0

    def motion_derivative(self, field, shift):
        assert shift == -2
        self.calls += 1
        return np.array([0.5, -1.0])


def test_virtual_work_uses_full_residual_negative_sign_and_sector_stack_scale():
    operator = _PointwiseOperator()
    motion = _Motion()
    # r=[5,2], dA/dtheta=[0.5,-1], r.dA=0.5 per mechanical radian.
    torque = _p2_virtual_work_torque(
        operator, np.array([2.0, 1.0]), np.array([1.0, 2.0]),
        motion, -2, sector_count=4, stack_length_m=0.02)
    assert torque == -0.04
    assert operator.calls == motion.calls == 1


def test_only_accepted_imposed_current_pointwise_newton_is_eligible():
    state = dict(eddy=False, voltage_drive=False, demag=False,
                 frozen_nu=False, saturable=True, newton_ok=True)
    assert _p2_virtual_work_ineligible_reason(**state) is None
    for flag, reason in (
        ("eddy", "coupled_eddy_field"),
        ("voltage_drive", "voltage_drive_field"),
        ("demag", "irreversible_demagnetisation"),
        ("frozen_nu", "frozen_permeability"),
        ("saturable", "linear_path_not_certified"),
        ("newton_ok", "pointwise_newton_not_accepted"),
    ):
        altered = dict(state)
        altered[flag] = flag not in ("saturable", "newton_ok")
        assert _p2_virtual_work_ineligible_reason(**altered) == reason


def test_virtual_work_series_is_post_solve_aligned_and_trimmed():
    source = (Path(__file__).resolve().parents[1] / "src" / "motor_ai_sim"
              / "simulation" / "fem_solver_2d.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    solver = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                  and node.name == "fem_transient_sliding_band")
    assert any(isinstance(dec, ast.Name) and dec.id == "_pardiso_scope"
               for dec in solver.decorator_list)
    loops = [node for node in ast.walk(solver) if isinstance(node, ast.While)]
    frame_loop = next(node for node in loops if any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and isinstance(n.func.value, ast.Name) and n.func.value.id == "_T2"
        and n.func.attr == "append" for n in ast.walk(node)))
    append_lines = {}
    for node in ast.walk(frame_loop):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "append"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in ("_T2", "_T_vw", "_T_vw_reason")):
            append_lines[node.func.value.id] = node.lineno
    assert set(append_lines) == {"_T2", "_T_vw", "_T_vw_reason"}
    assert append_lines["_T_vw"] < append_lines["_T2"]
    assert append_lines["_T_vw_reason"] < append_lines["_T2"]

    trimmed = next(node.value for node in ast.walk(solver)
                   if isinstance(node, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "_v2_lists"
                           for t in node.targets))
    names = {node.id for node in ast.walk(trimmed) if isinstance(node, ast.Name)}
    assert {"_T2", "_T_vw", "_T_vw_reason"} <= names
    history = next(node.value for node in ast.walk(solver)
                   if isinstance(node, ast.Assign)
                   and any(isinstance(t, ast.Name)
                           and t.id == "_p2_scalar_series" for t in node.targets))
    assert "torque_virtual_work_diagnostic_Nm" in [
        key.value for key in history.keys if isinstance(key, ast.Constant)]
