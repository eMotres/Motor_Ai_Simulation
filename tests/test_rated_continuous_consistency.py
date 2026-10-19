"""A RATED duty is the coils at 180 °C, held for ever, and its own record says so.

Launch day 2026-10-19.  Owner: a rated duty is always continuous (short-time
is for peak only), and "make all the coils the same temperature, 180 °C: that
is rated for every motor".  So rated = the continuous point at which the
winding MEAN (the coil temperature the EM solve runs at) is
``report.PROJECT_RATED_WINDING_C`` = 180 °C, at the duty's own speed and
cooling; 200 °C stays the insulation limit warnings are judged on; the bearing
seat is REPORTED and never limits.

What went wrong on the public records (CIANO14 40 new L12 / L20, CIANO14 50
edited L15, CIANO28 150_35 new L35):

* ``continuous_rating`` verified an S1 current (41.0 A on L12) and the record's
  em / field / temperatures BECAME that S1 machine (``record_is_s1``), the duty
  current was set to it — but the record's ``time_to_limit`` was still the
  SETPOINT's (42.78 A, "runs 3 m 33 s").  ``duty_class.classify`` read it as
  the duty's own and called the S1 duty short-time.
* ``time_to_limit`` judged the bearing seat, the rating did not; the 2-D
  "seat" is the shaft inside the active stack (about the rotor), not a bearing.
* a verified S1 pass could sit up to 3 K OVER its limit (two-sided ±3 K).

Pinned here: (1) the record moved to the S1 machine carries that machine's own
``time_to_limit`` and is continuous; (2) the S1 acceptance is one-sided,
[-1 K, 0]; (3) the rated winding is the MEAN at 180 °C, one constant; (4) the
bearing is advisory: reported, never a limit, never short-time.
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


_RATED_BLOCK = {**_RATING_BLOCK, "s": 0.8, "I_cont_A_rms": 16.0,
                "limiting_part": "winding", "winding_basis": "mean",
                "temperatures_c": {"winding": 180.3, "magnet": 120.0},
                "limits_c": {"winding": 180.0, "magnet": 150.0}}


# ---------------------------------------------------------------------------
# (1) the record moved to the S1 machine carries that machine's verdict
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
    # ...and the class is the S1 duty's: continuous
    cls = duty_class.classify(None, c)
    assert cls and cls["duty_class"] == duty_class.CONTINUOUS


def test_a_setpoint_below_rated_still_moves_up_to_the_rated_point(
        client, monkeypatch):
    """Rated is the MAXIMUM continuous point (the coils at 180 °C), so a
    cooler setpoint's record moves up to it too."""
    _fake_rating(monkeypatch, block={**_RATED_BLOCK, "s": 1.2,
                                     "I_cont_A_rms": 24.0})
    seen = _fake_verify(monkeypatch,
                        winding_max_at=lambda i: 184.6 if i > 22.0 else 150.0,
                        magnet_max_at=lambda i: 120.0)   # mean = max - 5
    _ttl_by_current(monkeypatch, over_from_a=1e9)
    c = _run(client, solve_to="continuous")
    cr = c["continuous_rating"]
    assert 24.0 in seen["I"]
    assert cr["verified"] is True and cr["record_is_s1"] is True
    # the EM pass ran with the coils AT the rated temperature, and the record
    # carries that number
    assert c["coil_temp_c"] == pytest.approx(180.0)
    assert c["time_to_limit"]["point"] == "s1"


# ---------------------------------------------------------------------------
# (2) one-sided acceptance, [-1 K, 0], on the winding MEAN for rated
# ---------------------------------------------------------------------------

def test_a_rated_pass_is_judged_on_the_winding_mean(client, monkeypatch):
    _fake_rating(monkeypatch, block=dict(_RATED_BLOCK))
    _fake(monkeypatch, ttl=_ttl_block())
    # max 184.6 -> mean 179.6: inside [-1, 0] on the MEAN, 4.6 K over on the
    # hot spot; verified only if the mean is what is read
    seen = _fake_verify(monkeypatch,
                        winding_max_at=lambda i: 184.6 if i < 17.0 else 430.0,
                        magnet_max_at=lambda i: 95.0)
    _ttl_by_current(monkeypatch, over_from_a=19.0)
    c = _run(client, solve_to="continuous")
    cr = c["continuous_rating"]
    assert [i for i in seen["I"] if i < 19.0] == [16.0]
    assert cr["verified"] is True
    assert cr["miss_K"] == pytest.approx(-0.4, abs=1e-6)


def test_a_pass_over_the_rated_point_is_corrected_down(client, monkeypatch):
    _fake_rating(monkeypatch, block=dict(_RATED_BLOCK))
    _fake(monkeypatch, ttl=_ttl_block())
    seen = _fake_verify(monkeypatch,
                        winding_max_at=lambda i: 187.0 if i > 15.95 else 184.5,
                        magnet_max_at=lambda i: 95.0)   # mean 182.0 / 179.5
    _ttl_by_current(monkeypatch, over_from_a=19.0)
    c = _run(client, solve_to="continuous")
    cr = c["continuous_rating"]
    s1 = [i for i in seen["I"] if i < 19.0]
    assert len(s1) == 2 and s1[1] < s1[0]          # corrected DOWN
    assert cr["verified"] is True
    assert cr["verification_passes"] == 2
    assert -1.0 <= cr["miss_K"] <= 0.0


def test_a_pass_two_kelvin_under_is_not_verified_until_corrected(
        client, monkeypatch):
    _fake_rating(monkeypatch, block=dict(_RATED_BLOCK))
    _fake(monkeypatch, ttl=_ttl_block())
    seen = _fake_verify(monkeypatch,
                        winding_max_at=lambda i: 183.0 if i < 16.05 else 184.6,
                        magnet_max_at=lambda i: 95.0)   # mean 178.0 / 179.6
    _ttl_by_current(monkeypatch, over_from_a=19.0)
    c = _run(client, solve_to="continuous")
    cr = c["continuous_rating"]
    s1 = [i for i in seen["I"] if i < 19.0]
    assert len(s1) == 2 and s1[1] > s1[0]          # corrected UP
    assert cr["verified"] is True


def test_the_s1_window_is_one_sided():
    from motor_ai_sim.routes import coupled as cp

    assert cp._S1_UNDER_TOL_K == 1.0
    assert 0.0 < cp._S1_AIM_K < cp._S1_UNDER_TOL_K
    assert cp._S1_MAX_PASSES >= 3


# ---------------------------------------------------------------------------
# (3) one rated winding temperature
# ---------------------------------------------------------------------------

def test_one_rated_winding_constant():
    from motor_ai_sim import coupled_time_to_limit as ttl
    from motor_ai_sim import report
    from motor_ai_sim import thermal_duty_cycle as tdc

    assert report.PROJECT_RATED_WINDING_C == 180.0
    assert tdc.PROJECT_RATED_WINDING_C == report.PROJECT_RATED_WINDING_C
    assert ttl.rated_winding_limit()[0] == 180.0
    assert report.PROJECT_INSULATION_C == 200.0      # the overheat limit stays


def test_the_rated_winding_limit_is_the_mean_at_180():
    from motor_ai_sim import coupled_time_to_limit as ttl

    field = {"components": {"winding": {"avg": 170.0, "max": 174.0},
                            "magnet": {"avg": 100.0, "max": 101.0}}}
    rated = {p.part: p for p in ttl.part_limits(thermal_result=field,
                                                 rated_winding=True)}
    w = rated["winding"]
    assert w.limit_c == 180.0 and w.offset_k == 0.0 and w.at_point_c == 170.0
    plain = {p.part: p for p in ttl.part_limits(thermal_result=field)}
    assert plain["winding"].limit_c == 200.0
    assert plain["winding"].at_point_c == pytest.approx(174.0)


def test_the_loops_rating_asks_for_the_rated_winding(monkeypatch):
    from motor_ai_sim.routes import coupled as cp

    _mock_rating_deps(monkeypatch, rate_return={
        "feasible": True, "s": 0.5, "I_cont_A_rms": 30.0,
        "limiting_part": "winding", "limits_c": {"winding": 180.0},
        "temperatures_c": {"winding": 180.0}, "notes": [],
        "converged": True, "n_thermal_fem_solves": 1})
    cp._continuous_rating_for_loop(
        {"geo": None}, cooling={"cooling_mode": "air", "air_speed_mps": 40.0},
        rpm=10000.0, coil_temp_c=200.0, magnet_temp_c=48.2, duty="rated",
        mode="motor", time_to_limit=None)
    # no catalogued configuration in this test: the default (aviation) 180 °C
    assert RATE_CALLS == [{"rated_winding": True, "winding_limit_c": 180.0}]


def test_a_continuous_class_says_where_the_coils_sit():
    from motor_ai_sim import duty_class

    c = {"coil_temp_c": 180.0,
         "time_to_limit": {"within_limits": True}}
    cls = duty_class.classify(None, c)
    assert cls["duty_class"] == duty_class.CONTINUOUS
    assert cls["rated_winding_c"] == 180.0 and cls["at_rated_winding"] is True
    c["coil_temp_c"] = 150.0
    assert duty_class.classify(None, c)["at_rated_winding"] is False


# ---------------------------------------------------------------------------
# (4) the bearing: advisory, reported, never a limit
# ---------------------------------------------------------------------------

def test_a_bearing_over_its_grease_is_advisory_not_a_limit():
    from motor_ai_sim import coupled_time_to_limit as ttl

    lims = [ttl.PartLimit("winding", "winding", 200.0, 0.0, 170.0, "x"),
            ttl.PartLimit("bearing", "rotor", 110.0, 0.0, 125.0, "y")]
    out = ttl.solve(thermal_result={}, em_summary={}, limits=lims, caps={})
    assert out["within_limits"] is True
    assert out["advisories"]["bearing"]["over_by_K"] == pytest.approx(15.0)
    assert "never a limit" in out["advisories"]["bearing"]["note"]


def test_an_old_record_over_only_on_the_bearing_is_not_short_time():
    from motor_ai_sim import duty_class

    c = {"time_to_limit": {"within_limits": False, "over_parts": ["bearing"],
                           "limiting_part": "bearing", "time_to_limit_s": 355.0,
                           "starts": {"cold": {"time_to_limit_s": 355.0,
                                               "limiting_part": "bearing"}}}}
    assert duty_class.short_time_info(c) is None
    assert duty_class.classify(None, c)["duty_class"] == duty_class.CONTINUOUS
    c["time_to_limit"]["over_parts"] = ["winding", "bearing"]
    assert duty_class.classify(None, c)["duty_class"] == duty_class.SHORT_TIME


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



def _alternating_rate(monkeypatch, *, still_on_limit):
    """``rate`` on the linear-map fixture with the bisection's answer forced
    to jump 1.156 <-> 1.172 (the 40 L20 bearing-seat case, 1.35 % > REL_TOL)."""
    from tests.test_continuous_rating import RATED_SUMMARY, _limit, _linear_map
    from tests.test_thermal_capacities import (L13_MATERIALS, L13_PARTS,
                                               L13_SUMMARY)
    from tests.test_thermal_duty_cycle import D_HOUSING_M, L13_GEO, RATED_MAP
    from motor_ai_sim import coupled_continuous_rating as ccr
    from motor_ai_sim.thermal_capacities import part_capacities

    real = ccr._search_scale
    seq = iter([1.15625, 1.17188] * 4)

    def jumping(*a, **k):
        out = dict(real(*a, **k))
        out["s"] = next(seq)
        return out

    monkeypatch.setattr(ccr, "_search_scale", jumping)
    monkeypatch.setattr(ccr, "_still_on_limit", lambda *a, **k: still_on_limit)
    caps = part_capacities(L13_SUMMARY, L13_MATERIALS, L13_PARTS)
    p_cu_ref = float(RATED_MAP["P_cu_exact_W"])
    return ccr.rate(thermal_result=_linear_map(p_cu_ref),
                    em_summary=RATED_SUMMARY, caps=caps,
                    limits=[_limit("winding", "winding", 180.0, 3.0)],
                    geometry=L13_GEO, cooling={"ambient_temp": 40.0},
                    d_housing_m=D_HOUSING_M, duty="rated",
                    resolve=lambda f: _linear_map(p_cu_ref * float(f)))


def test_a_jumping_s_converges_when_the_previous_s_is_still_on_the_limit(
        monkeypatch):
    out = _alternating_rate(monkeypatch, still_on_limit=True)
    assert out["converged"] is True
    assert out["n_thermal_fem_solves"] == 2


def test_a_jumping_s_off_the_limit_is_still_not_converged(monkeypatch):
    from motor_ai_sim import coupled_continuous_rating as ccr

    out = _alternating_rate(monkeypatch, still_on_limit=False)
    assert out["converged"] is False
    assert out["n_thermal_fem_solves"] == ccr.MAX_MAP_PASSES


def test_still_on_limit_reads_the_previous_s_on_the_new_network(monkeypatch):
    from tests.test_continuous_rating import (_limit, _one_node_network,
                                              _loss)
    from motor_ai_sim import coupled_continuous_rating as ccr
    from motor_ai_sim import thermal_duty_cycle as dc
    from motor_ai_sim.thermal_capacities import NODES

    monkeypatch.setattr(dc, "cu_rho_ratio", lambda *a, **k: 1.0)
    net = _one_node_network(1.0, t_amb=25.0)
    loss = _loss(100.0)
    caps = {n: 50.0 for n in NODES}
    lim = [_limit("winding", "winding", 125.0, 0.0)]       # 100 W -> 125 C
    assert ccr._still_on_limit(loss, 100.0, 1.0, net, caps, lim, "rated", 0.5)
    assert not ccr._still_on_limit(loss, 100.0, 1.05, net, caps, lim,
                                   "rated", 0.5)


def test_the_rated_point_judges_coils_and_magnets_only():
    from motor_ai_sim import coupled_time_to_limit as ttl

    field = {"components": {"winding": {"avg": 170.0, "max": 174.0},
                            "magnet": {"avg": 100.0, "max": 101.0},
                            "stator": {"avg": 150.0, "max": 151.0}},
             "cooling": {"heat_path": {"body": "housing", "t_body_c": 95.0}}}
    rated = [p.part for p in ttl.part_limits(thermal_result=field,
                                             rated_winding=True,
                                             bearing_temp_c=None)]
    assert "housing" not in rated and "winding" in rated
    plain = [p.part for p in ttl.part_limits(thermal_result=field)]
    assert "housing" in plain


def test_the_rated_point_depends_on_the_application():
    """Owner 2026-10-19: 180 °C for aviation / propeller machines; robots 70 °C
    at 30 °C air.  One table, read through one function."""
    from motor_ai_sim import configure_limits as cl
    from motor_ai_sim import coupled_time_to_limit as ttl

    assert ttl.rated_winding_limit("propeller")[0] == 180.0
    assert ttl.rated_winding_limit("aviation")[0] == 180.0
    assert ttl.rated_winding_limit("robot")[0] == 70.0
    assert ttl.rated_winding_limit(None)[0] == 180.0
    assert ttl.rated_ambient("robot") == 30.0
    assert ttl.rated_ambient("propeller") is None
    robot = cl.thermal_limits({"applications": ["robot"]})
    assert robot["rated_winding_C"] == 70.0 and robot["rated_ambient_C"] == 30.0
    assert robot["winding_C"] == 200.0                 # the overheat line stays
    prop = cl.thermal_limits({"applications": ["propeller"]})
    assert prop["rated_winding_C"] == 180.0 and "rated_ambient_C" not in prop
    w = {p.part: p for p in ttl.part_limits(
        thermal_result={"components": {"winding": {"avg": 60.0, "max": 63.0}}},
        rated_winding=True, winding_limit_c=70.0)}["winding"]
    assert w.limit_c == 70.0 and w.offset_k == 0.0 and w.at_point_c == 60.0


def test_the_class_reads_the_records_own_rated_temperature():
    from motor_ai_sim import duty_class

    c = {"coil_temp_c": 70.0, "time_to_limit": {"within_limits": True},
         "continuous_rating": {"winding_basis": "mean",
                               "limits_c": {"winding": 70.0}}}
    cls = duty_class.classify(None, c)
    assert cls["rated_winding_c"] == 70.0 and cls["at_rated_winding"] is True


def test_configure_reads_the_same_rated_winding():
    """Configure's rated cap aims at the SAME rated point (PR #331 reads
    ``thermal_limits.rated_winding_C``); the red line stays on ``winding_C``."""
    from motor_ai_sim import configure_limits as cl
    from motor_ai_sim import propeller_s1 as ps1

    t = cl.thermal_limits({})
    assert t["rated_winding_C"] == 180.0 and t["winding_C"] == 200.0
    assert "rated" in t["rated_winding_basis"]
    assert ps1._rated_winding_c() == 180.0
