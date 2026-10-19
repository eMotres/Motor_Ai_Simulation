"""A RATED duty must be a continuous point its own record calls continuous.

Launch day 2026-10-19 (owner: «rated всегда постоянный, short time только
peak»).  Four public rated duties (CIANO14 40 new L12 / L20, CIANO14 50 edited
L15, CIANO28 150_35 new L35) came out of ``solve_to: continuous`` with a record
that contradicts itself:

* ``continuous_rating`` verified an S1 current (41.0 A on L12) and the record's
  em / field / temperatures BECAME that S1 machine (``record_is_s1``), the duty
  current was set to it — but the record's ``time_to_limit`` was still the
  SETPOINT's (42.78 A, "runs 3 m 33 s") and ``s`` (0.958) was still a multiple
  of the setpoint.  ``duty_class.classify`` read both as the duty's own and
  called the S1 duty short-time.
* the rating judged winding + magnet only, while ``time_to_limit`` judged the
  bearing seat too — so an S1 current "held for ever" with the seat 40 K over
  its grease (L20: rotor 150 °C, MT47 110 °C).
* a verified S1 pass could sit up to 3 K OVER its limit (two-sided ±3 K), which
  the record's own time to the limit then calls over.

Pinned here: (1) a setpoint that already holds keeps its record (the S1 pass
is verified as an errand), (2) a record moved to the S1 machine carries the S1
machine's own ``time_to_limit`` and the class is continuous, (3) the S1
acceptance is one-sided, (4) the bearing seat is read off a real map and the
loop's rating asks for it.
"""
from __future__ import annotations

import pytest

from tests.test_coupled_continuous_solve_to import (  # noqa: F401
    RATE_CALLS, _RATING_BLOCK, _fake_rating, _fake_verify, _mock_rating_deps,
    _run, client)
from tests.test_coupled_limited_state import _fake, _ttl_block


def _ttl_by_current(monkeypatch, *, over_from_a: float):
    """``_ttl_step`` that answers for the CURRENT it is asked about: over the
    winding class from ``over_from_a`` up, inside every limit below."""
    from motor_ai_sim.routes import coupled as cp

    asked = []

    def _ttl_step(body, cooling, em_summary, field, **_k):
        i = float(body.get("I_phase_rms") or 0.0)
        asked.append(round(i, 3))
        return (_ttl_block() if i >= over_from_a
                else _ttl_block(within=True))

    monkeypatch.setattr(cp, "_ttl_step", _ttl_step, raising=True)
    return asked


# ---------------------------------------------------------------------------
# (1) the setpoint already holds: the record stays the setpoint's
# ---------------------------------------------------------------------------

def test_a_setpoint_that_holds_keeps_its_record_and_is_continuous(
        client, monkeypatch):
    from motor_ai_sim import duty_class

    _fake_rating(monkeypatch, block={
        **_RATING_BLOCK, "s": 1.2, "I_cont_A_rms": 24.0,
        "limiting_part": "winding",
        "temperatures_c": {"winding": 199.0, "magnet": 120.0},
        "limits_c": {"winding": 200.0, "magnet": 150.0}})
    seen = _fake_verify(monkeypatch,
                        winding_max_at=lambda i: 199.2 if i > 22.0 else 170.0,
                        magnet_max_at=lambda i: 120.0)
    _ttl_by_current(monkeypatch, over_from_a=1e9)       # inside at any current
    c = _run(client, solve_to="continuous")
    cr = c["continuous_rating"]
    # the S1 pass ran, at the rating's current, and verified it…
    assert 24.0 in seen["I"]
    assert cr["verified"] is True
    assert cr["I_cont_A_rms"] == pytest.approx(24.0)
    # …but the record is still the 20 A setpoint's machine
    assert cr["record_is_s1"] is False
    assert "headroom" in cr["record_note"]
    assert c["coil_temp_c"] < 199.0
    assert all(h.get("phase") != "s1_verify" for h in c["history"])
    assert c["time_to_limit"]["within_limits"] is True
    assert c["time_to_limit"].get("point") is None
    cls = duty_class.classify(None, c)
    assert cls and cls["duty_class"] == duty_class.CONTINUOUS


def test_a_real_pass_below_a_holding_setpoint_is_flagged_not_trusted(
        client, monkeypatch):
    """The network said s = 1.2, the real pass verified only 18 A at a 20 A
    setpoint its own verdict calls inside every limit: contradiction."""
    _fake_rating(monkeypatch, block={
        **_RATING_BLOCK, "s": 1.2, "I_cont_A_rms": 18.0,
        "limiting_part": "winding",
        "temperatures_c": {"winding": 199.0, "magnet": 120.0},
        "limits_c": {"winding": 200.0, "magnet": 150.0}})
    _fake_verify(monkeypatch, winding_max_at=lambda i: 199.0,
                 magnet_max_at=lambda i: 120.0)
    _ttl_by_current(monkeypatch, over_from_a=1e9)
    c = _run(client, solve_to="continuous")
    cr = c["continuous_rating"]
    assert cr["record_is_s1"] is False
    assert cr["trustworthy"] is False
    assert "CONTRADICTS" in " ".join(cr["notes"])


# ---------------------------------------------------------------------------
# (2) the record moved to the S1 machine carries that machine's verdict
# ---------------------------------------------------------------------------

def test_a_record_moved_to_s1_carries_the_s1_time_to_limit(client, monkeypatch):
    from motor_ai_sim import duty_class

    _fake_rating(monkeypatch, block={
        **_RATING_BLOCK, "s": 0.8, "I_cont_A_rms": 16.0,
        "limiting_part": "winding",
        "temperatures_c": {"winding": 199.0, "magnet": 120.0},
        "limits_c": {"winding": 200.0, "magnet": 150.0}})
    _fake(monkeypatch, ttl=_ttl_block())           # attach / remember faked
    _fake_verify(monkeypatch,
                 winding_max_at=lambda i: 199.4 if i < 17.0 else 430.0,
                 magnet_max_at=lambda i: 95.0)
    asked = _ttl_by_current(monkeypatch, over_from_a=19.0)
    c = _run(client, solve_to="continuous")
    cr = c["continuous_rating"]
    assert cr["verified"] is True and cr["record_is_s1"] is True
    # the record's own verdict is the S1 machine's, asked at ITS current
    assert 16.0 in asked
    t = c["time_to_limit"]
    assert t["point"] == "s1" and t["within_limits"] is True
    assert t["I_phase_rms_A"] == pytest.approx(16.0)
    assert t["reported_state"] == "steady"
    # the setpoint's story is kept, with the setpoint
    sp = cr["duty_point"]["time_to_limit"]
    assert sp["within_limits"] is False
    assert cr["duty_point"]["I_phase_rms_A"] == 20.0
    assert "Setpoint 20.00 A" in c["limited"]["line"]
    # …and the class is the S1 duty's: continuous
    cls = duty_class.classify(None, c)
    assert cls and cls["duty_class"] == duty_class.CONTINUOUS


# ---------------------------------------------------------------------------
# (3) one-sided acceptance
# ---------------------------------------------------------------------------

def test_a_pass_two_kelvin_over_is_not_verified_until_corrected(
        client, monkeypatch):
    _fake_rating(monkeypatch, block={
        **_RATING_BLOCK, "s": 0.8, "I_cont_A_rms": 16.0,
        "limiting_part": "winding",
        "temperatures_c": {"winding": 199.0, "magnet": 120.0},
        "limits_c": {"winding": 200.0, "magnet": 150.0}})
    _fake(monkeypatch, ttl=_ttl_block())
    seen = _fake_verify(monkeypatch,
                        winding_max_at=lambda i: 202.0 if i > 15.95 else 199.0,
                        magnet_max_at=lambda i: 95.0)
    _ttl_by_current(monkeypatch, over_from_a=19.0)
    c = _run(client, solve_to="continuous")
    cr = c["continuous_rating"]
    s1 = [i for i in seen["I"] if i < 19.0]
    assert len(s1) == 2 and s1[1] < s1[0]          # corrected DOWN
    assert cr["verified"] is True
    assert cr["verification_passes"] == 2
    assert cr["miss_K"] <= 0.5


def test_the_s1_window_is_one_sided():
    from motor_ai_sim import coupled_time_to_limit as ttl
    from motor_ai_sim.routes import coupled as cp

    assert cp._S1_AIM_K > 0.0
    assert ttl.OVER_TOL_K == 0.5


# ---------------------------------------------------------------------------
# (4) the bearing seat
# ---------------------------------------------------------------------------

def test_the_bearing_seat_is_read_off_a_real_map():
    from motor_ai_sim import coupled_continuous_rating as ccr

    assert ccr.field_reading({"components": {"shaft": {"avg": 101.0}}},
                             "bearing") == pytest.approx(101.0)
    assert ccr.field_reading(
        {"components": {"shaft": {"avg": 101.0}},
         "cooling": {"shaft_ends": {"t_shaft_mean_c": 97.5}}},
        "bearing") == pytest.approx(97.5)
    assert ccr.field_reading({"components": {}}, "bearing") is None
    assert "bearing" in ccr.SLOW_PARTS


def test_the_loops_rating_asks_for_the_bearing_seat(monkeypatch):
    from motor_ai_sim.routes import coupled as cp

    _mock_rating_deps(monkeypatch, rate_return={
        "feasible": True, "s": 0.5, "I_cont_A_rms": 30.0,
        "limiting_part": "magnet", "limits_c": {"magnet": 150.0},
        "temperatures_c": {"magnet": 150.0}, "notes": [],
        "converged": True, "n_thermal_fem_solves": 1})
    cp._continuous_rating_for_loop(
        {"geo": None}, cooling={"cooling_mode": "air", "air_speed_mps": 40.0},
        rpm=10000.0, coil_temp_c=200.0, magnet_temp_c=48.2, duty="rated",
        mode="motor", time_to_limit=None)
    assert RATE_CALLS == [{"bearing_seat_from_map": True}]


def test_rate_judges_the_bearing_seat_read_off_each_map():
    from tests.test_continuous_rating import (RATED_SUMMARY, _linear_map)
    from tests.test_thermal_capacities import (L13_MATERIALS, L13_PARTS,
                                               L13_SUMMARY)
    from tests.test_thermal_duty_cycle import (D_HOUSING_M, L13_GEO,
                                               RATED_MAP)
    from motor_ai_sim import coupled_continuous_rating as ccr
    from motor_ai_sim.thermal_capacities import part_capacities

    caps = part_capacities(L13_SUMMARY, L13_MATERIALS, L13_PARTS)
    p_cu_ref = float(RATED_MAP["P_cu_exact_W"])

    def with_shaft(m):
        m = dict(m)
        comps = dict(m["components"])
        comps["shaft"] = {"avg": comps["rotor"]["avg"] - 1.0,
                          "max": comps["rotor"]["avg"]}
        m["components"] = comps
        return m

    ref = with_shaft(_linear_map(p_cu_ref))
    seat_ref = ref["components"]["shaft"]["avg"]
    # a seat limit BELOW the reference's own seat: only the bearing can bind
    b_lim = 40.0 + 0.8 * (seat_ref - 40.0)
    kw = dict(thermal_result=ref, em_summary=RATED_SUMMARY, caps=caps,
              geometry=L13_GEO, cooling={"ambient_temp": 40.0},
              d_housing_m=D_HOUSING_M, duty="rated",
              resolve=lambda f: with_shaft(_linear_map(p_cu_ref * float(f))),
              winding_limit_c=1000.0, bearing_limit_c=b_lim)
    out = ccr.rate(bearing_seat_from_map=True, **kw)
    assert "bearing" in out["judged"]
    assert out["limiting_part"] == "bearing"
    assert out["s"] < 1.0
    # without the flag (the old call) the seat is not judged at all
    old = ccr.rate(**kw)
    assert "bearing" not in old["judged"]
