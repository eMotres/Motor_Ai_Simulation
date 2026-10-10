"""Read-time shared CIANO14 40 default for enabled registered accounts."""
from __future__ import annotations

import json

import pytest

pytest_plugins = ("tests.test_user_load_granted_motor",)

DIE = "CIANO14 40 new"
DEFAULT = {"die": DIE, "config": "L12"}


@pytest.fixture()
def baseline(tmp_path, monkeypatch):
    from motor_ai_sim import users as U
    from motor_ai_sim import workspace as W

    shared = tmp_path / "shared"
    die = shared / "dies" / DIE
    die.mkdir(parents=True)
    (die / "die.yaml").write_text("name: baseline\n", encoding="utf-8")
    (die / "L12.yaml").write_text("name: L12\n", encoding="utf-8")
    monkeypatch.setattr(W, "shared_root", lambda: shared)
    users_file = tmp_path / "users.json"
    records = {
        "ok@example.com": {"role": "user", "disabled": False,
                            "last_motor": {"die": "OLD", "config": "L8"},
                            "motors": {"all": False, "dies": []}},
        "legacy@example.com": {"role": "user"},
        "new@example.com": {"role": "user", "disabled": False},
        "explicit@example.com": {"role": "user", "disabled": False, "motors": {
            "all": False, "dies": ["OWN DIE"],
            "default": {"die": "OWN DIE", "config": "L20"}}},
        "all@example.com": {"role": "user", "disabled": False, "motors": {
            "all": True, "dies": [],
            "default": {"die": "OTHER", "config": "L8"}}},
        "disabled@example.com": {"role": "user", "disabled": True,
                                 "motors": {"all": False, "dies": []}},
        "bad@example.com": "malformed",
    }
    original = json.dumps(records, separators=(",", ":"))
    users_file.write_text(original, encoding="utf-8")
    monkeypatch.setattr(U, "_USERS_FILE", users_file)
    return U, users_file, original


def test_enabled_user_gets_baseline_without_changing_registry_or_remembered_state(baseline):
    U, users_file, original = baseline
    before = json.loads(users_file.read_text(encoding="utf-8"))
    grants = U.get_motor_grants("ok@example.com")
    default = U.get_default_motor("ok@example.com")

    assert grants == {"all": False, "dies": [DIE], "default": DEFAULT}
    assert default == DEFAULT
    grants["dies"].append("PRIVATE 85")
    default["config"] = "L99"
    assert U.get_motor_grants("ok@example.com")["dies"] == [DIE]
    assert U.get_default_motor("ok@example.com") == DEFAULT
    assert U.get_motor_grants("legacy@example.com") == {
        "all": False, "dies": [DIE], "default": DEFAULT}
    assert U.get_motor_grants("new@example.com") == {
        "all": False, "dies": [DIE], "default": DEFAULT}
    assert json.loads(users_file.read_text(encoding="utf-8")) == before
    assert users_file.read_text(encoding="utf-8") == original
    assert before["ok@example.com"]["last_motor"] == {"die": "OLD", "config": "L8"}


def test_existing_explicit_default_and_all_grant_are_preserved(baseline):
    U, _, _ = baseline
    assert U.get_motor_grants("explicit@example.com") == {
        "all": False, "dies": ["CIANO14 40 new", "OWN DIE"],
        "default": {"die": "OWN DIE", "config": "L20"}}
    assert U.get_motor_grants("all@example.com") == {
        "all": True, "dies": [], "default": {"die": "OTHER", "config": "L8"}}


def test_unknown_disabled_and_malformed_accounts_do_not_get_baseline(baseline):
    U, _, _ = baseline
    for email in ("missing@example.com", "disabled@example.com", "bad@example.com"):
        assert U.get_motor_grants(email) == {"all": False, "dies": []}
        assert U.get_default_motor(email) is None


def test_missing_shared_configuration_or_registry_failure_keeps_stored_answer(baseline, monkeypatch):
    U, users_file, _ = baseline
    shared_config = users_file.parent / "shared" / "dies" / DIE / "L12.yaml"
    shared_config.unlink()
    assert U.get_motor_grants("ok@example.com") == {"all": False, "dies": []}
    assert U.get_default_motor("ok@example.com") is None

    shared_config.write_text("name: L12\n", encoding="utf-8")
    shared_die = shared_config.parent / "die.yaml"
    shared_die.unlink()
    assert U.get_motor_grants("ok@example.com") == {"all": False, "dies": []}
    shared_die.write_text("name: baseline\n", encoding="utf-8")
    from motor_ai_sim.sessions import StoreUnavailable
    monkeypatch.setattr(U, "_load", lambda: (_ for _ in ()).throw(StoreUnavailable("offline")))
    assert U.get_motor_grants("ok@example.com") == {"all": False, "dies": []}
    assert U.get_default_motor("ok@example.com") is None


def test_motor_access_consumer_still_denies_unrelated_private_die(baseline, monkeypatch):
    from motor_ai_sim import motor_access as MA

    monkeypatch.setattr(MA, "caller_identity", lambda _auth: {
        "id": "ok@example.com", "is_admin": False})
    access = MA.catalog_access("irrelevant")
    assert MA.may_see_die(access, DIE)
    assert not MA.may_see_die(access, "PRIVATE 85")


def test_authenticated_me_reports_baseline_default(env):
    from tests import test_user_load_granted_motor as load_test

    from motor_ai_sim import workspace as W
    shared = W.shared_root()
    load_test._write_die(shared, DIE, cfgs=("L12",))

    response = load_test.client.get("/api/me", headers=env["user"])
    assert response.status_code == 200, response.text
    assert response.json()["defaultMotor"] == DEFAULT
    assert DIE in env["U"].get_motor_grants(load_test.USER)["dies"]
    tree = load_test.client.get("/api/family/tree", headers=env["user"])
    assert tree.status_code == 200, tree.text
    assert any(row["name"] == DIE for row in tree.json()["dies"])
    payload = load_test.client.get(
        f"/api/family/payload/{DIE}/L12?duty=rated", headers=env["user"])
    assert payload.status_code == 200, payload.text
