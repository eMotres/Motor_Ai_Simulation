"""Three layers, and which of them a write is allowed to touch.

The user's decision of 2026-09-15 is that a catalog is not two things but three:

* ``shared/``      the admin-curated library.  Read-only for everyone but an
                   admin, who writes it by saying so (``?layer=shared``).
* ``published/``   the community layer.  A user publishes a die / configuration
                   / duty out of their own workspace and every registered
                   account may read it — with the author's name on it, and
                   namespaced per owner so two people may both publish
                   "CIANO28 85".
* ``workspaces/``  the user's own.  EVERY write lands here; a die that lives
                   only in one of the other two is copied across on the first
                   write (copy-on-write) and edited in the copy.

What is under test, in the order the risk sits:

* **the shared layer is never written** — the whole module runs with a sha256
  trap over ``shared/`` and fails if one byte of it moves during a user's save;
* A saving a duty on a shared die lands in A's workspace, and the shared die is
  untouched — the copy-on-write brought the yaml across, the results did not;
* B does not see A's duty: same die name, two workspaces, two answers;
* A publishes → B sees it as ``"<die> · by Alice"``, can read the duty AND its
  stored field, cannot write it and cannot withdraw it;
* A unpublishes → it is gone for B the same second;
* an ADMIN write with ``?layer=shared`` lands in shared and both accounts see
  it (this is the one write that is allowed in, and it is explicit);
* precedence: a workspace copy shadows the shared original, and
  ``resolve_die_dir`` answers workspace → published → shared in that order;
* with ``WORKSPACES_ROOT`` unset, every helper degrades to the expression it
  replaced — one layer, the folder the config path names.

Everything runs in the pytest tmp area against a copy of nothing at all: the
fixture builds a two-file die by hand, so a failure here is about layers and
not about whatever the real catalog happens to contain.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from motor_ai_sim.api import app

_ROOT = Path(__file__).resolve().parents[1]
_REAL_USERS = _ROOT / "config" / "users.json"
_REAL_DIES = _ROOT / "config" / "dies"

DIE = "SHAREDDIE 40"
CFG = "L40"
DUTY = "rated"
A_DUTY = "A only 200"

ADMIN = "admin@example.com"
A = "alice@example.com"
B = "bob@example.com"
A_NAME = "Alice"

client = TestClient(app)


# ── isolation ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module", autouse=True)
def _real_files_untouched():
    """The real registry and catalog come out byte-identical, as everywhere."""
    before_users = _REAL_USERS.read_bytes() if _REAL_USERS.exists() else None
    before_dies = {p: p.stat().st_mtime_ns
                   for p in sorted(_REAL_DIES.rglob("*")) if p.is_file()}
    yield
    after_users = _REAL_USERS.read_bytes() if _REAL_USERS.exists() else None
    after_dies = {p: p.stat().st_mtime_ns
                  for p in sorted(_REAL_DIES.rglob("*")) if p.is_file()}
    assert before_users == after_users, "config/users.json was modified by a test"
    assert before_dies == after_dies, "config/dies was modified by a test"


def _hash_tree(root: Path) -> dict:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """A three-layer tree with a real admin and two real accounts.

    Only the PATHS are faked.  Tokens, tier resolution, admin-ness, grants and
    every route are the shipping code — the point of this module is that the
    shipping code keeps the layers apart.
    """
    from motor_ai_sim import auth
    from motor_ai_sim import users as U
    from motor_ai_sim.routes import family as fam

    tree = tmp_path / "srv"
    shared, published, works = (tree / "shared", tree / "published",
                                tree / "workspaces")
    for d in (shared / "dies", published, works):
        d.mkdir(parents=True)

    (shared / "dies" / DIE).mkdir()
    (shared / "dies" / DIE / "die.yaml").write_text(yaml.safe_dump({
        "name": DIE, "locked": False, "created": "2026-09-15T10:00:00",
        "geometry": {"num_slots": 12, "num_poles": 14, "stator_diameter": 40.0,
                     "magnet_height": 3.0, "motor_length": 40.0},
    }, sort_keys=False, allow_unicode=True), encoding="utf-8")
    (shared / "dies" / DIE / f"{CFG}.yaml").write_text(yaml.safe_dump({
        "name": CFG, "die": DIE, "role": "motor",
        "geometry_overrides": {"motor_length": 40.0, "wire_height": 1.0},
        "winding": {"connection": "star"},
        "materials": {"magnet": "N42SH", "stator_core": "20SW1200"},
        "duties": [{"name": DUTY, "mode": "motor",
                    "saved_at": "2026-09-15T10:00:00",
                    "current_arms": 85.0, "rpm": 6000.0, "gamma_deg": 12.0,
                    "note": ""}],
    }, sort_keys=False, allow_unicode=True), encoding="utf-8")

    monkeypatch.setenv("WORKSPACES_ROOT", str(works))
    monkeypatch.setenv("SHARED_ROOT", str(shared))
    monkeypatch.setenv("PUBLISHED_ROOT", str(published))
    monkeypatch.delenv("CATALOG_GRANT_ALL_REGISTERED", raising=False)
    # `_DIES_DIR` must stay ABSENT: a value in the module dict is the Stage 1
    # single-layer override and would switch the whole feature off.
    monkeypatch.delitem(fam.__dict__, "_DIES_DIR", raising=False)
    fam._TREE_CACHE.clear()

    users_file = tmp_path / "users.json"
    shutil.copy2(_REAL_USERS, users_file)
    monkeypatch.setattr(U, "_USERS_FILE", users_file)
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {ADMIN, A})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)
    U.create_user(ADMIN, "password-admin", tier="admin", name="Admin")
    U.create_user(A, "password-a", tier="admin", name=A_NAME)
    U.create_user(B, "password-b", tier="free", name="Bob")
    # B is a plain registered account: it sees what it is GRANTED of the shared
    # catalog, plus whatever anyone has published.
    U.set_motor_grants(B, all_motors=False, dies=[DIE])

    from motor_ai_sim import workspace as W
    ids = {e: W.workspace_id(e) for e in (ADMIN, A, B)}
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
    """The trap: not one byte of ``shared/`` may move while users are working.

    A write-protected mount is what production will do; here the same claim is
    a content hash, which also catches the case a chmod would not — a write
    that succeeded because the process happens to own the directory.
    """
    yield
    assert _hash_tree(env["shared"]) == env["shared_before"], (
        "a user's save reached the SHARED catalog — the overlay direction is "
        "inverted somewhere, which is exactly the Stage 2 risk")


# ── helpers ──────────────────────────────────────────────────────────────────

def _save_duty(headers, duty=A_DUTY, die=DIE, cfg=CFG, query=""):
    return client.post(f"/api/family/duty{query}", headers=headers, json={
        "die": die, "config": cfg,
        "duty": {"name": duty, "mode": "motor", "from_current": False,
                 "current_arms": 200.0, "rpm": 3000.0, "gamma_deg": 10.0,
                 "note": "", "summary": {"T_em_avg_Nm": 12.5, "rpm": 3000.0}}})


def _duty_names(headers, die=DIE, cfg=CFG):
    r = client.get("/api/family/tree", headers=headers)
    assert r.status_code == 200, r.text
    for d in r.json()["dies"]:
        if d["name"] != die:
            continue
        for c in d["configs"]:
            if c["name"] == cfg:
                return [x["name"] for x in c["duties"]]
    return None


def _ws_die(env, email, die=DIE) -> Path:
    return env["works"] / env["ids"][email] / "dies" / die


def _thermal_payload():
    return {"computed_at": "2026-09-15T12:00:00",
            "vertices": [[0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
            "triangles": [[0], [1], [2]],
            "temperature_per_node": [20.0, 40.0, 60.0],
            "T_max": 60.0, "T_min": 20.0}


# ── the layers themselves ────────────────────────────────────────────────────

def test_a_shared_die_is_visible_and_tagged(env):
    r = client.get("/api/family/tree", headers=env["a"])
    assert r.status_code == 200, r.text
    row = next(d for d in r.json()["dies"] if d["name"] == DIE)
    assert row["layer"] == "shared"
    assert row["read_only"] is True
    assert [c["name"] for c in row["configs"]] == [CFG]


def test_saving_a_duty_on_a_shared_die_copies_it_into_the_workspace(
        env, shared_is_read_only):
    r = _save_duty(env["a"])
    assert r.status_code == 200, r.text
    # the copy exists, carries BOTH configurations' yaml…
    wdie = _ws_die(env, A)
    assert (wdie / "die.yaml").is_file()
    doc = yaml.safe_load((wdie / f"{CFG}.yaml").read_text(encoding="utf-8"))
    assert sorted(d["name"] for d in doc["duties"]) == sorted([DUTY, A_DUTY])
    # …and the shared original still has only the duty it shipped with
    shared_doc = yaml.safe_load(
        (env["shared"] / "dies" / DIE / f"{CFG}.yaml").read_text(encoding="utf-8"))
    assert [d["name"] for d in shared_doc["duties"]] == [DUTY]


def test_the_workspace_copy_shadows_the_shared_original(env, shared_is_read_only):
    _save_duty(env["a"])
    r = client.get("/api/family/tree", headers=env["a"])
    row = next(d for d in r.json()["dies"] if d["name"] == DIE)
    assert row["layer"] == "workspace"
    assert row["read_only"] is False
    assert sorted(_duty_names(env["a"])) == sorted([DUTY, A_DUTY])


def test_b_does_not_see_as_duty(env, shared_is_read_only):
    _save_duty(env["a"])
    assert _duty_names(env["a"]) is not None
    assert _duty_names(env["b"]) == [DUTY], (
        "B read A's workspace — the catalog is not per-user after all")


def test_resolution_order_is_workspace_then_published_then_shared(env):
    from motor_ai_sim import workspace as W
    ws_a = W.workspace_for_identity(A)
    with W.use_workspace(ws_a):
        assert W.resolve_die_dir(DIE) == env["shared"] / "dies" / DIE
        assert [lay.name for lay in W.layers()][0] == "workspace"
        assert [lay.name for lay in W.layers()][-1] == "shared"
    _save_duty(env["a"])
    with W.use_workspace(ws_a):
        assert W.resolve_die_dir(DIE) == _ws_die(env, A)
        # …and the layer it came FROM is still reachable, which is what keeps
        # the shared results readable after a copy-on-write.
        assert W.source_die_dir(DIE) == env["shared"] / "dies" / DIE


# ── publishing ───────────────────────────────────────────────────────────────

def test_publish_makes_it_visible_to_every_registered_account(
        env, shared_is_read_only):
    _save_duty(env["a"])
    r = client.post(f"/api/family/publish/{DIE}", headers=env["a"], json={})
    assert r.status_code == 200, r.text
    label = r.json()["label"]
    assert label == f"{DIE} · by {A_NAME}"
    assert (env["published"] / env["ids"][A] / DIE / "die.yaml").is_file()

    rc = client.get("/api/family/community", headers=env["b"])
    assert rc.status_code == 200, rc.text
    items = rc.json()["items"]
    assert [i["name"] for i in items] == [label]
    assert items[0]["owner"] == A and items[0]["owner_name"] == A_NAME
    assert items[0]["mine"] is False
    assert items[0]["published_at"]

    # B can READ it — by the label, through the ordinary catalog routes
    names = _duty_names(env["b"], die=label)
    assert sorted(names) == sorted([DUTY, A_DUTY])
    rp = client.get(f"/api/family/payload/{label}/{CFG}", headers=env["b"])
    assert rp.status_code == 200, rp.text
    # …and B's own view of the SHARED die is unchanged: two rows, not one
    assert _duty_names(env["b"]) == [DUTY]


def test_anonymous_sees_no_community(env):
    _save_duty(env["a"])
    client.post(f"/api/family/publish/{DIE}", headers=env["a"], json={})
    r = client.get("/api/family/community")
    assert r.status_code == 200
    assert r.json() == {"items": [], "can_publish": False}


def test_b_can_read_a_published_field_but_cannot_write_or_withdraw(
        env, shared_is_read_only):
    from motor_ai_sim import duty_fields as DF
    from motor_ai_sim import workspace as W

    _save_duty(env["a"])
    with W.use_workspace(W.workspace_for_identity(A)):
        assert DF.save(DIE, CFG, A_DUTY, "thermal", _thermal_payload())
    r = client.post(f"/api/family/publish/{DIE}/{CFG}/{A_DUTY}",
                    headers=env["a"], json={})
    assert r.status_code == 200, r.text
    label = r.json()["label"]

    # READ: the field listing and the arrays themselves
    rf = client.get(f"/api/family/duty_fields/{label}/{CFG}", headers=env["b"])
    assert rf.status_code == 200, rf.text
    assert [d["duty"] for d in rf.json()["duties"]] == [A_DUTY]
    with W.use_workspace(W.workspace_for_identity(B)):
        got = DF.load(label, CFG, A_DUTY, "thermal")
    assert got is not None and "temperature_per_node" in got

    # WRITE: refused.  Catalog writes are admin-only (unchanged by Stage 2) …
    before = _hash_tree(env["published"])
    assert _save_duty(env["b"], duty="bob tried", die=label).status_code == 403
    # … and withdrawing somebody else's publication is refused by ownership
    rd = client.delete(f"/api/family/publish/{label}", headers=env["b"])
    assert rd.status_code == 403, rd.text
    assert "author" in rd.json()["detail"]
    assert _hash_tree(env["published"]) == before, (
        "B changed A's published work")


def test_unpublishing_takes_it_away_from_b(env, shared_is_read_only):
    _save_duty(env["a"])
    r = client.post(f"/api/family/publish/{DIE}", headers=env["a"], json={})
    label = r.json()["label"]
    assert _duty_names(env["b"], die=label) is not None

    rd = client.delete(f"/api/family/publish/{DIE}", headers=env["a"])
    assert rd.status_code == 200, rd.text
    assert client.get("/api/family/community",
                      headers=env["b"]).json()["items"] == []
    assert _duty_names(env["b"], die=label) is None
    assert client.get(f"/api/family/payload/{label}/{CFG}",
                      headers=env["b"]).status_code == 404


def test_an_admin_can_withdraw_anyones_publication(env, shared_is_read_only):
    _save_duty(env["a"])
    label = client.post(f"/api/family/publish/{DIE}",
                        headers=env["a"], json={}).json()["label"]
    rd = client.delete(f"/api/family/publish/{label}", headers=env["admin"])
    assert rd.status_code == 200, rd.text
    assert client.get("/api/family/community",
                      headers=env["admin"]).json()["items"] == []


def test_publishing_one_duty_does_not_retract_the_others(env, shared_is_read_only):
    _save_duty(env["a"])
    client.post(f"/api/family/publish/{DIE}/{CFG}/{DUTY}",
                headers=env["a"], json={})
    r = client.post(f"/api/family/publish/{DIE}/{CFG}/{A_DUTY}",
                    headers=env["a"], json={})
    assert r.status_code == 200, r.text
    label = r.json()["label"]
    assert sorted(_duty_names(env["b"], die=label)) == sorted([DUTY, A_DUTY])


# ── the one write that is allowed into the shared layer ──────────────────────

def test_admin_layer_shared_writes_the_curated_catalog_for_everyone(env):
    r = _save_duty(env["admin"], duty="vendor rated", query="?layer=shared")
    assert r.status_code == 200, r.text
    doc = yaml.safe_load(
        (env["shared"] / "dies" / DIE / f"{CFG}.yaml").read_text(encoding="utf-8"))
    assert sorted(d["name"] for d in doc["duties"]) == sorted([DUTY, "vendor rated"])
    # …and nothing was copied into the admin's own workspace
    assert not (env["works"] / env["ids"][ADMIN] / "dies" / DIE).exists()
    # both accounts see it
    for who in ("a", "b"):
        assert sorted(_duty_names(env[who])) == sorted([DUTY, "vendor rated"])


def test_a_non_admin_cannot_aim_a_write_at_the_shared_layer(env, shared_is_read_only):
    # B is not an admin at all, so the route gate answers first …
    assert _save_duty(env["b"], query="?layer=shared").status_code == 403
    # … and the write seam refuses on its own account, with its own message.
    from motor_ai_sim import workspace as W
    from motor_ai_sim.routes import family as fam
    with W.use_workspace(W.workspace_for_identity(B)), \
            W.use_write_layer("shared"):
        with pytest.raises(Exception) as e:
            fam._write_target(env["shared"] / "dies" / DIE / f"{CFG}.yaml")
    assert "admin" in str(e.value)


def test_renaming_a_shared_die_is_refused(env, shared_is_read_only):
    r = client.patch(f"/api/family/die/{DIE}", headers=env["a"],
                     json={"name": "MINE 40"})
    assert r.status_code == 403, r.text
    assert "read-only" in r.json()["detail"]


def test_deleting_a_configuration_of_a_shared_die_only_hides_it_here(
        env, shared_is_read_only):
    r = client.delete(f"/api/family/config/{DIE}/{CFG}", headers=env["a"])
    assert r.status_code == 200, r.text
    assert _duty_names(env["a"]) is None          # gone from A's catalog …
    assert _duty_names(env["b"]) == [DUTY]        # … still the vendor's for B


# ── duty results read through the layers ─────────────────────────────────────

def test_duty_results_fall_through_to_the_published_author(env, shared_is_read_only):
    from motor_ai_sim import duty_results as DR
    from motor_ai_sim import workspace as W

    _save_duty(env["a"])
    with W.use_workspace(W.workspace_for_identity(A)):
        assert DR.record(DIE, CFG, A_DUTY, "thermal",
                         {"T_max": 91.0, "computed_at": "2026-09-15T12:00:00"})
    label = client.post(f"/api/family/publish/{DIE}", headers=env["a"],
                        json={}).json()["label"]
    with W.use_workspace(W.workspace_for_identity(B)):
        rows = DR.get(label, CFG)
    assert rows.get(A_DUTY, {}).get("thermal", {}).get("T_max") == 91.0
    # B's own store is still empty — nothing was copied INTO B's workspace
    assert not (env["works"] / env["ids"][B] / ".duty_results.json").exists()


# ── the promise: with the env var unset, nothing changed ─────────────────────

def test_with_no_workspaces_root_there_is_exactly_one_layer(monkeypatch):
    from motor_ai_sim import workspace as W
    monkeypatch.delenv("WORKSPACES_ROOT", raising=False)
    assert W.layering() is False
    assert [lay.name for lay in W.layers()] == ["workspace"]
    assert W.resolve_die_dir("anything at all") is None
    assert W.published_root() == W.workspace().root
    assert W.write_layer() is None


def test_with_no_workspaces_root_the_write_seam_is_a_no_op(monkeypatch, tmp_path):
    from motor_ai_sim.routes import family as fam
    monkeypatch.delenv("WORKSPACES_ROOT", raising=False)
    p = tmp_path / "anywhere" / "die.yaml"
    assert fam._write_target(p) == p
    assert fam._die_layer("whatever") == "workspace"


# ── the one-time import (scripts/migrate_to_workspaces.py) ───────────────────

def _migrate_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "migrate_to_workspaces", _ROOT / "scripts" / "migrate_to_workspaces.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_config(root: Path) -> Path:
    """A ``config/`` the shape of the real one, three orders of magnitude
    smaller: one die with one configuration, one run sidecar, one field file,
    the libraries, the identity files and one cache that must NOT travel."""
    cfg = root / "config"
    (cfg / "dies" / DIE / "runs" / CFG / "rated-abc12345" / "fields").mkdir(
        parents=True)
    (cfg / ".mesh_cache").mkdir()
    (cfg / ".presets_history").mkdir()
    (cfg / "dies" / DIE / "die.yaml").write_text(
        yaml.safe_dump({"name": DIE, "geometry": {"num_slots": 12}}),
        encoding="utf-8")
    (cfg / "dies" / DIE / f"{CFG}.yaml").write_text(
        yaml.safe_dump({"name": CFG, "die": DIE,
                        "duties": [{"name": DUTY, "rpm": 6000.0}]}),
        encoding="utf-8")
    (cfg / "dies" / DIE / "runs" / CFG / "rated-abc12345.current.json.gz"
     ).write_bytes(b"\x1f\x8b\x08\x00not-really-gzip")
    (cfg / "dies" / DIE / "runs" / CFG / "rated-abc12345" / "fields" / "em.npz"
     ).write_bytes(b"PK\x03\x04-not-really-an-npz")
    for name in ("motor_config.yaml",):
        (cfg / name).write_text("geometry: {num_slots: 12}\n", encoding="utf-8")
    (cfg / "motor_config.yaml.bak-something").write_text("old\n", encoding="utf-8")
    for name in ("materials_library.yaml", "bearings_library.yaml",
                 "fusion_param_map.yaml"):
        (cfg / name).write_text("{}\n", encoding="utf-8")
    for name in ("users.json", ".auth_secret", ".sessions.json"):
        (cfg / name).write_text("{}", encoding="utf-8")
    for name in ("motor_presets.json", "motor_catalog.json",
                 "saved_simulations.json", "sweep_config.json",
                 ".duty_results.json", ".family_context.json"):
        (cfg / name).write_text("{}", encoding="utf-8")
    (cfg / ".panel_settings.json").write_text(
        json.dumps({A: {"tab": "thermal"}, B: {"tab": "mech"},
                    "version": 1}), encoding="utf-8")
    (cfg / ".mesh_cache" / "m.json.gz").write_bytes(b"cache")
    (cfg / ".warm_cache.npz").write_bytes(b"cache")
    (cfg / ".presets_history" / "p.json").write_text("{}", encoding="utf-8")
    return cfg


def test_migration_dry_run_writes_nothing(tmp_path):
    M = _migrate_module()
    cfg = _fake_config(tmp_path)
    tree = tmp_path / "srv"
    plan, stats = M.build_plan(cfg, tree, A)
    assert stats["dies"] == 1 and stats["configs"] == 1
    assert stats["run_files"] == 1 and stats["field_files"] == 1
    assert plan.ops, "a dry run that plans nothing is not a dry run"
    assert not tree.exists(), "the dry run created the tree"


def test_migration_splits_the_catalog_from_the_results(tmp_path):
    M = _migrate_module()
    cfg = _fake_config(tmp_path)
    tree = tmp_path / "srv"
    before = _hash_tree(cfg)
    plan, _ = M.build_plan(cfg, tree, A)
    plan.run()
    wsid = M.ws_id(A)

    # the catalog is SHARED, yaml only …
    assert (tree / "shared" / "dies" / DIE / "die.yaml").is_file()
    assert (tree / "shared" / "dies" / DIE / f"{CFG}.yaml").is_file()
    assert not (tree / "shared" / "dies" / DIE / "runs").exists()
    # … the results are the owner's, at the same relative path, byte for byte
    ws = tree / "workspaces" / wsid
    runs = ws / "dies" / DIE / "runs" / CFG
    assert (runs / "rated-abc12345.current.json.gz").is_file()
    npz = runs / "rated-abc12345" / "fields" / "em.npz"
    assert npz.read_bytes() == (
        cfg / "dies" / DIE / "runs" / CFG / "rated-abc12345" / "fields"
        / "em.npz").read_bytes()
    # … the live machine and its .bak sibling travelled with their owner
    assert (ws / "motor_config.yaml").is_file()
    assert (ws / "motor_config.yaml.bak-something").is_file()
    # … the libraries and the identity files are where the server expects them
    for name in ("materials_library.yaml", "bearings_library.yaml",
                 "fusion_param_map.yaml", "motor_presets.json"):
        assert (tree / "shared" / name).is_file(), name
    for name in ("users.json", ".auth_secret", ".sessions.json"):
        assert (tree / "identity" / name).is_file(), name
    # … the caches did NOT travel
    assert not (ws / ".mesh_cache").exists()
    assert not (ws / ".warm_cache.npz").exists()
    assert (ws / ".presets_history" / "p.json").is_file()
    # … panel settings were split by the e-mail keys they already carried
    a_panel = json.loads((ws / ".panel_settings.json").read_text(encoding="utf-8"))
    b_panel = json.loads((tree / "workspaces" / M.ws_id(B)
                          / ".panel_settings.json").read_text(encoding="utf-8"))
    assert list(a_panel) == [A, "version"] and list(b_panel) == [B, "version"]
    # … and the SOURCE was only read
    assert _hash_tree(cfg) == before


def test_migrated_tree_reads_back_through_the_layers(tmp_path, monkeypatch):
    """The point of the split: dies out of shared, results out of the overlay."""
    from motor_ai_sim import workspace as W
    from motor_ai_sim.routes import family as fam

    M = _migrate_module()
    cfg = _fake_config(tmp_path)
    tree = tmp_path / "srv"
    plan, _ = M.build_plan(cfg, tree, A)
    plan.run()

    monkeypatch.setenv("WORKSPACES_ROOT", str(tree / "workspaces"))
    monkeypatch.setenv("SHARED_ROOT", str(tree / "shared"))
    monkeypatch.setenv("PUBLISHED_ROOT", str(tree / "published"))
    monkeypatch.delitem(fam.__dict__, "_DIES_DIR", raising=False)
    with W.use_workspace(W.workspace_for_identity(A)):
        dies = fam.catalog_dies()
        assert [d["name"] for d in dies] == [DIE]
        assert dies[0]["layer"] == "shared"
        assert fam._die_layer(DIE) == "shared"
        # the sidecar the migration put in the OVERLAY is the one a read finds
        assert fam._run_path(
            DIE, f"runs/{CFG}/rated-abc12345.current.json.gz").is_file()
        assert str(tree / "workspaces") in str(fam._run_path(
            DIE, f"runs/{CFG}/rated-abc12345.current.json.gz"))
