"""The Motors catalog by die (owner 2026-11-17): every tree row carries the standard designation and the parameters shown
BESIDE it (steel, magnet, magnet segmentation, slot insulation); an incomplete designation never reaches a customer or the
factory (the #720 rule, applied in the route); the product revision is added per request.  Temporary layers, no solver."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from motor_ai_sim import motor_designation as MD
from motor_ai_sim.api import app

from test_motor_designation import LIB, motor

_ROOT = Path(__file__).resolve().parents[1]
_REAL_USERS = _ROOT / "config" / "users.json"
ADMIN, A = "admin@example.com", "alice@example.com"
DIE = "CIAN28 40"
client = TestClient(app)


def test_catalog_fields_name_the_motor_and_put_the_rest_beside_it():
    die, cfg = motor(length=65, rows=17)
    cfg["materials"]["slot_insulation"] = "Al2O3"
    cfg["geometry_overrides"]["magnet_lamination"] = 5.0
    f = MD.catalog_fields("CILN28 vadim", die, cfg, library=LIB)
    assert f["designation"] == "CILN28 200_65 17F4SY" and f["designation_complete"] and f["family"] == "CILN28 200"
    assert f["beside"] == {"steel": "NO20", "magnet": "NDFEB52_150C", "magnet_lamination_mm": 5.0, "slot_insulation": "Al2O3"}
    cfg["geometry_overrides"]["magnet_lamination"] = 0
    assert MD.catalog_fields("CILN28", die, cfg, library=LIB)["beside"]["magnet_lamination_mm"] is None   # one solid piece
    assert isinstance(MD.catalog_fields("X", None, None), dict)                       # never raises


def test_catalog_view_adds_the_revision_and_hides_an_incomplete_name_from_non_staff():
    dies = [{"name": "D", "configs": [
        {"name": "C1", "designation": "CILN28 200_40 24F4SY", "designation_complete": True, "designation_unknown": []},
        {"name": "C2", "designation": "CI?N28 200_40 24F4SY", "designation_complete": False, "designation_unknown": ["cooling"]},
        {"name": "C3"}]}]
    staff = MD.catalog_view(dies, staff=True, revisions={("D", "C1"): "A"})
    assert [c.get("designation") for c in staff[0]["configs"]] == ["CILN28 200_40 24F4SY Rev A", "CI?N28 200_40 24F4SY", None]
    member = MD.catalog_view(dies, staff=False, revisions={})
    assert [c.get("designation") for c in member[0]["configs"]] == ["CILN28 200_40 24F4SY", None, None]
    assert member[0]["configs"][1]["designation_unknown"] == []
    assert dies[0]["configs"][1]["designation"] == "CI?N28 200_40 24F4SY"          # the cached rows are not touched


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from motor_ai_sim import auth
    from motor_ai_sim import users as U
    from motor_ai_sim.routes import family as fam
    shared, published, works = tmp_path / "shared", tmp_path / "published", tmp_path / "workspaces"
    for d in (shared / "dies" / DIE, published, works):
        d.mkdir(parents=True)
    # no radii: the rotor letter is "?", so the designation is incomplete
    (shared / "dies" / DIE / "die.yaml").write_text(yaml.safe_dump({"name": DIE, "geometry": {
        "num_slots": 24, "num_poles": 28, "stator_diameter": 40.0}}), encoding="utf-8")
    (shared / "dies" / DIE / "L12.yaml").write_text(yaml.safe_dump({"name": "L12", "die": DIE, "geometry_overrides": {
        "motor_length": 12, "num_wires_per_slot": 7, "wire_width": 2.0, "wire_height": 0.5},
        "winding": {"connection": "4S"}, "materials": {"stator_core": "20SW1200", "magnet": "N42SH"}, "duties": []}),
        encoding="utf-8")
    shutil.copy2(_ROOT / "config" / "motor_config.yaml", shared / "motor_config.yaml")
    monkeypatch.setenv("WORKSPACES_ROOT", str(works))
    monkeypatch.setenv("SHARED_ROOT", str(shared))
    monkeypatch.setenv("PUBLISHED_ROOT", str(published))
    monkeypatch.delenv("CATALOG_GRANT_ALL_REGISTERED", raising=False)
    monkeypatch.delitem(fam.__dict__, "_DIES_DIR", raising=False)
    fam._TREE_CACHE.clear()
    users_file = tmp_path / "users.json"
    if _REAL_USERS.exists():
        shutil.copy2(_REAL_USERS, users_file)
    monkeypatch.setattr(U, "_USERS_FILE", users_file)
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)
    U.create_user(ADMIN, "password-admin", role="admin", name="Admin")
    U.create_user(A, "password-a", role="user", name="Alice")
    U.set_motor_grants(A, all_motors=True)
    monkeypatch.setattr("motor_ai_sim.config.DEFAULT_CONFIG_PATH", str(tmp_path / "identity" / "motor_config.yaml"))
    (tmp_path / "identity").mkdir(exist_ok=True)
    yield {"admin": {"Authorization": f"Bearer {U.issue_token(ADMIN)}"}, "a": {"Authorization": f"Bearer {U.issue_token(A)}"}}
    fam._TREE_CACHE.clear()


def _row(headers):
    r = client.get("/api/family/tree", headers=headers)
    assert r.status_code == 200, r.text
    die = next(d for d in r.json()["dies"] if d["name"] == DIE)
    return die["configs"][0]


def test_the_tree_names_each_motor_and_a_customer_never_gets_an_incomplete_name(env):
    staff = _row(env["admin"])
    assert staff["designation"] and "?" in staff["designation"] and staff["designation_complete"] is False
    assert "rotor" in staff["designation_unknown"] and staff["beside"]["steel"] == "20SW1200"
    member = _row(env["a"])                                                   # a granted account, the same cached row
    assert member["designation"] is None and member["designation_unknown"] == []
    assert member["beside"]["magnet"] == "N42SH"
