"""The bearings limit Configure's speed (owner 2026-10-28), and test duties stay out of Configure.

* ``configure_limits.bearing_speed_limit`` reads the configuration's own ``bearings:`` block with the friction
  model's reader and the library's limiting speed for the lubrication: the LOWEST over the two ends, the
  card(s) that set it named, an unknown lubrication = grease; whatever cannot be stated is ``set: False`` with
  a reason - never a default number;
* the answer rides on ``context()['speed']['bearing']`` and on every preset;
* a duty flagged ``kind: test`` (or an unflagged legacy ``noload_*``) is not a preset duty point and never the
  preset's default duty.

No solver, no FEM: the bearing library is a temp file.
"""
from __future__ import annotations

import pytest
import yaml

from motor_ai_sim import bearings as brg
from motor_ai_sim import configure_limits as cl

_LIB = {
    "lubricants": {"MT47": {"kind": "grease", "nu40": 70.0, "nu100": 9.0}},
    "bearings": {
        "AAA-1": {"type": "deep_groove", "d": 70, "D": 90, "B": 10, "default_lubricant": "MT47",
                  "n_limit_grease_rpm": 6300, "n_limit_oil_air_rpm": 9000},
        "BBB-2": {"type": "deep_groove", "d": 70, "D": 90, "B": 10, "default_lubricant": "MT47",
                  "n_limit_grease_rpm": 8000, "n_limit_oil_air_rpm": None},
        "NOLIM": {"type": "deep_groove", "d": 70, "D": 90, "B": 10, "default_lubricant": "MT47"},
    },
}


@pytest.fixture(autouse=True)
def library(tmp_path, monkeypatch):
    lib = tmp_path / "bearings_library.yaml"
    lib.write_text(yaml.safe_dump(_LIB), encoding="utf-8")
    monkeypatch.setattr(brg, "_LIB_PATH", lib, raising=True)
    monkeypatch.setattr(brg, "_library", None, raising=True)
    monkeypatch.setattr(brg, "_lib_mtime", 0.0, raising=True)
    monkeypatch.setattr(brg, "_lib_checked", 0.0, raising=True)
    brg._CARD_CACHE.clear()
    yield
    brg._CARD_CACHE.clear()


def _doc(**bearings):
    return {"name": "L50", "die": "D", "bearings": bearings}


def test_the_limit_is_the_cards_for_the_stated_lubrication():
    g = cl.bearing_speed_limit(_doc(A={"card": "AAA-1"}, B={"card": "AAA-1"}, lubrication="grease"))
    assert g["set"] is True and g["rpm"] == 6300.0 and g["cards"] == ["AAA-1"] and g["lubrication"] == "grease"
    o = cl.bearing_speed_limit(_doc(A={"card": "AAA-1"}, B={"card": "AAA-1"}, lubrication="oil_air"))
    assert o["set"] is True and o["rpm"] == 9000.0 and o["lubrication"] == "oil_air"


def test_the_lowest_end_wins_and_the_cards_that_set_it_are_named():
    r = cl.bearing_speed_limit(_doc(A={"card": "BBB-2"}, B={"card": "AAA-1"}, lubrication="grease"))
    assert r["set"] is True and r["rpm"] == 6300.0 and r["cards"] == ["AAA-1"]
    assert [(e["end"], e["card"], e["limit_rpm"]) for e in r["ends"]] == [("A", "BBB-2", 8000.0), ("B", "AAA-1", 6300.0)]
    # a tie names both cards, once each
    t = cl.bearing_speed_limit(_doc(A={"card": "AAA-1"}, B={"card": "AAA-1"}))
    assert t["cards"] == ["AAA-1"]


def test_one_named_end_stands_for_both_like_the_friction_model():
    r = cl.bearing_speed_limit(_doc(A={"card": "AAA-1"}))
    assert r["set"] is True and r["rpm"] == 6300.0 and [e["card"] for e in r["ends"]] == ["AAA-1", "AAA-1"]


def test_an_unknown_or_missing_lubrication_is_grease_the_conservative_one():
    for lub in ({"lubrication": "mist"}, {}):
        r = cl.bearing_speed_limit(_doc(A={"card": "AAA-1"}, B={"card": "AAA-1"}, **lub))
        assert r["set"] is True and r["lubrication"] == "grease" and r["rpm"] == 6300.0


def test_no_bearings_is_an_explicit_not_set_never_a_number():
    for doc in ({}, {"bearings": None}, {"bearings": {}}, {"bearings": {"lubrication": "grease"}}, None):
        r = cl.bearing_speed_limit(doc)
        assert r["set"] is False and "rpm" not in r and "no bearings" in r["reason"]


def test_a_card_missing_from_the_library_or_without_a_limit_is_not_set_with_a_reason():
    r = cl.bearing_speed_limit(_doc(A={"card": "AAA-1"}, B={"card": "GHOST"}))
    assert r["set"] is False and "rpm" not in r and "GHOST" in r["reason"]
    n = cl.bearing_speed_limit(_doc(A={"card": "NOLIM"}, B={"card": "NOLIM"}))
    assert n["set"] is False and "NOLIM" in n["reason"] and "grease" in n["reason"]
    # a sealed card has no oil-air figure: asking for oil-air is "not set", not the grease number
    s = cl.bearing_speed_limit(_doc(A={"card": "BBB-2"}, B={"card": "BBB-2"}, lubrication="oil_air"))
    assert s["set"] is False and "oil-air" in s["reason"] and "rpm" not in s
    # a malformed saved block does not raise
    assert cl.bearing_speed_limit({"bearings": {"A": {"card": "AAA-1"}, "preload_n": "heavy"}})["set"] is False


def test_the_context_and_every_preset_carry_the_speed_block():
    doc = {"name": "L50", "die": "D", "bearings": {"A": {"card": "AAA-1"}, "B": {"card": "AAA-1"}, "lubrication": "grease"},
           "duties": [{"name": "rated", "rpm": 3000, "current_arms": 40}]}
    ctx = cl.context({"id": "x"}, doc)
    assert ctx["speed"]["bearing"]["rpm"] == 6300.0
    assert cl.context({"id": "x"}, {})["speed"]["bearing"]["set"] is False
    pr = cl.preset_of(doc)
    assert pr["speed"]["bearing"]["rpm"] == 6300.0 and pr["speed"]["bearing"]["cards"] == ["AAA-1"]
    # each configuration of a die states its own bearings
    other = {**doc, "name": "L60", "bearings": {"A": {"card": "BBB-2"}, "lubrication": "grease"}}
    assert [p["speed"]["bearing"]["rpm"] for p in cl.presets_for_die([doc, other])] == [6300.0, 8000.0]


# ── test / validation duties are not presets ────────────────────────────────

def _cfg_with_tests():
    return {"name": "L50", "die": "D", "duties": [
        {"name": "noload_3021", "rpm": 3021, "current_arms": 0.1, "mode": "motor"},     # legacy, unflagged
        {"name": "bench 4000", "rpm": 4000, "current_arms": 0.1, "kind": "test"},        # flagged
        {"name": "continuous", "rpm": 3000, "current_arms": 40, "mode": "motor"},
        {"name": "peak", "rpm": 3500, "current_arms": 90, "mode": "motor"},
    ]}


def test_test_duties_are_not_duty_points_and_never_the_default_duty():
    p = cl.preset_of(_cfg_with_tests())
    assert [d["name"] for d in p["duty_points"]] == ["continuous", "peak"]
    assert p["duty"] == "continuous" and p["knobs"]["I_A"] == 40.0 and p["knobs"]["rpm"] == 3000.0
    # a configuration whose ONLY duties are tests offers no duty at all, not a bench point as the default
    only = cl.preset_of({"name": "T", "die": "D", "duties": [{"name": "noload_1", "rpm": 1000, "current_arms": 0.1}]})
    assert only["duty"] is None and only["duty_points"] == [] and only["knobs"]["I_A"] is None


def test_an_explicit_kind_outranks_the_legacy_name_rule():
    d = _cfg_with_tests()
    d["duties"][0]["kind"] = "operating"            # the owner says: this noload_ duty IS a product duty
    assert [x["name"] for x in cl.preset_of(d)["duty_points"]] == ["noload_3021", "continuous", "peak"]


# ── a frameless kit has no bearing limit: the bearings are the customer's ─────

def test_a_frameless_kit_states_no_bearing_limit_and_no_missing_bearings():
    kit = {**_doc(A={"card": "AAA-1"}, lubrication="grease"), "cad_variant": "frameless"}
    r = cl.bearing_speed_limit(kit)
    assert r["set"] is False and r["customer_owned"] is True and "customer" in r["reason"] and "rpm" not in r
    assert cl.bearing_speed_limit({"name": "L13", "cad_variant": " Frameless "})["customer_owned"] is True
    # a full build (or an unknown value, like motor_mass.config_cad_variant) is judged by its bearings
    for v in ("full", "weird", None):
        assert cl.bearing_speed_limit({**kit, "cad_variant": v})["rpm"] == 6300.0
    # a configuration with no bearings is "not set" (customer_owned absent), not customer-owned
    assert "customer_owned" not in cl.bearing_speed_limit({"name": "L12"})


# ── the hand-set speed maximum ───────────────────────────────────────────────

def test_hand_limits_and_validation_of_the_speed_maximum():
    card = {"configure_limits": {"L_max_mm": 30, "rpm_max": 5000, "set_by": "boss", "set_at": "t"}}
    assert cl.hand_limits(card) == {"L_max_mm": 30.0, "rpm_max": 5000.0, "set_by": "boss", "set_at": "t"}
    assert cl.hand_limits({})["rpm_max"] is None and cl.hand_limits({"configure_limits": {"L_max_mm": 5}})["rpm_max"] is None
    assert cl.validate_rpm_max(None) is None and cl.validate_rpm_max("5000") == 5000.0
    for bad in (0, -1, "abc", True, 1e9, float("nan")):
        with pytest.raises(ValueError):
            cl.validate_rpm_max(bad)


@pytest.fixture()
def route(tmp_path, monkeypatch):
    import json

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from motor_ai_sim.routes import catalog as cat_mod
    p = tmp_path / "motor_catalog.json"
    p.write_text(json.dumps({"motors": [{"id": "cat_x", "name": "X die L12", "passport": {}}]}), encoding="utf-8")
    monkeypatch.setattr(cat_mod, "_CATALOG_PATH", p)
    monkeypatch.setattr(cat_mod, "_family_doc_of_motor", lambda m, g=None: {})
    monkeypatch.setattr(cat_mod, "_caller_identity", lambda a=None: {"id": "boss", "is_admin": True})
    app = FastAPI()
    app.include_router(cat_mod.router)
    return TestClient(app), p, cat_mod, monkeypatch


def test_the_speed_maximum_is_admin_only_stored_beside_the_stack_maximum_and_clearable(route):
    import json
    c, path, cat_mod, mp = route
    url = "/api/catalog/cat_x/configure_limits"
    stored = lambda: json.loads(path.read_text(encoding="utf-8"))["motors"][0].get("configure_limits")   # noqa: E731
    mp.setattr(cat_mod, "_caller_identity", lambda a=None: {"id": "u", "is_admin": False})
    assert c.patch(url, json={"rpm_max": 5000}).status_code == 403
    mp.setattr(cat_mod, "_caller_identity", lambda a=None: {"id": "boss", "is_admin": True})
    assert c.patch(url, json={}).status_code == 422
    for bad in (0, -5, "abc", 1e9, True):
        assert c.patch(url, json={"rpm_max": bad}).status_code == 422
    r = c.patch(url, json={"rpm_max": 5000})
    assert r.status_code == 200 and r.json()["rpm_max"] == 5000.0 and r.json()["L_max_mm"] is None
    assert stored()["rpm_max"] == 5000.0 and stored()["set_by"] == "boss"
    # the two limits are independent: setting one keeps the other, clearing one keeps the other
    assert c.patch(url, json={"L_max_mm": 30}).json() == {"ok": True, "motor_id": "cat_x", "L_max_mm": 30.0, "rpm_max": 5000.0}
    assert c.patch(url, json={"rpm_max": None}).json()["L_max_mm"] == 30.0
    assert "rpm_max" not in stored() and stored()["L_max_mm"] == 30.0
    assert c.patch(url, json={"L_max_mm": None}).status_code == 200
    assert stored() is None                                          # nothing left: the block goes
    assert c.patch("/api/catalog/nope/configure_limits", json={"rpm_max": 5000}).status_code == 404


def test_the_context_carries_the_speed_maximum_in_limits(route):
    c, path, cat_mod, mp = route
    c.patch("/api/catalog/cat_x/configure_limits", json={"rpm_max": 5000})
    lim = c.get("/api/catalog/cat_x/configure_context").json()["limits"]
    assert lim["rpm_max"] == 5000.0 and lim["L_max_mm"] is None


# ── a card with no configuration behind it carries its bearings itself ───────

def test_a_card_without_a_configuration_reads_its_own_bearings_block():
    card = {"id": "c", "configure_limits": {"bearings": {"A": {"card": "AAA-1"}, "B": {"card": "AAA-1"}, "lubrication": "grease"}}}
    b = cl.context(card, None)["speed"]["bearing"]
    assert b["set"] is True and b["rpm"] == 6300.0 and b["source"] == "catalogue card"
    assert cl.context(card, {})["speed"]["bearing"]["rpm"] == 6300.0
    # the configuration's own bearings always win (even a different card), and its absence is not papered over
    own = {"name": "L12", "bearings": {"A": {"card": "BBB-2"}, "lubrication": "grease"}}
    r = cl.context(card, own)["speed"]["bearing"]
    assert r["rpm"] == 8000.0 and "source" not in r
    assert cl.context(card, {"name": "L12"})["speed"]["bearing"]["set"] is False
    # nothing on the card either: not set
    assert cl.context({"id": "c"}, None)["speed"]["bearing"]["set"] is False


def test_clearing_the_hand_limits_keeps_the_cards_bearings_block(route):
    import json
    c, path, cat_mod, mp = route
    d = json.loads(path.read_text(encoding="utf-8"))
    d["motors"][0]["configure_limits"] = {"bearings": {"A": {"card": "AAA-1"}}}
    path.write_text(json.dumps(d), encoding="utf-8")
    url = "/api/catalog/cat_x/configure_limits"
    assert c.patch(url, json={"rpm_max": 5000}).status_code == 200
    assert c.patch(url, json={"rpm_max": None}).status_code == 200
    stored = json.loads(path.read_text(encoding="utf-8"))["motors"][0]["configure_limits"]
    assert stored == {"bearings": {"A": {"card": "AAA-1"}}}
    assert cl.hand_limits({"configure_limits": stored})["rpm_max"] is None
