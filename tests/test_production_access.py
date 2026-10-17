"""Access control tests for AeroStator Production module:
Admin privileges, OEM membership visibility, non-member 404, NDA gate, and editing permissions.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from motor_ai_sim.api import app
from motor_ai_sim.production import models, store

ADMIN = "admin@example.com"
ENGINEER = "engineer@example.com"
OEM_NDA = "oem_nda@example.com"
OEM_NO_NDA = "oem_nonda@example.com"
NON_MEMBER = "nonmember@example.com"
DIE = "CIANO14 40 new"
CONFIG = "L12"


@pytest.fixture()
def prod_env(tmp_path, monkeypatch):
    from motor_ai_sim import auth, users as U

    prod_root = tmp_path / "production"
    prod_root.mkdir(parents=True)
    users_file = tmp_path / "users.json"
    audit_file = tmp_path / "admin_audit.jsonl"

    monkeypatch.setenv("MOTRES_PRODUCTION_ROOT", str(prod_root))
    monkeypatch.setattr(U, "_USERS_FILE", users_file)
    monkeypatch.setenv("AUTH_SECRET", "test-secret-prod-access-1234")
    monkeypatch.delenv("CATALOG_GRANT_ALL_REGISTERED", raising=False)
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)
    monkeypatch.setenv("ADMIN_AUDIT_FILE", str(audit_file))

    U.create_user(ADMIN, "password-admin", role="admin", name="Admin User")
    U.create_user(ENGINEER, "password-eng", role="engineer", name="Engineer User")
    U.create_user(OEM_NDA, "password-nda", role="oem", name="OEM with NDA")
    U.create_user(OEM_NO_NDA, "password-nonda", role="oem", name="OEM without NDA")
    U.create_user(NON_MEMBER, "password-none", role="oem", name="Non-Member")

    # Give OEM_NDA signed NDA by admin
    U.set_nda(OEM_NDA, True, "2026-10-01", by=ADMIN)

    client = TestClient(app)

    def auth_headers(email: str) -> dict:
        return {"Authorization": f"Bearer {U.issue_token(email)}"}

    return {
        "client": client,
        "admin": auth_headers(ADMIN),
        "engineer": auth_headers(ENGINEER),
        "oem_nda": auth_headers(OEM_NDA),
        "oem_no_nda": auth_headers(OEM_NO_NDA),
        "non_member": auth_headers(NON_MEMBER),
        "root": prod_root,
    }


def test_admin_can_create_and_list_production(prod_env):
    client = prod_env["client"]
    admin = prod_env["admin"]

    # Initially empty admin list
    res = client.get("/api/admin/production", headers=admin)
    assert res.status_code == 200
    assert res.json()["productions"] == []

    # Admin creates production
    payload = {
        "title": {"en": "CIANO14 40 L12 Production", "zh": "CIANO14 40 L12 生产", "src_lang": "en", "status": {"en": "confirmed", "zh": "confirmed"}},
        "order_quantity": 100,
        "members": [
            {"email": ENGINEER, "role": "engineer"},
            {"email": OEM_NDA, "role": "oem"},
            {"email": OEM_NO_NDA, "role": "oem"},
        ],
    }
    create_res = client.post(f"/api/admin/production/{DIE}/{CONFIG}", headers=admin, json=payload)
    assert create_res.status_code == 200
    created = create_res.json()
    assert created["die"] == DIE
    assert created["config"] == CONFIG
    assert created["order_quantity"] == 100
    assert len(created["members"]) == 3

    # Admin lists productions and sees members_without_nda
    list_res = client.get("/api/admin/production", headers=admin)
    assert list_res.status_code == 200
    prods = list_res.json()["productions"]
    assert len(prods) == 1
    assert OEM_NO_NDA in prods[0]["members_without_nda"]
    assert OEM_NDA not in prods[0]["members_without_nda"]


def test_visibility_and_non_member_404(prod_env):
    client = prod_env["client"]
    admin = prod_env["admin"]
    non_member = prod_env["non_member"]
    oem_nda = prod_env["oem_nda"]

    # Create production with engineer and OEM_NDA
    payload = {
        "title": {"en": "Test", "zh": "测试", "src_lang": "en", "status": {"en": "confirmed", "zh": "confirmed"}},
        "order_quantity": 50,
        "members": [{"email": OEM_NDA, "role": "oem"}],
    }
    client.post(f"/api/admin/production/{DIE}/{CONFIG}", headers=admin, json=payload)

    # Non-member lists productions -> sees empty
    res = client.get("/api/production", headers=non_member)
    assert res.status_code == 200
    assert res.json()["productions"] == []

    # Non-member tries to get production -> gets 404 (not 403)
    res404 = client.get(f"/api/production/{DIE}/{CONFIG}", headers=non_member)
    assert res404.status_code == 404

    # OEM member sees production in list
    res_oem = client.get("/api/production", headers=oem_nda)
    assert res_oem.status_code == 200
    assert len(res_oem.json()["productions"]) == 1
    assert res_oem.json()["productions"][0]["die"] == DIE


def test_nda_gate_refusal_and_pass(prod_env):
    client = prod_env["client"]
    admin = prod_env["admin"]
    oem_no_nda = prod_env["oem_no_nda"]
    oem_nda = prod_env["oem_nda"]

    # Create production with both OEM accounts
    payload = {
        "title": {"en": "Test", "zh": "测试", "src_lang": "en", "status": {"en": "confirmed", "zh": "confirmed"}},
        "order_quantity": 50,
        "members": [
            {"email": OEM_NO_NDA, "role": "oem"},
            {"email": OEM_NDA, "role": "oem"},
        ],
    }
    post_res = client.post(f"/api/admin/production/{DIE}/{CONFIG}", headers=admin, json=payload)
    assert post_res.status_code == 200

    # OEM without NDA gets 403 nda_required
    res_denied = client.get(f"/api/production/{DIE}/{CONFIG}", headers=oem_no_nda)
    assert res_denied.status_code == 403
    body = res_denied.json()
    assert body.get("detail", {}).get("code") == "nda_required" or body.get("code") == "nda_required"

    # OEM with NDA opens production successfully
    res_allowed = client.get(f"/api/production/{DIE}/{CONFIG}", headers=oem_nda)
    assert res_allowed.status_code == 200
    assert res_allowed.json()["die"] == DIE


def test_oem_cannot_edit_parts_while_engineer_can(prod_env):
    client = prod_env["client"]
    admin = prod_env["admin"]
    engineer = prod_env["engineer"]
    oem_nda = prod_env["oem_nda"]

    payload = {
        "title": {"en": "Test", "zh": "测试", "src_lang": "en", "status": {"en": "confirmed", "zh": "confirmed"}},
        "order_quantity": 50,
        "members": [
            {"email": ENGINEER, "role": "engineer"},
            {"email": OEM_NDA, "role": "oem"},
        ],
    }
    client.post(f"/api/admin/production/{DIE}/{CONFIG}", headers=admin, json=payload)

    part_payload = {
        "name": {"en": "Shaft", "zh": "轴", "src_lang": "en", "status": {"en": "confirmed", "zh": "confirmed"}},
        "kind": "made",
        "qty_per_motor": 1,
        "material": "SUS304",
        "spec": "Ø8h6",
        "description": {"en": "Ground shaft", "zh": "研磨轴", "src_lang": "en", "status": {"en": "confirmed", "zh": "confirmed"}},
    }

    # OEM tries to create part -> 403
    res_oem_edit = client.post(f"/api/production/{DIE}/{CONFIG}/parts", headers=oem_nda, json=part_payload)
    assert res_oem_edit.status_code == 403

    # Engineer creates part -> 200
    res_eng_create = client.post(f"/api/production/{DIE}/{CONFIG}/parts", headers=engineer, json=part_payload)
    assert res_eng_create.status_code == 200
    created_part = res_eng_create.json()
    assert created_part["kind"] == "made"
    part_id = created_part["id"]

    # OEM tries to delete part -> 403
    res_oem_del = client.delete(f"/api/production/{DIE}/{CONFIG}/parts/{part_id}", headers=oem_nda)
    assert res_oem_del.status_code == 403

    # Engineer patches part
    patch_res = client.patch(
        f"/api/production/{DIE}/{CONFIG}/parts/{part_id}",
        headers=engineer,
        json={"material": "SUS316"}
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["material"] == "SUS316"


def test_admin_members_management_and_delete(prod_env):
    client = prod_env["client"]
    admin = prod_env["admin"]

    payload = {
        "title": {"en": "Test", "zh": "测试", "src_lang": "en", "status": {"en": "confirmed", "zh": "confirmed"}},
        "order_quantity": 50,
        "members": [],
    }
    client.post(f"/api/admin/production/{DIE}/{CONFIG}", headers=admin, json=payload)

    # Add member
    add_res = client.post(
        f"/api/admin/production/{DIE}/{CONFIG}/members",
        headers=admin,
        json={"email": ENGINEER, "role": "engineer"}
    )
    assert add_res.status_code == 200

    prod_res = client.get(f"/api/production/{DIE}/{CONFIG}", headers=admin)
    assert any(m["email"] == ENGINEER for m in prod_res.json()["members"])

    # Remove member
    rem_res = client.delete(f"/api/admin/production/{DIE}/{CONFIG}/members/{ENGINEER}", headers=admin)
    assert rem_res.status_code == 200

    prod_res = client.get(f"/api/production/{DIE}/{CONFIG}", headers=admin)
    assert not any(m["email"] == ENGINEER for m in prod_res.json()["members"])

    # Delete production
    del_res = client.delete(f"/api/admin/production/{DIE}/{CONFIG}", headers=admin)
    assert del_res.status_code == 200

    # Getting it now is 404
    assert client.get(f"/api/production/{DIE}/{CONFIG}", headers=admin).status_code == 404
