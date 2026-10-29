"""The Simulation tab's MAGNET TEMPERATURE field, end to end (owner 2026-10-29:
«сделать так, чтобы можно было задавать температуру магнитов в расчёте … а это
не работает» — "make it possible to set the magnet temperature in the calculation … it doesn't work").

Pinned with a stub in place of the solve (no FEM):
  * the typed temperature reaches the solver;
  * the run summary says which magnet temperature it used and where it came
    from (typed | card);
and, on the card itself, that an N52UH card at 90 °C is stronger than at its
own 150 °C (Br and the knee), which is what makes the torque rise.
"""
from __future__ import annotations

import pytest

from tests.test_run_ledger import RUN, ledger_dir, sim, solver  # noqa: F401 (fixtures)


def test_typed_magnet_temperature_reaches_the_solver(sim, solver):
    res = sim.get_fem_transient(**RUN, magnet_temp_c=90.0)
    assert solver[-1]["magnet_temp_c"] == 90.0
    s = res["summary"]
    assert s["magnet_temp_used_c"] == 90.0
    assert s["magnet_temp_source"] == "typed"


def test_empty_field_is_the_cards_own_temperature(sim, solver):
    res = sim.get_fem_transient(**RUN)
    assert solver[-1].get("magnet_temp_c") is None
    s = res["summary"]
    assert s["magnet_temp_source"] == "card"
    assert "magnet_card" not in s or s.get("magnet_temp_used_c") == s.get("magnet_card_temp_c")


@pytest.fixture
def n52uh():
    """The request names N52UH_150C as its magnet (the per-request override the
    browser sends with every solve), and drops it again afterwards."""
    from motor_ai_sim.material_context import set_request_materials
    set_request_materials({"assignment": {"magnet": "N52UH_150C"}})
    yield
    set_request_materials(None)


def test_the_stamp_names_the_card_and_the_magnet_it_became(sim, solver, n52uh):
    hot = sim.get_fem_transient(**RUN)["summary"]
    assert hot["magnet_card"] == "N52UH_150C"
    assert hot["magnet_temp_source"] == "card"
    assert hot["magnet_temp_used_c"] == hot["magnet_card_temp_c"] == 150.0
    cool = sim.get_fem_transient(**RUN, magnet_temp_c=90.0)["summary"]
    assert cool["magnet_temp_source"] == "typed" and cool["magnet_temp_used_c"] == 90.0
    # 60 K cooler: Br up (alpha -0.14 %/K), the knee further out (beta -1.5 %/K)
    assert cool["magnet_Br_used_T"] > hot["magnet_Br_used_T"]
    assert cool["magnet_knee_used_kA_m"] < hot["magnet_knee_used_kA_m"]


def _netgen_ok() -> bool:
    try:
        import netgen  # noqa: F401
        return True
    except Exception:   # noqa: BLE001 — blocked DLLs on a Windows workstation
        return False


@pytest.mark.slow
@pytest.mark.skipif(not _netgen_ok(), reason="the geometry-driven mesh needs netgen")
def test_torque_at_90c_exceeds_torque_at_150c_for_an_n52uh_card():
    """The physics the field exists for: the same machine, the same current,
    the N52UH card solved at 90 °C instead of its own 150 °C — more torque."""
    from motor_ai_sim.material_context import set_request_materials
    from motor_ai_sim.simulation.fem_solver_2d import fem_transient_sliding_band
    from tests.test_physics_regression import CASES, CONNECTION, GEO_30MM, OVERRIDE, RPM
    case = dict(CASES["p2_load"], n_steps_per_period=6)
    ov = {**OVERRIDE, "assignment": {**OVERRIDE["assignment"], "magnet": "N52UH_150C"}}
    ov["materials"] = {k: v for k, v in (OVERRIDE.get("materials") or {}).items()
                       if k != OVERRIDE["assignment"].get("magnet")}
    set_request_materials(ov)
    try:
        T = {t: float(fem_transient_sliding_band(
                geo_override=dict(GEO_30MM), rpm=RPM, connection=CONNECTION,
                magnet_temp_c=t, **case)["T_avg_Nm"]) for t in (150.0, 90.0)}
    finally:
        set_request_materials(None)
    assert T[90.0] > T[150.0] * 1.02, T
