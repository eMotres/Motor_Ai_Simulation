"""Archive ONE configuration of a die (owner 2026-11-22: one motor per project; the rest are old and archived; never delete).
``motor_ai_sim.config_archive`` + ``/api/config-archive`` + the configuration rule in ``motor_access``.

What is checked: admin only; nothing on disk changes; the configuration leaves the tree, the project motor picker and the
cards for everybody, admins keep the direct link; a customer and a factory account see it exactly like a missing one; refusals
while a project links it or it has a production; the last configuration of a die may go; history; restore; a die rename and a
configuration move / rename keep the archive.  Temporary layers and stores, neutral names, no solver."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from motor_ai_sim import config_archive as CA
from motor_ai_sim.api import app

_ROOT = Path(__file__).resolve().parents[1]
_REAL_USERS = _ROOT / "config" / "users.json"

DIE = "NEWDIE 12_40"
OLDCFG = "L60-15t"
NEWCFG = "L60-14t"
ADMIN = "admin@example.com"
A = "alice@example.com"
F = "factory@example.com"
OTHER = "OTHERDIE 12_40"
WSDIE = "WSDIE 12_40"

client = TestClient(app)


def _die_doc(name):
    return yaml.safe_dump({"name": name, "locked": False, "created": "2026-11-17T10:00:00",
                           "geometry": {"num_slots": 12, "num_poles": 10, "stator_diameter": 12.0, "magnet_height": 1.0,
                                        "motor_length": 40.0}}, sort_keys=False)


def _cfg_doc(die, cfg):
    return yaml.safe_dump({"name": cfg, "die": die, "role": "motor", "geometry_overrides": {"motor_length": 10.0},
                           "winding": {"connection": "star"}, "materials": {"magnet": "N42SH", "stator_core": "20SW1200"},
                           "duties": []}, sort_keys=False)


def _put_die(dies: Path, name: str, cfgs=(OLDCFG,)):
    (dies / name).mkdir(parents=True)
    (dies / name / "die.yaml").write_text(_die_doc(name), encoding="utf-8")
    for c in cfgs:
        (dies / name / f"{c}.yaml").write_text(_cfg_doc(name, c), encoding="utf-8")


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
    _put_die(shared / "dies", DIE, cfgs=(OLDCFG, NEWCFG))
    _put_die(shared / "dies", OTHER, cfgs=())
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
    U.create_user(F, "password-f", role="oem", name="Factory")
    U.set_motor_grants(F, all_motors=True)
    ids = {e: W.workspace_id(e) for e in (ADMIN, A)}
    _put_die(works / ids[ADMIN] / "dies", WSDIE, cfgs=(OLDCFG,))          # a workspace-only die of the admin
    monkeypatch.setattr("motor_ai_sim.config.DEFAULT_CONFIG_PATH", str(tmp_path / "identity" / "motor_config.yaml"))
    (tmp_path / "identity").mkdir(exist_ok=True)
    yield {"srv": srv, "tmp": tmp_path, "ids": ids,
           "admin": {"Authorization": f"Bearer {U.issue_token(ADMIN)}"},
           "a": {"Authorization": f"Bearer {U.issue_token(A)}"},
           "f": {"Authorization": f"Bearer {U.issue_token(F)}"}}
    fam._TREE_CACHE.clear()


def _tree(headers) -> dict:
    r = client.get("/api/family/tree", headers=headers)
    assert r.status_code == 200, r.text
    return {d["name"]: sorted(c["name"] for c in d.get("configs") or []) for d in r.json()["dies"]}


def _audit(env) -> list:
    p = env["tmp"] / "audit.jsonl"
    return [json.loads(l)["action"] for l in p.read_text().splitlines()] if p.exists() else []


def _archive(env, cfg=OLDCFG, who="admin", die=DIE, reason="replaced by L60-14t"):
    body = {} if reason is None else {"reason": reason}
    return client.post(f"/api/config-archive/{die}/{cfg}/archive", headers=env[who], json=body)


def test_an_admin_archives_one_configuration_and_restores_it_nothing_on_disk_changes(env):
    assert _tree(env["admin"])[DIE] == [NEWCFG, OLDCFG]
    before = _hash_tree(env["srv"] / "shared")
    r = _archive(env)
    assert r.status_code == 200, r.text
    assert r.json()["archived"] is True and r.json()["reason"] == "replaced by L60-14t" and r.json()["by"] == ADMIN
    assert _hash_tree(env["srv"] / "shared") == before                                    # nothing moved, renamed or deleted
    assert _tree(env["admin"])[DIE] == [NEWCFG]                                # gone from the listing, for the admin too
    assert _tree(env["a"])[DIE] == [NEWCFG]
    assert client.get(f"/api/family/payload/{DIE}/{OLDCFG}", headers=env["admin"]).status_code == 200   # the admin's direct link
    listed = client.get("/api/config-archive", headers=env["admin"]).json()["configs"]
    assert [(c["die"], c["config"]) for c in listed] == [(DIE, OLDCFG)]
    assert client.get(f"/api/config-archive?die={DIE}", headers=env["admin"]).json()["configs"] == listed
    check = client.get(f"/api/config-archive/{DIE}/{OLDCFG}", headers=env["admin"]).json()
    assert check["archived"] is True and [h["to"] for h in check["history"]] == ["archived"]
    assert check["history"][0]["by"] == ADMIN and check["history"][0]["reason"] == "replaced by L60-14t"
    assert _archive(env).status_code == 409                                    # already archived

    r = client.post(f"/api/config-archive/{DIE}/{OLDCFG}/restore", headers=env["admin"])
    assert r.status_code == 200 and r.json()["archived"] is False
    assert _tree(env["a"])[DIE] == [NEWCFG, OLDCFG]
    assert client.post(f"/api/config-archive/{DIE}/{OLDCFG}/restore", headers=env["admin"]).status_code == 404
    assert [h["to"] for h in client.get(f"/api/config-archive/{DIE}/{OLDCFG}", headers=env["admin"]).json()["history"]] == ["archived", "active"]
    assert _audit(env) == ["config.archive", "config.unarchive"]
    assert _hash_tree(env["srv"] / "shared") == before


@pytest.mark.parametrize("who", ["a", "f"])
def test_a_customer_and_a_factory_account_see_it_exactly_like_a_missing_configuration(env, who):
    assert client.get(f"/api/family/payload/{DIE}/{OLDCFG}", headers=env[who]).status_code == 200     # visible before
    assert OLDCFG in json.dumps(client.get("/api/family/tree", headers=env[who]).json())
    assert _archive(env).status_code == 200
    for path in (f"/api/family/payload/{DIE}/{OLDCFG}",):
        hidden = client.get(path, headers=env[who])
        missing = client.get(path.replace(OLDCFG, "NOPE-1t"), headers=env[who])
        assert hidden.status_code == missing.status_code == 404, (path, hidden.text)
        assert hidden.json()["detail"].replace(OLDCFG, "X") == missing.json()["detail"].replace("NOPE-1t", "X")
    assert OLDCFG not in json.dumps(client.get("/api/family/tree", headers=env[who]).json())


def test_only_a_verified_admin(env):
    assert _archive(env, who="a").status_code == 403
    assert _archive(env, who="f").status_code == 403
    assert client.get("/api/config-archive", headers=env["a"]).status_code == 403
    assert client.post(f"/api/config-archive/{DIE}/{OLDCFG}/restore", headers=env["a"]).status_code == 403
    assert _archive(env, cfg="NOPE-1t").status_code == 404
    assert _audit(env) == []


def test_a_linked_configuration_or_one_with_a_production_is_refused_in_plain_words(env):
    from motor_ai_sim.development import store
    store.create_project({"id": "p1", "title": "Pump", "status": "design", "customer_org": "Org", "created_at": "t",
                          "updated_at": "t", "notes": "", "members": [], "pending_invites": [],
                          "linked_motors": [{"die": DIE, "config": OLDCFG, "status": "current"}]})
    r = _archive(env)
    assert r.status_code == 409 and r.json().get("code") == "config.in_use", r.text
    assert "a project links it (unlink it there first) (Pump)" in r.json()["detail"]
    assert _tree(env["admin"])[DIE] == [NEWCFG, OLDCFG]
    assert _archive(env, cfg=NEWCFG).status_code == 200                       # the sibling is free
    assert _audit(env) == ["config.archive"]


def test_refusals_name_what_holds_the_configuration():
    clear = dict(projects=[], productions=[], jobs=[])
    proj = lambda status, cfg: {"title": "P", "status": status, "linked_motors": [{"die": "D", "config": cfg}]}  # noqa: E731
    assert CA.refusals("D", "C", **clear) == []
    assert CA.refusals("D", "C", **{**clear, "projects": [proj("closed", "C"), proj("design", "OTHER")]}) == []
    assert CA.refusals("D", "C", **{**clear, "projects": [proj("design", "C")]}) == [{"code": "project_link", "names": ["P"]}]
    assert CA.refusals("D", "C", **{**clear, "productions": [{"die": "D", "config": "C", "status": "active"}]}) == [
        {"code": "production", "names": ["D/C"]}]
    assert CA.refusals("D", "C", **{**clear, "jobs": [{"die": "D", "where": "job x"}, {"die": "E", "where": "job y"}]}) == [
        {"code": "running_job", "names": ["job x"]}]


def test_the_last_configuration_of_a_die_may_go_and_the_die_stays(env):
    assert _archive(env, cfg=OLDCFG).status_code == 200
    assert _archive(env, cfg=NEWCFG).status_code == 200
    assert DIE not in _tree(env["a"])                                         # no configuration left to show: no header
    assert (env["srv"] / "shared" / "dies" / DIE / "die.yaml").is_file()


def test_archived_cards_are_dropped_for_everybody_but_admins(tmp_path, monkeypatch):
    from motor_ai_sim import motor_access as MA
    monkeypatch.setenv("MOTRES_OBJECT_REGISTRY_ROOT", str(tmp_path / "registry"))
    CA.archive("D", "C", by="admin", reason="old")
    cards = [{"name": "D C"}, {"name": "D C2"}, {"die": "D", "config": "C", "name": "x"}]
    assert MA.drop_private_config_cards(cards, {"is_admin": True}) == cards                 # an admin's direct card links keep working
    assert MA._drop_archived_cards(cards) == [{"name": "D C2"}]                             # everybody else: no card
    CA.restore("D", "C", by="admin")
    assert MA._drop_archived_cards(cards) == cards


def test_a_configuration_rename_and_move_keep_the_archive_and_its_history(env):
    _archive(env)
    r = client.post(f"/api/family/config/{DIE}/{OLDCFG}/rename", headers=env["admin"], json={"name": "L60-15t-old"})
    assert r.status_code == 200, r.text
    assert CA.is_archived(DIE, "L60-15t-old") and not CA.is_archived(DIE, OLDCFG)
    assert [h["to"] for h in CA.history(DIE, "L60-15t-old")] == ["archived"]
    doc = {"configs": {"D/C": {}, "D/K": {}}, "history": [{"key": "D/C"}, {"key": "D/K"}]}
    assert CA.rename_in(doc, "D", "E", "C", "C") == 2                         # a move to another die
    assert sorted(doc["configs"]) == ["D/K", "E/C"] and doc["history"][0]["key"] == "E/C"
    from motor_ai_sim import config_move as CM
    assert CM.ARCHIVE_STEPS == (CM.step_config_archive_publish, CM.step_config_archive)


def test_a_die_rename_keeps_the_archive(env):
    from motor_ai_sim import die_rename as DRN
    assert DRN.ARCHIVE_STEPS == (DRN.step_config_archive_publish, DRN.step_config_archive)
    doc = {"configs": {"OLD/C": {"by": "a"}, "OTHER/C": {}}, "history": [{"key": "OLD/C"}, {"key": "OTHER/C"}]}
    assert CA.rename_in(doc, "OLD", "NEW") == 2
    assert sorted(doc["configs"]) == ["NEW/C", "OTHER/C"] and doc["history"][0]["key"] == "NEW/C"
    _archive(env)
    r = client.patch(f"/api/family/die/{DIE}", headers=env["admin"], json={"name": "NEWDIE 12_40 b"})
    assert r.status_code == 200, r.text
    assert CA.is_archived("NEWDIE 12_40 b", OLDCFG) and [h["to"] for h in CA.history("NEWDIE 12_40 b", OLDCFG)] == ["archived"]



@pytest.mark.parametrize("reason", [None, "", "   "])
def test_a_reason_is_required(env, reason):
    assert _archive(env, reason=reason).status_code == 422
    assert _archive(env, reason="x" * 201).status_code == 422
    assert not CA.is_archived(DIE, OLDCFG)


def test_only_shared_catalog_configurations_are_archived(env):
    r = _archive(env, die=WSDIE)
    assert r.status_code == 409 and r.json().get("code") == "config.archive_shared_only", r.text


def test_a_case_alias_archives_the_stored_name(env):
    r = _archive(env, cfg=OLDCFG.upper())
    assert r.status_code == 200 and r.json()["config"] == OLDCFG and CA.is_archived(DIE, OLDCFG)


def test_a_public_configuration_is_archived_too(env, monkeypatch):
    from motor_ai_sim import motor_access as MA
    MA.set_config_visibility(DIE, OLDCFG, "public")
    assert client.get(f"/api/family/payload/{DIE}/{OLDCFG}", headers=env["a"]).status_code == 200
    _archive(env)
    assert client.get(f"/api/family/payload/{DIE}/{OLDCFG}", headers=env["a"]).status_code == 404
    monkeypatch.setattr(MA, "request_is_bound", lambda: True)
    monkeypatch.setattr(MA, "catalog_access", lambda authorization=None: {"is_admin": False, "mode": "granted", "dies": frozenset(), "configs": frozenset()})
    assert MA.hidden_for_request(DIE, OLDCFG) is True                            # the low-level readers (controller, duty results)
    monkeypatch.setattr(MA, "catalog_access", lambda authorization=None: {"is_admin": True})
    assert MA.hidden_for_request(DIE, OLDCFG) is False


def test_a_damaged_store_fails_closed_and_is_never_overwritten(env):
    assert _archive(env).status_code == 200
    path = CA.store_path()
    path.write_text("{ not json", encoding="utf-8")
    assert CA.is_archived(DIE, NEWCFG) is True                                   # every configuration counts as archived
    assert NEWCFG not in json.dumps(client.get("/api/family/tree", headers=env["a"]).json())
    assert _archive(env, cfg=NEWCFG).status_code == 503
    assert client.post(f"/api/config-archive/{DIE}/{OLDCFG}/restore", headers=env["admin"]).status_code == 503
    assert client.get("/api/config-archive", headers=env["admin"]).status_code == 503
    assert path.read_text(encoding="utf-8") == "{ not json"                      # the history it holds is not replaced


def test_a_project_link_and_a_production_are_refused_for_an_archived_configuration(env):
    from motor_ai_sim.development import store
    store.create_project({"id": "p2", "title": "Fan", "status": "design", "customer_org": "Org", "created_at": "t",
                          "updated_at": "t", "notes": "", "members": [], "pending_invites": [], "linked_motors": []})
    _archive(env)
    r = client.post("/api/projects/p2/motors", headers=env["admin"], json={"die": DIE, "config": OLDCFG})
    assert r.status_code == 409 and "archived" in r.json()["detail"]
    r = client.post(f"/api/admin/production/{DIE}/{OLDCFG}", headers=env["admin"], json={"title": {"en": "x"}})
    assert r.status_code == 409 and "archived" in r.json()["detail"]
    assert store.get_project("p2")["linked_motors"] == []


def test_any_production_record_refuses_and_jobs_count_per_configuration():
    clear = dict(projects=[], productions=[], jobs=[])
    assert CA.refusals("D", "C", **{**clear, "productions": [{"die": "D", "config": "C", "status": "archived"}]}) == [
        {"code": "production", "names": ["D/C"]}]
    assert CA.refusals("D", "C", **{**clear, "jobs": [{"die": "D", "config": "C2", "where": "job x"}]}) == []
    assert CA.refusals("D", "C", **{**clear, "jobs": [{"die": "D", "config": "", "where": "job y"}]}) == [
        {"code": "running_job", "names": ["job y"]}]


def test_an_unreadable_refusal_source_refuses_the_archive(env, monkeypatch):
    monkeypatch.setattr(CA, "_projects_strict", lambda: (_ for _ in ()).throw(OSError("projects unreadable")))
    assert _archive(env).status_code == 503 and not CA.is_archived(DIE, OLDCFG)


def test_a_cross_die_move_through_the_route_keeps_the_archive(env):
    _archive(env)
    r = client.post(f"/api/family/config/{DIE}/{OLDCFG}/move", headers=env["admin"], json={"target_die": OTHER})
    assert r.status_code == 200, r.text
    assert CA.is_archived(OTHER, OLDCFG) and not CA.is_archived(DIE, OLDCFG)
    assert [h["to"] for h in CA.history(OTHER, OLDCFG)] == ["archived"]


def test_the_rename_hook_runs_under_the_lock_and_its_undo_keeps_later_writes(env):
    _archive(env)

    class Ctx:
        dry_run = False

        def __init__(self):
            self.changes, self.undo = [], []

        def add(self, store, where, change, **extra):
            self.changes.append(store)

    ctx = Ctx()
    CA.rename_step(ctx, DIE, OTHER)
    assert CA.is_archived(OTHER, OLDCFG) and ctx.changes == ["config_archive"]
    CA.archive(DIE, NEWCFG, by=ADMIN, reason="later write")                     # acknowledged after the rename
    for u in reversed(ctx.undo):
        u()
    assert CA.is_archived(DIE, OLDCFG) and CA.is_archived(DIE, NEWCFG)           # undo moved the key back, the later write stays
    dry = Ctx()
    dry.dry_run = True
    CA.rename_step(dry, DIE, OTHER)
    assert dry.changes == ["config_archive"] and dry.undo == [] and CA.is_archived(DIE, OLDCFG)



def test_a_damaged_store_hides_only_shared_state_from_non_admins_and_admins_recover(env):
    assert _archive(env).status_code == 200
    CA.store_path().write_text("{ not json", encoding="utf-8")
    assert NEWCFG not in json.dumps(client.get("/api/family/tree", headers=env["a"]).json())       # non-admin: unknown = hidden
    assert _tree(env["admin"])[DIE] == [NEWCFG, OLDCFG]                                            # admin listing: recovery view
    assert _tree(env["admin"])[WSDIE] == [OLDCFG]                                                  # another layer: untouched
    r = client.post("/api/config-archive/recover", headers=env["admin"])
    assert r.status_code == 200, r.text
    assert CA.is_archived(DIE, OLDCFG) and not CA.is_archived(DIE, NEWCFG)
    assert list(CA.store_path().parent.glob("config_archive.damaged-*.json"))
    assert client.post("/api/config-archive/recover", headers=env["a"]).status_code == 403


@pytest.mark.parametrize("doc", [{}, {"version": 1, "history": []}, {"version": 1, "configs": {}},
                                 {"version": 1, "configs": {"D/C": {"by": "x"}}, "history": []},
                                 {"version": 2, "configs": {}, "history": []}])
def test_valid_json_missing_the_schema_is_damage_not_an_empty_archive(env, doc):
    CA.store_path().parent.mkdir(parents=True, exist_ok=True)
    CA.store_path().write_text(json.dumps(doc), encoding="utf-8")
    assert CA.state(DIE, OLDCFG) == CA.UNKNOWN
    assert _archive(env).status_code == 503
    assert json.loads(CA.store_path().read_text()) == doc                                         # never overwritten


def test_a_damaged_project_or_production_record_refuses_the_archive(env, tmp_path):
    from motor_ai_sim.development import store
    store.create_project({"id": "p9", "title": "X", "status": "design", "customer_org": "Org", "created_at": "t",
                          "updated_at": "t", "notes": "", "members": [], "pending_invites": [], "linked_motors": []})
    (store.path_for("p9") / "project.json").write_text("{ broken", encoding="utf-8")
    assert _archive(env).status_code == 503 and not CA.is_archived(DIE, OLDCFG)
    (store.path_for("p9") / "project.json").write_text(json.dumps({"id": "p9", "status": "design", "linked_motors": []}))
    from motor_ai_sim.production import store as PS
    PS.path_for(DIE, OLDCFG).mkdir(parents=True, exist_ok=True)
    (PS.path_for(DIE, OLDCFG) / "production.json").write_text("{ also broken", encoding="utf-8")
    r = _archive(env)
    assert r.status_code == 409 and r.json().get("code") == "config.in_use"


def test_an_automatic_link_never_links_an_archived_configuration(env):
    from motor_ai_sim.development import autolink, store
    store.create_project({"id": "p3", "title": "Auto", "status": "design", "customer_org": "Org", "created_at": "t",
                          "updated_at": "t", "notes": "", "members": [], "pending_invites": [], "linked_motors": []})
    _archive(env)
    assert autolink._link("p3", DIE, OLDCFG, ADMIN) is False
    assert store.get_project("p3")["linked_motors"] == []
    with pytest.raises(CA.Archived):
        with CA.admission(DIE, OLDCFG):
            pass


def test_a_case_alias_is_archived_too_on_a_case_insensitive_file_system(env, monkeypatch):
    _archive(env)
    monkeypatch.setitem(CA._CACHE, "ci", True)
    assert CA.state(DIE, OLDCFG.lower()) == CA.ARCHIVED
    monkeypatch.setitem(CA._CACHE, "ci", False)
    assert CA.state(DIE, OLDCFG.lower()) == CA.ACTIVE                                              # distinct files there


def test_saved_snapshots_carry_their_source_and_follow_the_archive(env, monkeypatch):
    from motor_ai_sim.routes import family as fam
    monkeypatch.setattr(fam, "_read_ctx", lambda: {"die": DIE, "config": OLDCFG, "duty": "rated"})
    r = client.post("/api/sims/saved", headers=env["a"], json={"name": "run", "params": {"rpm": 1000}, "results": {}})
    if r.status_code == 404:
        pytest.skip("saved simulations router not mounted here")
    assert r.json()["source"] == {"die": DIE, "config": OLDCFG, "layer": "shared"}
    assert [x["name"] for x in client.get("/api/sims/saved", headers=env["a"]).json()["sims"]] == ["run"]
    _archive(env)
    assert client.get("/api/sims/saved", headers=env["a"]).json()["sims"] == []


def test_the_support_digest_cache_follows_the_archive(env):
    from motor_ai_sim import support_catalog as SC
    before = SC._archive_generation()
    _archive(env)
    assert SC._archive_generation() != before
    CA.store_path().write_text("{ broken", encoding="utf-8")
    assert SC._archive_generation()[0] == "unknown" and SC._archive_generation() != SC._archive_generation()


def test_admin_card_listing_drops_archived_cards_but_keeps_them_while_damaged(env):
    from motor_ai_sim import motor_access as MA
    cards = [{"name": f"{DIE} {OLDCFG}"}, {"name": f"{DIE} {NEWCFG}"}]
    _archive(env)
    assert MA._drop_archived_cards(cards, admin=True) == [cards[1]]
    CA.store_path().write_text("{ broken", encoding="utf-8")
    assert MA._drop_archived_cards(cards, admin=True) == cards and MA._drop_archived_cards(cards) == []


# ── round 3 of #820: the rename / move is one transaction under the lifecycle lock ──
def _pause_after_files(monkeypatch, reached, release):
    import threading  # noqa: F401
    from motor_ai_sim import config_move as CM
    real = CM.step_files

    def paused(ctx):
        real(ctx)
        reached.set()
        assert release.wait(20)
    monkeypatch.setattr(CM, "LOCAL_STEPS", tuple(paused if s is real else s for s in CM.LOCAL_STEPS))


def test_an_archive_cannot_land_between_the_steps_of_a_rename(env, monkeypatch):
    import threading
    reached, release, out = threading.Event(), threading.Event(), {}
    _pause_after_files(monkeypatch, reached, release)
    mover = threading.Thread(target=lambda: out.update(rename=client.post(
        f"/api/family/config/{DIE}/{OLDCFG}/rename", headers=env["admin"], json={"name": "L60-15t-new"})))
    mover.start()
    assert reached.wait(20)                                   # the new file exists, the old one is not removed yet
    archiver = threading.Thread(target=lambda: out.update(archive=_archive(env)))
    archiver.start()
    archiver.join(1.0)
    assert archiver.is_alive()                                # the archive waits for the whole rename
    release.set()
    mover.join(20)
    archiver.join(20)
    assert out["rename"].status_code == 200, out["rename"].text
    assert out["archive"].status_code == 404, out["archive"].text   # the old name is gone; nothing archived by mistake
    assert not CA.is_archived(DIE, OLDCFG) and not CA.is_archived(DIE, "L60-15t-new")
    assert "L60-15t-new" in _tree(env["a"])[DIE]


def test_while_an_archived_configuration_is_renamed_both_names_read_archived(env, monkeypatch):
    import threading
    _archive(env)
    reached, release, out = threading.Event(), threading.Event(), {}
    _pause_after_files(monkeypatch, reached, release)
    mover = threading.Thread(target=lambda: out.update(rename=client.post(
        f"/api/family/config/{DIE}/{OLDCFG}/rename", headers=env["admin"], json={"name": "L60-15t-new"})))
    mover.start()
    assert reached.wait(20)
    try:
        assert CA.is_archived(DIE, OLDCFG) and CA.is_archived(DIE, "L60-15t-new")   # a reader never sees either name active
    finally:
        release.set()
        mover.join(20)
    assert out["rename"].status_code == 200, out["rename"].text
    assert CA.is_archived(DIE, "L60-15t-new") and not CA.is_archived(DIE, OLDCFG)
    assert [h["to"] for h in CA.history(DIE, "L60-15t-new")] == ["archived"]


def test_a_failed_rename_of_an_archived_configuration_leaves_the_archive_as_it_was(env, monkeypatch):
    from motor_ai_sim import config_move as CM
    _archive(env)
    before = CA.load()["configs"]

    def boom(ctx):
        raise RuntimeError("disk full")
    monkeypatch.setattr(CM, "GLOBAL_STEPS", CM.GLOBAL_STEPS + (boom,))
    r = client.post(f"/api/family/config/{DIE}/{OLDCFG}/rename", headers=env["admin"], json={"name": "L60-15t-new"})
    assert r.status_code == 500
    assert CA.load()["configs"] == before and not CA.is_archived(DIE, "L60-15t-new")


def test_a_link_spelled_in_another_case_refuses_where_the_file_system_ignores_case(monkeypatch):
    clear = {"productions": [], "jobs": []}
    proj = {"title": "P", "status": "design", "linked_motors": [{"die": "d", "config": "c"}]}
    monkeypatch.setitem(CA._CACHE, "ci", True)                      # Windows / macOS: D/c is the file C.yaml of D
    assert CA.refusals("D", "C", projects=[proj], **clear) == [{"code": "project_link", "names": ["P"]}]
    assert CA.refusals("D", "C", projects=[], productions=[{"die": "D", "config": "c"}], jobs=[]) == [
        {"code": "production", "names": ["D/c"]}]
    monkeypatch.setitem(CA._CACHE, "ci", False)                     # case-sensitive: c.yaml is another configuration
    assert CA.refusals("D", "C", projects=[proj], **clear) == []


def test_only_a_snapshot_that_names_an_archived_shared_source_is_hidden(env, monkeypatch):
    """Round 4 of #820: no guessing by geometry.  A row without a source stays listed, with a healthy and with a damaged
    archive - also one whose machine equals the archived one but for wire_parallel 2 (the archived one has the default 1).  A
    named shared source is hidden; a source stamped in a workspace layer is not the shared configuration."""
    from motor_ai_sim import motor_access as MA
    from motor_ai_sim.routes import family as fam
    resolves = {"layer": "shared"}                                          # what the saver's catalog resolves the die to
    monkeypatch.setattr(MA, "_die_layer_of", lambda die, layer=None: layer or resolves["layer"])
    geo = {"num_slots": 12, "num_poles": 10, "stator_diameter": 12.0, "magnet_height": 1.0, "motor_length": 10.0,
           "mat_magnet": "N42SH"}
    rows = (("same machine", None, geo), ("wire_parallel 2", None, {**geo, "wire_parallel": 2}),
            ("named in params", None, {**geo, "die": DIE, "config": OLDCFG}),
            ("stamped", {"die": DIE, "config": OLDCFG}, geo), ("workspace copy", {"die": DIE, "config": OLDCFG}, geo))
    for name, ctx, params in rows:
        monkeypatch.setattr(fam, "_read_ctx", lambda ctx=ctx: ctx)
        resolves["layer"] = "workspace" if name == "workspace copy" else "shared"   # saved from her workspace namesake
        r = client.post("/api/sims/saved", headers=env["a"], json={"name": name, "params": params, "results": {}})
        if r.status_code == 404:
            pytest.skip("saved simulations router not mounted here")
    resolves["layer"] = "shared"
    names = lambda who: sorted(x["name"] for x in client.get("/api/sims/saved", headers=env[who]).json()["sims"])  # noqa: E731
    everything = sorted(n for n, _c, _p in rows)
    assert names("a") == everything
    _archive(env, cfg=OLDCFG)
    kept = ["same machine", "wire_parallel 2", "workspace copy"]
    assert names("a") == kept
    CA.store_path().write_text("{ broken", encoding="utf-8")
    assert names("a") == kept                                               # damaged: unnamed rows stay, named shared hidden


def test_a_new_revision_takes_the_lifecycle_lock_before_the_revision_records(env, monkeypatch, tmp_path):
    """Round 4 of #820 (P1 deadlock): a shared configuration rename holds lifecycle, then the revision records; a new revision
    must take them in the same order, because its copy admits through auto-link (lifecycle)."""
    import threading
    import time
    from fastapi import HTTPException
    from motor_ai_sim import motor_revisions as MR
    from motor_ai_sim.routes import family as fam
    monkeypatch.setenv("MOTRES_MOTOR_REVISIONS_ROOT", str(tmp_path / "revisions"))
    lin = MR.assign(DIE, OLDCFG, actor="t")

    def copy_that_admits(*_a, **_k):                                        # duplicate_config -> auto-link -> admission
        with CA.lifecycle_lock():
            raise HTTPException(418, detail="stop after the admission")
    monkeypatch.setattr(fam, "duplicate_config", copy_that_admits)
    holding, out = threading.Event(), {}

    def rename():                                                           # lifecycle first, then revisions.json
        with CA.lifecycle_lock():
            holding.set()
            time.sleep(0.3)
            lk = MR._lock()
            out["rename"] = lk.acquire(timeout=5)
            if out["rename"]:
                lk.release()

    def revision():
        try:
            MR.new_revision(lin["id"], reason="r", who={"authorization": None}, actor="t")
        except HTTPException as exc:
            out["revision"] = exc.status_code
    t2 = threading.Thread(target=rename)
    t2.start()
    holding.wait(5)
    t1 = threading.Thread(target=revision)
    t1.start()
    t2.join(10)
    t1.join(10)
    assert out == {"rename": True, "revision": 418}


def test_the_archive_touches_only_shared_cards_and_admissions(env, monkeypatch):
    from motor_ai_sim import motor_access as MA
    _archive(env)
    layers = {DIE: "shared", "WSONLY 12_40": "workspace"}
    monkeypatch.setattr(MA, "_die_layer_of", lambda die, layer=None: layer or layers.get(str(die), "shared"))
    shared_card = {"name": f"{DIE} {OLDCFG}"}
    cards = [shared_card, {"name": f"{DIE} {NEWCFG}"}, {"die": "WSONLY 12_40", "config": OLDCFG, "name": "mine"}]
    assert MA._drop_archived_cards(cards) == cards[1:]
    layers[DIE] = "workspace"                                    # the caller's own namesake of the archived die
    assert MA._drop_archived_cards(cards) == cards
    with CA.admission(DIE, OLDCFG):                              # a workspace configuration of that name is admitted
        pass
    with CA.admission(DIE, OLDCFG, layer="published"):
        pass
    with pytest.raises(CA.Archived):
        with CA.admission(DIE, OLDCFG, layer="shared"):
            pass
    CA.store_path().write_text("{ broken", encoding="utf-8")     # damaged: only shared dies lose their cards / admissions
    layers[DIE] = "shared"
    assert MA._drop_archived_cards(cards) == [cards[2]]
    with CA.admission("WSONLY 12_40", OLDCFG):
        pass
    with pytest.raises(CA.Archived):
        with CA.admission(DIE, NEWCFG):
            pass
