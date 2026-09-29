"""What is left of the air gap once the rotor has grown into it.

User 2026-09-10: *"не забудь добавить в отчёт, как меняется зазор"* — the growth
alone was never the answer, because the clearance it eats is the air gap MINUS
the band, two different geometry fields, and the reader was left to subtract.

Pinned here is the DEFINITION, not a value:

  * the clearance is measured off the DRAWN section — the smallest radius
    anywhere on the stator (a tooth tip, which is what the rotor touches first)
    against the rotor's own largest — so a chamfer, a slot opening or a stepped
    pole counts, and nothing has to be kept in step with a parameter name;
  * it is the rotor's OUTERMOST surface, the band's top where there is a band;
  * the three numbers close: clearance = closed + remaining;
  * and it is absent, never zero, when the section carried no stator.
"""
from __future__ import annotations

import math

import pytest


@pytest.fixture(scope="module")
def sandbox_result():
    """The FROZEN 40 mm section — WITH its stator, which is the point here —
    solved straight through the solver, never through a persisting route."""
    import motor_ai_sim.simulation.mechanical.rotor_stress as rs
    from motor_ai_sim.cadquery_geometry import CadQueryMotor

    from tests.test_mechanical_contact import FROZEN_GEO, FROZEN_MATERIALS

    motor = CadQueryMotor()
    motor.set_parameters(dict(FROZEN_GEO))
    polys = motor.get_2d_polygons(0.0)
    assert polys.get("stator") is not None, "the fixture drew no stator"
    return rs.solve_rotor_stress(
        polys, dict(FROZEN_MATERIALS), 8000.0, 1.2, 0.0,
        stack_length_mm=float(FROZEN_GEO.get("motor_length") or 40),
        mesh_size_mm=1.2, order=2, with_field=False, loads="centrifugal")


def test_a_the_clearance_closes_with_the_growth(sandbox_result):
    """clearance − closed = remaining, on the numbers the tables print."""
    for name, case in sandbox_result["cases"].items():
        ag = case.get("air_gap")
        if ag is None:
            continue
        assert ag["clearance_um"] > 0.0, name
        assert ag["closed_um"] == pytest.approx(
            (case.get("od_growth") or {})["max_um"], rel=1e-12), name
        assert ag["remaining_um"] == pytest.approx(
            ag["clearance_um"] - ag["closed_um"], rel=1e-9), name
        assert ag["closed_pct"] == pytest.approx(
            100.0 * ag["closed_um"] / ag["clearance_um"], rel=1e-9), name


def test_b_the_clearance_is_the_bore_minus_the_rotor(sandbox_result):
    """…and both radii are the ones it says they are."""
    for name, case in sandbox_result["cases"].items():
        ag = case.get("air_gap")
        if ag is None:
            continue
        assert ag["bore_r_mm"] > ag["rotor_r_mm"] > 0.0, name
        assert ag["clearance_um"] == pytest.approx(
            (ag["bore_r_mm"] - ag["rotor_r_mm"]) * 1e3, rel=1e-9), name
        # the same clearance whatever the speed: it is a COLD, drawn number
        first = next(iter(sandbox_result["cases"].values()))
        if first.get("air_gap"):
            assert ag["clearance_um"] == pytest.approx(
                first["air_gap"]["clearance_um"], rel=1e-12), name


def test_c_standstill_closes_nothing(sandbox_result):
    """A rotor at rest with no interference has the whole gap."""
    st = sandbox_result["cases"].get("standstill")
    if not st or not st.get("air_gap"):
        pytest.skip("this fixture has no standstill case")
    ag = st["air_gap"]
    assert ag["closed_um"] == pytest.approx(0.0, abs=1e-6)
    assert ag["remaining_um"] == pytest.approx(ag["clearance_um"], rel=1e-9)


def test_d_a_section_without_a_stator_reports_nothing():
    """Absent, not zero — a machine whose stator was not handed to the solve
    has no clearance to report, and a 0 µm gap would read as a rub."""
    import motor_ai_sim.simulation.mechanical.rotor_stress as rs
    from motor_ai_sim.cadquery_geometry import CadQueryMotor

    from tests.test_mechanical_contact import FROZEN_GEO, FROZEN_MATERIALS

    geo = dict(FROZEN_GEO)
    geo["sleeve_thickness"] = 0.0
    motor = CadQueryMotor()
    motor.set_parameters(geo)
    polys = dict(motor.get_2d_polygons(0.0))
    assert polys.pop("stator", None) is not None, "the fixture drew no stator"
    out = rs.solve_rotor_stress(
        polys, dict(FROZEN_MATERIALS), 8000.0, 1.0, 0.0,
        stack_length_mm=float(geo.get("motor_length") or 40),
        mesh_size_mm=1.2, order=2, with_field=False, case_mode="single",
        loads="centrifugal")
    for case in out["cases"].values():
        assert case.get("air_gap") is None
        # …and the growth itself is still there, so nothing else was lost
        assert math.isfinite((case.get("od_growth") or {})["max_um"])
