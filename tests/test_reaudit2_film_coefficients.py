# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Follow-up to Astra re-audit #2 R6: no field of a CAD-derived film lets the
face / channel air speed be inverted for a caller without the gate.

The open rotor-face film and the rotor axial channels run at the wash × the
MEASURED open fraction.  #355 withheld the speed, h_plate, Nu and Re; h_conv /
h_total / h_disc, the regime, the mass flow and the areas / lengths a
coefficient is per would still give it back through the public correlation.
For a caller without the gate those films now carry only a whitelist: G, the
watts, the temperatures and the plain facts of the face.  The real scalar
models produce the blocks; no FEM.
"""
from __future__ import annotations

import json

import pytest

from tests.test_config_visibility import client
from tests.test_reaudit_r1_r2_r3 import (_thermal_result, env, gate, planted,  # noqa: F401
                                         thermal_last)

FACE_SPEED = 12.0 * 0.4321

#: Fields that are functions of the speed (or that turn G into a coefficient):
#: none of them may carry a value for a caller without the gate.
SPEED_BEARING = ("h_conv", "h_total", "h_disc", "h_plate", "h", "re", "re_omega",
                 "re_plate", "nu", "nu_forced", "nu_inlet_swirl_avg", "nu_rotation",
                 "regime", "air_speed_mps", "v_mps", "m_dot_g_s", "friction_factor",
                 "area_m2", "area_total_m2", "area_per_face_m2", "char_len_m",
                 "char_len_mm", "area_mm2", "perimeter_mm", "d_h_mm", "ra")


def _real_films():
    from motor_ai_sim.simulation.cooling_models import (rotor_axial_channels,
                                                        rotor_end_faces_open)
    face = rotor_end_faces_open(air_speed_mps=FACE_SPEED, rpm=8000.0, t_wall_c=90.0,
                                t_ambient_c=40.0, area_m2=4e-4, radius_m=0.018)
    ch = rotor_axial_channels(
        channels=[{"area_m2": 12e-6, "perimeter_m": 0.014, "perimeter_magnet_m": 0.005,
                   "perimeter_iron_m": 0.009, "r_m": 0.012, "count": 14}],
        length_m=0.012, face_speed_mps=FACE_SPEED, rpm=8000.0, t_air_c=45.0, t_wall_c=90.0)
    assert ch["channels"][0]["open"], ch["channels"][0]          # a real ventilated channel
    res = _thermal_result()
    res["cooling"]["end_faces"]["rotor"] = dict(face)
    res["cooling"]["rotor_channels"].update(
        {"channels": ch["channels"], "note": ch["note"],
         "face_speed_mps": ch["face_speed_mps"]})
    return res, face, ch


@pytest.mark.parametrize("who", ["plain", "factory"])
def test_no_film_field_lets_the_speed_be_inverted(thermal_last, monkeypatch, who):
    from motor_ai_sim.routes import thermal
    res, face, ch = _real_films()
    monkeypatch.setattr(thermal, "_LAST", {"field": {"result": res, "params": {},
                                                      "geometry_fingerprint": None}})
    r = client.get("/api/thermal/last", headers=thermal_last[who])
    assert r.status_code == 200, r.text[:300]
    cooling = r.json()["field"]["result"]["cooling"]
    blk = cooling["end_faces"]["rotor"]
    # re-audit #3 B2 superseded the per-field whitelist: a CAD-derived film
    # comes back with no number at all — not G, not watts, not a temperature
    assert not [k for k, v in blk.items() if isinstance(v, (int, float))
                and not isinstance(v, bool)], blk
    for k in SPEED_BEARING + ("G_W_per_K", "heat_removed_W", "t_wall_c", "t_mean_c"):
        assert blk.get(k) is None, ("end face", k)
    assert "channels" not in cooling["rotor_channels"]
    assert cooling["rotor_channels"].get("face_speed_mps") is None
    # and no speed-derived number of either film survives anywhere in the answer
    for k in ("h_conv", "h_plate", "re", "nu", "air_speed_mps", "G_W_per_K"):
        if isinstance(face.get(k), float) and face[k] > 0:
            assert json.dumps(round(face[k], 6)) not in r.text, k
    for k in ("h", "re", "v_mps", "m_dot_g_s", "G_W_per_K"):
        v = ch["channels"][0].get(k)
        if isinstance(v, float) and v > 0:
            assert json.dumps(round(v, 6)) not in r.text, k


def test_the_admin_keeps_every_film_field(thermal_last, monkeypatch):
    from motor_ai_sim.routes import thermal
    res, face, ch = _real_films()
    monkeypatch.setattr(thermal, "_LAST", {"field": {"result": res, "params": {},
                                                      "geometry_fingerprint": None}})
    cooling = client.get("/api/thermal/last", headers=thermal_last["admin"]
                         ).json()["field"]["result"]["cooling"]
    assert cooling["end_faces"]["rotor"]["h_conv"] == pytest.approx(face["h_conv"])
    assert cooling["rotor_channels"]["channels"][0]["v_mps"] == pytest.approx(
        ch["channels"][0]["v_mps"])
