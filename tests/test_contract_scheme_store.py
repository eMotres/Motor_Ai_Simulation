"""The contract section's staff scheme in the server's SHARED store (coordinator 2026-11-22: MOTOR_AI_SIM_PRIVATE_DATA is not set
in production): resolution order, the admin upload with its schema check, the previous version kept."""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from motor_ai_sim import auth
from motor_ai_sim.development import project_contract as PC
from motor_ai_sim.development import store

ROLES = {"t-admin": ("admin@example.test", "admin"), "t-staff": ("engineer@example.test", "engineer"),
         "t-owner": ("owner@example.test", "user")}
SCHEME = {"version": "v0.2", "nodes": [{"id": "customer", "label": {"en": "Customer", "zh": "客户"}, "x": 30, "y": 150},
                                       {"id": "motres", "label": "MOTRES d.o.o.", "sub": "Slovenia", "x": 720, "y": 20, "w": 250}],
          "edges": [{"from": "customer", "to": "motres", "kind": "money", "label": "100 % in advance", "bend": -35}],
          "notes": [{"en": "To be confirmed by the accountants.", "zh": "须由会计师确认。"}]}


def hdr(who):
    return {"Authorization": "Bearer " + who}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("MOTRES_CONTRACT_STORE_ROOT", str(tmp_path / "shared" / "contract"))
    monkeypatch.setenv("ADMIN_AUDIT_FILE", str(tmp_path / "admin_audit.jsonl"))
    monkeypatch.delenv("MOTOR_AI_SIM_PRIVATE_DATA", raising=False)
    monkeypatch.setattr(auth, "resolve_user", lambda token: None if not token else
                        dict(zip(("email", "role"), ROLES[token.split()[-1]])))
    store.create_project({"id": "proj1", "title": "P", "customer_org": "Org", "status": "simulation", "created_at": "t", "updated_at": "t",
                          "owner_engineer": "owner@example.test", "linked_motors": [], "notes": "", "pending_invites": [],
                          "members": [{"email": "owner@example.test", "role": "owner"}]})
    app = FastAPI()
    app.include_router(PC.router)
    app.include_router(PC.scheme_router)
    return TestClient(app)


def test_an_admin_uploads_the_scheme_and_staff_see_it_the_customer_never(client, tmp_path):
    assert client.get("/api/projects/proj1/contract", headers=hdr("t-staff")).json()["staff_scheme"] is None
    r = client.put("/api/contract-scheme", headers=hdr("t-admin"), content=json.dumps(SCHEME))
    assert r.status_code == 200, r.text
    assert (tmp_path / "shared" / "contract" / "supply_scheme_staff.json").is_file()
    seen = client.get("/api/projects/proj1/contract", headers=hdr("t-staff")).json()["staff_scheme"]
    assert seen["nodes"][0]["label"] == {"en": "Customer", "zh": "客户"} and seen["version"] == "v0.2"
    assert client.get("/api/projects/proj1/contract", headers=hdr("t-owner")).json()["staff_scheme"] is None
    assert client.get("/api/contract-scheme", headers=hdr("t-admin")).json()["source"] == "shared"


def test_only_admins_manage_it(client):
    for who in ("t-staff", "t-owner"):
        assert client.put("/api/contract-scheme", headers=hdr(who), content=json.dumps(SCHEME)).status_code == 403
        assert client.get("/api/contract-scheme", headers=hdr(who)).status_code == 403
    assert client.put("/api/contract-scheme", content=json.dumps(SCHEME)).status_code == 401


@pytest.mark.parametrize("bad", [
    [], {"nodes": []}, {"nodes": [{"id": "A", "label": "x", "x": 1, "y": 1}]},
    {"nodes": [{"id": "a", "label": "x", "x": 1, "y": 1}, {"id": "a", "label": "y", "x": 2, "y": 2}]},
    {"nodes": [{"id": "a", "label": "x", "x": 5000, "y": 1}]},
    {"nodes": [{"id": "a", "label": "x", "x": 1, "y": 1}], "edges": [{"from": "a", "to": "b", "kind": "money"}]},
    {"nodes": [{"id": "a", "label": "x", "x": 1, "y": 1}], "edges": [{"from": "a", "to": "a", "kind": "profit"}]},
    {"nodes": [{"id": "a", "label": {"de": "x"}, "x": 1, "y": 1}]},
    {"nodes": [{"id": "a", "label": "x" * 201, "x": 1, "y": 1}]},
])
def test_a_scheme_that_does_not_fit_the_schema_is_refused_and_changes_nothing(client, tmp_path, bad):
    r = client.put("/api/contract-scheme", headers=hdr("t-admin"), content=json.dumps(bad))
    assert r.status_code == 422, r.text
    assert not (tmp_path / "shared" / "contract" / "supply_scheme_staff.json").exists()


def test_the_previous_scheme_is_kept_and_unknown_fields_are_dropped(client, tmp_path):
    client.put("/api/contract-scheme", headers=hdr("t-admin"), content=json.dumps(SCHEME))
    newer = {**SCHEME, "version": "v0.3", "secret_note": "never stored"}
    client.put("/api/contract-scheme", headers=hdr("t-admin"), content=json.dumps(newer))
    kept = list((tmp_path / "shared" / "contract").glob("supply_scheme_staff.*.json"))
    assert len(kept) == 1 and json.loads(kept[0].read_text())["version"] == "v0.2"
    stored = json.loads((tmp_path / "shared" / "contract" / "supply_scheme_staff.json").read_text())
    assert stored["version"] == "v0.3" and "secret_note" not in stored


def test_the_shared_store_comes_first_then_the_private_checkout(client, tmp_path, monkeypatch):
    private = tmp_path / "private"
    (private / "config" / "contract").mkdir(parents=True)
    (private / "config" / "contract" / "supply_scheme_staff.json").write_text(json.dumps({**SCHEME, "version": "private"}))
    monkeypatch.setenv("MOTOR_AI_SIM_PRIVATE_DATA", str(private))
    assert PC.staff_scheme()["version"] == "private"                         # no shared one yet: the development fallback
    client.put("/api/contract-scheme", headers=hdr("t-admin"), content=json.dumps(SCHEME))
    assert PC.staff_scheme()["version"] == "v0.2"                            # the shared store wins


def test_a_hand_edited_scheme_that_breaks_the_schema_is_not_served(client, tmp_path):
    (tmp_path / "shared" / "contract").mkdir(parents=True)
    (tmp_path / "shared" / "contract" / "supply_scheme_staff.json").write_text('{"nodes": "oops"}')
    assert client.get("/api/projects/proj1/contract", headers=hdr("t-staff")).json()["staff_scheme"] is None


def test_without_a_shared_root_the_default_is_the_workspace_shared_root(monkeypatch, tmp_path):
    monkeypatch.delenv("MOTRES_CONTRACT_STORE_ROOT", raising=False)
    from motor_ai_sim import workspace as W
    monkeypatch.setattr(W, "shared_root", lambda: tmp_path / "srv" / "shared")
    assert PC.contract_store_root() == tmp_path / "srv" / "shared" / "contract"
