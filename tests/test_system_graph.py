"""The System graph (Simulations > System, stage 1): schema, the propeller S1 view against the code, the route + its gate.

No solver is run: the graph is read from the module manifests and from the source of ``propeller_s1.py`` (``ast``).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from motor_ai_sim import auth
from motor_ai_sim import propeller_s1 as S1
from motor_ai_sim import system_graph as SG
from motor_ai_sim import users as U
from motor_ai_sim.api import app

client = TestClient(app)
PKG = Path(S1.__file__).resolve().parent
REPO = PKG.parents[1]


def _check_schema(doc):
    ids = {n["id"] for n in doc["nodes"]}
    assert doc["version"] == SG.VERSION
    for n in doc["nodes"]:
        assert {"id", "kind", "title", "summary", "inputs", "outputs", "source", "status"} <= set(n)
        assert n["status"]["state"] in SG.STATES
    by = {n["id"]: n for n in doc["nodes"]}
    eids = set()
    for e in doc["edges"]:
        assert e["from"]["node"] in ids and e["to"]["node"] in ids, e["id"]
        if e["from"]["port"] is not None:
            assert e["from"]["port"] in {p["port"] for p in by[e["from"]["node"]]["outputs"]}, e["id"]
        if e["to"]["port"] is not None:
            assert e["to"]["port"] in {p["port"] for p in by[e["to"]["node"]]["inputs"]}, e["id"]
        assert e["type"] and e["id"] not in eids
        eids.add(e["id"])
    for lp in doc["loops"]:
        assert set(lp["nodes"]) <= ids and set(lp["edges"]) <= eids, lp["id"]
        assert lp["kind"] in ("fixed_point", "search")
    return by


# -- all modules -----------------------------------------------------------------------------------

def test_modules_view_is_the_registry():
    from motor_ai_sim.modules.bootstrap import default_registry
    doc = SG.build("modules")
    by = _check_schema(doc)
    assert set(by) == {m.name for m in default_registry().manifests()}
    for m in default_registry().manifests():
        assert [p["type"] for p in by[m.name]["inputs"]] == m.inputs
        assert [p["type"] for p in by[m.name]["outputs"]] == m.outputs
    pairs = {(e["from"]["node"], e["to"]["node"]) for e in doc["edges"]}
    assert ("geometry-2d-aerostator", "mesh-skfem") in pairs
    assert ("mesh-skfem", "solver-em-transient") in pairs
    assert ("solver-em-transient", "solver-thermal") in pairs
    assert ("solver-em-transient", "controller-foc") in pairs
    assert by["solver-mechanical"]["roadmap"] is True            # a stub says so


def test_modules_loops_carry_the_code_constants():
    lp = {x["id"]: x for x in SG.build("modules")["loops"]}
    mod, orch = lp["em_thermal_module"], lp["em_thermal_orchestrator"]
    from motor_ai_sim.routes import coupled as C
    from motor_ai_sim.routes import thermal as T
    assert (orch["max_iterations"], orch["tolerance"]) == (C.DEFAULT_MAX_ITER, C.DEFAULT_TOL_K)
    assert mod["tolerance"] == T.COUPLED_TOL_C and mod["max_iterations"] == 12


# -- propeller S1: the graph matches the code ------------------------------------------------------

def _calls(path: str, fn: str) -> set:
    tree = ast.parse((PKG / path).read_text(encoding="utf-8"))
    f = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == fn)
    out = set()
    for n in ast.walk(f):
        if isinstance(n, ast.Call):
            out.add(n.func.id if isinstance(n.func, ast.Name) else getattr(n.func, "attr", ""))
    return out


def test_propeller_s1_nodes_point_at_real_code():
    doc = SG.build("propeller_s1")
    by = _check_schema(doc)
    for n in doc["nodes"]:
        s = n["source"]
        assert s, n["id"]
        path = REPO / s["file"]
        tree = ast.parse(path.read_text(encoding="utf-8"))
        d = next(x for x in ast.walk(tree) if isinstance(x, (ast.FunctionDef, ast.ClassDef)) and x.name == s["function"])
        assert d.lineno <= s["line"] <= d.end_lineno, n["id"]
    lines = (PKG / "propeller_s1.py").read_text(encoding="utf-8").splitlines()
    assert "q_em = op" in lines[by["torque_demand"]["source"]["line"] - 1]
    assert 'viol.append(("winding"' in lines[by["chk_winding"]["source"]["line"] - 1]
    assert 'viol.append(("controller"' in lines[by["chk_controller"]["source"]["line"] - 1]


def test_propeller_s1_chain_is_what_the_code_calls():
    doc = SG.build("propeller_s1")
    by = {n["id"]: n for n in doc["nodes"]}
    fixed = _calls("propeller_s1.py", "_fixed_point")
    for node in ("current", "voltage", "losses", "thermal"):
        assert by[node]["source"]["function"] in fixed, node
    point = _calls("propeller_s1.py", "solve_point")
    for fn in ("model_slipstream", "operating_point", "housing_film", "board_limit_A", "demag_limit_at",
               "_fixed_point", "_mech_W", "controller_loss", "losses", "temperatures"):
        assert fn in point, fn
    assert "solve_point" in _calls("propeller_s1.py", "s1_rating")
    # the air speed is computed once per speed, OUTSIDE the temperature loop
    lp = {x["id"]: x for x in doc["loops"]}
    assert not {"propeller", "housing_film", "slipstream"} & set(lp["thermal_fixed_point"]["nodes"])
    assert set(lp["thermal_fixed_point"]["nodes"]) <= set(lp["speed_search"]["nodes"])


def test_propeller_s1_loop_constants_come_from_the_module():
    doc = SG.build("propeller_s1")
    lp = {x["id"]: x for x in doc["loops"]}["thermal_fixed_point"]
    assert lp["max_iterations"] == S1.FIXED_POINT_MAX_IT
    assert lp["tolerance"] == S1.FIXED_POINT_TOL_K
    assert lp["detail"]["runaway_above_C"] == S1.RUNAWAY_C
    fb = [e for e in doc["edges"] if e["feedback"] and e["from"]["node"] == "thermal"]
    assert {(e["to"]["node"], e["to"]["port"]) for e in fb} == {
        ("current", "T_magnet"), ("voltage", "T_magnet"), ("voltage", "T_winding"), ("losses", "T_winding")}


def test_anchor_that_is_gone_fails_loudly():
    with pytest.raises(LookupError):
        SG.src("propeller_s1.py", "no_such_function")
    with pytest.raises(LookupError):
        SG.src("propeller_s1.py", "solve_point", "text that is not in the function")


# -- drawing hints (context inputs, layout grid, minor / lane wires) ----------------------------------

@pytest.mark.parametrize("view", SG.VIEWS)
def test_hints_are_complete_and_consistent(view):
    doc = SG.build(view)
    ids = {n["id"] for n in doc["nodes"]}
    cells = {}
    for n in doc["nodes"]:
        assert isinstance(n["context"], bool)
        if n["context"]:
            continue
        assert n["layout"] and {"col", "row"} <= set(n["layout"]), f"{n['id']} has no layout hint"
        cell = (n["layout"]["col"], n["layout"]["row"])
        assert cell not in cells, f"{n['id']} and {cells[cell]} share a cell"
        cells[cell] = n["id"]
    for e in doc["edges"]:
        assert isinstance(e["minor"], bool) and e["route"] in ("direct", "lane")
        assert e["from"]["node"] in ids
    ctx = {n["id"] for n in doc["nodes"] if n["context"]}
    assert not any(e["to"]["node"] in ctx and e["from"]["node"] not in ctx for e in doc["edges"])


def test_propeller_s1_chain_row_and_context():
    doc = SG.build("propeller_s1")
    by = {n["id"]: n for n in doc["nodes"]}
    assert {i for i, n in by.items() if n["context"]} == {"conditions", "model", "limits", "drive", "slipstream"}
    chain = ["rpm_search", "propeller", "torque_demand", "current", "voltage", "losses", "thermal", "verdict", "result"]
    assert {by[i]["layout"]["row"] for i in chain} == {0}
    cols = [by[i]["layout"]["col"] for i in chain]
    assert cols == sorted(cols) and len(set(cols)) == len(cols)
    checks = [by[i]["layout"] for i in ("chk_winding", "chk_magnet", "chk_demag", "chk_controller", "chk_battery")]
    assert len({c["col"] for c in checks}) == 1 and checks[0]["col"] < by["verdict"]["layout"]["col"]
    assert [c["row"] for c in checks] == sorted(c["row"] for c in checks)
    lane = [e["id"] for e in doc["edges"] if e["route"] == "lane"]
    assert lane == ["rpm_search.rating->result.rating"]
    assert {e["label"] for e in doc["edges"] if e["minor"]} == {"rpm", "shaft power", "air speed", "converged current"}


# -- status layer ----------------------------------------------------------------------------------

def test_status_reads_the_model_and_never_computes():
    doc = SG.build("propeller_s1", die="CIANO14 40 new", config="L12")
    st = {n["id"]: n["status"] for n in doc["nodes"]}
    assert st["model"]["state"] == "cached"
    assert st["model"]["values"]["slipstream_factor"] == pytest.approx(0.53)
    assert all(v["state"] == "unknown" for k, v in st.items() if k != "model")
    assert doc["status_context"] == {"die": "CIANO14 40 new", "config": "L12"}
    wire = next(e for e in doc["edges"] if e["id"] == "model.calibration->slipstream.calibration")
    assert wire["values"]["slipstream_factor"] == pytest.approx(0.53)
    nomodel = SG.build("propeller_s1", die="no such die", config="x")
    assert {n["id"]: n["status"]["state"] for n in nomodel["nodes"]}["model"] == "unknown"


def test_modules_status_is_unknown_without_records(monkeypatch):
    monkeypatch.setattr(SG, "_job_status", lambda want: {})
    monkeypatch.setattr(SG, "_history_status", lambda want: {})
    doc = SG.build("modules", die="d", config="c", duty="u")
    assert all(n["status"] == {"state": "unknown"} for n in doc["nodes"])


def test_status_maps_jobs_and_history(monkeypatch):
    monkeypatch.setattr(SG, "_job_status", lambda want: {"solver-thermal": {"state": "running", "time_s": 3.0}})
    monkeypatch.setattr(SG, "_history_status", lambda want: {
        "solver-thermal": {"state": "cached"}, "controller-foc": {"state": "cached", "summary": "x"}})
    st = {n["id"]: n["status"] for n in SG.build("modules", die="d")["nodes"]}
    assert st["solver-thermal"]["state"] == "running"          # a job is fresher than a stored result
    assert st["controller-foc"]["state"] == "cached"
    assert st["mesh-skfem"]["state"] == "unknown"


def test_matches_filter():
    assert SG._matches({"die": "A", "x": 1}, {"die": "A", "config": None})
    assert not SG._matches({"die": "B"}, {"die": "A"})
    assert SG._matches({}, {"die": None})


# -- the route + its gate --------------------------------------------------------------------------

def test_route_open_backend_serves_both_views():
    r = client.get("/api/system/graph")
    assert r.status_code == 200 and r.json()["view"] == "modules"
    r = client.get("/api/system/graph", params={"view": "propeller_s1"})
    j = r.json()
    assert r.status_code == 200 and j["view"] == "propeller_s1" and len(j["loops"]) == 2
    assert client.get("/api/system/graph", params={"view": "nope"}).status_code == 400


@pytest.fixture()
def accounts(tmp_path, monkeypatch):
    monkeypatch.setattr(U, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {"owner@example.com"})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", True)
    out = {}
    for who, role in (("owner@example.com", "admin"), ("eng@example.com", "engineer"), ("usr@example.com", "user")):
        U.create_user(who, "password-" + role, role=role, name=role)
        out[role] = {"Authorization": f"Bearer {U.issue_token(who)}"}
    return out


def test_gate_engineer_and_admin_only(accounts):
    url = "/api/system/graph"
    assert client.get(url, headers=accounts["engineer"]).status_code == 200
    assert client.get(url, headers=accounts["admin"]).status_code == 200
    assert client.get(url, headers=accounts["user"]).status_code == 403
    assert client.get(url).status_code == 401
