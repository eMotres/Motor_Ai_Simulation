"""Archive a die in a workspace or published layer (owner 2026-11-17: archive what is not needed in the Motors catalog; we
never delete).  ``motor_ai_sim.die_archive`` + ``/api/die-archive``.

What is checked: admin only; the name is repeated to confirm; nothing on disk changes; the die leaves the catalog tree and
the admin picker but every read path that names it keeps working; the archive is per workspace owner; a shared die keeps
its Retire; refusals while the die is in use; audited both ways; restore brings it back; a die rename carries the archive.
Temporary layers and stores, neutral names, no solver."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from motor_ai_sim import die_archive as DA
from motor_ai_sim.api import app

_ROOT = Path(__file__).resolve().parents[1]
_REAL_USERS = _ROOT / "config" / "users.json"

OLD = "OLDDIE 12_40"            # the owner's old workspace copy with one junk configuration
SHARED = "NEWDIE 12_40"
CFG = "L10"
ADMIN = "admin@example.com"
A = "alice@example.com"

client = TestClient(app)


def _die_doc(name):
    return yaml.safe_dump({"name": name, "locked": False, "created": "2026-11-17T10:00:00",
                           "geometry": {"num_slots": 12, "num_poles": 10, "stator_diameter": 12.0, "magnet_height": 1.0,
                                        "motor_length": 40.0}}, sort_keys=False)


def _cfg_doc(die):
    return yaml.safe_dump({"name": CFG, "die": die, "role": "motor", "geometry_overrides": {"motor_length": 10.0},
                           "winding": {"connection": "star"}, "materials": {"magnet": "N42SH", "stator_core": "20SW1200"},
                           "duties": []}, sort_keys=False)


def _put_die(dies: Path, name: str, cfgs=(CFG,)):
    (dies / name).mkdir(parents=True)
    (dies / name / "die.yaml").write_text(_die_doc(name), encoding="utf-8")
    for c in cfgs:
        (dies / name / f"{c}.yaml").write_text(_cfg_doc(name), encoding="utf-8")


def _hash_tree(root: Path) -> dict:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from motor_ai_sim import auth
    from motor_ai_sim import users as U
    from motor_ai_sim import workspace as W
    from motor_ai_sim.routes import family as fam

    srv = tmp_path / "srv"
    shared, published, works = srv / "shared", srv / "published", srv / "workspaces"
    for d in (shared / "dies", published, works):
        d.mkdir(parents=True)
    _put_die(shared / "dies", SHARED)
    shutil.copy2(_ROOT / "config" / "motor_config.yaml", shared / "motor_config.yaml")
    monkeypatch.setenv("WORKSPACES_ROOT", str(works))
    monkeypatch.setenv("SHARED_ROOT", str(shared))
    monkeypatch.setenv("PUBLISHED_ROOT", str(published))
    monkeypatch.setenv("MOTRES_OBJECT_REGISTRY_ROOT", str(tmp_path / "registry"))
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("MOTRES_PRODUCTION_ROOT", str(tmp_path / "production"))
    monkeypatch.setenv("MOTRES_JOBS_STATUS", str(tmp_path / "jobs_status.json"))
    monkeypatch.setenv("ADMIN_AUDIT_FILE", str(tmp_path / "audit.jsonl"))
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
    ids = {e: W.workspace_id(e) for e in (ADMIN, A)}
    _put_die(works / ids[ADMIN] / "dies", OLD)
    _put_die(works / ids[A] / "dies", OLD)                 # the same name in another workspace: never touched
    monkeypatch.setattr("motor_ai_sim.config.DEFAULT_CONFIG_PATH", str(tmp_path / "identity" / "motor_config.yaml"))
    (tmp_path / "identity").mkdir(exist_ok=True)
    yield {"srv": srv, "tmp": tmp_path, "ids": ids,
           "admin": {"Authorization": f"Bearer {U.issue_token(ADMIN)}"},
           "a": {"Authorization": f"Bearer {U.issue_token(A)}"}}
    fam._TREE_CACHE.clear()


def _tree(headers) -> set:
    r = client.get("/api/family/tree", headers=headers)
    assert r.status_code == 200, r.text
    return {d["name"] for d in r.json()["dies"]}


def _audit(env) -> list:
    p = env["tmp"] / "audit.jsonl"
    return [json.loads(l)["action"] for l in p.read_text().splitlines()] if p.exists() else []


def _archive(env, die=OLD, who="admin", confirm=None):
    return client.post(f"/api/die-archive/{die}/archive", headers=env[who], json={"confirm": die if confirm is None else confirm,
                                                                                  "reason": "old copy"})


def test_an_admin_archives_a_workspace_die_and_restores_it_nothing_on_disk_changes(env):
    assert OLD in _tree(env["admin"]) and OLD in _tree(env["a"])               # (a first request seeds each workspace)
    before = _hash_tree(env["srv"])
    assert _archive(env, confirm="something else").status_code == 422          # the name is repeated to confirm
    r = _archive(env)
    assert r.status_code == 200, r.text
    assert r.json()["archived"] is True and r.json()["configs"] == [CFG] and r.json()["reason"] == "old copy"
    assert _hash_tree(env["srv"]) == before                                     # nothing moved, renamed or deleted
    assert OLD not in _tree(env["admin"])                                       # it left the Motors catalog ...
    motors = client.get("/api/admin/motors", headers=env["admin"])
    assert motors.status_code == 200 and OLD not in json.dumps(motors.json())   # the admin motor picker too
    assert OLD in _tree(env["a"])                                               # ... of this workspace only
    listed = client.get("/api/die-archive", headers=env["admin"]).json()["dies"]
    assert [d["die"] for d in listed] == [OLD] and listed[0]["layer"] == "workspace"
    assert client.get(f"/api/family/payload/{OLD}/{CFG}", headers=env["admin"]).status_code == 200  # read paths still find it
    assert client.get(f"/api/die-archive/{OLD}", headers=env["admin"]).json()["archived"] is True

    r = client.post(f"/api/die-archive/{OLD}/restore", headers=env["admin"])
    assert r.status_code == 200 and r.json()["archived"] is False
    assert OLD in _tree(env["admin"])
    assert client.post(f"/api/die-archive/{OLD}/restore", headers=env["admin"]).status_code == 404
    assert _audit(env) == ["die.archive", "die.unarchive"]
    assert _hash_tree(env["srv"]) == before


def test_only_a_verified_admin_and_a_shared_die_keeps_its_retire(env):
    assert _archive(env, who="a").status_code == 403
    assert client.get("/api/die-archive", headers=env["a"]).status_code == 403
    r = _archive(env, die=SHARED)
    assert r.status_code == 409 and r.json().get("code") == "die.retire_required", r.text
    assert client.get(f"/api/die-archive/{SHARED}", headers=env["admin"]).json()["archivable"] is False
    assert _archive(env, die="NO SUCH DIE").status_code == 404
    assert _audit(env) == []


def test_a_die_in_use_is_refused(env):
    from motor_ai_sim.development import store
    store.create_project({"id": "p1", "title": "Pump", "status": "design", "customer_org": "Org", "created_at": "t",
                          "updated_at": "t", "notes": "", "members": [], "pending_invites": [],
                          "linked_motors": [{"die": OLD, "config": CFG, "status": "current"}]})
    r = _archive(env)
    assert r.status_code == 409 and r.json().get("code") == "die.in_use", r.text
    assert "an open project links it (Pump)" in r.json()["detail"] and OLD in _tree(env["admin"])
    check = client.get(f"/api/die-archive/{OLD}", headers=env["admin"]).json()
    assert check["refusals"] == [{"code": "project_link", "names": ["Pump"]}]
    (env["tmp"] / "jobs_status.json").write_text(json.dumps({"jobs": [{"tag": "t1", "state": "running", "die": OLD}]}))
    codes = [x["code"] for x in client.get(f"/api/die-archive/{OLD}", headers=env["admin"]).json()["refusals"]]
    assert codes == ["running_job", "project_link"]
    assert _audit(env) == []


def test_refusals_name_what_holds_the_die():
    proj = lambda status, link: {"title": "P", "status": status, "linked_motors": [{"die": "D", "config": "C", "status": link}]}  # noqa: E731
    clear = dict(projects=[], productions=[], jobs=[], revisions=[])
    assert DA.refusals("D", **clear) == []
    assert DA.refusals("D", **{**clear, "projects": [proj("closed", "current"), proj("design", "trial")]}) == []
    assert DA.refusals("D", **{**clear, "projects": [proj("design", "current")]})[0]["code"] == "project_link"
    assert DA.refusals("D", **{**clear, "productions": [{"die": "D", "config": "C", "status": "archived"}]}) == []
    assert DA.refusals("D", **{**clear, "productions": [{"die": "D", "config": "C", "status": "paused"}]}) == [
        {"code": "open_production", "names": ["D/C"]}]
    assert DA.refusals("D", **{**clear, "jobs": [{"die": "D", "where": "job x"}, {"die": "E", "where": "job y"}]}) == [
        {"code": "running_job", "names": ["job x"]}]
    assert DA.refusals("D", **{**clear, "revisions": ["C (Rev A)"]}) == [{"code": "product_revision", "names": ["C (Rev A)"]}]


def test_the_overview_lists_archived_and_retired_dies(env):
    from motor_ai_sim import workspace as W
    from motor_ai_sim.routes import overview as OV
    DA.archive(env["ids"][ADMIN], OLD, layer="workspace", by=ADMIN, configs=[CFG])
    W.add_tombstone(SHARED, ADMIN)
    rows = OV.archived_dies(env["ids"][ADMIN])
    assert [(r["die"], r["layer"], r["retired"]) for r in rows] == [(SHARED, "shared", True), (OLD, "workspace", False)]
    assert [r["die"] for r in OV.archived_dies(env["ids"][A])] == [SHARED]                     # another workspace: its own list


def test_a_die_rename_carries_the_archive():
    from motor_ai_sim import die_rename as DRN
    assert DRN.step_die_archive in DRN.GLOBAL_STEPS
    doc = {"dies": {"w1/OLD": {"layer": "workspace"}, "w2/OLD": {"layer": "workspace"}, "w1/OTHER": {}},
           "history": [{"key": "w1/OLD"}, {"key": "w1/OTHER"}]}
    assert DA.rename_die_in(doc, "OLD", "NEW") == 3
    assert sorted(doc["dies"]) == ["w1/NEW", "w1/OTHER", "w2/NEW"] and doc["history"][0]["key"] == "w1/NEW"
    with pytest.raises(ValueError):
        DA.rename_die_in({"dies": {"w1/A": {}, "w1/B": {}}}, "A", "B")
