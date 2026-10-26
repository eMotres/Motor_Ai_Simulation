"""Product REVISIONS of a motor - Rev A, Rev B ... (owner 2026-10-27, ``motor_ai_sim.motor_revisions``).

Pinned here:
  * the letters (A..Z, AA ...) and their inverse;
  * ``POST /api/motor-revisions/assign`` (Rev A: snapshot, project, one lineage per motor) and
    ``POST /api/motor-revisions/{lineage}/new`` (the next letter from the current one: a configuration copy or a die copy, the reason is
    required, the replaced revision is locked and read-only, never deleted);
  * the guards in the catalogue: delete config / delete die / retire / rename / unlock refuse a motor that holds a revision (409), every
    write to a frozen revision's configuration is refused at the write seam;
  * the auto-link of the new revision into the project of the old one (and the staff mark "recommended" carried along);
  * access: writes are staff only, reads need a signed-in account and see only the motors they may see, reasons are staff only;
  * the production page / list carry the label, a die rename follows.

Synthetic names only.  Runs on a throwaway catalog in two shapes: FLAT (single layer, admin writes) and LAYERED (production).
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from motor_ai_sim import motor_revisions as MR
from motor_ai_sim.api import app

_ROOT = Path(__file__).resolve().parents[1]
_REAL_USERS = _ROOT / "config" / "users.json"
_REAL_DIES = _ROOT / "config" / "dies"

DIE = "REVDIE 40"
CFG = "L40"
OTHER = "L55"          # a configuration that never gets a revision
PRIV = "L41"           # a PRIVATE configuration
DUTY = "rated"

ADMIN = "admin@example.com"
ENG = "eng@example.com"        # engineer: staff
MEMBER = "member@example.com"  # role user: reads
PROJECT = "revproj"

client = TestClient(app)


def _die_doc(name: str) -> str:
    return yaml.safe_dump({
        "name": name, "locked": False, "created": "2026-10-27T10:00:00",
        "geometry": {"num_slots": 12, "num_poles": 14, "stator_diameter": 40.0,
                     "magnet_height": 3.0, "motor_length": 40.0},
    }, sort_keys=False, allow_unicode=True)


def _cfg_doc(die: str, name: str, private: bool = False) -> str:
    doc = {
        "name": name, "die": die, "role": "motor",
        "geometry_overrides": {"motor_length": 40.0, "wire_height": 1.0},
        "winding": {"connection": "star"},
        "materials": {"magnet": "N42SH", "stator_core": "20SW1200"},
        "duties": [{"name": DUTY, "mode": "motor", "saved_at": "2026-10-27T10:00:00",
                    "current_arms": 85.0, "rpm": 6000.0, "gamma_deg": 12.0, "torque_nm": 1.5, "power_kw": 0.9, "note": "",
                    "result": {"efficiency_pct": 93.0, "mass_kg": 0.5}}],
    }
    text = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)
    return ("visibility: private\n" + text) if private else text


def _write_catalog(dies_dir: Path) -> None:
    d = dies_dir / DIE
    d.mkdir(parents=True)
    (d / "die.yaml").write_text(_die_doc(DIE), encoding="utf-8")
    for name, private in ((CFG, False), (OTHER, False), (PRIV, True)):
        (d / f"{name}.yaml").write_text(_cfg_doc(DIE, name, private), encoding="utf-8")


@pytest.fixture(scope="module", autouse=True)
def _real_files_untouched():
    before_users = _REAL_USERS.read_bytes() if _REAL_USERS.exists() else None
    before_dies = {p: p.stat().st_mtime_ns for p in sorted(_REAL_DIES.rglob("*")) if p.is_file()}
    yield
    after_users = _REAL_USERS.read_bytes() if _REAL_USERS.exists() else None
    after_dies = {p: p.stat().st_mtime_ns for p in sorted(_REAL_DIES.rglob("*")) if p.is_file()}
    assert before_users == after_users, "config/users.json was modified by a test"
    assert before_dies == after_dies, "config/dies was modified by a test"


@pytest.fixture(params=["flat", "layered"])
def env(request, tmp_path, monkeypatch):
    from motor_ai_sim import auth
    from motor_ai_sim import motor_access as MA
    from motor_ai_sim import users as U
    from motor_ai_sim.development import motors as dev_motors
    from motor_ai_sim.development import store
    from motor_ai_sim.routes import catalog as cat
    from motor_ai_sim.routes import family as fam
    from motor_ai_sim.routes import presets as pre

    layered = request.param == "layered"
    monkeypatch.setattr(U, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(MA, "_DIE_ACCESS_FILE", tmp_path / "die_access.json")
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.delenv("CATALOG_GRANT_ALL_REGISTERED", raising=False)
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)
    monkeypatch.setenv("MOTRES_MOTOR_REVISIONS_ROOT", str(tmp_path / "revisions"))
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    (tmp_path / "projects").mkdir()
    MA._CFG_VIS_CACHE.clear()
    fam._TREE_CACHE.clear()

    if layered:
        tree = tmp_path / "srv"
        shared, published, works = tree / "shared", tree / "published", tree / "workspaces"
        for d in (shared / "dies", published, works):
            d.mkdir(parents=True)
        _write_catalog(shared / "dies")
        shutil.copy2(_ROOT / "config" / "motor_config.yaml", shared / "motor_config.yaml")
        (shared / "motor_catalog.json").write_text(json.dumps({"tiers": [], "diameters_mm": [], "motors": []}), encoding="utf-8")
        (shared / "motor_presets.json").write_text("{}", encoding="utf-8")
        monkeypatch.setenv("WORKSPACES_ROOT", str(works))
        monkeypatch.setenv("SHARED_ROOT", str(shared))
        monkeypatch.setenv("PUBLISHED_ROOT", str(published))
        monkeypatch.delitem(fam.__dict__, "_DIES_DIR", raising=False)
        monkeypatch.delitem(cat.__dict__, "_CATALOG_PATH", raising=False)
        monkeypatch.delitem(pre.__dict__, "_PRESETS_PATH", raising=False)
        monkeypatch.setattr("motor_ai_sim.config.DEFAULT_CONFIG_PATH", str(tmp_path / "identity" / "motor_config.yaml"))
        (tmp_path / "identity").mkdir(exist_ok=True)
        dies_dir = shared / "dies"
    else:
        dies_dir = tmp_path / "dies"
        dies_dir.mkdir()
        _write_catalog(dies_dir)
        monkeypatch.setattr(fam, "_DIES_DIR", dies_dir)
        monkeypatch.setitem(cat.__dict__, "_CATALOG_PATH", tmp_path / "motor_catalog.json")
        (tmp_path / "motor_catalog.json").write_text(json.dumps({"tiers": [], "diameters_mm": [], "motors": []}), encoding="utf-8")
        monkeypatch.setitem(pre.__dict__, "_PRESETS_PATH", tmp_path / "motor_presets.json")
        (tmp_path / "motor_presets.json").write_text("{}", encoding="utf-8")

    U.create_user(ADMIN, "password-admin", role="admin", name="Admin")
    U.create_user(ENG, "password-eng-1", role="engineer", name="Eng")
    U.create_user(MEMBER, "password-member", role="user", name="Member")
    U.set_motor_grants(ENG, all_motors=False, dies=[DIE])
    U.set_motor_grants(MEMBER, all_motors=False, dies=[DIE])

    # a project that holds the first configuration (the revisions' auto-link follows it)
    store.create_project({"id": PROJECT, "status": "design", "members": [], "linked_motors": [
        {"die": DIE, "config": CFG, "status": "current", "recommended": {"by": ADMIN, "at": "2026-10-27T09:00:00"}}]})
    store.save_index({"motors": {dev_motors._key(DIE, CFG): {"project_id": PROJECT, "die": DIE, "config": CFG}}})
    monkeypatch.setattr(dev_motors, "_capture_project_motor_snapshot", lambda *a, **k: None)

    hdr = lambda e: {"Authorization": f"Bearer {U.issue_token(e)}"}   # noqa: E731
    yield {"layered": layered, "dies_dir": dies_dir, "tmp": tmp_path, "admin": hdr(ADMIN), "eng": hdr(ENG),
           "member": hdr(MEMBER), "anon": {}}
    fam._TREE_CACHE.clear()
    MA._CFG_VIS_CACHE.clear()


def _assign(env, who="admin", die=DIE, cfg=CFG, **extra):
    return client.post("/api/motor-revisions/assign", json={"die": die, "config": cfg, **extra}, headers=env[who])


def _new(env, lineage, who="admin", **body):
    body.setdefault("reason", "Slot fill raised to cut copper loss")
    return client.post(f"/api/motor-revisions/{lineage}/new", json=body, headers=env[who])


def _lineage_id(r) -> str:
    return r.json()["lineage"]["id"]


def _linked(project=PROJECT) -> list:
    from motor_ai_sim.development import store
    return [(m["die"], m["config"], m.get("status"), bool(m.get("recommended"))) for m in store.get_project(project)["linked_motors"]]


def _cfg_path(env, die, cfg) -> Path:
    """Where the configuration file is read from for the admin (flat: the dies folder; layered: the admin's own workspace copy, else shared)."""
    from motor_ai_sim.routes import family as fam
    return Path(str(fam._cfg_file(die, cfg)))


# ── letters ──────────────────────────────────────────────────────────────────

def test_letters_run_a_to_z_then_aa():
    assert [MR.rev_letters(i) for i in (1, 2, 26, 27, 28, 52, 53, 702, 703)] == ["A", "B", "Z", "AA", "AB", "AZ", "BA", "ZZ", "AAA"]
    assert [MR.next_rev(x) for x in ("A", "Y", "Z", "AZ", "ZZ")] == ["B", "Z", "AA", "BA", "AAA"]
    for n in (1, 5, 26, 27, 100, 702, 703, 5000):
        assert MR.rev_number(MR.rev_letters(n)) == n
    for bad in ("", "a", "A1", "1"):
        with pytest.raises(ValueError):
            MR.rev_number(bad)
    with pytest.raises(ValueError):
        MR.rev_letters(0)


# ── Rev A ────────────────────────────────────────────────────────────────────

def test_assign_rev_a_records_the_snapshot_the_project_and_the_client_agreement(env):
    r = _assign(env, agreed_with_client=True)
    assert r.status_code == 200, r.text
    body = r.json()
    rev = body["revision"]
    assert body["rev"] == "A" and rev["label"] == "Rev A" and rev["die"] == DIE and rev["config"] == CFG
    assert rev["agreed_with_client"] is True and rev["frozen"] is False and rev["latest"] is True
    assert rev["project_id"] == PROJECT                                   # taken from the project that holds the motor
    assert rev["created_by"] == ADMIN and rev["reason"] == ""
    lid = body["lineage"]["id"]
    snap = Path(env["tmp"]) / "revisions" / "snapshots" / lid / "A"
    assert (snap / "config.yaml").is_file() and (snap / "die.yaml").is_file()
    assert rev["snapshot"]["config_sha256"] == hashlib.sha256((snap / "config.yaml").read_bytes()).hexdigest()
    assert rev["snapshot_intact"] is True
    # a motor belongs to ONE lineage
    again = _assign(env)
    assert again.status_code == 409 and "Rev A" in again.json()["detail"]
    assert _assign(env, cfg="L99").status_code in (404, 422)
    bad_project = _assign(env, cfg=OTHER, project_id="no-such-project")
    assert bad_project.status_code == 422
    ok = _assign(env, cfg=OTHER, project_id=PROJECT)
    assert ok.status_code == 200 and ok.json()["revision"]["project_id"] == PROJECT and ok.json()["revision"]["agreed_with_client"] is False
    assert ok.json()["lineage"]["id"] != lid


def test_for_motor_and_lineage_reads(env):
    lid = _lineage_id(_assign(env))
    r = client.get("/api/motor-revisions", params={"die": DIE, "config": CFG}, headers=env["admin"])
    assert r.status_code == 200
    assert r.json()["revision"]["rev"] == "A" and r.json()["lineage"]["id"] == lid and r.json()["lineage"]["current"] == "A"
    none = client.get("/api/motor-revisions", params={"die": DIE, "config": OTHER}, headers=env["admin"])
    assert none.json() == {"revision": None, "lineage": None}
    assert client.get("/api/motor-revisions", params={"die": DIE}, headers=env["admin"]).status_code == 422
    assert client.get(f"/api/motor-revisions/{lid}", headers=env["admin"]).json()["lineage"]["revisions"][0]["rev"] == "A"
    assert client.get("/api/motor-revisions/nope", headers=env["admin"]).status_code == 404


# ── Rev B: a configuration copy ──────────────────────────────────────────────

def test_new_revision_copies_the_configuration_freezes_the_old_one_and_follows_the_project(env):
    lid = _lineage_id(_assign(env))
    before = _cfg_path(env, DIE, CFG).read_text(encoding="utf-8")
    r = _new(env, lid, reason="  Rotor skew added  ", agreed_with_client=False)
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["rev"], body["die"], body["config"]) == ("B", DIE, f"{CFG}-B")
    assert body["previous"] == {"rev": "A", "die": DIE, "config": CFG, "read_only": True}
    revs = body["lineage"]["revisions"]
    assert [x["rev"] for x in revs] == ["A", "B"] and body["lineage"]["current"] == "B"
    a, b = revs
    assert a["frozen"] and a["read_only"] and not a["latest"] and a["frozen_at"]
    assert not b["frozen"] and b["latest"] and b["parent_rev"] == "A" and b["new_die"] is False
    assert b["reason"] == "Rotor skew added"                                  # trimmed, stored
    # the old configuration is LOCKED (the existing lock), the copy is not, and the copy carries the duty with its results
    old_doc = yaml.safe_load(_cfg_path(env, DIE, CFG).read_text(encoding="utf-8"))
    new_doc = yaml.safe_load(_cfg_path(env, DIE, f"{CFG}-B").read_text(encoding="utf-8"))
    assert old_doc["locked"] is True and not new_doc.get("locked")
    assert [d["name"] for d in new_doc["duties"]] == [DUTY]
    assert {k: v for k, v in old_doc.items() if k != "locked"} == yaml.safe_load(before)       # nothing else of A changed
    # A's snapshot was retaken AFTER the lock: it is the final state and still matches the file
    assert a["snapshot_intact"] is True and b["snapshot_intact"] is True
    # the project: B is linked, current, newest; the staff mark "recommended" moved from A to B
    assert (DIE, f"{CFG}-B", "current", True) in _linked()
    assert (DIE, CFG, "current", False) in _linked()
    assert body["recommended_carried"] is True and body["project_id"] == PROJECT
    # the catalogue tree still lists both configurations
    names = {c["name"] for d in client.get("/api/family/tree", headers=env["admin"]).json()["dies"] if d["name"] == DIE for c in d["configs"]}
    assert {CFG, f"{CFG}-B"} <= names


def test_a_reason_is_required_and_the_current_revision_is_checked(env):
    lid = _lineage_id(_assign(env))
    for reason in ("", "   "):
        r = client.post(f"/api/motor-revisions/{lid}/new", json={"reason": reason}, headers=env["admin"])
        assert r.status_code == 422, r.text
    assert client.post(f"/api/motor-revisions/{lid}/new", json={}, headers=env["admin"]).status_code == 422
    # nothing was created by the refusals
    assert not (env["dies_dir"] / DIE / f"{CFG}-B.yaml").exists()
    assert yaml.safe_load(_cfg_path(env, DIE, CFG).read_text(encoding="utf-8")).get("locked") is not True
    assert _new(env, lid, from_rev="A").status_code == 200
    stale = _new(env, lid, from_rev="A")                                      # someone else already made B
    assert stale.status_code == 409 and "Rev B" in stale.json()["detail"]
    assert _new(env, "no-such-lineage").status_code == 404


def test_a_named_configuration_a_taken_name_and_the_letters_beyond_z(env):
    lid = _lineage_id(_assign(env))
    r = _new(env, lid, config_name="L40 skewed")
    assert r.status_code == 200 and r.json()["config"] == "L40 skewed"
    for _ in range(24):                                                       # C .. Z
        assert _new(env, lid).status_code == 200
    z = client.get(f"/api/motor-revisions/{lid}", headers=env["admin"]).json()["lineage"]
    assert z["current"] == "Z" and len(z["revisions"]) == 26
    aa = _new(env, lid)
    assert aa.status_code == 200 and aa.json()["rev"] == "AA"
    assert aa.json()["config"] == f"{CFG}-AA"
    taken = _new(env, lid, config_name=f"{CFG}-AA")                           # the name of the current one
    assert taken.status_code == 409
    assert client.get(f"/api/motor-revisions/{lid}", headers=env["admin"]).json()["lineage"]["current"] == "AA"


# ── Rev B: a new die ─────────────────────────────────────────────────────────

def test_a_new_lamination_makes_a_die_copy_and_links_it(env):
    lid = _lineage_id(_assign(env))
    r = _new(env, lid, new_die=True, reason="Slot opening widened: new lamination", die_name="REVDIE 40 v2")
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["rev"], body["die"], body["config"]) == ("B", "REVDIE 40 v2", CFG)
    assert body["revision"]["new_die"] is True
    new_die = yaml.safe_load((env["dies_dir"] / "REVDIE 40 v2" / "die.yaml").read_text(encoding="utf-8")) if not env["layered"] else None
    if new_die is not None:
        assert new_die["derived_from"]["die"] == DIE and new_die["locked"] is False
    # the old die and its configuration are locked; the other configurations of the old die are untouched
    old = yaml.safe_load(_cfg_path(env, DIE, CFG).read_text(encoding="utf-8"))
    assert old["locked"] is True
    # the new die's configuration joined the project through the die it was copied from
    assert ("REVDIE 40 v2", CFG, "current", True) in _linked()
    assert body["project_id"] == PROJECT
    # a name that is taken is refused and leaves the lineage on Rev A... after B exists the lineage is at B
    again = _new(env, lid, new_die=True, die_name=DIE)
    assert again.status_code in (409, 422)
    assert client.get(f"/api/motor-revisions/{lid}", headers=env["admin"]).json()["lineage"]["current"] == "B"


# ── guards: nothing that holds a revision is deleted ─────────────────────────

def test_a_motor_that_holds_a_revision_cannot_be_deleted_retired_or_renamed(env):
    lid = _lineage_id(_assign(env))
    _new(env, lid)
    admin = env["admin"]
    for cfg in (CFG, f"{CFG}-B"):                                             # the frozen one and the current one
        r = client.delete(f"/api/family/config/{DIE}/{cfg}", headers=admin)
        assert r.status_code == 409 and "never deleted" in r.json()["detail"], (cfg, r.text)
        assert _cfg_path(env, DIE, cfg).is_file()
    r = client.delete(f"/api/family/die/{DIE}", params={"force": "true"}, headers=admin)
    assert r.status_code == 409 and "never deleted" in r.json()["detail"]
    assert client.patch(f"/api/family/config/{DIE}/{CFG}", json={"name": "renamed"}, headers=admin).status_code == 409
    assert client.patch(f"/api/family/config/{DIE}/{f'{CFG}-B'}", json={"name": "renamed"}, headers=admin).status_code == 409
    assert _cfg_path(env, DIE, CFG).is_file()
    # a configuration with no revision is still an ordinary one
    assert client.patch(f"/api/family/config/{DIE}/{OTHER}", json={"name": "L56"}, headers=admin).status_code == 200
    assert client.delete(f"/api/family/config/{DIE}/L56", headers=admin).status_code == 200
    # the guard is the library's too (a cleanup that calls the helpers directly)
    from fastapi import HTTPException
    for call in (lambda: MR.refuse_delete_config(DIE, CFG), lambda: MR.refuse_delete_die(DIE), lambda: MR.refuse_rename_config(DIE, f"{CFG}-B")):
        with pytest.raises(HTTPException) as e:
            call()
        assert e.value.status_code == 409
    MR.refuse_delete_config(DIE, "L99")
    MR.refuse_delete_die("OTHER DIE")


def test_retiring_a_shared_die_with_a_revision_is_refused(env):
    if not env["layered"]:
        pytest.skip("retire is the shared layer's verb")
    _assign(env)
    r = client.post(f"/api/family/die/{DIE}/retire", json={"confirm": DIE}, headers=env["admin"])
    assert r.status_code == 409 and "never deleted" in r.json()["detail"], r.text


def test_the_replaced_revision_is_read_only_but_still_viewable(env):
    lid = _lineage_id(_assign(env))
    _new(env, lid)
    admin = env["admin"]
    # viewing works
    assert client.get(f"/api/family/payload/{DIE}/{CFG}", headers=admin).status_code == 200
    assert client.get(f"/api/family/duty_results/{DIE}/{CFG}", headers=admin).status_code == 200
    # every kind of edit is refused at the write seam, with the reason
    unlock = client.patch(f"/api/family/config/{DIE}/{CFG}/lock", json={"locked": False}, headers=admin)
    assert unlock.status_code == 409 and "Rev A" in unlock.json()["detail"] and "Rev B" in unlock.json()["detail"], unlock.text
    duty = client.post("/api/family/duty", headers=admin, json={"die": DIE, "config": CFG, "duty": {
        "name": "extra", "mode": "motor", "current_arms": 1.0, "rpm": 1.0, "gamma_deg": 0.0}})
    assert duty.status_code == 409
    assert client.delete(f"/api/family/duty/{DIE}/{CFG}/{DUTY}", headers=admin).status_code == 409
    assert client.patch(f"/api/family/config/{DIE}/{CFG}/default_duty", json={"duty": DUTY}, headers=admin).status_code == 409
    assert client.patch(f"/api/family/config/{DIE}/{CFG}/limits", json={"winding_c": 180}, headers=admin).status_code == 409
    assert client.post(f"/api/family/config/{DIE}/{CFG}/duplicate", json={"name": "copy of A"}, headers=admin).status_code == 200   # copying is fine
    still = yaml.safe_load(_cfg_path(env, DIE, CFG).read_text(encoding="utf-8"))
    assert still["locked"] is True and [d["name"] for d in still["duties"]] == [DUTY]
    # the die of a replaced revision stays locked
    assert client.patch(f"/api/family/die/{DIE}/lock", json={"locked": False}, headers=admin).status_code == 409
    assert client.patch(f"/api/family/die/{DIE}/lock", json={"locked": True}, headers=admin).status_code == 200
    # the CURRENT revision is an ordinary working configuration
    cur = client.post("/api/family/duty", headers=admin, json={"die": DIE, "config": f"{CFG}-B", "duty": {
        "name": "extra", "mode": "motor", "current_arms": 1.0, "rpm": 1.0, "gamma_deg": 0.0}})
    assert cur.status_code == 200, cur.text
    # and the frozen file still reads as at the snapshot
    view = client.get(f"/api/motor-revisions/{lid}", headers=admin).json()["lineage"]["revisions"][0]
    assert view["snapshot_intact"] is True


def test_a_failed_copy_leaves_nothing_behind(env, monkeypatch):
    lid = _lineage_id(_assign(env))
    real = MR._snapshot
    monkeypatch.setattr(MR, "_snapshot", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        TestClient(app, raise_server_exceptions=True).post(
            f"/api/motor-revisions/{lid}/new", json={"reason": "x"}, headers=env["admin"])
    monkeypatch.setattr(MR, "_snapshot", real)
    # the copy was taken away again and the old configuration is not locked
    assert not _cfg_path(env, DIE, f"{CFG}-B").is_file()
    assert yaml.safe_load(_cfg_path(env, DIE, CFG).read_text(encoding="utf-8")).get("locked") is not True
    assert MR.get_lineage(lid)["revisions"][-1]["rev"] == "A"


# ── access ───────────────────────────────────────────────────────────────────

def test_writes_are_staff_only_and_reads_need_an_account(env):
    # anonymous: signed-in required
    assert _assign(env, who="anon").status_code in (401, 403)
    # a plain account may not write
    r = _assign(env, who="member")
    assert r.status_code in (401, 403), r.text
    lid = _lineage_id(_assign(env))
    assert _new(env, lid, who="member").status_code in (401, 403)
    assert client.patch(f"/api/motor-revisions/{lid}/A/agreed", json={"agreed": True}, headers=env["member"]).status_code in (401, 403)
    assert MR.get_lineage(lid)["revisions"][-1]["rev"] == "A"
    # reads: anonymous refused, a member reads the letters but not the engineers' reasons
    assert client.get("/api/motor-revisions/index").status_code == 401
    assert client.get(f"/api/motor-revisions/{lid}").status_code == 401
    assert client.get("/api/motor-revisions", params={"die": DIE, "config": CFG}).status_code == 401
    assert _new(env, lid, reason="Internal: supplier change").status_code == 200
    m = client.get(f"/api/motor-revisions/{lid}", headers=env["member"])
    assert m.status_code == 200
    revs = m.json()["lineage"]["revisions"]
    assert [x["rev"] for x in revs] == ["A", "B"] and all("reason" not in x and "created_by" not in x and "snapshot" not in x for x in revs)
    assert "Internal" not in m.text
    s = client.get(f"/api/motor-revisions/{lid}", headers=env["admin"]).json()["lineage"]["revisions"][1]
    assert s["reason"] == "Internal: supplier change" and s["created_by"] == ADMIN


def test_an_engineer_is_staff_in_the_layered_catalogue(env):
    if not env["layered"]:
        pytest.skip("flat install: only the admin writes the catalogue")
    r = _assign(env, who="eng", cfg=OTHER)
    assert r.status_code == 200, r.text
    lid = _lineage_id(r)
    n = _new(env, lid, who="eng")
    assert n.status_code == 200, n.text
    assert n.json()["revision"]["created_by"] == ENG
    # the member sees the letter of the motors it may see
    idx = client.get("/api/motor-revisions/index", headers=env["member"]).json()["revisions"]
    assert {(x["config"], x["rev"]) for x in idx if x["config"] == OTHER} == {(OTHER, "A")}


def test_the_index_lists_only_what_the_caller_may_see(env):
    a = _lineage_id(_assign(env))
    _new(env, a)
    p = _assign(env, cfg=PRIV)                                                # a PRIVATE configuration (admin sees it)
    assert p.status_code == 200, p.text
    adm = client.get("/api/motor-revisions/index", headers=env["admin"]).json()["revisions"]
    assert {(x["config"], x["rev"], x["latest"], x["frozen"]) for x in adm} == {
        (CFG, "A", False, True), (f"{CFG}-B", "B", True, False), (PRIV, "A", True, False)}
    mem = client.get("/api/motor-revisions/index", headers=env["member"]).json()["revisions"]
    assert {x["config"] for x in mem} == {CFG, f"{CFG}-B"}                    # the private one does not exist for the member
    assert PRIV not in json.dumps(mem)
    hidden = client.get("/api/motor-revisions", params={"die": DIE, "config": PRIV}, headers=env["member"])
    missing = client.get("/api/motor-revisions", params={"die": DIE, "config": "L99"}, headers=env["member"])
    assert hidden.status_code == missing.status_code == 404
    assert hidden.text.replace(PRIV, "<C>") == missing.text.replace("L99", "<C>"), (hidden.text, missing.text)      # indistinguishable
    assert client.get(f"/api/motor-revisions/{_lineage_id(p)}", headers=env["member"]).status_code == 404
    # a hidden configuration cannot be given a revision by an account that cannot see it
    eng = _assign(env, who="eng", cfg=PRIV)
    assert eng.status_code in (403, 404)


def test_agreement_with_the_client_can_be_recorded_later(env):
    lid = _lineage_id(_assign(env))
    r = client.patch(f"/api/motor-revisions/{lid}/A/agreed", json={"agreed": True}, headers=env["admin"])
    assert r.status_code == 200 and r.json()["lineage"]["revisions"][0]["agreed_with_client"] is True
    assert client.patch(f"/api/motor-revisions/{lid}/Q/agreed", json={"agreed": True}, headers=env["admin"]).status_code == 404


# ── the other modules ────────────────────────────────────────────────────────

def test_production_carries_the_label(env):
    lid = _lineage_id(_assign(env))
    _new(env, lid)
    from motor_ai_sim.production.routes import _with_product_revision
    assert _with_product_revision({"die": DIE, "config": CFG, "title": "x"})["product_revision"] == {
        "rev": "A", "label": "Rev A", "lineage": lid, "latest": False, "frozen": True}
    assert _with_product_revision({"die": DIE, "config": f"{CFG}-B"})["product_revision"]["label"] == "Rev B"
    plain = {"die": DIE, "config": OTHER}
    assert _with_product_revision(plain) == plain and "product_revision" not in plain


def test_a_die_rename_follows_the_revision_records(env):
    if env["layered"]:
        pytest.skip("rename of a shared die is the shared layer's job")
    lid = _lineage_id(_assign(env))
    r = client.patch(f"/api/family/die/{DIE}", json={"name": "REVDIE 40 renamed"}, headers=env["admin"])
    assert r.status_code == 200, r.text
    assert any(c["store"] == "motor_revisions" for c in r.json().get("changes", [])) or MR.get_lineage(lid)["revisions"][0]["die"] == "REVDIE 40 renamed"
    assert MR.get_lineage(lid)["revisions"][0]["die"] == "REVDIE 40 renamed"
    assert MR.find("REVDIE 40 renamed", CFG) is not None and MR.find(DIE, CFG) is None
    doc = {"lineages": {"x": {"revisions": [{"die": "A"}, {"die": "B"}, {"die": "A"}]}}}
    assert MR.rename_die_in(doc, "A", "C") == 2 and [r["die"] for r in doc["lineages"]["x"]["revisions"]] == ["C", "B", "C"]


def test_no_store_means_no_cost_and_no_refusal(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_MOTOR_REVISIONS_ROOT", str(tmp_path / "none"))
    MR.guard_config_write("D", "C")
    MR.refuse_delete_config("D", "C")
    MR.refuse_delete_die("D")
    assert MR.find("D", "C") is None and MR.label_for("D", "C") is None
