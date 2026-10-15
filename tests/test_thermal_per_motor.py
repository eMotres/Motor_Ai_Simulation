"""The Thermal cooling fields belong to the MOTOR, not to the account.

Owner 2026-10-15: after the 40 mm propeller motor (frame open, bore air 20 m/s)
the 200 mm liquid-cooled L155 was solved with the open frame and the 40 mm bore
air, because only a few keys followed the loaded motor and the rest stayed from
the previous one.  Now a motor's fields are remembered per motor
(``thermal -> <email> -> by_motor``), a configuration may declare its own
(``thermal_defaults:``) and a motor with neither starts from its application's
rule with the frame and the bore reset.

Pure functions, spies and a temporary panel-settings store only: no FEM, no API.
"""
from __future__ import annotations

import pytest

from motor_ai_sim import thermal_settings as ts

EMAIL = "vadim@example.test"
PROP = {"die": "CIANO14 40 new", "config": "L12", "ref_id": "cat_prop", "duty": None}
L155 = {"die": "CIANO10 200 opt", "config": "L155 motor", "ref_id": "cat_l155", "duty": None}
ROBOT = {"die": "CIANO28 85", "config": "L13", "ref_id": "cat_robot", "duty": None}
KEY_PROP = "%s|CIANO14 40 new|L12|cat_prop" % EMAIL
MOTOR_PROP = "CIANO14 40 new|L12"
MOTOR_L155 = "CIANO10 200 opt|L155 motor"
MOTOR_ROBOT = "CIANO28 85|L13"

OPTIONS = {
    ("CIANO14 40 new", "L12"): {
        "applications": ["propeller"], "application": "propeller",
        "cooling_options": ["propeller_air"],
        "propellers": ["fpv_10x5", "p12x4"],
        "propeller_details": [{"id": "fpv_10x5", "selectable": True},
                              {"id": "p12x4", "selectable": True}],
        "default_propeller": "p12x4",
    },
    ("CIANO10 200 opt", "L155 motor"): {
        "applications": ["aviation"], "application": "aviation",
        "cooling_options": None, "propellers": None, "propeller_details": [],
        "default_propeller": None,
    },
    ("CIANO28 85", "L13"): {
        "applications": ["robot"], "application": "robot",
        "cooling_options": ["robotics"], "propellers": [], "propeller_details": [],
        "default_propeller": None,
    },
}

#: The 40 mm propeller motor as the owner had it: open frame, 20 m/s bore air.
FORTY_MM = {"coolMode": "air", "airSpeedSource": "propeller", "propellerId": "p12x4",
            "propellerContextKey": KEY_PROP, "frame": "open", "openAirSpeed": "0",
            "boreMode": "air", "boreAirSpeed": "20", "ambientT": "30",
            "airSpeed": "10", "fluid": "water", "tIn": "40", "flowLpm": "8",
            "maxIter": "6"}


@pytest.fixture()
def world(tmp_path, monkeypatch):
    """A temporary Thermal store, the cooling-options registry above, the
    account's loaded motor and the catalogue context, and configuration docs."""
    from motor_ai_sim import duty_results
    from motor_ai_sim.routes import account, family, panel_settings, propellers
    monkeypatch.setattr(panel_settings, "_store_path",
                        lambda: str(tmp_path / ".panel_settings.json"))
    monkeypatch.setattr(panel_settings, "_who", lambda authorization=None: EMAIL)
    state = {"selection": None, "docs": {}}

    def options(die, config=None, authorization=None):
        return OPTIONS[(die, config)]

    monkeypatch.setattr(propellers, "get_cooling_options", options)
    monkeypatch.setattr(account, "get_last_motor", lambda **_kw: {
        "user": EMAIL, "selection": state["selection"]})
    monkeypatch.setattr(family, "_read_ctx", lambda: (
        {"die": state["selection"]["die"], "config": state["selection"]["config"]}
        if state["selection"] else None))
    monkeypatch.setattr(family, "config_doc",
                        lambda die, cfg: state["docs"].get((die, cfg)))
    monkeypatch.setattr(duty_results, "active_context", lambda: (
        (state["selection"]["die"], state["selection"]["config"], "rated")
        if state["selection"] else None))
    return state


def _seed(settings, motor=None, source="saved"):
    """The account's Thermal entry as an earlier session left it."""
    from motor_ai_sim.routes import panel_settings as ps
    d = ps._load()
    entry = {"settings": dict(settings), "updated_at": "2026-10-15T08:00:00+00:00"}
    if motor:
        entry.update(motor=motor, source=source)
    d.setdefault("thermal", {})[EMAIL] = entry
    ps._save(d)


def _load(world, prev, new):
    """What PUT /api/me/last_motor does after remembering ``new``."""
    world["selection"] = dict(new)
    return ts.follow_loaded_motor(EMAIL, new, prev)


def _user_saves(settings, motor=None):
    """The Thermal panel's debounced PUT (``motor`` = the motor it names)."""
    from motor_ai_sim.routes import panel_settings as ps
    body = {"settings": dict(settings)}
    if motor:
        body["motor"] = dict(zip(("die", "config"), motor.split("|")))
    return ps.put_panel_settings(panel="thermal", body=body, authorization=None)


def _stored():
    return ts._stored_thermal(EMAIL)


# ── 1. a different motor, with and without its own record ───────────────────

def test_aviation_motor_after_propeller_motor_resets_frame_and_bore(world):
    """The incident: no record for the L155 -> the application's rule, and the
    open frame / bore air of the 40 mm motor are NOT inherited."""
    _seed(FORTY_MM, motor=MOTOR_PROP)
    world["selection"] = dict(PROP)
    out = _load(world, PROP, L155)
    s = _stored()
    assert out["motor_changed"] is True and out["source"] == "application"
    assert out["application"] == "aviation"
    assert (s["coolMode"], s["airSpeedSource"]) == ("liquid", "manual")
    assert (s["frame"], s["boreMode"]) == ("housed", "none")
    assert s["flowLpm"] == "8" and s["ambientT"] == "40"      # the panel defaults, not the 40 mm motor's 30
    cf = ts.cooling_fields(s)
    assert cf["cooling_mode"] == "liquid" and cf["bore_mode"] == "none"
    assert "frame" not in cf and "bore_air_speed_mps" not in cf
    assert out["fields"]["frame"] == "housed"
    assert ts._stored_thermal(EMAIL)["frame"] == "housed"


def test_aviation_motor_with_its_own_record_gets_it_back(world):
    """The record the owner saved for the L155 wins over every default, and the
    40 mm motor's record comes back when it is loaded again."""
    _seed({**FORTY_MM, "boreAirSpeed": "15"}, motor=MOTOR_PROP)
    world["selection"] = dict(PROP)
    _user_saves(FORTY_MM, MOTOR_PROP)                      # a real cooling edit: a record
    l155 = {**FORTY_MM, "coolMode": "liquid", "airSpeedSource": "manual",
            "propellerId": "", "propellerContextKey": "", "frame": "housed",
            "boreMode": "none", "fluid": "water", "tIn": "55", "flowLpm": "20"}
    _user_saves(l155, MOTOR_L155)
    out = _load(world, PROP, L155)
    s = _stored()
    assert out["source"] == "saved"
    assert (s["coolMode"], s["frame"], s["boreMode"], s["tIn"], s["flowLpm"]) \
        == ("liquid", "housed", "none", "55", "20")
    back = _load(world, L155, PROP)
    s = _stored()
    assert back["source"] == "saved"
    assert (s["frame"], s["boreMode"], s["boreAirSpeed"]) == ("open", "air", "20")
    assert s["airSpeedSource"] == "propeller" and s["propellerContextKey"] == KEY_PROP


def test_saving_a_cooling_edit_writes_the_named_motors_record(world):
    world["selection"] = dict(L155)
    _user_saves({**FORTY_MM, "coolMode": "liquid", "airSpeedSource": "manual",
                 "propellerId": "", "propellerContextKey": ""}, MOTOR_L155)
    from motor_ai_sim.routes import panel_settings as ps
    entry = ps._load()["thermal"][EMAIL]
    assert set(entry["by_motor"]) == {MOTOR_L155}
    assert entry["motor"] == MOTOR_L155 and entry["source"] == "saved"
    assert entry["settings"] == entry["by_motor"][MOTOR_L155]["settings"]   # last-used kept
    # a save naming another motor is the panel's word and stays separate
    world["selection"] = dict(PROP)
    _user_saves(FORTY_MM, MOTOR_PROP)
    entry = ps._load()["thermal"][EMAIL]
    assert set(entry["by_motor"]) == {MOTOR_L155, MOTOR_PROP}
    assert entry["by_motor"][MOTOR_L155]["settings"]["coolMode"] == "liquid"


def test_robot_motor_without_record_gets_robotics_and_an_open_bore(world):
    _seed(FORTY_MM, motor=MOTOR_PROP)
    world["selection"] = dict(PROP)
    out = _load(world, PROP, ROBOT)
    s = _stored()
    assert out["source"] == "application"
    assert (s["coolMode"], s["airSpeedSource"], s["frame"], s["boreMode"]) \
        == ("robotics", "manual", "housed", "still")
    assert ts.cooling_issue(s) is None


def test_propeller_motor_is_housed_unless_its_configuration_says_open(world):
    _seed({**FORTY_MM, "coolMode": "liquid", "airSpeedSource": "manual",
           "propellerId": "", "propellerContextKey": ""}, motor=MOTOR_L155)
    world["selection"] = dict(L155)
    out = _load(world, L155, PROP)
    s = _stored()
    assert out["source"] == "application"
    assert (s["airSpeedSource"], s["propellerId"], s["frame"], s["boreMode"]) \
        == ("propeller", "p12x4", "housed", "none")
    assert s["propellerContextKey"] == KEY_PROP
    # the configuration declares an open frame: honoured, still the application rule
    world["docs"][("CIANO14 40 new", "L12")] = {"frame": "open"}
    _seed(s, motor=MOTOR_L155)
    _load(world, L155, PROP)
    assert _stored()["frame"] == "open"


# ── 2. the same motor loaded again keeps manual edits ───────────────────────

def test_same_motor_reloaded_keeps_the_users_edits(world):
    _seed(FORTY_MM, motor=MOTOR_PROP)
    world["selection"] = dict(PROP)
    _load(world, PROP, L155)
    _user_saves({**_stored(), "flowLpm": "12", "tIn": "35", "boreMode": "liquid"},
                MOTOR_L155)
    again = _load(world, L155, L155)
    s = _stored()
    assert again["motor_changed"] is False and again["changed"] is False
    # nothing changed, but the answer still carries the motor's fields, so a
    # browser that lost an earlier answer can resynchronise from it
    assert again["fields"]["flowLpm"] == "12" and again["fields"]["boreMode"] == "liquid"
    assert (s["flowLpm"], s["tIn"], s["boreMode"]) == ("12", "35", "liquid")
    # even when only the catalogue reference differs, the saved record is what
    # comes back - the edits survive
    _load(world, L155, {**L155, "ref_id": "cat_l155_b"})
    s = _stored()
    assert (s["flowLpm"], s["tIn"], s["boreMode"]) == ("12", "35", "liquid")


# ── 3. a solve uses the record of the motor being SOLVED ────────────────────

def test_solve_of_motor_b_while_a_is_loaded_uses_bs_record(world):
    world["selection"] = dict(PROP)                       # A is loaded
    _seed(FORTY_MM, motor=MOTOR_PROP)
    b = {"coolMode": "liquid", "airSpeedSource": "manual", "frame": "housed",
         "boreMode": "none", "tIn": "50", "flowLpm": "15", "fluid": "water",
         "ambientT": "25"}
    _user_saves(b, MOTOR_L155)                            # B's record; A stays the active one
    assert _stored()["frame"] == "open"
    s_b, info = ts.motor_thermal_settings(None, "CIANO10 200 opt", "L155 motor")
    assert info["source"] == "saved" and info["motor"] == MOTOR_L155
    assert (s_b["coolMode"], s_b["flowLpm"], s_b["tIn"]) == ("liquid", "15", "50")
    assert ts.cooling_fields(s_b)["cooling_mode"] == "liquid"
    s_a = ts.thermal_panel_settings(None)                 # the loaded motor, as ever
    assert s_a["frame"] == "open" and s_a["boreMode"] == "air"
    assert ts.thermal_panel_settings(None, die="CIANO10 200 opt",
                                     config="L155 motor") == s_b


def test_coupled_run_reads_the_record_of_the_motor_it_solves(world, monkeypatch):
    """The orchestrator itself: a body naming motor B resolves B's record."""
    from motor_ai_sim.routes import coupled
    world["selection"] = dict(PROP)
    _seed(FORTY_MM, motor=MOTOR_PROP)
    _user_saves({"coolMode": "liquid", "airSpeedSource": "manual", "frame": "housed",
                 "boreMode": "none", "flowLpm": "15", "tIn": "50"}, MOTOR_L155)

    seen = {}

    class Stop(Exception):
        pass

    def spy(settings):
        seen["settings"] = dict(settings)
        raise Stop()

    monkeypatch.setattr(coupled, "_preflight", lambda *a, **k: None)
    monkeypatch.setattr(ts, "cooling_issue", lambda s: None)
    monkeypatch.setattr(ts, "cooling_fields", spy)
    with pytest.raises(Stop):
        coupled._run({"die": "CIANO10 200 opt", "config": "L155 motor"}, None)
    assert seen["settings"]["coolMode"] == "liquid"
    assert seen["settings"]["frame"] == "housed" and seen["settings"]["flowLpm"] == "15"
    seen.clear()
    with pytest.raises(Stop):
        coupled._run({}, None)                            # no motor named: the loaded one (A)
    assert seen["settings"]["frame"] == "open"


def test_solve_without_a_record_never_takes_another_motors_fields(world):
    """A is loaded and saved; B has no record: B is solved with its application's
    rule and the result says so - not with A's open frame and bore air."""
    world["selection"] = dict(PROP)
    _seed(FORTY_MM, motor=MOTOR_PROP)
    s, info = ts.motor_thermal_settings(None, "CIANO10 200 opt", "L155 motor")
    assert info["source"] == "application" and info["application"] == "aviation"
    assert "CIANO10 200 opt L155 motor" in info["note"]
    assert "application default" in info["note"]
    assert (s["coolMode"], s["frame"], s["boreMode"]) == ("liquid", "housed", "none")
    cf = ts.cooling_fields(s)
    assert "frame" not in cf and cf["bore_mode"] == "none"
    # A is untouched by the lookup
    assert _stored()["frame"] == "open"


def test_legacy_entry_without_a_motor_marker_is_the_loaded_motors(world):
    """An entry saved before motors were recorded: for the loaded motor it is what
    the panel shows; for any other motor it is NOT taken."""
    _seed(FORTY_MM)                                       # no marker, no by_motor
    world["selection"] = dict(PROP)
    s, info = ts.motor_thermal_settings(None)
    assert info["source"] == "last_used" and s["frame"] == "open"
    s, info = ts.motor_thermal_settings(None, "CIANO10 200 opt", "L155 motor")
    assert info["source"] == "application" and s["frame"] == "housed"


def test_no_motor_at_all_is_the_last_used_fields(world):
    _seed(FORTY_MM)
    s, info = ts.motor_thermal_settings(None)
    assert info["source"] == "last_used" and info["motor"] == ""
    assert s["frame"] == "open"


# ── 4. the configuration's own defaults ─────────────────────────────────────

def test_configuration_thermal_defaults_are_used(world):
    world["docs"][("CIANO10 200 opt", "L155 motor")] = {"thermal_defaults": {
        "coolMode": "liquid", "fluid": "water_glycol_50", "tIn": 45, "flowLpm": 22.5,
        "frame": "housed", "boreMode": "air", "boreAirSpeed": 12,
        "airSpeedSource": "propeller",      # refused: the binding is the loaded motor's
        "bogus": "x", "tIn_extra": [1]}}
    _seed(FORTY_MM, motor=MOTOR_PROP)
    world["selection"] = dict(PROP)
    out = _load(world, PROP, L155)
    s = _stored()
    assert out["source"] == "config"
    assert (s["fluid"], s["tIn"], s["flowLpm"]) == ("water_glycol_50", "45", "22.5")
    assert (s["boreMode"], s["boreAirSpeed"], s["frame"]) == ("air", "12", "housed")
    assert s["airSpeedSource"] == "manual" and "bogus" not in s
    # a saved record still outranks the configuration's defaults
    _user_saves({**s, "tIn": "60"}, MOTOR_L155)
    _load(world, L155, PROP)
    again = _load(world, PROP, L155)
    assert again["source"] == "saved" and _stored()["tIn"] == "60"


def test_defaults_apply_to_a_solve_without_a_record_and_say_so(world):
    world["docs"][("CIANO10 200 opt", "L155 motor")] = {
        "thermal_defaults": {"coolMode": "liquid", "flowLpm": "30", "tIn": "42"}}
    world["selection"] = dict(PROP)
    _seed(FORTY_MM, motor=MOTOR_PROP)
    s, info = ts.motor_thermal_settings(None, "CIANO10 200 opt", "L155 motor")
    assert info["source"] == "config" and "configuration defaults" in info["note"]
    assert (s["flowLpm"], s["tIn"], s["frame"], s["boreMode"]) == ("30", "42", "housed", "none")


def test_defaults_may_open_the_frame_of_a_propeller_motor(world):
    world["docs"][("CIANO14 40 new", "L12")] = {"thermal_defaults": {
        "frame": "open", "boreMode": "none"}}
    _seed({"coolMode": "liquid", "frame": "housed", "boreMode": "air"}, motor=MOTOR_L155)
    world["selection"] = dict(L155)
    out = _load(world, L155, PROP)
    s = _stored()
    assert out["source"] == "config"
    assert (s["frame"], s["boreMode"], s["airSpeedSource"]) == ("open", "none", "propeller")


# ── 5. the stores stay compatible ───────────────────────────────────────────

def test_put_keeps_other_motors_records_and_last_used_stays_readable(world):
    from motor_ai_sim.routes import panel_settings as ps
    world["selection"] = dict(PROP)
    _user_saves(FORTY_MM, MOTOR_PROP)
    world["selection"] = dict(L155)
    _user_saves({"coolMode": "liquid", "tIn": "55"}, MOTOR_L155)
    world["selection"] = dict(ROBOT)
    out = _user_saves({"coolMode": "robotics", "emissivity": "0.5"}, MOTOR_ROBOT)
    assert out["motor"] == MOTOR_ROBOT
    got = ps.get_panel_settings(panel="thermal", authorization=None)
    assert got["settings"] == {"coolMode": "robotics", "emissivity": "0.5"}   # last used
    assert got["motor"] == MOTOR_ROBOT and got["source"] == "saved"
    by = ps._load()["thermal"][EMAIL]["by_motor"]
    assert set(by) == {MOTOR_PROP, MOTOR_L155, MOTOR_ROBOT}
    # other panels are untouched by any of it
    ps.put_panel_settings(panel="mechanical", body={"settings": {"a": "1"}}, authorization=None)
    assert "by_motor" not in ps._load()["mechanical"][EMAIL]


def test_corrected_cooling_is_written_to_the_motors_record_too(world):
    """The run-time safety net (a propeller source on a robot) corrects the
    motor's saved record without touching another motor's."""
    world["selection"] = dict(ROBOT)
    _user_saves({"coolMode": "air", "airSpeedSource": "propeller", "propellerId": "x",
                 "propellerContextKey": "k"}, MOTOR_ROBOT)
    other = {**FORTY_MM, "boreAirSpeed": "17"}
    world["selection"] = dict(PROP)
    _user_saves(other, MOTOR_PROP)
    fixed, _note = ts.settle_application_cooling(
        ts.motor_thermal_settings(None, "CIANO28 85", "L13")[0], None,
        expected_die="CIANO28 85", expected_config="L13")
    assert fixed["coolMode"] == "robotics" and fixed["airSpeedSource"] == "manual"
    ts._save_thermal_patch(EMAIL, {"coolMode": "robotics", "airSpeedSource": "manual"}, MOTOR_ROBOT)
    s, info = ts.motor_thermal_settings(None, "CIANO28 85", "L13")
    assert info["source"] == "saved" and s["coolMode"] == "robotics"
    assert ts.motor_thermal_settings(None, "CIANO14 40 new", "L12")[0]["boreAirSpeed"] == "17"


# ═══ the independent review of PR #218 (Astra, FIX FIRST): one test per finding ═══

def test_f1_first_load_of_an_account_initialises_the_cooling(world):
    """Finding 1: a brand-new account's first selection used to resolve nothing -
    an aviation motor then solved as still air at 40 degC."""
    world["docs"][("CIANO10 200 opt", "L155 motor")] = {
        "thermal_defaults": {"flowLpm": 20, "tIn": 45}}
    out = _load(world, None, L155)                         # empty store, no previous motor
    s = _stored()
    assert out["motor_changed"] is True and out["source"] == "config"
    assert (s["coolMode"], s["flowLpm"], s["tIn"]) == ("liquid", "20", "45")
    assert (s["frame"], s["boreMode"]) == ("housed", "none")
    from motor_ai_sim.routes import panel_settings as ps
    assert ps._load()["thermal"][EMAIL]["motor"] == MOTOR_L155
    assert ts.cooling_fields(ts.motor_thermal_settings(None)[0])["cooling_mode"] == "liquid"
    # a robot's first load is robotics with an open bore
    robot = _load(world, None, ROBOT)
    s = _stored()
    assert robot["source"] == "application"
    assert (s["coolMode"], s["boreMode"]) == ("robotics", "still")


def test_f1_legacy_manual_entry_on_first_selection_gets_the_rules(world):
    """...and an entry from before motors were recorded, met on the first
    selection of a DIFFERENT motor, does not survive it."""
    _seed(FORTY_MM)                                        # no marker
    out = _load(world, None, L155)                         # no previous motor to compare
    s = _stored()
    assert out["source"] == "application"
    assert (s["coolMode"], s["frame"], s["boreMode"]) == ("liquid", "housed", "none")


def test_f1_legacy_entry_is_claimed_not_reset_when_its_own_motor_reloads(world):
    """The explicit legacy path: the same motor loaded again after the upgrade keeps
    its fields, and from then on the entry is its."""
    _seed(FORTY_MM)
    world["selection"] = dict(PROP)
    out = _load(world, PROP, PROP)
    s = _stored()
    assert out["motor_changed"] is False and out["source"] == "last_used"
    assert (s["frame"], s["boreAirSpeed"]) == ("open", "20")
    from motor_ai_sim.routes import panel_settings as ps
    assert ps._load()["thermal"][EMAIL]["motor"] == MOTOR_PROP


def test_f2_sparse_saved_record_does_not_inherit_the_previous_motors_fields(world):
    """Finding 2: B's record omits frame / bore / shaft; A (open, bore air, 100 mm
    of shaft) must not leak into B, and the panel and the backend agree."""
    a = {**FORTY_MM, "shaftExtMm": "100"}
    _seed(a, motor=MOTOR_PROP)
    world["selection"] = dict(PROP)
    world["selection"] = dict(L155)
    _user_saves({"coolMode": "liquid", "airSpeedSource": "manual", "flowLpm": "15",
                 "tIn": "50"}, MOTOR_L155)                 # sparse; not promoted (A active)
    out = _load(world, PROP, L155)
    s = _stored()
    assert out["source"] == "saved"
    assert (s["frame"], s["boreMode"], s["shaftExtMm"]) == ("housed", "none", "0")
    assert (s["flowLpm"], s["tIn"]) == ("15", "50")
    backend, info = ts.motor_thermal_settings(None, "CIANO10 200 opt", "L155 motor")
    assert info["source"] == "saved"
    assert ts.cooling_fields(backend) == ts.cooling_fields(s)
    assert "shaft_ext_length_mm" not in ts.cooling_fields(s)


def test_f2_no_record_does_not_keep_the_previous_motors_shaft_or_ambient(world):
    _seed({**FORTY_MM, "shaftExtMm": "100", "shaftExtSides": "1", "hConv": "99"}, motor=MOTOR_PROP)
    world["selection"] = dict(PROP)
    _load(world, PROP, L155)
    s = _stored()
    assert (s["shaftExtMm"], s["shaftExtSides"], s["hConv"], s["ambientT"]) \
        == ("0", "2", "50", "40")
    assert "shaft_ext_length_mm" not in ts.cooling_fields(s)


def test_f3_a_delayed_save_of_motor_a_does_not_replace_motor_bs_settings(world):
    """Finding 3: edit A, load B before the debounce fires, then A's PUT lands."""
    from motor_ai_sim.routes import panel_settings as ps
    _seed(FORTY_MM, motor=MOTOR_PROP)
    world["selection"] = dict(PROP)
    _load(world, PROP, L155)                               # B loaded: top-level is B's
    b_before = dict(_stored())
    edited_a = {**FORTY_MM, "boreAirSpeed": "25", "maxIter": "9"}
    _user_saves(edited_a, MOTOR_PROP)                      # A's delayed save arrives
    entry = ps._load()["thermal"][EMAIL]
    assert entry["motor"] == MOTOR_L155
    s = entry["settings"]
    assert {k: s[k] for k in ts.COOLING_KEYS} == {k: b_before[k] for k in ts.COOLING_KEYS}
    assert s["maxIter"] == "9"                             # the account-level field is the newest
    assert entry["by_motor"][MOTOR_PROP]["settings"]["boreAirSpeed"] == "25"
    assert ts.motor_thermal_settings(None, "CIANO10 200 opt", "L155 motor")[0]["frame"] == "housed"


def test_f3_a_save_naming_no_motor_never_writes_a_record_or_the_cooling(world):
    from motor_ai_sim.routes import panel_settings as ps
    _seed(FORTY_MM, motor=MOTOR_PROP)
    world["selection"] = dict(PROP)
    _load(world, PROP, L155)
    before = dict(_stored())
    _user_saves({**FORTY_MM, "maxIter": "3"})              # an old client: no identity
    entry = ps._load()["thermal"][EMAIL]
    assert "by_motor" not in entry or MOTOR_L155 not in entry["by_motor"]
    assert {k: entry["settings"][k] for k in ts.COOLING_KEYS} \
        == {k: before[k] for k in ts.COOLING_KEYS}
    assert entry["settings"]["maxIter"] == "3" and entry["motor"] == MOTOR_L155


def test_f3_a_first_ever_save_naming_the_loaded_motor_becomes_its_active_settings(world):
    from motor_ai_sim.routes import panel_settings as ps
    world["selection"] = dict(L155)
    _user_saves({"coolMode": "liquid", "tIn": "33"}, MOTOR_L155)
    entry = ps._load()["thermal"][EMAIL]
    assert entry["motor"] == MOTOR_L155 and entry["settings"]["tIn"] == "33"


def test_f5_controller_for_motor_b_uses_bs_cooling_not_the_active_motors(world):
    """Finding 5: A active (liquid 60 degC / 2 L/min); a controller request for B
    (liquid 25 degC / 20 L/min) must inherit B's, not A's."""
    from motor_ai_sim.routes import controller as rc
    world["selection"] = dict(L155)                        # A = the L155, active
    _seed({"coolMode": "liquid", "airSpeedSource": "manual", "fluid": "water",
           "tIn": "60", "flowLpm": "2", "ambientT": "35"}, motor=MOTOR_L155)
    world["selection"] = dict(L155)
    # B = the robot-side motor's record, saved while A stays active
    _user_saves({"coolMode": "liquid", "airSpeedSource": "manual", "fluid": "water",
                 "tIn": "25", "flowLpm": "20", "ambientT": "22"}, MOTOR_ROBOT)
    out = rc._thermal_cooling_for_mode("liquid", "CIANO28 85", "L13", None, None)
    assert out["fields"] == {"coolant": "water", "flow_lpm": 20.0, "t_in_c": 25.0}
    own = rc._thermal_cooling_for_mode("liquid", "CIANO10 200 opt", "L155 motor", None, None)
    assert own["fields"] == {"coolant": "water", "flow_lpm": 2.0, "t_in_c": 60.0}


def test_f6_a_family_context_without_a_duty_names_the_motor(world, monkeypatch):
    """Finding 6: the machine is activated through the API with no duty and the
    account's last-motor preference still names A (or nothing)."""
    from motor_ai_sim import duty_results
    from motor_ai_sim.routes import account, family
    _seed(FORTY_MM, motor=MOTOR_PROP)
    world["selection"] = dict(L155)
    _user_saves({"coolMode": "liquid", "airSpeedSource": "manual", "tIn": "48",
                 "flowLpm": "14"}, MOTOR_L155)             # B's record
    # the family context says B with NO duty; active_context() therefore says None
    monkeypatch.setattr(duty_results, "active_context", lambda: None)
    monkeypatch.setattr(family, "_read_ctx",
                        lambda: {"die": "CIANO10 200 opt", "config": "L155 motor", "duty": None})
    for pref in ({"die": "CIANO14 40 new", "config": "L12", "ref_id": "cat_prop"}, None):
        monkeypatch.setattr(account, "get_last_motor", lambda pref=pref, **_kw: {
            "user": EMAIL, "selection": pref})
        s, info = ts.motor_thermal_settings(None)
        assert info["motor"] == MOTOR_L155 and info["source"] == "saved"
        assert (s["coolMode"], s["flowLpm"], s["tIn"]) == ("liquid", "14", "48")
    # with neither a context nor a preference the legacy last-used fallback stays
    monkeypatch.setattr(family, "_read_ctx", lambda: None)
    s, info = ts.motor_thermal_settings(None)
    assert info["motor"] == "" and info["source"] == "last_used"


def test_f7_a_display_only_edit_keeps_defaults_a_default(world):
    """Finding 7: changing the view / the iteration limit while the cooling is the
    configuration's default must not turn it into 'your saved settings'."""
    from motor_ai_sim.routes import panel_settings as ps
    world["docs"][("CIANO10 200 opt", "L155 motor")] = {
        "thermal_defaults": {"flowLpm": 20, "tIn": 45}}
    _load(world, None, L155)
    shown = {**_stored(), "view": "flux", "maxIter": "9"}
    _user_saves(shown, MOTOR_L155)
    entry = ps._load()["thermal"][EMAIL]
    assert "by_motor" not in entry or MOTOR_L155 not in entry["by_motor"]
    assert entry["source"] == "config"
    assert entry["settings"]["view"] == "flux" and entry["settings"]["maxIter"] == "9"
    # a real cooling edit does
    _user_saves({**shown, "tIn": "52"}, MOTOR_L155)
    entry = ps._load()["thermal"][EMAIL]
    assert entry["source"] == "saved" and entry["by_motor"][MOTOR_L155]["settings"]["tIn"] == "52"


def test_f8_a_repaired_propeller_binding_reaches_backend_solves(world):
    """Finding 8: A saved under reference R1, loaded again under R2: a solve with no
    explicit cooling must carry R2's binding, and the record must be repaired."""
    from motor_ai_sim.routes import panel_settings as ps
    r1 = {**PROP, "ref_id": "R1"}
    r2 = {**PROP, "ref_id": "R2"}
    key1 = "%s|CIANO14 40 new|L12|R1" % EMAIL
    key2 = "%s|CIANO14 40 new|L12|R2" % EMAIL
    world["selection"] = dict(r1)
    _user_saves({**FORTY_MM, "propellerId": "fpv_10x5", "propellerContextKey": key1,
                 "boreAirSpeed": "5"}, MOTOR_PROP)
    _load(world, r1, ROBOT)
    out = _load(world, ROBOT, r2)
    assert out["source"] == "saved"
    assert _stored()["propellerContextKey"] == key2 and _stored()["propellerId"] == "fpv_10x5"
    assert ps._load()["thermal"][EMAIL]["by_motor"][MOTOR_PROP]["settings"][
        "propellerContextKey"] == key2
    s, _info = ts.motor_thermal_settings(None)
    assert s["propellerContextKey"] == key2
    assert ts.propeller_cooling_context_issue(s) is None


def test_f9_backend_keeps_the_accounts_current_max_iter(world):
    """Finding 9: A saved at maxIter 1; the account later set 6 on B; solving A with
    no explicit settings must use 6 (cooling is the motor's, the iteration limit
    the account's)."""
    world["selection"] = dict(PROP)
    _user_saves({**FORTY_MM, "boreAirSpeed": "9", "maxIter": "1"}, MOTOR_PROP)
    _load(world, PROP, L155)
    _user_saves({**_stored(), "maxIter": "6"}, MOTOR_L155)
    _load(world, L155, PROP)
    s, info = ts.motor_thermal_settings(None, "CIANO14 40 new", "L12")
    assert info["source"] == "saved" and s["boreAirSpeed"] == "9"
    assert s["maxIter"] == "6"
    assert ts.coupled_iteration_settings(s)["max_iter"] == 6


# ═══ second review of PR #218 (f3f4b95e): one test per remaining finding ═════════

def test_r1_a_saved_zero_flow_is_refused_not_repaired(world):
    """R1: flowLpm 0 (or blank) in a record stays what it is; validation refuses it
    by name - the saved record and an explicit request behave the same."""
    for bad in ("0", "-3", ""):
        world["selection"] = dict(L155)
        _user_saves({"coolMode": "liquid", "airSpeedSource": "manual", "tIn": "50",
                     "flowLpm": bad}, MOTOR_L155)
        s, info = ts.motor_thermal_settings(None, "CIANO10 200 opt", "L155 motor")
        assert info["source"] == "saved"
        assert s["flowLpm"] == bad
        assert "flow must be greater than 0" in ts.cooling_issue(s)
        assert ts.cooling_issue({"coolMode": "liquid", "flowLpm": bad}) == ts.cooling_issue(s)
        # loading the motor again does not invent a flow either
        _load(world, PROP, L155)
        assert _stored()["flowLpm"] == bad
    # an ABSENT flow is still the panel default
    from motor_ai_sim.routes import panel_settings as ps
    d = ps._load()
    rec = d["thermal"][EMAIL]["by_motor"][MOTOR_L155]["settings"]
    rec.pop("flowLpm")
    ps._save(d)
    assert ts.motor_thermal_settings(None, "CIANO10 200 opt", "L155 motor")[0]["flowLpm"] == "8"


def test_r3_a_follow_never_overwrites_a_newer_saved_record(world, monkeypatch):
    """R3: B's record is read, a second tab saves flow 20, then the follow resumes
    with its stale snapshot - the 20 L/min edit must survive."""
    from motor_ai_sim.routes import panel_settings as ps
    world["selection"] = dict(L155)
    _user_saves({"coolMode": "liquid", "airSpeedSource": "manual", "tIn": "50",
                 "flowLpm": "8"}, MOTOR_L155)
    _seed_active = _stored()
    world["selection"] = dict(PROP)
    ps_entry = ps._load()
    ps_entry["thermal"][EMAIL]["motor"] = MOTOR_PROP        # A is the active one
    ps._save(ps_entry)

    real = ts.resolve_motor_settings
    calls = {"n": 0}

    def interleaved(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:                                # the other tab saves in between
            world["selection"] = dict(L155)
            _user_saves({"coolMode": "liquid", "airSpeedSource": "manual", "tIn": "50",
                         "flowLpm": "20"}, MOTOR_L155)
        return real(*a, **kw)

    monkeypatch.setattr(ts, "resolve_motor_settings", interleaved)
    world["selection"] = dict(L155)
    out = ts.follow_loaded_motor(EMAIL, L155, PROP)
    assert calls["n"] == 2                                  # resolved again from the fresh entry
    assert out["fields"]["flowLpm"] == "20"
    assert _stored()["flowLpm"] == "20"
    entry = ps._load()["thermal"][EMAIL]
    assert entry["by_motor"][MOTOR_L155]["settings"]["flowLpm"] == "20"


def test_r3_a_superseded_follow_writes_nothing(world):
    """...and a follow whose selection is no longer the loaded one does not write."""
    _seed(FORTY_MM, motor=MOTOR_PROP)
    world["selection"] = dict(ROBOT)                         # a newer selection won
    out = ts.follow_loaded_motor(EMAIL, L155, PROP)
    assert out["fields"] is None
    assert _stored()["frame"] == "open"                      # untouched


def test_r4_an_unchanged_sparse_legacy_entry_keeps_its_effective_cooling(world):
    """R4: no airSpeed in an old entry meant 0 m/s and no tIn the ambient; neither
    becomes a panel default for a motor nobody touched."""
    legacy_air = {"coolMode": "air", "airSpeedSource": "manual", "ambientT": "30"}
    _seed(legacy_air)                                        # no marker, no by_motor
    world["selection"] = dict(PROP)
    s, info = ts.motor_thermal_settings(None)
    assert s == legacy_air and info["source"] == "last_used"
    assert ts.cooling_fields(s) == ts.cooling_fields(legacy_air)
    assert ts.cooling_fields(s)["air_speed_mps"] == 0.0
    # the same machine reloaded (the legacy claim) does not materialise defaults
    out = _load(world, PROP, PROP)
    assert out["motor_changed"] is False
    # the entry is now DESCRIBED completely (with the solver's own meanings for
    # what it lacked) and the physics is bit-identical
    assert set(ts.COOLING_KEYS) <= set(_stored())
    assert {k: _stored()[k] for k in legacy_air} == legacy_air
    assert ts.cooling_fields(_stored()) == ts.cooling_fields(legacy_air)
    assert ts.cooling_fields(ts.motor_thermal_settings(None)[0]) == ts.cooling_fields(legacy_air)
    assert out["fields"] == ts._fields_of(_stored()) and set(out["fields"]) == set(ts.COOLING_KEYS)
    legacy_liquid = {"coolMode": "liquid", "ambientT": "30", "flowLpm": "5"}
    _seed(legacy_liquid)
    s, _info = ts.motor_thermal_settings(None)
    assert ts.cooling_fields(s)["fluid_temp_in_c"] == 30.0   # the ambient, not 40
    assert ts.cooling_fields(s) == ts.cooling_fields(legacy_liquid)


def test_r5_reloading_the_motor_after_a_lost_answer_returns_its_fields(world):
    """R5 (server half): the follow for B was written but its answer was lost; the
    reload finds nothing to change and must still return B's complete cooling."""
    _seed(FORTY_MM, motor=MOTOR_PROP)
    world["selection"] = dict(PROP)
    _load(world, PROP, L155)                                 # answer discarded by the test
    again = _load(world, L155, L155)
    assert again["motor_changed"] is False and again["changed"] is False
    assert again["fields"] == ts._fields_of(_stored())
    assert (again["fields"]["coolMode"], again["fields"]["frame"]) == ("liquid", "housed")
    assert again["source"] == "application"


def test_r6_controller_preview_keeps_the_requested_motor_without_a_duty(world):
    """R6: A active (liquid 60 degC, 2 L/min); the preview for B with no duty
    returns B's cooling - and never A's saved duty record."""
    from motor_ai_sim.routes import controller as rc
    _seed({"coolMode": "liquid", "airSpeedSource": "manual", "fluid": "water",
           "tIn": "60", "flowLpm": "2"}, motor=MOTOR_L155)
    world["selection"] = dict(L155)
    _user_saves({"coolMode": "liquid", "airSpeedSource": "manual", "fluid": "water",
                 "tIn": "25", "flowLpm": "20"}, MOTOR_ROBOT)
    out = rc.get_thermal_cooling(die="CIANO28 85", config="L13", duty=None,
                                 authorization=None)
    assert (out["die"], out["config"]) == ("CIANO28 85", "L13")
    assert out["duty"] is None                               # the active duty is A's
    assert out["modes"]["liquid"]["fields"] == {
        "coolant": "water", "flow_lpm": 20.0, "t_in_c": 25.0}
    # a mode B is not set to falls back to B's saved duty record only; with no
    # duty named it has none, and A's must not appear
    air = out["modes"]["air_forced"]
    assert air["fields"] == {} and air["source"] is None
    # no motor named: the active one, as before
    own = rc.get_thermal_cooling(die=None, config=None, duty=None, authorization=None)
    assert (own["die"], own["config"], own["duty"]) == ("CIANO10 200 opt", "L155 motor", "rated")
    assert own["modes"]["liquid"]["fields"]["t_in_c"] == 60.0


# ═══ third review of PR #218 (de6561d9) ═══════════════════════════════════════════

def test_t1_a_sparse_legacy_entry_is_answered_complete_with_the_solvers_meanings(world):
    """T1 (server half): the follow answer for a sparse legacy motor carries EVERY
    cooling key, with the solver's old meaning for each absent one - the browser
    adopts these, never its own localStorage values."""
    legacy = {"coolMode": "air", "airSpeedSource": "manual", "ambientT": "30"}
    _seed(legacy)
    world["selection"] = dict(L155)
    out = _load(world, L155, L155)
    f = out["fields"]
    assert set(f) == set(ts.COOLING_KEYS)
    assert (f["airSpeed"], f["frame"], f["boreMode"], f["boreAirSpeed"]) == ("0", "housed", "none", "0")
    assert f["tIn"] == "30" and f["boreTIn"] == "30"           # the ambient, as the mapper did
    assert ts.cooling_fields(f) == ts.cooling_fields(legacy)
    # a sparse GET response is exactly what the browser must NOT trust
    from motor_ai_sim.routes import panel_settings as ps
    sparse = {"coolMode": "air", "airSpeedSource": "manual", "ambientT": "30"}
    _seed(sparse)
    assert set(ps.get_panel_settings(panel="thermal", authorization=None)["settings"])         < set(ts.COOLING_KEYS)


def test_t2_an_older_reference_load_cannot_revive_its_propeller_binding(world, monkeypatch):
    """T2: the follow for reference R1 pauses; R2 of the SAME die/config is loaded and
    its follow finishes; R1 resumes - it must write nothing."""
    from motor_ai_sim.routes import panel_settings as ps
    r1, r2 = {**PROP, "ref_id": "R1"}, {**PROP, "ref_id": "R2"}
    key1 = "%s|CIANO14 40 new|L12|R1" % EMAIL
    key2 = "%s|CIANO14 40 new|L12|R2" % EMAIL
    world["selection"] = dict(r1)
    _user_saves({**FORTY_MM, "propellerId": "fpv_10x5", "propellerContextKey": key1,
                 "boreAirSpeed": "5"}, MOTOR_PROP)
    _load(world, r1, ROBOT)                                  # ROBOT active; the PROP record exists

    real = ts.resolve_motor_settings
    calls = {"n": 0}

    def interleaved(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:                                  # the newer load runs to completion
            world["selection"] = dict(r2)
            ts.follow_loaded_motor(EMAIL, r2, r1)
        return real(*a, **kw)

    monkeypatch.setattr(ts, "resolve_motor_settings", interleaved)
    world["selection"] = dict(r1)
    old = ts.follow_loaded_motor(EMAIL, r1, ROBOT)
    assert old["fields"] is None                             # superseded: no answer, no write
    assert _stored()["propellerContextKey"] == key2
    entry = ps._load()["thermal"][EMAIL]
    assert entry["by_motor"][MOTOR_PROP]["settings"]["propellerContextKey"] == key2
