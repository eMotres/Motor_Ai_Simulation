"""Integration tests for the FastAPI endpoints."""

import pytest
from fastapi.testclient import TestClient

from motor_ai_sim.api import app

client = TestClient(app)


class TestHealthAndMeta:
    def test_root(self):
        r = client.get("/")
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == "Motor Geometry API"
        assert "endpoints" in data

    def test_health(self):
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "healthy"

    def test_materials(self):
        r = client.get("/api/materials")
        assert r.status_code == 200
        assert isinstance(r.json(), dict)

    def test_config(self):
        r = client.get("/api/config")
        assert r.status_code == 200
        data = r.json()
        assert "geometry" in data
        assert "materials" in data
        assert "mesh" in data
        assert "simulation" in data


class TestGeometryEndpoints:
    def test_get_geometry_returns_dict(self):
        r = client.get("/api/geometry")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, dict)
        assert "stator_diameter" in data

    def test_get_geometry_has_derived_params(self):
        r = client.get("/api/geometry")
        data = r.json()
        for key in ("stator_outer_radius", "stator_inner_radius", "rotor_outer_radius", "num_slots", "num_poles"):
            assert key in data, f"Missing derived param: {key}"

    def test_update_geometry_single_param(self):
        r = client.put("/api/geometry", json={"stator_diameter": 220.0})
        assert r.status_code == 200
        data = r.json()
        assert data["stator_diameter"] == 220.0
        assert data["stator_outer_radius"] == pytest.approx(110.0)

    def test_update_geometry_multiple_params(self):
        r = client.put("/api/geometry", json={"stator_diameter": 180.0, "num_seg": 4})
        assert r.status_code == 200
        data = r.json()
        assert data["stator_diameter"] == 180.0
        assert data["num_seg"] == 4

    def test_update_geometry_partial_preserves_others(self):
        baseline = client.get("/api/geometry").json()
        slot_height_before = baseline["slot_height"]

        client.put("/api/geometry", json={"stator_diameter": 200.0})
        after = client.get("/api/geometry").json()
        assert after["slot_height"] == slot_height_before

    def test_reset_geometry(self):
        client.put("/api/geometry", json={"stator_diameter": 300.0})
        r = client.post("/api/geometry/reset")
        assert r.status_code == 200
        data = r.json()
        assert data["stator_diameter"] == 200.0

    def test_geometry_summary(self):
        r = client.get("/api/geometry/summary")
        assert r.status_code == 200
        data = r.json()
        for key in ("stator_outer_radius", "stator_inner_radius", "rotor_outer_radius",
                    "air_gap", "num_slots", "num_poles", "shaft_radius"):
            assert key in data

    def test_geometry_summary_values_consistent(self):
        r = client.get("/api/geometry/summary")
        data = r.json()
        assert data["stator_outer_radius"] > data["stator_inner_radius"] > data["rotor_outer_radius"]
        assert data["air_gap"] > 0
        assert data["num_slots"] > 0
        assert data["num_poles"] > 0


class TestGeometrySchema:
    def test_schema_returns_parameters_and_groups(self):
        r = client.get("/api/geometry/schema")
        assert r.status_code == 200
        data = r.json()
        assert "parameters" in data
        assert "groups" in data

    def test_schema_parameters_have_required_fields(self):
        r = client.get("/api/geometry/schema")
        params = r.json()["parameters"]
        assert len(params) > 0
        for p in params:
            for field in ("name", "label", "type", "min", "max", "step", "group"):
                assert field in p, f"Parameter {p.get('name')} missing field: {field}"

    def test_schema_groups_are_sorted_by_order(self):
        r = client.get("/api/geometry/schema")
        groups = r.json()["groups"]
        orders = [g["order"] for g in groups]
        assert orders == sorted(orders)

    def test_schema_parameter_types_are_valid(self):
        r = client.get("/api/geometry/schema")
        for p in r.json()["parameters"]:
            assert p["type"] in ("float", "int", "string"), f"Invalid type: {p['type']}"

    def test_schema_min_less_than_max(self):
        r = client.get("/api/geometry/schema")
        for p in r.json()["parameters"]:
            if p["type"] in ("float", "int"):
                assert p["min"] < p["max"], f"min >= max for {p['name']}"


class TestPipelineStatus:
    def test_pipeline_status(self):
        r = client.get("/api/pipeline/status")
        assert r.status_code == 200
        data = r.json()
        assert "fusion360_available" in data
        assert "modulus_bridge_available" in data
        assert "cache_enabled" in data
        assert isinstance(data["cache_enabled"], bool)
