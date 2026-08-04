"""A saved motor belongs to somebody, and nobody else may overwrite it.

Every signed-in visitor used to be able to overwrite, rename or delete any
motor in the store — and the editor's 0.9 s auto-save wrote into whatever preset
happened to be loaded, so merely LOADING someone else's design started
overwriting it.  With external clients on the way that is not a permissions
nicety, it is the difference between a shared library and a shared accident.

The rules under test:

* every mutating path (overwrite, rename, delete, the /settings auto-save, and
  the catalog-side card mutations) refuses a caller who is neither the owner nor
  an admin — with a 403 that NAMES the owner, because "Forbidden" alone leaves
  the engineer reading it with nowhere to go;
* an explicit `locked` flag beats ownership: a locked motor is read-only for
  everyone but an admin, its owner included (that is what the owner asked for);
* legacy entries — written before ownership existed — are back-filled ONCE to
  the admin, who created them.  An unowned entry would be an unprotected hole,
  and the back-fill must not disturb the fork behaviour those entries have;
* a load the caller cannot write does not 403-spam: it either forks into their
  own copy or opens read-only, and says which.

Identity comes from the app's own mechanism (`auth.caller_identity` over the
Firebase bearer token) — the token verification is faked, nothing else is.
"""
from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

GEO_A = {"stator_diameter": 40.0, "num_seg": 1, "num_slots_per_segment": 12,
         "num_poles_per_segment": 14, "motor_length": 35.0}
GEO_B = {"stator_diameter": 30.0, "num_seg": 1, "num_slots_per_segment": 12,
         "num_poles_per_segment": 10, "motor_length": 35.0}

ADMIN = "Bearer admin@example.com"
ALICE = "Bearer alice@example.com"
BOB = "Bearer bob@example.com"
CHARLIE = "Bearer charlie@example.com"


# ── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture()
def stores(tmp_path, monkeypatch):
    """A synthetic presets + catalog store, isolated from config/."""
    from motor_ai_sim.routes import catalog as cat_mod, presets as pre_mod

    presets_path = tmp_path / "motor_presets.json"
    catalog_path = tmp_path / "motor_catalog.json"
    presets_path.write_text("{}", encoding="utf-8")
    catalog_path.write_text(
        json.dumps({"tiers": [], "diameters_mm": [], "motors": []}), encoding="utf-8")

    monkeypatch.setattr(pre_mod, "_PRESETS_PATH", presets_path)
    monkeypatch.setattr(pre_mod, "_CATALOG_PATH", catalog_path)
    monkeypatch.setattr(cat_mod, "_CATALOG_PATH", catalog_path)
    monkeypatch.setattr(pre_mod, "_gen_thumb_svg", lambda geo: None)
    monkeypatch.setattr(pre_mod, "_enrich_card_entry",
                        lambda entry, geo, sim, met: None)
    monkeypatch.setattr("motor_ai_sim.config.get_config",
                        lambda: {"geometry": dict(GEO_A)})
    return {"presets": presets_path, "catalog": catalog_path}


@pytest.fixture(autouse=True)
def identities(monkeypatch):
    """Make the real identity path usable in-process: only the RS256 token
    verification is faked (a bearer whose body is an email is that account).
    Admin-ness, the tier ladder and `caller_identity` are the shipping code."""
    from motor_ai_sim import auth

    admins = {"admin@example.com"}
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", admins)
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)

    def _fake_resolve(authorization):
        if not isinstance(authorization, str):
            return None
        if not authorization.lower().startswith("bearer "):
            return None
        email = authorization.split(" ", 1)[1].strip().lower()
        return {"uid": f"uid-{email}", "email": email,
                "tier": "admin" if email in admins else "free"}

    monkeypatch.setattr(auth, "resolve_user", _fake_resolve)


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _save(pre_mod, pid, name, geo, who):
    return pre_mod.save_current_as_preset(
        pre_mod.SavePresetRequest(id=pid, name=name, geometry=geo,
                                  simulation={"rpm": 5000, "max_current": 30}),
        authorization=who)


# ── ownership: the four mutating paths ──────────────────────────────────────

def test_non_owner_overwrite_is_refused_and_names_the_owner(stores, monkeypatch):
    from motor_ai_sim.routes import presets as pre_mod

    _save(pre_mod, "m_a", "Motor A", GEO_A, ALICE)
    with pytest.raises(HTTPException) as e:
        _save(pre_mod, "m_a", "Hijacked", GEO_B, BOB)
    assert e.value.status_code == 403
    assert "alice@example.com" in e.value.detail
    assert "Motor A" in e.value.detail          # WHICH motor, by its name
    # …and nothing moved: the refusal is not a partial write.
    stored = _read(stores["presets"])["m_a"]
    assert stored["name"] == "Motor A"
    assert stored["geometry"]["stator_diameter"] == 40.0
    assert stored["owner"] == "alice@example.com"


def test_non_owner_rename_is_refused(stores):
    from motor_ai_sim.routes import presets as pre_mod

    _save(pre_mod, "m_a", "Motor A", GEO_A, ALICE)
    with pytest.raises(HTTPException) as e:
        pre_mod.rename_preset("m_a", pre_mod.RenamePresetRequest(name="Bob's now"),
                              authorization=BOB)
    assert e.value.status_code == 403 and "alice@example.com" in e.value.detail
    assert _read(stores["presets"])["m_a"]["name"] == "Motor A"


def test_non_owner_delete_is_refused(stores):
    from motor_ai_sim.routes import presets as pre_mod

    _save(pre_mod, "m_a", "Motor A", GEO_A, ALICE)
    with pytest.raises(HTTPException) as e:
        pre_mod.delete_preset("m_a", authorization=BOB)
    assert e.value.status_code == 403 and "alice@example.com" in e.value.detail
    assert "m_a" in _read(stores["presets"])


def test_non_owner_autosave_sync_is_refused(stores):
    """The 0.9 s auto-save is a write like any other — the path that silently
    rewrote whatever preset was loaded."""
    from motor_ai_sim.routes import presets as pre_mod

    _save(pre_mod, "m_a", "Motor A", GEO_A, ALICE)
    with pytest.raises(HTTPException) as e:
        pre_mod.save_motor_settings(
            "m_a", pre_mod.SettingsPatch(geometry=GEO_B, simulation={"rpm": 9000}),
            authorization=BOB)
    assert e.value.status_code == 403 and "alice@example.com" in e.value.detail
    assert _read(stores["presets"])["m_a"]["geometry"]["stator_diameter"] == 40.0


def test_owner_may_write_their_own_motor(stores):
    from motor_ai_sim.routes import presets as pre_mod

    _save(pre_mod, "m_a", "Motor A", GEO_A, ALICE)
    pre_mod.save_motor_settings("m_a", pre_mod.SettingsPatch(simulation={"rpm": 9000}),
                                authorization=ALICE)
    pre_mod.rename_preset("m_a", pre_mod.RenamePresetRequest(name="Motor A2"),
                          authorization=ALICE)
    assert _read(stores["presets"])["m_a"]["name"] == "Motor A2"
    assert _read(stores["presets"])["m_a"]["simulation"]["rpm"] == 9000
    pre_mod.delete_preset("m_a", authorization=ALICE)
    assert "m_a" not in _read(stores["presets"])


def test_admin_bypasses_ownership(stores):
    from motor_ai_sim.routes import presets as pre_mod

    _save(pre_mod, "m_a", "Motor A", GEO_A, ALICE)
    pre_mod.save_motor_settings("m_a", pre_mod.SettingsPatch(simulation={"rpm": 7000}),
                                authorization=ADMIN)
    pre_mod.rename_preset("m_a", pre_mod.RenamePresetRequest(name="Admin fixed it"),
                          authorization=ADMIN)
    # An admin's repair does NOT quietly transfer ownership to the admin.
    _save(pre_mod, "m_a", "Admin fixed it", GEO_B, ADMIN)
    assert _read(stores["presets"])["m_a"]["owner"] == "alice@example.com"
    pre_mod.delete_preset("m_a", authorization=ADMIN)
    assert "m_a" not in _read(stores["presets"])


def test_new_save_stamps_the_creator_as_owner(stores):
    from motor_ai_sim.routes import presets as pre_mod

    _save(pre_mod, "m_bob", "Bob's motor", GEO_B, BOB)
    entry = _read(stores["presets"])["m_bob"]
    assert entry["owner"] == "bob@example.com"
    assert entry["template"] is False
    assert entry.get("locked") is False
    # …and its catalog card carries the same owner, not a generic "user".
    card = _read(stores["catalog"])["motors"][0]
    assert card["owner"] == "bob@example.com"


def test_anonymous_caller_cannot_touch_a_named_owners_motor(stores):
    from motor_ai_sim.routes import presets as pre_mod

    _save(pre_mod, "m_a", "Motor A", GEO_A, ALICE)
    with pytest.raises(HTTPException) as e:
        pre_mod.delete_preset("m_a", authorization=None)
    assert e.value.status_code == 403 and "alice@example.com" in e.value.detail


# ── the lock: beats ownership, admin-only ───────────────────────────────────

def test_lock_beats_ownership(stores):
    """A locked motor is read-only for EVERYONE but an admin — including the
    owner, who is exactly who asked for the lock."""
    from motor_ai_sim.routes import presets as pre_mod

    _save(pre_mod, "m_a", "Motor A", GEO_A, ALICE)
    out = pre_mod.set_preset_lock("m_a", pre_mod.LockRequest(locked=True),
                                  _admin={"uid": "u", "tier": "admin"})
    assert out["locked"] is True

    for call in (
        lambda: _save(pre_mod, "m_a", "Motor A", GEO_B, ALICE),
        lambda: pre_mod.rename_preset("m_a", pre_mod.RenamePresetRequest(name="x"),
                                      authorization=ALICE),
        lambda: pre_mod.delete_preset("m_a", authorization=ALICE),
        lambda: pre_mod.save_motor_settings(
            "m_a", pre_mod.SettingsPatch(simulation={"rpm": 1}), authorization=ALICE),
    ):
        with pytest.raises(HTTPException) as e:
            call()
        assert e.value.status_code == 403
        assert "locked" in e.value.detail and "Motor A" in e.value.detail

    # The admin still writes, and can lift the lock.
    pre_mod.save_motor_settings("m_a", pre_mod.SettingsPatch(simulation={"rpm": 4200}),
                                authorization=ADMIN)
    pre_mod.set_preset_lock("m_a", pre_mod.LockRequest(locked=False),
                            _admin={"uid": "u", "tier": "admin"})
    pre_mod.rename_preset("m_a", pre_mod.RenamePresetRequest(name="Unlocked again"),
                          authorization=ALICE)
    assert _read(stores["presets"])["m_a"]["name"] == "Unlocked again"


def test_lock_flag_reaches_the_catalog_card(stores):
    from motor_ai_sim.routes import catalog as cat_mod, presets as pre_mod

    _save(pre_mod, "m_a", "Motor A", GEO_A, ALICE)
    pre_mod.set_preset_lock("m_a", pre_mod.LockRequest(locked=True),
                            _admin={"uid": "u", "tier": "admin"})
    assert _read(stores["catalog"])["motors"][0]["locked"] is True
    card = cat_mod.get_catalog(authorization=ALICE)["motors"][0]
    assert card["locked"] is True and card["can_write"] is False
    assert card["owner"] == "alice@example.com"
    assert cat_mod.get_catalog(authorization=ADMIN)["motors"][0]["can_write"] is True


def test_lock_endpoint_is_admin_only_over_http(stores, monkeypatch):
    """The toggle itself is gated by the project's `require_admin` dependency."""
    from fastapi.testclient import TestClient

    from motor_ai_sim.api import app
    from motor_ai_sim.routes import presets as pre_mod

    _save(pre_mod, "m_a", "Motor A", GEO_A, ALICE)
    client = TestClient(app)
    body = {"locked": True}
    assert client.post("/api/presets/m_a/lock", json=body).status_code == 401
    assert client.post("/api/presets/m_a/lock", json=body,
                       headers={"Authorization": ALICE}).status_code == 403
    r = client.post("/api/presets/m_a/lock", json=body,
                    headers={"Authorization": ADMIN})
    assert r.status_code == 200 and r.json()["locked"] is True
    # …and the 403 an ordinary write gets travels as a 403 over the wire too.
    r = client.request("DELETE", "/api/presets/m_a", headers={"Authorization": BOB})
    assert r.status_code == 403 and "alice@example.com" in r.json()["detail"]


# ── the back-fill: no legacy entry is an unprotected hole ───────────────────

def test_backfill_stamps_legacy_entries_once_and_is_idempotent(stores):
    from motor_ai_sim.routes import presets as pre_mod

    stores["presets"].write_text(json.dumps({
        # written before the owner field existed → a curated template
        "curated": {"id": "curated", "name": "Curated", "geometry": GEO_A},
        # the legacy "user" sentinel → the user's own copy, not a template
        "mine": {"id": "mine", "name": "Mine", "owner": "user", "geometry": GEO_B},
    }), encoding="utf-8")
    stores["catalog"].write_text(json.dumps({
        "tiers": [], "diameters_mm": [30],
        "motors": [{"id": "cat_mine", "name": "Mine", "preset": "mine",
                    "diameter_mm": 30, "owner": "user"}]}), encoding="utf-8")

    pre_mod._backfill_owners()
    doc = _read(stores["presets"])
    assert doc["curated"]["owner"] == "admin"
    assert doc["mine"]["owner"] == "admin"
    # The fork behaviour each entry had is PRESERVED, not re-decided.
    assert doc["curated"]["template"] is True
    assert doc["mine"]["template"] is False
    assert _read(stores["catalog"])["motors"][0]["owner"] == "admin"

    # Idempotent: a second pass changes nothing at all.
    before = stores["presets"].read_text(encoding="utf-8")
    before_cat = stores["catalog"].read_text(encoding="utf-8")
    pre_mod._backfill_owners()
    assert stores["presets"].read_text(encoding="utf-8") == before
    assert stores["catalog"].read_text(encoding="utf-8") == before_cat


def test_a_legacy_entry_is_not_an_unprotected_hole(stores):
    from motor_ai_sim.routes import presets as pre_mod

    stores["presets"].write_text(json.dumps({
        "mine": {"id": "mine", "name": "Legacy motor", "owner": "user",
                 "geometry": GEO_B, "simulation": {}, "mesh": {}},
    }), encoding="utf-8")

    with pytest.raises(HTTPException) as e:
        pre_mod.save_motor_settings("mine", pre_mod.SettingsPatch(geometry=GEO_A),
                                    authorization=BOB)
    assert e.value.status_code == 403 and "admin" in e.value.detail
    # The admin, who wrote it, still can.
    pre_mod.save_motor_settings("mine", pre_mod.SettingsPatch(simulation={"rpm": 100}),
                                authorization=ADMIN)
    assert _read(stores["presets"])["mine"]["simulation"]["rpm"] == 100


# ── catalog-side mutations ──────────────────────────────────────────────────

def test_catalog_delete_and_passport_respect_ownership(stores, monkeypatch):
    from motor_ai_sim.routes import catalog as cat_mod, presets as pre_mod

    _save(pre_mod, "m_a", "Motor A", GEO_A, ALICE)
    with pytest.raises(HTTPException) as e:
        cat_mod.delete_motor("cat_m_a", authorization=BOB)
    assert e.value.status_code == 403 and "alice@example.com" in e.value.detail
    assert _read(stores["catalog"])["motors"]           # card still there
    assert "m_a" in _read(stores["presets"])            # and its motor

    with pytest.raises(HTTPException) as e:
        cat_mod.generate_motor_passport("cat_m_a", coarse=True, authorization=BOB)
    assert e.value.status_code == 403

    cat_mod.delete_motor("cat_m_a", authorization=ADMIN)
    assert _read(stores["catalog"])["motors"] == []
    assert "m_a" not in _read(stores["presets"])


# ── fork semantics: a load you cannot write does not 403-spam ───────────────

def test_opening_someone_elses_motor_forks_into_your_own_copy(stores, monkeypatch):
    from motor_ai_sim.routes import presets as pre_mod

    monkeypatch.setattr(pre_mod, "apply_preset",
                        lambda pid, authorization=None: {
                            "applied": pid, "preset": pre_mod._load_presets()[pid],
                            "writable": pre_mod._can_write(
                                pre_mod._load_presets()[pid],
                                pre_mod._caller_identity(authorization)),
                        })
    _save(pre_mod, "shared", "Shared motor", GEO_A, ALICE)

    out = pre_mod.open_motor("shared", authorization=BOB)
    assert out["applied"] == "my_shared" and out["writable"] is True
    assert _read(stores["presets"])["my_shared"]["owner"] == "bob@example.com"
    # Alice's motor is untouched by Bob's arrival.
    assert _read(stores["presets"])["shared"]["owner"] == "alice@example.com"
    assert _read(stores["presets"])["shared"]["name"] == "Shared motor"


def test_a_load_with_nowhere_writable_opens_read_only(stores, monkeypatch):
    """Bob already owns `my_shared`; Charlie must NOT be forked into Bob's copy —
    that is the very overwrite this feature exists to prevent.  He gets the
    original, read-only, and the editor suspends its auto-save."""
    from motor_ai_sim.routes import presets as pre_mod

    monkeypatch.setattr(pre_mod, "apply_preset",
                        lambda pid, authorization=None: {
                            "applied": pid, "preset": pre_mod._load_presets()[pid],
                            "writable": pre_mod._can_write(
                                pre_mod._load_presets()[pid],
                                pre_mod._caller_identity(authorization)),
                        })
    _save(pre_mod, "shared", "Shared motor", GEO_A, ALICE)
    pre_mod.open_motor("shared", authorization=BOB)          # takes my_shared

    out = pre_mod.open_motor("shared", authorization=CHARLIE)
    assert out["applied"] == "shared" and out["writable"] is False
    assert _read(stores["presets"])["my_shared"]["owner"] == "bob@example.com"

    # …and the auto-save that follows such a load is the one that would 403 —
    # which is why the editor is told `writable: false` at load time.
    with pytest.raises(HTTPException) as e:
        pre_mod.save_motor_settings("shared", pre_mod.SettingsPatch(geometry=GEO_B),
                                    authorization=CHARLIE)
    assert e.value.status_code == 403


def test_apply_reports_writability_to_the_editor(stores, monkeypatch):
    from motor_ai_sim.routes import presets as pre_mod

    _save(pre_mod, "m_a", "Motor A", GEO_A, ALICE)
    monkeypatch.setattr(pre_mod, "_CONFIG_PATH", stores["presets"].parent / "cfg.yaml")
    (stores["presets"].parent / "cfg.yaml").write_text(
        "geometry: {}\nsimulation: {}\nmesh: {}\n", encoding="utf-8")

    assert pre_mod.apply_preset("m_a", ALICE)["writable"] is True
    out = pre_mod.apply_preset("m_a", BOB)
    assert out["writable"] is False and out["owner"] == "alice@example.com"
