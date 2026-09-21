"""Limit speed (SF = 1) — the pure search, and the route.

Owner 2026-09-21: "нужно искать ещё максимальную скорость вращения, на
всякий случай — она будет, когда достигает SF = 1".

Three groups, cheapest first:

  (a) the pure search (``simulation.mechanical.limit_speed.find_limit_speed``)
      against a synthetic SF(rpm) curve — no FEM, milliseconds;
  (b) the route, with ``solve_rotor_stress`` monkeypatched to the same
      synthetic curve — exercises parameter parsing, the ``limit_speed`` block
      on the response, and ``/last``, without a real solve;
  (c) the report row — appears only when the block exists.
"""
from __future__ import annotations

import math

import pytest

from motor_ai_sim.simulation.mechanical.limit_speed import find_limit_speed


# ---------------------------------------------------------------------------
# (a) the pure search
# ---------------------------------------------------------------------------
# SF(rpm) = C / rpm**2 — a clean stand-in for "stress rises with the square of
# speed, safety factor is strength over stress" — with a KNOWN root at
# rpm = sqrt(C / target).

def _sf_curve(root_rpm: float, target: float = 1.0):
    """A synthetic solver: SF(rpm) = (root_rpm**2 * target) / rpm**2."""
    c = root_rpm ** 2 * target
    calls = []

    def solve(rpm: float):
        calls.append(rpm)
        sf = c / (rpm ** 2)
        part = "sleeve" if sf < target else "magnet"
        return sf, part, {part: {"averaged": sf}}

    return solve, calls


def test_brackets_upward_when_the_analysed_speed_is_still_safe():
    root = 20_000.0
    solve, calls = _sf_curve(root)
    rpm0 = 10_000.0
    sf0 = (root ** 2) / (rpm0 ** 2)
    out = find_limit_speed(solve, rpm0, sf0, tol_rel=0.005)
    assert out["reached"] is True
    assert out["rpm_sf1"] == pytest.approx(root, rel=0.01)
    assert out["bracket"][0] <= root <= out["bracket"][1]
    assert out["n_solves"] == len(calls)
    assert out["n_solves"] <= 12
    assert out["sf_at_rpm0"] == pytest.approx(sf0)
    # every candidate the search tried was ABOVE the analysed speed
    assert all(c >= rpm0 for c in calls)


def test_brackets_downward_when_the_analysed_speed_already_fails():
    root = 20_000.0
    solve, calls = _sf_curve(root)
    rpm0 = 30_000.0
    sf0 = (root ** 2) / (rpm0 ** 2)
    assert sf0 < 1.0
    out = find_limit_speed(solve, rpm0, sf0, tol_rel=0.005)
    assert out["reached"] is True
    assert out["rpm_sf1"] == pytest.approx(root, rel=0.01)
    assert all(c <= rpm0 for c in calls)


def test_limiting_part_is_the_one_named_at_the_failing_edge():
    root = 20_000.0
    solve, _calls = _sf_curve(root)
    rpm0 = 10_000.0
    sf0 = (root ** 2) / (rpm0 ** 2)
    out = find_limit_speed(solve, rpm0, sf0, tol_rel=0.01)
    assert out["limiting_part"] == "sleeve"   # sf < target on this curve


def test_not_reached_when_sf_never_crosses_target_within_max_factor():
    def always_safe(rpm: float):
        return 50.0, "magnet", {"magnet": {"averaged": 50.0}}

    out = find_limit_speed(always_safe, 10_000.0, 50.0, max_factor=3.0, max_solves=6)
    assert out["reached"] is False
    assert out["rpm_sf1"] is None
    assert out["bracket"] is None
    assert out["n_solves"] <= 6
    assert "max_factor" in out["note"]


def test_tolerance_narrows_the_bracket_to_within_tol_rel():
    root = 20_000.0
    solve, _calls = _sf_curve(root)
    rpm0 = 10_000.0
    sf0 = (root ** 2) / (rpm0 ** 2)
    out = find_limit_speed(solve, rpm0, sf0, tol_rel=0.02, max_solves=20)
    lo, hi = out["bracket"]
    rel_width = (hi - lo) / (0.5 * (hi + lo))
    assert rel_width <= 0.02 + 1e-9


def test_solve_budget_is_respected():
    root = 20_000.0
    solve, calls = _sf_curve(root)
    rpm0 = 10_000.0
    sf0 = (root ** 2) / (rpm0 ** 2)
    out = find_limit_speed(solve, rpm0, sf0, tol_rel=1e-6, max_solves=4)
    assert out["n_solves"] <= 4
    assert len(calls) == out["n_solves"]


def test_already_at_target_spends_no_solves():
    out = find_limit_speed(lambda rpm: (99.0, "x", {}), 15_000.0, 1.0, tol_rel=0.01)
    assert out["reached"] is True
    assert out["n_solves"] == 0
    assert out["rpm_sf1"] == pytest.approx(15_000.0)
    assert out["log"] == []


def test_omega_squared_cross_check_matches_the_pure_ratio():
    out = find_limit_speed(lambda rpm: (99.0, "x", {}), 10_000.0, 4.0, target=1.0)
    assert out["omega2_extrapolation_rpm"] == pytest.approx(10_000.0 * math.sqrt(4.0))


@pytest.mark.parametrize("rpm0,sf0", [(0.0, 1.0), (-1.0, 1.0), (10_000.0, 0.0),
                                      (10_000.0, -1.0), (float("nan"), 1.0)])
def test_bad_inputs_raise(rpm0, sf0):
    with pytest.raises(ValueError):
        find_limit_speed(lambda rpm: (1.0, None, {}), rpm0, sf0)


# ---------------------------------------------------------------------------
# (b) the route — solve_rotor_stress monkeypatched, no real FEM
# ---------------------------------------------------------------------------

@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from motor_ai_sim.api import app
    return TestClient(app)


def _fake_solve_rotor_stress(root_rpm=20_000.0):
    """A drop-in for ``rotor_stress.solve_rotor_stress`` shaped like the real
    one's ``cases`` dict, driven by the same SF(rpm) = C/rpm**2 curve."""
    def _solve(polys, assignments, rpm, overspeed_factor=1.2,
              interference_mm=0.0, *, stack_length_mm=0.0,
              material_overrides=None, mesh_size_mm=1.5, order=2,
              with_field=True, contacts=None, lift_off_solves=6,
              case_mode="three", loads="centrifugal", torque_nm=0.0,
              rotor_temp_c=20.0, sleeve_temp_c=20.0, ref_temp_c=20.0,
              part_temps_c=None, thermal_model="band_fit", symmetry="full",
              num_poles=None, progress=None):
        c = root_rpm ** 2
        sf = c / (rpm ** 2)
        part = "sleeve" if sf < 1.0 else "magnet"
        name = f"{rpm:,.0f} rpm"
        return {
            "cases": {name: {
                "rpm": rpm, "sf_min": sf, "sf_min_part": part,
                "sf_min_per_part": {part: {"averaged": sf}},
                "parts": {}, "interfaces": {},
            }},
            "materials": {}, "lift_off_rpm": {},
        }
    return _solve


def test_route_reports_the_limit_speed_block(client, monkeypatch):
    from motor_ai_sim.simulation.mechanical import rotor_stress as rsm
    monkeypatch.setattr(rsm, "solve_rotor_stress", _fake_solve_rotor_stress(20_000.0))

    r = client.post("/api/mechanical/limit_speed",
                    params={"rpm": 10_000, "loads": "centrifugal", "torque_nm": 0})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "limit_speed" in body
    ls = body["limit_speed"]
    assert ls["reached"] is True
    assert ls["rpm_sf1"] == pytest.approx(20_000.0, rel=0.02)
    assert ls["target_sf"] == pytest.approx(1.0)
    assert ls["loads"] == "centrifugal"


def test_route_result_becomes_the_last_rotor_stress_result(client, monkeypatch):
    from motor_ai_sim.simulation.mechanical import rotor_stress as rsm
    monkeypatch.setattr(rsm, "solve_rotor_stress", _fake_solve_rotor_stress(20_000.0))

    r = client.post("/api/mechanical/limit_speed",
                    params={"rpm": 10_000, "loads": "centrifugal", "torque_nm": 0})
    assert r.status_code == 200, r.text

    last = client.get("/api/mechanical/last")
    assert last.status_code == 200, last.text
    entry = last.json()["rotor_stress"]
    assert entry is not None
    assert "limit_speed" in entry["result"]


def test_route_target_sf_is_configurable(client, monkeypatch):
    from motor_ai_sim.simulation.mechanical import rotor_stress as rsm
    monkeypatch.setattr(rsm, "solve_rotor_stress", _fake_solve_rotor_stress(20_000.0))

    r = client.post("/api/mechanical/limit_speed",
                    params={"rpm": 10_000, "loads": "centrifugal", "torque_nm": 0,
                            "target_sf": 2.0})
    assert r.status_code == 200, r.text
    ls = r.json()["limit_speed"]
    # SF = 2 at rpm = root/sqrt(2)
    assert ls["rpm_sf1"] == pytest.approx(20_000.0 / math.sqrt(2.0), rel=0.02)


def test_route_unknown_loads_is_a_422(client):
    r = client.post("/api/mechanical/limit_speed", params={"loads": "nonsense"})
    assert r.status_code == 422
    assert r.json()["detail"]["invalid_parameters"][0]["field"] == "loads"


def test_route_zero_rpm_is_a_422(client):
    r = client.post("/api/mechanical/limit_speed", params={"rpm": 0})
    assert r.status_code == 422
    assert r.json()["detail"]["invalid_parameters"][0]["field"] == "rpm"


# ---------------------------------------------------------------------------
# (c) the report row — appears only when the block exists
# ---------------------------------------------------------------------------

_LS_BLOCK = {
    "reached": True, "rpm_sf1": 25412.3, "limiting_part": "sleeve",
    "sf_at_rpm0": 1.5, "target_sf": 1.0, "analysed_rpm": 20000.0,
    "loads": "both", "torque_nm": 120.0, "n_solves": 5,
    "omega2_extrapolation_rpm": 24494.9,
    "note": "held loads/contacts/temperatures/mesh as analysed",
}


def _mech_col(duty: str, extra_rotor_stress: dict) -> dict:
    return {"duty": duty, "em": {}, "d": {}, "res": {"rotor_stress": {
        "rpm": 20000.0, "case": "rated", "sf_min": 1.5, "sf_min_part": "sleeve",
        "parts": {}, **extra_rotor_stress,
    }}}


def test_report_row_absent_when_no_duty_has_the_limit_speed_block():
    from motor_ai_sim import report as R

    cols = [_mech_col("rated", {})]
    labels = [r[0] for r in R.mech_compare_rows(cols)[1]]
    assert "Speed at SF = 1 (same loads)" not in labels


def test_report_row_present_and_names_the_limiting_part():
    from motor_ai_sim import report as R

    cols = [_mech_col("rated", {"limit_speed": _LS_BLOCK})]
    rows = {r[0]: r[1:] for r in R.mech_compare_rows(cols)[1]}
    key = "Speed at SF = 1 (same loads)"
    assert key in rows
    assert rows[key] == ["25,412 rpm — sleeve"]


def test_report_row_says_not_reached_when_the_search_did_not_bracket():
    from motor_ai_sim import report as R

    not_reached = {**_LS_BLOCK, "reached": False, "rpm_sf1": None,
                   "limiting_part": None}
    cols = [_mech_col("rated", {"limit_speed": not_reached})]
    rows = {r[0]: r[1:] for r in R.mech_compare_rows(cols)[1]}
    assert rows["Speed at SF = 1 (same loads)"] == ["not reached in the searched range"]


def test_report_row_is_present_for_only_the_duty_that_has_it():
    """One duty pressed Limit speed, the other only ran a plain Solve — the
    row appears (a real duty has it), and the untouched column reads '—' with
    the same discipline every other conditional row in this table follows."""
    from motor_ai_sim import report as R

    cols = [_mech_col("rated", {"limit_speed": _LS_BLOCK}),
           _mech_col("peak", {})]
    rows = {r[0]: r[1:] for r in R.mech_compare_rows(cols)[1]}
    key = "Speed at SF = 1 (same loads)"
    assert rows[key][0] == "25,412 rpm — sleeve"
    assert rows[key][1] == "—"


# ---------------------------------------------------------------------------
# duty_results.compact_mechanical — the block survives compaction
# ---------------------------------------------------------------------------

def test_compact_mechanical_carries_the_limit_speed_block_through():
    from motor_ai_sim import duty_results as DR

    result = {
        "primary_case": "20,000 rpm",
        "cases": {"20,000 rpm": {"rpm": 20000.0, "sf_min": 1.5,
                                 "sf_min_part": "sleeve", "parts": {},
                                 "interfaces": {}}},
        "limit_speed": _LS_BLOCK,
    }
    compact = DR.compact_mechanical("rotor_stress", result, {}, None, None)
    assert compact["limit_speed"]["rpm_sf1"] == pytest.approx(25412.3)
    assert compact["limit_speed"]["limiting_part"] == "sleeve"
    assert compact["limit_speed"]["reached"] is True


def test_compact_mechanical_omits_limit_speed_when_absent():
    from motor_ai_sim import duty_results as DR

    result = {
        "primary_case": "20,000 rpm",
        "cases": {"20,000 rpm": {"rpm": 20000.0, "sf_min": 1.5,
                                 "sf_min_part": "sleeve", "parts": {},
                                 "interfaces": {}}},
    }
    compact = DR.compact_mechanical("rotor_stress", result, {}, None, None)
    assert "limit_speed" not in compact
