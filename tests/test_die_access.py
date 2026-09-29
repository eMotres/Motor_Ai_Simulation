"""Die-level access: PRIVATE (default) / PUBLIC / SELECTED clients.

Orthogonal to the per-user grant (test_motor_access_grants.py): a grant says
which dies an ACCOUNT may see; this says whether a DIE, on its own, is open to
every signed-in account or a named few, regardless of grants. One store
(motor_access.die_access.json), admin-only writes, one gate
(motor_access.may_see_die) shared by every route AND the MCP tools.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from motor_ai_sim.api import app

_ROOT = Path(__file__).resolve().parents[1]
_REAL_USERS = _ROOT / "config" / "users.json"
_REAL_DIES = _ROOT / "config" / "dies"

ADMIN = "admin@example.com"
OWNER_CLIENT = "clienta@example.com"   # has the die via its own grant
OTHER_CLIENT = "clientb@example.com"   # no grant at all — sees it only via die access

client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def _real_files_untouched():
    before = _REAL_USERS.read_bytes() if _REAL_USERS.exists() else None
    yield
    after = _REAL_USERS.read_bytes() if _REAL_USERS.exists() else None
    assert before == after, "config/users.json was modified by a test"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from motor_ai_sim import auth
    from motor_ai_sim import motor_access as MA
    from motor_ai_sim import users as U
    from motor_ai_sim.routes import family as fam

    users_file = tmp_path / "users.json"
    dies_dir = tmp_path / "dies"
    da_file = tmp_path / "die_access.json"
    shutil.copy2(_REAL_USERS, users_file)
    shutil.copytree(_REAL_DIES, dies_dir)

    monkeypatch.setattr(U, "_USERS_FILE", users_file)
    monkeypatch.setattr(fam, "_DIES_DIR", dies_dir)
    monkeypatch.setattr(MA, "_DIE_ACCESS_FILE", da_file)
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.delenv("CATALOG_GRANT_ALL_REGISTERED", raising=False)
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)

    U.create_user(ADMIN, "password-admin", tier="admin", name="Admin")
    U.create_user(OWNER_CLIENT, "password-a", tier="free", name="A")
    U.create_user(OTHER_CLIENT, "password-b", tier="free", name="B")

    all_dies = sorted(p.name for p in dies_dir.iterdir() if (p / "die.yaml").is_file())
    assert all_dies, "fixture catalog is empty — nothing to grant"
    die = all_dies[0]
    # OWNER_CLIENT gets the die through the ordinary per-user grant, so the
    # "who may see" tests below have one account that already sees it and one
    # that starts from nothing.
    U.set_motor_grants(OWNER_CLIENT, all_motors=False, dies=[die])
    yield {"die": die, "all_dies": all_dies,
           "admin": {"Authorization": f"Bearer {U.issue_token(ADMIN)}"},
           "owner": {"Authorization": f"Bearer {U.issue_token(OWNER_CLIENT)}"},
           "other": {"Authorization": f"Bearer {U.issue_token(OTHER_CLIENT)}"}}


def _names(resp):
    return sorted(d["name"] for d in resp.json()["dies"])


def _set_access(env, visibility, clients=None, expect=200):
    r = client.put(f"/api/admin/dies/{env['die']}/access",
                   json={"visibility": visibility, "clients": clients or []},
                   headers=env["admin"])
    assert r.status_code == expect, r.text
    return r


# ── default is private ───────────────────────────────────────────────────────

def test_default_is_private_and_invisible_to_others(env):
    r = client.get("/api/family/tree", headers=env["other"])
    assert env["die"] not in _names(r)
    r = client.get(f"/api/admin/dies/{env['die']}/access", headers=env["admin"])
    assert r.json() == {"die": env["die"], "visibility": "private", "clients": []}


# ── public ────────────────────────────────────────────────────────────────────

def test_public_is_visible_to_every_signed_in_account(env):
    _set_access(env, "public")
    r = client.get("/api/family/tree", headers=env["other"])
    assert env["die"] in _names(r)
    # read-only: still 200 to read, but no write access implied
    assert r.json()["can_write"] is False


def test_public_die_is_visible_to_mcp_tools(env):
    from motor_ai_sim.agent_keys import Principal
    from motor_ai_sim.mcp_tools import list_machines
    _set_access(env, "public")
    # OTHER_CLIENT has no grant of their own — only the die's public flag.
    p = Principal(email=OTHER_CLIENT, credential_id="k", scopes=("read",))
    out = list_machines(p)
    names = {m.get("die") for m in out.get("machines", [])}
    assert env["die"] in names


def test_revoking_public_hides_it_again(env):
    _set_access(env, "public")
    assert env["die"] in _names(client.get("/api/family/tree", headers=env["other"]))
    _set_access(env, "private")
    assert env["die"] not in _names(client.get("/api/family/tree", headers=env["other"]))


# ── selected clients ─────────────────────────────────────────────────────────

def test_selected_clients_only(env):
    _set_access(env, "selected", clients=[OTHER_CLIENT])
    assert env["die"] in _names(client.get("/api/family/tree", headers=env["other"]))
    # a THIRD account, not on the list, still sees nothing
    from motor_ai_sim import users as U
    U.create_user("clientc@example.com", "password-c", tier="free", name="C")
    third = {"Authorization": f"Bearer {U.issue_token('clientc@example.com')}"}
    assert env["die"] not in _names(client.get("/api/family/tree", headers=third))


def test_selected_clients_are_read_only(env):
    _set_access(env, "selected", clients=[OTHER_CLIENT])
    r = client.get("/api/family/tree", headers=env["other"])
    assert r.json()["can_write"] is False


# ── admin-only write ──────────────────────────────────────────────────────────

def test_non_admin_cannot_change_die_access(env):
    r = client.put(f"/api/admin/dies/{env['die']}/access",
                   json={"visibility": "public"}, headers=env["other"])
    assert r.status_code == 403
    r = client.put(f"/api/admin/dies/{env['die']}/access",
                   json={"visibility": "public"})
    assert r.status_code in (401, 403)


def test_bad_visibility_is_refused(env):
    _set_access(env, "not-a-real-mode", expect=422)


def test_unknown_die_is_404(env):
    r = client.put("/api/admin/dies/No Such Die/access",
                   json={"visibility": "public"}, headers=env["admin"])
    assert r.status_code == 404
    r = client.get("/api/admin/dies/No Such Die/access", headers=env["admin"])
    assert r.status_code == 404


def test_unknown_client_is_refused(env):
    r = client.put(f"/api/admin/dies/{env['die']}/access",
                   json={"visibility": "selected", "clients": ["nobody@example.com"]},
                   headers=env["admin"])
    assert r.status_code == 422
    assert "nobody@example.com" in r.json()["detail"]


# ── the die list + the existing grant tests still pass ──────────────────────

def test_die_list_carries_visibility_and_used_by(env):
    _set_access(env, "selected", clients=[OTHER_CLIENT])
    r = client.get("/api/admin/dies", headers=env["admin"])
    assert r.status_code == 200
    row = next(d for d in r.json()["dies"] if d["name"] == env["die"])
    assert row["visibility"] == "selected"
    assert row["clients"] == [OTHER_CLIENT]
    assert row["used_by"] == 1     # OWNER_CLIENT holds it as a direct grant
