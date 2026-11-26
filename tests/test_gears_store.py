"""Stage 3 of the gear module: saved gearbox records (gear_store.py, routes/gears_store.py).

A throwaway app with only the store router; the store root is a tmp folder.
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from motor_ai_sim import auth, gear_store
from motor_ai_sim.gears import GearSpec, GearboxRequest, PairStageSpec
from motor_ai_sim.routes.gears_store import router

B = "/api/gears/boxes"


def gear(z):
    return GearSpec(z, 1., 12., tooth_thickness_allowance_mm=.04)


def req(z2=40, rid="req_a"):
    return GearboxRequest([PairStageSpec("p", gear(20), gear(z2), stage_id="stage_p")], request_id=rid)


def body(z2=40, with_response=True, **kw):
    r = req(z2)
    d = {"name": "Test box", "request": r.to_dict()}
    if with_response:
        d["response"] = r.execute().to_dict()
    d.update(kw)
    return d


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", set())
    monkeypatch.setattr(gear_store, "root_dir", lambda: tmp_path / "gearboxes")
    app = FastAPI()
    app.include_router(router)
    c = TestClient(app)
    c.root = tmp_path / "gearboxes"
    return c


@pytest.fixture
def enforced(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "AUTH_ENFORCE", True)
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {"owner@example.com"})
    monkeypatch.setattr(auth, "resolve_user", lambda authorization, **kw: (
        {"uid": "u", "email": authorization.split()[-1] + "@example.com", "role": authorization.split()[-1]}
        if authorization else None))
    monkeypatch.setattr(gear_store, "root_dir", lambda: tmp_path / "gearboxes")
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def as_(role):
    return {"Authorization": "Bearer " + role}


def make(client, **kw):
    r = client.post(B, json=body(**kw))
    assert r.status_code == 200, r.text
    return r.json()


def test_create_list_get(client):
    rec = make(client)
    assert rec["version"] == 1 and rec["state"] == "active" and rec["flags"] == [] and not rec["response_stale"]
    assert rec["author"] == "local-dev" or rec["author"]  # dev user
    assert rec["id"].startswith("gb_") and rec["request"] == req().to_dict()
    assert rec["core_version"] == "gear-core-r4" and len(rec["request_hash"]) == 64
    lst = client.get(B).json()
    assert [i["id"] for i in lst["items"]] == [rec["id"]] and "request" not in lst["items"][0]
    assert lst["items"][0]["has_response"] is True
    got = client.get(f"{B}/{rec['id']}").json()
    assert got["request"] == rec["request"] and got["response"] == rec["response"]


def test_scopes_are_separate(client):
    a = make(client)
    b = client.post(B + "?project=proj-1", json=body()).json()
    assert [i["id"] for i in client.get(B).json()["items"]] == [a["id"]]
    assert [i["id"] for i in client.get(B + "?project=proj-1").json()["items"]] == [b["id"]]
    assert client.get(f"{B}/{a['id']}?project=proj-1").status_code == 404
    assert client.get(B + "?project=../x").status_code == 422


def test_update_and_stale_version_409(client):
    rec = make(client)
    r = client.put(f"{B}/{rec['id']}", json={"version": 1, "name": "Renamed"})
    assert r.status_code == 200 and r.json()["version"] == 2 and r.json()["name"] == "Renamed"
    assert r.json()["response"] == rec["response"]
    stale = client.put(f"{B}/{rec['id']}", json={"version": 1, "name": "Other"})
    assert stale.status_code == 409
    assert stale.json()["detail"]["error"] == "stale_version" and stale.json()["detail"]["current_version"] == 2
    assert client.get(f"{B}/{rec['id']}").json()["name"] == "Renamed"
    assert client.put(f"{B}/{rec['id']}", json={"name": "x"}).status_code == 422


def test_update_request_clears_old_response_and_accepts_new(client):
    rec = make(client)
    r2 = req(60)
    up = client.put(f"{B}/{rec['id']}", json={"version": 1, "request": r2.to_dict()}).json()
    assert up["response"] is None and up["request_hash"] != rec["request_hash"]
    up2 = client.put(f"{B}/{rec['id']}", json={"version": 2, "response": r2.execute().to_dict()}).json()
    assert up2["version"] == 3 and up2["response"]["request_hash"] == up2["request_hash"]
    bad = client.put(f"{B}/{rec['id']}", json={"version": 3, "response": req(30).execute().to_dict()})
    assert bad.status_code == 422 and bad.json()["detail"]["error"] == "response_mismatch"


def test_archive_restore_never_hard_delete(client):
    rec = make(client)
    d = client.delete(f"{B}/{rec['id']}")
    assert d.status_code == 200 and d.json()["state"] == "archived" and d.json()["version"] == 2
    assert (client.root / "_workspace" / f"{rec['id']}.json").exists()
    assert client.get(B).json()["items"] == []
    assert [i["id"] for i in client.get(B + "?state=archived").json()["items"]] == [rec["id"]]
    assert [i["id"] for i in client.get(B + "?state=all").json()["items"]] == [rec["id"]]
    assert client.put(f"{B}/{rec['id']}", json={"version": 2, "name": "x"}).status_code == 409
    assert client.get(f"{B}/{rec['id']}").status_code == 200
    r = client.post(f"{B}/{rec['id']}/restore")
    assert r.status_code == 200 and r.json()["state"] == "active" and r.json()["version"] == 3
    assert client.delete(f"{B}/{rec['id']}?version=1").status_code == 409
    assert client.get(B + "?state=bogus").status_code == 422


def test_hash_mismatch_flag(client):
    rec = make(client)
    p = client.root / "_workspace" / f"{rec['id']}.json"
    doc = json.loads(p.read_text())
    doc["request"]["stages"][0]["gear2"]["z"] = 41            # hand edit of the request
    p.write_text(json.dumps(doc))
    got = client.get(f"{B}/{rec['id']}").json()
    assert "request_hash_mismatch" in got["flags"] and "checksum_mismatch" in got["flags"]
    assert got["response_stale"] is True


def test_core_version_flag(client):
    rec = make(client)
    p = client.root / "_workspace" / f"{rec['id']}.json"
    doc = json.loads(p.read_text())
    doc["core_version"] = "gear-core-r3"
    doc["checksum"] = gear_store._checksum(doc)
    p.write_text(json.dumps(doc))
    got = client.get(f"{B}/{rec['id']}")
    assert got.status_code == 200 and got.json()["flags"] == ["core_stale"] and got.json()["response_stale"] is True
    assert client.get(B).json()["items"][0]["flags"] == ["core_stale"]


def test_catalogue_changed_flag(client, monkeypatch):
    rec = make(client)
    monkeypatch.setattr(gear_store, "catalogue_digest", lambda: "0" * 64)
    assert client.get(f"{B}/{rec['id']}").json()["flags"] == ["catalogue_changed"]


def test_corrupt_file_does_not_break_list(client):
    a, b = make(client), make(client)
    (client.root / "_workspace" / f"{a['id']}.json").write_text("{ not json")
    lst = client.get(B).json()
    assert [i["id"] for i in lst["items"]] == [b["id"]]
    assert lst["unreadable"][0]["file"] == f"{a['id']}.json"
    assert client.get(f"{B}/{a['id']}").status_code == 500
    assert client.get(f"{B}/{b['id']}").status_code == 200


def test_atomic_write_leaves_no_tmp(client):
    rec = make(client)
    client.put(f"{B}/{rec['id']}", json={"version": 1, "name": "n"})
    assert sorted(p.name for p in (client.root / "_workspace").iterdir()) == [f"{rec['id']}.json"]


def test_validation_paths_422(client):
    bad = req().to_dict()
    bad["stages"][0]["gear1"]["z"] = -3
    r = client.post(B, json={"name": "x", "request": bad})
    assert r.status_code == 422
    assert r.json()["detail"]["errors"][0]["path"] == "/stages/0/gear1/z"
    assert client.post(B, json={"name": "x"}).status_code == 422
    assert client.post(B, json={"name": " ", "request": req().to_dict()}).status_code == 422
    assert client.post(B, json={"name": "x" * 500, "request": req().to_dict()}).status_code == 422
    assert client.get(B).json()["items"] == []


def test_size_limits(client, monkeypatch):
    monkeypatch.setattr(gear_store, "MAX_REQUEST_BYTES", 100)
    assert client.post(B, json=body(with_response=False)).status_code == 413
    monkeypatch.setattr(gear_store, "MAX_REQUEST_BYTES", 1_000_000)
    monkeypatch.setattr(gear_store, "MAX_RESPONSE_BYTES", 100)
    assert client.post(B, json=body()).status_code == 413


def test_ids_and_missing(client):
    assert client.get(f"{B}/nope").status_code == 404
    assert client.get(f"{B}/gb_0123456789abcdef").status_code == 404
    assert client.put(f"{B}/gb_0123456789abcdef", json={"version": 1}).status_code == 404


def test_staff_only(enforced):
    b = body()
    assert enforced.get(B).status_code == 401
    for role in ("user", "oem"):
        h = as_(role)
        assert enforced.get(B, headers=h).status_code == 403
        assert enforced.post(B, json=b, headers=h).status_code == 403
        assert enforced.put(B + "/gb_0123456789abcdef", json={"version": 1}, headers=h).status_code == 403
        assert enforced.delete(B + "/gb_0123456789abcdef", headers=h).status_code == 403
        assert enforced.post(B + "/gb_0123456789abcdef/restore", headers=h).status_code == 403
    r = enforced.post(B, json=b, headers=as_("engineer"))
    assert r.status_code == 200 and r.json()["author"] == "engineer@example.com"


def test_role_gate_prefix_registered():
    for m in ("GET", "POST", "PUT", "DELETE"):
        assert (m, "/api/gears", "engineer") in auth._GATED_PREFIX
