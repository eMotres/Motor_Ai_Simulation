"""Access on BOTH levels - the die AND each configuration (owner 2026-10-16).

"We have motors and configurations of them; make it possible to restrict access to BOTH."  Concrete case: die
``CIANO28 85 20SW1200`` stays Private, ``L15`` is open to every signed-in account, ``L13`` stays hidden.

A configuration's status (one top-level yaml line, ``motor_access.config_visible``):
  * ``inherit``  (no line)  - follows the die;
  * ``private``             - admins + accounts holding the ``"<die>/<cfg>"`` grant;
  * ``public``              - every signed-in account, even when the die is Private;
  * ``selected`` (admin)    - ``private`` + at least one account holding the grant.

A die is visible when it is open itself OR one of its configurations is; then ONLY the visible configurations are
shown.  The leak this file guards above all: an ``inherit`` configuration of a die that is visible only through ANOTHER
configuration must stay hidden on every reader.

Runs on a throwaway catalog in two shapes: FLAT (single layer) and LAYERED (shared / published / workspaces).  No FEM.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml
from fastapi import HTTPException
from fastapi.testclient import TestClient

from motor_ai_sim.api import app

_ROOT = Path(__file__).resolve().parents[1]
_REAL_USERS = _ROOT / "config" / "users.json"
_REAL_DIES = _ROOT / "config" / "dies"

DIE = "ACCLV 85"
OPEN = "L15"         # public: open even though the die is private
HIDDEN = "L13"       # private
SIBLING = "L17"      # inherit: follows the die
DUTY = "rated"

ADMIN = "admin@example.com"
USER = "user@example.com"          # signed in, granted mode, NO die grant
CLIENT = "client@example.com"      # signed in, granted mode, NO die grant (a "selected client" candidate)
OTHER = "other@example.com"        # signed in, granted mode, NO die grant

client = TestClient(app)


# ── fixtures ─────────────────────────────────────────────────────────────────

def _die_doc() -> str:
    return yaml.safe_dump({
        "name": DIE, "locked": False, "created": "2026-10-16T10:00:00",
        "geometry": {"num_slots": 24, "num_poles": 28, "stator_diameter": 85.0,
                     "magnet_height": 3.0, "motor_length": 15.0},
    }, sort_keys=False)


def _cfg_doc(name: str, status: str | None) -> str:
    doc = {
        "name": name, "die": DIE, "role": "motor",
        "geometry_overrides": {"motor_length": float(name[1:]), "wire_height": 1.0},
        "winding": {"connection": "star"},
        "materials": {"magnet": "N42SH", "stator_core": "20SW1200"},
        "duties": [{"name": DUTY, "mode": "motor", "saved_at": "2026-10-16T10:00:00",
                    "current_arms": 85.0, "rpm": 3950.0, "gamma_deg": 12.0, "torque_nm": 1.5,
                    "power_kw": 0.9, "note": "", "result": {"efficiency_pct": 93.0, "mass_kg": 0.5}}],
    }
    text = yaml.safe_dump(doc, sort_keys=False)
    return (f"visibility: {status}\n" + text) if status else text


def _cards() -> dict:
    def card(cid, cfg):
        return {"id": cid, "name": f"{DIE} {cfg}", "diameter_mm": 85, "preset": "p_" + cid, "owner": "admin",
                "passport": {"passport": {"L0_mm": 15.0, "T0_Nm": 1.0}, "fit": {"slotHeight_mm": 1},
                             "geo": {"numSlots": 24, "numPoles": 28, "statorOR_mm": 42.5, "magnetHeight_mm": 3.0}}}
    return {"tiers": [], "diameters_mm": [85],
            "motors": [card("c13", HIDDEN), card("c15", OPEN), card("c17", SIBLING)]}


def _presets() -> dict:
    return {f"p_c{n}": {"id": f"p_c{n}", "name": f"{DIE} L{n}", "owner": "admin", "geometry": {}, "simulation": {}}
            for n in (13, 15, 17)}


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
    monkeypatch.setenv("MOTRES_CAD_ROOT", str(tmp_path / "cad"))
    MA._CFG_VIS_CACHE.clear()
    fam._TREE_CACHE.clear()

    def write(dies_dir: Path) -> None:
        d = dies_dir / DIE
        d.mkdir(parents=True)
        (d / "die.yaml").write_text(_die_doc(), encoding="utf-8")
        (d / f"{OPEN}.yaml").write_text(_cfg_doc(OPEN, "public"), encoding="utf-8")
        (d / f"{HIDDEN}.yaml").write_text(_cfg_doc(HIDDEN, "private"), encoding="utf-8")
        (d / f"{SIBLING}.yaml").write_text(_cfg_doc(SIBLING, None), encoding="utf-8")

    if layered:
        tree = tmp_path / "srv"
        shared, published, works = tree / "shared", tree / "published", tree / "workspaces"
        for d in (shared / "dies", published, works):
            d.mkdir(parents=True)
        write(shared / "dies")
        shutil.copy2(_ROOT / "config" / "motor_config.yaml", shared / "motor_config.yaml")
        (shared / "motor_catalog.json").write_text(json.dumps(_cards()), encoding="utf-8")
        (shared / "motor_presets.json").write_text(json.dumps(_presets()), encoding="utf-8")
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
        write(dies_dir)
        monkeypatch.setattr(fam, "_DIES_DIR", dies_dir)
        monkeypatch.setitem(cat.__dict__, "_CATALOG_PATH", tmp_path / "motor_catalog.json")
        (tmp_path / "motor_catalog.json").write_text(json.dumps(_cards()), encoding="utf-8")
        monkeypatch.setitem(pre.__dict__, "_PRESETS_PATH", tmp_path / "motor_presets.json")
        (tmp_path / "motor_presets.json").write_text(json.dumps(_presets()), encoding="utf-8")
    cat._REF_CACHE.update({"key": None, "cards": []})

    U.create_user(ADMIN, "password-admin", role="admin", name="Admin")
    for e in (USER, CLIENT, OTHER):
        U.create_user(e, "password-" + e.split("@")[0], role="engineer", name=e.split("@")[0])
        U.set_motor_grants(e, all_motors=False, dies=[])

    hdr = lambda e: {"Authorization": f"Bearer {U.issue_token(e)}"}   # noqa: E731
    yield {"layered": layered, "dies_dir": dies_dir,
           "admin": hdr(ADMIN), "user": hdr(USER), "client": hdr(CLIENT), "other": hdr(OTHER), "anon": {}}
    fam._TREE_CACHE.clear()
    MA._CFG_VIS_CACHE.clear()
    cat._REF_CACHE.update({"key": None, "cards": []})


# ── helpers ──────────────────────────────────────────────────────────────────

def _tree_cfgs(who_hdr) -> list | None:
    r = client.get("/api/family/tree", headers=who_hdr)
    assert r.status_code == 200, r.text
    for d in r.json()["dies"]:
        if d["name"] == DIE:
            assert d["config_count"] == len(d["configs"])                 # counted = listed
            return sorted(c["name"] for c in d["configs"])
    return None


def _card_ids(who_hdr) -> list:
    r = client.get("/api/catalog", headers=who_hdr)
    assert r.status_code == 200, r.text
    return sorted(m["id"] for m in r.json()["motors"])


def _access(email):
    from motor_ai_sim import motor_access as MA
    from motor_ai_sim import users as U
    return MA.catalog_access(f"Bearer {U.issue_token(email)}")


def _require_ok(acc, cfg) -> bool:
    from motor_ai_sim import motor_access as MA
    try:
        MA.require_config(DIE, cfg, access=acc)
        return True
    except HTTPException as exc:
        assert exc.status_code == 404
        return False


def _hidden_in_request(email, cfg) -> bool:
    """``hidden_for_request`` as the low-level readers call it, with the request bound to ``email``."""
    from motor_ai_sim import motor_access as MA
    from motor_ai_sim import users as U
    tok = MA._REQ_AUTH.set(f"Bearer {U.issue_token(email)}")
    try:
        return MA.hidden_for_request(DIE, cfg)
    finally:
        MA._REQ_AUTH.reset(tok)


def _payload(who_hdr, cfg) -> int:
    return client.get(f"/api/family/payload/{DIE}/{cfg}", headers=who_hdr).status_code


def _put_cfg(env, cfg, body):
    return client.put(f"/api/admin/dies/{DIE}/configs/{cfg}/access", json=body, headers=env["admin"])


def _admin_row():
    rows = client.get("/api/admin/dies", headers=_hdr_admin()).json()["dies"]
    return next(d for d in rows if d["name"] == DIE)


def _hdr_admin():
    from motor_ai_sim import users as U
    return {"Authorization": f"Bearer {U.issue_token(ADMIN)}"}


# ── (1) die Private + L15 public + L13 private ──────────────────────────────

def test_private_die_with_one_public_configuration(env):
    from motor_ai_sim import motor_access as MA
    acc = _access(USER)
    assert not MA.die_open(acc, DIE)
    assert MA.may_see_die(acc, DIE)                                        # opened through L15
    assert _tree_cfgs(env["user"]) == [OPEN]
    assert _card_ids(env["user"]) == ["c15"]
    assert _require_ok(acc, OPEN) and not _require_ok(acc, HIDDEN)
    assert _hidden_in_request(USER, OPEN) is False
    assert _hidden_in_request(USER, HIDDEN) is True
    assert _payload(env["user"], OPEN) == 200
    assert _payload(env["user"], HIDDEN) == 404
    # admin sees all three, everywhere
    assert _tree_cfgs(env["admin"]) == [HIDDEN, OPEN, SIBLING]
    assert _card_ids(env["admin"]) == ["c13", "c15", "c17"]


def test_admin_list_reports_both_levels(env):
    row = _admin_row()
    assert row["visibility"] == "private"
    assert {c["name"]: c["status"] for c in row["config_access"]} == {OPEN: "public", HIDDEN: "private", SIBLING: "inherit"}
    assert row["private_configs"] == [HIDDEN]                              # kept for older clients
    assert sorted(row["config_names"]) == [HIDDEN, OPEN, SIBLING]


# ── (2) the leak test: an inherit sibling of a die visible only via ANOTHER configuration ──

def test_inherit_sibling_of_a_die_opened_by_another_configuration_stays_hidden(env):
    from motor_ai_sim import mcp_tools as T
    from motor_ai_sim import motor_access as MA
    from motor_ai_sim.agent_keys import Principal
    from motor_ai_sim.routes import family as fam
    acc = _access(USER)
    assert MA.may_see_die(acc, DIE)
    assert not MA.config_visible(acc, DIE, SIBLING)
    assert not MA.may_see_config(acc, DIE, SIBLING)
    assert not MA.config_allowed(acc, DIE, SIBLING)
    assert not MA.may_see_config_file(acc, DIE, SIBLING, env["dies_dir"] / DIE / f"{SIBLING}.yaml")
    assert not _require_ok(acc, SIBLING)
    assert _hidden_in_request(USER, SIBLING) is True
    assert MA.drop_private_config_cards(_cards()["motors"], acc) == [m for m in _cards()["motors"] if m["id"] == "c15"]
    assert SIBLING not in (_tree_cfgs(env["user"]) or [])
    assert "c17" not in _card_ids(env["user"])
    assert _payload(env["user"], SIBLING) == 404
    for path in (f"/api/family/datasheet/{DIE}/{SIBLING}", f"/api/family/duty_results/{DIE}/{SIBLING}"):
        assert client.get(path, headers=env["user"]).status_code == 404, path
    # a die-level route that picks "the first configuration" never picks the hidden one
    r = client.get(f"/api/family/die/{DIE}/part_materials", headers=env["user"])
    if r.status_code == 200:
        assert r.json()["config"] == OPEN
    # the low-level reader every solve / report route uses
    from motor_ai_sim import users as U
    tok = MA._REQ_AUTH.set(f"Bearer {U.issue_token(USER)}")
    try:
        assert fam.config_doc(DIE, SIBLING) is None
        assert fam.config_doc(DIE, OPEN)["name"] == OPEN
    finally:
        MA._REQ_AUTH.reset(tok)
    # MCP: list_machines / check_fit / the catalog listing
    p = Principal(email=USER, credential_id="k", scopes=("read",))
    assert sorted(m["config"] for m in T.list_machines(p)["machines"] if m["die"] == DIE) == [OPEN]
    fit = T.check_fit(p, torque_nm=1.0, speed_rpm=100.0, limit=50)
    assert {r["config"] for r in fit["fits"] + fit["rejected"] if r.get("die") == DIE} <= {OPEN}
    row = next(d for d in T.list_catalog(p, "dies")["entries"] if d["id"] == DIE)
    assert row["configurations"] == [OPEN]
    # presets of the hidden configurations do not exist for the account
    ids = {x["id"] for x in client.get("/api/presets", headers=env["user"]).json()["presets"]}
    assert "p_c15" in ids and not ({"p_c13", "p_c17"} & ids)


def test_a_closed_die_with_no_open_configuration_is_invisible(env):
    from motor_ai_sim import motor_access as MA
    assert _put_cfg(env, OPEN, {"status": "inherit"}).status_code == 200
    acc = _access(USER)
    assert not MA.may_see_die(acc, DIE)
    assert _tree_cfgs(env["user"]) is None
    assert _card_ids(env["user"]) == []
    assert _payload(env["user"], OPEN) == 404


# ── (3) die Public: an inherit configuration follows it ──────────────────────

def test_public_die_opens_its_inherit_configurations(env):
    from motor_ai_sim import motor_access as MA
    MA.set_die_access(DIE, visibility="public")
    acc = _access(USER)
    assert MA.die_open(acc, DIE)
    assert MA.config_visible(acc, DIE, SIBLING)
    assert _tree_cfgs(env["user"]) == [OPEN, SIBLING]                      # L13 is still private
    assert _card_ids(env["user"]) == ["c15", "c17"]
    assert _payload(env["user"], SIBLING) == 200
    assert _hidden_in_request(USER, SIBLING) is False
    assert _hidden_in_request(USER, HIDDEN) is True
    # and "selected clients" on the die works the same way for the named account only
    MA.set_die_access(DIE, visibility="selected", clients=[CLIENT])
    assert _tree_cfgs(env["client"]) == [OPEN, SIBLING]
    assert _tree_cfgs(env["user"]) == [OPEN]


# ── (4) selected clients on a configuration ──────────────────────────────────

def test_selected_clients_see_a_private_configuration_of_a_private_die(env):
    from motor_ai_sim import users as U
    r = _put_cfg(env, HIDDEN, {"status": "selected", "clients": [CLIENT.upper()]})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "selected" and r.json()["clients"] == [CLIENT]
    assert (env["dies_dir"] / DIE / f"{HIDDEN}.yaml").read_text(encoding="utf-8").startswith("visibility: private\n")
    assert U.get_stored_motor_grants(CLIENT)["configs"] == [f"{DIE}/{HIDDEN}"]
    assert _tree_cfgs(env["client"]) == [HIDDEN, OPEN]                      # never the inherit sibling
    assert _tree_cfgs(env["user"]) == [OPEN]
    assert _payload(env["client"], HIDDEN) == 200 and _payload(env["user"], HIDDEN) == 404
    assert _hidden_in_request(CLIENT, HIDDEN) is False and _hidden_in_request(USER, HIDDEN) is True
    row = {c["name"]: c for c in _admin_row()["config_access"]}
    assert row[HIDDEN]["status"] == "selected" and row[HIDDEN]["clients"] == [CLIENT]

    # saving another list syncs both ways: added to USER, removed from CLIENT
    r = _put_cfg(env, HIDDEN, {"status": "selected", "clients": [USER, OTHER]})
    assert r.status_code == 200 and r.json()["clients"] == [OTHER, USER]
    assert "configs" not in U.get_stored_motor_grants(CLIENT)
    assert U.get_stored_motor_grants(USER)["configs"] == [f"{DIE}/{HIDDEN}"]
    assert _tree_cfgs(env["client"]) == [OPEN]
    assert _tree_cfgs(env["user"]) == [HIDDEN, OPEN]

    # Private removes the grant from every account
    r = _put_cfg(env, HIDDEN, {"status": "private"})
    assert r.status_code == 200 and r.json()["clients"] == []
    for e in (USER, OTHER, CLIENT):
        assert "configs" not in U.get_stored_motor_grants(e), e
    assert {c["name"]: c["status"] for c in _admin_row()["config_access"]}[HIDDEN] == "private"


def test_selected_needs_registered_clients_and_keeps_other_grants(env):
    from motor_ai_sim import users as U
    U.set_motor_grants(CLIENT, all_motors=False, dies=["Some die"], configs=["Some die/L1"])
    assert _put_cfg(env, HIDDEN, {"status": "selected", "clients": []}).status_code == 422
    assert _put_cfg(env, HIDDEN, {"status": "selected"}).status_code == 422
    assert _put_cfg(env, HIDDEN, {"status": "selected", "clients": ["nobody@example.com"]}).status_code == 422
    assert _put_cfg(env, HIDDEN, {"status": "secret"}).status_code == 422
    assert _put_cfg(env, "L99", {"status": "public"}).status_code == 404
    assert _put_cfg(env, HIDDEN, {"status": "selected", "clients": [CLIENT]}).status_code == 200
    g = U.get_stored_motor_grants(CLIENT)
    assert g["dies"] == ["Some die"] and g["configs"] == sorted(["Some die/L1", f"{DIE}/{HIDDEN}"])
    _put_cfg(env, HIDDEN, {"status": "private"})
    assert U.get_stored_motor_grants(CLIENT)["configs"] == ["Some die/L1"]      # only this configuration's grant goes
    for who in ("user", "client", "anon"):
        r = client.put(f"/api/admin/dies/{DIE}/configs/{HIDDEN}/access", json={"status": "public"}, headers=env[who])
        assert r.status_code in (401, 403), who


# ── (5) the stored line: old body, inherit, line endings, BOM ───────────────

def test_old_body_and_inherit_edit_one_line(env):
    from motor_ai_sim import motor_access as MA
    f = env["dies_dir"] / DIE / f"{HIDDEN}.yaml"
    bare = _cfg_doc(HIDDEN, None).encode()
    r = _put_cfg(env, HIDDEN, {"visibility": "public"})                    # the old "public" = no line = inherit
    assert r.status_code == 200 and r.json()["status"] == "inherit", r.text
    assert f.read_bytes() == bare
    assert MA.file_visibility(f) == "inherit"
    assert _put_cfg(env, HIDDEN, {"visibility": "private"}).json()["status"] == "private"
    assert f.read_bytes() == b"visibility: private\n" + bare
    assert _put_cfg(env, HIDDEN, {"status": "public"}).status_code == 200
    assert f.read_bytes() == b"visibility: public\n" + bare                # rewritten, never stacked
    assert _put_cfg(env, HIDDEN, {"status": "inherit"}).status_code == 200
    assert f.read_bytes() == bare


def test_crlf_and_bom_survive_every_status(env):
    f = env["dies_dir"] / DIE / f"{SIBLING}.yaml"
    original = b"\xef\xbb\xbf" + _cfg_doc(SIBLING, None).replace("\n", "\r\n").encode()
    f.write_bytes(original)
    for status, line in (("public", b"visibility: public\r\n"), ("private", b"visibility: private\r\n"),
                         ("selected", b"visibility: private\r\n")):
        body = {"status": status, **({"clients": [CLIENT]} if status == "selected" else {})}
        assert _put_cfg(env, SIBLING, body).status_code == 200, status
        assert f.read_bytes() == b"\xef\xbb\xbf" + line + original[3:], status
    assert _put_cfg(env, SIBLING, {"status": "inherit"}).status_code == 200
    assert f.read_bytes() == original


def test_a_private_line_right_after_a_bom_is_read(tmp_path):
    from motor_ai_sim import motor_access as MA
    p = tmp_path / "L1.yaml"
    p.write_bytes(b"\xef\xbb\xbfvisibility: private\r\nname: L1\r\n")
    assert MA.file_visibility(p) == "private"                              # fail closed, never "no line"


# ── (6) anonymous: never private; public / inherit as before ─────────────────

def test_anonymous_never_sees_private(env):
    from motor_ai_sim import motor_access as MA
    anon = {"mode": MA.MODE_ANONYMOUS, "dies": frozenset(), "email": None, "is_admin": False, "configs": frozenset()}
    assert MA.may_see_die(anon, DIE)
    assert MA.config_visible(anon, DIE, OPEN)
    assert MA.config_visible(anon, DIE, SIBLING)                           # inherit: the exhibit is unchanged
    assert not MA.config_visible(anon, DIE, HIDDEN)
    assert not MA.may_see_config(anon, DIE, HIDDEN)
    hidden = MA.hidden_config_names(anon)
    assert f"{DIE} {HIDDEN}".casefold() in hidden and f"{DIE} {SIBLING}".casefold() not in hidden
    assert _payload(env["anon"], HIDDEN) in (401, 404)
    r = client.get("/api/family/tree")
    if r.status_code == 200:
        names = [c["name"] for d in r.json()["dies"] if d["name"] == DIE for c in d["configs"]]
        assert HIDDEN not in names
