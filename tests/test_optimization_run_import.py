"""Saved optimisation runs per motor: import (dry run first), list, open, access."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from motor_ai_sim.api import app

_ROOT = Path(__file__).resolve().parents[1]
_REAL_USERS = _ROOT / "config" / "users.json"
DIE, CFG = "TESTDIE 40", "L40"
ADMIN, CLIENT = "admin@example.com", "client@example.com"
client = TestClient(app)


@pytest.fixture()
def dies(tmp_path, monkeypatch):
    from motor_ai_sim.routes import family as fam
    root = tmp_path / "dies"
    (root / DIE).mkdir(parents=True)
    (root / DIE / "die.yaml").write_text(yaml.safe_dump({
        "name": DIE, "locked": False, "created": "2026-09-02T10:00:00",
        "geometry": {"num_slots": 12, "num_poles": 14, "stator_diameter": 40.0,
                     "magnet_height": 3.0, "motor_length": 40.0}}), encoding="utf-8")
    (root / DIE / f"{CFG}.yaml").write_text(yaml.safe_dump({
        "name": CFG, "die": DIE, "role": "motor", "winding": {"connection": "star"},
        "materials": {"magnet": "N42SH"}, "duties": []}), encoding="utf-8")
    monkeypatch.setattr(fam, "_DIES_DIR", root)
    monkeypatch.setattr(fam, "_admin_shared_die_dir", lambda die, **k: root / str(die))
    return root


@pytest.fixture()
def hdr(tmp_path, monkeypatch, dies):
    from motor_ai_sim import auth
    from motor_ai_sim import users as U
    users_file = tmp_path / "users.json"
    if _REAL_USERS.exists():
        shutil.copy2(_REAL_USERS, users_file)
    monkeypatch.setattr(U, "_USERS_FILE", users_file)
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.delenv("CATALOG_GRANT_ALL_REGISTERED", raising=False)
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)
    U.create_user(ADMIN, "password-admin", role="admin", name="Admin")
    U.create_user(CLIENT, "password-client", role="user", name="Client")
    return {"admin": {"Authorization": f"Bearer {U.issue_token(ADMIN)}"},
            "client": {"Authorization": f"Bearer {U.issue_token(CLIENT)}"}}


def _state(run_id="hl-1", **kw):
    s = {"running": False, "iter": 3, "n_evals": 40, "run_id": run_id,
         "variables": [{"name": "magnet_height", "min": 2.0, "max": 4.0}],
         "history": [{"iter": 0, "cost": 1.0}, {"iter": 1, "cost": 0.8}],
         "points": [{"x": [3.0], "cost": 0.8}], "best": {"cost": 0.8, "x": [3.0]},
         "machine_fp": "abc123", "eval_params": {"gap_layers": 1}}
    s.update(kw)
    return s


def _imp(headers, state=None, dry=False, **kw):
    body = {"die": DIE, "config": CFG, "state": state or _state(), **kw}
    return client.post("/api/optimization/runs/import" + ("?dry_run=1" if dry else ""),
                       json=body, headers=headers)


def test_dry_run_writes_nothing_then_import_lists_and_opens(dies, hdr):
    r = _imp(hdr["admin"], dry=True)
    assert r.status_code == 200 and r.json()["written"] is False and r.json()["run_id"] == "hl-1"
    assert not (dies / DIE / "optimization_runs").exists()
    r = _imp(hdr["admin"])
    assert r.status_code == 200 and r.json()["written"] is True
    ls = client.get("/api/optimization/runs", params={"die": DIE, "config": CFG}, headers=hdr["admin"]).json()
    assert [x["run_id"] for x in ls["runs"]] == ["hl-1"] and "state" not in ls["runs"][0]
    assert ls["runs"][0]["gap_layers"] == 1.0 and ls["runs"][0]["variables"] == ["magnet_height"]
    op = client.get("/api/optimization/runs/hl-1", params={"die": DIE, "config": CFG}, headers=hdr["admin"])
    assert op.status_code == 200 and op.json()["state"]["machine_fp"] == "abc123"


def test_second_import_needs_overwrite(dies, hdr):
    assert _imp(hdr["admin"]).status_code == 200
    r = _imp(hdr["admin"])
    assert r.status_code == 409 and r.json()["detail"]["error"] == "run_exists"
    assert _imp(hdr["admin"], overwrite=True).status_code == 200


def test_bad_states_are_refused_not_repaired(dies, hdr):
    assert _imp(hdr["admin"], state=_state(running=True)).status_code == 422
    assert _imp(hdr["admin"], state=_state(history=[], points=[])).status_code == 422
    assert _imp(hdr["admin"], state=_state(variables=[])).status_code == 422
    assert _imp(hdr["admin"], state=_state(run_id="../x")).status_code == 422


def test_ungranted_client_cannot_import_or_read(dies, hdr):
    assert _imp(hdr["admin"]).status_code == 200
    assert _imp(hdr["client"]).status_code in (403, 404)
    r = client.get("/api/optimization/runs", params={"die": DIE, "config": CFG}, headers=hdr["client"])
    assert r.status_code == 404


def test_store_path_is_inside_the_die_folder(tmp_path):
    from motor_ai_sim.optimization import run_store as RS
    assert RS.runs_dir(tmp_path / DIE, CFG) == tmp_path / DIE / "optimization_runs" / CFG
    rec = RS.build_record(_state())
    p = RS.write_run(tmp_path / DIE, CFG, rec)
    assert p.parent.parent.parent == tmp_path / DIE
    shutil.move(str(tmp_path / DIE), str(tmp_path / "RENAMED"))
    assert [r["run_id"] for r in RS.list_runs(tmp_path / "RENAMED", config=CFG)] == ["hl-1"]


def test_list_without_names_uses_the_open_machine(dies, hdr, monkeypatch):
    from motor_ai_sim.routes import optimization as O
    assert _imp(hdr["admin"]).status_code == 200
    monkeypatch.setattr(O, "_machine_stamp", lambda *a, **k: {"die": DIE, "config": CFG})
    r = client.get("/api/optimization/runs", headers=hdr["admin"]).json()
    assert r["die"] == DIE and [x["run_id"] for x in r["runs"]] == ["hl-1"]
    monkeypatch.setattr(O, "_machine_stamp", lambda *a, **k: {})
    assert client.get("/api/optimization/runs", headers=hdr["admin"]).json()["runs"] == []
