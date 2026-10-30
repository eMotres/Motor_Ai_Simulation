"""GET / PUT /api/geometry/optimizable — the Sweep / Optimize / DOE variable switch (owner 2026-10-30).

"How do I switch a variable for optimisation on and off from the interface?"  The set lives in the workspace
``motor_config.yaml`` as ``sweep_whitelist``; these tests pin the contract the Geometry table and the Optimization
variable list rely on:

  * the answer says plainly when the list is empty (= every parameter allowed);
  * a name that is not a geometry parameter is refused BY NAME, a delta that would leave nothing enabled is refused;
  * a write changes ONE key — comments, other keys and line endings stay; the old file is kept and the change is audited;
  * a run that already started keeps its list (nothing in the run state is touched);
  * engineers / admins only, customers never.

A throwaway config; no FEM.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from motor_ai_sim import optimizable_set as O
from motor_ai_sim.api import app

client = TestClient(app)

_ROOT = Path(__file__).resolve().parents[1]
_SOURCE = _ROOT / "config" / "motor_config.yaml"

START = ["tooth_width", "slot_height", "magnet_height"]


def _with_list(text: str, names, *, nl="\n") -> str:
    """The real config with its sweep_whitelist replaced (and a comment line added so 'untouched' means something)."""
    out = O.replace_key_text(text, list(names))
    out = "# keep me: a hand-written comment\n" + out
    return out.replace("\r\n", "\n").replace("\n", nl)


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """The process config redirected to a copy with a known list."""
    from motor_ai_sim import config as C
    path = tmp_path / "ws" / "motor_config.yaml"
    path.parent.mkdir()
    base = _SOURCE.read_text(encoding="utf-8")
    path.write_text(_with_list(base, START), encoding="utf-8", newline="")
    monkeypatch.setattr(C, "DEFAULT_CONFIG_PATH", path)
    C.clear_config_cache()
    yield path
    C.clear_config_cache()


def _stored(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8")).get("sweep_whitelist")


def _rest(path: Path):
    d = yaml.safe_load(path.read_text(encoding="utf-8"))
    d.pop("sweep_whitelist", None)
    return d


# ── reading ──────────────────────────────────────────────────────────────────

def test_get_lists_the_effective_set_in_schema_order(cfg):
    r = client.get("/api/geometry/optimizable")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["all_allowed"] is False
    assert j["stored"] == START
    assert set(j["optimizable"]) == set(START)
    assert j["always_allowed"] == ["gamma_deg"]
    assert set(START) <= set(j["known"])


def test_an_empty_list_is_said_to_mean_all_allowed(cfg):
    r = client.put("/api/geometry/optimizable", json={"names": []})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["all_allowed"] is True and j["stored"] == []
    assert len(j["optimizable"]) > len(START), "'all allowed' must list every numeric parameter"
    assert _stored(cfg) in ([], None)
    # and the schema route (the Geometry table) agrees: everything is optimizable
    sch = client.get("/api/geometry/schema").json()["parameters"]
    assert all(p["optimizable"] for p in sch)


def test_a_missing_key_reads_the_same_as_an_empty_one(cfg):
    text = cfg.read_text(encoding="utf-8")
    d = yaml.safe_load(text)
    d.pop("sweep_whitelist")
    cfg.write_text(yaml.safe_dump(d, sort_keys=False), encoding="utf-8")
    from motor_ai_sim import config as C
    C.clear_config_cache()
    j = client.get("/api/geometry/optimizable").json()
    assert j["all_allowed"] is True and j["stored"] == []


# ── validation ───────────────────────────────────────────────────────────────

def test_an_unknown_name_is_refused_by_name_and_nothing_is_written(cfg):
    before = cfg.read_bytes()
    r = client.put("/api/geometry/optimizable", json={"names": ["tooth_width", "no_such_knob"]})
    assert r.status_code == 422
    fields = [p["field"] for p in r.json()["detail"]["invalid_parameters"]]
    assert fields == ["no_such_knob"]
    assert cfg.read_bytes() == before
    assert not (cfg.parent / O.BACKUP_DIR).exists()


@pytest.mark.parametrize("body", [{}, {"names": "tooth_width"}, {"names": [3]}, {"names": ["a"], "enable": ["b"]},
                                  {"enable": "tooth_width"}])
def test_a_malformed_body_is_a_422(cfg, body):
    before = cfg.read_bytes()
    assert client.put("/api/geometry/optimizable", json=body).status_code == 422
    assert cfg.read_bytes() == before


def test_a_delta_that_would_leave_nothing_is_refused(cfg):
    before = cfg.read_bytes()
    r = client.put("/api/geometry/optimizable", json={"disable": START})
    assert r.status_code == 422
    assert r.json()["detail"]["invalid_parameters"][0]["kind"] == "would_empty"
    assert cfg.read_bytes() == before


def test_gamma_deg_is_always_allowed_and_never_stored(cfg):
    r = client.put("/api/geometry/optimizable", json={"names": ["tooth_width", "gamma_deg"]})
    assert r.status_code == 200, r.text
    assert _stored(cfg) == ["tooth_width"]
    assert "gamma_deg" in r.json()["always_allowed"]


def test_a_word_valued_parameter_cannot_be_enabled():
    schema = {"a": {"type": "float"}, "mode": {"type": "string"}}
    with pytest.raises(O.OptimizableError) as e:
        O.compute({"names": ["mode"]}, ["a"], schema)
    assert e.value.kind == "not_numeric"
    assert O.universe(schema) == ["a"]


# ── what a change does ───────────────────────────────────────────────────────

def test_enable_and_disable_change_a_few_against_what_is_effective(cfg):
    r = client.put("/api/geometry/optimizable", json={"enable": ["motor_length"], "disable": ["slot_height"]})
    assert r.status_code == 200, r.text
    j = r.json()
    assert set(j["optimizable"]) == {"tooth_width", "magnet_height", "motor_length"}
    assert j["changed"] == {"added": ["motor_length"], "removed": ["slot_height"]}
    assert set(_stored(cfg)) == set(j["optimizable"])
    # the schema route (Geometry table + Optimization variable list) follows at once, no reload
    sch = {p["name"]: p["optimizable"] for p in client.get("/api/geometry/schema").json()["parameters"]}
    assert sch["motor_length"] is True and sch["slot_height"] is False


def test_disabling_one_when_everything_was_allowed_keeps_the_others(cfg):
    client.put("/api/geometry/optimizable", json={"names": []})
    all_names = set(client.get("/api/geometry/optimizable").json()["optimizable"])
    j = client.put("/api/geometry/optimizable", json={"disable": ["tooth_width"]}).json()
    assert j["all_allowed"] is False
    assert set(j["optimizable"]) == all_names - {"tooth_width"}


def test_the_guards_that_start_a_run_see_the_new_set_at_once(cfg):
    from motor_ai_sim.config import get_config
    client.put("/api/geometry/optimizable", json={"names": ["motor_length"]})
    assert list(get_config().get("sweep_whitelist")) == ["motor_length"]
    from motor_ai_sim.optimization.doe import sample_bounds
    assert list(sample_bounds(0.2)) == ["motor_length"]


# ── one key, backed up, audited ──────────────────────────────────────────────

def test_only_that_key_is_written(cfg):
    before_text = cfg.read_text(encoding="utf-8")
    before_rest = _rest(cfg)
    r = client.put("/api/geometry/optimizable", json={"names": ["motor_length", "wire_width"]})
    assert r.status_code == 200, r.text
    after_text = cfg.read_text(encoding="utf-8")
    assert _rest(cfg) == before_rest
    assert after_text.startswith("# keep me: a hand-written comment\n"), "comments survive"
    # everything outside the list block is byte-identical
    assert O.replace_key_text(before_text, ["x"]).replace("sweep_whitelist:\n- x\n", "") \
        == O.replace_key_text(after_text, ["x"]).replace("sweep_whitelist:\n- x\n", "")


def test_crlf_line_endings_are_kept(cfg):
    cfg.write_bytes(_with_list(_SOURCE.read_text(encoding="utf-8"), START, nl="\r\n").encode("utf-8"))
    from motor_ai_sim import config as C
    C.clear_config_cache()
    assert client.put("/api/geometry/optimizable", json={"names": ["motor_length"]}).status_code == 200
    raw = cfg.read_bytes()
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")


def test_the_previous_file_is_kept_and_the_change_is_audited(cfg):
    before = cfg.read_bytes()
    j = client.put("/api/geometry/optimizable", json={"names": ["motor_length"]}).json()
    assert j["backup"]
    kept = Path(j["backup"])
    assert kept.parent == cfg.parent / O.BACKUP_DIR and kept.read_bytes() == before
    from motor_ai_sim.audit import audit_path
    lines = [json.loads(x) for x in (audit_path().parent / O.AUDIT_FILE).read_text(encoding="utf-8").splitlines()]
    last = lines[-1]
    assert last["added"] == ["motor_length"] and sorted(last["removed"]) == sorted(START)
    assert last["actor"] and last["config"] == str(cfg)


def test_an_unchanged_set_writes_nothing(cfg):
    before = cfg.read_bytes()
    j = client.put("/api/geometry/optimizable", json={"names": START}).json()
    assert j["backup"] is None
    assert cfg.read_bytes() == before


def test_a_write_that_would_touch_another_key_is_refused_before_it_lands(cfg, monkeypatch):
    before = cfg.read_bytes()
    real = O.replace_key_text

    def sloppy(text, names):
        return real(text, names).replace("geometry:", "geometry_x:", 1)
    monkeypatch.setattr(O, "replace_key_text", sloppy)
    r = client.put("/api/geometry/optimizable", json={"names": ["motor_length"]})
    assert r.status_code == 500
    assert cfg.read_bytes() == before
    assert not (cfg.parent / O.BACKUP_DIR).exists(), "no backup for a write that never happened"


def test_replace_key_text_handles_the_layouts_a_config_can_have():
    for src in ("a: 1\nsweep_whitelist:\n- x\n- y\nmesh:\n  n: 1\n",
                "a: 1\nsweep_whitelist:\n  - x\n  - y\n\nmesh:\n  n: 1\n",
                "a: 1\nsweep_whitelist: []\nmesh:\n  n: 1\n",
                "a: 1\nsweep_whitelist: [x, y]\nmesh:\n  n: 1\n",
                "a: 1\nmesh:\n  n: 1\n",
                "a: 1\nmesh:\n  n: 1"):
        out = O.replace_key_text(src, ["p", "q"])
        old, new = yaml.safe_load(src), yaml.safe_load(out)
        assert new["sweep_whitelist"] == ["p", "q"]
        assert {k: v for k, v in new.items() if k != "sweep_whitelist"} == \
            {k: v for k, v in old.items() if k != "sweep_whitelist"}


# ── a run that already started ───────────────────────────────────────────────

def test_a_running_sweep_is_not_touched(cfg):
    """The set is read once, when a run starts (scan / descent / auto-optimize / DOE).  A change in between must leave the
    run's own state alone — it keeps the variables it was started with."""
    from motor_ai_sim.routes import optimization as OPT
    started = [{"name": "slot_height", "min": 1.0, "max": 2.0, "mode": "range", "step": 0.1}]
    OPT._scan_state.update({"running": True, "variables": started})
    snapshot = json.dumps(dict(OPT._scan_state), sort_keys=True, default=str)
    try:
        r = client.put("/api/geometry/optimizable", json={"disable": ["slot_height"]})
        assert r.status_code == 200, r.text
        assert json.dumps(dict(OPT._scan_state), sort_keys=True, default=str) == snapshot
        assert OPT._scan_state["variables"] == started
    finally:
        OPT._scan_state.update({"running": False, "variables": []})


# ── who may ──────────────────────────────────────────────────────────────────

@pytest.fixture
def accounts(cfg, tmp_path, monkeypatch):
    from motor_ai_sim import auth
    from motor_ai_sim import users as U
    monkeypatch.setattr(U, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {"boss@example.com"})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", True)
    U.create_user("boss@example.com", "password-boss", role="admin", name="Boss")
    U.create_user("eng@example.com", "password-eng", role="engineer", name="Eng")
    U.create_user("cust@example.com", "password-cust", role="user", name="Cust")
    return {k: {"Authorization": f"Bearer {U.issue_token(e)}"} for k, e in
            (("admin", "boss@example.com"), ("engineer", "eng@example.com"), ("customer", "cust@example.com"))}


def test_customers_never_see_it(accounts):
    for method in ("get", "put"):
        r = getattr(client, method)("/api/geometry/optimizable", headers=accounts["customer"],
                                    **({"json": {"names": []}} if method == "put" else {}))
        assert r.status_code == 403, (method, r.status_code)


def test_anonymous_is_refused(accounts):
    assert client.get("/api/geometry/optimizable").status_code == 401
    assert client.put("/api/geometry/optimizable", json={"names": []}).status_code == 401


def test_an_engineer_reads_and_an_admin_writes_on_the_one_shared_config(accounts):
    got = client.get("/api/geometry/optimizable", headers=accounts["engineer"])
    assert got.status_code == 200 and got.json()["can_edit"] is False, "the UI greys the switches for them"
    assert client.get("/api/geometry/optimizable", headers=accounts["admin"]).json()["can_edit"] is True
    assert client.put("/api/geometry/optimizable", headers=accounts["engineer"],
                      json={"names": ["motor_length"]}).status_code == 403
    assert client.put("/api/geometry/optimizable", headers=accounts["admin"],
                      json={"names": ["motor_length"]}).status_code == 200


def test_with_per_user_workspaces_an_engineer_writes_their_own(accounts, monkeypatch):
    """The engineer's config is theirs once layering is on, so the gate relaxes — exactly like the ledger."""
    from motor_ai_sim import auth
    monkeypatch.setattr(auth, "_layering_on", lambda: True)
    assert auth.required_role("PUT", "/api/geometry/optimizable") == "engineer"
    assert auth.required_role("PUT", "/api/geometry") == "admin", "the neighbouring write stays the owner's"
    assert client.get("/api/geometry/optimizable", headers=accounts["engineer"]).json()["can_edit"] is True
    assert client.put("/api/geometry/optimizable", headers=accounts["engineer"],
                      json={"names": ["motor_length"]}).status_code == 200
    assert client.put("/api/geometry/optimizable", headers=accounts["customer"],
                      json={"names": ["motor_length"]}).status_code == 403
