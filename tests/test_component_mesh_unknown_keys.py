"""component_mesh: an unknown key is a 422 naming it (not a silent drop, not an alias); the valid keys parse as before."""
from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from motor_ai_sim.routes import simulation as S


def test_valid_keys_parse_as_before():
    out = S._parse_component_mesh(json.dumps({"stator": 1.5, "Rotor": 2, "outer": 6, "coil_rel": 1.2, "magnet": 0, "shaft": -1}))
    assert out == {"stator": 1.5, "rotor": 2.0, "outer": 6.0, "coil_rel": 1.0}        # non-positive sizes dropped, coil_rel snapped
    assert S._parse_component_mesh("") == {} and S._parse_component_mesh("not json") == {} and S._parse_component_mesh("[1]") == {}


@pytest.mark.parametrize("bad", ["air", "AIR", "far_air", "iron"])
def test_an_unknown_key_is_refused_with_the_valid_ones_named(bad):
    with pytest.raises(HTTPException) as e:
        S._parse_component_mesh(json.dumps({"stator": 1.5, bad: 3.0}))
    assert e.value.status_code == 422 and bad in e.value.detail and '"outer"' in e.value.detail and "airgap" in e.value.detail


def test_every_key_the_app_and_the_presets_send_is_valid():
    from motor_ai_sim import mesh_presets as MP
    for k in ("stator", "rotor", "magnet", "outer", "coil_rel"):                          # web/src/lib/meshPresets.ts
        assert k in S._VALID_MESH_COMPONENTS
    cm = MP.resolve("optimization", {"stator_diameter": 80.0, "num_slots": 12, "num_poles": 10, "stator_outer_radius": 40.0})["values"].get("component_mesh", {})
    assert set(cm) <= set(S._VALID_MESH_COMPONENTS)
