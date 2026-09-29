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
* a SHARED die is never unlinked — it is TOMBSTONED
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
    U.create_user(ADMIN, "password-admin", tier="admin", name="Admin")
    U.create_user(A, "password-a", tier="free", name=A_NAME)
    U.create_user(B, "password-b", tier="free", name="Bob")
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


# ── shared layer: tombstone, not unlink ───────────────────────────────────────

def test_admin_deletes_an_empty_shared_die_by_tombstone(env, shared_is_read_only):
    from motor_ai_sim import workspace as W

    assert EMPTY_DIE in _tree_names(env["admin"])
    r = client.delete(f"/api/family/die/{EMPTY_DIE}", headers=env["admin"])
    assert r.status_code == 200, r.text
    assert r.json()["layer"] == "shared"

    # gone from the tree for the admin AND for every other account
    assert EMPTY_DIE not in _tree_names(env["admin"])
    assert EMPTY_DIE not in _tree_names(env["a"])
    assert client.get(f"/api/family/payload/{EMPTY_DIE}/{CFG}",
                      headers=env["a"]).status_code == 404

    # nothing on disk under shared/ moved (shared_is_read_only) — reversible
    assert W.is_tombstoned(EMPTY_DIE)
    assert W.remove_tombstone(EMPTY_DIE) is True
    assert EMPTY_DIE in _tree_names(env["admin"]), (
        "removing the tombstone must bring the die straight back")


def test_admin_delete_of_a_shared_die_with_configs_needs_force(
        env, shared_is_read_only):
    from motor_ai_sim import workspace as W

    r = client.delete(f"/api/family/die/{CFG_DIE}", headers=env["admin"])
    assert r.status_code == 409, r.text
    assert CFG in r.json()["detail"]
    assert not W.is_tombstoned(CFG_DIE)

    r2 = client.delete(f"/api/family/die/{CFG_DIE}?force=true", headers=env["admin"])
    assert r2.status_code == 200, r2.text
    assert r2.json()["deleted_configs"] == [CFG]
    assert CFG_DIE not in _tree_names(env["admin"])
    assert W.is_tombstoned(CFG_DIE)


def test_non_admin_cannot_delete_a_shared_die(env, shared_is_read_only):
    from motor_ai_sim import workspace as W

    r = client.delete(f"/api/family/die/{EMPTY_DIE}", headers=env["a"])
    assert r.status_code == 403, r.text
    assert "catalog admin" in r.json()["detail"]
    assert not W.is_tombstoned(EMPTY_DIE)
    assert EMPTY_DIE in _tree_names(env["a"])


def test_deleting_a_shared_die_is_logged_with_the_admin_email(
        env, shared_is_read_only, caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger="motor_ai_sim.routes.family"):
        r = client.delete(f"/api/family/die/{EMPTY_DIE}", headers=env["admin"])
    assert r.status_code == 200, r.text
    assert any(ADMIN in rec.getMessage() for rec in caplog.records), (
        "the admin's own email must land in the log line, not just 'deleted'")


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
    # … but the shared original is still there for everyone else — this test
    # runs under shared_is_read_only, so any write to it fails the fixture too.
    assert (env["shared"] / "dies" / SHADOW_DIE / "die.yaml").is_file()
    assert SHADOW_DIE in _tree_names(env["a"])
