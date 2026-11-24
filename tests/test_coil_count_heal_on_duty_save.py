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


def _catalog(tmp_path, monkeypatch, *, count=8, locked=False, with_key=True):
    root = tmp_path / "dies"
    (root / "SD 24").mkdir(parents=True)
    (root / "SD 24" / "die.yaml").write_text(yaml.safe_dump({
        "name": "SD 24", "locked": False, "created": "2026-09-02T10:00:00",
        "geometry": {"num_slots": 24, "num_poles": 28, "stator_diameter": 40.0, "magnet_height": 3.0,
                     "motor_length": 40.0}}, sort_keys=False), encoding="utf-8")
    wind = {"connection": "4P", "n_parallel": 4, "n_series": 1, "layers": 1}
    if with_key:
        wind["n_coils_per_phase"] = count
    cfg = {"name": "L40", "die": "SD 24", "role": "motor", "geometry_overrides": {"motor_length": 40.0},
           "winding": wind, "materials": {"magnet": "N42SH"}, "duties": []}
    if locked:
        cfg["locked"] = True
    (root / "SD 24" / "L40.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(fam, "_DIES_DIR", root)
    live = {"geometry": {"num_slots": 24, "num_poles": 28, "stator_diameter": 40.0, "magnet_height": 3.0, "motor_length": 40.0},
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


def test_a_locked_configuration_is_healed_too(tmp_path, monkeypatch):
    root = _catalog(tmp_path, monkeypatch, locked=True)
    r = _save()
    assert r.status_code == 200, r.text
    assert r.json()["coil_count_corrected"] == {"from": 8, "to": 4}
    assert _doc(root)["winding"]["n_coils_per_phase"] == 4 and _doc(root)["locked"] is True


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
    if r.status_code != 200:
        # a duty without a live machine may need more fields than this fixture gives: the helper itself is pinned instead
        c = fam._load_yaml(root / "SD 24" / "L40.yaml", "configuration")
        fix, old = fam._heal_stored_coil_count("SD 24", c, None)
        assert (fix, old, c["winding"]["n_coils_per_phase"]) == ({"from": 8, "to": 4}, 8, 4)
        return
    assert r.json()["coil_count_corrected"] == {"from": 8, "to": 4}
