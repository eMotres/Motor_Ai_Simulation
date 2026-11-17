"""The motors emotres.com shows (owner 2026-11-17): an unflagged motor and a private field cannot leave through the public
endpoint; the answer matches the versioned schema; the admin side is admin only.  Synthetic motors in temporary layers."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from motor_ai_sim import site_publication as SP
from motor_ai_sim.api import app
from motor_ai_sim.mcp_tools import GEOMETRY_DENYLIST

_ROOT = Path(__file__).resolve().parents[1]
ADMIN, A = "admin@example.com", "alice@example.com"
DIE, CFG, BARE = "CIAN28 150", "L30", "L99"
client = TestClient(app)
SCHEMA = json.loads((_ROOT / "docs" / "schemas" / "site_motors.v1.json").read_text(encoding="utf-8"))


def _duty(name, kind=None):
    d = {"name": name, "mode": "motor", "rpm": 3000, "torque_nm": 10.0, "power_kw": 3.1, "current_arms": 40.0,
         "saved_at": "2026-11-01T00:00:00Z", "result": {"efficiency_pct": 95.1, "v_ll_peak_v": 300.0, "mass_kg": 2.5}}
    if kind:
        d["kind"] = kind
    return d


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from motor_ai_sim import auth
    from motor_ai_sim import users as U
    from motor_ai_sim import duty_results as dr
    from motor_ai_sim.routes import family as fam
    shared, published, works = tmp_path / "shared", tmp_path / "published", tmp_path / "workspaces"
    for d in (shared / "dies" / DIE, published, works):
        d.mkdir(parents=True)
    (shared / "dies" / DIE / "die.yaml").write_text(yaml.safe_dump({"name": DIE, "geometry": {
        "num_slots": 24, "num_poles": 28, "stator_diameter": 150.0, "rotor_outer_radius": 73.0, "stator_inner_radius": 74.0,
        "motor_length": 10}}), encoding="utf-8")
    base = {"name": CFG, "die": DIE, "geometry_overrides": {"motor_length": 30, "num_wires_per_slot": 9, "wire_width": 2.0, "wire_height": 0.5},
            "winding": {"connection": "2S-2P", "star_delta": "star"}, "materials": {"stator_core": "20SW1200", "magnet": "N52UH"},
            "customer": "Secret Customer Ltd", "project": "secret-project", "duties": []}
    (shared / "dies" / DIE / f"{CFG}.yaml").write_text(yaml.safe_dump(base), encoding="utf-8")
    (shared / "dies" / DIE / f"{BARE}.yaml").write_text(yaml.safe_dump({**base, "name": BARE}), encoding="utf-8")
    shutil.copy2(_ROOT / "config" / "motor_config.yaml", shared / "motor_config.yaml")
    monkeypatch.setenv("WORKSPACES_ROOT", str(works))
    monkeypatch.setenv("SHARED_ROOT", str(shared))
    monkeypatch.setenv("PUBLISHED_ROOT", str(published))
    monkeypatch.setenv("MOTRES_OBJECT_REGISTRY_ROOT", str(tmp_path / "registry"))
    monkeypatch.setenv("ADMIN_AUDIT_FILE", str(tmp_path / "audit.jsonl"))
    from motor_ai_sim import motor_access as MA
    monkeypatch.setattr(MA, "_DIE_ACCESS_FILE", tmp_path / "die_access.json")
    monkeypatch.delitem(fam.__dict__, "_DIES_DIR", raising=False)
    fam._TREE_CACHE.clear()
    SP._CACHE.clear()
    users_file = tmp_path / "users.json"
    monkeypatch.setattr(U, "_USERS_FILE", users_file)
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)
    U.create_user(ADMIN, "password-admin", role="admin", name="Admin")
    U.create_user(A, "password-a", role="user", name="Alice")
    points = {CFG: {"rated": {"em": _duty("rated")}, "peak": {"em": _duty("peak")}, "secret": {"em": _duty("secret")},
                    "bench": {"em": _duty("bench", kind="test")}}}
    monkeypatch.setattr(dr, "index", lambda die, shared=False: points if die == DIE else {})
    monkeypatch.setattr(fam, "_build_config_row", _row_with_duties(fam._build_config_row, base))
    (tmp_path / "identity").mkdir(exist_ok=True)
    monkeypatch.setattr("motor_ai_sim.config.DEFAULT_CONFIG_PATH", str(tmp_path / "identity" / "motor_config.yaml"))
    yield {"admin": {"Authorization": f"Bearer {U.issue_token(ADMIN)}"}, "a": {"Authorization": f"Bearer {U.issue_token(A)}"}}
    SP._CACHE.clear()
    fam._TREE_CACHE.clear()


def _row_with_duties(real, base):
    def build(die, c, name, dr_cfg):
        row = real(die, c, name, dr_cfg)
        if name == CFG:
            row["duties"] = [_duty("rated"), _duty("peak"), _duty("secret"), {**_duty("bench"), "kind": "test"}]
        return row
    return build


def _put(env, cfg=CFG, who="admin", **body):
    return client.put(f"/api/admin/site-publication/{DIE}/{cfg}", json=body, headers=env.get(who, {}))


def _publish(env):
    r = _put(env, show=True, availability="to_order", lead_time_text="8 weeks", points=["rated", "peak"], rated_point="rated",
             price_text="on request", slug="ciano28-150-30")
    assert r.status_code == 200, r.text
    return r.json()


def _walk(o):
    if isinstance(o, dict):
        for k, v in o.items():
            yield k
            yield from _walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v)


def test_nothing_leaves_while_no_motor_is_flagged(env):
    r = client.get("/api/public/site/motors")
    assert r.status_code == 200 and r.json()["count"] == 0 and r.json()["motors"] == []
    assert client.get("/api/public/site/motors/anything").status_code == 404


def test_a_flagged_motor_is_public_and_matches_the_schema(env):
    rec = _publish(env)
    assert rec["problems"] == [] and rec["preview"]["designation"]
    r = client.get("/api/public/site/motors")                           # no sign-in
    assert r.status_code == 200
    body = r.json()
    assert body["schema_version"] == 1 and body["count"] == 1
    m = body["motors"][0]
    assert m["slug"] == "ciano28-150-30" and m["designation"].startswith("CIAN28 150_30 ") and "?" not in m["designation"]
    assert m["outer_diameter_mm"] == 150.0 and m["slots"] == 24 and m["poles"] == 28 and m["stack_length_mm"] == 30.0
    assert m["availability"] == "to_order" and m["lead_time_text"] == "8 weeks" and m["price_text"] == "on request"
    assert m["rated_point"]["name"] == "rated" and [p["name"] for p in m["points"]] == ["rated", "peak"]
    assert m["mass_kg"] == 2.5 and "turns per phase" in m["winding"]["words"]
    one = client.get("/api/public/site/motors/ciano28-150-30").json()
    assert one["motor"] == m
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.validate(body, SCHEMA)
    jsonschema.validate(one, SCHEMA)
    bad = json.loads(json.dumps(body))
    bad["motors"][0]["geometry"] = {"air_gap": 1}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, SCHEMA)                                # a new field fails loudly


def test_private_fields_and_unmarked_results_cannot_leave(env):
    _publish(env)
    text = client.get("/api/public/site/motors").text
    body = json.loads(text)
    for secret in ("Secret Customer", "secret-project", "admin@example.com", "alice@example.com", "bench", "secret"):
        assert secret not in text, secret
    keys = set(_walk(body))
    assert not (keys & GEOMETRY_DENYLIST), keys & GEOMETRY_DENYLIST
    for k in ("project", "projects", "production", "customer", "owner", "email", "svg", "thumb_svg", "build_sig", "payload"):
        assert k not in keys, k


def test_an_unflagged_motor_of_the_same_die_stays_private(env):
    _publish(env)
    assert _put(env, cfg=BARE, show=True, availability="to_order").status_code == 200
    slugs = [m["slug"] for m in client.get("/api/public/site/motors").json()["motors"]]
    assert len(slugs) == 2 and len(set(slugs)) == 2 and "ciano28-150-30" in slugs     # equal designations still get distinct slugs
    assert _put(env, cfg=BARE, show=False).status_code == 200
    assert [m["slug"] for m in client.get("/api/public/site/motors").json()["motors"]] == ["ciano28-150-30"]
    assert client.get("/api/public/site/motors/l99").status_code == 404


def test_an_incomplete_designation_or_missing_availability_never_publishes(env, tmp_path):
    assert _put(env, show=True).status_code == 200                                     # flagged, no availability: not public yet
    assert client.get("/api/public/site/motors").json()["count"] == 0
    assert "availability not set" in client.get(f"/api/admin/site-publication/{DIE}/{CFG}", headers=env["admin"]).json()["problems"]
    die_file = tmp_path / "shared" / "dies" / DIE / "die.yaml"
    doc = yaml.safe_load(die_file.read_text())
    del doc["geometry"]["rotor_outer_radius"]
    die_file.write_text(yaml.safe_dump(doc))
    assert _put(env, availability="to_order").status_code == 200
    SP._CACHE.clear()
    assert client.get("/api/public/site/motors").json()["count"] == 0
    assert any(p.startswith("designation incomplete") for p in client.get(f"/api/admin/site-publication/{DIE}/{CFG}", headers=env["admin"]).json()["problems"])


def test_cors_cache_and_etag(env):
    _publish(env)
    ok = client.get("/api/public/site/motors", headers={"Origin": "https://emotres.com"})
    assert ok.headers["access-control-allow-origin"] == "https://emotres.com" and "max-age" in ok.headers["cache-control"]
    assert ok.headers["etag"] and ok.headers["vary"].lower().find("origin") >= 0
    other = client.get("/api/public/site/motors", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in other.headers
    assert client.get("/api/public/site/motors").headers["etag"] == ok.headers["etag"]       # generated_at is not part of it


def test_the_public_routes_work_when_the_exhibit_is_closed(env, monkeypatch):
    from motor_ai_sim import auth
    monkeypatch.setenv("PUBLIC_EXHIBIT", "0")
    assert auth.anonymous_allowed("/api/public/site/motors") and auth.anonymous_allowed("/api/public/site/motors/x")
    assert not auth.anonymous_allowed("/api/admin/site-publication")


def test_only_an_admin_reads_or_sets_the_record(env):
    for who in ("a", "anon"):
        assert _put(env, who=who, show=True).status_code in (401, 403)
        assert client.get(f"/api/admin/site-publication/{DIE}/{CFG}", headers=env.get(who, {})).status_code in (401, 403, 404)
        assert client.get("/api/admin/site-publication", headers=env.get(who, {})).status_code in (401, 403, 404)
    assert SP.record_of(DIE, CFG)["show"] is False


def test_validation_and_audit(env, tmp_path):
    assert _put(env, availability="soon").status_code == 422
    assert _put(env, slug="Bad Slug").status_code == 422
    assert _put(env, image_url="http://x.test/a.png").status_code == 422
    assert _put(env, points=["rated"], rated_point="peak").status_code == 422
    assert _put(env, slug="taken").status_code == 200
    assert _put(env, cfg=BARE, slug="taken").status_code == 422
    assert client.put(f"/api/admin/site-publication/NOPE/X", json={"show": True}, headers=env["admin"]).status_code == 404
    audit = [json.loads(l) for l in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert audit and all(a["action"] == "site.publication" for a in audit)


def test_a_rename_and_a_move_carry_the_record():
    doc = {"motors": {"motor:A/c1": {"show": True}, "motor:B/c2": {"show": False}}, "history": [{"key": "motor:A/c1"}]}
    assert SP.rename_die_in(doc, "A", "A2") == 2 and "motor:A2/c1" in doc["motors"] and doc["history"][0]["key"] == "motor:A2/c1"
    assert SP.move_config_in(doc, "B", "c2", "C", "c9") == 1 and doc["motors"]["motor:C/c9"] == {"show": False}
    with pytest.raises(ValueError):
        SP.rename_die_in({"motors": {"motor:A/c": {}, "motor:B/c": {}}}, "A", "B")


def test_a_private_configuration_a_retired_die_and_a_closed_die_cannot_be_shown(env, tmp_path):
    from motor_ai_sim import motor_access as MA
    from motor_ai_sim import workspace as WS
    cfg_file = tmp_path / "shared" / "dies" / DIE / f"{BARE}.yaml"
    cfg_file.write_text("visibility: private\n" + cfg_file.read_text())
    r = _put(env, cfg=BARE, show=True)
    assert r.status_code == 422 and "private" in r.json()["detail"]
    _publish(env)
    assert client.get("/api/public/site/motors").json()["count"] == 1
    cfg_file2 = tmp_path / "shared" / "dies" / DIE / f"{CFG}.yaml"
    cfg_file2.write_text("visibility: private\n" + cfg_file2.read_text())          # made private AFTER it was published
    SP._CACHE.clear()
    assert client.get("/api/public/site/motors").json()["count"] == 0
    assert "the configuration is private" in client.get(f"/api/admin/site-publication/{DIE}/{CFG}", headers=env["admin"]).json()["problems"]
    cfg_file2.write_text(cfg_file2.read_text().replace("visibility: private\n", "", 1))
    SP._CACHE.clear()
    assert client.get("/api/public/site/motors").json()["count"] == 1
    WS.add_tombstone(DIE, "admin@example.com")                                      # a retired shared die leaves the site
    SP._CACHE.clear()
    assert client.get("/api/public/site/motors").json()["count"] == 0
    WS.remove_tombstone(DIE)
    MA.set_die_access(DIE, visibility="selected", clients=["x@example.com"])
    SP._CACHE.clear()
    assert client.get("/api/public/site/motors").json()["count"] == 0
    assert _put(env, show=True).status_code == 422


def test_availability_in_production_must_agree_with_the_production_state(env):
    from motor_ai_sim import production_lifecycle as PL
    assert _put(env, availability="in_production").status_code == 422
    PL.set_state(DIE, CFG, "in_production", by="admin@example.com", payment="paid")
    assert _put(env, show=True, availability="in_production", slug="x1").status_code == 200
    assert client.get("/api/public/site/motors").json()["count"] == 1
    PL.set_state(DIE, CFG, "finished", by="admin@example.com")
    SP._CACHE.clear()
    assert client.get("/api/public/site/motors").json()["count"] == 0               # the stored value now contradicts it


def test_the_derived_slug_is_fixed_at_the_first_publish(env):
    assert _put(env, show=True, availability="to_order").status_code == 200
    first = SP.record_of(DIE, CFG)["slug"]
    assert first and first.startswith("cian28-150-30")
    assert _put(env, cfg=BARE, show=True, availability="to_order").status_code == 200
    second = SP.record_of(DIE, BARE)["slug"]
    assert second != first                                                          # equal designations never share a slug
    assert _put(env, lead_time_text="4 weeks").status_code == 200
    assert SP.record_of(DIE, CFG)["slug"] == first
