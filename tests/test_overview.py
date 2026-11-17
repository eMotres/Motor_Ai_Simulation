"""The owner's Overview (owner 2026-11-16): items with their projects and productions, display states, registry ids and
review; admins only; reads, never writes.  Temporary stores, neutral names, no solver."""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from motor_ai_sim import auth
from motor_ai_sim import display_state as DS
from motor_ai_sim import object_registry as OR
from motor_ai_sim.development import store
from motor_ai_sim.production import store as pstore
from motor_ai_sim.routes.overview import build, router

USERS = {"admin": {"email": "admin@example.test", "role": "admin"},
         "eng": {"email": "eng@example.test", "role": "engineer"},
         "customer": {"email": "customer@example.test", "role": "user"}}


def _h(who):
    return {"Authorization": "Bearer " + who}


def _production(die, config, **kw):
    path = pstore.path_for(die, config) / "production.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"die": die, "config": config, "status": "active", "title": {"en": f"{die} {config}"},
                                "current_revision": "1.2", "order_quantity": 40, **kw}), encoding="utf-8")
    return path


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("MOTRES_PRODUCTION_ROOT", str(tmp_path / "production"))
    monkeypatch.setenv("MOTRES_OBJECT_REGISTRY_ROOT", str(tmp_path / "registry"))
    monkeypatch.setattr(auth, "resolve_user", lambda a, **_k: USERS.get(str(a or "").split(" ", 1)[-1]))
    base = {"customer_org": "Org", "created_at": "t", "updated_at": "t", "notes": "", "members": [], "pending_invites": []}
    store.create_project({**base, "id": "hydro", "title": "Hydro", "status": "design",
                          "linked_motors": [{"die": "DIE A", "config": "L12", "status": "current"},
                                            {"die": "CTRL", "config": "R07", "status": "current"}]})
    store.create_project({**base, "id": "joint", "title": "Joint", "status": "closed",
                          "linked_motors": [{"die": "DIE A", "config": "L20", "status": "trial"}]})
    store.create_project({**base, "id": "fresh", "title": "Fresh", "status": "spec", "linked_motors": []})
    files = [store.path_for(p) / "project.json" for p in ("hydro", "joint", "fresh")]
    files += [_production("DIE A", "L12"), _production("CTRL", "R07", product_type="electronics", status="paused")]
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), files, tmp_path


def test_items_carry_their_projects_productions_and_states(env):
    client, files, _tmp = env
    before = {p: p.read_bytes() for p in files}
    body = client.get("/api/overview", headers=_h("admin")).json()
    a12, a20, board = body["items"]["DIE A/L12"], body["items"]["DIE A/L20"], body["items"]["CTRL/R07"]
    assert [p["id"] for p in a12["projects"]] == ["hydro"] and a12["productions"] == ["DIE A/L12"]
    assert a20["projects"] == [{"id": "joint", "link_status": "trial"}] and a20["productions"] == []
    assert board["kind"] == "board" and board["productions"] == ["CTRL/R07"]
    assert a12["works_with"] == ["CTRL/R07"] and board["works_with"] == ["DIE A/L12"]
    assert body["projects"]["joint"]["display"]["state"] == "archived"           # closed -> archived (default)
    assert body["productions"]["CTRL/R07"]["display"]["state"] == "parked"      # paused -> parked
    assert body["no_item_yet"] == ["fresh"]
    assert body["registry"] is None and a12["item_id"] is None                  # complete before the first backfill
    assert {p: p.read_bytes() for p in files} == before


def test_registry_ids_and_explicit_states_are_used(env, tmp_path):
    client, _files, tmp = env
    cat = tmp / "dies"
    for die, cfg in (("DIE A", "L12"), ("DIE A", "L20")):
        (cat / die).mkdir(parents=True, exist_ok=True)
        (cat / die / "die.yaml").write_text("name: x\n")
        (cat / die / f"{cfg}.yaml").write_text("controller:\n  catalog_id: ctl-1\n")
    OR.backfill(catalog_root=cat, projects_root=tmp / "projects", production_root=tmp / "production")
    DS.set_state(DS.object_key("project", project_id="hydro"), "parked", by="admin@example.test", reason="waiting")
    body = client.get("/api/overview", headers=_h("admin")).json()
    a12 = body["items"]["DIE A/L12"]
    assert a12["item_id"].startswith("itm_") and a12["analysis_controllers"] == ["ctl-1"]
    assert body["projects"]["hydro"]["project_id"].startswith("prj_")
    assert body["projects"]["hydro"]["display"]["state"] == "parked"
    assert body["registry"]["objects"] > 0


@pytest.mark.parametrize("who, code", [("eng", 403), ("customer", 403), (None, 401)])
def test_admins_only(env, who, code):
    client, _files, _tmp = env
    assert client.get("/api/overview", headers=_h(who) if who else {}).status_code == code


def test_build_is_pure():
    out = build([{"id": "p", "title": "P", "linked_motors": [{"die": "D", "config": "C"}]}], [], {}, {},
                has_results=lambda die, cfg: False)
    assert out["items"]["D/C"]["projects"] == [{"id": "p", "link_status": "current"}]


def test_stage_is_derived_and_board_projects_go_to_review():
    projects = [{"id": "hydro", "title": "Hydro", "status": "spec", "linked_motors": [{"die": "D", "config": "A"}]},
                {"id": "calc", "title": "Calc", "status": "spec", "linked_motors": [{"die": "D", "config": "B"}]},
                {"id": "sub", "title": "Sub", "status": "spec", "spec_submitted": {"version": 2, "at": "t"}, "linked_motors": []},
                {"id": "ctl", "title": "Controller 800V board", "status": "design", "linked_motors": []},
                {"id": "gone", "title": "Gone", "status": "closed", "linked_motors": [{"die": "D", "config": "A"}]}]
    productions = [{"die": "D", "config": "A", "status": "active"},
                   {"die": "SIC", "config": "Rev A", "status": "active", "product_type": "electronics"}]
    out = build(projects, productions, {}, {}, has_results=lambda die, cfg: cfg == "B")
    stages = {pid: r["stage"] for pid, r in out["projects"].items()}
    # owner 2026-11-17: a production RECORD alone is not production (prepared = waiting for the payment: confirmation); stored
    # results are "simulation" (production_lifecycle / overview.motor_stage)
    assert stages == {"hydro": "confirmation", "calc": "simulation", "sub": "spec_submitted", "ctl": "design", "gone": "closed"}
    assert out["items"]["D/B"]["computed"] is True and out["items"]["D/A"]["computed"] is False
    assert sorted(out["no_item_yet"]) == ["ctl", "sub"]
    rev = [r for r in out["review"] if r["code"] == "project_without_item"]
    assert rev == [{"code": "project_without_item", "where": "Controller 800V board", "project": "ctl", "candidates": ["SIC/Rev A"]}]


def test_computed_reads_each_die_once_and_caches_it(monkeypatch):
    """Coordinator 2026-11-17: the page took 6-10 s with one full results read per motor."""
    from motor_ai_sim import duty_results
    from motor_ai_sim.routes import overview as OV
    calls = []

    def fake_index(die, shared=False):
        calls.append((die, shared))
        return {"A": {"rated": {"em": {}}}, "B": {}}
    monkeypatch.setattr(duty_results, "index", fake_index)
    monkeypatch.setattr(OV, "_RESULTS_CACHE", {})
    projects = [{"id": f"p{i}", "title": "P", "linked_motors": [{"die": "D", "config": c} for c in ("A", "B", "C")]} for i in range(5)]
    out = build(projects, [], {}, {})
    assert calls == [("D", True)]                                             # one read for the die, not one per motor
    assert {k: v["computed"] for k, v in out["items"].items()} == {"D/A": True, "D/B": False, "D/C": False}
    build(projects, [], {}, {})
    assert calls == [("D", True)]                                             # cached for a minute
