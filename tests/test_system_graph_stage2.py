"""Tests for System Graph Stage 2 (user-composed graphs, wiring validation, cycles, storage, roles).

Verification requirements from the brief:
- type-checked wiring;
- cycle refusal;
- topological order;
- save/load with name validation;
- per-user isolation;
- the role gate;
- template execution with mocked capabilities (no FEM).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

from motor_ai_sim import auth
from motor_ai_sim import system_graph as SG
from motor_ai_sim import system_graph_edit as SGE
from motor_ai_sim import users as U
from motor_ai_sim import workspace as WS
from motor_ai_sim.api import app
from motor_ai_sim.modules.base import ModuleManifest, StubModule
from motor_ai_sim.modules.kernel import Kernel
from motor_ai_sim.modules.registry import ModuleRegistry

client = TestClient(app)


# ---------------------------------------------------------------------------
# 1. Type-Checked Wiring
# ---------------------------------------------------------------------------

def test_type_checked_wiring_valid():
    nodes = [
        {
            "id": "geo",
            "capability": "geometry.2d",
            "inputs": [{"port": "params", "type": "ParameterSet"}],
            "outputs": [{"port": "geometry", "type": "GeometryIR"}],
        },
        {
            "id": "mesh",
            "capability": "mesh",
            "inputs": [{"port": "geometry", "type": "GeometryIR"}],
            "outputs": [{"port": "mesh", "type": "MeshIR"}],
        },
    ]
    edges = [
        {
            "id": "geo.geometry->mesh.geometry",
            "from": {"node": "geo", "port": "geometry"},
            "to": {"node": "mesh", "port": "geometry"},
            "type": "GeometryIR",
        }
    ]
    # Should validate without error
    errors = SGE.validate_graph_wiring(nodes, edges)
    assert errors == []


def test_type_checked_wiring_mismatch_refused():
    nodes = [
        {
            "id": "geo",
            "capability": "geometry.2d",
            "inputs": [],
            "outputs": [{"port": "geometry", "type": "GeometryIR"}],
        },
        {
            "id": "solver",
            "capability": "solver.em_static",
            "inputs": [{"port": "excitation", "type": "Excitation"}],
            "outputs": [],
        },
    ]
    edges = [
        {
            "id": "geo.geometry->solver.excitation",
            "from": {"node": "geo", "port": "geometry"},
            "to": {"node": "solver", "port": "excitation"},
            "type": "GeometryIR",
        }
    ]
    with pytest.raises(SGE.WiringError) as exc_info:
        SGE.validate_graph_wiring(nodes, edges)
    assert "Type mismatch" in str(exc_info.value)
    assert "GeometryIR" in str(exc_info.value)
    assert "Excitation" in str(exc_info.value)


def test_type_checked_wiring_missing_port():
    nodes = [
        {"id": "a", "inputs": [], "outputs": [{"port": "out1", "type": "TypeA"}]},
        {"id": "b", "inputs": [{"port": "in1", "type": "TypeA"}], "outputs": []},
    ]
    edges = [
        {
            "id": "a.nonexistent->b.in1",
            "from": {"node": "a", "port": "nonexistent"},
            "to": {"node": "b", "port": "in1"},
            "type": "TypeA",
        }
    ]
    with pytest.raises(SGE.WiringError) as exc_info:
        SGE.validate_graph_wiring(nodes, edges)
    assert "has no output port 'nonexistent'" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 2. Cycle Refusal
# ---------------------------------------------------------------------------

def test_cycle_refusal_simple_loop():
    nodes = [
        {"id": "a", "inputs": [{"port": "in", "type": "T"}], "outputs": [{"port": "out", "type": "T"}]},
        {"id": "b", "inputs": [{"port": "in", "type": "T"}], "outputs": [{"port": "out", "type": "T"}]},
    ]
    edges = [
        {"id": "a->b", "from": {"node": "a", "port": "out"}, "to": {"node": "b", "port": "in"}, "type": "T"},
        {"id": "b->a", "from": {"node": "b", "port": "out"}, "to": {"node": "a", "port": "in"}, "type": "T"},
    ]
    with pytest.raises(SGE.CycleRefusalError) as exc_info:
        SGE.topological_sort(nodes, edges)
    assert "Cycle detected" in str(exc_info.value)


def test_cycle_refusal_three_node_cycle():
    nodes = [
        {"id": "a", "inputs": [{"port": "in", "type": "T"}], "outputs": [{"port": "out", "type": "T"}]},
        {"id": "b", "inputs": [{"port": "in", "type": "T"}], "outputs": [{"port": "out", "type": "T"}]},
        {"id": "c", "inputs": [{"port": "in", "type": "T"}], "outputs": [{"port": "out", "type": "T"}]},
    ]
    edges = [
        {"id": "a->b", "from": {"node": "a", "port": "out"}, "to": {"node": "b", "port": "in"}, "type": "T"},
        {"id": "b->c", "from": {"node": "b", "port": "out"}, "to": {"node": "c", "port": "in"}, "type": "T"},
        {"id": "c->a", "from": {"node": "c", "port": "out"}, "to": {"node": "a", "port": "in"}, "type": "T"},
    ]
    with pytest.raises(SGE.CycleRefusalError) as exc_info:
        SGE.topological_sort(nodes, edges)
    assert "Cycle detected" in str(exc_info.value)


def test_declared_feedback_loop_allowed():
    # Feedback loop explicitly declared (e.g. feedback=True on the backward wire)
    nodes = [
        {"id": "losses", "inputs": [{"port": "temp", "type": "T"}], "outputs": [{"port": "loss", "type": "L"}]},
        {"id": "thermal", "inputs": [{"port": "loss", "type": "L"}], "outputs": [{"port": "temp", "type": "T"}]},
    ]
    edges = [
        {"id": "losses->thermal", "from": {"node": "losses", "port": "loss"}, "to": {"node": "thermal", "port": "loss"}, "type": "L", "feedback": False},
        {"id": "thermal->losses", "from": {"node": "thermal", "port": "temp"}, "to": {"node": "losses", "port": "temp"}, "type": "T", "feedback": True},
    ]
    loops = [{"id": "fp", "kind": "fixed_point", "nodes": ["losses", "thermal"], "edges": ["thermal->losses"]}]
    ordered = SGE.topological_sort(nodes, edges, loops)
    assert [n["id"] for n in ordered] == ["losses", "thermal"]


# ---------------------------------------------------------------------------
# 3. Topological Order
# ---------------------------------------------------------------------------

def test_topological_order_linear_chain():
    nodes = [
        {"id": "stage3", "inputs": [{"port": "in", "type": "T3"}], "outputs": []},
        {"id": "stage1", "inputs": [], "outputs": [{"port": "out", "type": "T1"}]},
        {"id": "stage2", "inputs": [{"port": "in", "type": "T1"}], "outputs": [{"port": "out", "type": "T3"}]},
    ]
    edges = [
        {"from": {"node": "stage1", "port": "out"}, "to": {"node": "stage2", "port": "in"}, "type": "T1"},
        {"from": {"node": "stage2", "port": "out"}, "to": {"node": "stage3", "port": "in"}, "type": "T3"},
    ]
    ordered = SGE.topological_sort(nodes, edges)
    assert [n["id"] for n in ordered] == ["stage1", "stage2", "stage3"]


def test_topological_order_branch_and_merge():
    # A -> B, A -> C, B -> D, C -> D
    nodes = [{"id": "D"}, {"id": "C"}, {"id": "B"}, {"id": "A"}]
    edges = [
        {"from": {"node": "A"}, "to": {"node": "B"}},
        {"from": {"node": "A"}, "to": {"node": "C"}},
        {"from": {"node": "B"}, "to": {"node": "D"}},
        {"from": {"node": "C"}, "to": {"node": "D"}},
    ]
    ordered = SGE.topological_sort(nodes, edges)
    ids = [n["id"] for n in ordered]
    assert ids[0] == "A"
    assert ids[-1] == "D"
    assert set(ids[1:3]) == {"B", "C"}


# ---------------------------------------------------------------------------
# 4. Save / Load with Name Validation
# ---------------------------------------------------------------------------

def test_name_validation_rules():
    assert SGE.validate_graph_name("Propeller motor S1") == "Propeller motor S1"
    assert SGE.validate_graph_name("my_custom-graph.v1") == "my_custom-graph.v1"

    invalid_names = [
        "",
        "   ",
        "../escape",
        "..\\escape",
        "foo/bar",
        "foo\\bar",
        ".hidden",
        "bad*name",
        "a" * 81,
    ]
    for bad in invalid_names:
        with pytest.raises(ValueError):
            SGE.validate_graph_name(bad)


def test_save_and_load_graph(tmp_path, monkeypatch):
    monkeypatch.setattr(WS, "root", lambda: tmp_path)
    sample = {
        "title": "My Graph",
        "nodes": [
            {"id": "n1", "outputs": [{"port": "out", "type": "X"}]},
            {"id": "n2", "inputs": [{"port": "in", "type": "X"}]},
        ],
        "edges": [
            {"from": {"node": "n1", "port": "out"}, "to": {"node": "n2", "port": "in"}, "type": "X"}
        ],
    }
    saved = SGE.save_user_graph("test_graph", sample)
    assert saved["name"] == "test_graph"
    assert (tmp_path / "system_graphs" / "test_graph.json").is_file()

    loaded = SGE.load_user_graph("test_graph")
    assert loaded["name"] == "test_graph"
    assert len(loaded["nodes"]) == 2

    # Delete
    assert SGE.delete_user_graph("test_graph") is True
    assert not (tmp_path / "system_graphs" / "test_graph.json").exists()

    with pytest.raises(FileNotFoundError):
        SGE.load_user_graph("test_graph")


def test_template_shipped_and_loadable(tmp_path, monkeypatch):
    monkeypatch.setattr(WS, "root", lambda: tmp_path)
    # Template should load even if no file exists in workspace
    tmpl = SGE.load_user_graph(SGE.TEMPLATE_PROPELLER_S1_NAME)
    assert tmpl["name"] == SGE.TEMPLATE_PROPELLER_S1_NAME
    assert len(tmpl["nodes"]) == 5
    assert len(tmpl["edges"]) == 6

    # Template should appear in list_user_graphs
    graphs = SGE.list_user_graphs()
    names = [g["name"] for g in graphs]
    assert SGE.TEMPLATE_PROPELLER_S1_NAME in names


# ---------------------------------------------------------------------------
# 5. Per-User Isolation
# ---------------------------------------------------------------------------

def test_per_user_isolation(tmp_path, monkeypatch):
    ws_root = tmp_path / "workspaces"
    ws_root.mkdir()
    monkeypatch.setenv("WORKSPACES_ROOT", str(ws_root))

    # User A
    ws_a = WS.workspace_for_identity("user_a@example.com")
    with WS.use_workspace(ws_a):
        graph_a = {
            "title": "A's Private Graph",
            "nodes": [{"id": "n1", "outputs": [{"port": "o", "type": "T"}]}],
            "edges": [],
        }
        SGE.save_user_graph("secret_a", graph_a)
        assert "secret_a" in [g["name"] for g in SGE.list_user_graphs()]
        loaded_a = SGE.load_user_graph("secret_a")
        assert loaded_a["title"] == "A's Private Graph"

    # User B
    ws_b = WS.workspace_for_identity("user_b@example.com")
    with WS.use_workspace(ws_b):
        # User B should NOT see User A's graph
        user_b_graphs = [g["name"] for g in SGE.list_user_graphs()]
        assert "secret_a" not in user_b_graphs

        # User B loading User A's graph fails
        with pytest.raises(FileNotFoundError):
            SGE.load_user_graph("secret_a")

        # User B deleting User A's graph fails
        with pytest.raises(FileNotFoundError):
            SGE.delete_user_graph("secret_a")


# ---------------------------------------------------------------------------
# 6. Role Gate
# ---------------------------------------------------------------------------

@pytest.fixture()
def accounts(tmp_path, monkeypatch):
    monkeypatch.setattr(U, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setenv("AUTH_SECRET", "test-secret-system-graph-2")
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {"admin@example.com"})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", True)
    out = {}
    for who, role in (("admin@example.com", "admin"), ("eng@example.com", "engineer"), ("usr@example.com", "user")):
        U.create_user(who, "password-" + role, role=role, name=role)
        out[role] = {"Authorization": f"Bearer {U.issue_token(who)}"}
    return out


def test_role_gate_endpoints(accounts):
    routes = [
        ("GET", "/api/system/graphs"),
        ("GET", f"/api/system/graphs/{SGE.TEMPLATE_PROPELLER_S1_NAME}"),
        ("PUT", "/api/system/graphs/test_gate", {"nodes": [], "edges": []}),
        ("DELETE", "/api/system/graphs/test_gate"),
        ("POST", "/api/system/run", {"graph": {"nodes": [], "edges": []}}),
    ]

    for method, url, *body in routes:
        data = body[0] if body else None
        # 1. Anonymous -> 401
        res_anon = client.request(method, url, json=data)
        assert res_anon.status_code == 401, f"{method} {url} anon expected 401, got {res_anon.status_code}"

        # 2. User -> 403
        res_user = client.request(method, url, json=data, headers=accounts["user"])
        assert res_user.status_code == 403, f"{method} {url} user expected 403, got {res_user.status_code}"

        # 3. Engineer -> not 401/403
        res_eng = client.request(method, url, json=data, headers=accounts["engineer"])
        assert res_eng.status_code not in (401, 403), f"{method} {url} eng got {res_eng.status_code}"

        # 4. Admin -> not 401/403
        res_admin = client.request(method, url, json=data, headers=accounts["admin"])
        assert res_admin.status_code not in (401, 403), f"{method} {url} admin got {res_admin.status_code}"


def test_api_path_escape_refused(accounts):
    bad_urls = [
        "/api/system/graphs/..%2Fescape",
        "/api/system/graphs/sub%2Fescape",
        "/api/system/graphs/.hidden",
    ]
    for url in bad_urls:
        r = client.get(url, headers=accounts["engineer"])
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# 7. Template Execution with Mocked Capabilities (No FEM)
# ---------------------------------------------------------------------------

class MockModule:
    def __init__(self, name: str, capability: str, ret_val: Dict[str, Any]):
        self._manifest = ModuleManifest(
            name=name,
            capability=capability,
            contracts_version="0.1.0",
            summary="mock for test",
        )
        self._ret = ret_val

    def manifest(self) -> ModuleManifest:
        return self._manifest

    def run(self, payload: Any) -> Any:
        return dict(self._ret, input_upstream=payload.get("upstream"))


def test_template_execution_mocked_study():
    tmpl = SGE.get_propeller_s1_template()

    # Build mock registry for the 5 template capabilities
    reg = ModuleRegistry()
    reg.register(MockModule("mod-prop", "propeller", {"torque": 1.25, "air_speed": 18.0}))
    reg.register(MockModule("mod-passport", "motor.passport", {"current": 42.0}))
    reg.register(MockModule("mod-loss", "losses", {"loss_W": 120.0}))
    reg.register(MockModule("mod-thermal", "thermal", {"temperature": 85.0}))
    reg.register(MockModule("mod-limit", "s1_limit", {"rating": "PASS", "s1_rpm": 6200}))

    kernel = Kernel(registry=reg)
    res = SGE.run_graph_study(tmpl, kernel=kernel)

    assert res["ok"] is True
    assert "time_s" in res
    nodes = {n["id"]: n for n in res["graph"]["nodes"]}

    for nid in ("propeller", "motor_passport", "losses", "thermal", "s1_limit"):
        assert nodes[nid]["status"]["state"] == "done"
        assert "result" in nodes[nid]["status"]
        assert nodes[nid]["status"]["time_s"] >= 0
