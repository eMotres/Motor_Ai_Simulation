"""Where a production really is, and the one derived stage of a motor (owner 2026-11-17: "In production now is ONLY this:
CIANO14 40 ... G2 has finished production, 200_50 has finished too. All the others are at the simulation or confirmation
stage. Production starts when the money from the customer arrives").  Temporary stores, neutral names."""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from motor_ai_sim import auth
from motor_ai_sim import production_lifecycle as PL
from motor_ai_sim.production import store as pstore
from motor_ai_sim.routes.overview import build, motor_stage

USERS = {"admin": {"email": "admin@example.test", "role": "admin"},
         "oem": {"email": "oem@example.test", "role": "user"},
         "other": {"email": "x@example.test", "role": "user"}}


def _production(die, config):
    path = pstore.path_for(die, config) / "production.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"die": die, "config": config, "status": "active", "title": {"en": die},
                                "members": [{"email": "oem@example.test", "role": "oem"}]}), encoding="utf-8")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PRODUCTION_ROOT", str(tmp_path / "production"))
    monkeypatch.setenv("MOTRES_OBJECT_REGISTRY_ROOT", str(tmp_path / "registry"))
    monkeypatch.setenv("ADMIN_AUDIT_FILE", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(auth, "resolve_user", lambda a, **_k: USERS.get(str(a or "").split(" ", 1)[-1]))
    _production("CIANO14 40", "L12")
    from motor_ai_sim.routes.production_lifecycle import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), tmp_path


def _h(who):
    return {"Authorization": "Bearer " + who}


def test_a_production_is_prepared_until_an_admin_says_otherwise_and_starting_it_needs_the_payment_line(env):
    c, tmp = env
    assert PL.state_of("CIANO14 40", "L12")["state"] == "prepared"
    put = lambda body, who="admin": c.put("/api/production-lifecycle", json={"die": "CIANO14 40", "config": "L12", **body}, headers=_h(who))  # noqa: E731
    assert put({"state": "in_production"}).status_code == 422                      # no payment line: refused
    r = put({"state": "in_production", "payment": "2026-11-10, advance received", "source": "owner 2026-11-17"})
    assert r.status_code == 200 and r.json()["state"] == "in_production" and r.json()["payment"] == "2026-11-10, advance received"
    assert put({"state": "finished"}).json()["payment"] == "2026-11-10, advance received"   # the start's line stays with it
    assert put({"state": "finished"}, "oem").status_code == 403                    # the factory cannot set it
    assert put({"state": "in_production", "payment": "x"}, "other").status_code == 403
    audit = [json.loads(l) for l in (tmp / "audit.jsonl").read_text().splitlines()]
    assert [a["action"] for a in audit] == ["production.lifecycle", "production.lifecycle"]
    hist = PL.load()["history"]
    assert [(h["from"], h["to"]) for h in hist] == [(None, "in_production"), ("in_production", "finished")]
    assert c.put("/api/production-lifecycle", json={"die": "NOPE", "config": "X", "state": "finished"}, headers=_h("admin")).status_code == 404


def test_the_factory_sees_the_state_but_not_who_or_why_and_nobody_else_sees_it(env):
    c, _ = env
    PL.set_state("CIANO14 40", "L12", "in_production", by="admin@example.test", payment="paid", source="owner")
    oem = c.get("/api/production-lifecycle/CIANO14 40/L12", headers=_h("oem")).json()
    assert oem == {"die": "CIANO14 40", "config": "L12", "state": "in_production", "explicit": True}
    assert c.get("/api/production-lifecycle/CIANO14 40/L12", headers=_h("other")).status_code == 404
    admin = c.get("/api/production-lifecycle/CIANO14 40/L12", headers=_h("admin")).json()
    assert admin["payment"] == "paid" and admin["source"] == "owner"
    assert c.get("/api/production-lifecycle", headers=_h("oem")).status_code == 403


def test_the_seed_on_the_owners_words_has_a_dry_run(env):
    rows = [{"die": "CIANO14 40", "config": "L12", "state": "in_production", "payment": "owner 2026-11-17: in production"}]
    assert PL.seed(rows, by="coordinator", source="owner 2026-11-17", dry_run=True) == [
        {"die": "CIANO14 40", "config": "L12", "from": "prepared", "to": "in_production"}]
    assert PL.state_of("CIANO14 40", "L12")["state"] == "prepared"                 # a dry run writes nothing
    PL.seed(rows, by="coordinator", source="owner 2026-11-17", dry_run=False)
    assert PL.state_of("CIANO14 40", "L12")["source"] == "owner 2026-11-17"


@pytest.mark.parametrize("facts, stage", [
    (dict(lifecycle="finished", has_production=True, offered=True, computed=True, spec_submitted=True), "finished"),
    (dict(lifecycle="in_production", has_production=True, offered=False, computed=True, spec_submitted=False), "in_production"),
    (dict(lifecycle=None, has_production=True, offered=False, computed=True, spec_submitted=False), "confirmation"),   # prepared only
    (dict(lifecycle=None, has_production=False, offered=True, computed=True, spec_submitted=True), "confirmation"),    # recommended
    (dict(lifecycle=None, has_production=False, offered=False, computed=True, spec_submitted=True), "simulation"),
    (dict(lifecycle=None, has_production=False, offered=False, computed=False, spec_submitted=True), "specification"),
    (dict(lifecycle=None, has_production=False, offered=False, computed=False, spec_submitted=False), None),
])
def test_each_stage_from_its_stored_fact(facts, stage):
    assert motor_stage(**facts) == stage


def test_the_overview_reads_the_one_stage_per_motor_and_per_project():
    rec = {"at": "t", "by": "admin"}
    projects = [
        {"id": "pump", "title": "Pump", "status": "design", "linked_motors": [{"die": "D", "config": "A", "status": "current"}]},
        {"id": "fan", "title": "Fan", "status": "design", "linked_motors": [{"die": "D", "config": "B", "status": "current", "recommended": rec}]},
        {"id": "old", "title": "Old", "status": "closed", "linked_motors": [{"die": "D", "config": "C", "status": "current", "recommended": rec}]},
        {"id": "new", "title": "New", "status": "spec", "spec_submitted": {"version": 1, "at": "t"},
         "linked_motors": [{"die": "D", "config": "E", "status": "current"}]}]
    productions = [{"die": "D", "config": "A", "status": "active"}, {"die": "D", "config": "F", "status": "active"},
                   {"die": "D", "config": "G", "status": "active"}]
    life = {"production:D/A": {"state": "in_production"}, "production:D/G": {"state": "finished"}}
    out = build(projects, productions, {}, {}, has_results=lambda d, c: c in ("B", "C"), lifecycle=life)
    stages = {k: v["stage"] for k, v in out["items"].items()}
    assert stages == {"D/A": "in_production", "D/F": "confirmation", "D/G": "finished", "D/B": "confirmation",
                      "D/C": "simulation", "D/E": "specification"}                 # C: offered only by a CLOSED project
    assert out["productions"]["D/F"]["lifecycle"] == "prepared"
    assert {p: r["stage"] for p, r in out["projects"].items()} == {"pump": "in_production", "fan": "confirmation", "old": "closed",
                                                                    "new": "specification"}
