"""Admin deletion of a die in the SHARED or PUBLISHED catalog layer.

2026-09-24: the owner (an admin) tried to delete the die "40 mm · 12s-14p
high-speed" from the web.  It lives in the SHARED catalog layer, and
``DELETE /api/family/die/{die}`` answered 403 twice — ``_require_writable_die``
only let an admin past when the call ALSO carried ``?layer=shared`` (the query
string the ordinary delete button never sends), so an admin looked exactly
like a stranger to that one check.  Separately, ``/srv/motres/shared`` is
mounted ``:ro`` in the API container in production (``deploy/docker-compose.yml``),
so even a caller who got past the gate could not ``unlink()`` the files.

The fix, and what this module checks:

* an admin's DELETE of a die needs no ``?layer=shared`` opt-in any more —
  ``_require_deletable_die`` in routes/family.py decides purely on
  ``who["is_admin"]`` and the die's current layer;
* (2026-10-10) a DELETE never hides a SHARED die for other users: an admin's
  DELETE removes only the admin's own workspace copy, and a shared-only die
  answers 409 ``die.retire_required``; "Retire for all users" is the separate
  ``POST /die/{die}/retire`` (confirmation repeats the name, audited,
  reversible with ``/restore``), which TOMBSTONES the die
  (``workspace.add_tombstone``): a name recorded in a small JSON file beside
  ``users.json``, reversible with ``workspace.remove_tombstone``, and every
  read-through (tree, resolve, source) hides a tombstoned name;
* a PUBLISHED die (someone else's publish, but writable on disk) is moved to
  ``<published_root>/.trash/<owner>/<die>-<stamp>/`` instead of unlinked, for
  the same reversibility;
* a non-admin still gets 403, with a plain reason instead of the old generic
  read-only message;
* a WORKSPACE die deletes exactly as it always did, and if a shared/published
  die of the same name still exists behind it, the response says so.
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from motor_ai_sim.api import app

_ROOT = Path(__file__).resolve().parents[1]
_REAL_USERS = _ROOT / "config" / "users.json"
_REAL_DIES = _ROOT / "config" / "dies"

EMPTY_DIE = "EMPTYSHARED 40"
CFG_DIE = "CFGSHARED 40"
SHADOW_DIE = "SHADOWED 40"
CFG = "L40"
DUTY = "rated"

ADMIN = "admin@example.com"
A = "alice@example.com"
B = "bob@example.com"
A_NAME = "Alice"

client = TestClient(app)


def _hash_tree(root: Path) -> dict:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def _die_doc(name: str) -> str:
    return yaml.safe_dump({
        "name": name, "locked": False, "created": "2026-09-15T10:00:00",
        "geometry": {"num_slots": 12, "num_poles": 14, "stator_diameter": 40.0,
                     "magnet_height": 3.0, "motor_length": 40.0},
    }, sort_keys=False, allow_unicode=True)


def _cfg_doc(die: str) -> str:
    return yaml.safe_dump({
        "name": CFG, "die": die, "role": "motor",
        "geometry_overrides": {"motor_length": 40.0, "wire_height": 1.0},
        "winding": {"connection": "star"},
        "materials": {"magnet": "N42SH", "stator_core": "20SW1200"},
        "duties": [{"name": DUTY, "mode": "motor",
                    "saved_at": "2026-09-15T10:00:00",
                    "current_arms": 85.0, "rpm": 6000.0, "gamma_deg": 12.0,
                    "note": ""}],
    }, sort_keys=False, allow_unicode=True)


@pytest.fixture(scope="module", autouse=True)
def _real_files_untouched():
    """The real registry and catalog come out byte-identical, as everywhere
    else in this suite (see tests/test_workspace_layers.py)."""
    before_users = _REAL_USERS.read_bytes() if _REAL_USERS.exists() else None
    before_dies = {p: p.stat().st_mtime_ns
                   for p in sorted(_REAL_DIES.rglob("*")) if p.is_file()}
    yield
    after_users = _REAL_USERS.read_bytes() if _REAL_USERS.exists() else None
    after_dies = {p: p.stat().st_mtime_ns
                  for p in sorted(_REAL_DIES.rglob("*")) if p.is_file()}
    assert before_users == after_users, "config/users.json was modified by a test"
    assert before_dies == after_dies, "config/dies was modified by a test"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from motor_ai_sim import auth
    from motor_ai_sim import users as U
    from motor_ai_sim import workspace as W
    from motor_ai_sim.routes import family as fam

    tree = tmp_path / "srv"
    shared, published, works = (tree / "shared", tree / "published",
                                tree / "workspaces")
    for d in (shared / "dies", published, works):
        d.mkdir(parents=True)

    (shared / "dies" / EMPTY_DIE).mkdir()
    (shared / "dies" / EMPTY_DIE / "die.yaml").write_text(
        _die_doc(EMPTY_DIE), encoding="utf-8")

    (shared / "dies" / CFG_DIE).mkdir()
    (shared / "dies" / CFG_DIE / "die.yaml").write_text(
        _die_doc(CFG_DIE), encoding="utf-8")
    (shared / "dies" / CFG_DIE / f"{CFG}.yaml").write_text(
        _cfg_doc(CFG_DIE), encoding="utf-8")

    (shared / "dies" / SHADOW_DIE).mkdir()
    (shared / "dies" / SHADOW_DIE / "die.yaml").write_text(
        _die_doc(SHADOW_DIE), encoding="utf-8")

    shutil.copy2(_ROOT / "config" / "motor_config.yaml", shared / "motor_config.yaml")

    monkeypatch.setenv("WORKSPACES_ROOT", str(works))
    monkeypatch.setenv("SHARED_ROOT", str(shared))
    monkeypatch.setenv("PUBLISHED_ROOT", str(published))
    monkeypatch.delenv("CATALOG_GRANT_ALL_REGISTERED", raising=False)
    # `_DIES_DIR` must stay ABSENT: a value in the module dict is the
    # single-layer override and would switch layering off entirely.
    monkeypatch.delitem(fam.__dict__, "_DIES_DIR", raising=False)
    fam._TREE_CACHE.clear()

    users_file = tmp_path / "users.json"
    shutil.copy2(_REAL_USERS, users_file)
    monkeypatch.setattr(U, "_USERS_FILE", users_file)
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)
    U.create_user(ADMIN, "password-admin", role="admin", name="Admin")
    U.create_user(A, "password-a", role="user", name=A_NAME)
    U.create_user(B, "password-b", role="user", name="Bob")
    U.set_motor_grants(A, all_motors=True)
    U.set_motor_grants(B, all_motors=True)

    ids = {e: W.workspace_id(e) for e in (ADMIN, A, B)}

    # A die of the SAME NAME sitting in the admin's own workspace too, so the
    # "which one did you just delete" note (AGENTS.md item 3) is exercised
    # for real rather than assumed.
    admin_ws_dies = works / ids[ADMIN] / "dies"
    admin_ws_dies.mkdir(parents=True)
    (admin_ws_dies / SHADOW_DIE).mkdir()
    (admin_ws_dies / SHADOW_DIE / "die.yaml").write_text(
        _die_doc(SHADOW_DIE), encoding="utf-8")

    # config/users.json's redirect for the tombstone file: same process config
    # directory `users.json` itself lives in, so it moves with `MOTOR_AI_SIM_CONFIG`
    # exactly the way the module docstring says it must.
    monkeypatch.setattr(
        "motor_ai_sim.config.DEFAULT_CONFIG_PATH",
        str(tmp_path / "identity" / "motor_config.yaml"))
    (tmp_path / "identity").mkdir(exist_ok=True)

    yield {
        "tree": tree, "shared": shared, "published": published, "works": works,
        "admin": {"Authorization": f"Bearer {U.issue_token(ADMIN)}"},
        "a": {"Authorization": f"Bearer {U.issue_token(A)}"},
        "b": {"Authorization": f"Bearer {U.issue_token(B)}"},
        "ids": ids,
        "shared_before": _hash_tree(shared),
    }
    fam._TREE_CACHE.clear()


@pytest.fixture()
def shared_is_read_only(env):
    """The trap: an admin delete must not move one byte under shared/ — that
    mount is `:ro` in production and a write there is not a bug, it is an
    outage."""
    yield
    assert _hash_tree(env["shared"]) == env["shared_before"], (
        "an admin delete wrote to the read-only shared mount instead of "
        "tombstoning it")


def _tree_names(headers) -> set:
    r = client.get("/api/family/tree", headers=headers)
    assert r.status_code == 200, r.text
    return {d["name"] for d in r.json()["dies"]}


# ── shared layer: DELETE never hides it; "Retire for all users" does ─────────
# Owner 2026-10-10: deleting must not hide a shared die for other users.
# Retiring is a separate, explicit, confirmed, audited and reversible action.

def test_admin_delete_of_a_shared_die_is_refused_and_points_at_retire(
        env, shared_is_read_only):
    from motor_ai_sim import workspace as W

    for die in (EMPTY_DIE, CFG_DIE):
        for q in ("", "?force=true"):
            r = client.delete(f"/api/family/die/{die}{q}", headers=env["admin"])
            assert r.status_code == 409, r.text
            assert r.json().get("code") == "die.retire_required", r.text
            assert "Retire for all users" in r.json()["detail"]
            assert not W.is_tombstoned(die)
            assert die in _tree_names(env["a"])


def test_admin_retires_a_shared_die_with_confirmation_and_restores_it(
        env, shared_is_read_only):
    from motor_ai_sim import workspace as W

    aud = client.get(f"/api/family/die/{CFG_DIE}/audience", headers=env["admin"])
    assert aud.status_code == 200, aud.text
    # admin + A + B (both with an `all` grant) can see it
    assert aud.json() == {"die": CFG_DIE, "shared": True, "retired": False,
                          "users": 3}

    # the confirmation must repeat the die name
    bad = client.post(f"/api/family/die/{CFG_DIE}/retire", headers=env["admin"],
                      json={"confirm": "something else"})
    assert bad.status_code == 422, bad.text
    assert not W.is_tombstoned(CFG_DIE)

    r = client.post(f"/api/family/die/{CFG_DIE}/retire", headers=env["admin"],
                    json={"confirm": CFG_DIE})
    assert r.status_code == 200, r.text
    assert r.json()["retired"] is True and r.json()["users"] == 3
    assert CFG_DIE not in _tree_names(env["admin"])
    assert CFG_DIE not in _tree_names(env["a"])
    assert client.get(f"/api/family/payload/{CFG_DIE}/{CFG}",
                      headers=env["a"]).status_code == 404
    listed = client.get("/api/family/retired", headers=env["admin"]).json()["dies"]
    assert [d["die"] for d in listed] == [CFG_DIE]

    # reversible: nothing under shared/ moved (shared_is_read_only)
    back = client.post(f"/api/family/die/{CFG_DIE}/restore", headers=env["admin"])
    assert back.status_code == 200, back.text
    assert not W.is_tombstoned(CFG_DIE)
    assert CFG_DIE in _tree_names(env["a"])


def test_retire_and_restore_are_in_the_admin_audit(env, shared_is_read_only,
                                                   tmp_path, monkeypatch, caplog):
    import json
    import logging
    audit = tmp_path / "admin_audit.jsonl"
    monkeypatch.setenv("ADMIN_AUDIT_FILE", str(audit))
    with caplog.at_level(logging.WARNING, logger="motor_ai_sim.routes.family"):
        assert client.post(f"/api/family/die/{EMPTY_DIE}/retire",
                           headers=env["admin"],
                           json={"confirm": EMPTY_DIE}).status_code == 200
        assert client.post(f"/api/family/die/{EMPTY_DIE}/restore",
                           headers=env["admin"]).status_code == 200
    rows = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]
    assert [(r["action"], r["target"], r["actor"]) for r in rows] == [
        ("die.retire", EMPTY_DIE, ADMIN), ("die.restore", EMPTY_DIE, ADMIN)]
    assert rows[0]["details"]["users"] == 3
    assert any(ADMIN in rec.getMessage() for rec in caplog.records), (
        "the admin's own email must land in the log line")


def test_non_admin_cannot_delete_or_retire_a_shared_die(env, shared_is_read_only):
    from motor_ai_sim import workspace as W

    r = client.delete(f"/api/family/die/{EMPTY_DIE}", headers=env["a"])
    assert r.status_code == 403, r.text
    assert "only a verified signed-in admin may delete it" in r.json()["detail"]
    for path, body in (("retire", {"confirm": EMPTY_DIE}), ("restore", None)):
        rr = client.post(f"/api/family/die/{EMPTY_DIE}/{path}", headers=env["a"],
                         json=body)
        assert rr.status_code == 403, rr.text
    assert client.get(f"/api/family/die/{EMPTY_DIE}/audience",
                      headers=env["a"]).status_code == 403
    assert not W.is_tombstoned(EMPTY_DIE)
    assert EMPTY_DIE in _tree_names(env["a"])


# ── published layer: reversible trash-move, not unlink ───────────────────────

def test_admin_deletes_a_published_die_by_moving_it_to_trash(env):
    from motor_ai_sim import workspace as W

    # A publishes a die out of their own workspace (the ordinary flow).
    r = client.post("/api/family/die", headers=env["a"], json={"name": "ALICEDIE 30"})
    assert r.status_code == 200, r.text
    rp = client.post("/api/family/publish/ALICEDIE 30", headers=env["a"], json={})
    assert rp.status_code == 200, rp.text
    label = rp.json()["label"]
    assert label.endswith(f"· by {A_NAME}")

    pub_dir = env["published"] / env["ids"][A] / "ALICEDIE 30"
    assert pub_dir.is_dir()

    rd = client.delete(f"/api/family/die/{label}", headers=env["admin"])
    assert rd.status_code == 200, rd.text
    assert rd.json()["layer"] == "published"

    # not unlinked — moved aside, reversibly
    assert not pub_dir.exists()
    trash = env["published"] / ".trash" / env["ids"][A]
    moved = list(trash.glob("ALICEDIE 30-*"))
    assert len(moved) == 1, "expected exactly one trashed copy"
    assert (moved[0] / "die.yaml").is_file()

    # gone from the community listing for everyone
    assert label not in _tree_names(env["b"])


def test_non_admin_cannot_delete_someone_elses_published_die(env):
    r = client.post("/api/family/die", headers=env["a"], json={"name": "ALICEDIE2 30"})
    assert r.status_code == 200, r.text
    label = client.post("/api/family/publish/ALICEDIE2 30",
                        headers=env["a"], json={}).json()["label"]

    rd = client.delete(f"/api/family/die/{label}", headers=env["b"])
    assert rd.status_code == 403, rd.text
    assert "catalog admin" in rd.json()["detail"]
    assert (env["published"] / env["ids"][A] / "ALICEDIE2 30").is_dir()


# ── workspace layer: unchanged, and honest about a shadowed shared die ───────

def test_workspace_delete_is_unchanged_and_leaves_shared_untouched(
        env, shared_is_read_only):
    r = client.delete(f"/api/family/die/{SHADOW_DIE}", headers=env["admin"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["layer"] == "workspace"
    assert "note" in body and "shared" in body["note"]

    # the admin's own workspace copy is gone …
    assert not (env["works"] / env["ids"][ADMIN] / "dies" / SHADOW_DIE).exists()
    from motor_ai_sim import workspace as W
    assert not W.is_tombstoned(SHADOW_DIE), (
        "deleting the admin's workspace copy must not hide the shared die")
    # … but the shared original is still there for everyone else — this test
    # runs under shared_is_read_only, so any write to it fails the fixture too.
    assert (env["shared"] / "dies" / SHADOW_DIE / "die.yaml").is_file()
    assert SHADOW_DIE in _tree_names(env["a"])
