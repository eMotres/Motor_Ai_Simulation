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
# (a2) non-monotonic SF near lift-off / a contact-state change (2026-09-21
# addendum — measured on the Ø50: 45,000 -> 1.51, 47,188 -> 5.78,
# 47,461 -> 0.86, 48,281 -> 0.75; a separation contact lands on a different
# branch between samples, so a plain bisection can report the WRONG, higher
# crossing).  The two helpers are unit-tested directly (deterministic), then
# one end-to-end run against the measured data.
# ---------------------------------------------------------------------------

def test_is_non_monotonic_flags_a_later_sample_reading_over_10pct_higher():
    from motor_ai_sim.simulation.mechanical import limit_speed as lsm

    monotonic = [(45_000.0, 1.51, "sleeve"), (47_461.0, 0.86, "sleeve"),
                (48_281.0, 0.75, "sleeve")]
    assert lsm._is_non_monotonic(monotonic) is False

    bumpy = [(45_000.0, 1.51, "magnet"), (47_188.0, 5.78, "magnet"),
            (47_461.0, 0.86, "sleeve"), (48_281.0, 0.75, "sleeve")]
    assert lsm._is_non_monotonic(bumpy) is True


def test_is_non_monotonic_ignores_a_bump_under_10_percent():
    from motor_ai_sim.simulation.mechanical import limit_speed as lsm

    # 1.05 is only 5 % above 1.00 — inside noise, not a branch change.
    almost = [(45_000.0, 1.00, "x"), (46_000.0, 1.05, "x"), (47_000.0, 0.9, "x")]
    assert lsm._is_non_monotonic(almost) is False


def test_conservative_crossing_brackets_the_lowest_failing_sample():
    from motor_ai_sim.simulation.mechanical import limit_speed as lsm

    pts = sorted([(45_000.0, 1.51, "magnet"), (47_188.0, 5.78, "magnet"),
                 (47_461.0, 0.86, "sleeve"), (48_281.0, 0.75, "sleeve")],
                key=lambda t: t[0])
    rpm_sf1, bracket, part, note = lsm._conservative_crossing(
        pts, 1.0, rpm_sf1=48_000.0, bracket=(47_188.0, 48_281.0),
        limiting_part="sleeve")
    # bracketed against the sample JUST BELOW the first failure (47,188, the
    # bump) and the first failure itself (47,461) — never the second,
    # deeper failure at 48,281 that a naive bisection might have landed near.
    assert bracket == (47_188.0, 47_461.0)
    assert 47_188.0 < rpm_sf1 < 47_461.0
    assert part == "sleeve"
    assert "contact state changes between 47,188 and 47,461" in note


def test_conservative_crossing_is_a_no_op_when_no_earlier_passing_sample():
    from motor_ai_sim.simulation.mechanical import limit_speed as lsm

    # the LOWEST rpm sampled already fails — nothing below it to bracket
    # against, so the original answer is returned unchanged.
    pts = [(40_000.0, 0.5, "sleeve"), (42_000.0, 3.0, "sleeve"),
          (44_000.0, 0.4, "sleeve")]
    rpm_sf1, bracket, part, note = lsm._conservative_crossing(
        pts, 1.0, rpm_sf1=41_000.0, bracket=(40_000.0, 44_000.0),
        limiting_part="sleeve")
    assert rpm_sf1 == 41_000.0
    assert bracket == (40_000.0, 44_000.0)
    assert note == ""


def test_end_to_end_non_monotonic_measured_data_reports_conservative_and_flags():
    """The exact Ø50 measurement (owner 2026-09-21 addendum): whatever rpm the
    search actually queries, answered from the nearest of the four measured
    points — so the samples this run collects are always a realistic,
    non-monotonic subset of the real data, regardless of which exact rpms
    the bracket/bisection happen to try."""
    table = [(45_000.0, 1.51), (47_188.0, 5.78), (47_461.0, 0.86),
            (48_281.0, 0.75)]

    def solve(rpm):
        nearest = min(table, key=lambda t: abs(t[0] - rpm))
        sf = nearest[1]
        part = "sleeve" if sf < 1.0 else "magnet"
        return sf, part, {part: {"averaged": sf}}

    out = find_limit_speed(solve, 45_000.0, 1.51, tol_rel=0.01, max_solves=12)
    assert out["reached"] is True
    assert out["non_monotonic"] is True
    assert "contact state changes" in out["note"]
    # conservative: never past the second, deeper failure (48,281) — the
    # reported speed sits at or below the first place any sample failed.
    assert out["rpm_sf1"] <= 48_281.0
    assert out["bracket"] is not None


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
# (b2) the coupled loop's AUTOMATIC search — owner 2026-09-21: "нужно эту
# максимальную скорость обязательно добавлять в отчёт", every duty a coupled
# run saves, not only when the button was pressed.  Wired into
# `run_rotor_stress_at` only; cached per (geometry, loads/torque, contacts,
# temperatures, mesh, order); MECH_LIMIT_SPEED_AUTO=0 turns it off.
# ---------------------------------------------------------------------------

def _fake_out(fp: str = "fp-1", **overrides) -> dict:
    """The bare minimum `run_rotor_stress_at`/`_auto_limit_speed` need out of
    a rotor_stress answer — no real solve behind it."""
    out = {
        "geo_fingerprint": fp, "primary_case": "rated",
        "cases": {"rated": {"rpm": 10_000.0, "sf_min": 4.0,
                            "sf_min_part": "magnet"}},
        "loads": "centrifugal", "torque_nm": 0.0, "interference_mm": 0.0,
        "contacts": {}, "thermal": {"rotor_temp_c": 20.0, "sleeve_temp_c": 20.0},
        "mesh": {"mesh_size_mm": 1.5, "element_order": 2},
        "symmetry": {"mode": "full"},
    }
    out.update(overrides)
    return out


def test_limit_speed_auto_cache_key_is_stable_and_sensitive():
    from motor_ai_sim.routes import mechanical as M

    out = _fake_out(torque_nm=120.0, loads="both",
                    contacts={"sleeve_rotor": {"type": "separation", "mu": 0.2}},
                    thermal={"rotor_temp_c": 150.0, "sleeve_temp_c": 150.0})
    k1 = M._limit_speed_auto_cache_key(out["geo_fingerprint"], out)
    k2 = M._limit_speed_auto_cache_key(out["geo_fingerprint"], dict(out))
    assert k1 == k2   # same inputs -> same key, independent of dict identity

    assert M._limit_speed_auto_cache_key("fp-2", out) != k1        # geometry
    assert M._limit_speed_auto_cache_key(out["geo_fingerprint"],
        {**out, "torque_nm": 121.0}) != k1                          # torque
    # temperatures round to 5 K: 150 and 152 land on the same key, 150 and
    # 156 do not.
    assert M._limit_speed_auto_cache_key(out["geo_fingerprint"],
        {**out, "thermal": {"rotor_temp_c": 152.0, "sleeve_temp_c": 150.0}}) == k1
    assert M._limit_speed_auto_cache_key(out["geo_fingerprint"],
        {**out, "thermal": {"rotor_temp_c": 156.0, "sleeve_temp_c": 150.0}}) != k1


def test_auto_limit_speed_disabled_by_env(monkeypatch):
    from motor_ai_sim.routes import mechanical as M

    monkeypatch.setenv("MECH_LIMIT_SPEED_AUTO", "0")
    assert M._auto_limit_speed(_fake_out(), None) is None


def test_auto_limit_speed_disabled_env_never_calls_the_solver(monkeypatch):
    from motor_ai_sim.routes import mechanical as M
    from motor_ai_sim.simulation.mechanical import rotor_stress as rsm

    monkeypatch.setenv("MECH_LIMIT_SPEED_AUTO", "off")
    calls = []
    monkeypatch.setattr(rsm, "solve_rotor_stress",
                        lambda *a, **kw: calls.append(1) or (0, None, None))
    assert M._auto_limit_speed(_fake_out(), None) is None
    assert calls == []


def test_auto_limit_speed_cache_hit_skips_the_search(monkeypatch):
    from motor_ai_sim.routes import mechanical as M
    from motor_ai_sim.simulation.mechanical import rotor_stress as rsm

    M._LIMIT_SPEED_AUTO_CACHE.clear()
    fake = _fake_solve_rotor_stress(20_000.0)
    calls: list = []

    def counting(*a, **kw):
        calls.append(1)
        return fake(*a, **kw)
    monkeypatch.setattr(rsm, "solve_rotor_stress", counting)

    out = _fake_out(fp="fp-cache-1")
    block1 = M._auto_limit_speed(out, None)
    assert block1 is not None and block1["reached"] is True
    n1 = len(calls)
    assert n1 >= 1   # the search spent at least one solve — cost on the Ø50

    block2 = M._auto_limit_speed(dict(out), None)   # same inputs, a NEW dict
    assert block2 is not None
    assert len(calls) == n1                          # cache hit: zero extra
    assert block2["rpm_sf1"] == pytest.approx(block1["rpm_sf1"])


def test_auto_limit_speed_cache_miss_on_a_different_geometry(monkeypatch):
    """The owner's rule stated plainly: a second coupled run of the SAME
    machine must not repeat the search; a DIFFERENT geometry must."""
    from motor_ai_sim.routes import mechanical as M
    from motor_ai_sim.simulation.mechanical import rotor_stress as rsm

    M._LIMIT_SPEED_AUTO_CACHE.clear()
    fake = _fake_solve_rotor_stress(20_000.0)
    calls: list = []

    def counting(*a, **kw):
        calls.append(1)
        return fake(*a, **kw)
    monkeypatch.setattr(rsm, "solve_rotor_stress", counting)

    M._auto_limit_speed(_fake_out(fp="fp-geo-A"), None)
    n1 = len(calls)
    M._auto_limit_speed(_fake_out(fp="fp-geo-B"), None)
    assert len(calls) > n1   # a different machine: a fresh search ran


def test_auto_limit_speed_cache_miss_on_a_different_case(monkeypatch):
    """Same geometry, different torque (a different duty's case): also not a
    cache hit — the block would describe the wrong load."""
    from motor_ai_sim.routes import mechanical as M
    from motor_ai_sim.simulation.mechanical import rotor_stress as rsm

    M._LIMIT_SPEED_AUTO_CACHE.clear()
    fake = _fake_solve_rotor_stress(20_000.0)
    calls: list = []

    def counting(*a, **kw):
        calls.append(1)
        return fake(*a, **kw)
    monkeypatch.setattr(rsm, "solve_rotor_stress", counting)

    M._auto_limit_speed(_fake_out(fp="fp-case", torque_nm=0.0), None)
    n1 = len(calls)
    M._auto_limit_speed(_fake_out(fp="fp-case", torque_nm=80.0), None)
    assert len(calls) > n1


def test_run_rotor_stress_at_attaches_the_block_end_to_end(monkeypatch):
    """The full hook the coupled loop calls: a plain rotor-stress answer, plus
    `limit_speed` riding on it, from ONE call — no separate button press."""
    from motor_ai_sim.routes import mechanical as M
    from motor_ai_sim.simulation.mechanical import rotor_stress as rsm

    M._LIMIT_SPEED_AUTO_CACHE.clear()
    monkeypatch.setattr(rsm, "solve_rotor_stress", _fake_solve_rotor_stress(20_000.0))
    out = M.run_rotor_stress_at({}, rpm=10_000, loads="centrifugal",
                                torque_nm=0, cases="single", field=False)
    assert "limit_speed" in out
    assert out["limit_speed"]["reached"] is True


def test_run_rotor_stress_at_env_off_attaches_nothing(monkeypatch):
    from motor_ai_sim.routes import mechanical as M
    from motor_ai_sim.simulation.mechanical import rotor_stress as rsm

    monkeypatch.setenv("MECH_LIMIT_SPEED_AUTO", "0")
    monkeypatch.setattr(rsm, "solve_rotor_stress", _fake_solve_rotor_stress(20_000.0))
    out = M.run_rotor_stress_at({}, rpm=10_000, loads="centrifugal",
                                torque_nm=0, cases="single", field=False)
    assert "limit_speed" not in out


# ---------------------------------------------------------------------------
# (c) the report row — MANDATORY (owner 2026-09-21: "нужно эту максимальную
# скорость обязательно добавлять в отчёт"), never blank, never omitted
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


def test_report_row_is_mandatory_even_with_no_limit_speed_block_anywhere():
    from motor_ai_sim import report as R

    cols = [_mech_col("rated", {})]
    labels = [r[0] for r in R.mech_compare_rows(cols)[1]]
    assert "Speed at SF = 1 (same loads)" in labels


def test_report_row_flags_the_gap_when_mechanical_solved_but_no_block():
    from motor_ai_sim import report as R

    cols = [_mech_col("rated", {})]
    rows = {r[0]: r[1:] for r in R.mech_compare_rows(cols)[1]}
    assert rows["Speed at SF = 1 (same loads)"] == [
        "not solved — re-run the coupled loop"]


def test_report_row_reads_plain_not_solved_when_mechanical_never_ran():
    from motor_ai_sim import report as R

    cols = [{"duty": "rated", "em": {}, "d": {}, "res": {}}]
    rows = {r[0]: r[1:] for r in R.mech_compare_rows(cols)[1]}
    assert rows["Speed at SF = 1 (same loads)"] == [R.NOT_SOLVED]


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


def test_report_row_flags_the_gap_per_duty_not_globally():
    """One duty has the block, the other only ran a plain Solve — the row is
    the real number for the first and the flagged gap for the second, never
    borrowed from its neighbour."""
    from motor_ai_sim import report as R

    cols = [_mech_col("rated", {"limit_speed": _LS_BLOCK}),
           _mech_col("peak", {})]
    rows = {r[0]: r[1:] for r in R.mech_compare_rows(cols)[1]}
    key = "Speed at SF = 1 (same loads)"
    assert rows[key][0] == "25,412 rpm — sleeve"
    assert rows[key][1] == "not solved — re-run the coupled loop"


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


# ---------------------------------------------------------------------------
# datasheet.py's DESIGN block — "Max mechanical speed (SF = 1)"
# ---------------------------------------------------------------------------

def test_datasheet_row_names_the_speed_and_limiting_part():
    from motor_ai_sim import datasheet as DS

    mech_by_duty = {"rated": {"rotor_stress": {"limit_speed": _LS_BLOCK}}}
    label, value, note, d = DS.limit_speed_datasheet_row(mech_by_duty)
    assert label == "Max mechanical speed (SF = 1)"
    assert value == pytest.approx(25412.3)
    assert "sleeve" in note
    assert d == 0


def test_datasheet_row_not_reached():
    from motor_ai_sim import datasheet as DS

    not_reached = {**_LS_BLOCK, "reached": False, "rpm_sf1": None}
    mech_by_duty = {"rated": {"rotor_stress": {"limit_speed": not_reached}}}
    label, value, note, d = DS.limit_speed_datasheet_row(mech_by_duty)
    assert value == "not reached"


def test_datasheet_row_not_solved_when_no_duty_has_the_block():
    from motor_ai_sim import datasheet as DS

    mech_by_duty = {"rated": {"rotor_stress": {"sf_min": 1.5}}}
    label, value, note, d = DS.limit_speed_datasheet_row(mech_by_duty)
    assert value == "not solved"
    assert "coupled loop" in note or "Limit speed" in note


def test_datasheet_row_not_solved_on_an_empty_store():
    from motor_ai_sim import datasheet as DS

    label, value, note, d = DS.limit_speed_datasheet_row({})
    assert value == "not solved"


def test_datasheet_pick_limit_speed_takes_any_duty_that_has_it():
    from motor_ai_sim import datasheet as DS

    mech_by_duty = {"continuous": {"rotor_stress": {"sf_min": 3.0}},
                    "peak": {"rotor_stress": {"limit_speed": _LS_BLOCK}}}
    assert DS.pick_limit_speed(mech_by_duty) == _LS_BLOCK
