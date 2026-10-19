"""The assistant's "Published motors" knowledge block (owner 2026-10-19).

Pinned here (no provider is called, no FEM runs):

* the RENDERER, from a fixture catalog tree: a continuous rated point with its
  limiting part and cooling, the peak / short-time duty with its time, KV and Kt,
  mass, battery, application and allowed propellers, the 3-D model flags, the
  "in production" mark for the 40 mm motor, an honest line for a rated point that
  is NOT continuous, and nothing from a configuration without a catalogue card;
* NO LEAK: a cost, a supplier, stock, a design note planted on the fixture never
  reaches the text;
* ACCESS: a visitor and a plain account read only what a brand-new account may
  open, a granted account also reads its die, a private configuration appears for
  an admin only - on a throwaway catalog through the real ``motor_access``;
* the PROMPT: the block rides in the system prompt of the right caller, after the
  visitor note, and the visitor text says free / instant sign-up (no invitation,
  no pricing).
"""
from __future__ import annotations

import copy
import json

import pytest
import yaml

from motor_ai_sim import support_catalog as SC

PLANTED = ("SECRET_NOTE", "ACME_SUPPLIER", "STOCK_42KG", "PRICE_EUR_99", "unpublished patent")


# ── a fixture tree (the shape routes.family.tree returns) ────────────────────

def _duty(name, *, rpm, amps, nm, kw, eff, **extra):
    d = {"name": name, "mode": "motor", "rpm": rpm, "current_arms": amps, "torque_nm": nm, "power_kw": kw,
         "note": "SECRET_NOTE supplier ACME_SUPPLIER stock STOCK_42KG",
         "result": {"efficiency_pct": eff, "kv_rpm_per_v": 821.04, "mass_kg": 0.123, "cost_eur": "PRICE_EUR_99"}}
    d.update(extra)
    return d


def _cfg(name="L12", **over):
    c = {"name": name, "stack_mm": 12, "magnet": "F52SH_150C", "steel": "20SW1200", "role": "motor",
         "applications": ["propeller"], "default_duty": "rated", "card_date": "2026-10-14",
         "battery": {"cells": 6, "chemistry": "NMC", "v_nom": 22.2, "v_min": 18.0, "v_max": 25.2},
         "mass": {"full_kg": 0.123, "partial": False},
         "supplier": "ACME_SUPPLIER", "cost_eur": "PRICE_EUR_99", "stock": "STOCK_42KG",
         "duties": [
             _duty("rated", rpm=13000, amps=41.0, nm=0.55, kw=0.74, eff=91.1,
                   continuous_rating={"feasible": True, "i_cont_A": 41.0, "part": "winding", "limit_c": 200.0,
                                      "torque_Nm": 0.547, "cooling_label": "propeller tmotor_p13x4_4 slipstream, 40 °C"}),
             _duty("peak", rpm=14400, amps=48.79, nm=0.66, kw=1.0, eff=90.8, duty_class="short_time",
                   short_time={"words": "short-time: 1 min 31 s from cold (winding 200 °C)"})]}
    c.update(over)
    return c


def _die(name="CIANO14 40 new", dia=40, layer="shared", cfgs=None):
    return {"name": name, "stator_diameter": dia, "slots": 12, "poles": 14, "layer": layer,
            "configs": cfgs if cfgs is not None else [_cfg()]}


COOL = {"propellers": [f"prop_{i}" for i in range(11)]}
MODEL = {"available": True, "simplified_available": True, "step_available": True}


def _render(dies, **kw):
    return SC.render_block(dies, cooling_of=lambda d, c: COOL, model_of=lambda d, c: MODEL, **kw)


# ── 1. the renderer ──────────────────────────────────────────────────────────

def test_a_continuous_rated_point_and_the_peak_are_quoted():
    t = _render([_die()])
    assert "CIANO14 40 new / L12" in t and "stator Ø40 mm" in t and "12 slots / 14 poles" in t
    assert "stack 12 mm" in t and "F52SH_150C" in t and "20SW1200" in t
    assert "Rated (continuous): 13000 rpm, 41 A rms, 0.55 N·m, 0.74 kW, efficiency 91.1 %" in t
    assert "limited by the winding (200 °C)" in t and "cooling: propeller tmotor_p13x4_4 slipstream, 40 °C" in t
    assert "Peak: 14400 rpm, 48.8 A rms, 0.66 N·m, 1 kW" in t
    assert "short-time: 1 min 31 s from cold (winding 200 °C)" in t
    assert "KV 821 rpm/V" in t and "Kt 0.013 N·m/A rms" in t
    assert "mass 0.123 kg" in t and "battery 6S NMC 22.2 V nominal (18–25.2 V)" in t
    assert "propeller (drone) motor" in t
    assert "prop_0" in t and "prop_7" in t and "prop_8" not in t and "(+3 more)" in t
    assert "viewable after sign-up" in t and "simplified STEP downloadable without an NDA" in t
    assert "full STEP after the online NDA" in t
    assert "IN PRODUCTION" in t                                  # the 40 mm motor
    assert "datasheet / Configure is the source" not in t        # that sentence lives in the prompt ...
    assert "For exact live numbers the datasheet" in t           # ... and in the block header


def test_only_the_40_mm_motor_is_marked_in_production():
    t = _render([_die("CIANO14 50 edited", 50, cfgs=[_cfg("L15")])])
    assert "IN PRODUCTION" not in t


def test_a_short_time_rated_point_is_never_passed_off_as_the_rating():
    c = _cfg("L35")
    c["duties"] = [_duty("rated", rpm=4000, amps=82.3, nm=31.07, kw=13.01, eff=96.3, duty_class="short_time",
                         short_time={"words": "short-time: 6 min 11 s from cold (winding 200 °C)"})]
    t = _render([_die("CIANO28 150_35 new", 150, cfgs=[c])])
    assert "Rated point (short-time only, NOT a continuous rating)" in t
    assert "6 min 11 s from cold" in t and "Rated (continuous)" not in t


def test_an_unclassified_rated_point_claims_nothing_about_the_hours():
    c = _cfg("L13")
    c["duties"] = [_duty("rated", rpm=1000, amps=24.7, nm=4.43, kw=0.46, eff=73.8)]
    t = _render([_die("CIANO28 85 20SW1200", 85, cfgs=[c])])
    assert "a continuous rating is not established" in t
    assert "short-time only" not in t and "Rated (continuous)" not in t


def test_a_configuration_without_a_catalogue_card_and_other_layers_are_left_out():
    dies = [_die(cfgs=[_cfg("L12"), _cfg("L99", card_date=None)]),
            _die("WORKSPACE COPY", layer="workspace"), _die("COMMUNITY", layer="published")]
    t = _render(dies)
    assert "L12" in t and "L99" not in t and "WORKSPACE COPY" not in t and "COMMUNITY" not in t


def test_no_cost_supplier_stock_or_internal_note_leaks():
    t = _render([_die()])
    for secret in PLANTED:
        assert secret not in t, secret
    assert "€" not in t and "price" not in t.split("never quote a price")[0].lower()


def test_an_empty_catalog_says_so_and_tells_nothing_to_guess():
    assert "No motor is open to this account yet" in SC.render_block([])
    v = SC.render_block([], visitor=True)
    assert "No motor is open to a new account" in v and "aerostator.com" in v


def test_one_broken_card_does_not_blank_the_rest():
    bad = _cfg("L50")
    bad["duties"] = ["not a dict"]
    t = _render([_die(cfgs=[bad, _cfg("L12")])])
    assert "L12" in t


def test_the_block_stays_compact():
    # six configurations with two duties each: a few lines apiece, a few kilobytes in all
    t = _render([_die(f"CIANO14 {i} new", 40 + i, cfgs=[_cfg("L12"), _cfg("L20")]) for i in range(3)])
    assert len(t) < 9000


# ── 2. access, on a throwaway catalog through the real motor_access ──────────

DIE_PUB, DIE_PRIV = "PUB 40", "PRIV 85"
ADMIN, PLAIN, GRANTED = "admin@example.com", "plain@example.com", "granted@example.com"


def _cfg_yaml(die, name, vis):
    doc = {"name": name, "die": die, "role": "motor", "applications": ["propeller"],
           "geometry_overrides": {"motor_length": float(name[1:])},
           "winding": {"connection": "star"}, "materials": {"magnet": "N42SH", "stator_core": "20SW1200"},
           "cost_eur": "PRICE_EUR_99", "supplier": "ACME_SUPPLIER",
           "duties": [{"name": "rated", "mode": "motor", "saved_at": "2026-10-19T10:00:00", "current_arms": 20.0,
                       "rpm": 5000.0, "torque_nm": 1.5, "power_kw": 0.8, "note": "SECRET_NOTE ACME_SUPPLIER",
                       "result": {"efficiency_pct": 90.0, "mass_kg": 0.5}}]}
    text = yaml.safe_dump(doc, sort_keys=False)
    return (f"visibility: {vis}\n" + text) if vis else text


@pytest.fixture()
def cat(tmp_path, monkeypatch):
    from motor_ai_sim import auth, model3d, passport_store
    from motor_ai_sim import motor_access as MA
    from motor_ai_sim import users as U
    from motor_ai_sim.routes import family as fam

    dies = tmp_path / "dies"
    for die, dia, cfgs in ((DIE_PUB, 40.0, (("L12", None), ("L20", "private"))),
                           (DIE_PRIV, 85.0, (("L15", None), ("L13", "private")))):
        (dies / die).mkdir(parents=True)
        (dies / die / "die.yaml").write_text(yaml.safe_dump({
            "name": die, "locked": False, "created": "2026-10-19T10:00:00",
            "geometry": {"num_slots": 24, "num_poles": 28, "stator_diameter": dia, "magnet_height": 3.0,
                         "motor_length": 15.0}}, sort_keys=False), encoding="utf-8", newline="\n")
        for name, vis in cfgs:
            (dies / die / f"{name}.yaml").write_text(_cfg_yaml(die, name, vis), encoding="utf-8", newline="\n")

    monkeypatch.setattr(fam, "_DIES_DIR", dies)
    monkeypatch.setattr(U, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(MA, "_DIE_ACCESS_FILE", tmp_path / "die_access.json")
    (tmp_path / "die_access.json").write_text(
        json.dumps({DIE_PUB: {"visibility": "public", "clients": []}}), encoding="utf-8")
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.delenv("CATALOG_GRANT_ALL_REGISTERED", raising=False)
    monkeypatch.setenv("MOTRES_CAD_ROOT", str(tmp_path / "cad"))
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)
    monkeypatch.setattr(passport_store, "card_index",
                        lambda: {DIE_PUB: {"L12": "2026-10-19", "L20": "2026-10-19"},
                                 DIE_PRIV: {"L15": "2026-10-19", "L13": "2026-10-19"}})
    monkeypatch.setattr(model3d, "scan", lambda root=None: [])
    MA._CFG_VIS_CACHE.clear()
    fam._TREE_CACHE.clear()
    SC.clear_cache()
    U.create_user(ADMIN, "password-admin", role="admin", name="Admin")
    for e in (PLAIN, GRANTED):
        U.create_user(e, "password-" + e.split("@")[0], role="engineer", name=e.split("@")[0])
        U.set_motor_grants(e, all_motors=False, dies=[DIE_PRIV] if e == GRANTED else [])
    hdr = lambda e: f"Bearer {U.issue_token(e)}"          # noqa: E731
    yield {"admin": hdr(ADMIN), "plain": hdr(PLAIN), "granted": hdr(GRANTED)}
    fam._TREE_CACHE.clear()
    MA._CFG_VIS_CACHE.clear()
    SC.clear_cache()


def _names(text):
    import re
    return sorted(re.findall(r"^- \*\*(.+?)\*\* —", text, flags=re.M))


def test_a_visitor_reads_only_what_a_new_account_may_open(cat):
    t = SC.published_motors_block(None, visitor=True)
    assert _names(t) == [f"{DIE_PUB} / L12"]                      # the public die, its open configuration
    for hidden in (f"{DIE_PRIV}", "L13", "L20"):
        assert hidden not in t, hidden


def test_a_visitor_gets_the_shared_baseline_die_like_a_fresh_account(cat, monkeypatch):
    """Every registered account is given the baseline die at read time (users._effective_motor_grants);
    a visitor is told exactly that - the die's open configurations, never a private one."""
    from motor_ai_sim import users as U
    monkeypatch.setattr(U, "_BASELINE_DIE", DIE_PRIV)
    monkeypatch.setattr(U, "_shared_baseline_available", lambda: True)
    SC.clear_cache()
    assert _names(SC.published_motors_block(None, visitor=True)) == [f"{DIE_PRIV} / L15", f"{DIE_PUB} / L12"]
    # the same set a freshly registered, ungranted account really gets
    SC.clear_cache()
    assert _names(SC.published_motors_block(cat["plain"])) == [f"{DIE_PRIV} / L15", f"{DIE_PUB} / L12"]


def test_a_plain_account_sees_what_a_visitor_sees(cat):
    assert _names(SC.published_motors_block(cat["plain"])) == [f"{DIE_PUB} / L12"]


def test_a_granted_account_also_reads_its_die_but_not_a_private_configuration(cat):
    assert _names(SC.published_motors_block(cat["granted"])) == [f"{DIE_PRIV} / L15", f"{DIE_PUB} / L12"]


def test_an_admin_reads_everything(cat):
    assert _names(SC.published_motors_block(cat["admin"])) == sorted(
        [f"{DIE_PRIV} / L13", f"{DIE_PRIV} / L15", f"{DIE_PUB} / L12", f"{DIE_PUB} / L20"])


def test_the_live_block_leaks_no_cost_supplier_or_note(cat):
    for who, visitor in (("admin", False), ("granted", False), ("plain", False), (None, True)):
        t = SC.published_motors_block(cat.get(who) if who else None, visitor=visitor)
        assert t
        for secret in PLANTED:
            assert secret not in t, (who, secret)


def test_the_block_is_cached_per_access_for_a_few_minutes(cat):
    from motor_ai_sim.routes import family as fam
    a = SC.published_motors_block(cat["plain"], now=1000.0)
    # a changed catalog is not seen inside the TTL ...
    (fam._DIES_DIR / DIE_PUB / "L12.yaml").unlink()
    fam._TREE_CACHE.clear()
    assert SC.published_motors_block(cat["plain"], now=1000.0 + SC.CACHE_TTL_S - 1) == a
    # ... and is seen after it
    assert _names(SC.published_motors_block(cat["plain"], now=1000.0 + SC.CACHE_TTL_S + 1)) == []
    # another access shape is a different entry
    assert _names(SC.published_motors_block(cat["admin"], now=1000.0)) != []


def test_a_failure_in_the_block_never_costs_the_answer(monkeypatch):
    from motor_ai_sim.routes import family as fam

    def boom(*a, **k):
        raise RuntimeError("catalog on fire")

    monkeypatch.setattr(fam, "tree_for_access", boom)
    SC.clear_cache()
    assert SC.published_motors_block(None, visitor=True) == ""


# ── 3. the prompt that is actually sent ──────────────────────────────────────

def test_the_block_rides_in_the_prompt_of_the_right_caller(cat):
    from motor_ai_sim.routes import support
    visitor = support.build_system_prompt("anon", anon=True)
    assert "## Published motors" in visitor and f"{DIE_PUB} / L12" in visitor and DIE_PRIV not in visitor
    assert visitor.index(support.VISITOR_NOTE) < visitor.index("## Published motors")
    user = support.build_system_prompt("engineer", anon=False, context={}, authorization=cat["granted"])
    assert f"{DIE_PRIV} / L15" in user and "L13" not in user
    admin = support.build_system_prompt("admin", anon=False, context={}, authorization=cat["admin"])
    assert f"{DIE_PRIV} / L13" in admin
    # the ticket protocol is still there, and the block sits before the session context
    assert "TICKET_DRAFT" in user


def test_the_visitor_text_says_free_and_instant_not_invitation_or_pricing():
    from motor_ai_sim.routes import support
    note = support.VISITOR_NOTE
    low = note.lower()
    assert "free" in low and "sign-up is instant" in low and "create account" in low
    assert "invitation" not in low and "pricing" not in low
    from motor_ai_sim import support_prompt as P
    assert "invitation" not in P.USER_PROMPT.lower() and "pricing" not in P.USER_PROMPT.lower()
    for reply in support._LIMIT_REPLIES.values():
        assert "invitation" not in reply.lower()
