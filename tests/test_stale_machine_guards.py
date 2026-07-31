"""The stale-machine class, pinned.

Two failure modes, one root cause — a decision made from a copy of the world
that has since moved on:

1. A RESULT is served without saying which machine produced it, so numbers from
   the previous motor read as the current one's.
2. A whole-file WRITE is built from a document read earlier, so a concurrent
   writer's entry is not merged with — it is erased.

These tests fail if either guard is removed.  They touch no solver: the restore
path returns before the geometry gate and before the FEM lock, and the store
tests use a temp file.
"""
from __future__ import annotations

import json

import pytest


# ── 1. A result carries — and is checked against — its machine ───────────────

def test_geometry_fingerprint_tracks_the_machine(monkeypatch):
    """Different geometry -> different fingerprint; same geometry -> same one."""
    from motor_ai_sim.routes import simulation as sim

    cfgs = [{"geometry": {"stator_diameter": 40.0, "num_slots": 12}},
            {"geometry": {"stator_diameter": 30.0, "num_slots": 12}}]
    seen = []
    for cfg in cfgs:
        monkeypatch.setattr("motor_ai_sim.config.get_config", lambda c=cfg: c)
        seen.append(sim._geometry_fingerprint())
    assert seen[0] != seen[1], "a 40 mm and a 30 mm machine share a fingerprint"

    monkeypatch.setattr("motor_ai_sim.config.get_config", lambda: cfgs[0])
    assert sim._geometry_fingerprint() == seen[0], "fingerprint is not stable"


def test_geometry_fingerprint_separates_a_geo_override(monkeypatch):
    """A per-candidate eval (`geo=`) is not the motor on screen."""
    from motor_ai_sim.routes import simulation as sim
    monkeypatch.setattr("motor_ai_sim.config.get_config",
                        lambda: {"geometry": {"stator_diameter": 40.0}})
    assert sim._geometry_fingerprint() != sim._geometry_fingerprint(
        {"stator_diameter": 33.0})


def test_restore_flags_a_run_from_another_machine(monkeypatch):
    """THE headline scenario: solve on machine A, load machine B, reload.

    The restored run must come back flagged — `stale_geometry` true and
    `stale_reason` "geometry" — not served as the current result.
    """
    from motor_ai_sim.routes import simulation as sim

    saved = {"time_s": [0.0], "T_avg_Nm": 1.23,
             "geo_fingerprint": "machine_A_fp"}
    # Empty cache: a warm entry would be returned BEFORE the restore branch,
    # and whether one exists depends on what this machine last solved.
    monkeypatch.setattr(sim, "_fem_transient_cache", {})
    monkeypatch.setitem(sim._last_transient_ref, "key", ("some", "other", "key"))
    monkeypatch.setitem(sim._last_transient_ref, "result", saved)
    monkeypatch.setattr(sim, "_geometry_fingerprint",
                        lambda *a, **k: "machine_B_fp")

    out = sim.get_fem_transient(restore=True)
    assert out["restored"] is True
    assert out["stale"] is True
    assert out["stale_geometry"] is True
    assert out["stale_reason"] == "geometry"
    # A stale restore never claims the in-memory field snapshot of another run.
    assert out["field_snapshot"] is False


def test_restore_of_the_same_machine_is_not_geometry_stale(monkeypatch):
    """An operating-point tweak must NOT be reported as a machine change —
    a banner that cries wolf is a banner nobody reads."""
    from motor_ai_sim.routes import simulation as sim

    saved = {"time_s": [0.0], "T_avg_Nm": 1.23, "geo_fingerprint": "same_fp"}
    # Empty cache: a warm entry would be returned BEFORE the restore branch,
    # and whether one exists depends on what this machine last solved.
    monkeypatch.setattr(sim, "_fem_transient_cache", {})
    monkeypatch.setitem(sim._last_transient_ref, "key", ("some", "other", "key"))
    monkeypatch.setitem(sim._last_transient_ref, "result", saved)
    monkeypatch.setattr(sim, "_geometry_fingerprint", lambda *a, **k: "same_fp")

    out = sim.get_fem_transient(restore=True)
    assert out["stale"] is True          # the key differs: inputs moved
    assert out["stale_geometry"] is False
    assert out["stale_reason"] == "inputs"


def test_restore_of_an_unstamped_run_reports_unknown(monkeypatch):
    """A run saved before the stamp existed cannot prove anything — it says so
    (None), instead of claiming to be fine."""
    from motor_ai_sim.routes import simulation as sim

    # Empty cache: a warm entry would be returned BEFORE the restore branch,
    # and whether one exists depends on what this machine last solved.
    monkeypatch.setattr(sim, "_fem_transient_cache", {})
    monkeypatch.setitem(sim._last_transient_ref, "key", ("k",))
    monkeypatch.setitem(sim._last_transient_ref, "result",
                        {"time_s": [0.0], "T_avg_Nm": 1.0})
    monkeypatch.setattr(sim, "_geometry_fingerprint", lambda *a, **k: "fp")

    out = sim.get_fem_transient(restore=True)
    assert out["stale_geometry"] is None


# ── 2. A write merges with the file as it stands, and is atomic ──────────────

def test_mutate_json_merges_a_concurrent_writers_entry(tmp_path):
    """The store re-read happens at WRITE time, so an entry that appeared after
    our snapshot survives."""
    from motor_ai_sim.json_store import mutate_json, read_json

    p = tmp_path / "store.json"
    p.write_text(json.dumps({"a": 1}), encoding="utf-8")
    stale_copy = read_json(p, {})            # our snapshot: {"a": 1}

    # …meanwhile, somebody else adds "b".
    p.write_text(json.dumps({"a": 1, "b": 2}), encoding="utf-8")

    mutate_json(p, lambda d: d.__setitem__("a", 99))
    out = read_json(p, {})
    assert out == {"a": 99, "b": 2}, "a concurrent writer's entry was erased"
    assert "b" not in stale_copy             # it really was invisible to us


def test_atomic_write_leaves_no_partial_document(tmp_path):
    """A reader must never catch the file mid-truncate: the replace is atomic,
    so the path either has the old document or the new one."""
    from motor_ai_sim.json_store import atomic_write_json, read_json

    p = tmp_path / "store.json"
    atomic_write_json(p, {"x": 1})
    atomic_write_json(p, {"x": 2, "y": [1, 2, 3]})
    assert read_json(p, None) == {"x": 2, "y": [1, 2, 3]}
    assert not list(tmp_path.glob("*.tmp")), "temp file left behind"


def test_preset_settings_save_does_not_clobber_a_concurrent_write(tmp_path,
                                                                  monkeypatch):
    """End-to-end on the AUTOSAVE path — the one that fires on every edit.

    An external writer adds a motor after the handler's existence check and
    before its write.  Under the old whole-file save that motor vanished; now
    the patch is applied to the document as it stands on disk.
    """
    from motor_ai_sim.routes import presets as pr

    store = tmp_path / "motor_presets.json"
    store.write_text(json.dumps({
        "mine": {"id": "mine", "name": "Mine", "owner": "user",
                 "geometry": {"stator_diameter": 40.0}, "mesh": {}, "simulation": {}},
    }), encoding="utf-8")
    monkeypatch.setattr(pr, "_PRESETS_PATH", store)
    monkeypatch.setattr(pr, "_upsert_catalog_entry", lambda *a, **k: None)

    real_load = pr._load_presets

    def _load_then_someone_else_writes():
        d = real_load()
        # A concurrent writer lands HERE — between our read and our write.
        cur = json.loads(store.read_text(encoding="utf-8"))
        cur["theirs"] = {"id": "theirs", "name": "Theirs", "owner": "user"}
        store.write_text(json.dumps(cur), encoding="utf-8")
        return d
    monkeypatch.setattr(pr, "_load_presets", _load_then_someone_else_writes)

    pr.save_motor_settings("mine", pr.SettingsPatch(
        geometry={"stator_diameter": 30.0}, mesh={"mesh_size_mm": 2.0}))

    out = json.loads(store.read_text(encoding="utf-8"))
    assert "theirs" in out, "the concurrent writer's motor was clobbered"
    assert out["mine"]["geometry"]["stator_diameter"] == 30.0
    assert out["mine"]["mesh"]["mesh_size_mm"] == 2.0


def test_saved_preset_carries_its_machine_stamp(tmp_path, monkeypatch):
    """Every stored motor says which machine it is and when it was written —
    and the stamp is recomputed from what LANDED, never taken on the client's
    word (a stamp that can lie is worse than none, because it is believed)."""
    from motor_ai_sim.routes import presets as pr

    store = tmp_path / "motor_presets.json"
    store.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(pr, "_PRESETS_PATH", store)
    monkeypatch.setattr(pr, "_upsert_catalog_entry", lambda *a, **k: None)

    pr._put_preset("m", {"id": "m", "name": "M",
                         "geometry": {"stator_diameter": 40.0, "num_slots": 12}})
    entry = json.loads(store.read_text(encoding="utf-8"))["m"]
    assert entry["geo_sig"] == "num_slots:12|stator_diameter:40"
    assert entry["updated_at"]

    # A client claiming a different geometry does not get to set the stamp.
    pr.save_motor_settings("m", pr.SettingsPatch(
        geometry={"stator_diameter": 30.0}, geo_sig="stator_diameter:999"))
    entry = json.loads(store.read_text(encoding="utf-8"))["m"]
    assert entry["geo_sig"] == "num_slots:12|stator_diameter:30"


def test_card_metrics_refuse_a_run_from_another_machine(tmp_path, monkeypatch):
    """`.last_transient.json` is the last run of ANY motor.  A card must not be
    stamped with it unless it was solved on the machine being saved."""
    from motor_ai_sim.routes import presets as pr
    from motor_ai_sim.routes import simulation as sim

    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / ".last_transient.json").write_text(json.dumps(
        {"result": {"T_avg_Nm": 9.99, "geo_fingerprint": "machine_A_fp"}}),
        encoding="utf-8")
    monkeypatch.setattr(pr, "_ROOT", tmp_path)

    monkeypatch.setattr(sim, "_geometry_fingerprint", lambda *a, **k: "machine_B_fp")
    assert pr._last_transient_summary() is None, \
        "another machine's torque was accepted as this card's"

    monkeypatch.setattr(sim, "_geometry_fingerprint", lambda *a, **k: "machine_A_fp")
    assert pr._last_transient_summary()["T_avg_Nm"] == pytest.approx(9.99)
