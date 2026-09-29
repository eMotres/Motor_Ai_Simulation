"""Loading a duty never silently writes die-level geometry.

Incident 2026-09-27 ~20:20: loading "CIANO14 12_40 Ø12 12s10p / L10 / rated"
through the owner API (activate → PUT /api/geometry) rewrote the die:
wire_width 0.8→2, wire_height 0.2→0.190333 (the slot-fit bound the clamp
derived for 6 wires), num_wires_per_slot 5→6, motor_length 40→10.  The PUT's
die sync copied the whole live machine — config-level keys and clamp output
included — into die.yaml; activate never looked at the duty's own stamp.
"""
from __future__ import annotations

import json

import pytest
import yaml
from fastapi import HTTPException

from motor_ai_sim.routes import family as fam

DIE = "TINY 12"
CFG = "L10"

DIE_GEO = {"stator_diameter": 12.0, "num_seg": 2, "num_slots_per_segment": 6,
           "num_poles_per_segment": 5, "tooth_width": 1.5,
           "wire_width": 0.8, "wire_height": 0.2, "num_wires_per_slot": 5,
           "motor_length": 40}

# the stale stamp: same lamination, other winding + tooth, stack 10
SIG = ("motor_length:10|num_poles_per_segment:5|num_seg:2|"
       "num_slots_per_segment:6|num_wires_per_slot:6|stator_diameter:12|"
       "tooth_width:1.7|wire_height:0.2|wire_width:2")


@pytest.fixture
def dies(monkeypatch, tmp_path):
    root = tmp_path / "dies"
    (root / DIE).mkdir(parents=True)
    (root / DIE / "die.yaml").write_text(yaml.safe_dump(
        {"name": DIE, "locked": False, "geometry": dict(DIE_GEO)},
        sort_keys=False), encoding="utf-8")
    (root / DIE / f"{CFG}.yaml").write_text(yaml.safe_dump({
        "name": CFG, "die": DIE, "geometry_overrides": {"motor_length": 10},
        "duties": [{"name": "rated", "mode": "motor", "rpm": 15000.0,
                    "summary": {"_geoSig": SIG}},
                   {"name": "clean", "mode": "motor", "rpm": 15000.0}],
    }, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(fam, "_DIES_DIR", root)
    monkeypatch.setattr(fam, "_CTX_FILE", tmp_path / ".family_context.json")
    monkeypatch.setattr(fam, "_sync_mesh_config_from_duty", lambda c, n: False)
    return root


def _die(root):
    return yaml.safe_load((root / DIE / "die.yaml").read_text(encoding="utf-8"))


def _hist(root):
    h = root / ".history" / DIE
    return sorted(h.glob("*.yaml")) if h.is_dir() else []


def test_differing_duty_is_refused_with_a_diff_and_nothing_is_written(dies, tmp_path):
    before = (dies / DIE / "die.yaml").read_bytes()
    with pytest.raises(HTTPException) as ei:
        fam.activate(fam.Activate(die=DIE, config=CFG, duty="rated"), _w={})
    assert ei.value.status_code == 409
    det = ei.value.detail
    assert det["code"] == "duty_geometry_differs"
    by = {x["key"]: x for x in det["diffs"]}
    assert by["wire_width"]["scope"] == "winding" and by["wire_width"]["duty"] == 2
    assert by["num_wires_per_slot"]["duty"] == 6
    assert by["tooth_width"]["scope"] == "die"
    assert "motor_length" not in by            # config says 10 too — no diff
    assert (dies / DIE / "die.yaml").read_bytes() == before
    assert not (tmp_path / ".family_context.json").exists()
    assert _hist(dies) == []


def test_keep_die_loads_without_writing(dies):
    before = (dies / DIE / "die.yaml").read_bytes()
    out = fam.activate(fam.Activate(die=DIE, config=CFG, duty="rated",
                                    geometry_choice="keep_die"), _w={})
    assert out["ok"] and out["geometry_applied"] is None
    assert (dies / DIE / "die.yaml").read_bytes() == before
    assert _hist(dies) == []


def test_explicit_apply_writes_with_history_snapshot(dies):
    out = fam.activate(fam.Activate(die=DIE, config=CFG, duty="rated",
                                    geometry_choice="apply_duty"), _w={})
    assert out["geometry_applied"]["die_keys"] == ["tooth_width"]
    g = _die(dies)["geometry"]
    assert g["tooth_width"] == 1.7
    # winding keys go to the CONFIGURATION, never into die.yaml
    assert g["wire_width"] == 0.8 and g["num_wires_per_slot"] == 5
    assert g["motor_length"] == 40
    c = yaml.safe_load((dies / DIE / f"{CFG}.yaml").read_text(encoding="utf-8"))
    assert c["geometry_overrides"]["wire_width"] == 2
    assert c["geometry_overrides"]["num_wires_per_slot"] == 6
    names = [p.name for p in _hist(dies)]
    assert any(n.startswith("die.") for n in names)
    assert any(n.startswith(f"{CFG}.") for n in names)


def test_duty_without_stamp_loads_as_before(dies):
    out = fam.activate(fam.Activate(die=DIE, config=CFG, duty="clean"), _w={})
    assert out["ok"] and out["geometry_diffs"] == []


def test_foreign_lamination_cannot_be_applied(dies):
    c = yaml.safe_load((dies / DIE / f"{CFG}.yaml").read_text(encoding="utf-8"))
    c["duties"][0]["summary"]["_geoSig"] = SIG.replace("stator_diameter:12",
                                                       "stator_diameter:30")
    (dies / DIE / f"{CFG}.yaml").write_text(yaml.safe_dump(c), encoding="utf-8")
    with pytest.raises(HTTPException) as ei:
        fam.activate(fam.Activate(die=DIE, config=CFG, duty="rated",
                                  geometry_choice="apply_duty"), _w={})
    assert ei.value.status_code == 422
    assert _die(dies)["geometry"]["stator_diameter"] == 12.0


def test_geometry_put_sync_never_writes_config_keys_into_die(dies, tmp_path):
    """The PUT's die sync after a load: live carries the config overrides and
    the clamp-derived wire_height — die.yaml must not change at all."""
    (tmp_path / ".family_context.json").write_text(
        json.dumps({"die": DIE, "config": CFG, "duty": "rated"}), encoding="utf-8")
    before = (dies / DIE / "die.yaml").read_bytes()
    live = dict(DIE_GEO, wire_width=2, wire_height=0.190333,
                num_wires_per_slot=6, motor_length=10)
    assert fam.sync_active_die_geometry(live, prev_geo=dict(DIE_GEO)) is None
    assert (dies / DIE / "die.yaml").read_bytes() == before
    # a genuine die-level edit still syncs, free keys still the die's own
    live2 = dict(live, tooth_width=1.6)
    assert fam.sync_active_die_geometry(live2, prev_geo=dict(DIE_GEO)) == DIE
    g = _die(dies)["geometry"]
    assert g["tooth_width"] == 1.6 and g["wire_height"] == 0.2
    assert g["motor_length"] == 40 and g["num_wires_per_slot"] == 5
