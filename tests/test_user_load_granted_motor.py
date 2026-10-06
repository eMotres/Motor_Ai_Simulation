"""A REGULAR account opens its granted motors in Motors + Configure, and nothing more.

Live symptom (deployed 6a2ca6b, AUTH_ENFORCE=1): a role "user" account with
``motors = {"all": true}`` could load no motor - the web ran the OWNER load flow
(activate, geometry PUT, winding / materials / simulation PATCH) because the
server reports ``can_write`` for any registered account under the per-user
workspaces, and the writer routes answered 403 (and ``PATCH /api/winding/config``
400, because the geometry PUT that precedes it is a client-local no-op for a
non-admin, so the winding was validated against the workspace's OLD machine).

Owner decision 2026-10-05: a standard user sees only Motors + Configure.  Those
two tabs need READS only (tree, payload, catalog references, configure_context,
passports), so the gate is NOT widened.  What is under test:

* the tier table still refuses a regular user on every writer / admin / compute
  route the old load flow touched, with the message the web shows;
* an admin passes the same gate (unchanged);
* a granted user reads exactly his dies / cards: ungranted die -> 404 on the
  payload, Configure's references and its configure_context (never a 403 - an
  ungranted motor must not be distinguishable from a missing one);
* the per-user DEFAULT motor: admin-only, validated against the grants and the
  die's configurations, cleared with the grant, kept while still covered, and
  delivered to the user through /api/me.
"""
from __future__ import annotations

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

GRANTED = "GRANTED 40"
UNGRANTED = "VENDORONLY 30"
CFG = "L40"
CFG2 = "L60"
ADMIN = "admin@example.com"
USER = "client@example.com"

client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def _real_files_untouched():
    before_users = _REAL_USERS.read_bytes() if _REAL_USERS.exists() else None
    before_dies = {p: p.stat().st_mtime_ns
                   for p in sorted(_REAL_DIES.rglob("*")) if p.is_file()}
    yield
    assert before_users == (_REAL_USERS.read_bytes() if _REAL_USERS.exists() else None)
    assert before_dies == {p: p.stat().st_mtime_ns
                           for p in sorted(_REAL_DIES.rglob("*")) if p.is_file()}


def _write_die(shared: Path, die: str, cfgs=(CFG,)) -> None:
    d = shared / "dies" / die
    d.mkdir(parents=True)
    (d / "die.yaml").write_text(yaml.safe_dump({
        "name": die, "locked": False, "created": "2026-09-16T10:00:00",
        "geometry": {"num_slots": 12, "num_poles": 14, "stator_diameter": 40.0,
                     "magnet_height": 3.0, "motor_length": 40.0},
    }, sort_keys=False), encoding="utf-8")
    for cfg in cfgs:
        (d / f"{cfg}.yaml").write_text(yaml.safe_dump({
            "name": cfg, "die": die, "role": "motor",
            "geometry_overrides": {"motor_length": 40.0, "wire_height": 1.0},
            "winding": {"connection": "star"},
            "materials": {"magnet": "N42SH", "stator_core": "20SW1200"},
            "duties": [{"name": "rated", "mode": "motor",
                        "saved_at": "2026-09-16T10:00:00",
                        "current_arms": 85.0, "rpm": 6000.0, "gamma_deg": 12.0,
                        "note": ""}],
        }, sort_keys=False), encoding="utf-8")


def _card(cid: str, name: str) -> dict:
    return {"id": cid, "name": name, "diameter_mm": 40,
            "passport": {"passport": {"L0_mm": 40.0}}}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """The production posture: layering on, door closed, AUTH_ENFORCE on, one
    admin and one REGULAR account granted a single die."""
    from motor_ai_sim import auth
    from motor_ai_sim import sessions as S
    from motor_ai_sim import users as U
    from motor_ai_sim.routes import catalog as cat
    from motor_ai_sim.routes import family as fam

    tree = tmp_path / "srv"
    shared, published, works = (tree / "shared", tree / "published",
                                tree / "workspaces")
    for d in (shared / "dies", published, works):
        d.mkdir(parents=True)
    _write_die(shared, GRANTED, cfgs=(CFG, CFG2))
    _write_die(shared, UNGRANTED)
    shutil.copy2(_ROOT / "config" / "motor_config.yaml", shared / "motor_config.yaml")
    catalog_file = tmp_path / "motor_catalog.json"
    catalog_file.write_text(json.dumps({
        "tiers": [], "diameters_mm": [40],
        "motors": [_card("c1", f"{GRANTED} {CFG}"), _card("c2", f"{UNGRANTED} {CFG}"),
                   _card("c3", "Legacy duplicate without a die")]}),
        encoding="utf-8")
    monkeypatch.setattr(cat, "_CATALOG_PATH", catalog_file, raising=False)

    monkeypatch.setenv("WORKSPACES_ROOT", str(works))
    monkeypatch.setenv("SHARED_ROOT", str(shared))
    monkeypatch.setenv("PUBLISHED_ROOT", str(published))
    monkeypatch.delenv("CATALOG_GRANT_ALL_REGISTERED", raising=False)
    monkeypatch.setenv("PUBLIC_EXHIBIT", "0")
    monkeypatch.delitem(fam.__dict__, "_DIES_DIR", raising=False)
    fam._TREE_CACHE.clear()

    users_file = tmp_path / "users.json"
    users_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(U, "_USERS_FILE", users_file)
    monkeypatch.setattr(S, "_SESSIONS_FILE", tmp_path / "sessions.json")
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", True)
    U.create_user(ADMIN, "password-admin", role="admin", name="Admin")
    U.create_user(USER, "password-user", role="user", name="Client")
    U.set_motor_grants(USER, all_motors=False, dies=[GRANTED])
    from motor_ai_sim import workspace as W
    W.provision(USER)
    yield {
        "admin": {"Authorization": f"Bearer {U.issue_token(ADMIN)}"},
        "user": {"Authorization": f"Bearer {U.issue_token(USER)}"},
        "U": U,
    }
    fam._TREE_CACHE.clear()


# -- 1. the gate is NOT widened ------------------------------------------------

_WRITERS = [
    ("PATCH", "/api/simulation/config", {"max_current": 10}),
    ("PATCH", "/api/mesh/config", {"gap_layers": 3}),
    ("PUT", "/api/geometry", {"motor_length": 41}),
    ("PATCH", "/api/materials", {"part": "magnet", "material": "N42SH"}),
    ("POST", "/api/simulation/run", {}),
    ("POST", "/api/simulation/caches/clear", {}),
    ("GET", "/api/simulation/caches", None),
    ("GET", "/api/simulation/ledger", None),
    ("POST", "/api/optimization", {}),
    ("POST", "/api/catalog/cards", {}),
]


@pytest.mark.parametrize("method,path,body", _WRITERS)
def test_regular_user_is_refused_with_a_readable_reason(env, method, path, body):
    r = client.request(method, path, headers=env["user"],
                       **({"json": body} if body is not None else {}))
    assert r.status_code == 403, (method, path, r.text)
    j = r.json()
    assert j["required_role"] == "admin" and j["your_role"] == "user"
    assert "admin" in j["detail"].lower()


@pytest.mark.parametrize("method,path,body", _WRITERS)
def test_admin_passes_the_same_gate(env, method, path, body):
    r = client.request(method, path, headers=env["admin"],
                       **({"json": body} if body is not None else {}))
    # whatever the route itself answers, it is NOT the gate's refusal
    assert not (r.status_code in (401, 403)
                and isinstance(r.json(), dict) and "required_role" in r.json()), (
        method, path, r.text)


def test_regular_user_cannot_reach_admin_routes(env):
    for url in ("/api/auth/users", "/api/admin/motors"):
        assert client.get(url, headers=env["user"]).status_code == 403, url
    r = client.put(f"/api/admin/users/{USER}/motors", headers=env["user"],
                   json={"all": True})
    assert r.status_code == 403
    r = client.put(f"/api/admin/users/{USER}/motors", headers=env["user"],
                   json={"default": {"die": GRANTED, "config": CFG}})
    assert r.status_code == 403, "a user must not set his own default motor"
    assert env["U"].get_motor_grants(USER) == {"all": False, "dies": [GRANTED]}


# -- 2. what Motors + Configure read -------------------------------------------

def test_motors_tab_lists_only_the_granted_die(env):
    r = client.get("/api/family/tree", headers=env["user"])
    assert r.status_code == 200
    assert [d["name"] for d in r.json()["dies"]] == [GRANTED]


def test_payload_of_a_granted_die_reads_and_an_ungranted_one_is_404(env):
    ok = client.get(f"/api/family/payload/{GRANTED}/{CFG}?duty=rated", headers=env["user"])
    assert ok.status_code == 200, ok.text
    no = client.get(f"/api/family/payload/{UNGRANTED}/{CFG}?duty=rated", headers=env["user"])
    assert no.status_code == 404


def test_configure_references_hold_only_granted_cards(env):
    r = client.get("/api/catalog/references", headers=env["user"])
    assert r.status_code == 200
    assert [m["id"] for m in r.json()["motors"]] == ["c1"]
    ra = client.get("/api/catalog/references", headers=env["admin"])
    assert sorted(m["id"] for m in ra.json()["motors"]) == ["c1", "c2", "c3"]


def test_full_catalog_list_is_grant_filtered_too(env):
    ids = [m["id"] for m in client.get("/api/catalog", headers=env["user"]).json()["motors"]]
    assert ids == ["c1"]


def test_configure_context_of_an_ungranted_card_is_404(env):
    assert client.get("/api/catalog/c1/configure_context", headers=env["user"]).status_code == 200
    assert client.get("/api/catalog/c2/configure_context", headers=env["user"]).status_code == 404
    assert client.get("/api/catalog/c2/configure_context", headers=env["admin"]).status_code == 200


def test_an_all_grant_sees_every_card(env):
    env["U"].set_motor_grants(USER, all_motors=True)
    ids = sorted(m["id"] for m in client.get("/api/catalog/references",
                                             headers=env["user"]).json()["motors"])
    assert ids == ["c1", "c2", "c3"]


# -- 3. the default motor --------------------------------------------------------

def _put(env, body):
    return client.put(f"/api/admin/users/{USER}/motors", headers=env["admin"], json=body)


def test_admin_sets_a_default_and_the_user_receives_it(env):
    r = _put(env, {"all": False, "dies": [GRANTED],
                   "default": {"die": GRANTED, "config": CFG2}})
    assert r.status_code == 200, r.text
    assert r.json()["motors"]["default"] == {"die": GRANTED, "config": CFG2}
    me = client.get("/api/me", headers=env["user"]).json()
    assert me["defaultMotor"] == {"die": GRANTED, "config": CFG2}
    rows = client.get("/api/auth/users", headers=env["admin"]).json()["users"]
    mine = next(x for x in rows if x["email"] == USER)
    assert mine["motors"]["default"] == {"die": GRANTED, "config": CFG2}


def test_default_must_be_granted(env):
    r = _put(env, {"all": False, "dies": [GRANTED],
                   "default": {"die": UNGRANTED, "config": CFG}})
    assert r.status_code == 422 and "not granted" in r.json()["detail"]
    assert "default" not in env["U"].get_motor_grants(USER)


def test_default_must_name_an_existing_configuration_and_die(env):
    r = _put(env, {"all": False, "dies": [GRANTED],
                   "default": {"die": GRANTED, "config": "L99"}})
    assert r.status_code == 422 and "L99" in r.json()["detail"]
    r = _put(env, {"all": True, "default": {"die": "NO SUCH", "config": CFG}})
    assert r.status_code == 422
    r = _put(env, {"all": False, "dies": [GRANTED], "default": {"die": GRANTED}})
    assert r.status_code == 422


def test_default_is_kept_then_cleared_with_the_grant(env):
    assert _put(env, {"all": False, "dies": [GRANTED],
                      "default": {"die": GRANTED, "config": CFG}}).status_code == 200
    # key absent + the grant still covers it -> kept
    assert _put(env, {"all": False, "dies": [GRANTED, UNGRANTED]}).json()["motors"]["default"] \
        == {"die": GRANTED, "config": CFG}
    # grant removed -> default gone
    r = _put(env, {"all": False, "dies": [UNGRANTED]})
    assert "default" not in r.json()["motors"]
    assert client.get("/api/me", headers=env["user"]).json()["defaultMotor"] is None


def test_default_can_be_cleared_explicitly_and_an_all_grant_allows_any_die(env):
    assert _put(env, {"all": True,
                      "default": {"die": UNGRANTED, "config": CFG}}).status_code == 200
    assert env["U"].get_default_motor(USER) == {"die": UNGRANTED, "config": CFG}
    r = _put(env, {"all": True, "default": None})
    assert "default" not in r.json()["motors"]


def test_stale_default_in_the_registry_is_ignored_on_read(env):
    """A hand-edited / older users.json whose default is no longer covered."""
    from motor_ai_sim import users as U
    raw = json.loads(U._USERS_FILE.read_text(encoding="utf-8"))
    raw[USER]["motors"] = {"all": False, "dies": [GRANTED],
                           "default": {"die": UNGRANTED, "config": CFG}}
    U._USERS_FILE.write_text(json.dumps(raw), encoding="utf-8")
    assert U.get_default_motor(USER) is None
    assert client.get("/api/me", headers=env["user"]).json()["defaultMotor"] is None


# -- 4. the "full passport card" flag follows the store --------------------------

def _record(store: Path, die: str, cfg: str) -> Path:
    f = store / die / f"{cfg}.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"schema": "passport-v1-full-1",
                             "machine": {"die": die, "configuration": cfg},
                             "pwm_variants": []}), encoding="utf-8")
    return f


@pytest.fixture()
def store(tmp_path, monkeypatch):
    from motor_ai_sim import passport_store as ps
    d = tmp_path / "passports"
    d.mkdir()
    monkeypatch.setattr(ps, "_DIR", d)
    return d


def _admin_die(env, die):
    rows = client.get("/api/admin/motors", headers=env["admin"]).json()["dies"]
    return next(x for x in rows if x["name"] == die)


def test_admin_motor_list_flags_the_configurations_that_have_a_full_card(env, store):
    assert _admin_die(env, GRANTED)["cards"] == {}
    f = _record(store, GRANTED, CFG)
    row = _admin_die(env, GRANTED)
    assert list(row["cards"]) == [CFG] and row["cards"][CFG]            # a date
    assert row["config_names"] == [CFG, CFG2]                             # 1 of 2 -> "1/2 configs"
    _record(store, GRANTED, CFG2)
    assert sorted(_admin_die(env, GRANTED)["cards"]) == [CFG, CFG2]
    f.unlink()
    assert list(_admin_die(env, GRANTED)["cards"]) == [CFG2]
    assert _admin_die(env, UNGRANTED)["cards"] == {}


def test_a_record_that_is_not_a_v1_passport_is_not_a_card(env, store):
    f = store / GRANTED / f"{CFG}.json"
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps({"schema": "something-else", "pwm_variants": []}), encoding="utf-8")
    assert _admin_die(env, GRANTED)["cards"] == {}


def test_configure_lists_the_flag_on_references_and_presets(env, store):
    def refs():
        return {m["id"]: m["card"] for m in client.get(
            "/api/catalog/references", headers=env["user"]).json()["motors"]}

    def presets():
        j = client.get("/api/catalog/c1/configure_context", headers=env["user"]).json()
        return {p["config"]: p["card_date"] for p in j["presets"]}

    assert refs() == {"c1": None}
    assert presets() == {CFG: None, CFG2: None}
    f = _record(store, GRANTED, CFG)
    assert refs()["c1"]["config"] == CFG and refs()["c1"]["date"]
    assert presets()[CFG] and presets()[CFG2] is None
    f.unlink()
    assert refs() == {"c1": None} and presets() == {CFG: None, CFG2: None}


def test_motors_tab_tree_flags_cards_per_configuration_for_a_regular_user(env, store):
    def die():
        return client.get("/api/family/tree", headers=env["user"]).json()["dies"][0]

    d = die()
    assert d["cards"] == 0 and d["config_count"] == 2
    assert [c["card_date"] for c in d["configs"]] == [None, None]
    f = _record(store, GRANTED, CFG)
    d = die()
    assert d["cards"] == 1 and d["config_count"] == 2
    got = {c["name"]: c["card_date"] for c in d["configs"]}
    assert got[CFG] and got[CFG2] is None
    f.unlink()
    assert die()["cards"] == 0


# -- 5. a regular user's saves land in HIS workspace, never in a shared store ------

def test_my_motor_settings_save_is_his_own_workspace_only(env, tmp_path, monkeypatch):
    """The live log showed POST /api/presets/my_motor/settings 200 for a regular
    account.  It is allowed because the preset store resolves to the caller's
    workspace; this pins that it can reach neither the shared store nor another
    account's workspace, nor a motor of someone else."""
    import hashlib
    from motor_ai_sim import workspace as W
    from motor_ai_sim.routes import presets as _presets
    # the suite pins the preset store to a sandbox file; production resolves it
    # per workspace (MOTOR_AI_SIM_PRESETS unset) - which is what is under test
    monkeypatch.setattr(_presets, "_PRESETS_ENV", None)
    from motor_ai_sim import users as U

    OTHER = "other@example.com"
    U.create_user(OTHER, "password-other", role="user", name="Other")
    W.provision(OTHER)
    import os
    shared_presets = Path(os.environ["SHARED_ROOT"]) / "motor_presets.json"
    ws_mine = Path(os.environ["WORKSPACES_ROOT"]) / W.workspace_id(USER)
    ws_other = Path(os.environ["WORKSPACES_ROOT"]) / W.workspace_id(OTHER)

    def put(path, d):
        path.write_text(json.dumps(d), encoding="utf-8")

    entry = lambda owner: {"name": "my motor", "owner": owner, "geometry": {"motor_length": 40},
                           "mesh": {}, "simulation": {}}
    put(shared_presets, {"my_motor": entry("admin"), "template": entry("admin")})
    put(ws_mine / "motor_presets.json", {"my_motor": entry(USER), "theirs": entry(OTHER)})
    put(ws_other / "motor_presets.json", {"my_motor": entry(OTHER)})
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    before = (digest(shared_presets), digest(ws_other / "motor_presets.json"))

    r = client.post("/api/presets/my_motor/settings", headers=env["user"],
                    json={"mesh": {"gap_layers": 3}})
    assert r.status_code == 200, r.text
    saved = json.loads((ws_mine / "motor_presets.json").read_text(encoding="utf-8"))
    assert saved["my_motor"]["mesh"]["gap_layers"] == 3
    assert (digest(shared_presets), digest(ws_other / "motor_presets.json")) == before

    # a shared template is not in his store at all; a motor owned by someone else
    # who happens to sit in his store is refused
    assert client.post("/api/presets/template/settings", headers=env["user"],
                       json={"mesh": {"gap_layers": 9}}).status_code == 404
    assert client.post("/api/presets/theirs/settings", headers=env["user"],
                       json={"mesh": {"gap_layers": 9}}).status_code == 403
    assert (digest(shared_presets), digest(ws_other / "motor_presets.json")) == before


def test_configure_keeps_shared_references_when_account_has_its_own_catalog(env, tmp_path, monkeypatch):
    """A user's catalog supplements shared Configure references.

    Personal catalog writes must not shadow the shared characterized motors:
    distinct stable IDs are both candidates, while the grant still limits the
    response to this account's die and never exposes the ungranted shared card.
    """
    import os
    from motor_ai_sim.routes import catalog as cat
    monkeypatch.delattr(cat, "_CATALOG_PATH", raising=False)
    shared = Path(os.environ["SHARED_ROOT"]) / "motor_catalog.json"
    shared.write_text(json.dumps({"tiers": [], "diameters_mm": [40], "motors": [
        _card("c1", f"{GRANTED} {CFG}"), _card("c2", f"{UNGRANTED} {CFG}")]}), encoding="utf-8")
    ids = [m["id"] for m in client.get("/api/catalog/references", headers=env["user"]).json()["motors"]]
    assert ids == ["c1"], "granted-only: the shared card of his die, nothing else"
    # His workspace card is listed first, while the distinct shared ID remains
    # available. The ungranted shared card is still withheld.
    from motor_ai_sim import workspace as W
    own = Path(os.environ["WORKSPACES_ROOT"]) / W.workspace_id(USER) / "motor_catalog.json"
    own.write_text(json.dumps({"tiers": [], "diameters_mm": [40], "motors": [
        _card("mine", f"{GRANTED} {CFG2}")]}), encoding="utf-8")
    ids = [m["id"] for m in client.get("/api/catalog/references", headers=env["user"]).json()["motors"]]
    assert ids == ["mine", "c1"]
    assert "c2" not in ids, "combining sources must not bypass die grants"
    assert shared.read_text(encoding="utf-8").count('"c1"') == 1, "the shared file is never written"
