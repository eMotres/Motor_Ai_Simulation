"""Motor cards answer two questions: WHEN was this saved, and IS THIS the one
I am editing.

Both are honesty-sensitive:

* `saved_at` must be stamped by every path that WRITES a motor, and must be a
  real instant on a shared timeline (tz-aware UTC) — a naive local string is
  unplaceable by a browser in another zone.  A motor written before the field
  existed keeps NO `saved_at`: the store file's mtime belongs to the whole file,
  not to that entry, and back-filling from it would be a fabricated fact.
* `is_active` must follow the geometry STAMP, never the name.  Exactly the card
  whose geometry the editor is on is flagged; when the geometry has been edited
  since the last save, nothing matches, and that is the correct answer.

No solver, no FEM: the stores are temp files and the config is monkeypatched.
"""
from __future__ import annotations

import datetime as dt
import json

import pytest


# ── helpers ─────────────────────────────────────────────────────────────────

GEO_A = {"stator_diameter": 40.0, "num_seg": 1, "num_slots_per_segment": 12,
         "num_poles_per_segment": 14, "motor_length": 35.0}
GEO_B = {"stator_diameter": 30.0, "num_seg": 1, "num_slots_per_segment": 12,
         "num_poles_per_segment": 10, "motor_length": 35.0}


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
    # Thumbnail generation runs CadQuery — irrelevant here and slow.
    monkeypatch.setattr(pre_mod, "_gen_thumb_svg", lambda geo: None)
    # Card enrichment reaches into config/materials/masses; the fields it fills
    # are not what these tests are about.
    monkeypatch.setattr(pre_mod, "_enrich_card_entry",
                        lambda entry, geo, sim, met: None)
    return {"presets": presets_path, "catalog": catalog_path}


def _set_live_geometry(monkeypatch, geo: dict | None):
    """Make `config.get_config()` report `geo` as the editor's machine."""
    cfg = {"geometry": dict(geo)} if geo is not None else {}
    monkeypatch.setattr("motor_ai_sim.config.get_config", lambda: cfg)


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _assert_utc_iso(value, where: str):
    assert isinstance(value, str) and value, f"{where}: no saved_at stamped"
    parsed = dt.datetime.fromisoformat(value)      # must be ISO-8601, parseable
    assert parsed.tzinfo is not None, f"{where}: saved_at is naive — unplaceable"
    assert parsed.utcoffset() == dt.timedelta(0), f"{where}: saved_at is not UTC"
    return parsed


# ── saved_at: every write path stamps it ────────────────────────────────────

def test_save_as_new_motor_stamps_saved_at(stores, monkeypatch):
    """POST /api/presets with an explicit geometry (the "Save as new motor" path)."""
    from motor_ai_sim.routes import presets as pre_mod

    _set_live_geometry(monkeypatch, GEO_A)
    before = dt.datetime.now(dt.timezone.utc)
    out = pre_mod.save_current_as_preset(pre_mod.SavePresetRequest(
        id="m_a", name="Motor A", geometry=GEO_A,
        simulation={"rpm": 5000, "max_current": 30}))
    after = dt.datetime.now(dt.timezone.utc)

    stamp = _assert_utc_iso(_read(stores["presets"])["m_a"]["saved_at"], "preset")
    # timespec="seconds" truncates, so allow the second the call started in.
    assert before - dt.timedelta(seconds=1) <= stamp <= after
    # …and the listing hands it to the client.
    assert out is not None
    assert _assert_utc_iso(
        pre_mod.list_presets()["presets"][0]["saved_at"], "listing")
    # …and the card mirrors the preset's stamp rather than reading its own clock.
    card = _read(stores["catalog"])["motors"][0]
    assert card["saved_at"] == _read(stores["presets"])["m_a"]["saved_at"]


def test_autosave_settings_stamps_saved_at(stores, monkeypatch):
    """POST /api/presets/{id}/settings — the auto-save that syncs the active motor."""
    from motor_ai_sim.routes import presets as pre_mod

    _set_live_geometry(monkeypatch, GEO_A)
    pre_mod.save_current_as_preset(pre_mod.SavePresetRequest(
        id="m_a", name="Motor A", geometry=GEO_A))
    first = _read(stores["presets"])["m_a"]["saved_at"]

    # Wipe the stamp to prove the settings path writes its own (rather than the
    # test passing on the leftover from the save above).
    doc = _read(stores["presets"])
    doc["m_a"].pop("saved_at")
    stores["presets"].write_text(json.dumps(doc), encoding="utf-8")

    res = pre_mod.save_motor_settings("m_a", pre_mod.SettingsPatch(
        geometry=GEO_B, simulation={"rpm": 6000}))
    assert res["status"] == "ok"
    second = _assert_utc_iso(_read(stores["presets"])["m_a"]["saved_at"], "autosave")
    assert second >= dt.datetime.fromisoformat(first) - dt.timedelta(seconds=1)


def test_rename_stamps_saved_at(stores, monkeypatch):
    """PATCH /api/presets/{id} — a rename is a write to the motor."""
    from motor_ai_sim.routes import presets as pre_mod

    _set_live_geometry(monkeypatch, GEO_A)
    pre_mod.save_current_as_preset(pre_mod.SavePresetRequest(
        id="m_a", name="Motor A", geometry=GEO_A))
    doc = _read(stores["presets"])
    doc["m_a"].pop("saved_at")
    stores["presets"].write_text(json.dumps(doc), encoding="utf-8")

    pre_mod.rename_preset("m_a", pre_mod.RenamePresetRequest(name="Motor A2"))
    _assert_utc_iso(_read(stores["presets"])["m_a"]["saved_at"], "rename")


def test_legacy_entry_keeps_no_saved_at(stores, monkeypatch):
    """A motor written before the field existed is reported WITHOUT a time —
    never back-filled from the store file's mtime, which is not its save time."""
    from motor_ai_sim.routes import catalog as cat_mod, presets as pre_mod

    _set_live_geometry(monkeypatch, GEO_A)
    stores["presets"].write_text(json.dumps({
        "legacy": {"id": "legacy", "name": "Old motor", "owner": "user",
                   "geometry": GEO_B, "simulation": {}, "mesh": {}},
    }), encoding="utf-8")
    stores["catalog"].write_text(json.dumps({
        "tiers": [], "diameters_mm": [30], "motors": [
            {"id": "cat_legacy", "name": "Old motor", "preset": "legacy",
             "diameter_mm": 30, "owner": "user"}]}), encoding="utf-8")

    assert pre_mod.list_presets()["presets"][0]["saved_at"] is None
    card = cat_mod.get_catalog()["motors"][0]
    assert card.get("saved_at") is None, "a save time was invented for a legacy motor"


# ── is_active: exactly the card the editor is on ────────────────────────────

def test_is_active_marks_only_the_loaded_geometry(stores, monkeypatch):
    """Two saved motors, the editor on one of them: exactly that card is flagged."""
    from motor_ai_sim.routes import catalog as cat_mod, presets as pre_mod

    _set_live_geometry(monkeypatch, GEO_A)
    pre_mod.save_current_as_preset(pre_mod.SavePresetRequest(
        id="m_a", name="Motor A", geometry=GEO_A))
    pre_mod.save_current_as_preset(pre_mod.SavePresetRequest(
        id="m_b", name="Motor B", geometry=GEO_B))

    flags = {m["id"]: m["is_active"] for m in cat_mod.get_catalog()["motors"]}
    assert flags == {"cat_m_a": True, "cat_m_b": False}

    # Load the other motor: the flag MOVES, it is not sticky.
    _set_live_geometry(monkeypatch, GEO_B)
    flags = {m["id"]: m["is_active"] for m in cat_mod.get_catalog()["motors"]}
    assert flags == {"cat_m_a": False, "cat_m_b": True}


def test_edited_geometry_matches_no_card(stores, monkeypatch):
    """The user nudged a dimension since the last save -> NOTHING is highlighted.
    Falling back to "probably still that one" would be a claim we cannot make."""
    from motor_ai_sim.routes import catalog as cat_mod, presets as pre_mod

    _set_live_geometry(monkeypatch, GEO_A)
    pre_mod.save_current_as_preset(pre_mod.SavePresetRequest(
        id="m_a", name="Motor A", geometry=GEO_A))

    _set_live_geometry(monkeypatch, {**GEO_A, "motor_length": 35.5})
    assert [m["is_active"] for m in cat_mod.get_catalog()["motors"]] == [False]


def test_is_active_ignores_names_and_follows_the_stamp(stores, monkeypatch):
    """Same NAME, different machine: the name must not carry the highlight."""
    from motor_ai_sim.routes import catalog as cat_mod, presets as pre_mod

    _set_live_geometry(monkeypatch, GEO_A)
    pre_mod.save_current_as_preset(pre_mod.SavePresetRequest(
        id="twin_a", name="CIANO 150_40", geometry=GEO_A))
    pre_mod.save_current_as_preset(pre_mod.SavePresetRequest(
        id="twin_b", name="CIANO 150_40", geometry=GEO_B))

    flags = {m["id"]: m["is_active"] for m in cat_mod.get_catalog()["motors"]}
    assert flags == {"cat_twin_a": True, "cat_twin_b": False}


def test_unknown_live_geometry_highlights_nothing(stores, monkeypatch):
    """No readable config -> no claim.  An unknown editor state must not match a
    card by accident (e.g. both sides collapsing to the empty stamp)."""
    from motor_ai_sim.routes import catalog as cat_mod, presets as pre_mod

    _set_live_geometry(monkeypatch, GEO_A)
    pre_mod.save_current_as_preset(pre_mod.SavePresetRequest(
        id="m_a", name="Motor A", geometry=GEO_A))
    # A preset with NO geometry at all: its stamp is '' — must not match ''.
    doc = _read(stores["presets"])
    doc["empty"] = {"id": "empty", "name": "Empty", "owner": "user", "geometry": {}}
    stores["presets"].write_text(json.dumps(doc), encoding="utf-8")
    doc_c = _read(stores["catalog"])
    doc_c["motors"].append({"id": "cat_empty", "name": "Empty", "preset": "empty",
                            "diameter_mm": 0, "owner": "user"})
    stores["catalog"].write_text(json.dumps(doc_c), encoding="utf-8")

    _set_live_geometry(monkeypatch, None)     # config has no geometry section
    assert [m["is_active"] for m in cat_mod.get_catalog()["motors"]] == [False, False]


def test_is_active_is_not_persisted(stores, monkeypatch):
    """The flag is computed per request; writing it into the store would leave a
    highlight behind the moment the user edited the geometry."""
    from motor_ai_sim.routes import catalog as cat_mod, presets as pre_mod

    _set_live_geometry(monkeypatch, GEO_A)
    pre_mod.save_current_as_preset(pre_mod.SavePresetRequest(
        id="m_a", name="Motor A", geometry=GEO_A))
    assert cat_mod.get_catalog()["motors"][0]["is_active"] is True
    assert "is_active" not in _read(stores["catalog"])["motors"][0]
