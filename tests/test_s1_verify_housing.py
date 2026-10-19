"""S1 verification of a HOUSING-limited machine (robotics heat path).

Bug 2026-10-19 (85 mm robot motor "CIANO28 85 20SW1200", L15, still air, 70 C
touch limit): ``_s1_verify`` read the housing only when the limiting part was
named ``link``, a name nothing produces; ``rate()`` names it ``housing``.  The
verification then said "the map carries no housing reading" and the network's
8.095 A stood, 28 % too high (the FEM loop put 8.095 A at 77.5 C, 7.1 A at
72.9 C, 6.3 A at 69.65 C).
"""
from __future__ import annotations

import math

import pytest

from motor_ai_sim import coupled_continuous_rating as ccr
from motor_ai_sim.routes import coupled as cp

#: The measured FEM housing temperatures of the 85 mm L15 case (I, T).
_REAL = [(6.3, 69.65), (7.1, 72.9), (8.095, 77.5)]


def _t_real(i: float) -> float:
    """Piecewise-linear through the measured points, linear beyond them."""
    pts = _REAL
    if i <= pts[0][0]:
        (i0, t0), (i1, t1) = pts[0], pts[1]
    elif i >= pts[-1][0]:
        (i0, t0), (i1, t1) = pts[-2], pts[-1]
    else:
        k = next(n for n in range(len(pts) - 1)
                 if pts[n][0] <= i <= pts[n + 1][0])
        (i0, t0), (i1, t1) = pts[k], pts[k + 1]
    return t0 + (t1 - t0) * (i - i0) / (i1 - i0)


def _field(t_body, body="housing", winding=120.0):
    return {"components": {"winding": {"avg": winding - 5.0, "max": winding}},
            "cooling": {"heat_path": {"option": "housing", "body": body,
                                      "t_body_c": t_body,
                                      "touch_limit_c": 70.0}}}


def _fake_solver(monkeypatch, t_at, body="housing"):
    seen = []

    def _em(b, **kw):
        i = float(b["I_phase_rms"])
        seen.append(round(i, 4))
        return {"summary": {"I_phase_rms_A": i, "T_em_avg_Nm": 2.0},
                "I_phase_rms_solved_A": i}

    def _th(b, cooling, **kw):
        return _field(t_at(float(b["I_phase_rms"])), body=body)

    monkeypatch.setattr(cp, "_em_run", _em, raising=True)
    monkeypatch.setattr(cp, "_thermal_solve", _th, raising=True)
    return seen


def _verify(**kw):
    args = dict(cooling={"ambient_temp": 25.0}, rpm=1200.0, inverter=None,
                i_estimate=8.095, coil_temp_c_guess=90.0,
                magnet_temp_c_guess=80.0, limiting_part="housing",
                limit_c=70.0)
    args.update(kw)
    return cp._s1_verify({"I_phase_rms": args["i_estimate"]}, **args)


# ---------------------------------------------------------------- the mapping

def test_field_reading_maps_every_part_name():
    f = _field(71.2)
    assert ccr.field_reading(f, "housing") == 71.2
    assert ccr.field_reading(f, "link") == 71.2          # legacy alias
    assert ccr.field_reading(f, "winding") == 120.0
    assert ccr.field_reading(f, "magnet") is None         # not on this map
    # heat_path='shaft': the body is the structure, there is no housing.
    s = _field(64.0, body="structure")
    assert ccr.field_reading(s, "structure") == 64.0
    assert ccr.field_reading(s, "link") == 64.0
    assert ccr.field_reading(s, "housing") is None
    # no heat path at all
    none = {"components": {}, "cooling": {"heat_path": {
        "option": "none", "body": None, "t_body_c": None}}}
    assert ccr.field_reading(none, "housing") is None
    # the older mount.link spelling is still read for "link"
    old = {"cooling": {"mount": {"link": {"t_link_c": 55.5}}}}
    assert ccr.field_reading(old, "link") == 55.5
    assert ccr.field_reading(old, "housing") is None


def test_touch_parts_include_the_names_the_network_uses():
    from motor_ai_sim import coupled_time_to_limit as ttl
    for p in ttl.PART_NODE:
        if p not in ttl.PARTS:                  # housing / structure
            assert p in ccr.TOUCH_PARTS


def test_next_current_square_law_then_secant():
    amb, lim = 25.0, 70.0
    i1 = ccr.touch_next_current([(8.095, 77.5)], limit_c=lim, ambient_c=amb)
    assert i1 == pytest.approx(
        8.095 * math.sqrt((lim - 0.5 - amb) / (77.5 - amb)))
    i2 = ccr.touch_next_current([(8.095, 77.5), (i1, _t_real(i1))],
                                limit_c=lim, ambient_c=amb)
    assert i2 < i1 < 8.095
    assert _t_real(i2) == pytest.approx(lim - 0.5, abs=0.6)


# -------------------------------------------------------------- the correction

def test_housing_limited_network_overestimate_is_corrected_down(monkeypatch):
    seen = _fake_solver(monkeypatch, _t_real)
    out = _verify()
    assert out["verified"] is True
    assert out["passes"] <= ccr.TOUCH_MAX_PASSES
    assert out["actual_c"] == pytest.approx(_t_real(out["I_cont_A_rms"]),
                                            abs=0.01)
    assert -ccr.TOUCH_UNDER_TOL_K <= out["miss_K"] <= 0.0   # never over
    assert out["I_cont_A_rms"] < 7.0                         # not 8.095 / 7.1
    assert out["I_cont_A_rms"] == pytest.approx(6.3, abs=0.4)
    assert seen[0] == pytest.approx(8.095)
    assert all(b < a for a, b in zip(seen, seen[1:]))        # stepped down


def test_housing_over_by_a_hair_is_not_verified_on_the_winding_tolerance(
        monkeypatch):
    """+2 K over a hard touch limit passed the winding rule (|miss| <= 3 K)."""
    _fake_solver(monkeypatch, lambda i: 72.0)
    out = _verify(max_passes=1, i_estimate=7.0)
    assert out["verified"] is False
    assert out["miss_K"] == pytest.approx(2.0)
    assert "over its limit" in out["note"] and "unverified" in out["note"]


def test_no_point_within_the_passes_reports_the_miss(monkeypatch):
    _fake_solver(monkeypatch, lambda i: 90.0 + 0.0 * i)    # nothing cools it
    out = _verify()
    assert out["verified"] is False
    assert out["passes"] == ccr.TOUCH_MAX_PASSES
    assert out["miss_K"] == pytest.approx(20.0)
    assert "20.0 K over" in out["note"]


def test_a_body_that_is_not_the_named_part_is_not_silently_judged(monkeypatch):
    _fake_solver(monkeypatch, _t_real, body="structure")
    out = _verify()
    assert out["verified"] is False and out["miss_K"] is None
    assert "no housing reading" in out["note"]


def test_winding_verification_is_unchanged(monkeypatch):
    seen = _fake_solver(monkeypatch, lambda i: 70.0)
    out = _verify(limiting_part="winding", limit_c=121.0)    # winding reads 120
    assert out["verified"] is True and out["passes"] == 1
    assert seen == [pytest.approx(8.095)]


# ------------------------------------------------- the continuous-point check

def test_continuous_point_judges_the_housing_too(monkeypatch):
    seen = {}

    def fake_verify(body, **kw):
        seen.update(part=kw["limiting_part"], limit=kw["limit_c"],
                    passes=kw["max_passes"])
        return {"verified": False, "passes": 4, "miss_K": 3.0,
                "I_cont_A_rms": 6.0, "actual_c": 73.0,
                "em": {"summary": {}}, "field": _field(73.0, winding=100.0),
                "note": "still 3.0 K over its limit"}

    monkeypatch.setattr(cp, "_s1_verify", fake_verify)
    block = {"ok": True, "feasible": False,
             "limits_c": {"winding": 200.0, "housing": 70.0}}
    point = {"feasible": True, "rpm": 1000.0, "I_cont_A_rms": 8.0,
             "limiting_part": "housing",
             "temperatures_c": {"winding": 110.0, "housing": 69.8}}
    cp._cp_verify_continuous_point({"I_phase_rms": 10.0}, block, point,
                                   cooling={"ambient_temp": 25.0},
                                   inverter=None, controller=None,
                                   t_coil=100.0, t_mag=None)
    assert seen == {"part": "housing", "limit": 70.0, "passes": None}
    assert point["verified"] is False
    assert point["verification_trial"]["actual_c"] == 73.0
    assert point["temperatures_c"]["housing"] == 73.0
    assert point["I_cont_A_rms"] < 6.0
