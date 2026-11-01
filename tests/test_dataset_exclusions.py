"""Dataset exclusion list (platform_agreement.dataset_exclusions, `python -m motor_ai_sim.dataset_exclusions`,
GET /api/admin/dataset/exclusions): everything that belongs to a client whose newest acceptance is PRIVATE.

Synthetic data only: a throwaway users file, legal / directory / projects / production stores.  Nothing is mailed, no solver.
"""
from __future__ import annotations

import json
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from motor_ai_sim import platform_agreement as PA
from motor_ai_sim.api import app
from motor_ai_sim.development import store
from motor_ai_sim.production import store as PRS
from motor_ai_sim.workspace import workspace_id

ADMIN, ENG, OWN = "admin@example.com", "eng@example.com", "vadim@motresres.com"
ANN, BOB, LONE, FREE1, GUEST = ("ann@acme-drones.example", "bob@acme-drones.example", "solo@solo-works.example",
                                "fred@free-co.example", "gus@gmail.example")
EMPTY = {"companies": [], "emails": [], "workspace_ids": [], "project_ids": [], "motors": [], "productions": [], "acceptance_ids": []}

client = TestClient(app)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from motor_ai_sim import auth
    from motor_ai_sim import users as U
    from motor_ai_sim.directory import service as D

    legal = tmp_path / "legal"
    legal.mkdir()
    monkeypatch.setenv("MOTRES_LEGAL_ROOT", str(legal))
    monkeypatch.setenv("MOTRES_DIRECTORY_ROOT", str(tmp_path / "directory"))
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("MOTRES_PRODUCTION_ROOT", str(tmp_path / "production"))
    monkeypatch.setenv("ADMIN_AUDIT_FILE", str(tmp_path / "admin_audit.jsonl"))
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.setattr(U, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)
    PA._CACHE.update(sig=None, rows=[])
    U.create_user(ADMIN, "password-admin", role="admin", name="Admin")
    U.create_user(ENG, "password-eng", role="engineer", name="Eng")
    for e in (ANN, BOB, LONE, FREE1, GUEST, OWN):
        U.create_user(e, "password-" + e.split("@")[0], role="user", name=e.split("@")[0])

    def company(name, reg, emails):
        comp = D.create_company(dict(legal_name=name, reg_no=reg, country="DE", address="Street 1"))
        for e in emails:
            person = D.create_person(dict(name=e.split("@")[0], emails=[e]))
            D.create_affiliation(comp["id"], person["id"], status="confirmed")
        return comp

    acme = company("Acme Drones Ltd", "HRB 1", [ANN, BOB])
    freeco = company("Free Co", "HRB 2", [FREE1])
    h = lambda e: {"Authorization": f"Bearer {U.issue_token(e)}"}
    return dict(legal=legal, acme=acme, freeco=freeco, admin=h(ADMIN), user=h(ANN), tmp=tmp_path)


_TS = [1000.0]


def accept(env, key, mode, ts=None):
    _TS[0] += 10
    rec = dict(id="a%08d-%012x" % (len(key), int(_TS[0])), ts=ts if ts is not None else _TS[0], key=key, mode=mode, version="v1")
    with open(env["legal"] / "platform_agreement_acceptances.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")
    PA._CACHE["sig"] = None


def project(pid, members, motors=(), history=()):
    store.create_project({"id": pid, "title": pid, "status": "spec", "members": [{"email": e, "role": r} for e, r in members],
                          "linked_motors": [{"die": d, "config": c, "status": "current"} for d, c in motors],
                          "motor_history": [{"die": d, "config": c, "linked_at": None} for d, c in history]})


def production(die, config, members=()):
    PRS.save_file(die, config, "production.json", {"die": die, "config": config, "members": [{"email": e, "role": r} for e, r in members]})


def build_world(env):
    project("acme-1", [(ANN, "owner"), (BOB, "customer")], motors=[("CIANO28 85", "A1")], history=[("CIANO28 85", "OLD")])
    project("acme-by-staff", [(ENG, "owner"), (BOB, "customer")], motors=[("DIE-X", "cfg")])
    project("free-1", [(FREE1, "owner")], motors=[("FREE DIE", "f")])
    project("mixed", [(FREE1, "owner"), (BOB, "customer")], motors=[("MIXED DIE", "m")])   # FREE owner decides: not excluded
    project("motres-only", [(ENG, "owner"), (OWN, "engineer")], motors=[("OWN DIE", "o")])
    production("CIANO28 85", "A1", [(ENG, "owner")])
    production("FREE DIE", "f", [(FREE1, "owner")])
    production("LOOSE DIE", "l", [(ANN, "owner")])                                         # a production owned by the client


def test_no_acceptance_means_everything_is_empty(env):
    build_world(env)
    out = PA.dataset_exclusions()
    assert {k: v for k, v in out.items() if k not in ("generated", "training")} == EMPTY and out["generated"].endswith("Z")
    accept(env, "company:" + env["freeco"]["id"], "FREE")                                  # a FREE acceptance excludes nobody either
    assert {k: v for k, v in PA.dataset_exclusions().items() if k not in ("generated", "training")} == EMPTY


def test_private_company_lists_people_workspaces_projects_motors_productions(env):
    build_world(env)
    accept(env, "company:" + env["acme"]["id"], "PRIVATE")
    accept(env, "company:" + env["freeco"]["id"], "FREE")
    out = PA.dataset_exclusions()
    assert out["companies"] == [env["acme"]["id"]]
    assert out["emails"] == sorted([ANN, BOB])
    assert len(out["acceptance_ids"]) == 1                                   # the PRIVATE record of Acme, not the FREE one of Free Co
    assert out["workspace_ids"] == sorted(workspace_id(e) for e in (ANN, BOB))
    # acme-1 (owner Ann) and acme-by-staff (staff owner, non-staff member Bob) -> listed; the FREE owner's mixed project is not
    assert out["project_ids"] == ["acme-1", "acme-by-staff"]
    assert {(m["die"], m["config"]) for m in out["motors"]} == {("CIANO28 85", "A1"), ("CIANO28 85", "OLD"), ("DIE-X", "cfg"),
                                                                  ("LOOSE DIE", "l")}
    assert {(m["die"], m["config"]) for m in out["productions"]} == {("CIANO28 85", "A1"), ("LOOSE DIE", "l")}
    blob = json.dumps({k: v for k, v in out.items() if k != "training"})
    assert ENG not in blob and OWN not in blob and FREE1 not in blob and "FREE DIE" not in blob and "MIXED DIE" not in blob


def test_a_later_free_acceptance_removes_the_company(env):
    build_world(env)
    accept(env, "company:" + env["acme"]["id"], "PRIVATE", ts=2000)
    assert PA.dataset_exclusions()["emails"]
    accept(env, "company:" + env["acme"]["id"], "FREE", ts=3000)
    assert {k: v for k, v in PA.dataset_exclusions().items() if k not in ("generated", "training")} == EMPTY
    accept(env, "company:" + env["acme"]["id"], "PRIVATE", ts=4000)                       # and PRIVATE again brings it back
    assert PA.dataset_exclusions()["companies"] == [env["acme"]["id"]]


def test_staff_and_motres_are_never_listed(env):
    build_world(env)
    for k in ("user:" + ENG, "user:" + ADMIN, "user:" + OWN):
        accept(env, k, "PRIVATE")
    out = PA.dataset_exclusions()
    assert out["emails"] == [] and out["workspace_ids"] == [] and out["project_ids"] == []
    # a staff account that sits in a PRIVATE company is still not listed, but the company's other people are
    from motor_ai_sim.directory import service as D
    p = D.create_person(dict(name="eng", emails=[ENG]))
    D.create_affiliation(env["acme"]["id"], p["id"], status="confirmed")
    accept(env, "company:" + env["acme"]["id"], "PRIVATE")
    assert PA.dataset_exclusions()["emails"] == sorted([ANN, BOB])


def test_lone_private_account_without_a_company(env):
    project("solo-1", [(LONE, "owner")], motors=[("SOLO DIE", "s")])
    project("guest-1", [(ENG, "owner"), (GUEST, "customer")], motors=[("G DIE", "g")])
    accept(env, "user:" + LONE, "PRIVATE")
    out = PA.dataset_exclusions()
    assert out["companies"] == [] and out["emails"] == [LONE] and out["project_ids"] == ["solo-1"]
    assert out["workspace_ids"] == [workspace_id(LONE)] and out["motors"] == [{"die": "SOLO DIE", "config": "s"}]


def test_a_proposed_affiliation_does_not_count(env):
    from motor_ai_sim.directory import service as D
    p = D.create_person(dict(name="ghost", emails=["ghost@acme-drones.example"]))
    D.create_affiliation(env["acme"]["id"], p["id"], status="proposed")
    accept(env, "company:" + env["acme"]["id"], "PRIVATE")
    assert "ghost@acme-drones.example" not in PA.dataset_exclusions()["emails"]


def test_cli_prints_the_json(env, tmp_path):
    build_world(env)
    accept(env, "company:" + env["acme"]["id"], "PRIVATE")
    accept(env, "company:" + env["freeco"]["id"], "FREE")
    import os
    e = dict(os.environ, MOTRES_LEGAL_ROOT=str(env["legal"]), MOTRES_DIRECTORY_ROOT=str(tmp_path / "directory"),
             MOTRES_PROJECTS_ROOT=str(tmp_path / "projects"), MOTRES_PRODUCTION_ROOT=str(tmp_path / "production"))
    r = subprocess.run([sys.executable, "-m", "motor_ai_sim.dataset_exclusions"], capture_output=True, text=True, env=e, timeout=120)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["emails"] == sorted([ANN, BOB]) and out["project_ids"] == ["acme-1", "acme-by-staff"]


def test_admin_route(env):
    build_world(env)
    accept(env, "company:" + env["acme"]["id"], "PRIVATE")
    assert client.get("/api/admin/dataset/exclusions").status_code in (401, 403)
    assert client.get("/api/admin/dataset/exclusions", headers=env["user"]).status_code in (401, 403)
    r = client.get("/api/admin/dataset/exclusions", headers=env["admin"])
    assert r.status_code == 200 and r.json()["companies"] == [env["acme"]["id"]] and set(r.json()) == set(EMPTY) | {"generated", "training"}


def test_owner_without_any_acceptance_falls_back_to_the_members(env):
    """Documented rule: the owner decides only when he has a recorded acceptance; else any non-staff PRIVATE member does."""
    build_world(env)
    accept(env, "company:" + env["acme"]["id"], "PRIVATE")
    assert "mixed" in PA.dataset_exclusions()["project_ids"]                # FREE1's company never accepted anything
    accept(env, "company:" + env["freeco"]["id"], "FREE")
    assert "mixed" not in PA.dataset_exclusions()["project_ids"]
