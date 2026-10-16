"""``POST /api/presets/{id}/apply`` used to write a preset's geometry straight
into ``motor_config.yaml`` (``geo_sec[k] = v`` for every key) with no check at
all -- the same bug class the Fusion CSV import had (2026-09-25): a preset
saved, hand-edited, or dropped in by a script with an impossible combination
applied cleanly and built garbage.

Fix: ``apply_preset`` now runs the imported geometry through
``routes.geometry.check_geometry_submission`` -- the SAME guard
``PUT /api/geometry`` and the Fusion/FreeCAD imports refuse an impossible
machine with -- before writing anything.  LOADING (``GET /api/presets``,
``GET /api/presets/{id}``) must still never crash on a preset whose geometry
is bad; only APPLY, the one call that actually changes the live machine, is
gated.
"""
from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

# A full, self-consistent geometry (every key the radii-chain checks need, so
# the outcome does not depend on whatever machine the sandbox happens to have
# loaded) -- copied from tests/test_geometry_validation.py's GEO_30MM, proven
# buildable there.
GOOD_GEO = {
    "stator_diameter": 30, "slot_height": 4.3, "core_thickness": 1.5,
    "num_seg": 2, "num_slots_per_segment": 6, "num_poles_per_segment": 7,
    "air_gap": 0.2, "tooth_width": 2.6, "tooth2_width": 1.4, "cut_width": 1.5,
    "insulation_thickness": 0.05, "wire_width": 2, "wire_height": 0.5,
    "wire_spacing_x": 0.1, "wire_spacing_y": 0.1, "num_wires_per_slot": 6,
    "wire_parallel": 1, "wire_split": 1, "slot_hs": 0.267, "magnet_height": 4.5,
    "rotor_house_height": 0.8, "shaft_height": 2, "magnet_fill_down": 0.9,
    "magnet_fill_up": 0.3, "magnet_fill_radius": 0.1, "magnet_up_gap": 0.1,
    "rotor_hole": 0.7, "magnet_down_height": 1.4, "magnet_lamination": 0,
    "stator_fillet_r": 1.2, "stator_fillet_r1": 0, "rotor_fill_r": 0.2,
    "motor_length": 10,
}

# The owner's Fusion CSV combination (2026-09-25): stator_diameter too small
# for its own slot_height + core_thickness + air_gap + magnet_height +
# rotor_house_height stack -- rotor_inner_radius comes out negative.
BAD_GEO = {**GOOD_GEO, "stator_diameter": 6, "core_thickness": 0.7,
          "slot_height": 1.842, "air_gap": 0.1, "magnet_height": 2,
          "rotor_house_height": 0.3}


@pytest.fixture()
def presets_store(tmp_path, monkeypatch):
    from motor_ai_sim.routes import presets as pre_mod

    presets_path = tmp_path / "motor_presets.json"
    presets_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(pre_mod, "_PRESETS_PATH", presets_path)

    def _put(pid: str, name: str, geometry: dict) -> None:
        data = json.loads(presets_path.read_text(encoding="utf-8"))
        data[pid] = {"id": pid, "name": name, "geometry": geometry,
                    "simulation": {}, "mesh": {}}
        presets_path.write_text(json.dumps(data), encoding="utf-8")

    return pre_mod, _put, presets_path


def _config_geometry():
    from motor_ai_sim.config import get_config
    return dict(get_config(reload=True).get("geometry") or {})


def test_apply_refuses_an_impossible_preset_and_writes_nothing(presets_store):
    pre_mod, put, _ = presets_store
    put("bad", "Impossible motor", BAD_GEO)
    before = _config_geometry()

    with pytest.raises(HTTPException) as ei:
        pre_mod.apply_preset("bad", authorization=None)
    assert ei.value.status_code == 422
    detail = ei.value.detail
    assert isinstance(detail, dict) and detail.get("invalid_parameters")
    assert "Impossible motor" in detail["error"]
    fields = {r["field"] for r in detail["invalid_parameters"]}
    assert fields & {"magnet_height", "air_gap", "slot_height"}, detail

    after = _config_geometry()
    assert after == before, "a refused apply must not touch the live config"


def test_apply_still_works_for_a_valid_preset(presets_store):
    pre_mod, put, _ = presets_store
    put("good", "Fine motor", GOOD_GEO)

    out = pre_mod.apply_preset("good", authorization=None)
    assert out["status"] == "ok"
    assert _config_geometry().get("stator_diameter") == 30


def test_listing_and_reading_never_crash_on_a_bad_stored_preset(presets_store):
    """A preset that would now be REFUSED on apply must still be listable and
    readable -- the guard is on the write, never on loading the library."""
    pre_mod, put, _ = presets_store
    put("bad", "Impossible motor", BAD_GEO)

    listed = pre_mod.list_presets()
    ids = {p["id"] for p in listed["presets"]}
    assert "bad" in ids

    fetched = pre_mod.get_preset("bad")
    assert fetched["geometry"]["stator_diameter"] == 6
