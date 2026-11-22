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
SCHEME = {"version": "v0.2", "audience": "staff", "nodes": [{"id": "customer", "label": {"en": "Customer", "zh": "客户"}, "x": 30, "y": 150},
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


def test_staff_read_it_only_admins_upload_it(client, tmp_path):
    for who in ("t-staff", "t-owner"):
        assert client.put("/api/contract-scheme", headers=hdr(who), content=json.dumps(SCHEME)).status_code == 403
    assert client.put("/api/contract-scheme", content=json.dumps(SCHEME)).status_code == 401
    client.put("/api/contract-scheme", headers=hdr("t-admin"), content=json.dumps(SCHEME))
    r = client.get("/api/contract-scheme", headers=hdr("t-staff"))
    assert r.status_code == 200 and r.json()["scheme"]["version"] == "v0.2" and r.json()["can_upload"] is False
    assert client.get("/api/contract-scheme", headers=hdr("t-admin")).json()["can_upload"] is True
    assert client.get("/api/contract-scheme", headers=hdr("t-owner")).status_code == 403
    reads = [json.loads(x) for x in (tmp_path / "admin_audit.jsonl").read_text().splitlines()]
    assert [r["action"] for r in reads].count("contract.scheme_read") == 2
    assert all("nodes" not in json.dumps(r.get("details") or {}) for r in reads if r["action"] == "contract.scheme_read")


@pytest.mark.parametrize("bad", [
    [], {"nodes": []}, {"nodes": [{"id": "A", "label": "x", "x": 1, "y": 1}]},
    {"nodes": [{"id": "a", "label": "x", "x": 1, "y": 1}, {"id": "a", "label": "y", "x": 2, "y": 2}]},
    {"nodes": [{"id": "a", "label": "x", "x": 5000, "y": 1}]},
    {"nodes": [{"id": "a", "label": "x", "x": 1, "y": 1}], "edges": [{"from": "a", "to": "b", "kind": "money"}]},
    {"nodes": [{"id": "a", "label": "x", "x": 1, "y": 1}], "edges": [{"from": "a", "to": "a", "kind": "profit"}]},
    {"nodes": [{"id": "a", "label": {"de": "x"}, "x": 1, "y": 1}]},
    {"nodes": [{"id": "a", "label": "x" * 201, "x": 1, "y": 1}]},
    {"nodes": [{"id": "a", "label": "x", "x": 1, "y": 1}], "edges": [{"from": ["a"], "to": "a", "kind": "money"}]},
    {"nodes": [{"id": "a", "label": "x", "x": 1, "y": 1}], "edges": [{"from": "a", "to": {"a": 1}, "kind": "money"}]},
    {"nodes": [{"id": "a", "label": "x", "x": 1, "y": 1}], "edges": [{"from": "a", "to": "a", "kind": ["money"]}]},
])
def test_a_scheme_that_does_not_fit_the_schema_is_refused_and_changes_nothing(client, tmp_path, bad):
    r = client.put("/api/contract-scheme", headers=hdr("t-admin"), content=json.dumps(bad))
    assert r.status_code == 422, r.text
    assert not (tmp_path / "shared" / "contract" / "supply_scheme_staff.json").exists()


def test_the_previous_scheme_is_kept_and_unknown_fields_are_dropped(client, tmp_path):
    client.put("/api/contract-scheme", headers=hdr("t-admin"), content=json.dumps(SCHEME))
    newer = {**SCHEME, "version": "v0.3", "secret_note": "never stored"}                # unknown fields are dropped
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


def test_the_shared_store_is_used_only_when_one_is_configured(monkeypatch, tmp_path):
    monkeypatch.delenv("MOTRES_CONTRACT_STORE_ROOT", raising=False)
    monkeypatch.delenv("SHARED_ROOT", raising=False)
    monkeypatch.delenv("WORKSPACES_ROOT", raising=False)
    from motor_ai_sim import workspace as W
    monkeypatch.setattr(W, "shared_root", lambda: tmp_path / "srv" / "shared")
    assert PC.contract_store_root() is None                                     # the process config dir is not a store
    monkeypatch.setenv("WORKSPACES_ROOT", str(tmp_path / "srv" / "ws"))
    assert PC.contract_store_root() is None                                     # WORKSPACES_ROOT alone names no shared store
    monkeypatch.setenv("SHARED_ROOT", str(tmp_path / "srv" / "shared"))
    assert PC.contract_store_root() == tmp_path / "srv" / "shared" / "contract"


def test_without_a_configured_store_the_private_checkout_serves(monkeypatch, tmp_path):
    monkeypatch.delenv("MOTRES_CONTRACT_STORE_ROOT", raising=False)
    monkeypatch.delenv("SHARED_ROOT", raising=False)
    monkeypatch.delenv("WORKSPACES_ROOT", raising=False)
    private = tmp_path / "private"
    (private / "config" / "contract").mkdir(parents=True)
    (private / "config" / "contract" / "supply_scheme_staff.json").write_text(json.dumps({**SCHEME, "version": "private"}))
    monkeypatch.setenv("MOTOR_AI_SIM_PRIVATE_DATA", str(private))
    assert PC.staff_scheme()["version"] == "private"



def test_every_previous_version_is_kept_under_its_own_name(client, tmp_path):
    for v in ("v1", "v2", "v3"):
        assert client.put("/api/contract-scheme", headers=hdr("t-admin"), content=json.dumps({**SCHEME, "version": v})).status_code == 200
    kept = sorted(json.loads(p.read_text())["version"] for p in (tmp_path / "shared" / "contract").glob("supply_scheme_staff.*.json"))
    assert kept == ["v1", "v2"]


def test_a_damaged_stored_scheme_is_not_replaced_silently(client, tmp_path):
    store_dir = tmp_path / "shared" / "contract"
    store_dir.mkdir(parents=True)
    (store_dir / "supply_scheme_staff.json").write_bytes(b'{"nodes": "damaged"}')
    r = client.put("/api/contract-scheme", headers=hdr("t-admin"), content=json.dumps(SCHEME))
    assert r.status_code == 409 and (store_dir / "supply_scheme_staff.json").read_bytes() == b'{"nodes": "damaged"}'
    r = client.put("/api/contract-scheme?replace_damaged=true", headers=hdr("t-admin"), content=json.dumps(SCHEME))
    assert r.status_code == 200
    assert [p.read_bytes() for p in store_dir.glob("supply_scheme_staff.*.json")] == [b'{"nodes": "damaged"}']


def test_the_body_cap_holds_while_it_arrives(client):
    big = json.dumps({**SCHEME, "notes": ["x" * 199] * 20}).encode() + b" " * (300 * 1024)

    def chunked():
        for i in range(0, len(big), 8192):
            yield big[i:i + 8192]

    assert client.put("/api/contract-scheme", headers=hdr("t-admin"), content=chunked()).status_code == 413


def test_a_failed_atomic_write_changes_nothing(client, tmp_path, monkeypatch):
    client.put("/api/contract-scheme", headers=hdr("t-admin"), content=json.dumps(SCHEME))
    path = tmp_path / "shared" / "contract" / "supply_scheme_staff.json"
    before = path.read_bytes()
    import os
    monkeypatch.setattr(os, "replace", lambda a, b: (_ for _ in ()).throw(OSError("rename failed")))
    r = client.put("/api/contract-scheme", headers=hdr("t-admin"), content=json.dumps({**SCHEME, "version": "v9"}))
    assert r.status_code == 503 and path.read_bytes() == before
    assert not list(path.parent.glob(".*.tmp"))


def test_the_customer_scheme_is_stored_too_and_every_reader_of_the_section_sees_it(client, tmp_path):
    cust = {**SCHEME, "version": "customer v1", "audience": "customer"}
    assert client.get("/api/projects/proj1/contract", headers=hdr("t-owner")).json()["customer_scheme"] is None   # built-in shown
    assert client.put("/api/contract-scheme?kind=customer", headers=hdr("t-staff"), content=json.dumps(cust)).status_code == 403
    r = client.put("/api/contract-scheme?kind=customer", headers=hdr("t-admin"), content=json.dumps(cust))
    assert r.status_code == 200, r.text
    assert (tmp_path / "shared" / "contract" / "scheme_customer.json").is_file()
    assert client.get("/api/projects/proj1/contract", headers=hdr("t-owner")).json()["customer_scheme"]["version"] == "customer v1"
    got = client.get("/api/contract-scheme?kind=customer", headers=hdr("t-owner"))
    assert got.status_code == 200 and got.json()["scheme"]["version"] == "customer v1"
    assert "store" not in got.json() and "source" not in got.json()                     # where it lives: staff only
    assert client.get("/api/contract-scheme?kind=customer", headers=hdr("t-staff")).json()["source"] == "shared"
    assert client.get("/api/projects/proj1/contract", headers=hdr("t-staff")).json()["staff_scheme"] is None     # kinds are separate
    assert client.get("/api/contract-scheme?kind=other", headers=hdr("t-admin")).status_code == 422
    assert client.put("/api/contract-scheme?kind=customer", headers=hdr("t-admin"), content=json.dumps({"nodes": []})).status_code == 422


def test_a_file_must_say_it_is_for_the_audience_it_is_published_to(client, tmp_path):
    """#825 follow-up F1: the staff and the customer scheme share one schema - the internal chain never reaches customers by mistake."""
    r = client.put("/api/contract-scheme?kind=customer", headers=hdr("t-admin"), content=json.dumps(SCHEME))     # a staff file
    assert r.status_code == 422 and "audience" in r.json()["detail"]
    unmarked = {k: v for k, v in SCHEME.items() if k != "audience"}
    assert client.put("/api/contract-scheme?kind=staff", headers=hdr("t-admin"), content=json.dumps(unmarked)).status_code == 422
    assert client.put("/api/contract-scheme?kind=staff", headers=hdr("t-admin"), content=json.dumps({**SCHEME, "audience": "all"})).status_code == 422
    assert not (tmp_path / "shared" / "contract" / "scheme_customer.json").exists()


def test_a_stored_file_marked_for_the_other_audience_is_never_served(client, tmp_path):
    d = tmp_path / "shared" / "contract"
    d.mkdir(parents=True)
    (d / "scheme_customer.json").write_text(json.dumps(SCHEME))                        # placed by hand, marked "staff"
    assert client.get("/api/projects/proj1/contract", headers=hdr("t-owner")).json()["customer_scheme"] is None
    legacy = {k: v for k, v in SCHEME.items() if k != "audience"}
    (d / "scheme_customer.json").write_text(json.dumps(legacy))                        # uploaded before the marker existed
    assert client.get("/api/projects/proj1/contract", headers=hdr("t-owner")).json()["customer_scheme"] is None   # customers: strict
    (d / "supply_scheme_staff.json").write_text(json.dumps(legacy))
    assert client.get("/api/projects/proj1/contract", headers=hdr("t-staff")).json()["staff_scheme"]["version"] == "v0.2"  # staff: lenient


def test_boxes_are_at_least_24_px(client):
    tiny = {**SCHEME, "nodes": [{**SCHEME["nodes"][0], "w": 20}, SCHEME["nodes"][1]]}
    assert client.put("/api/contract-scheme?kind=staff", headers=hdr("t-admin"), content=json.dumps(tiny)).status_code == 422


def test_a_factory_account_does_not_read_the_customer_scheme(client, monkeypatch):
    monkeypatch.setitem(ROLES, "t-oem", ("factory@example.test", "oem"))
    assert client.get("/api/contract-scheme?kind=customer", headers=hdr("t-oem")).status_code == 403



def test_a_stored_file_with_a_20_px_box_stays_served_and_is_not_damaged(client, tmp_path):
    d = tmp_path / "shared" / "contract"
    d.mkdir(parents=True)
    narrow = {**SCHEME, "nodes": [{**SCHEME["nodes"][0], "w": 20}, SCHEME["nodes"][1]]}
    (d / "supply_scheme_staff.json").write_text(json.dumps(narrow))                    # valid under #825 (minimum 20)
    assert client.get("/api/projects/proj1/contract", headers=hdr("t-staff")).json()["staff_scheme"]["nodes"][0]["w"] == 20
    r = client.put("/api/contract-scheme?kind=staff", headers=hdr("t-admin"), content=json.dumps(SCHEME))
    assert r.status_code == 200                                                        # not "damaged": a normal replacement
    assert client.put("/api/contract-scheme?kind=staff", headers=hdr("t-admin"), content=json.dumps(narrow)).status_code == 422


@pytest.mark.parametrize("audience", [["customer"], {"a": 1}, 3, None])
def test_an_unhashable_or_wrong_type_audience_is_refused_not_a_500(client, tmp_path, audience):
    bad = {**SCHEME, "audience": audience}
    assert client.put("/api/contract-scheme?kind=staff", headers=hdr("t-admin"), content=json.dumps(bad)).status_code == 422
    d = tmp_path / "shared" / "contract"
    d.mkdir(parents=True, exist_ok=True)
    (d / "scheme_customer.json").write_text(json.dumps(bad))                           # placed by hand
    r = client.get("/api/projects/proj1/contract", headers=hdr("t-owner"))
    assert r.status_code == 200 and r.json()["customer_scheme"] is None


GRID = {"audience": "staff", "nodes": [{"id": "customer", "label": "Customer", "col": 0, "row": 1},
                                       {"id": "motres", "label": {"en": "MOTRES d.o.o.", "zh": "MOTRES d.o.o."}, "col": 1, "row": 0}],
        "edges": [{"from": "customer", "to": "motres", "kind": "money", "label": "100 % in advance"}]}


def test_a_grid_scheme_without_x_y_is_valid_and_an_old_one_stays_valid(client):
    r = client.put("/api/contract-scheme?kind=staff", headers=hdr("t-admin"), content=json.dumps(GRID))
    assert r.status_code == 200, r.text
    assert r.json()["scheme"]["nodes"][0] == {"id": "customer", "label": "Customer", "col": 0, "row": 1}
    assert client.put("/api/contract-scheme?kind=staff", headers=hdr("t-admin"), content=json.dumps(SCHEME)).status_code == 200


@pytest.mark.parametrize("nodes", [
    [{"id": "a", "label": "A", "col": 0, "row": 0}, {"id": "b", "label": "B", "col": 0, "row": 0}],      # one cell twice
    [{"id": "a", "label": "A", "col": 9, "row": 0}],
    [{"id": "a", "label": "A", "col": 1.5, "row": 0}],
    [{"id": "a", "label": "A", "col": True, "row": 0}],
    [{"id": "a", "label": "A", "col": 0}],                                                               # row missing
    [{"id": "a", "label": "A"}],                                                                         # neither grid nor x / y
])
def test_a_wrong_grid_cell_is_refused(client, nodes):
    bad = {"audience": "staff", "nodes": nodes, "edges": []}
    assert client.put("/api/contract-scheme?kind=staff", headers=hdr("t-admin"), content=json.dumps(bad)).status_code == 422
