"""Admin -> Agent activity: cross-account agent drafts (MCP Stage 3).

Moved off the Motors catalog page 2026-09-30 (owner: that page is not the
right place for every AI agent's draft designs and job queue — move it into
Admin). Covers the two new routes in routes/admin.py:

  GET    /api/admin/agent_designs             every account's drafts
  DELETE /api/admin/agent_designs/{design_id}  delete one, any account

admin-only (403 for a plain user, 401 signed-out), lists across workspaces,
delete works and is audited (admin_audit.jsonl).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from motor_ai_sim.api import app

ADMIN = "admin@example.com"
ALICE = "alice@example.com"
BOB = "bob@example.com"

client = TestClient(app)


def _design(design_id: str, owner: str, *, name: str, client_name: str = "Claude",
           credential_kind: str = "oauth") -> dict:
    now = time.time()
    params = {"stack_mm": 14.5, "turns_factor": 1.0, "parallel_paths": 1,
              "connection": "9S", "current_a_rms": 24.4, "speed_rpm": 6000.0,
              "gamma_deg": 0.0, "mode": "motor", "star_delta": "star"}
    return {
        "id": design_id, "version": 1, "owner": owner, "status": "draft", "name": name,
        "created_at": now, "updated_at": now,
        "created_by": {"kind": "agent", "client_name": client_name,
                       "credential_id": "cred-1", "credential_kind": credential_kind},
        "requirements": {"torque_nm": 0.86, "speed_rpm": 6000.0},
        "assumptions": [], "base": {"die": "CIANO14 40", "config": "new / L12",
                                    "duty": "S1", "outer_diameter_mm": 40.0,
                                    "base_active_length_mm": 12.0,
                                    "base_torque_nm": 0.7, "base_speed_rpm": 6000.0,
                                    "base_current_a_rms": 20.0, "slots": 12, "poles": 10,
                                    "magnet": None, "core_steel": None},
        "why": ["nearest existing machine"], "warnings": [], "alternatives": [],
        "params": params, "initial_params": json.loads(json.dumps(params)),
        "estimate": {"torque_nm": 0.86}, "initial_estimate": {"torque_nm": 0.86},
        "internal": {}, "results": {"em": {"what": "em", "torque_nm": 0.86}},
        "runs": [], "edited_by_user": False, "history": [{"at": now, "event": "created"}],
    }


def _write_design(workspaces_root: Path, owner: str, d: dict) -> Path:
    from motor_ai_sim import workspace as WS
    wsid = WS.workspace_id(owner)
    dd = workspaces_root / wsid / "agent_designs" / d["id"]
    dd.mkdir(parents=True, exist_ok=True)
    (dd / "design.json").write_text(json.dumps(d), encoding="utf-8")
    return dd


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from motor_ai_sim import auth
    from motor_ai_sim import users as U
    from motor_ai_sim import admin_audit as AA

    users_file = tmp_path / "users.json"        # missing file = empty registry
    monkeypatch.setattr(U, "_USERS_FILE", users_file)
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://aerostator.test")
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)

    ws_root = tmp_path / "workspaces"
    ws_root.mkdir()
    monkeypatch.setenv("WORKSPACES_ROOT", str(ws_root))
    monkeypatch.delenv("ADMIN_AUDIT_FILE", raising=False)
    audit_path = users_file.parent / "admin_audit.jsonl"
    monkeypatch.setattr(AA, "audit_path", lambda: audit_path)

    U.create_user(ADMIN, "password-admin", role="admin", name="Admin")
    U.create_user(ALICE, "password-a", role="user", name="Alice")
    U.create_user(BOB, "password-b", role="user", name="Bob")

    d_alice = _design("d-aaaaaaaaaaaa", ALICE, name="Alice draft", client_name="Claude")
    d_bob = _design("d-bbbbbbbbbbbb", BOB, name="Bob draft", client_name="ChatGPT",
                    credential_kind="key")
    _write_design(ws_root, ALICE, d_alice)
    _write_design(ws_root, BOB, d_bob)

    yield {
        "ws_root": ws_root, "audit_path": audit_path,
        "alice_id": d_alice["id"], "bob_id": d_bob["id"],
        "admin": {"Authorization": f"Bearer {U.issue_token(ADMIN)}"},
        "alice": {"Authorization": f"Bearer {U.issue_token(ALICE)}"},
    }


# ── admin-only ────────────────────────────────────────────────────────────

def test_list_requires_admin_403_for_a_user(env):
    r = client.get("/api/admin/agent_designs", headers=env["alice"])
    assert r.status_code == 403, r.text


def test_list_requires_sign_in_401_when_anonymous(env):
    r = client.get("/api/admin/agent_designs")
    assert r.status_code == 401, r.text


def test_delete_requires_admin_403_for_a_user(env):
    r = client.delete(f"/api/admin/agent_designs/{env['alice_id']}", headers=env["alice"])
    assert r.status_code == 403, r.text
    # refused — the draft must still be on disk
    from motor_ai_sim import workspace as WS
    wsid = WS.workspace_id(ALICE)
    assert (env["ws_root"] / wsid / "agent_designs" / env["alice_id"] / "design.json").is_file()


# ── lists across workspaces ─────────────────────────────────────────────────

def test_list_spans_every_account(env):
    r = client.get("/api/admin/agent_designs", headers=env["admin"])
    assert r.status_code == 200, r.text
    designs = r.json()["designs"]
    owners = {d["owner"] for d in designs}
    assert owners == {ALICE, BOB}
    assert len(designs) == 2

    alice_row = next(d for d in designs if d["owner"] == ALICE)
    assert alice_row["name"] == "Alice draft"
    assert alice_row["created_by"]["client_name"] == "Claude"
    assert alice_row["created_by"]["credential_kind"] == "oauth"
    assert alice_row["starting_point"]["die"] == "CIANO14 40"
    assert alice_row["parameters"]["speed_rpm"] == 6000.0
    assert alice_row["open_in_configure"].startswith("https://aerostator.test/?tab=configure&design=")

    bob_row = next(d for d in designs if d["owner"] == BOB)
    assert bob_row["created_by"]["client_name"] == "ChatGPT"
    assert bob_row["created_by"]["credential_kind"] == "key"

    # cross-account listing is a personal-data read: audited
    from motor_ai_sim import admin_audit as AA
    events = AA.read(50, action="agent_design.read")
    assert any(e["actor"] == ADMIN for e in events)


def test_list_never_leaks_a_users_own_designs_endpoint(env):
    """The regular per-account route stays owner-scoped — this admin route is
    additive, not a widened version of it."""
    r = client.get("/api/agent_designs", headers=env["alice"])
    assert r.status_code == 200, r.text
    designs = r.json()["designs"]
    assert {d["design_id"] for d in designs} == {env["alice_id"]}


# ── delete ───────────────────────────────────────────────────────────────

def test_delete_works_and_is_audited(env):
    from motor_ai_sim import workspace as WS
    wsid = WS.workspace_id(BOB)
    on_disk = env["ws_root"] / wsid / "agent_designs" / env["bob_id"]
    assert on_disk.is_dir()

    r = client.delete(f"/api/admin/agent_designs/{env['bob_id']}", headers=env["admin"])
    assert r.status_code == 200, r.text
    assert r.json() == {"deleted": env["bob_id"], "owner": BOB}
    assert not on_disk.exists()

    r2 = client.get("/api/admin/agent_designs", headers=env["admin"])
    assert {d["design_id"] for d in r2.json()["designs"]} == {env["alice_id"]}

    from motor_ai_sim import admin_audit as AA
    events = AA.read(50, action="agent_design.delete")
    hit = next((e for e in events if e["target"] == env["bob_id"]), None)
    assert hit is not None, events
    assert hit["actor"] == ADMIN
    assert hit["subject"] == BOB


def test_delete_unknown_id_404(env):
    r = client.delete("/api/admin/agent_designs/d-ffffffffffff", headers=env["admin"])
    assert r.status_code == 404, r.text
