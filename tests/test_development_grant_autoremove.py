"""Project motor grants follow the project automatically (owner 2026-10-27).

A grant the project made leaves with the motor (unlink, configuration deleted, die retired, member removed); an
independent grant (admin, another project) never does; the stale ``grant_removal_candidates`` are drained once.
Synthetic data only; the account registry and the project store live in a temp folder.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from starlette.requests import Request

from motor_ai_sim import users
from motor_ai_sim.development import admin_routes, grant_cleanup, motors, routes, store

ADA, BOB, CAL, STAFF = "ada@example.test", "bob@example.test", "cal@example.test", "staff@example.test"
KEY = motors._key("DIE", "A")


def _request():
    return Request({"type": "http", "headers": []})


def _project(project_id="alpha", members=None, linked=None):
    return {"id": project_id, "linked_motors": linked or [], "members": members or [], "status": "spec"}


def _member(email, role="customer"):
    return {"email": email, "role": role}


@pytest.fixture()
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    (tmp_path / "projects").mkdir()
    registry = tmp_path / "users.json"
    registry.write_text(json.dumps({
        ADA: {"role": "user", "name": "Ada"}, BOB: {"role": "user", "name": "Bob"},
        CAL: {"role": "user", "name": "Cal"}, STAFF: {"role": "admin", "name": "Staff"}}), encoding="utf-8")
    monkeypatch.setattr(users, "_USERS_FILE", registry)
    monkeypatch.setattr(motors, "_motor", lambda *args: (Path("."), Path("."), {"is_admin": True}, {}))
    return tmp_path


def _configs(email):
    return list(users.get_stored_motor_grants(email).get("configs") or [])


def _grant_by_admin(email, *keys):
    stored = users.get_stored_motor_grants(email)
    users.set_motor_grants(email, all_motors=False, dies=list(stored.get("dies") or []),
                           configs=sorted(set(stored.get("configs") or []) | set(keys)))


def _audit(project_id, action="motor.grant_autoremove"):
    path = store.path_for(project_id) / "audit.jsonl"
    if not path.is_file():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in rows if r.get("action") == action]


def test_unlink_removes_only_what_the_project_granted(world):
    store.create_project(_project(members=[_member(ADA), _member(BOB)]))
    _grant_by_admin(BOB, "DIE/A", "MANUAL/B")                       # an admin gave Bob this motor first
    _grant_by_admin(ADA, "MANUAL/B")
    motors.link_motor("alpha", "DIE", "A", _request())
    assert _configs(ADA) == ["DIE/A", "MANUAL/B"]
    assert users.get_grant_sources(ADA) == {"DIE/A": ["project:alpha"]}
    assert users.get_grant_sources(BOB) == {}                       # independent: no origin
    assert store.get_project("alpha")["project_motor_grants"][KEY]["emails"] == [ADA]
    motors.unlink_motor("alpha", "DIE", "A", actor="boss@example.test")
    assert _configs(ADA) == ["MANUAL/B"]                            # the project's grant went, the manual one stays
    assert _configs(BOB) == ["DIE/A", "MANUAL/B"]                   # the admin's grant stays
    project = store.get_project("alpha")
    assert not project.get("grant_removal_candidates")
    assert project["project_motor_grants"][KEY]["emails"] == []
    rows = _audit("alpha")
    assert [(r["entity_id"], r["details"]["email"], r["details"]["reason"], r["actor_email"]) for r in rows] == [
        ("DIE/A", ADA, "motor_unlinked", "boss@example.test")]


def test_linking_again_restores_the_grant(world):
    store.create_project(_project(members=[_member(ADA)]))
    motors.link_motor("alpha", "DIE", "A", _request())
    motors.unlink_motor("alpha", "DIE", "A")
    assert _configs(ADA) == []
    motors.link_motor("alpha", "DIE", "A", _request())
    assert _configs(ADA) == ["DIE/A"]
    assert users.get_grant_sources(ADA) == {"DIE/A": ["project:alpha"]}


def test_a_grant_the_admin_reissued_after_the_project_is_kept(world):
    store.create_project(_project(members=[_member(ADA)]))
    motors.link_motor("alpha", "DIE", "A", _request())
    users.set_motor_grants(ADA, all_motors=False, dies=[], configs=[])           # the admin removes ...
    _grant_by_admin(ADA, "DIE/A")                                                # ... and issues it again by hand
    assert users.get_grant_sources(ADA) == {}
    motors.unlink_motor("alpha", "DIE", "A")
    assert _configs(ADA) == ["DIE/A"]
    assert store.get_project("alpha")["grant_removal_candidates"] == [
        {"email": ADA, "die": "DIE", "config": "A", "reason": "motor_unlinked"}]
    assert _audit("alpha") == []


def test_admin_saving_the_grant_list_back_keeps_the_origin(world):
    store.create_project(_project(members=[_member(ADA)]))
    motors.link_motor("alpha", "DIE", "A", _request())
    _grant_by_admin(ADA, "OTHER/C")                                  # an unrelated edit through the admin API
    assert users.get_grant_sources(ADA) == {"DIE/A": ["project:alpha"]}
    users.set_config_grant_holders("OTHER/C", [])                    # and removing another one
    assert users.get_grant_sources(ADA) == {"DIE/A": ["project:alpha"]}


def test_a_grant_shared_with_a_second_project_is_kept(world):
    store.create_project(_project(members=[_member(ADA)]))
    store.create_project(_project("beta", members=[_member(ADA)], linked=[{"die": "DIE", "config": "A", "status": "current"}]))
    motors.link_motor("alpha", "DIE", "A", _request())
    beta = store.get_project("beta")
    motors._sync_grants(beta, KEY, "DIE/A")                          # beta gives Ada the same configuration
    store.mutate_project("beta", lambda doc: doc.update(beta))
    assert users.get_grant_sources(ADA) == {"DIE/A": ["project:alpha", "project:beta"]}
    motors.unlink_motor("alpha", "DIE", "A")
    assert _configs(ADA) == ["DIE/A"]
    assert users.get_grant_sources(ADA) == {"DIE/A": ["project:beta"]}
    assert _audit("alpha") == []
    assert not store.get_project("alpha").get("grant_removal_candidates")
    # when beta lets the motor go too, the grant goes
    motors.unlink_motor("beta", "DIE", "A")
    assert _configs(ADA) == []
    assert len(_audit("beta")) == 1


def test_legacy_grant_still_given_by_another_project_is_kept(world):
    # no origin recorded (stored before origins existed), but beta still links the motor to Ada
    store.create_project(_project("alpha", members=[_member(ADA)]))
    store.create_project(_project("beta", members=[_member(ADA)], linked=[{"die": "DIE", "config": "A", "status": "current"}]))
    _grant_by_admin(ADA, "DIE/A")
    alpha = store.get_project("alpha")
    alpha["linked_motors"] = [{"die": "DIE", "config": "A", "status": "current"}]
    alpha["project_motor_grants"] = {KEY: {"grant_key": "DIE/A", "emails": [ADA]}}
    store.mutate_project("alpha", lambda doc: doc.update(alpha))
    store.save_index({"motors": {KEY: {"project_id": "alpha", "die": "DIE", "config": "A"}}})
    motors.unlink_motor("alpha", "DIE", "A")
    assert _configs(ADA) == ["DIE/A"]
    assert not store.get_project("alpha").get("grant_removal_candidates")


def test_configuration_deleted_takes_the_project_grants_and_keeps_the_link(world):
    store.create_project(_project(members=[_member(ADA), _member(CAL, "owner")]))
    motors.link_motor("alpha", "DIE", "A", _request())
    removed = grant_cleanup.release_for_catalog_removal("DIE", ["A"], reason="config_deleted", actor="boss@example.test")
    assert sorted(r["email"] for r in removed) == [ADA, CAL]
    assert _configs(ADA) == [] and _configs(CAL) == []
    project = store.get_project("alpha")
    assert project["linked_motors"] == [{"die": "DIE", "config": "A", "status": "current"}]
    assert sorted(r["details"]["email"] for r in _audit("alpha")) == [ADA, CAL]
    assert {r["details"]["reason"] for r in _audit("alpha")} == {"config_deleted"}
    # nothing left to do the second time
    assert grant_cleanup.release_for_catalog_removal("DIE", ["A"], reason="config_deleted") == []


def test_family_hooks_release_only_when_the_motor_is_really_gone(world, monkeypatch):
    from motor_ai_sim.routes import family
    store.create_project(_project(members=[_member(ADA)]))
    motors.link_motor("alpha", "DIE", "A", _request())
    monkeypatch.setattr(family, "_motor_still_exists", lambda die, cfg: True)     # a layer behind still holds it
    family._release_project_grants("DIE", ["A"], "config_deleted", {"user": {"email": "boss@example.test"}})
    assert _configs(ADA) == ["DIE/A"]
    monkeypatch.setattr(family, "_motor_still_exists", lambda die, cfg: False)
    family._release_project_grants("DIE", ["A"], "die_retired", {"user": {"email": "boss@example.test"}})
    assert _configs(ADA) == []
    assert _audit("alpha")[0]["details"]["reason"] == "die_retired"
    # the die comes back: the members get the motor again
    assert grant_cleanup.regrant_for_restored_die("DIE", ["A"]) == 1
    assert _configs(ADA) == ["DIE/A"]


def test_member_removed_loses_their_project_grants(world, monkeypatch):
    store.create_project(_project(members=[_member(ADA), _member(BOB)]))
    _grant_by_admin(BOB, "DIE/A")
    motors.link_motor("alpha", "DIE", "A", _request())
    monkeypatch.setattr(routes.access, "require_project",
                        lambda request, project_id, edit=False: ({"email": "boss@example.test", "role": "admin"},
                                                                 store.get_project(project_id), "owner"))
    asyncio.run(routes.remove_member(_request(), "alpha", ADA))
    assert _configs(ADA) == []
    asyncio.run(routes.remove_member(_request(), "alpha", BOB))
    assert _configs(BOB) == ["DIE/A"]                                # Bob's was independent
    project = store.get_project("alpha")
    assert [m["email"] for m in project["members"]] == []
    assert not project.get("grant_removal_candidates")
    rows = _audit("alpha")
    assert [(r["details"]["email"], r["details"]["reason"], r["actor_email"]) for r in rows] == [
        (ADA, "customer_membership_removed", "boss@example.test")]


def test_a_member_who_stops_being_a_granting_role_loses_the_grants(world):
    store.create_project(_project(members=[_member(ADA)]))
    motors.link_motor("alpha", "DIE", "A", _request())
    routes._apply_member("alpha", ADA, "observer")
    assert _configs(ADA) == []
    assert _audit("alpha")[0]["details"]["reason"] == "customer_membership_changed"


def test_staff_accounts_are_never_touched(world):
    store.create_project(_project(members=[_member(STAFF, "engineer")]))
    motors.link_motor("alpha", "DIE", "A", _request())
    assert _configs(STAFF) == ["DIE/A"]
    motors.unlink_motor("alpha", "DIE", "A")
    assert _configs(STAFF) == ["DIE/A"]
    assert _audit("alpha") == []
    assert not store.get_project("alpha").get("grant_removal_candidates")


def _legacy_project(world_candidates, ledger_emails, members):
    """A project as stored before origins existed: ledger + candidates, no origin on the accounts."""
    project = _project(members=members)
    project["motor_history"] = [{"die": "DIE", "config": "A", "linked_at": "2026-10-01", "unlinked_at": "2026-10-02"}]
    project["project_motor_grants"] = {KEY: {"grant_key": "DIE/A", "emails": ledger_emails}}
    project["grant_removal_candidates"] = world_candidates
    return project


def test_drain_of_legacy_candidates_is_idempotent_and_keeps_the_unprovable(world):
    rows = [{"email": ADA, "die": "DIE", "config": "A", "reason": "motor_unlinked"},         # member, in the ledger: removed
            {"email": BOB, "die": "DIE", "config": "A", "reason": "motor_unlinked"},         # origin of another project: stays
            {"email": CAL, "die": "DIE", "config": "A", "reason": "motor_unlinked"},         # not a member any more: stays
            {"email": STAFF, "die": "DIE", "config": "A", "reason": "motor_unlinked"},       # staff: settled, access untouched
            {"email": "gone@example.test", "die": "DIE", "config": "A", "reason": "motor_unlinked"}]  # no such account
    store.create_project(_legacy_project(rows, [ADA, BOB, CAL, STAFF, "gone@example.test"],
                                         [_member(ADA), _member(BOB), _member(STAFF, "engineer")]))
    for email in (ADA, BOB, CAL, STAFF):
        _grant_by_admin(email, "DIE/A")
    users.add_grant_source(BOB, "DIE/A", "project:other")
    first = grant_cleanup.drain_all(actor="boss@example.test")
    assert first["removed"] == 1
    assert _configs(ADA) == []
    assert _configs(BOB) == ["DIE/A"] and _configs(CAL) == ["DIE/A"] and _configs(STAFF) == ["DIE/A"]
    left = store.get_project("alpha")["grant_removal_candidates"]
    assert [(r["email"], r["reason"]) for r in left] == [(BOB, "motor_unlinked"), (CAL, "motor_unlinked")]
    assert [r["details"]["email"] for r in _audit("alpha")] == [ADA]
    snapshot = json.dumps(store.get_project("alpha"), sort_keys=True)
    again = grant_cleanup.drain_all(actor="boss@example.test")                   # idempotent
    assert again["removed"] == 0
    assert json.dumps(store.get_project("alpha"), sort_keys=True) == snapshot
    assert len(_audit("alpha")) == 1


def test_admin_read_drains_before_listing(world):
    rows = [{"email": ADA, "die": "DIE", "config": "A", "reason": "customer_membership_removed"}]
    store.create_project(_legacy_project(rows, [ADA], []))                       # Ada was removed from the project
    _grant_by_admin(ADA, "DIE/A")
    listed = admin_routes.admin_list({"email": "boss@example.test"})["projects"]
    assert listed[0]["grant_removal_candidates"] == []
    assert _configs(ADA) == []
    assert admin_routes.admin_grant_removal_candidates("alpha", {"email": "boss@example.test"}) == {"candidates": []}


def test_candidates_without_a_project_ledger_stay_listed(world):
    rows = [{"email": ADA, "die": "DIE", "config": "A", "reason": "motor_unlinked"}]
    project = _legacy_project(rows, [], [_member(ADA)])
    project["project_motor_grants"] = {}
    store.create_project(project)
    _grant_by_admin(ADA, "DIE/A")
    assert grant_cleanup.drain_all()["removed"] == 0
    assert _configs(ADA) == ["DIE/A"]
    assert store.get_project("alpha")["grant_removal_candidates"] == rows


def test_origins_follow_a_die_rename_and_vanish_with_the_grant(world):
    store.create_project(_project(members=[_member(ADA)]))
    motors.link_motor("alpha", "DIE", "A", _request())
    users.rename_die_in_grants("DIE", "DIE2")
    assert _configs(ADA) == ["DIE2/A"]
    assert users.get_grant_sources(ADA) == {"DIE2/A": ["project:alpha"]}
    users.set_config_grant_holders("DIE2/A", [])
    assert users.get_grant_sources(ADA) == {}
    assert "sources" not in users.get_stored_motor_grants(ADA)
