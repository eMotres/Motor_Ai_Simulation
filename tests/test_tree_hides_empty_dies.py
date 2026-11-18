"""The catalog tree does not list a die with no configuration the caller may see (coordinator / owner 2026-11-19: the dies
emptied by moving their configurations to the single die of their size showed as headers with nothing under them).

Pinned: a shared die with no configuration is neither in ``/api/family/tree`` nor in ``/api/family/thumbs``; a shared die
whose only configuration the caller may not see is hidden for that caller and listed for one who may; the caller's OWN
workspace die stays listed while it is empty (they may be filling it); a die with a configuration is listed as before.
Throwaway layered catalog under the pytest tmp area.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from motor_ai_sim.api import app

_ROOT = Path(__file__).resolve().parents[1]
_REAL_USERS = _ROOT / "config" / "users.json"
ADMIN, A = "admin@example.com", "alice@example.com"
EMPTY, FULL, OWN = "EMPTY 40", "FULL 40", "OWNEMPTY 40"
client = TestClient(app)


def _die_doc(name: str) -> str:
    return yaml.safe_dump({"name": name, "locked": True,
                           "geometry": {"stator_diameter": 40.0, "num_seg": 1, "num_slots_per_segment": 12,
                                        "num_poles_per_segment": 14, "motor_length": 10.0}})


def _cfg_doc(die: str) -> str:
    return yaml.safe_dump({"name": "L10", "die": die, "geometry_overrides": {"motor_length": 10.0},
                           "duties": [{"name": "rated", "mode": "motor", "current_arms": 5.0, "rpm": 3000.0,
                                       "gamma_deg": 0.0}]})


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from motor_ai_sim import auth
    from motor_ai_sim import users as U
    from motor_ai_sim import workspace as W
    from motor_ai_sim.routes import family as fam

    shared, published, works = tmp_path / "shared", tmp_path / "published", tmp_path / "workspaces"
    for d in (shared / "dies", published, works):
        d.mkdir(parents=True)
    for name, with_cfg in ((EMPTY, False), (FULL, True)):
        (shared / "dies" / name).mkdir()
        (shared / "dies" / name / "die.yaml").write_text(_die_doc(name), encoding="utf-8")
        if with_cfg:
            (shared / "dies" / name / "L10.yaml").write_text(_cfg_doc(name), encoding="utf-8")
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
    monkeypatch.setattr("motor_ai_sim.config.DEFAULT_CONFIG_PATH", str(tmp_path / "identity" / "motor_config.yaml"))
    (tmp_path / "identity").mkdir(exist_ok=True)
    U.create_user(ADMIN, "password-admin", role="admin", name="Admin")
    U.create_user(A, "password-a", role="user", name="Alice")
    U.set_motor_grants(A, all_motors=True)
    own = works / W.workspace_id(A) / "dies" / OWN
    own.mkdir(parents=True)
    (own / "die.yaml").write_text(_die_doc(OWN), encoding="utf-8")
    yield {"shared": shared, "admin": {"Authorization": f"Bearer {U.issue_token(ADMIN)}"},
           "a": {"Authorization": f"Bearer {U.issue_token(A)}"}}
    fam._TREE_CACHE.clear()


def _names(headers) -> set:
    r = client.get("/api/family/tree", headers=headers)
    assert r.status_code == 200, r.text
    return {d["name"] for d in r.json()["dies"]}


def _thumbs(headers) -> set:
    r = client.get("/api/family/thumbs", headers=headers)
    assert r.status_code == 200, r.text
    return set(r.json()["thumbs"])


def test_an_empty_shared_die_is_not_listed_for_anyone(env):
    for who in ("admin", "a"):
        names = _names(env[who])
        assert FULL in names and EMPTY not in names
        assert EMPTY not in _thumbs(env[who])


def test_the_callers_own_empty_workspace_die_stays_listed(env):
    assert OWN in _names(env["a"])
    assert OWN not in _names(env["admin"])        # someone else's workspace, as before


def test_a_die_appears_again_once_a_configuration_is_in_it(env):
    from motor_ai_sim.routes import family as fam
    (env["shared"] / "dies" / EMPTY / "L10.yaml").write_text(_cfg_doc(EMPTY), encoding="utf-8")
    fam._TREE_CACHE.clear()
    assert EMPTY in _names(env["admin"]) and EMPTY in _names(env["a"])


def test_a_die_whose_only_configuration_is_private_is_hidden_from_who_may_not_see_it(env, monkeypatch):
    from motor_ai_sim.routes import family as fam
    real = fam.may_see_config_file

    def gate(acc, die, cfg, path, **kw):
        if die == FULL and acc.get("email") == A:
            return False
        return real(acc, die, cfg, path, **kw)
    monkeypatch.setattr(fam, "may_see_config_file", gate)
    fam._TREE_CACHE.clear()
    assert FULL not in _names(env["a"])
    assert FULL in _names(env["admin"])
