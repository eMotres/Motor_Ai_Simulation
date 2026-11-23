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
        old = list(p["members"])
        p["members"] = old + [{"email": "eng@example.test", "role": "engineer"}, {"email": "cust@example.test", "role": "customer"}]
        store.save_json(pid, "project.json", p)
        store.sync_membership_index(pid, old, p["members"])
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


def test_a_save_on_a_revision_somebody_else_saved_over_is_refused(env):
    """Astra round 1 of #832: A saves, B saves, A saves again with the revision of A's own answer -> 409, B's value stays."""
    c = env["c"]
    a1 = c.put("/api/projects/ctl/controller-spec", headers=H, json={"fields": {"pwm": {"value": 48}}, "revision": 0}).json()
    b = c.put("/api/projects/ctl/controller-spec", headers=H, json={"fields": {"pwm": {"value": 60}}, "revision": a1["revision"]})
    assert b.status_code == 200 and b.json()["revision"] == 2
    a2 = c.put("/api/projects/ctl/controller-spec", headers=H,
               json={"fields": {"pwm": {"value": 48}, "mcu": {"value": "X"}}, "revision": a1["revision"]})
    assert a2.status_code == 409 and a2.json()["detail"]["revision"] == 2
    assert c.get("/api/projects/ctl/controller-spec", headers=H).json()["fields"]["pwm"]["value"] == 60.0


def test_members_who_are_not_staff_never_see_an_authors_address(env):
    c = env["c"]
    p = store.get_project("ctl")
    p["members"].append({"email": "oem@example.test", "role": "customer"})
    store.save_json("ctl", "project.json", p)
    assert c.put("/api/projects/ctl/controller-spec", headers=H, json={"fields": GOOD, "revision": 0}).status_code == 200
    for email, role in (("cust@example.test", "user"), ("oem@example.test", "oem")):
        env["who"].update(email=email, role=role)
        r = c.get("/api/projects/ctl/controller-spec", headers=H)
        assert r.status_code == 200 and "admin@example.test" not in r.text
        assert r.json()["history"][0]["changes"]                       # what changed is still shown
    env["who"].update(email="eng@example.test", role="engineer")    # staff keep the address
    assert c.get("/api/projects/ctl/controller-spec", headers=H).json()["updated_by"] == "admin@example.test"
    assert "admin@example.test" in (env["root"] / "ctl" / CS.FILE).read_text()          # storage keeps the attribution
    assert "admin@example.test" in (env["root"] / "ctl" / CS.HISTORY).read_text()


def test_the_spec_assistant_never_serves_a_controller_project(env):
    from motor_ai_sim.development import spec_assistant as A
    member = {"email": "cust@example.test", "role": "user"}
    staff = {"email": "admin@example.test", "role": "admin"}
    assert A.may_use(member, store.get_project("mot")) and A.may_use(staff, store.get_project("mot"))
    assert not A.may_use(member, store.get_project("ctl")) and not A.may_use(staff, store.get_project("ctl"))


def test_the_mcp_order_tools_never_reach_a_controller_project(env):
    from motor_ai_sim.development import spec_order as SO
    member = {"email": "cust@example.test", "role": "user"}
    assert SO._own_project(member, "mot")["id"] == "mot"
    with pytest.raises(SO.OrderError):
        SO._own_project(member, "ctl")
    with pytest.raises(SO.OrderError):
        SO.order_status(member, "ctl")
    listed = [r.get("id") or r.get("project_id") for r in SO.order_status(member)["projects"]]
    assert "ctl" not in listed and "mot" in listed
