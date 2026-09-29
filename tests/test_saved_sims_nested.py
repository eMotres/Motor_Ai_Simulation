"""A Compare point may carry NESTED result blocks, not only flat numbers.

Since 2026-09-07 the Mechanical and Thermal tabs file their answers into the
same library the Simulation tab does (user: *"нужно везде сделать такую же
кнопку для сравнения всех величин в механических и температурных
моделированиях"*).  An EM point's ``results`` is flat — ``{"T_em_avg_Nm": 12.3,
…}`` — while those two rows add a whole sub-object each:

    {"T_em_avg_Nm": …, "thermal": {"winding_max": 163.4, …},
                       "mechanical": {"sf_sleeve": 1.94, …}}

``SaveSimRequest.results`` is typed ``Dict[str, Any]``, so pydantic accepts that
today.  This test is here because the FRONTEND now depends on it: if the type is
ever narrowed to ``Dict[str, float]`` — an easy, well-meaning tightening — the
button on those two tabs starts answering 422 and the only symptom is a point
that will not save.  It also pins the round trip, not just the parse: the store
is JSON on disk, and a nested block has to survive being written and read back
unchanged.

The store file is redirected into ``tmp_path`` so the suite can never touch the
user's real ``config/saved_simulations.json`` (standing project rule: a test
never writes live state).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from motor_ai_sim.api import app
from motor_ai_sim.routes import saved_sims


THERMAL = {
    "T_max": 163.4,
    "T_min": 41.2,
    "winding_max": 163.4,
    "winding_avg": 151.0,
    "magnet_max": 118.7,
    "stator_max": 140.1,
    "rotor_max": 121.5,
    "sleeve_max": 119.9,
    "liner_max": 158.2,
    "enamel_max": 160.0,
    "slot_fill_max": 155.3,
    "gap_air_max": 130.2,
    "pocket_air_max": 120.4,
    "shaft_max": 96.4,
    "h_outer": 42.0,
    "housing_W": 900.0,
    "bore_W": 96.0,
    "gap_W": 34.0,
    "coupled_coil_c": 154.3,
    "coupled_converged": True,
    "loss_source_kind": "reused",
}

MECHANICAL = {
    "case": "23,000 rpm",
    "sf_sleeve": 1.94,
    "sleeve_hoop_p995_mpa": 812.4,
    "sf_iron": 1.31,
    "iron_vm_p995_mpa": 291.8,
    "open_sleeve_magnet": 2.34,
    "retention_verdict": "sleeve carries the magnets",
    "poles_held": True,
}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """A TestClient whose saved-sims store is a file in ``tmp_path``."""
    monkeypatch.setattr(saved_sims, "_STORE", tmp_path / "saved_simulations.json")
    return TestClient(app)


def test_nested_result_blocks_round_trip(client):
    """POST a row with `results.thermal` + `results.mechanical`, GET it back."""
    body = {
        "name": "Thermal · nested round trip",
        "params": {
            "air_gap": 1.6,
            "therm_cooling_mode": "liquid",
            "therm_flow_lpm": 12.0,
            "therm_ambient_c": 40.0,
            "mech_rpm": 23000.0,
            "mech_contacts": "magnet_rotor:separation µ0.2",
        },
        # The EM summary stays FLAT alongside the two blocks — that is the whole
        # point of one table: torque and the hot-spot on the same line.
        "results": {
            "T_em_avg_Nm": 183.2,
            "efficiency": 0.964,
            "thermal": THERMAL,
            "mechanical": MECHANICAL,
        },
    }
    r = client.post("/api/sims/saved", json=body)
    assert r.status_code == 200, r.text
    saved = r.json()
    assert saved["results"]["thermal"] == THERMAL
    assert saved["results"]["mechanical"] == MECHANICAL

    # …and it is the same object after a trip through the JSON file, which is
    # what the Compare tab actually reads.
    got = client.get("/api/sims/saved")
    assert got.status_code == 200, got.text
    rows = [s for s in got.json()["sims"] if s["id"] == saved["id"]]
    assert len(rows) == 1
    row = rows[0]
    assert row["results"]["thermal"] == THERMAL
    assert row["results"]["mechanical"] == MECHANICAL
    # The flat EM keys are untouched by the nesting beside them.
    assert row["results"]["T_em_avg_Nm"] == pytest.approx(183.2)
    # Booleans stay booleans (JSON has them; a float-typed schema would not).
    assert row["results"]["thermal"]["coupled_converged"] is True
    assert row["results"]["mechanical"]["poles_held"] is True
    # Every part's peak temperature survived — the user's actual request.
    for key, value in THERMAL.items():
        assert row["results"]["thermal"][key] == value, key


def test_tab_inputs_ride_in_params_as_they_were_sent(client):
    """`mech_*` / `therm_*` are ordinary params — strings included."""
    r = client.post("/api/sims/saved", json={
        "name": "Mechanical · inputs",
        "params": {"mech_loads": "both", "mech_torque_nm": 183.0,
                   "therm_bore_mode": "none"},
        "results": {"mechanical": {"sf_iron": 1.31}},
    })
    assert r.status_code == 200, r.text
    p = r.json()["params"]
    assert p["mech_loads"] == "both"
    assert p["mech_torque_nm"] == pytest.approx(183.0)
    assert p["therm_bore_mode"] == "none"
