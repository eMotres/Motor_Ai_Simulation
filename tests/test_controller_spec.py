"""The controller specification (controller PR2): a data-file schema of 7 steps, per-value status, per-build answers, the
motor-spec edit rule, a recorded change per save; a motor project has none of it."""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from motor_ai_sim import auth, users
from motor_ai_sim.development import controller_spec as CS
from motor_ai_sim.development import store
from motor_ai_sim.development.routes import router

H = {"Authorization": "Bearer dev"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    who = {"email": "admin@example.test", "role": "admin"}
    monkeypatch.setattr(auth, "resolve_user", lambda token: dict(who) if token else None)
    monkeypatch.setattr(users, "get_user", lambda email: {"email_verified": True})
    app = FastAPI()
    app.include_router(router)
    c = TestClient(app)
    for pid, extra in (("ctl", {"project_type": "controller"}), ("mot", {})):
        assert c.post("/api/projects", headers=H, json={"id": pid, "title": "T", "customer_org": "O", **extra}).status_code == 200
    for pid in ("ctl", "mot"):
        p = store.get_project(pid)
        p["members"] += [{"email": "eng@example.test", "role": "engineer"}, {"email": "cust@example.test", "role": "customer"}]
        store.save_json(pid, "project.json", p)
    return {"c": c, "who": who, "root": tmp_path / "projects"}


GOOD = {"name": {"value": "INFA"}, "builds": {"value": ["6S", "12S", "6S"]},
        "bus_voltage": {"builds": {"6S": {"value": {"min": 18, "nom": 22.2, "max": 25.2}, "status": "requirement"},
                                   "12S": {"value": {"min": 36, "nom": None, "max": 50.4}, "status": "model", "ref": "study"}}},
        "pwm": {"value": 48, "status": "measured", "ref": "bench 2026-11-20"},
        "method": {"value": "foc_sensorless"}, "modes": {"value": ["regen", "current"]},
        "protections": {"value": {"over_voltage": {"threshold": 58, "unit": "V", "status": "requirement"}}},
        "tests": {"value": [{"name": "double-pulse", "pass": "<= 70 V"}, {"name": "", "pass": ""}]}}


def test_the_schema_has_seven_steps_in_both_languages_and_a_status_per_number():
    s = CS.schema()
    assert len(s["sections"]) == 7 and s["title"]["en"] == "Controller specification"
    assert [x["value"] for x in s["statuses"]] == ["requirement", "model", "measured"]
    for sec in s["sections"]:
        assert sec["title"]["en"] and sec["title"]["zh-CN"]
        for f in sec["fields"]:
            assert f["label"]["en"] and f["label"]["zh-CN"], f["id"]
            for o in f.get("options") or []:
                assert o["label"]["en"] and o["label"]["zh-CN"]
            assert f["kind"] in ("text", "longtext", "number", "range", "choice", "multi", "list", "thresholds", "rows")
    ids = [f["id"] for sec in s["sections"] for f in sec["fields"]]
    assert len(ids) == len(set(ids))


def test_save_read_and_the_recorded_change(env):
    c = env["c"]
    r = c.get("/api/projects/ctl/controller-spec", headers=H)
    assert r.status_code == 200 and r.json()["revision"] == 0 and r.json()["fields"] == {} and r.json()["can_edit"]
    r = c.put("/api/projects/ctl/controller-spec", headers=H, json={"fields": GOOD, "revision": 0})
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["revision"] == 1 and got["updated_by"] == "admin@example.test"
    f = got["fields"]
    assert f["builds"]["value"] == ["6S", "12S"]
    assert f["bus_voltage"]["builds"]["12S"] == {"value": {"min": 36.0, "nom": None, "max": 50.4}, "status": "model", "ref": "study"}
    assert f["pwm"] == {"value": 48.0, "status": "measured", "ref": "bench 2026-11-20"}
    assert f["modes"]["value"] == ["current", "regen"]                         # schema order
    assert f["tests"]["value"] == [{"name": "double-pulse", "pass": "<= 70 V"}]
    nxt = dict(GOOD, pwm={"value": 40, "status": "model"})
    r = c.put("/api/projects/ctl/controller-spec", headers=H, json={"fields": nxt, "revision": 1})
    assert r.status_code == 200 and r.json()["changed"] == ["pwm"]
    hist = c.get("/api/projects/ctl/controller-spec", headers=H).json()["history"]
    assert [h["revision"] for h in hist] == [2, 1]
    assert hist[0]["changes"] == [{"field": "pwm", "before": {"value": 48.0, "status": "measured", "ref": "bench 2026-11-20"},
                                   "after": {"value": 40.0, "status": "model", "ref": ""}}]
    same = c.put("/api/projects/ctl/controller-spec", headers=H, json={"fields": nxt, "revision": 2})
    assert same.json()["revision"] == 2 and same.json()["changed"] == []    # nothing changed: no new revision


def test_a_stale_revision_and_wrong_values_are_refused_and_nothing_is_written(env):
    c = env["c"]
    assert c.put("/api/projects/ctl/controller-spec", headers=H, json={"fields": GOOD, "revision": 0}).status_code == 200
    before = (env["root"] / "ctl" / CS.FILE).read_bytes()
    assert c.put("/api/projects/ctl/controller-spec", headers=H, json={"fields": {}, "revision": 0}).status_code == 409
    for bad in ({"nope": {"value": 1}}, {"pwm": {"value": "fast"}}, {"pwm": {"value": 1, "status": "guess"}},
                {"method": {"value": "magic"}}, {"bus_voltage": {"value": {"min": 1}}},
                {"ambient": {"value": {"min": 50, "max": -20}}}, {"modes": {"value": ["fly"]}}):
        r = c.put("/api/projects/ctl/controller-spec", headers=H, json={"fields": bad, "revision": 1})
        assert r.status_code == 422, (bad, r.text)
    assert (env["root"] / "ctl" / CS.FILE).read_bytes() == before


def test_only_the_project_owner_and_admins_edit_members_read(env):
    c = env["c"]
    for email, role in (("eng@example.test", "engineer"), ("cust@example.test", "user")):
        env["who"].update(email=email, role=role)
        r = c.get("/api/projects/ctl/controller-spec", headers=H)
        assert r.status_code == 200 and r.json()["can_edit"] is False
        assert c.put("/api/projects/ctl/controller-spec", headers=H, json={"fields": GOOD, "revision": 0}).status_code == 403


def test_a_motor_project_has_no_controller_specification(env):
    c = env["c"]
    assert c.get("/api/projects/mot/controller-spec", headers=H).status_code == 404
    assert c.put("/api/projects/mot/controller-spec", headers=H, json={"fields": GOOD, "revision": 0}).status_code == 404
    assert not (env["root"] / "mot" / CS.FILE).exists() and not (env["root"] / "mot" / CS.HISTORY).exists()
    audit = [json.loads(l)["entity"] for l in (env["root"] / "mot" / "audit.jsonl").read_text().splitlines()]
    assert "controller_spec" not in audit
