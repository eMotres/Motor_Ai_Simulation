# -*- coding: utf-8 -*-
"""A duty save corrects a stale stored ``n_coils_per_phase`` of a configuration whose ONLY deviation is that count (follow-up of the
coil-count heal): the heals of the first PR fire for a fresh configuration or one that adopts the live winding, a configuration with
stored geometry overrides and an unchanged winding never reached them.  The count reaches no solved number, so the correction is
also made in a locked configuration, the stamps stay current (alias), the history snapshot and the response name it, and nothing
else of the configuration changes; a correct count or an absent key is left as it is."""
from __future__ import annotations

import yaml
from fastapi.testclient import TestClient

from motor_ai_sim.api import app
from motor_ai_sim.routes import family as fam


def _catalog(tmp_path, monkeypatch, *, count=8, locked=False, with_key=True, layers=1, phases=None, slots=24, die_slots=True):
    root = tmp_path / "dies"
    (root / "SD 24").mkdir(parents=True)
    geo = {"num_poles": 28, "stator_diameter": 40.0, "magnet_height": 3.0, "motor_length": 40.0}
    if die_slots:
        geo["num_slots"] = slots
    (root / "SD 24" / "die.yaml").write_text(yaml.safe_dump({
        "name": "SD 24", "locked": False, "created": "2026-09-02T10:00:00", "geometry": geo}, sort_keys=False), encoding="utf-8")
    wind = {"connection": "4P", "n_parallel": 4, "n_series": 1, "layers": layers}
    if phases:
        wind["phases"] = phases
    if with_key:
        wind["n_coils_per_phase"] = count
    cfg = {"name": "L40", "die": "SD 24", "role": "motor", "geometry_overrides": {"motor_length": 40.0},
           "winding": wind, "materials": {"magnet": "N42SH"}, "duties": []}
    if locked:
        cfg["locked"] = True
    (root / "SD 24" / "L40.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(fam, "_DIES_DIR", root)
    live = {"geometry": {"num_slots": slots, "num_poles": 28, "stator_diameter": 40.0, "magnet_height": 3.0, "motor_length": 40.0},
            "winding": dict(wind, n_coils_per_phase=count) if with_key else dict(wind),
            "simulation": {"current_a": 85.0, "rpm": 6000.0, "gamma_deg": 12.0},
            "materials": {"magnet": "N42SH"}}
    monkeypatch.setattr(fam, "_live_cfg", lambda: live)
    return root


def _save(from_current=True):
    return TestClient(app).post("/api/family/duty", json={
        "die": "SD 24", "config": "L40", "duty": {"name": "peak", "mode": "motor", "from_current": from_current,
                                                   "current_arms": 85.0, "rpm": 6000.0, "gamma_deg": 12.0}})


def _doc(root):
    return yaml.safe_load((root / "SD 24" / "L40.yaml").read_text(encoding="utf-8"))


def test_a_stale_count_with_no_other_deviation_is_healed_and_the_stamps_stay_current(tmp_path, monkeypatch):
    root = _catalog(tmp_path, monkeypatch)
    die_doc = fam._load_yaml(root / "SD 24" / "die.yaml", "die")
    stamp = fam._build_sig(die_doc, fam._load_yaml(root / "SD 24" / "L40.yaml", "configuration"))     # stamped while stale (8)
    r = _save()
    assert r.status_code == 200, r.text
    assert r.json()["coil_count_corrected"] == {"from": 8, "to": 4}
    doc = _doc(root)
    assert doc["winding"]["n_coils_per_phase"] == 4
    assert {k: v for k, v in doc["winding"].items() if k != "n_coils_per_phase"} == {"connection": "4P", "n_parallel": 4, "n_series": 1, "layers": 1}
    assert doc["geometry_overrides"] == {"motor_length": 40.0} and doc["materials"] == {"magnet": "N42SH"}
    assert not fam.build_sig_moved(stamp, fam._build_sigs(die_doc, doc))                                # existing stamps stay current
    assert doc.get("build_sig_count_aliases")
    notes = sorted(f.name for f in (root / ".history" / "SD 24").glob("L40.*.yaml"))
    assert any("coil_count_corrected_8_to_4" in n for n in notes), notes


def test_a_locked_configuration_is_healed_in_that_one_key_only(tmp_path, monkeypatch):
    root = _catalog(tmp_path, monkeypatch, locked=True)
    p = root / "SD 24" / "L40.yaml"
    before = yaml.safe_load(p.read_text(encoding="utf-8"))
    die_doc = fam._load_yaml(root / "SD 24" / "die.yaml", "die")
    stamp = fam._build_sig(die_doc, fam._load_yaml(p, "configuration"))
    r = _save()
    assert r.status_code == 200, r.text
    assert r.json()["coil_count_corrected"] == {"from": 8, "to": 4} and "coil_count_stale" not in r.json()
    after = _doc(root)
    assert after["winding"]["n_coils_per_phase"] == 4
    assert {k: v for k, v in after["winding"].items() if k != "n_coils_per_phase"} == {k: v for k, v in before["winding"].items() if k != "n_coils_per_phase"}
    assert after["locked"] is True and after["materials"] == before["materials"] and after["geometry_overrides"] == before["geometry_overrides"]
    assert after.get("build_sig_count_aliases") and not fam.build_sig_moved(stamp, fam._build_sigs(die_doc, after))
    assert [x["name"] for x in after["duties"]] == ["peak"]
    notes = sorted(f.name for f in (root / ".history" / "SD 24").glob("L40.*.yaml"))
    assert any("coil_count_corrected_8_to_4" in n for n in notes), notes


def test_a_correct_count_changes_nothing_but_the_duty(tmp_path, monkeypatch):
    root = _catalog(tmp_path, monkeypatch, count=4)
    before = _doc(root)
    r = _save()
    assert r.status_code == 200, r.text
    assert "coil_count_corrected" not in r.json()
    after = _doc(root)
    assert after["winding"] == before["winding"] and "build_sig_count_aliases" not in after
    assert [d["name"] for d in after["duties"]] == ["peak"]
    notes = list((root / ".history" / "SD 24").glob("L40.*.yaml")) if (root / ".history").exists() else []
    assert not any("coil_count_corrected" in f.name for f in notes)


def test_an_absent_count_stays_absent(tmp_path, monkeypatch):
    root = _catalog(tmp_path, monkeypatch, with_key=False)
    r = _save()
    assert r.status_code == 200, r.text
    assert "coil_count_corrected" not in r.json()
    assert "n_coils_per_phase" not in _doc(root)["winding"]


def test_a_save_not_from_the_live_machine_derives_from_the_die(tmp_path, monkeypatch):
    root = _catalog(tmp_path, monkeypatch)
    r = _save(from_current=False)
    assert r.status_code == 200, r.text
    assert r.json()["coil_count_corrected"] == {"from": 8, "to": 4}
    assert _doc(root)["winding"]["n_coils_per_phase"] == 4


def test_the_slots_come_from_the_die_and_the_overrides_never_from_the_live_machine(tmp_path, monkeypatch):
    root = _catalog(tmp_path, monkeypatch, count=8, slots=48)       # die of 48 slots: derived 8, the stored 8 is right
    assert fam._live_cfg()["geometry"]["num_slots"] == 48
    del fam._live_cfg()["geometry"]["num_slots"]                    # a live snapshot without the slot key (the die check skips it): not used
    r = _save()
    assert r.status_code == 200, r.text
    assert "coil_count_corrected" not in r.json() and _doc(root)["winding"]["n_coils_per_phase"] == 8


def test_no_slot_keys_or_an_unreadable_die_means_no_heal_never_a_24_slot_default(tmp_path, monkeypatch):
    root = _catalog(tmp_path, monkeypatch, count=8, die_slots=False)
    assert _save().status_code == 200
    assert _doc(root)["winding"]["n_coils_per_phase"] == 8
    root2 = _catalog(tmp_path / "b", monkeypatch, count=8)
    c = fam._load_yaml(root2 / "SD 24" / "L40.yaml", "configuration")
    monkeypatch.setattr(fam, "_load_yaml", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("unreadable")))
    assert fam._heal_stored_coil_count("SD 24", c) == (None, None, None) and c["winding"]["n_coils_per_phase"] == 8


def test_a_double_layer_winding_is_not_touched_here(tmp_path, monkeypatch):
    root = _catalog(tmp_path, monkeypatch, count=8, layers=2)
    r = _save()
    assert r.status_code == 200, r.text
    assert "coil_count_corrected" not in r.json() and _doc(root)["winding"]["n_coils_per_phase"] == 8
    assert r.json()["coil_count_stale"] == {"stored": 8, "derived": 4}                  # reported, not rewritten


def test_a_six_phase_block_is_left_as_stored(tmp_path, monkeypatch):
    root = _catalog(tmp_path, monkeypatch, count=8, phases=6)
    r = _save()
    assert r.status_code == 200, r.text
    assert "coil_count_corrected" not in r.json() and _doc(root)["winding"]["n_coils_per_phase"] == 8


def test_a_non_numeric_stored_count_reports_the_stored_value_not_minus_one(tmp_path, monkeypatch):
    root = _catalog(tmp_path, monkeypatch, count="eight")
    r = _save()
    assert r.status_code == 200, r.text
    assert r.json()["coil_count_corrected"] == {"from": "eight", "to": 4}
    assert _doc(root)["winding"]["n_coils_per_phase"] == 4


def test_the_new_run_is_stamped_with_the_healed_build(tmp_path, monkeypatch):
    """The heal happens BEFORE the run is stored, so a run saved in the same call carries the healed signature."""
    import inspect
    text = inspect.getsource(fam._upsert_duty)
    assert text.index("_heal_stored_coil_count(die, c)") < text.index("_store_run(die, cfg, c, entry")
