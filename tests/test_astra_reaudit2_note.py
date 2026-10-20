# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Astra re-audit #2 R6 repro, flipped: the thermal note no longer leaks the CAD-derived face speed.

Uses the existing synthetic last-result fixture and a scalar convection model;
no FEM, mesh generation, app lifespan, or server is involved.
"""
from __future__ import annotations

import pytest

from tests.test_config_visibility import client
from tests.test_reaudit_r1_r2_r3 import (
    _thermal_result,
    env,  # noqa: F401 - imported fixture
    gate,  # noqa: F401 - imported fixture
    planted,  # noqa: F401 - imported fixture
    thermal_last,
)


@pytest.mark.parametrize("who", ["plain", "factory"])
def test_thermal_last_note_no_longer_discloses_the_face_speed(
        thermal_last, monkeypatch, who):
    from motor_ai_sim.routes import thermal
    from motor_ai_sim.simulation.cooling_models import rotor_end_faces_open

    fraction = 0.4321
    wash_mps = 12.0
    face_speed_mps = wash_mps * fraction
    face = rotor_end_faces_open(
        air_speed_mps=face_speed_mps, rpm=0.0, t_wall_c=25.0,
        t_ambient_c=25.0, area_m2=1e-4, radius_m=0.015,
    )
    result = _thermal_result()
    # Match the fields copied into the HTTP payload by thermal.py:6463-6470;
    # keep the scalar model's note, but do not expose its internal re_plate.
    result["cooling"]["end_faces"]["rotor"].update({
        "mode": face["mode"], "area_m2": face["area_m2"],
        "area_per_face_m2": face["area_m2"],
        "char_len_mm": face["char_len_m"] * 1e3,
        "orientation": face["orientation"], "h_conv": face["h_conv"],
        "h_rad": face["h_rad"], "h_total": face["h_total"],
        "ra": 0.0, "nu": face["nu"], "regime": face["regime"],
        "n_faces": face["n_faces"], "emissivity": face["emissivity"],
        "G_W_per_K": face["G_W_per_K"], "t_sink_c": face["t_sink_c"],
        "t_wall_c": face["t_wall_c"], "heat_removed_W": face["heat_removed_W"],
        "film_kind": face["film_kind"], "air_speed_mps": face["air_speed_mps"],
        "note": face["note"],
    })
    source = "CAD (bearings.thermal_path.rotor_face_open_fraction)"
    result["cooling"]["rotor_channels"]["holder_open_fraction_source"] = source
    result["cooling"]["holder_open_fraction_source"] = source
    monkeypatch.setattr(thermal, "_LAST", {"field": {
        "result": result, "params": {}, "geometry_fingerprint": None}})

    response = client.get("/api/thermal/last", headers=thermal_last[who])
    assert response.status_code == 200, response.text[:300]
    cooling = response.json()["field"]["result"]["cooling"]
    # The numeric field is redacted, but the scalar-model note still contains
    # the same CAD-derived face velocity and Reynolds number.
    assert cooling["end_faces"]["rotor"]["air_speed_mps"] is None
    note = cooling["end_faces"]["rotor"]["note"]
    # R6, flipped: the note reads neutrally, without the speed or its Reynolds number
    assert f"flat plate at {face_speed_mps:.1f} m/s" not in note
    assert "Re_D " not in note and "m/s" not in note
    assert "re_plate" not in cooling["end_faces"]["rotor"]
    assert f"{face_speed_mps:.1f} m/s" not in response.text
