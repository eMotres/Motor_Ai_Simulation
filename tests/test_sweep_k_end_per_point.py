"""A sweep / optimization bills each point's end turns at THAT point's coil.

Owner, 2026-10-30: a sweep of motor_length 90/95 × turns × wire_height on a
CIANO56 configuration gave every 90 mm point EXACTLY the efficiency and N·m/kg
of its 95 mm twin, and on the Pareto chart the 90 mm points disappeared under
the 95 mm ones.  The request carried the Simulation field's k_end (the built
coil's number for the CURRENT 95 mm stack) as a pin, so every point's end-turn
copper was k_end·L — it scaled with the stack, and a 2-D machine whose torque,
iron loss, copper loss and mass all scale with L has the same η and N·m/kg at
any L.  The end turns of a real coil do not get shorter with the stack.

Pinned here:
  * refine_proc with k_end = 0 bills each candidate at its own built coil, in
    the loss AND the mass, and says so on the point (k_end_used / k_end_source);
  * a length sweep then gives different η and N·m/kg per length, and a typed
    pin still reproduces the flat line it always gave (the user's override);
  * the optimizer pins the saved Simulation k_end only when it was TYPED.

The solver is faked (no FEM): the fake bills copper exactly as the solver does
(field_ops.copper_loss_W with the requested end_winding_factor, 0 = auto) and
scales torque and iron loss with the stack, which is what a 2-D solve does.
"""
from __future__ import annotations

import json
import math
import types

import pytest

from motor_ai_sim.optimization import refine_proc
from motor_ai_sim.optimization.design_eval import build_params
from motor_ai_sim.simulation.field_ops import copper_loss_W

from tests.test_optimizer_honesty import CFG, GEO, _raw

L0 = float(GEO["motor_length"])          # the fixture's stack, mm


@pytest.fixture()
def scaling_solver(monkeypatch):
    """A fake solver whose copper loss is the solver's own formula."""
    import motor_ai_sim.config as _cfgmod
    monkeypatch.setattr(_cfgmod, "get_config", lambda *a, **k: CFG)

    def run(cap, args):
        geo = {**GEO, **json.loads(args["geo"])}
        s = float(geo["motor_length"]) / L0          # 2-D: per unit length × L
        p_cu, k_used, _r = copper_loss_W(
            build_params(geo), geo, float(args["I_phase_rms"]), 1,
            coil_temp_c=float(args["coil_temp_c"]),
            end_winding_factor=float(args["end_winding_factor"]))
        raw = dict(_raw(converged=True))
        n = raw["n_steps"]
        raw.update({
            "T_avg_Nm": raw["T_avg_Nm"] * s,
            "P_cu_W": [p_cu] * n, "P_cu_dc_W": p_cu,
            "P_fe_W": [x * s for x in raw["P_fe_W"]],
            "end_winding_factor": k_used,
        })
        return {"ok": True, "result": types.SimpleNamespace(raw=raw)}

    monkeypatch.setattr(refine_proc, "_kernel",
                        lambda: types.SimpleNamespace(run=run))


def _point(length_mm: float, k_end: float = 0.0) -> dict:
    return refine_proc.run_one({"motor_length": length_mm}, current_a=60.0,
                               steps=12, coil_temp_c=120.0,
                               end_winding_factor=k_end)


def test_a_length_sweep_gives_each_length_its_own_eta_and_torque_density(scaling_solver):
    """The owner's case: two stacks, per-point coil → two different points."""
    a, b = _point(0.9 * L0), _point(0.95 * L0)
    assert a["k_end_source"] == b["k_end_source"] == "coil"
    # the same end turns on a shorter stack → a LARGER k_end
    assert a["k_end_used"] > b["k_end_used"] > 1.0
    # ...whose end-turn length is the same coil: (k − 1)·L is constant
    assert (a["k_end_used"] - 1.0) * 0.9 == pytest.approx(
        (b["k_end_used"] - 1.0) * 0.95, rel=2e-3)
    # the shorter stack carries relatively more end-turn copper: lower η
    assert a["efficiency"] < b["efficiency"]
    assert a["torque_per_mass_Nm_kg"] != pytest.approx(
        b["torque_per_mass_Nm_kg"], rel=1e-3)


def test_a_typed_pin_is_still_the_same_k_end_on_every_point(scaling_solver):
    """The user's override survives: one k_end on every point, said so."""
    k = 1.359
    a, b = _point(0.9 * L0, k), _point(0.95 * L0, k)
    assert a["k_end_source"] == b["k_end_source"] == "typed"
    assert a["k_end_used"] == b["k_end_used"] == pytest.approx(k)
    # ...which is the flat line the owner saw: everything scales with L
    assert a["efficiency"] == pytest.approx(b["efficiency"], abs=2e-5)


def test_the_mass_is_billed_at_the_same_k_end_as_the_loss(scaling_solver):
    """Copper mass and copper loss share the point's k_end (one source)."""
    from motor_ai_sim.masses import compute_masses
    geo = {**GEO, "motor_length": 0.9 * L0}
    pt = _point(0.9 * L0)
    want = compute_masses(build_params(geo), geo, k_end=pt["k_end_used"])["total"]
    assert pt["mass_total_kg"] == pytest.approx(want, abs=1e-3)
    pinned = compute_masses(build_params(geo), geo, k_end=1.0)["total"]
    assert not math.isclose(pt["mass_total_kg"], pinned, abs_tol=1e-4)


@pytest.mark.parametrize("sim,want", [
    ({"end_winding_factor": 1.359}, 0.0),                       # saved before the flag: auto-filled
    ({"end_winding_factor": 1.359, "end_winding_typed": False}, 0.0),
    ({"end_winding_factor": 1.5, "end_winding_typed": True}, 1.5),
    ({"end_winding_factor": 0.0, "end_winding_typed": True}, 0.0),
    ({"end_winding_factor": "x", "end_winding_typed": True}, 0.0),
    ({"end_winding_factor": float("nan"), "end_winding_typed": True}, 0.0),
    ({"end_winding_factor": 1.5, "end_winding_typed": "yes"}, 0.0),
])
def test_the_optimizer_pins_only_a_typed_k_end(sim, want):
    from motor_ai_sim.routes.optimization import end_winding_pin
    assert end_winding_pin(sim) == want


def test_the_simulation_config_carries_the_typed_flag():
    from motor_ai_sim.routes.simulation import SimConfigPatch
    assert SimConfigPatch(end_winding_typed=True).end_winding_typed is True
    assert SimConfigPatch().end_winding_typed is None
