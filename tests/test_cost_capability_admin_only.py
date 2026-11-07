"""The kernel's ``cost`` capability is admin-only, like the Cost tab (web accessUi TAB_GATES.cost = 'admin').

Owner 2026-11-02: no price talk with a customer before the quote stage.  ``POST /api/kernel/study`` and ``/run`` are "user"
routes in the tier table (a signed-up engineering user runs his own solves), so the capability itself is refused for a
non-admin in the route - and in the System graph's run, which reaches the same kernel.  The MCP ``simulate`` tool never
reaches the kernel: it accepts em / thermal / coupled only.

Throwaway registry; the only module run is the cost module on given masses (no geometry, no FEM).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from motor_ai_sim import auth
from motor_ai_sim import users as U
from motor_ai_sim.api import app

OWNER = "owner@example.com"
ENG = "eng@example.com"
USR = "usr@example.com"
MASSES = {"masses_kg": {"copper": 0.061, "magnet": 0.030, "steel": 0.050}}

client = TestClient(app)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(U, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.setenv("ADMIN_AUDIT_FILE", str(tmp_path / "admin_audit.jsonl"))
    monkeypatch.delenv("WORKSPACES_ROOT", raising=False)
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {OWNER})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", True)
    U.create_user(OWNER, "password-owner", role="admin", name="Owner")
    U.create_user(ENG, "password-engineer", role="engineer", name="Eve Engineer")
    U.create_user(USR, "password-user", role="user", name="Una User")
    h = lambda e: {"Authorization": f"Bearer {U.issue_token(e)}"}
    return dict(owner=h(OWNER), eng=h(ENG), usr=h(USR))


@pytest.mark.parametrize("who", ["usr", "eng"])
def test_kernel_cost_refused_for_non_admin(env, who):
    for caps in (["cost"], ["geometry.2d", "cost"]):
        r = client.post("/api/kernel/study", headers=env[who], json={"capabilities": caps, "payload": MASSES})
        assert r.status_code == 403, (who, caps, r.text)
        assert r.json()["detail"]["code"] == "admin_only_capability"
        assert "steps" not in r.json()
    r = client.post("/api/kernel/run", headers=env[who], json={"capability": "cost", "payload": MASSES})
    assert r.status_code == 403, r.text


def test_kernel_cost_runs_for_admin(env):
    r = client.post("/api/kernel/study", headers=env["owner"], json={"capabilities": ["cost"], "payload": MASSES})
    assert r.status_code == 200, r.text
    assert r.json()["steps"]["cost"]["ok"] is True
    r = client.post("/api/kernel/run", headers=env["owner"], json={"capability": "cost", "payload": MASSES})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


def test_system_graph_cost_block_refused_for_engineer(env):
    graph = {"nodes": [{"id": "cost", "capability": "cost", "parameters": MASSES}], "edges": []}
    r = client.post("/api/system/graphs/run", headers=env["eng"], json={"graph": graph})
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "admin_only_capability"


def test_mcp_simulate_cannot_reach_the_cost_capability():
    from motor_ai_sim import agent_designs as AD
    assert "cost" not in AD.WHATS
    assert set(AD.WHATS) == {"em", "thermal", "coupled"}
