"""Loading a motor moves the account's Thermal cooling to that motor's application.

Owner 2026-10-15: CIANO28 85 L13 (a robot motor, cooling "robotics") was refused
with "propeller selection belongs to a different motor" because the account-wide
Thermal record still held the propeller source of the CIANO14 40 drone motor.
Pure functions, spies and a temporary panel-settings store only: no FEM, no API.
"""
from __future__ import annotations

import pytest

from motor_ai_sim import thermal_settings as ts

EMAIL = "vadim@example.test"
PROP = {"die": "CIANO14 40 new", "config": "L12", "ref_id": "cat_prop", "duty": None}
ROBOT = {"die": "CIANO28 85", "config": "L13", "ref_id": "cat_robot", "duty": None}
KEY_PROP = "%s|CIANO14 40 new|L12|cat_prop" % EMAIL
KEY_ROBOT = "%s|CIANO28 85|L13|cat_robot" % EMAIL

OPTIONS = {
    ("CIANO14 40 new", "L12"): {
        "applications": ["propeller"], "application": "propeller",
        "cooling_options": ["propeller_air"],
        "propellers": ["fpv_10x5", "p12x4"],
        "propeller_details": [{"id": "fpv_10x5", "selectable": True},
                              {"id": "p12x4", "selectable": True}],
        "default_propeller": "p12x4",
    },
    ("CIANO28 85", "L13"): {
        "applications": ["robot"], "application": "robot",
        "cooling_options": ["robotics"], "propellers": [], "propeller_details": [],
        "default_propeller": None,
    },
    ("MARINE", "L1"): {
        "applications": ["marine"], "application": "marine",
        "cooling_options": None, "propellers": [], "propeller_details": [],
        "default_propeller": None,
    },
    ("PLAIN", "L1"): {
        "applications": [], "application": None, "cooling_options": None,
        "propellers": None, "propeller_details": [], "default_propeller": None,
    },
}


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """A temporary Thermal store, the cooling-options registry above, and the
    account's remembered motor that the run-time guard reads."""
    from motor_ai_sim.routes import account, family, panel_settings, propellers
    monkeypatch.setattr(panel_settings, "_store_path",
                        lambda: str(tmp_path / ".panel_settings.json"))
    state = {"selection": None}

    def options(die, config=None, authorization=None):
        return OPTIONS[(die, config)]

    monkeypatch.setattr(propellers, "get_cooling_options", options)
    monkeypatch.setattr(account, "get_last_motor", lambda **_kw: {
        "user": EMAIL, "selection": state["selection"]})
    monkeypatch.setattr(family, "_read_ctx", lambda: (
        {"die": state["selection"]["die"], "config": state["selection"]["config"]}
        if state["selection"] else None))
    return state


def _load(store, prev, new):
    """What PUT /api/me/last_motor does after remembering ``new``."""
    store["selection"] = dict(new)
    return ts.follow_loaded_motor(EMAIL, new, prev)


def _runs(store) -> dict:
    """Everything a coupled Run does with the stored settings before solving."""
    stored = ts._stored_thermal(EMAIL)
    settings, _note = ts.settle_application_cooling(stored)
    assert ts.propeller_cooling_context_issue(settings) is None
    assert ts.cooling_issue(settings) is None
    return ts.cooling_fields(settings)


def test_propeller_motor_then_robot_motor_runs_with_robotics_cooling(store):
    # the first ever load initialises the cooling from the application's rule
    first = _load(store, None, PROP)
    assert first["source"] == "application" and first["motor_changed"] is True
    # the user binds the propeller motor the way the Thermal tab does
    ts._save_thermal_patch(EMAIL, {"coolMode": "air", "airSpeedSource": "propeller",
                                   "propellerId": "fpv_10x5",
                                   "propellerContextKey": KEY_PROP, "ambientT": "30"})
    assert _runs(store)["air_speed_source"] == "propeller"

    out = _load(store, PROP, ROBOT)
    assert out["changed"] is True and out["application"] == "robot"
    assert out["settings"] == {"coolMode": "robotics", "airSpeedSource": "manual",
                               "propellerId": "", "propellerContextKey": ""}
    cooling = _runs(store)
    assert cooling["cooling_mode"] == "robotics"
    assert "air_speed_source" not in cooling and "propeller_id" not in cooling
    # the robot starts from the panel defaults, not from the propeller motor's
    # fields: nothing the previous motor typed leaks in (2026-10-15 review)
    assert ts._stored_thermal(EMAIL)["ambientT"] == "40"


def test_robot_motor_then_propeller_motor_gets_its_default_propeller_and_fresh_key(store):
    _load(store, None, ROBOT)
    out = _load(store, ROBOT, PROP)
    assert out["changed"] is True and out["application"] == "propeller"
    assert out["settings"] == {"coolMode": "air", "airSpeedSource": "propeller",
                               "propellerId": "p12x4", "propellerContextKey": KEY_PROP}
    assert out["allowed_propellers"] == ["fpv_10x5", "p12x4"]
    cooling = _runs(store)
    assert cooling["air_speed_source"] == "propeller"
    assert cooling["propeller_id"] == "p12x4"


def test_manual_edit_survives_reloading_the_same_motor(store):
    _load(store, None, PROP)
    _load(store, PROP, ROBOT)
    _load(store, ROBOT, PROP)
    ts._save_thermal_patch(EMAIL, {"propellerId": "fpv_10x5"})      # a deliberate pick
    again = _load(store, PROP, dict(PROP, duty="peak"))             # same motor, another duty
    assert again["changed"] is False
    assert ts._stored_thermal(EMAIL)["propellerId"] == "fpv_10x5"

    _load(store, PROP, ROBOT)
    ts._save_thermal_patch(EMAIL, {"coolMode": "manual", "hConv": "40"})
    again = _load(store, ROBOT, ROBOT)
    assert again["changed"] is False
    assert ts._stored_thermal(EMAIL)["coolMode"] == "manual"


def test_aviation_marine_and_unset_applications(store):
    ts._save_thermal_patch(EMAIL, {"coolMode": "air", "airSpeedSource": "propeller",
                                   "propellerId": "fpv_10x5",
                                   "propellerContextKey": KEY_PROP})
    marine = {"die": "MARINE", "config": "L1", "ref_id": None, "duty": None}
    assert _load(store, PROP, marine)["settings"]["coolMode"] == "liquid"
    assert _runs(store)["cooling_mode"] == "liquid"

    ts._save_thermal_patch(EMAIL, {"coolMode": "air", "airSpeedSource": "propeller",
                                   "propellerId": "x", "propellerContextKey": "k"})
    plain = {"die": "PLAIN", "config": "L1", "ref_id": None, "duty": None}
    out = _load(store, marine, plain)
    # no application: only the leftover propeller source goes, the mode stays
    assert out["settings"]["airSpeedSource"] == "manual"
    assert out["settings"]["coolMode"] == "air"


def test_run_switches_a_stale_propeller_source_on_a_robot_motor(store):
    """The 2026-10-15 symptom: the record still holds the drone motor's choice
    and nothing reloaded the motor.  The Run switches, it does not refuse."""
    store["selection"] = dict(ROBOT)
    ts._save_thermal_patch(EMAIL, {"coolMode": "air", "airSpeedSource": "propeller",
                                   "propellerId": "fpv_10x5",
                                   "propellerContextKey": KEY_PROP})
    # the old behaviour: the generic refusal ...
    issue = ts.propeller_cooling_context_issue(ts._stored_thermal(EMAIL))
    # ... now names the motor and what to do
    assert "robot motor" in issue and "Robotics" in issue
    assert "different motor" not in issue

    cooling = _runs(store)
    assert cooling["cooling_mode"] == "robotics"
    stored = ts._stored_thermal(EMAIL)
    assert stored["airSpeedSource"] == "manual" and stored["coolMode"] == "robotics"
    assert stored["propellerId"] == "" and stored["propellerContextKey"] == ""


def test_settle_uses_the_duty_motor_not_only_the_loaded_one(store):
    store["selection"] = dict(PROP)
    stale = {"airSpeedSource": "propeller", "propellerId": "fpv_10x5",
             "propellerContextKey": KEY_PROP, "coolMode": "air"}
    out, note = ts.settle_application_cooling(
        stale, expected_die="CIANO28 85", expected_config="L13")
    assert out["coolMode"] == "robotics" and out["airSpeedSource"] == "manual"
    assert "switched to Robotics" in note
    same, none_note = ts.settle_application_cooling(stale)       # loaded motor takes props
    assert same == stale and none_note is None


def test_guard_still_refuses_a_forged_or_foreign_key_on_a_propeller_motor(store):
    store["selection"] = dict(PROP)
    foreign = {"airSpeedSource": "propeller", "propellerId": "fpv_10x5",
               "propellerContextKey": "other@example.test|CIANO14 40 new|L12|cat_prop"}
    issue = ts.propeller_cooling_context_issue(foreign)
    assert issue and "different motor or account" in issue
    # settle does not rescue it: the motor does take propellers
    kept, note = ts.settle_application_cooling(foreign)
    assert kept == foreign and note is None
    forged = dict(foreign, propellerContextKey=KEY_ROBOT)
    assert ts.propeller_cooling_context_issue(forged)


def test_follow_never_raises_when_the_registry_is_unreadable(store, monkeypatch):
    from motor_ai_sim.routes import propellers

    def boom(*_a, **_k):
        raise RuntimeError("registry down")

    monkeypatch.setattr(propellers, "get_cooling_options", boom)
    out = ts.follow_loaded_motor(EMAIL, ROBOT, PROP)
    assert out["changed"] is False and out["settings"] == {}
