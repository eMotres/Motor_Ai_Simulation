"""``GET /api/public/motors`` - the landing page's public motor preview.

Pinned here (no FEM, no provider):

* the route answers an ANONYMOUS caller 200 - also with the exhibit door closed
  (``PUBLIC_EXHIBIT=0``, the way the server runs), where every other catalogue route is 401;
* ONLY public machines: a private die, a private configuration and a configuration without a
  catalogue card never appear (a throwaway catalogue through the real ``motor_access``);
* ONLY allow-listed keys, at the row and the ``rated`` level, with a planted cost / supplier /
  note / geometry / CAD key that must not come through;
* no construction key anywhere, cross-checked against ``construction_privacy`` (the protected
  schema) and the MCP geometry denylist;
* the answer is cached for 5 minutes, carries ``Cache-Control``, and the route is rate-limited
  per IP like the other open route.
"""
from __future__ import annotations

import json

import pytest
import yaml
from fastapi.testclient import TestClient

from motor_ai_sim import construction_privacy as CP
from motor_ai_sim import public_motors as PM
from motor_ai_sim.api import app
from motor_ai_sim.auth import anonymous_allowed

PLANTED = ("SECRET_NOTE", "ACME_SUPPLIER", "STOCK_42KG", "PRICE_EUR_99", "PLANTED_CAD", "thumb")
DIE_PUB, DIE_PRIV = "PUB 40", "PRIV 85"
client = TestClient(app)


def _cfg_yaml(die, name, vis):
    doc = {"name": name, "die": die, "role": "motor", "applications": ["propeller"],
           "geometry_overrides": {"motor_length": float(name[1:]), "wire_width": 1.7},
           "winding": {"connection": "star"}, "materials": {"magnet": "N42SH", "stator_core": "20SW1200"},
           "cost_eur": "PRICE_EUR_99", "supplier": "ACME_SUPPLIER", "stock": "STOCK_42KG",
           "cad_overrides": {"PLANTED_CAD": 1.0}, "cad_secret_dim": 3.3,
           "bearings": {"thermal_path": {"PLANTED_CAD": 1}},
           "duties": [{"name": "rated", "mode": "motor", "saved_at": "2026-10-19T10:00:00", "current_arms": 20.0,
                       "rpm": 5000.0, "torque_nm": -1.5, "power_kw": 0.8, "note": "SECRET_NOTE ACME_SUPPLIER",
                       "result": {"efficiency_pct": 90.04, "mass_kg": 0.5, "cost_eur": "PRICE_EUR_99"}}]}
    text = yaml.safe_dump(doc, sort_keys=False)
    return (f"visibility: {vis}\n" + text) if vis else text


@pytest.fixture()
def cat(tmp_path, monkeypatch):
    from motor_ai_sim import auth, model3d, passport_store, support_catalog as SC
    from motor_ai_sim import motor_access as MA
    from motor_ai_sim import users as U
    from motor_ai_sim.routes import family as fam
    from motor_ai_sim.routes import public_motors as route

    dies = tmp_path / "dies"
    # PUB 40: L12 open with the die, L20 private.  PRIV 85: private die, L15 inherits (closed), L13 PUBLIC cfg
    # is NOT planted - a private die with nothing public stays invisible.
    for die, dia, cfgs in ((DIE_PUB, 40.0, (("L12", None), ("L20", "private"), ("L25", None))),
                           (DIE_PRIV, 85.0, (("L15", None), ("L13", "private")))):
        (dies / die).mkdir(parents=True)
        (dies / die / "die.yaml").write_text(yaml.safe_dump({
            "name": die, "locked": False, "created": "2026-10-19T10:00:00",
            "thumb_svg": "<svg>PLANTED thumb</svg>",
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
    monkeypatch.delenv("PUBLIC_EXHIBIT", raising=False)
    monkeypatch.delenv("PUBLIC_MOTORS_BURST_MAX", raising=False)
    monkeypatch.setenv("MOTRES_CAD_ROOT", str(tmp_path / "cad"))
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {"admin@example.com"})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", True)
    # L25 has no catalogue card: it must not be listed even though its die is public
    monkeypatch.setattr(passport_store, "card_index",
                        lambda: {DIE_PUB: {"L12": "2026-10-19", "L20": "2026-10-19"},
                                 DIE_PRIV: {"L15": "2026-10-19", "L13": "2026-10-19"}})
    monkeypatch.setattr(model3d, "scan", lambda root=None: [])
    MA._CFG_VIS_CACHE.clear()
    fam._TREE_CACHE.clear()
    SC.clear_cache()
    PM.clear_cache()
    route.reset_limits()
    yield
    fam._TREE_CACHE.clear()
    MA._CFG_VIS_CACHE.clear()
    SC.clear_cache()
    PM.clear_cache()
    route.reset_limits()


def _get(headers=None):
    return client.get("/api/public/motors", headers=headers or {})


def _walk_keys(obj, out=None):
    out = set() if out is None else out
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(str(k))
            _walk_keys(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _walk_keys(v, out)
    return out


# -- 1. the door ------------------------------------------------------------------------------

def test_anonymous_gets_200(cat):
    r = _get()
    assert r.status_code == 200
    assert r.headers["cache-control"] == "public, max-age=300"
    assert isinstance(r.json()["motors"], list)


def test_open_with_the_exhibit_door_closed_while_the_catalogue_stays_401(cat, monkeypatch):
    monkeypatch.setenv("PUBLIC_EXHIBIT", "0")
    assert _get().status_code == 200
    assert client.get("/api/family/tree").status_code == 401
    assert client.get("/api/catalog").status_code == 401
    assert client.get("/api/catalog/references").status_code == 401


def test_the_allowlist_is_exactly_this_one_path():
    assert anonymous_allowed("/api/public/motors") is True
    for no in ("/api/public", "/api/public/", "/api/public/motors/x", "/api/public/other", "/api/publicmotors"):
        assert anonymous_allowed(no) is False, no


# -- 2. only public machines -----------------------------------------------------------------

def test_only_public_machines_are_listed(cat):
    rows = _get().json()["motors"]
    assert [(m["die"], m["configuration"]) for m in rows] == [(DIE_PUB, "L12")]


def test_a_private_machine_never_appears(cat):
    text = _get().text
    for hidden in (DIE_PRIV, "L13", "L15", "L20", "L25"):
        assert hidden not in text, hidden


def test_a_private_machine_never_appears_for_a_caller_holding_credentials_either(cat):
    # the route ignores the Authorization header altogether: a signed-in account sees the same preview
    from motor_ai_sim import users as U
    U.create_user("admin@example.com", "password-admin", role="admin", name="Admin")
    hdr = {"Authorization": f"Bearer {U.issue_token('admin@example.com')}"}
    assert _get(hdr).json() == _get().json()


def test_the_shared_baseline_die_is_listed_like_it_is_for_a_fresh_account(cat, monkeypatch):
    from motor_ai_sim import users as U
    monkeypatch.setattr(U, "_BASELINE_DIE", DIE_PRIV)
    monkeypatch.setattr(U, "_shared_baseline_available", lambda: True)
    PM.clear_cache()
    rows = _get().json()["motors"]
    assert sorted((m["die"], m["configuration"]) for m in rows) == [(DIE_PRIV, "L15"), (DIE_PUB, "L12")]


# -- 3. only allow-listed keys ------------------------------------------------------------------

def test_only_allow_listed_keys(cat):
    rows = _get().json()["motors"]
    assert rows
    for m in rows:
        assert set(m) == set(PM.MOTOR_KEYS)
        assert set(m["rated"]) == set(PM.RATED_KEYS)


def test_the_headline_numbers_are_the_saved_rated_point(cat):
    m = _get().json()["motors"][0]
    assert m["application"] == "propeller"
    assert m["rated"]["power_kw"] == 0.8
    assert m["rated"]["torque_nm"] == 1.5            # the sign of the stored torque is dropped
    assert m["rated"]["speed_rpm"] == 5000
    assert m["rated"]["efficiency_pct"] == 90.0
    assert m["rated"]["mass_kg"] is not None and m["rated"]["mass_is_partial"] is True   # no CAD parts list here
    assert m["rated"]["continuous"] is False         # no S1 evidence in the fixture: nothing claimed


def test_planted_internal_fields_do_not_come_through(cat):
    text = _get().text
    for p in PLANTED:
        assert p not in text, p


def test_a_row_is_built_key_by_key_so_a_new_tree_field_stays_out():
    die = {"name": "D", "stator_diameter": 40, "slots": 12, "poles": 14, "thumb_svg": "<svg/>"}
    cfg = {"name": "L1", "application": "robot", "default_duty": "rated", "card_date": "2026-10-19",
           "newly_added_tree_field": "X", "cost_eur": 1, "build_sig": "abc", "bearings": {"thermal_path": {}},
           "duties": [{"name": "rated", "rpm": 100, "torque_nm": 2, "power_kw": 0.2,
                       "result": {"efficiency_pct": 80, "mass_kg": 0.3, "p_core_w": 5}}]}
    row = PM.build_row(die, cfg, {"full_kg": 0.4, "active_kg": 0.3, "partial": False})
    assert set(row) == set(PM.MOTOR_KEYS) and set(row["rated"]) == set(PM.RATED_KEYS)
    assert row["rated"]["mass_kg"] == 0.4 and row["rated"]["mass_is_partial"] is False
    assert row["application"] == "robot"


def test_a_name_outside_the_catalogue_name_rule_is_left_out_not_altered():
    cfg = {"name": "L1", "default_duty": "r", "card_date": "x",
           "duties": [{"name": "r", "rpm": 1, "torque_nm": 1, "power_kw": 1, "result": {}}]}
    for bad in ("<b>X</b>", "A/B", "../x", "", " lead", "x" * 70):
        assert PM.build_row({"name": bad}, cfg, None) is None, bad
    assert PM.build_row({"name": "CIANO20 150_35 new"}, cfg, None)["die"] == "CIANO20 150_35 new"
    assert PM.build_row({"name": "D"}, {**cfg, "name": "L1 (a)#2+"}, None)["configuration"] == "L1 (a)#2+"


def test_a_non_finite_or_textual_number_becomes_null():
    die = {"name": "D"}
    cfg = {"name": "L1", "default_duty": "r", "card_date": "x",
           "duties": [{"name": "r", "rpm": "fast", "torque_nm": float("nan"), "power_kw": True,
                       "result": {"efficiency_pct": float("inf")}}]}
    r = PM.build_row(die, cfg, None)["rated"]
    assert r["speed_rpm"] is None and r["torque_nm"] is None and r["power_kw"] is None
    assert r["efficiency_pct"] is None and r["mass_kg"] is None


# -- 4. no construction keys, against the protected schema ------------------------------------------

def test_no_construction_key_anywhere(cat):
    keys = _walk_keys(_get().json())
    protected = set(CP.PROTECTED_KEYS) | set(CP.CAD_KEYS) | set(CP.PUBLIC_BEARING_KEYS) | {"bearings"}
    from motor_ai_sim.mcp_tools import GEOMETRY_DENYLIST
    assert keys.isdisjoint(protected), sorted(keys & protected)
    assert keys.isdisjoint(GEOMETRY_DENYLIST), sorted(keys & GEOMETRY_DENYLIST)
    assert not [k for k in keys if k.startswith("cad_")]
    # the protected document-level check the catalogue itself uses: the answer is not a catalogue document
    for row in _get().json()["motors"]:
        assert CP.protected_doc_keys(row) == []
        assert CP.protected_doc_keys(row["rated"]) == []


def test_the_guard_raises_rather_than_serve_a_leak():
    with pytest.raises(AssertionError):
        PM.assert_clean([{"die": "x", "rated": {"thermal_path": 1}}])
    with pytest.raises(AssertionError):
        PM.assert_clean([{"cad_anything": 1}])
    with pytest.raises(AssertionError):
        PM.assert_clean([{"geometry": {}}])
    with pytest.raises(AssertionError):
        PM.assert_allowlisted([{"die": "x", "configuration": "y", "application": None,
                                "rated": {k: None for k in PM.RATED_KEYS}, "extra": 1}])


# -- 5. cache and rate limit ------------------------------------------------------------------------

def test_the_list_is_cached_for_five_minutes(cat, monkeypatch):
    calls = []
    real = PM.build_list

    def counting(*a, **kw):
        calls.append(1)
        return real(*a, **kw)

    monkeypatch.setattr(PM, "build_list", counting)
    PM.clear_cache()
    first = PM.public_motors(now=1000.0)
    PM.public_motors(now=1000.0 + PM.CACHE_TTL_S - 1)
    assert len(calls) == 1
    PM.public_motors(now=1000.0 + PM.CACHE_TTL_S + 1)
    assert len(calls) == 2
    # a caller mutating its copy never changes the cache
    first[0]["rated"]["power_kw"] = -1
    assert PM.public_motors(now=1000.0 + PM.CACHE_TTL_S + 2)[0]["rated"]["power_kw"] != -1


def test_the_route_is_rate_limited_per_ip(cat, monkeypatch):
    monkeypatch.setenv("PUBLIC_MOTORS_BURST_MAX", "3")
    a = {"x-forwarded-for": "203.0.113.7, 172.18.0.1"}
    b = {"x-forwarded-for": "203.0.113.8, 172.18.0.1"}
    for _ in range(3):
        assert _get(a).status_code == 200
    r = _get(a)
    assert r.status_code == 429 and r.headers.get("retry-after")
    assert _get(b).status_code == 200            # another visitor has a window of its own


def test_a_build_failure_is_a_plain_503_not_a_trace(cat, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("catalogue on fire /secret/path")

    monkeypatch.setattr(PM, "build_list", boom)
    PM.clear_cache()
    r = _get()
    assert r.status_code == 503
    assert "secret" not in r.text and "RuntimeError" not in r.text
