"""The six silent-acceptance paths in routes/ — each one has to REJECT, loudly.

Every test here is a good/bad pair: the request a real client makes still works,
and the request that used to be swallowed now comes back 422 with the offending
field NAMED in structured JSON (``detail.invalid_parameters``, the shape the
Geometry tab already renders).

The paths, and what each used to do instead:

1. POST   /api/geometry/parameter        — created a knob with min > max, or one
                                           named after a DERIVED value, or
                                           silently overwrote an existing one.
2. DELETE /api/geometry/parameter/{name} — deleted a solver-required parameter
                                           with a cheerful {"success": true}.
3. geo= / mat= per-request overrides     — a malformed override fell back to the
                                           SHARED GLOBAL CONFIG, i.e. the client
                                           got somebody else's design with a 200.
4. GET    /api/geometry/pointcloud       — n_points unbounded (0 → empty viewer,
                                           1e9 → the process).
5. PUT    /api/geometry (unknown key)    — set in memory, dropped by the YAML
                                           writer: the "saved" edit reverted at
                                           the next restart.
6. PUT    /api/geometry (schema min/max) — the clamp lived only in the browser,
                                           so anything else could write a 3 m
                                           stator into the shared config.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from motor_ai_sim.api import app

_ROOT = Path(__file__).resolve().parents[1]
_CONFIG = _ROOT / "config" / "motor_config.yaml"


@pytest.fixture(autouse=True)
def _preserve_config():
    """These tests write the REAL config the user's active design lives in.
    Put it back byte-for-byte, whatever happens."""
    backup = _CONFIG.read_bytes() if _CONFIG.exists() else None
    try:
        yield
    finally:
        if backup is not None:
            _CONFIG.write_bytes(backup)
        from motor_ai_sim.config import clear_config_cache
        import motor_ai_sim.services.geometry_service as gs
        clear_config_cache()
        gs._current_geometry = None


@pytest.fixture
def client():
    return TestClient(app)


def _bad(r) -> list:
    """The structured rejection list, asserting the contract on the way."""
    assert r.status_code == 422, r.text[:500]
    detail = r.json()["detail"]
    assert isinstance(detail, dict), detail
    assert detail.get("error"), "a rejection must say what went wrong"
    params = detail["invalid_parameters"]
    assert params, "a rejection must name at least one field"
    for p in params:
        assert set(p) >= {"field", "value", "kind", "message"}
        assert p["message"]
    return params


def _fields(r) -> set:
    return {p["field"] for p in _bad(r)}


# ─────────────────────────────────────────────────────────────────────────────
# 1. POST /api/geometry/parameter
# ─────────────────────────────────────────────────────────────────────────────

_NEW_PARAM = {
    "name": "test_custom_knob", "label": "Test Custom Knob", "unit": "mm",
    "type": "float", "group": "custom", "min": 0.5, "max": 4.0, "step": 0.1,
    "default_value": 1.0, "description": "added by the test suite",
}


class TestAddParameter:
    def test_good_a_real_new_parameter_is_created(self, client):
        r = client.post("/api/geometry/parameter", json=dict(_NEW_PARAM))
        assert r.status_code == 200, r.text
        assert r.json()["name"] == "test_custom_knob"
        schema = {p["name"] for p in client.get("/api/geometry/schema").json()["parameters"]}
        assert "test_custom_knob" in schema

    def test_bad_min_not_below_max(self, client):
        r = client.post("/api/geometry/parameter",
                        json={**_NEW_PARAM, "min": 10.0, "max": 1.0})
        p = _bad(r)[0]
        assert p["field"] == "min"
        assert "less than max" in p["message"]

    def test_bad_a_derived_name_is_reserved(self, client):
        for name in ("stator_outer_radius", "num_slots", "angle_pole",
                     "shaft_radius"):
            r = client.post("/api/geometry/parameter",
                            json={**_NEW_PARAM, "name": name})
            p = _bad(r)[0]
            assert p["field"] == "name" and p["kind"] == "reserved"
            assert name in p["message"]

    def test_bad_an_existing_name_needs_an_explicit_update(self, client):
        r = client.post("/api/geometry/parameter",
                        json={**_NEW_PARAM, "name": "tooth_width"})
        p = _bad(r)[0]
        assert p["kind"] == "already_exists"
        # …and the live parameter was NOT overwritten
        assert client.get("/api/geometry").json()["tooth_width"] != 1.0

    def test_good_update_true_overwrites_deliberately(self, client):
        r = client.post("/api/geometry/parameter",
                        json={**_NEW_PARAM, "name": "test_custom_knob"})
        assert r.status_code == 200, r.text
        r = client.post("/api/geometry/parameter",
                        json={**_NEW_PARAM, "name": "test_custom_knob",
                              "max": 9.0, "update": True})
        assert r.status_code == 200, r.text

    def test_bad_default_outside_its_own_range(self, client):
        r = client.post("/api/geometry/parameter",
                        json={**_NEW_PARAM, "default_value": 99.0})
        assert "default_value" in _fields(r)

    def test_bad_name_is_not_an_identifier(self, client):
        r = client.post("/api/geometry/parameter",
                        json={**_NEW_PARAM, "name": "3;drop table"})
        assert _bad(r)[0]["field"] == "name"


# ─────────────────────────────────────────────────────────────────────────────
# 2. DELETE /api/geometry/parameter/{name}
# ─────────────────────────────────────────────────────────────────────────────

class TestDeleteParameter:
    @pytest.mark.parametrize("name", ["air_gap", "stator_diameter", "wire_width",
                                      "num_wires_per_slot", "motor_length",
                                      "magnet_height"])
    def test_bad_a_solver_required_parameter_cannot_be_deleted(self, client, name):
        r = client.delete(f"/api/geometry/parameter/{name}")
        p = _bad(r)[0]
        assert p["field"] == name and p["kind"] == "protected"
        assert "required by the solver" in r.json()["detail"]["error"]
        # still there
        assert name in client.get("/api/geometry").json()

    def test_good_a_custom_parameter_is_deletable(self, client):
        assert client.post("/api/geometry/parameter",
                           json=dict(_NEW_PARAM)).status_code == 200
        r = client.delete("/api/geometry/parameter/test_custom_knob")
        assert r.status_code == 200, r.text
        assert r.json()["success"] is True

    def test_unknown_name_is_still_a_404(self, client):
        r = client.delete("/api/geometry/parameter/no_such_parameter_at_all")
        assert r.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# 3. malformed geo= / mat= must not fall back to the shared global config
# ─────────────────────────────────────────────────────────────────────────────

_GOOD_GEO = '{"tooth_width": 2.6}'


class TestGeoOverride:
    def test_good_a_valid_override_is_applied(self, client):
        r = client.get("/api/geometry/validation", params={"geo": _GOOD_GEO})
        assert r.status_code == 200, r.text[:300]

    @pytest.mark.parametrize("geo", [
        "{not json",                      # truncated / hand-built
        '"%7B%22tooth_width%22%3A2%7D"',  # double-encoded
        "[1, 2, 3]",                      # right JSON, wrong shape
        "null",
    ])
    def test_bad_a_malformed_override_is_422_not_the_global_config(self, client, geo):
        r = client.get("/api/geometry/validation", params={"geo": geo})
        p = _bad(r)[0]
        assert p["field"] == "geo"
        assert "geo" in r.json()["detail"]["error"]

    def test_bad_a_non_numeric_value_names_the_key(self, client):
        r = client.get("/api/geometry/validation",
                       params={"geo": '{"tooth_width": "wide"}'})
        p = _bad(r)[0]
        assert p["field"] == "tooth_width" and p["kind"] == "not_a_number"

    def test_the_mesh_routes_reject_it_too(self, client):
        for route in ("/api/geometry/mesh2d", "/api/geometry/mesh_extruded"):
            r = client.get(route, params={"geo": "{broken"})
            assert r.status_code == 422, f"{route}: {r.status_code}"
            assert "geo" in _fields(r)

    def test_the_simulation_routes_reject_it_too(self, client):
        r = client.get("/api/simulation/physics/thermal_field2d",
                       params={"geo": "{broken"})
        assert r.status_code == 422, r.text[:300]
        assert "geo" in _fields(r)


class TestMatOverride:
    def test_good_a_valid_override_is_accepted(self, client):
        r = client.get("/api/simulation/status",
                       params={"mat": '{"assignment": {}, "materials": {}}'})
        assert r.status_code == 200, r.text[:300]

    @pytest.mark.parametrize("mat", ["{not json", "[1,2]", '"a string"'])
    def test_bad_a_malformed_override_is_422(self, client, mat):
        r = client.get("/api/simulation/status", params={"mat": mat})
        assert "mat" in _fields(r)

    def test_bad_neither_assignment_nor_materials(self, client):
        r = client.get("/api/simulation/status",
                       params={"mat": '{"assignmnet": {"magnet": "N52"}}'})
        p = _bad(r)[0]
        assert p["field"] == "mat"
        assert "assignment" in p["message"]

    def test_bad_assignment_of_the_wrong_type(self, client):
        r = client.get("/api/simulation/status",
                       params={"mat": '{"assignment": ["magnet"]}'})
        assert "assignment" in _fields(r)


# ─────────────────────────────────────────────────────────────────────────────
# 4. GET /api/geometry/pointcloud
# ─────────────────────────────────────────────────────────────────────────────

class TestPointcloud:
    def test_good_a_sane_request(self, client):
        r = client.get("/api/geometry/pointcloud", params={"n_points": 500})
        assert r.status_code == 200, r.text[:300]
        assert r.json()["n_points"] == 500

    @pytest.mark.parametrize("n", [0, -5, 2_000_001, 1_000_000_000])
    def test_bad_out_of_range(self, client, n):
        r = client.get("/api/geometry/pointcloud", params={"n_points": n})
        p = _bad(r)[0]
        assert p["field"] == "n_points"
        assert p["max"] == 2_000_000

    def test_the_documented_maximum_is_still_accepted_as_a_bound(self, client):
        from motor_ai_sim.routes._validation import MAX_POINTCLOUD_POINTS
        assert MAX_POINTCLOUD_POINTS == 2_000_000


# ─────────────────────────────────────────────────────────────────────────────
# 5. PUT /api/geometry — unknown keys
# ─────────────────────────────────────────────────────────────────────────────

class TestPutUnknownKeys:
    def test_good_a_real_key_still_saves(self, client):
        before = float(client.get("/api/geometry").json()["tooth_width"])
        r = client.put("/api/geometry", json={"tooth_width": before})
        assert r.status_code == 200, r.text[:300]

    def test_bad_a_typo_is_rejected_with_the_nearest_real_name(self, client):
        r = client.put("/api/geometry", json={"tooth_widht": 3.1})
        p = _bad(r)[0]
        assert p["field"] == "tooth_widht" and p["kind"] == "unknown_field"
        assert p["suggestion"] == "tooth_width"
        assert "did you mean 'tooth_width'?" in p["message"]

    def test_bad_a_key_from_nowhere_is_rejected_without_a_suggestion(self, client):
        r = client.put("/api/geometry", json={"zzz_not_a_parameter": 1.0})
        p = _bad(r)[0]
        assert p["kind"] == "unknown_field"
        assert p.get("suggestion") is None

    def test_nothing_is_written_when_a_key_is_unknown(self, client):
        before = client.get("/api/geometry").json()
        client.put("/api/geometry",
                   json={"tooth_width": float(before["tooth_width"]) + 0.3,
                         "tooth_widht": 9.0})
        after = client.get("/api/geometry").json()
        assert after["tooth_width"] == before["tooth_width"]


# ─────────────────────────────────────────────────────────────────────────────
# 6. PUT /api/geometry — the schema's own min/max, server-side
# ─────────────────────────────────────────────────────────────────────────────

def _schema_bounds(client, name: str):
    for p in client.get("/api/geometry/schema").json()["parameters"]:
        if p["name"] == name:
            return float(p["min"]), float(p["max"])
    raise AssertionError(f"{name} not in the served schema")


class TestPutSchemaBounds:
    def test_good_a_value_inside_the_served_bounds(self, client):
        lo, hi = _schema_bounds(client, "stator_diameter")
        r = client.put("/api/geometry", json={"stator_diameter": (lo + hi) / 2})
        assert r.status_code == 200, r.text[:300]

    def test_bad_above_the_maximum(self, client):
        lo, hi = _schema_bounds(client, "stator_diameter")
        r = client.put("/api/geometry", json={"stator_diameter": hi + 1000})
        p = _bad(r)[0]
        assert p["field"] == "stator_diameter" and p["kind"] == "out_of_range"
        assert p["max"] == hi
        assert "above the allowed maximum" in p["message"]

    def test_bad_below_the_minimum(self, client):
        lo, hi = _schema_bounds(client, "air_gap")
        r = client.put("/api/geometry", json={"air_gap": lo / 10.0})
        p = _bad(r)[0]
        assert p["field"] == "air_gap" and p["min"] == lo

    def test_nothing_is_written_when_a_value_is_out_of_range(self, client):
        before = client.get("/api/geometry").json()["stator_diameter"]
        _, hi = _schema_bounds(client, "stator_diameter")
        client.put("/api/geometry", json={"stator_diameter": hi + 1})
        assert client.get("/api/geometry").json()["stator_diameter"] == before

    def test_geo_unbounded_is_the_documented_escape_hatch(self, client, monkeypatch,
                                                          caplog):
        """GEO_UNBOUNDED=1 lifts the caps — and says so in the log."""
        import logging
        monkeypatch.setenv("GEO_UNBOUNDED", "1")
        with caplog.at_level(logging.WARNING,
                             logger="motor_ai_sim.routes._validation"):
            r = client.put("/api/geometry", json={"stator_diameter": 900.0})
        assert r.status_code == 200, r.text[:300]
        assert any("GEO_UNBOUNDED" in rec.message for rec in caplog.records), \
            "the escape hatch must be logged loudly while it is on"

    def test_an_untouched_out_of_bounds_field_does_not_block_other_edits(
            self, client, monkeypatch):
        """A design already outside a bound (older preset, tightened schema) has
        to stay editable in every OTHER field."""
        monkeypatch.setenv("GEO_UNBOUNDED", "1")
        assert client.put("/api/geometry",
                          json={"stator_diameter": 900.0}).status_code == 200
        monkeypatch.setenv("GEO_UNBOUNDED", "0")
        before = float(client.get("/api/geometry").json()["tooth_width"])
        r = client.put("/api/geometry", json={"tooth_width": before})
        assert r.status_code == 200, r.text[:300]
