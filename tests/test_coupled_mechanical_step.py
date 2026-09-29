"""The coupled loop's MECHANICAL step (phase 3, 2026-09-08).

The step hands the Mechanical solver the per-part temperatures of the converged
thermal map through ``routes.mechanical.run_rotor_stress_at`` and records the
verdict in the coupling block.  It is OPT-IN (``body["mechanical"] = true``),
takes each part's AVERAGE (max only where no average exists), and a mechanical
refusal is recorded, never raised.  The hook itself is faked here: this file
tests the plumbing, ``tests/test_mechanical_part_temps.py`` tests the solve.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from motor_ai_sim.routes import coupled

FIELD = {"components": {
    "rotor":  {"avg": 126.4, "max": 130.4},
    "magnet": {"avg": 126.6, "max": 131.7},
    "shaft":  {"max": 117.8},                 # a single-band part: max is all it has
    "sleeve": {"avg": 127.0, "max": 131.2},
    "winding": {"avg": 108.1, "max": 121.2},  # not a rotor part — must not be sent
}}


def _fake_hook(store):
    def hook(temps, **kw):
        store["temps"] = dict(temps)
        store["kw"] = dict(kw)
        return {"primary_case": "23,000 rpm", "rpm": 23000.0,
                "interference_effective_mm": 0.05, "elapsed_s": 41.0,
                "cases": {"23,000 rpm": {"sf_min": 0.295, "sf_min_part": "rotor",
                                         "sf_min_p05": 0.678,
                                         "rotor_od_growth_um": 578.6}}}
    return hook


def test_opt_in_only():
    assert coupled._mechanical_step({}, FIELD) is None
    assert coupled._mechanical_step({"mechanical": False}, FIELD) is None


def test_per_part_averages_reach_the_hook(monkeypatch):
    store: dict = {}
    from motor_ai_sim.routes import mechanical as mech
    monkeypatch.setattr(mech, "run_rotor_stress_at", _fake_hook(store))
    block = coupled._mechanical_step({"mechanical": True, "geo": "{}"}, FIELD)
    assert store["temps"] == {"rotor_core": 126.4, "magnet": 126.6,
                              "shaft": 117.8, "sleeve": 127.0}
    assert "winding" not in store["temps"]
    assert store["kw"] == {"geo": "{}"}
    assert block["ok"] is True
    assert block["temps_c"] == store["temps"]
    assert block["case"] == "23,000 rpm" and block["rpm"] == 23000.0
    assert block["sf_min"] == pytest.approx(0.295)
    assert block["sf_min_part"] == "rotor"
    assert block["rotor_od_growth_um"] == pytest.approx(578.6)
    assert block["interference_effective_mm"] == pytest.approx(0.05)


def test_a_mechanical_refusal_is_recorded_not_raised(monkeypatch):
    from motor_ai_sim.routes import mechanical as mech

    def refuse(temps, **kw):
        raise HTTPException(status_code=422, detail={"error": "no sleeve, but a fit"})
    monkeypatch.setattr(mech, "run_rotor_stress_at", refuse)
    block = coupled._mechanical_step({"mechanical": True}, FIELD)
    assert block["ok"] is False
    assert "no sleeve" in block["error"]
    assert block["temps_c"]["magnet"] == 126.6

    def crash(temps, **kw):
        raise RuntimeError("mesh failed")
    monkeypatch.setattr(mech, "run_rotor_stress_at", crash)
    block = coupled._mechanical_step({"mechanical": True}, FIELD)
    assert block["ok"] is False and "mesh failed" in block["error"]


def test_no_rotor_part_in_the_map_is_a_recorded_refusal(monkeypatch):
    from motor_ai_sim.routes import mechanical as mech
    called = {}
    monkeypatch.setattr(mech, "run_rotor_stress_at", _fake_hook(called))
    block = coupled._mechanical_step({"mechanical": True},
                                     {"components": {"winding": {"avg": 100.0}}})
    assert block["ok"] is False and "no rotor part" in block["error"]
    assert "temps" not in called            # the hook was never called


# ── the point of the run, not the boxes of another machine (2026-09-09) ──────
# The Mechanical tab remembers ONE rpm / torque per user.  On 2026-09-09 the
# saved boxes were the Ø200's (rated 20 000, proof 23 000, 245.5 N·m) while the
# coupled run solved a 3 000 rpm generator and then a 25 000 rpm, 1 N·m motor.
# The step now passes the run's own speed and torque, and the caller's
# authorization so the rest of the panel (overspeed factor, fit, mesh,
# contacts) is the USER's rather than the anonymous shared entry's.

def _panel(monkeypatch, **fields):
    from motor_ai_sim.routes import mechanical as mech
    monkeypatch.setattr(mech, "_mech_panel_settings", lambda auth=None: dict(fields))


def test_the_run_point_and_the_caller_reach_the_hook(monkeypatch):
    store: dict = {}
    from motor_ai_sim.routes import mechanical as mech
    monkeypatch.setattr(mech, "run_rotor_stress_at", _fake_hook(store))
    _panel(monkeypatch, cases="three", rpm="25000", rpm1="23000", torque="245.5")
    block = coupled._mechanical_step({"mechanical": True}, FIELD,
                                     authorization="Bearer me", rpm=25000.0,
                                     torque_nm=1.07)
    assert store["kw"]["authorization"] == "Bearer me"
    assert store["kw"]["rpm"] == 25000.0          # the run's
    assert store["kw"]["torque_nm"] == 1.07       # the run's, not the box's 245.5
    assert block["torque_nm"] == 1.07 and "note" not in block


def test_single_mode_solves_the_run_s_speed_not_the_proof_box(monkeypatch):
    """User 2026-09-09 morning: the coupled step had solved the 13 000 rpm L12
    at the Ø200's 23 000 proof speed — "обороты должны быть правильными"."""
    store: dict = {}
    from motor_ai_sim.routes import mechanical as mech
    monkeypatch.setattr(mech, "run_rotor_stress_at", _fake_hook(store))
    _panel(monkeypatch, cases="single", rpm="20000", rpm1="23000")
    block = coupled._mechanical_step({"mechanical": True}, FIELD,
                                     authorization="Bearer me", rpm=13000.0,
                                     torque_nm=0.623)
    assert store["kw"]["rpm"] == 13000.0
    assert "23,000" in block["note"] and "13,000" in block["note"]
    # …and a box that already agrees earns no note
    block2 = coupled._mechanical_step({"mechanical": True}, FIELD,
                                      authorization="Bearer me", rpm=23000.0,
                                      torque_nm=250.4)
    assert store["kw"]["rpm"] == 23000.0 and "note" not in block2


def test_three_case_mode_takes_the_run_s_speed_as_the_rated_one(monkeypatch):
    store: dict = {}
    from motor_ai_sim.routes import mechanical as mech
    monkeypatch.setattr(mech, "run_rotor_stress_at", _fake_hook(store))
    _panel(monkeypatch, cases="three", rpm="20000", rpm1="23000", osf="1.2")
    block = coupled._mechanical_step({"mechanical": True}, FIELD,
                                     authorization="Bearer me", rpm=25000.0,
                                     torque_nm=1.07)
    assert store["kw"]["rpm"] == 25000.0           # the route applies osf to it
    assert "20,000" in block["note"]


def test_no_point_means_the_hook_s_own_precedence(monkeypatch):
    store: dict = {}
    from motor_ai_sim.routes import mechanical as mech
    monkeypatch.setattr(mech, "run_rotor_stress_at", _fake_hook(store))
    _panel(monkeypatch, cases="single", rpm1="23000")
    coupled._mechanical_step({"mechanical": True}, FIELD)
    assert "rpm" not in store["kw"] and "torque_nm" not in store["kw"]
    assert "authorization" not in store["kw"]


def test_the_shaft_torque_is_the_tab_s_number_not_the_2d_mean():
    """User 2026-09-09: "0.59 Nm" — the Electromagnetic tab shows the 2-D mean
    times the machine's 3-D end-effect factor, and the rotor is solved at THAT."""
    t, note = coupled._shaft_torque_nm({"T_em_avg_Nm": 0.623,
                                        "end3d": {"k_flux": 0.947}})
    assert t == pytest.approx(0.623 * 0.947)
    assert "3-D" in note and "0.947" in note
    assert coupled._shaft_torque_nm({"T_em_avg_Nm": 0.623}) == (0.623, None)
    assert coupled._shaft_torque_nm({"T_em_avg_Nm": 0.623, "end3d": None}) == (0.623, None)
    assert coupled._shaft_torque_nm({}) == (None, None)
    assert coupled._shaft_torque_nm({"T_em_avg_Nm": "nan"}) == (None, None)


def test_the_map_s_temperatures_go_through_and_a_sleeveless_block_says_they_are_no_load(monkeypatch):
    """User 2026-09-09: "нам нужно учитывать температуру только как изменение
    давления на бандаж, если он есть".  The rule lives in the SOLVER
    (``solve_rotor_stress(thermal_model="band_fit")``, tests/test_mechanical_
    part_temps.py): this step passes the map's own numbers per part, exactly
    as the Thermal tab reported them, and on a machine with no band the block
    carries one line saying they are not a load.  A machine WITH a band goes
    through the same way — there the temperatures move the fit."""
    store: dict = {}
    from motor_ai_sim.routes import mechanical as mech
    monkeypatch.setattr(mech, "run_rotor_stress_at", _fake_hook(store))
    no_band = {"components": {"rotor": {"avg": 133.9, "max": 140.0},
                              "magnet": {"avg": 134.7, "max": 136.0},
                              "shaft": {"avg": 133.3, "max": 134.0}}}
    block = coupled._mechanical_step({"mechanical": True}, no_band)
    assert store["temps"] == {"rotor_core": 133.9, "magnet": 134.7, "shaft": 133.3}
    assert block["temps_c"] == store["temps"]
    assert "no retaining band" in block["temps_note"]
    assert "not a mechanical load" in block["temps_note"]
    assert "134" in block["temps_note"]
    # with a band the per-part temperatures go through the same way, no note
    block2 = coupled._mechanical_step({"mechanical": True}, FIELD)
    assert store["temps"]["sleeve"] == pytest.approx(127.0)
    assert store["temps"]["magnet"] == pytest.approx(126.6)
    assert "temps_note" not in block2


def test_the_mechanical_point_rule():
    assert coupled._mechanical_point({}, None) == (None, None)
    assert coupled._mechanical_point({"cases": "three", "rpm": "25000"}, 25000.0) == (25000.0, None)
    rpm, note = coupled._mechanical_point({"cases": "three", "rpm": "20000"}, 25000.0)
    assert rpm == 25000.0 and "20,000" in note
    rpm, note = coupled._mechanical_point({"cases": "single", "rpm1": "23000"}, 13000.0)
    assert rpm == 13000.0 and "proof rpm" in note and "23,000" in note
    # an empty box: the operating speed, quietly
    assert coupled._mechanical_point({"cases": "single", "rpm1": ""}, 3000.0) == (3000.0, None)
