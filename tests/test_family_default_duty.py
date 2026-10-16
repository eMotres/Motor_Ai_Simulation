"""A configuration's DEFAULT mode (owner 2026-10-13).

The Motors catalog shows one row per die; the numbers in it and the mode that
a configuration button loads are those of the configuration's default duty.
``default_duty`` in the configuration yaml names it (default ``rated``); a name
that points at nothing falls back instead of failing.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from motor_ai_sim.api import app
from motor_ai_sim.routes.family import config_default_duty

_ROOT = Path(__file__).resolve().parents[1]
_REAL_USERS = _ROOT / "config" / "users.json"
DIE, CFG, ADMIN = "TESTDIE DEFDUTY", "L12", "admin@example.com"
client = TestClient(app)


def _d(*names):
    return {"duties": [{"name": n, "mode": "motor"} for n in names]}


def test_default_is_rated_when_no_flag():
    assert config_default_duty(_d("peak", "rated")) == "rated"


def test_flag_wins_when_the_duty_exists():
    assert config_default_duty({**_d("peak", "rated"), "default_duty": "peak"}) == "peak"


def test_dangling_flag_falls_back_to_rated_then_prefix_then_first():
    assert config_default_duty({**_d("peak", "rated"), "default_duty": "gone"}) == "rated"
    assert config_default_duty(_d("peak", "rated 1x9 mm")) == "rated 1x9 mm"
    assert config_default_duty(_d("peak", "generator")) == "peak"


def test_no_duties_means_none():
    assert config_default_duty({}) is None
    assert config_default_duty({"duties": []}) is None


@pytest.fixture()
def dies(tmp_path, monkeypatch):
    from motor_ai_sim.routes import family as fam
    root = tmp_path / "dies"
    (root / DIE).mkdir(parents=True)
    (root / DIE / "die.yaml").write_text(yaml.safe_dump({
        "name": DIE, "locked": False, "created": "2026-10-13T10:00:00",
        "geometry": {"num_slots": 12, "num_poles": 10, "stator_diameter": 40.0,
                     "magnet_height": 3.0, "motor_length": 40.0},
    }, sort_keys=False), encoding="utf-8")
    (root / DIE / f"{CFG}.yaml").write_text(yaml.safe_dump({
        "name": CFG, "die": DIE, "role": "motor",
        "geometry_overrides": {"motor_length": 12.0, "wire_height": 1.0},
        "winding": {"connection": "star"},
        "materials": {"magnet": "N42SH", "stator_core": "20SW1200"},
        "duties": [{"name": "rated", "mode": "motor", "current_arms": 40.0, "rpm": 6000.0},
                   {"name": "peak", "mode": "motor", "current_arms": 80.0, "rpm": 6000.0}],
    }, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(fam, "_DIES_DIR", root)
    return root


@pytest.fixture()
def granted(tmp_path, monkeypatch, dies):
    import shutil
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
    return {"Authorization": f"Bearer {U.issue_token(ADMIN)}"}


def _tree_cfg(h):
    r = client.get("/api/family/tree", headers=h)
    assert r.status_code == 200, r.text
    die = next(d for d in r.json()["dies"] if d["name"] == DIE)
    return next(c for c in die["configs"] if c["name"] == CFG)


def test_tree_carries_the_default_and_patch_changes_it(dies, granted):
    assert _tree_cfg(granted)["default_duty"] == "rated"
    r = client.patch(f"/api/family/config/{DIE}/{CFG}/default_duty",
                     headers=granted, json={"duty": "peak"})
    assert r.status_code == 200, r.text
    assert _tree_cfg(granted)["default_duty"] == "peak"
    assert yaml.safe_load((dies / DIE / f"{CFG}.yaml").read_text("utf-8"))["default_duty"] == "peak"


def test_patch_refuses_an_unknown_duty_and_rename_follows(dies, granted):
    r = client.patch(f"/api/family/config/{DIE}/{CFG}/default_duty",
                     headers=granted, json={"duty": "nope"})
    assert r.status_code == 404
    client.patch(f"/api/family/config/{DIE}/{CFG}/default_duty",
                 headers=granted, json={"duty": "peak"})
    r = client.patch(f"/api/family/duty/{DIE}/{CFG}/peak", headers=granted,
                     json={"name": "boost"})
    assert r.status_code == 200, r.text
    assert _tree_cfg(granted)["default_duty"] == "boost"
