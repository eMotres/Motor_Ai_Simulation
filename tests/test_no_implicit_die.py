"""The application never creates a die by itself (owner 2026-11-12: "do not create a new die without
explicitly saving it").  2026-11-12 one day of work left "CIANO28 80_11 vadim 3 ... 8": a duty save for
'L12-F475' (wire_spacing_x changed in its geometry_overrides) on the LOCKED die 'vadim 3' forked 'vadim 8'.

Pinned: (a) that save lands in the existing configuration, the set of dies is unchanged; (b) the explicit
routes (duplicate, save as new die) still create a die; (c) a real lamination change is refused with
``new_die_requires_explicit_save`` and creates nothing; the geometry save no longer mints a die by default.
Throwaway catalog under the pytest tmp area.
"""
from __future__ import annotations

import json

import pytest
import yaml
from fastapi import HTTPException

from motor_ai_sim.routes import family as fam

DIE = "CIANO28 80_11 vadim 3"
CFG = "L12-F475"
WIRE = {"wire_width": 4.0, "wire_spacing_x": 0.1, "insulation_thickness": 0.1,
        "wire_height": 0.5, "num_wires_per_slot": 12, "motor_length": 12}
GEO = dict({"stator_diameter": 80.0, "num_seg": 1, "num_slots_per_segment": 12,
            "num_poles_per_segment": 14, "core_thickness": 3.0, "slot_height": 9.0,
            "air_gap": 0.6, "magnet_height": 4.0, "rotor_house_height": 2.0,
            "magnet_down": 1.0, "tooth_width": 4.0, "slot_width": 4.4}, **WIRE)
MATS = {"magnet": "N52UH", "slot_insulation": "Nomex"}


def _cfg_doc(name, die=DIE):
    return {"name": name, "die": die, "role": "motor", "locked": False,
            "geometry_overrides": dict(WIRE), "winding": {"connection": "4S"}, "materials": dict(MATS),
            "duties": [{"name": "rated", "mode": "motor", "current_arms": 30.0, "rpm": 3000.0,
                        "gamma_deg": 0.0, "note": ""}]}


def _write(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


def _names(root):
    return {p.name for p in root.iterdir() if not p.name.startswith(".")}


def _yaml(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture
def cat(tmp_path, monkeypatch):
    root = tmp_path / "dies"
    _write(root / DIE / "die.yaml", {"name": DIE, "locked": True, "geometry": dict(GEO)})
    _write(root / DIE / f"{CFG}.yaml", _cfg_doc(CFG))
    monkeypatch.setattr(fam, "_DIES_DIR", root)
    monkeypatch.setattr(fam, "_CTX_FILE", tmp_path / ".family_context.json")
    monkeypatch.setattr(fam, "_acting_account", lambda *_a, **_k: ("Vadim", "vadim@example.com"))
    monkeypatch.setattr(fam, "_autolink_new_config", lambda *_a, **_k: None)
    monkeypatch.setattr(fam, "_live_parts", lambda: {})
    monkeypatch.setattr(fam, "may_see_die", lambda *_a, **_k: True)
    from motor_ai_sim.routes import presets as _presets
    monkeypatch.setattr(_presets, "_gen_thumb_svg", lambda *_a, **_k: None)
    (tmp_path / ".family_context.json").write_text(
        json.dumps({"die": DIE, "config": CFG, "duty": "rated"}), encoding="utf-8")
    return root


def _live(monkeypatch, geo):
    live = {"geometry": dict(geo), "simulation": {"current_a": 31.0, "rpm": 3000.0, "gamma_deg": 1.0,
                                                  "mode": "motor"},
            "winding": {"connection": "4S"}, "materials": dict(MATS)}
    monkeypatch.setattr(fam, "_live_cfg", lambda: live)


def _save():
    spec = fam.DutySpec(name="rated", mode="motor", from_current=True)
    return fam.upsert_duty(fam.DutyCreate(die=DIE, config=CFG, duty=spec), _w={})


def test_changed_wire_spacing_on_a_locked_die_saves_into_the_configuration(cat, monkeypatch):
    # wire_spacing_x 0.1 -> 0.2: the derived slot width changes, no die input does
    _live(monkeypatch, dict(GEO, wire_spacing_x=0.2, slot_width=4.6))
    assert fam.die_shape_diffs(GEO, dict(GEO, wire_spacing_x=0.2, slot_width=4.6))   # the old fork condition
    before = (cat / DIE / "die.yaml").read_bytes()
    out = _save()
    assert _names(cat) == {DIE}
    assert (cat / DIE / "die.yaml").read_bytes() == before
    assert "transitioned" not in out
    c = _yaml(cat / DIE / f"{CFG}.yaml")
    assert c["geometry_overrides"]["wire_spacing_x"] == 0.2
    assert next(x for x in c["duties"] if x["name"] == "rated")["current_arms"] == 31.0


def test_a_real_lamination_change_is_refused_with_the_code_and_creates_nothing(cat, monkeypatch):
    _live(monkeypatch, dict(GEO, magnet_height=5.0))
    before = {p.name: p.read_bytes() for p in (cat / DIE).glob("*.yaml")}
    with pytest.raises(HTTPException) as e:
        _save()
    assert e.value.status_code == 409
    assert e.value.detail["code"] == "new_die_requires_explicit_save"
    assert _names(cat) == {DIE}
    assert {p.name: p.read_bytes() for p in (cat / DIE).glob("*.yaml")} == before


def test_a_geometry_save_does_not_mint_a_die_by_default(cat, monkeypatch):
    d = _yaml(cat / DIE / "die.yaml")
    d["locked"] = False
    _write(cat / DIE / "die.yaml", d)
    report: dict = {}
    new = fam.sync_active_die_geometry(dict(GEO, num_poles_per_segment=16), prev_geo=dict(GEO), report=report)
    assert new is None and _names(cat) == {DIE}
    assert report["blocked"] and report["code"] == "new_die_requires_explicit_save"
    ctx = json.loads(fam._ctx_file().read_text(encoding="utf-8"))
    assert ctx["die"] is None and ctx["released_from"] == DIE      # the strip then offers "Save as new die"


def test_a_geometry_save_may_mint_a_die_only_with_the_explicit_flag(cat):
    d = _yaml(cat / DIE / "die.yaml")
    d["locked"] = False
    _write(cat / DIE / "die.yaml", d)
    new = fam.sync_active_die_geometry(dict(GEO, num_poles_per_segment=16), prev_geo=dict(GEO),
                                       allow_new_die=True)
    assert new and new != DIE and _names(cat) == {DIE, new}


def test_the_duplicate_route_still_creates_a_die(cat):
    out = fam.duplicate_die(DIE, fam.DieDuplicate(name="CIANO28 80_11 copy"), _w={})
    assert "CIANO28 80_11 copy" in _names(cat)
    assert (cat / "CIANO28 80_11 copy" / f"{CFG}.yaml").is_file()


def test_save_as_new_die_still_creates_a_die(cat, monkeypatch):
    _live(monkeypatch, dict(GEO, magnet_height=5.0))
    out = fam.save_as_new_die(fam.SaveAsNewDie(name="CIANO28 80_11 explicit", config=CFG, duty="rated"), _w={})
    assert out["ok"] and "CIANO28 80_11 explicit" in _names(cat)
