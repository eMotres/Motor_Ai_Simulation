"""CONFIGURATION-level visibility (owner 2026-10-15): "the die stays ONE for all; we hide the configuration".

A configuration yaml may carry ``visibility: private``.  Inside a die the caller may see, a private configuration is shown only to
admins and to accounts holding a per-user CONFIG grant (``motors.configs = ["<die>/<config>"]``); for everybody else it is
indistinguishable from a configuration that does not exist: 404 (not 403), not listed, not counted.

This module pins:
  * the helper (`motor_access.may_see_config`, the line scanner, the grant store);
  * the catalog tree / counts, the Configure references, the catalog cards, presets, `used_by`;
  * an API SWEEP over every route that names a die + configuration in the path / query / body: a hidden configuration answers
    exactly like a non-existent one for an account without access, and answers for admin and for a granted account;
  * the MCP tools, `/api/components`, the 3-D model routes, `/api/me`'s default motor;
  * the Admin routes (toggle + grant) and that the toggle edits one line of the file and nothing else.

Runs on a throwaway catalog in two shapes: FLAT (single layer, `family._DIES_DIR`) and LAYERED (production: shared / published /
workspaces).  No FEM.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from motor_ai_sim.api import app

_ROOT = Path(__file__).resolve().parents[1]
_REAL_USERS = _ROOT / "config" / "users.json"
_REAL_DIES = _ROOT / "config" / "dies"

DIE = "CFGVIS 40"
PUB = "L40"          # public configuration
PRIV = "L41"         # PRIVATE configuration
NOSUCH = "L99"       # does not exist
DUTY = "rated"

ADMIN = "admin@example.com"
PLAIN = "plain@example.com"        # role user, ALL motors (the dangerous case: an `all` grant must not open a private config)
ENG = "eng@example.com"            # role engineer, the die granted, no config grant
GRANTED = "granted@example.com"    # role engineer, the die + the config grant

client = TestClient(app)


# ── fixtures ─────────────────────────────────────────────────────────────────

def _die_doc(name: str) -> str:
    return yaml.safe_dump({
        "name": name, "locked": False, "created": "2026-10-15T10:00:00",
        "geometry": {"num_slots": 12, "num_poles": 14, "stator_diameter": 40.0,
                     "magnet_height": 3.0, "motor_length": 40.0},
    }, sort_keys=False, allow_unicode=True)


def _cfg_doc(die: str, name: str, private: bool) -> str:
    doc = {
        "name": name, "die": die, "role": "motor",
        "geometry_overrides": {"motor_length": 40.0, "wire_height": 1.0},
        "winding": {"connection": "star"},
        "materials": {"magnet": "N42SH", "stator_core": "20SW1200"},
        "bearings": {"A": {"card": "618/8-2Z"}, "B": {"card": "618/8-2Z"}},
        "controller": {"device": "C3M0021120K"},
        # a NESTED key of the same name must never flip the top-level flag
        "extras": {"visibility": "private" if name == PUB else "public"},
        "duties": [{"name": DUTY, "mode": "motor", "saved_at": "2026-10-15T10:00:00",
                    "current_arms": 85.0, "rpm": 6000.0, "gamma_deg": 12.0, "torque_nm": 1.5,
                    "power_kw": 0.9, "note": "",
                    "result": {"efficiency_pct": 93.0, "mass_kg": 0.5}}],
    }
    text = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)
    return ("visibility: private\n" + text) if private else text


@pytest.fixture(scope="module", autouse=True)
def _real_files_untouched():
    before_users = _REAL_USERS.read_bytes() if _REAL_USERS.exists() else None
    before_dies = {p: p.stat().st_mtime_ns for p in sorted(_REAL_DIES.rglob("*")) if p.is_file()}
    yield
    after_users = _REAL_USERS.read_bytes() if _REAL_USERS.exists() else None
    after_dies = {p: p.stat().st_mtime_ns for p in sorted(_REAL_DIES.rglob("*")) if p.is_file()}
    assert before_users == after_users, "config/users.json was modified by a test"
    assert before_dies == after_dies, "config/dies was modified by a test"


def _write_catalog(dies_dir: Path) -> None:
    d = dies_dir / DIE
    d.mkdir(parents=True)
    (d / "die.yaml").write_text(_die_doc(DIE), encoding="utf-8")
    (d / f"{PUB}.yaml").write_text(_cfg_doc(DIE, PUB, False), encoding="utf-8")
    (d / f"{PRIV}.yaml").write_text(_cfg_doc(DIE, PRIV, True), encoding="utf-8")


def _cards() -> dict:
    def card(cid, name):
        return {"id": cid, "name": name, "diameter_mm": 40, "preset": "p_" + cid, "owner": "admin",
                "passport": {"passport": {"L0_mm": 40.0, "T0_Nm": 1.0}, "fit": {"slotHeight_mm": 1},
                             "geo": {"numSlots": 12, "numPoles": 14, "statorOR_mm": 20, "magnetHeight_mm": 5.7}}}
    return {"tiers": [], "diameters_mm": [40],
            "motors": [card("cat_pub", f"{DIE} {PUB}"), card("cat_priv", f"{DIE} {PRIV}")]}


def _presets() -> dict:
    def pre(pid, name):
        return {"id": pid, "name": name, "owner": "admin", "geometry": {}, "simulation": {}}
    return {"p_cat_pub": pre("p_cat_pub", f"{DIE} {PUB}"), "p_cat_priv": pre("p_cat_priv", f"{DIE} {PRIV}"),
            "other": pre("other", "Something else")}


@pytest.fixture(params=["flat", "layered"])
def env(request, tmp_path, monkeypatch):
    from motor_ai_sim import auth
    from motor_ai_sim import model3d as M3
    from motor_ai_sim import motor_access as MA
    from motor_ai_sim import users as U
    from motor_ai_sim.routes import catalog as cat
    from motor_ai_sim.routes import family as fam
    from motor_ai_sim.routes import presets as pre

    layered = request.param == "layered"
    users_file = tmp_path / "users.json"
    monkeypatch.setattr(U, "_USERS_FILE", users_file)
    monkeypatch.setattr(MA, "_DIE_ACCESS_FILE", tmp_path / "die_access.json")
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.delenv("CATALOG_GRANT_ALL_REGISTERED", raising=False)
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)
    monkeypatch.setenv("MOTRES_CAD_ROOT", str(tmp_path / "cad"))
    MA._CFG_VIS_CACHE.clear()
    fam._TREE_CACHE.clear()

    if layered:
        tree = tmp_path / "srv"
        shared, published, works = tree / "shared", tree / "published", tree / "workspaces"
        for d in (shared / "dies", published, works):
            d.mkdir(parents=True)
        _write_catalog(shared / "dies")
        shutil.copy2(_ROOT / "config" / "motor_config.yaml", shared / "motor_config.yaml")
        (shared / "motor_catalog.json").write_text(json.dumps(_cards()), encoding="utf-8")
        (shared / "motor_presets.json").write_text(json.dumps(_presets()), encoding="utf-8")
        monkeypatch.setenv("WORKSPACES_ROOT", str(works))
        monkeypatch.setenv("SHARED_ROOT", str(shared))
        monkeypatch.setenv("PUBLISHED_ROOT", str(published))
        monkeypatch.delitem(fam.__dict__, "_DIES_DIR", raising=False)
        # (the per-call resolvers must not be pinned by an earlier flat test: delete the module-dict overrides)
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
        (tmp_path / "motor_catalog.json").write_text(json.dumps(_cards()), encoding="utf-8")
        monkeypatch.setitem(pre.__dict__, "_PRESETS_PATH", tmp_path / "motor_presets.json")
        (tmp_path / "motor_presets.json").write_text(json.dumps(_presets()), encoding="utf-8")
    cat._REF_CACHE.update({"key": None, "cards": []})

    U.create_user(ADMIN, "password-admin", role="admin", name="Admin")
    U.create_user(PLAIN, "password-plain", role="user", name="Plain")
    U.create_user(ENG, "password-eng-1", role="engineer", name="Eng")
    U.create_user(GRANTED, "password-granted", role="engineer", name="Granted")
    U.set_motor_grants(PLAIN, all_motors=True)
    U.set_motor_grants(ENG, all_motors=False, dies=[DIE])
    U.set_motor_grants(GRANTED, all_motors=False, dies=[DIE], configs=[f"{DIE}/{PRIV}"])

    # a fake 3-D model for BOTH configurations
    cad = tmp_path / "cad"
    for c in (PUB, PRIV):
        dd = cad / DIE / c
        dd.mkdir(parents=True)
        (dd / f"{DIE}_{c}_full.glb").write_bytes(b"glTF-" + c.encode())
        (dd / "manifest.json").write_text(json.dumps({
            "motor_id": f"{DIE}_{c}", "die": DIE, "config": c, "param_hash": "ab" * 32, "built_at": "2026-10-15T00:00:00+00:00",
            "template": {"version": "A.4.0"},
            "files": {f"{DIE}_{c}_full.glb": {"sha256": "0" * 64, "bytes": 9}}}), encoding="utf-8")

    hdr = lambda e: {"Authorization": f"Bearer {U.issue_token(e)}"}   # noqa: E731
    yield {"layered": layered, "dies_dir": dies_dir, "tmp": tmp_path,
           "admin": hdr(ADMIN), "plain": hdr(PLAIN), "eng": hdr(ENG), "granted": hdr(GRANTED), "anon": {}}
    fam._TREE_CACHE.clear()
    MA._CFG_VIS_CACHE.clear()
    cat._REF_CACHE.update({"key": None, "cards": []})


def _cfg_names(resp, die=DIE):
    for d in resp.json()["dies"]:
        if d["name"] == die:
            return sorted(c["name"] for c in d["configs"])
    return None


# ── the helper ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,want", [
    ("visibility: private\nname: L1\n", "private"),
    ("name: L1\nvisibility: Private\n", "private"),
    ("name: L1\nvisibility: 'private'\n", "private"),
    ('name: L1\nvisibility: "private"  # hidden\n', "private"),
    ("name: L1\r\nvisibility: private\r\n", "private"),
    ("name: L1\nvisibility: public\n", "public"),
    ("name: L1\n", "inherit"),
    ("name: L1\nextras:\n  visibility: private\n", "inherit"),         # nested: ignored
    ("name: L1\nduties:\n- visibility: private\n", "inherit"),
    ("name: L1\n# visibility: private\n", "inherit"),                  # a comment
    ("name: L1\nvisibility_note: private\n", "inherit"),               # a different key
])
def test_scanner_reads_only_the_top_level_key(text, want):
    from motor_ai_sim import motor_access as MA
    assert MA._scan_visibility(text) == want


def test_file_visibility_follows_the_file(tmp_path):
    from motor_ai_sim import motor_access as MA
    p = tmp_path / "L1.yaml"
    assert MA.file_visibility(p) == "inherit"                   # missing: nothing to hide
    p.write_text("name: L1\n", encoding="utf-8")
    assert MA.file_visibility(p) == "inherit"                   # no line: follows the die (2026-10-16)
    p.write_text("visibility: private\nname: L1\n", encoding="utf-8")
    assert MA.file_visibility(p) == "private"                   # the cache follows size / mtime
    p.write_text("name: L1\n", encoding="utf-8")
    assert MA.file_visibility(p) == "inherit"


def _acc(**kw):
    base = {"mode": "granted", "dies": frozenset({DIE}), "email": "u@example.com", "is_admin": False,
            "configs": frozenset()}
    base.update(kw)
    return base


def test_may_see_config_matrix(env):
    from motor_ai_sim import motor_access as MA
    admin = _acc(mode="all", is_admin=True, dies=frozenset(), email=None)
    for cfg, who, want in [
        (PUB, _acc(), True),
        (PRIV, _acc(), False),                                              # granted DIE, no config grant
        (PRIV, _acc(mode="all", dies=frozenset()), False),                  # an `all` grant does not open it
        (PRIV, _acc(configs=frozenset({f"{DIE}/{PRIV}"})), True),           # the config grant
        (PRIV, _acc(configs=frozenset({f"{DIE}/{PUB}"})), False),           # another config's grant
        (PRIV, admin, True),
        (PRIV, _acc(mode="anonymous", dies=frozenset(), email=None), False),
        (NOSUCH, _acc(), True),                                             # nothing to hide: the route 404s for its own reason
        (None, _acc(), True),                                               # die-only question
    ]:
        assert MA.may_see_config(who, DIE, cfg) is want, (cfg, who)
    # since 2026-10-16 a config grant opens THAT configuration even when the die is not open to the account
    # ("selected clients" of a private configuration) - and only that one: the inherit sibling stays hidden
    only_grant = _acc(dies=frozenset(), configs=frozenset({f"{DIE}/{PRIV}"}))
    assert MA.may_see_config(only_grant, DIE, PRIV)
    assert not MA.may_see_config(only_grant, DIE, PUB)


def test_grants_round_trip_and_legacy_records_are_untouched(env):
    from motor_ai_sim import users as U
    assert U.get_stored_motor_grants(GRANTED)["configs"] == [f"{DIE}/{PRIV}"]
    assert "configs" not in U.get_stored_motor_grants(ENG)                  # key only when set
    # a later die-only grant change keeps the config grants (key absent = keep)
    U.set_motor_grants(GRANTED, all_motors=False, dies=[DIE])
    assert U.get_stored_motor_grants(GRANTED)["configs"] == [f"{DIE}/{PRIV}"]
    U.set_motor_grants(GRANTED, all_motors=False, dies=[DIE], configs=[])
    assert "configs" not in U.get_stored_motor_grants(GRANTED)
    assert U.normalize_config_grants(["a/b", " a/b ", "nonsense", "/x", "y/", None]) == ["a/b"]
    assert U.normalize_grants({"all": False, "dies": ["d"]}) == {"all": False, "dies": ["d"]}   # byte-identical legacy shape


# ── catalog tree, counts ─────────────────────────────────────────────────────

def test_tree_lists_and_counts(env):
    for who, want in [("admin", [PUB, PRIV]), ("granted", [PUB, PRIV]), ("eng", [PUB]), ("plain", [PUB])]:
        r = client.get("/api/family/tree", headers=env[who])
        assert r.status_code == 200, r.text
        assert _cfg_names(r) == want, who
        row = next(d for d in r.json()["dies"] if d["name"] == DIE)
        assert row["config_count"] == len(want), who                            # not counted
        assert "L41" not in json.dumps(r.json()) or PRIV in want, who           # not named anywhere in the payload


def test_tree_memo_never_serves_one_account_the_others_rows(env):
    # admin and the `all`-granted account are both mode ALL; the engineer with a grant differs from one without
    seq = ["admin", "plain", "granted", "eng", "plain", "admin", "eng", "granted"]
    want = {"admin": [PUB, PRIV], "plain": [PUB], "granted": [PUB, PRIV], "eng": [PUB]}
    for who in seq:
        assert _cfg_names(client.get("/api/family/tree", headers=env[who])) == want[who], who


def test_flipping_the_file_changes_the_next_answer(env):
    from motor_ai_sim import motor_access as MA
    f = env["dies_dir"] / DIE / f"{PUB}.yaml"
    assert _cfg_names(client.get("/api/family/tree", headers=env["eng"])) == [PUB]
    f.write_text("visibility: private\n" + f.read_text(encoding="utf-8"), encoding="utf-8")
    assert _cfg_names(client.get("/api/family/tree", headers=env["eng"])) == []
    f.write_text(f.read_text(encoding="utf-8").replace("visibility: private\n", "", 1), encoding="utf-8")
    assert _cfg_names(client.get("/api/family/tree", headers=env["eng"])) == [PUB]


def test_anonymous_never_sees_a_private_configuration(env):
    r = client.get("/api/family/tree")
    if r.status_code == 200:
        assert PRIV not in json.dumps(r.json())
    for path in (f"/api/family/payload/{DIE}/{PRIV}", f"/api/family/duty_results/{DIE}/{PRIV}"):
        assert client.get(path).status_code in (401, 404)


# ── the API sweep ────────────────────────────────────────────────────────────

# Every route that names a die + configuration in the PATH or the QUERY, and how this module covers it.  A NEW such route
# fails `test_every_config_route_is_covered` until it is added here (and gated).
READ_ROUTES = {          # (method, path template) -> request builder key
    ("GET", "/api/family/payload/{die}/{cfg}"): "plain",
    ("GET", "/api/family/datasheet/{die}/{cfg}"): "plain",
    ("GET", "/api/family/duty_results/{die}/{cfg}"): "plain",
    ("GET", "/api/family/duty_fields/{die}/{cfg}"): "plain",
    ("GET", "/api/family/duty_runs/{die}/{cfg}/{duty}"): "plain",
    ("GET", "/api/family/duty_run/{die}/{cfg}/{duty}/{drive}"): "plain",
    ("GET", "/api/family/report/{die}/{cfg}"): "plain",
    ("GET", "/api/family/history/{die}"): "query-config",
    ("GET", "/api/family/die/{die}/part_materials"): "query-config",
    ("GET", "/api/family/die/{die}/part_tree"): "query-config",
    ("GET", "/api/family/config/{die}/{cfg}/cad_params"): "plain",
    # the Motors-tab read-only view of stored results (tests/test_motor_view.py): gated by the app-wide path gate
    ("GET", "/api/motor-view/{die}/{cfg}"): "plain",
    ("GET", "/api/motor-view/{die}/{cfg}/{duty}/field/{kind}"): "plain",
    ("GET", "/api/simulation/cogging/{die}/{cfg}"): "plain",          # the saved no-load cogging torque (tests/test_cogging.py)
}
WRITE_ROUTES = {         # layered-only (a registered account writes into its own workspace); flat = admin-only
    ("PATCH", "/api/family/config/{die}/{cfg}"): {"name": "Lx"},
    ("POST", "/api/family/config/{die}/{cfg}/duplicate"): {"name": "Lcopy"},
    ("DELETE", "/api/family/config/{die}/{cfg}"): None,
    ("PATCH", "/api/family/config/{die}/{cfg}/lock"): {"locked": True},
    ("PUT", "/api/family/config/{die}/{cfg}/cad_params"): {"values": {}},
    ("POST", "/api/family/config/{die}/{cfg}/cad_rebuild"): None,
    ("PATCH", "/api/family/config/{die}/{cfg}/default_duty"): {"duty": DUTY},
    ("PUT", "/api/family/config/{die}/{cfg}/applications"): {"applications": ["propeller"]},
    ("PATCH", "/api/family/config/{die}/{cfg}/battery"): {"cells": 6, "v_cell_min": 3.0, "v_cell_max": 4.2},
    ("PATCH", "/api/family/config/{die}/{cfg}/controller"): {"devices_parallel": 1},
    ("PATCH", "/api/family/config/{die}/{cfg}/bearings"): {"A": {"card": "618/8-2Z"}, "B": {"card": "618/8-2Z"}},
    ("POST", "/api/family/duty/{die}/{cfg}/{duty}/duplicate"): {"name": "copy"},
    ("POST", "/api/family/duty/{die}/{cfg}/{duty}/save_continuous_point"): {"name": "continuous"},
    ("PATCH", "/api/family/duty/{die}/{cfg}/{duty}"): {"name": "renamed"},
    ("DELETE", "/api/family/duty/{die}/{cfg}/{duty}"): None,
    ("POST", "/api/family/publish/{die}/{cfg}"): None,
    ("DELETE", "/api/family/publish/{die}/{cfg}"): None,
    ("POST", "/api/family/publish/{die}/{cfg}/{duty}"): None,
    ("DELETE", "/api/family/publish/{die}/{cfg}/{duty}"): None,
    ("PUT", "/api/simulation/cogging/{die}/{cfg}"): {"cogging_id": "cog_none"},
}
BODY_ROUTES = {          # (method, path) -> body builder(cfg)
    ("POST", "/api/family/activate"): lambda c: {"die": DIE, "config": c, "duty": DUTY},
    ("POST", "/api/family/reattach"): lambda c: {"die": DIE, "config": c, "duty": DUTY},
    ("POST", "/api/family/duty"): lambda c: {"die": DIE, "config": c, "duty": {"name": "x", "mode": "motor", "current_arms": 1.0, "rpm": 1.0, "gamma_deg": 0.0}},
    ("POST", "/api/family/duty_result"): lambda c: {"die": DIE, "config": c, "duty": DUTY, "result": {"torque_nm": 1.0}},
    ("POST", "/api/family/duty_run"): lambda c: {"die": DIE, "config": c, "duty": DUTY, "drive": "current", "summary": {}},
    ("POST", "/api/family/history/restore"): lambda c: {"die": DIE, "config": c, "stamp": "20260101-000000"},
    ("POST", "/api/my_motors/duplicate"): lambda c: {"die": DIE, "config": c, "duty": DUTY},
    ("POST", "/api/simulation/cogging/run"): lambda c: {"die": DIE, "config": c},
    ("POST", "/api/family/config"): lambda c: {"die": DIE, "name": c},     # CREATE: nothing hidden to leak, covered by its own test
}
SELF_GATED = {           # routes whose natural "missing" answer is not a 404: gated in-route and tested below
    ("GET", "/api/bearings/losses"), ("GET", "/api/controller/cooling_from_thermal"), ("GET", "/api/controller/devices"),
    ("GET", "/api/controller/point"), ("GET", "/api/controller/settings"), ("GET", "/api/controller/thermal_cooling"),
    ("GET", "/api/model3d/{die}/{config}/model.glb"), ("GET", "/api/model3d/{die}/{config}/model.step"),
    # Geometry Full files: the same 404 as model.glb (`_entry_for`), tested in tests/test_construction_params.py
    ("GET", "/api/model3d/{die}/{config}/parts"), ("GET", "/api/model3d/{die}/{config}/bom.csv"),
    ("GET", "/api/model3d/{die}/{config}/bom.xlsx"), ("GET", "/api/model3d/{die}/{config}/params.csv"),
    ("GET", "/api/model3d/{die}/{config}/model.fcstd"),
    # the SIMPLIFIED model (open to every signed-in user, no NDA): the same 404 as model.glb (`_entry_for`), tested in tests/test_cad_simplified_access.py
    ("GET", "/api/model3d/{die}/{config}/simplified.step"), ("GET", "/api/model3d/{die}/{config}/simplified.glb"),
    ("GET", "/api/model3d/{die}/{config}/simplified.json"), ("GET", "/api/model3d/{die}/{config}/simplified_view.glb"),
    ("POST", "/api/family/config/{die}/{cfg}/cad_params/import"),       # multipart; tested in tests/test_construction_params.py
    ("GET", "/api/propellers/cooling-options"), ("GET", "/api/propellers/{prop_id}/s1"), ("GET", "/api/system/graph"),
    # bearing fit / holder strength: a hidden configuration answers 404 in-route (_gate_config), tested below
    ("GET", "/api/mechanical/bearing_fit"), ("GET", "/api/mechanical/holder_strength"), ("POST", "/api/mechanical/holder_fem_job"),
}
NOT_CONFIG_ROUTES = {    # name a `die` or a `config` that is not a catalog configuration
    ("POST", "/api/family/save_as_new_die"), ("POST", "/api/optimization/saved"),
    ("POST", "/api/family/duplicate"),
    ("PUT", "/api/admin/dies/{die}/configs/{cfg}/access"),         # admin-only toggle, tested below
    # project motor links: gated by project membership PLUS the ordinary motor gate (tests/test_development_*.py,
    # tests/test_motor_view.py access matrices)
    ("DELETE", "/api/admin/projects/{project_id}/motors"), ("DELETE", "/api/projects/{project_id}/motors"),
    ("GET", "/api/projects/{project_id}/motors/points"), ("GET", "/api/projects/{project_id}/motors/view"),
    ("GET", "/api/projects/{project_id}/motors/field"), ("PATCH", "/api/projects/{project_id}/motors/note"),
    ("PATCH", "/api/projects/{project_id}/motors/status"), ("POST", "/api/projects/{project_id}/motors"),
    ("PATCH", "/api/projects/{project_id}/motors/report_reviewed"), ("PATCH", "/api/projects/{project_id}/motors/recommended"),
    ("GET", "/api/projects/{project_id}/motors/report"),
}


def _api_routes():
    names_cfg = {"cfg", "config"}
    out = set()
    for r in app.routes:
        if not isinstance(r, APIRoute):
            continue
        params = {p.name for p in r.dependant.path_params + r.dependant.query_params}
        body = set()
        for b in r.dependant.body_params:
            fields = getattr(getattr(b, "field_info", None), "annotation", None)
            body |= set(getattr(fields, "model_fields", {}) or {})
        is_path_query = "die" in params and bool(names_cfg & params)
        is_body = "die" in body and bool(names_cfg & body)
        if is_path_query or is_body:
            for m in r.methods:
                out.add((m, r.path))
    return out


def _production_route(r) -> bool:
    """Production routes are gated by explicit production MEMBERSHIP (production/access.py), stricter than catalog
    visibility; tested in tests/test_production_access.py."""
    return r[1].startswith("/api/production/") or r[1].startswith("/api/admin/production/")


def test_every_config_route_is_covered():
    covered = set(READ_ROUTES) | set(WRITE_ROUTES) | set(BODY_ROUTES) | SELF_GATED | NOT_CONFIG_ROUTES
    missing = sorted(r for r in _api_routes() if r not in covered and not _production_route(r))
    assert not missing, ("a route names a die + configuration but is not in the config-visibility sweep "
                         f"(gate it with motor_access.may_see_config and add it here): {missing}")


def _norm(text: str, cfg: str) -> str:
    return text.replace(cfg, "<CFG>")


def _same_as_missing(resp_hidden, resp_missing, cfg_hidden, cfg_missing=NOSUCH):
    assert resp_hidden.status_code == resp_missing.status_code, (resp_hidden.status_code, resp_missing.status_code,
                                                                 resp_hidden.text[:200], resp_missing.text[:200])
    assert _norm(resp_hidden.text, cfg_hidden) == _norm(resp_missing.text, cfg_missing), (resp_hidden.text, resp_missing.text)


def _read_url(template: str, cfg: str) -> str:
    return template.format(die=DIE, cfg=cfg, duty=DUTY, drive="current", kind="em")


@pytest.mark.parametrize("route", sorted(READ_ROUTES))
@pytest.mark.parametrize("who", ["plain", "eng", "anon"])
def test_read_routes_answer_a_hidden_configuration_like_a_missing_one(env, route, who):
    method, template = route
    if READ_ROUTES[route] == "query-config":
        mk = lambda cfg: (template.format(die=DIE), {"config": cfg})      # noqa: E731
    else:
        mk = lambda cfg: (_read_url(template, cfg), {})                    # noqa: E731
    (u1, p1), (u2, p2) = mk(PRIV), mk(NOSUCH)
    r_hidden = client.get(u1, params=p1, headers=env[who])
    r_missing = client.get(u2, params=p2, headers=env[who])
    _same_as_missing(r_hidden, r_missing, PRIV)
    # and never the contents
    assert "N42SH" not in r_hidden.text and "ab" * 0 == ""


@pytest.mark.parametrize("route", sorted(READ_ROUTES))
def test_read_routes_serve_admin_and_a_granted_account(env, route):
    method, template = route
    for who in ("admin", "granted"):
        if READ_ROUTES[route] == "query-config":
            r = client.get(template.format(die=DIE), params={"config": PRIV}, headers=env[who])
        else:
            r = client.get(_read_url(template, PRIV), headers=env[who])
        miss = client.get(*( (template.format(die=DIE), ) if READ_ROUTES[route] == "query-config" else (_read_url(template, NOSUCH),) ),
                          params=({"config": NOSUCH} if READ_ROUTES[route] == "query-config" else None), headers=env[who])
        # the account that may see it is NOT told "no such configuration"
        assert not (r.status_code == 404 and "configuration" in r.text.lower() and "not found" in r.text.lower()), (who, r.text[:200])
        # (a route that answers 200 for the public sibling answers 200 here too)
        pub = client.get(*( (template.format(die=DIE), ) if READ_ROUTES[route] == "query-config" else (_read_url(template, PUB),) ),
                         params=({"config": PUB} if READ_ROUTES[route] == "query-config" else None), headers=env[who])
        assert r.status_code == pub.status_code or r.status_code in (200, 400, 422), (who, r.status_code, pub.status_code)
        # (403 nda_required: a full-construction route answers every configuration alike without the NDA gate - existing,
        # hidden or missing - so it is no oracle; security review 2026-10-20)
        assert miss.status_code in (404, 400, 422, 200) or _nda_required(miss), (who, miss.status_code, miss.text[:200])


def test_payload_content_is_the_hidden_machine_for_the_granted_account(env):
    r = client.get(f"/api/family/payload/{DIE}/{PRIV}", headers=env["granted"])
    assert r.status_code == 200, r.text
    assert client.get(f"/api/family/payload/{DIE}/{PRIV}", headers=env["admin"]).status_code == 200
    assert client.get(f"/api/family/payload/{DIE}/{PUB}", headers=env["eng"]).status_code == 200      # the sibling is untouched
    assert client.get(f"/api/family/payload/{DIE}/{PRIV}", headers=env["plain"]).status_code == 404
    assert client.get(f"/api/family/payload/{DIE}/{PRIV}", headers=env["eng"]).status_code == 404


def _nda_required(r) -> bool:
    try:
        d = r.json().get("detail")
    except ValueError:
        return False
    return r.status_code == 403 and isinstance(d, dict) and d.get("code") == "nda_required"


def test_part_materials_without_config_never_picks_the_hidden_one(env, monkeypatch):
    # the per-part breakdown is the full construction: without the NDA gate a 403 that names nothing (security review 2026-10-20)
    r = client.get(f"/api/family/die/{DIE}/part_materials", headers=env["eng"])
    assert r.status_code in (200, 403, 404, 500), r.text
    assert PRIV not in r.text
    # with the gate passed: no `config=` -> the first VISIBLE configuration (L40), not L41 (alphabetically after, but the rule
    # is "visible")
    from motor_ai_sim.routes import construction
    monkeypatch.setattr(construction, "require_full_construction", lambda *a, **k: None)
    r = client.get(f"/api/family/die/{DIE}/part_materials", headers=env["eng"])
    assert r.status_code in (200, 404, 500), r.text
    assert PRIV not in r.text


@pytest.mark.parametrize("route", sorted(WRITE_ROUTES))
@pytest.mark.parametrize("who", ["plain", "eng"])
def test_write_routes_answer_a_hidden_configuration_like_a_missing_one(env, route, who):
    method, template = route
    body = WRITE_ROUTES[route]
    before = {p.name: p.read_bytes() for p in (env["dies_dir"] / DIE).glob("*.yaml")}
    kw = {"json": body} if body is not None else {}
    r_hidden = client.request(method, _read_url(template, PRIV), headers=env[who], **kw)
    r_missing = client.request(method, _read_url(template, NOSUCH), headers=env[who], **kw)
    _same_as_missing(r_hidden, r_missing, PRIV)
    # nothing was written to, or copied from, the hidden configuration
    after = {p.name: p.read_bytes() for p in (env["dies_dir"] / DIE).glob("*.yaml")}
    assert after == before
    if env["layered"]:
        for ws in (env["tmp"] / "srv" / "workspaces").glob("*/dies/*/*.yaml"):
            assert ws.stem != PRIV, f"the hidden configuration was forked into {ws}"


@pytest.mark.parametrize("route", sorted(BODY_ROUTES))
@pytest.mark.parametrize("who", ["plain", "eng"])
def test_body_routes_answer_a_hidden_configuration_like_a_missing_one(env, route, who):
    method, path = route
    if path == "/api/family/config":
        # CREATE: asking for the name of a hidden configuration must not say "already exists"
        r = client.post(path, json=BODY_ROUTES[route](PRIV), headers=env[who])
        assert "already exists" not in r.text
        return
    r_hidden = client.request(method, path, json=BODY_ROUTES[route](PRIV), headers=env[who])
    r_missing = client.request(method, path, json=BODY_ROUTES[route](NOSUCH), headers=env[who])
    _same_as_missing(r_hidden, r_missing, PRIV)


def test_activate_works_for_admin_and_for_a_granted_account(env):
    if not env["layered"]:
        pytest.skip("a registered account writes its own workspace only when layering is on")
    r = client.post("/api/family/activate", json={"die": DIE, "config": PRIV}, headers=env["granted"])
    assert r.status_code != 404, r.text


def test_the_context_strip_never_names_a_hidden_configuration(env):
    from contextlib import ExitStack
    from motor_ai_sim import workspace as W
    from motor_ai_sim.routes import family as fam
    doc = json.dumps({"die": DIE, "config": PRIV, "duty": DUTY, "at": "2026-10-15T10:00:00"})
    written = []
    with ExitStack() as st:
        # the context file is per workspace with layering: write the same context for every account
        for email in ((PLAIN, ENG, GRANTED, ADMIN) if env["layered"] else (None,)):
            if email:
                st.enter_context(W.use_workspace(W.workspace_for_identity(email)))
            f = fam._ctx_file()
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(doc, encoding="utf-8")
            written.append(f)
    try:
        for who in ("plain", "eng"):
            body = client.get("/api/family/context", headers=env[who]).json()
            assert body.get("active") is False and PRIV not in json.dumps(body), who
        assert client.get("/api/family/context", headers=env["granted"]).json().get("active") is True
    finally:
        for f in written:
            f.unlink(missing_ok=True)


# ── a hidden configuration is not forked, duplicated or published away ──────

def test_duplicating_a_die_does_not_copy_the_hidden_configuration(env):
    if not env["layered"]:
        pytest.skip("flat install: only an admin writes, and an admin sees everything")
    r = client.post(f"/api/family/die/{DIE}/duplicate", json={"name": "COPY OF CFGVIS"}, headers=env["eng"])
    assert r.status_code == 200, r.text
    assert r.json()["configs"] == [PUB]
    ws_dies = list((env["tmp"] / "srv" / "workspaces").glob("*/dies/COPY OF CFGVIS/*.yaml"))
    assert sorted(p.stem for p in ws_dies if p.stem != "die") == [PUB]
    r = client.post(f"/api/family/die/{DIE}/duplicate", json={"name": "COPY OF CFGVIS ADMIN"}, headers=env["admin"])
    assert r.status_code == 200 and sorted(r.json()["configs"]) == [PUB, PRIV]


def test_copy_on_write_of_a_public_sibling_does_not_fork_the_hidden_file(env):
    if not env["layered"]:
        pytest.skip("copy-on-write exists only with layering")
    r = client.patch(f"/api/family/config/{DIE}/{PUB}/lock", json={"locked": True}, headers=env["eng"])
    assert r.status_code == 200, r.text
    forked = sorted(p.stem for p in (env["tmp"] / "srv" / "workspaces").glob("*/dies/*/*.yaml"))
    assert PRIV not in forked and PUB in forked


# ── self-gated routes (their natural "missing" answer is not a 404) ─────────

def test_bearings_losses_reads_a_hidden_configuration_as_one_without_bearings(env):
    q = {"rpm": 6000}
    for who in ("plain", "eng"):
        a = client.get("/api/bearings/losses", params={**q, "die": DIE, "cfg": PRIV}, headers=env[who])
        b = client.get("/api/bearings/losses", params={**q, "die": DIE, "cfg": NOSUCH}, headers=env[who])
        assert a.status_code == b.status_code
        assert a.json().get("has_bearings") is False and _norm(a.text, PRIV) == _norm(b.text, NOSUCH)
    r = client.get("/api/bearings/losses", params={**q, "die": DIE, "cfg": PRIV}, headers=env["granted"])
    assert r.status_code == 200, r.text


def test_controller_routes_read_a_hidden_configuration_as_an_unsaved_one(env):
    for who in ("plain", "eng"):
        a = client.get("/api/controller/settings", params={"die": DIE, "config": PRIV}, headers=env[who])
        b = client.get("/api/controller/settings", params={"die": DIE, "config": NOSUCH}, headers=env[who])
        assert a.status_code == b.status_code and a.json() == b.json() == {}, who
        a = client.get("/api/controller/point", params={"die": DIE, "config": PRIV, "duty": DUTY}, headers=env[who])
        b = client.get("/api/controller/point", params={"die": DIE, "config": NOSUCH, "duty": DUTY}, headers=env[who])
        assert a.status_code == b.status_code, who
        assert _norm(a.text, PRIV) == _norm(b.text, NOSUCH)
    g = client.get("/api/controller/settings", params={"die": DIE, "config": PRIV}, headers=env["granted"])
    assert g.json().get("device") == "C3M0021120K"          # the granted account reads the real settings


def test_system_graph_status_does_not_look_up_a_hidden_configuration(env):
    # the System graph is engineer/admin only since stage 2 (#259); a plain account is refused outright
    assert client.get("/api/system/graph", params={"view": "propeller_s1", "die": DIE, "config": PRIV},
                      headers=env["plain"]).status_code == 403
    for who in ("eng",):
        a = client.get("/api/system/graph", params={"view": "propeller_s1", "die": DIE, "config": PRIV}, headers=env[who])
        b = client.get("/api/system/graph", params={"view": "propeller_s1", "die": DIE, "config": NOSUCH}, headers=env[who])
        assert a.status_code == b.status_code == 200
        assert _norm(json.dumps(a.json(), sort_keys=True), PRIV) == _norm(json.dumps(b.json(), sort_keys=True), NOSUCH), who


def test_cooling_options_never_name_a_hidden_configuration(env, tmp_path, monkeypatch):
    from motor_ai_sim import cooling_options as CO
    f = tmp_path / "cooling_options.yaml"
    f.write_text(yaml.safe_dump({"version": 1, "dies": {DIE: {
        "cooling_options": ["propeller_air"], "propellers": ["tmotor_p12x4"],
        "defaults": {PUB: "tmotor_p12x4", PRIV: "tmotor_p12x4"},
        "configs": {PRIV: {"propellers": ["tmotor_p13x4_4"]}}}}}), encoding="utf-8")
    monkeypatch.setattr(CO, "_FILE", f)
    for who in ("plain", "eng"):
        a = client.get("/api/propellers/cooling-options", params={"die": DIE, "config": PRIV}, headers=env[who])
        b = client.get("/api/propellers/cooling-options", params={"die": DIE, "config": NOSUCH}, headers=env[who])
        assert a.status_code == b.status_code == 200
        assert _norm(json.dumps(a.json(), sort_keys=True), PRIV) == _norm(json.dumps(b.json(), sort_keys=True), NOSUCH), who
        full = client.get("/api/propellers/cooling-options", params={"die": DIE}, headers=env[who]).json()
        assert PRIV not in full["defaults"] and PRIV not in full["bad_defaults"]
    g = client.get("/api/propellers/cooling-options", params={"die": DIE}, headers=env["granted"]).json()
    assert PRIV in {**g["defaults"], **g["bad_defaults"]}


def test_propeller_s1_is_a_404_for_a_hidden_configuration(env):
    for who, want in (("plain", 404), ("eng", 404)):
        r = client.get("/api/propellers/any/s1", params={"die": DIE, "config": PRIV}, headers=env[who])
        assert r.status_code == want and f"no propeller S1 model for {DIE} {PRIV}" in r.text


# ── 3-D model ────────────────────────────────────────────────────────────────

def test_model3d_list_and_files(env):
    names = lambda who: sorted(m["config"] for m in client.get("/api/model3d/models", headers=env[who]).json()["models"])   # noqa: E731
    assert names("admin") == [PUB, PRIV] and names("granted") == [PUB, PRIV]
    assert names("eng") == [PUB] and names("plain") == [PUB]
    for who in ("eng", "plain"):
        for kind in ("model.glb", "model.step"):
            a = client.get(f"/api/model3d/{DIE}/{PRIV}/{kind}", headers=env[who])
            b = client.get(f"/api/model3d/{DIE}/{NOSUCH}/{kind}", headers=env[who])
            assert a.status_code == b.status_code == 404 and a.text == b.text
    # the FULL model needs the NDA gate now (owner 2026-10-15): a granted account without it is VISIBLE (403 nda_required, not 404); the admin gets it
    gr = client.get(f"/api/model3d/{DIE}/{PRIV}/model.glb", headers=env["granted"])
    assert gr.status_code == 403 and gr.json()["detail"]["code"] == "nda_required"
    assert client.get(f"/api/model3d/{DIE}/{PRIV}/model.glb", headers=env["admin"]).status_code == 200
    for who in ("eng", "plain"):
        assert client.get(f"/api/model3d/{DIE}/{PRIV}/simplified_view.glb", headers=env[who]).status_code == 404


# ── catalog cards, references, presets, used_by, components ─────────────────

def test_catalog_cards_and_references(env):
    ids = lambda who, path="/api/catalog": sorted(m["id"] for m in (client.get(path, headers=env[who]).json()["motors"]))   # noqa: E731
    assert ids("admin") == ["cat_priv", "cat_pub"]
    assert ids("granted") == ["cat_priv", "cat_pub"]
    for who in ("eng", "plain"):
        assert ids(who) == ["cat_pub"], who
        assert "cat_priv" not in client.get("/api/catalog", headers=env[who]).json()["by_diameter"].get("40", [])
        assert client.get("/api/catalog/cat_priv/passport", headers=env[who]).status_code == 404
        assert client.get("/api/catalog/cat_priv/configure_context", headers=env[who]).status_code == 404
        assert client.post("/api/catalog/cat_priv/load", headers=env[who]).status_code == 404
        assert client.post("/api/catalog/cat_priv/duplicate", headers=env[who]).status_code == 404
    assert client.get("/api/catalog/cat_priv/passport", headers=env["admin"]).status_code == 200
    r = client.get("/api/catalog/references", headers=env["eng"]).json()["motors"]
    assert [m["id"] for m in r] == ["cat_pub"]
    assert sorted(m["id"] for m in client.get("/api/catalog/references", headers=env["granted"]).json()["motors"]) == ["cat_priv", "cat_pub"]


def test_configure_context_presets_list_only_visible_configurations(env):
    r = client.get("/api/catalog/cat_pub/configure_context", headers=env["eng"])
    assert r.status_code == 200, r.text
    names = sorted(p.get("config") for p in r.json().get("presets") or [])
    assert PRIV not in names
    g = client.get("/api/catalog/cat_pub/configure_context", headers=env["granted"]).json()
    assert PRIV in sorted(p.get("config") for p in g.get("presets") or [])


# ── Configure's STORED answers (F1, pre-launch review 2026-10-20) ─────────────
# The context / load are stored answers (answer_store) shared by every caller.  They are stored UNFILTERED and filtered for
# the caller on every request: whoever built the stored answer first must never decide what the next caller sees.

_CTX = "/api/catalog/cat_pub/configure_context"
_LOAD = "/api/catalog/cat_pub/configure_load"
_SEES_PRIV = {"admin": True, "granted": True, "eng": False, "plain": False}


@pytest.fixture
def answers(env, monkeypatch):
    """A fresh answer store ON DISK (both shapes) and an empty memory; the CAD mass is not part of this question."""
    from motor_ai_sim import answer_store as st
    from motor_ai_sim.routes import catalog as cat
    monkeypatch.setattr(cat, "_preset_mass", lambda *a: None)
    root = env["tmp"] / "answers"
    monkeypatch.setenv(st.ENV_ROOT, str(root))
    st.clear_memory()
    yield {"st": st, "root": root}
    st.clear_memory()


def _ctx_names(who, env):
    r = client.get(_CTX, headers=env[who])
    assert r.status_code == 200, (who, r.text)
    assert "_config_access" not in r.text, who                      # the stored bookkeeping never leaves the server
    return sorted(p["config"] for p in r.json().get("presets") or [])


def _check_every_role(env):
    for who, sees in _SEES_PRIV.items():
        names = _ctx_names(who, env)
        assert names == ([PUB, PRIV] if sees else [PUB]), (who, names)


@pytest.mark.parametrize("first", ["admin", "granted", "plain", "eng"])
def test_stored_configure_context_is_filtered_for_every_caller_whoever_built_it(env, answers, first):
    _ctx_names(first, env)                                          # builds and stores the answer
    _check_every_role(env)
    _check_every_role(env)                                          # and again, all from the store


def test_stored_configure_context_admin_then_ordinary(env, answers):
    assert _ctx_names("admin", env) == [PUB, PRIV]
    assert _ctx_names("plain", env) == [PUB]                        # was [L40, L41] from the admin's stored answer
    assert _ctx_names("eng", env) == [PUB]


def test_stored_configure_context_ordinary_then_authorized(env, answers):
    assert _ctx_names("plain", env) == [PUB]
    assert _ctx_names("granted", env) == [PUB, PRIV]                # was [L40]: the authorized user lost the private preset
    assert _ctx_names("admin", env) == [PUB, PRIV]


def test_stored_configure_context_after_a_memory_clear_reads_the_disk_and_still_filters(env, answers):
    assert _ctx_names("admin", env) == [PUB, PRIV]
    stored = list(answers["root"].rglob("*.json"))
    if env["layered"]:
        assert stored, "the shared machine's answer is stored on disk"
    answers["st"].clear_memory()
    assert _ctx_names("plain", env) == [PUB]                        # was the admin's [L40, L41], read back from disk
    answers["st"].clear_memory()
    assert _ctx_names("granted", env) == [PUB, PRIV]


def test_a_stored_context_without_access_records_is_still_filtered(env, answers):
    """Defence in depth: even a stored answer that lists the private preset and carries no file record (an answer
    built some other way) is judged by name for every caller."""
    from motor_ai_sim.routes import catalog as cat
    motor, src = cat._configure_motor("cat_pub", env["admin"]["Authorization"])
    full = cat._stored_context("cat_pub", motor, src)
    assert sorted(p["config"] for p in full["presets"]) == [PUB, PRIV]          # stored WHOLE
    poisoned = {k: v for k, v in full.items() if k != "_config_access"}
    answers["st"].put("configure_context", {"motor": "cat_pub", "src": str(src)}, cat._configure_stamp(src), poisoned,
                      persist=False)
    _check_every_role(env)


def test_answers_stored_before_the_fix_are_ignored(env, answers):
    """The store version is part of every stamp: an answer stored under the old one is stale and recomputed."""
    from motor_ai_sim.routes import catalog as cat
    from motor_ai_sim import fscache
    assert cat.CONFIGURE_STORE_VERSION != "configure-1"
    motor, src = cat._configure_motor("cat_pub", env["admin"]["Authorization"])
    real = cat._configure_stamp(src)
    old = real
    try:
        orig = fscache.stamp_of
        fscache.stamp_of = lambda paths, dirs=(), *, salt="": orig(paths, dirs, salt="configure-1")
        old = cat._configure_stamp(src)
    finally:
        fscache.stamp_of = orig
    assert old != real
    answers["st"].put("configure_context", {"motor": "cat_pub", "src": str(src)}, old,
                      {"motor_id": "cat_pub", "presets": [{"die": DIE, "config": PRIV, "stale": True}]})
    answers["st"].clear_memory()
    r = client.get(_CTX, headers=env["admin"]).json()
    assert sorted(p["config"] for p in r["presets"]) == [PUB, PRIV]
    assert not any(p.get("stale") for p in r["presets"])            # recomputed, not the old answer


@pytest.mark.parametrize("first", ["admin", "plain"])
def test_configure_load_of_a_hidden_configuration_answers_like_a_missing_one(env, answers, first):
    """An ordinary user selecting the private L41 got HTTP 200 and its whole load (configure_load).  Now it is refused
    BEFORE anything is read, with the very answer a configuration the machine does not have gets - also when the
    admin's load of L41 is already stored."""
    if first == "admin":
        r = client.get(_LOAD, params={"config": PRIV}, headers=env["admin"])
        assert r.status_code == 200, r.text
        assert r.json()["config"] == PRIV
    for who in ("plain", "eng", "anon"):
        hidden = client.get(_LOAD, params={"config": PRIV}, headers=env[who])
        missing = client.get(_LOAD, params={"config": NOSUCH}, headers=env[who])
        _same_as_missing(hidden, missing, PRIV)
        assert hidden.status_code in (401, 404), (who, hidden.status_code)
        assert "N42SH" not in hidden.text
        if who != "anon":
            ok = client.get(_LOAD, params={"config": PUB}, headers=env[who])
            assert ok.status_code == 200, (who, ok.text)
            body = ok.json()
            assert sorted(p["config"] for p in body["context"]["presets"]) == [PUB], who   # embedded context filtered
            assert PRIV not in json.dumps(body["context"]) and "_config_access" not in ok.text, who
    for who in ("admin", "granted"):
        r = client.get(_LOAD, params={"config": PRIV}, headers=env[who])
        assert r.status_code == 200, (who, r.text)
        assert r.json()["config"] == PRIV
        assert sorted(p["config"] for p in r.json()["context"]["presets"]) == [PUB, PRIV], who
    if env["layered"]:                                               # and after a memory clear (disk only)
        answers["st"].clear_memory()
        hidden = client.get(_LOAD, params={"config": PRIV}, headers=env["plain"])
        assert hidden.status_code == 404, hidden.text


def test_presets_of_a_hidden_configuration_do_not_exist_for_others(env):
    for who in ("eng", "plain"):
        ids = {p["id"] for p in client.get("/api/presets", headers=env[who]).json()["presets"]}
        assert "p_cat_priv" not in ids and {"other", "p_cat_pub"} <= ids, who
        assert client.get("/api/presets/p_cat_priv", headers=env[who]).status_code == 404
        assert client.post("/api/presets/p_cat_priv/apply", headers=env[who]).status_code == 404
        assert client.post("/api/presets/p_cat_priv/open", headers=env[who]).status_code == 404
    for who in ("admin", "granted"):
        ids = {p["id"] for p in client.get("/api/presets", headers=env[who]).json()["presets"]}
        assert {"other", "p_cat_priv", "p_cat_pub"} <= ids, who


def test_used_by_does_not_name_a_hidden_configuration(env):
    # (used_by walks the CALLER'S workspace dies: with layering the shared dies are not in it, so only the flat shape lists them)
    for who, want in (("admin", [PUB, PRIV]), ("granted", [PUB, PRIV]), ("eng", [PUB]), ("plain", [PUB])):
        r = client.get("/api/catalog/cards/bearing/used_by", params={"id": "618/8-2Z"}, headers=env[who])
        assert r.status_code == 200, r.text
        got = sorted(m["config"] for m in r.json()["machines"])
        assert got == (want if not env["layered"] else []), who
        assert PRIV not in got or who in ("admin", "granted"), who


def test_components_follow_the_configuration(env, monkeypatch):
    from motor_ai_sim.modules import component_library as CL
    lib = CL.ComponentLibrary()
    base = CL.example_frame()
    for cfg in (PUB, PRIV):
        lib.add(base.model_copy(update={"component_id": f"motor_{cfg.lower()}"}), die=DIE, config=cfg)
        lib.add(base.model_copy(update={"component_id": f"pack_{cfg.lower()}"}), die=DIE, config=cfg)   # a controller / pack example follows its machine
    lib.add(base.model_copy(update={"component_id": "frame"}))
    monkeypatch.setattr(CL, "_LIB", lib)
    ids = lambda who: sorted(c["component_id"] for c in client.get("/api/components", headers=env[who]).json()["components"])   # noqa: E731
    assert ids("admin") == sorted(["frame", f"motor_{PUB.lower()}", f"motor_{PRIV.lower()}", f"pack_{PUB.lower()}", f"pack_{PRIV.lower()}"])
    assert ids("granted") == ids("admin")
    for who in ("eng", "plain"):
        assert ids(who) == sorted(["frame", f"motor_{PUB.lower()}", f"pack_{PUB.lower()}"]), who
        assert client.get(f"/api/components/motor_{PRIV.lower()}", headers=env[who]).status_code == 404
        assert client.get(f"/api/components/pack_{PRIV.lower()}", headers=env[who]).status_code == 404
        # the same module is reachable through the kernel
        k = client.post("/api/kernel/run", json={"capability": "component.library"}, headers=env[who]).json()
        assert PRIV not in json.dumps(k), who
    assert client.get(f"/api/components/motor_{PRIV.lower()}", headers=env["granted"]).status_code == 200


def test_default_motor_does_not_announce_a_hidden_configuration(env):
    from motor_ai_sim import users as U
    U.set_motor_grants(ENG, all_motors=False, dies=[DIE], default={"die": DIE, "config": PRIV})
    U.set_motor_grants(GRANTED, all_motors=False, dies=[DIE], default={"die": DIE, "config": PRIV})
    assert client.get("/api/me", headers=env["eng"]).json()["defaultMotor"] is None
    assert client.get("/api/me", headers=env["granted"]).json()["defaultMotor"] == {"die": DIE, "config": PRIV}


def test_last_motor_selection_is_unavailable_for_a_hidden_configuration(env, tmp_path, monkeypatch):
    from motor_ai_sim.routes import account
    monkeypatch.setattr(account, "_prefs_path", lambda: tmp_path / "prefs.json", raising=False)
    sel = {"selection": {"ref_id": None, "die": DIE, "config": PRIV, "duty": None}}
    r = client.put("/api/me/last_motor", json=sel, headers=env["eng"])
    assert r.status_code == 404, r.text


# ── MCP tools ────────────────────────────────────────────────────────────────

def _principal(email):
    from motor_ai_sim.agent_keys import Principal
    return Principal(email=email, credential_id="k", scopes=("read",))


@pytest.fixture
def _carded(monkeypatch):
    """Both configurations carry a FULL passport card: the MCP catalog lists only carded configurations (``_visible_tree``)."""
    from motor_ai_sim import passport_store as PS
    monkeypatch.setattr(PS, "card_index", lambda: {DIE: {PUB: "2026-10-15", PRIV: "2026-10-15"}})


def test_mcp_tools_do_not_see_a_hidden_configuration(env, _carded):
    from motor_ai_sim import mcp_tools as T
    for who, seen in ((ENG, False), (PLAIN, False), (GRANTED, True), (ADMIN, True)):
        p = _principal(who)
        ms = T.list_machines(p)["machines"]
        cfgs = sorted(m["config"] for m in ms if m["die"] == DIE)
        assert cfgs == ([PUB, PRIV] if seen else [PUB]), who
        dies = T.list_catalog(p, "dies")["entries"]
        row = next(d for d in dies if d["id"] == DIE)
        assert row["configurations"] == ([PUB, PRIV] if seen else [PUB]), who
        entry = T.get_catalog_entry(p, "dies", DIE)["entry"]
        assert (PRIV in entry["configurations"]) is seen, who
        if seen:
            assert T.get_machine_performance(p, DIE, PRIV, DUTY)["config"] == PRIV
        else:
            with pytest.raises(T.ToolError) as e1:
                T.get_machine_performance(p, DIE, PRIV, DUTY)
            with pytest.raises(T.ToolError) as e2:
                T.get_machine_performance(p, DIE, NOSUCH, DUTY)
            assert str(e1.value).replace(PRIV, "<C>") == str(e2.value).replace(NOSUCH, "<C>")
        fit = T.check_fit(p, torque_nm=1.0, speed_rpm=100.0, limit=50)
        names = {r["config"] for r in fit["fits"] + fit["rejected"]}
        assert (PRIV in names) is seen, who


def test_mcp_catalog_drops_a_die_with_no_visible_configuration(env, _carded):
    """2026-10-19 (owner: «150_35 L35 and 85 L13 are private»): a die whose every configuration is private is not listed to an
    account that cannot see them - not even its name - while the admin (whose agent key the connector uses) still sees it."""
    from motor_ai_sim import mcp_tools as T
    f = env["dies_dir"] / DIE / f"{PUB}.yaml"
    f.write_text(_cfg_doc(DIE, PUB, True), encoding="utf-8")         # now BOTH configurations are private
    for who, seen in ((ENG, False), (PLAIN, False), (ADMIN, True)):
        ids = [d["id"] for d in T.list_catalog(_principal(who), "dies")["entries"]]
        assert (DIE in ids) is seen, who
        if not seen:
            with pytest.raises(T.ToolError):
                T.get_catalog_entry(_principal(who), "dies", DIE)


# ── Admin: the toggle and the grant ──────────────────────────────────────────

def test_admin_toggle_edits_one_line_and_is_reversible(env):
    f = env["dies_dir"] / DIE / f"{PUB}.yaml"
    before = f.read_bytes()
    r = client.put(f"/api/admin/dies/{DIE}/configs/{PUB}/access", json={"visibility": "private"}, headers=env["admin"])
    assert r.status_code == 200 and r.json()["visibility"] == "private", r.text
    after = f.read_bytes()
    nl = b"\r\n" if b"\r\n" in before else b"\n"                    # the file's own line ending is kept
    assert after == b"visibility: private" + nl + before            # one line added, nothing else moved
    assert _cfg_names(client.get("/api/family/tree", headers=env["eng"])) == []
    assert client.get(f"/api/admin/dies/{DIE}/configs", headers=env["admin"]).json()["configs"] == [
        {"name": PUB, "visibility": "private"}, {"name": PRIV, "visibility": "private"}]
    r = client.put(f"/api/admin/dies/{DIE}/configs/{PUB}/access", json={"visibility": "public"}, headers=env["admin"])
    assert r.status_code == 200
    assert f.read_bytes() == before                                                # byte-identical round trip
    # the same toggle twice does not stack lines
    for _ in range(2):
        client.put(f"/api/admin/dies/{DIE}/configs/{PUB}/access", json={"visibility": "private"}, headers=env["admin"])
    assert re.findall(r"(?m)^visibility:", f.read_text(encoding="utf-8")) == ["visibility:"]   # exactly one top-level key


def test_admin_toggle_is_admin_only_and_validated(env):
    for who in ("eng", "plain", "granted", "anon"):
        r = client.put(f"/api/admin/dies/{DIE}/configs/{PUB}/access", json={"visibility": "private"}, headers=env[who])
        assert r.status_code in (401, 403), who
    assert client.put(f"/api/admin/dies/{DIE}/configs/{PUB}/access", json={"visibility": "secret"}, headers=env["admin"]).status_code == 422
    assert client.put(f"/api/admin/dies/{DIE}/configs/{NOSUCH}/access", json={"visibility": "private"}, headers=env["admin"]).status_code == 404
    assert client.put(f"/api/admin/dies/NOPE/configs/{PUB}/access", json={"visibility": "private"}, headers=env["admin"]).status_code == 404


def test_admin_grant_a_private_configuration_to_an_account(env):
    assert _cfg_names(client.get("/api/family/tree", headers=env["eng"])) == [PUB]
    r = client.put(f"/api/admin/users/{ENG}/motors", json={"all": False, "dies": [DIE], "configs": [f"{DIE}/{PRIV}"]},
                   headers=env["admin"])
    assert r.status_code == 200, r.text
    assert r.json()["motors"]["configs"] == [f"{DIE}/{PRIV}"]
    assert _cfg_names(client.get("/api/family/tree", headers=env["eng"])) == [PUB, PRIV]
    # a body without `configs` keeps the grant; an empty list clears it
    client.put(f"/api/admin/users/{ENG}/motors", json={"all": False, "dies": [DIE]}, headers=env["admin"])
    assert _cfg_names(client.get("/api/family/tree", headers=env["eng"])) == [PUB, PRIV]
    client.put(f"/api/admin/users/{ENG}/motors", json={"all": False, "dies": [DIE], "configs": []}, headers=env["admin"])
    assert _cfg_names(client.get("/api/family/tree", headers=env["eng"])) == [PUB]


def test_admin_grant_validation(env):
    put = lambda configs: client.put(f"/api/admin/users/{ENG}/motors", json={"all": False, "dies": [DIE], "configs": configs},   # noqa: E731
                                     headers=env["admin"])
    assert put([f"{DIE}/{NOSUCH}"]).status_code == 422
    assert put(["NOPE/L1"]).status_code == 422
    assert put(["nonsense"]).status_code == 422
    assert put("L41").status_code == 422
    for who in ("eng", "plain"):
        assert client.put(f"/api/admin/users/{ENG}/motors", json={"all": True, "configs": [f"{DIE}/{PRIV}"]},
                          headers=env[who]).status_code in (401, 403)


def test_admin_lists_show_the_private_configurations(env):
    rows = client.get("/api/admin/dies", headers=env["admin"]).json()["dies"]
    assert next(d for d in rows if d["name"] == DIE)["private_configs"] == [PRIV]
    rows = client.get("/api/admin/motors", headers=env["admin"]).json()["dies"]
    assert next(d for d in rows if d["name"] == DIE)["private_configs"] == [PRIV]
    u = client.get(f"/api/admin/users/{GRANTED}/motors", headers=env["admin"]).json()
    assert u["motors"]["configs"] == [f"{DIE}/{PRIV}"]


# ── outside a request nothing is hidden (CLI, nightly scripts) ──────────────

def test_readers_outside_a_request_are_not_gated(env):
    from motor_ai_sim import motor_access as MA
    from motor_ai_sim.routes import family as fam
    assert MA.request_is_bound() is False
    assert fam.config_doc(DIE, PRIV)["name"] == PRIV
    assert MA.hidden_for_request(DIE, PRIV) is False


def test_static3d_presets_of_a_hidden_configuration_do_not_exist(env, tmp_path, monkeypatch):
    from motor_ai_sim.routes import static3d
    f = tmp_path / "static3d_presets.json"
    f.write_text(json.dumps(_presets()), encoding="utf-8")
    monkeypatch.setattr(static3d, "_PRESETS_PATH", f)
    for who in ("eng", "plain"):
        a = client.get("/api/static3d/machine", params={"preset": "p_cat_priv"}, headers=env[who])
        b = client.get("/api/static3d/machine", params={"preset": "p_nosuch"}, headers=env[who])
        assert a.status_code == b.status_code == 404, (who, a.text[:200])
        assert _norm(a.text.replace("p_cat_priv", "<P>"), "x") == _norm(b.text.replace("p_nosuch", "<P>"), "x"), who
        assert "p_cat_priv" not in a.text.replace("'p_cat_priv'", "")        # not in the "known:" list either
    # the PUBLIC sibling's preset is still found (it has no geometry block of its own -> the 404 names the lookup, not a hiding)
    r = client.get("/api/static3d/machine", params={"preset": "p_cat_pub"}, headers=env["eng"])
    assert "known:" not in r.text or "p_cat_pub" in r.text


def test_a_die_with_a_private_configuration_cannot_be_published_as_open_data(tmp_path):
    from motor_ai_sim import data_sources as DS
    d = tmp_path / "dies" / "OPENDIE 40"
    d.mkdir(parents=True)
    (d / "die.yaml").write_text(yaml.safe_dump({"name": "OPENDIE 40", "owner_org": "MOTRES",
                                                "geometry": {"stator_diameter": 40, "num_slots": 12, "num_poles": 14}}),
                                encoding="utf-8")
    cfg = {"name": "L10", "die": "OPENDIE 40", "role": "motor",
           "duties": [{"name": "rated", "current_arms": 10.0, "rpm": 1000.0}]}
    (d / "L10.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    base = DS.move_blockers(d, DS.SOURCE_OPEN)
    assert not [b for b in base if b["kind"] == "private_configuration"]
    (d / "L11.yaml").write_text("visibility: private\n" + yaml.safe_dump({**cfg, "name": "L11"}), encoding="utf-8")
    got = DS.move_blockers(d, DS.SOURCE_OPEN)
    assert [b["name"] for b in got if b["kind"] == "private_configuration"] == ["L11"]
    # the key itself is not what the whitelist objects to (private -> private is fine)
    assert not [b for b in DS.move_blockers(d, DS.SOURCE_PRIVATE) if b["kind"] == "private_configuration"]
    assert not [b for b in got if b["kind"] in ("content", "unreadable") and b["name"] == "L11.yaml"]


def test_the_safety_net_gates_a_route_nobody_gated_by_hand(env):
    """A route added tomorrow that names a die + configuration in its path or query, and does nothing about visibility,
    is still a 404 for an account without access (motor_access.config_path_gate on the app)."""
    def ungated(die: str, cfg: str):
        return {"die": die, "cfg": cfg}

    def ungated_q(die: str, config: str):
        return {"die": die, "config": config}

    app.add_api_route("/api/_net_probe/{die}/{cfg}", ungated, methods=["GET"])
    app.add_api_route("/api/_net_probe_q", ungated_q, methods=["GET"])
    try:
        for who in ("eng", "plain", "anon"):
            assert client.get(f"/api/_net_probe/{DIE}/{PRIV}", headers=env[who]).status_code in (401, 404), who
            assert client.get("/api/_net_probe_q", params={"die": DIE, "config": PRIV}, headers=env[who]).status_code in (401, 404), who
        for who in ("admin", "granted"):
            assert client.get(f"/api/_net_probe/{DIE}/{PRIV}", headers=env[who]).status_code == 200, who
        assert client.get(f"/api/_net_probe/{DIE}/{PUB}", headers=env["eng"]).status_code == 200
        assert client.get(f"/api/_net_probe/{DIE}/{NOSUCH}", headers=env["eng"]).status_code == 200      # nothing to hide
    finally:
        app.router.routes[:] = [r for r in app.router.routes
                                if not str(getattr(r, "path", "")).startswith("/api/_net_probe")]


def test_bearing_fit_and_holder_strength_hide_a_hidden_configuration(env):
    for path in ("/api/mechanical/bearing_fit", "/api/mechanical/holder_strength"):
        for who in ("plain", "anon"):
            r = client.get(path, params={"die": DIE, "config": PRIV}, headers=env[who])
            assert r.status_code in (401, 404), (path, who, r.status_code)
        assert client.get(path, params={"die": DIE, "config": PUB}, headers=env["plain"]).status_code == 200, path
    assert client.post("/api/mechanical/holder_fem_job", params={"die": DIE, "config": PUB}, headers=env["eng"]).status_code == 501
