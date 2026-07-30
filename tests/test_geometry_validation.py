"""Real geometry validation — the check that the cross-section handed to the
mesher describes a machine that can physically exist.

The point of the module under test is that it reads the SAME 2-D polygons the
solver meshes (``CadQueryMotor.get_2d_polygons``), so these tests drive it
through real geometry wherever a real geometry can produce the defect, and
through hand-built polygons only for defects the parametric builder cannot
express (a magnet pushed through the shaft).
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from motor_ai_sim.geometry_validation import (
    GeometryInvalid,
    assert_valid,
    validate_geometry,
    validate_parameter_values,
    validate_polygons,
)

_ROOT = Path(__file__).resolve().parents[1]


def _codes(result) -> set:
    return {v.code for v in result.violations}


def _error_codes(result) -> set:
    return {v.code for v in result.errors}


# ── a known-good machine must stay silent ────────────────────────────────────
# The 30 mm 12s/14p design is the one the user actually runs; if the validator
# ever cries wolf on it the feature is worse than useless.
GEO_30MM = {
    "stator_diameter": 30, "slot_height": 4.3, "core_thickness": 1.5,
    "num_seg": 2, "num_slots_per_segment": 6, "num_poles_per_segment": 7,
    "air_gap": 0.2, "tooth_width": 2.6, "tooth2_width": 1.4, "cut_width": 1.5,
    "insulation_thickness": 0.05, "wire_width": 2, "wire_height": 0.5,
    "wire_spacing_x": 0.1, "wire_spacing_y": 0.1, "num_wires_per_slot": 6,
    "wire_split": 1, "slot_hs": 0.267, "magnet_height": 4.5,
    "rotor_house_height": 0.8, "shaft_height": 2, "magnet_fill_down": 0.9,
    "magnet_fill_up": 0.3, "magnet_fill_radius": 0.1, "magnet_up_gap": 0.1,
    "rotor_hole": 0.7, "magnet_down_height": 1.4, "magnet_lamination": 0,
    "stator_fillet_r": 1.2, "stator_fillet_r1": 0, "rotor_fill_r": 0.2,
    "motor_length": 10,
}

# The 37 mm 24s/28p design the user had loaded when this was written: the two
# coil sides in each slot interpenetrate, and copper sits inside the slot liner.
GEO_37MM_BAD = {
    "stator_diameter": 37.0, "slot_height": 6, "core_thickness": 1.9,
    "num_seg": 4, "num_slots_per_segment": 6, "num_poles_per_segment": 7,
    "air_gap": 0.2, "tooth_width": 3.1, "tooth2_width": 1.7, "cut_width": 1,
    "insulation_thickness": 0.06, "wire_width": 2.2, "wire_height": 0.6,
    "wire_spacing_x": 0.1, "wire_spacing_y": 0.1, "num_wires_per_slot": 7,
    "wire_split": 1, "slot_hs": 0.267, "magnet_height": 5.8,
    "rotor_house_height": 1, "shaft_height": 2, "magnet_fill_down": 0.85,
    "magnet_fill_up": 0.4, "magnet_fill_radius": 0.5, "magnet_up_gap": 0.2,
    "rotor_hole": 0.8, "magnet_down_height": 0.8, "magnet_lamination": 0,
    "stator_fillet_r": 0.4, "stator_fillet_r1": 0.2, "rotor_fill_r": 0,
    "motor_length": 12,
}


class TestValidGeometryPasses:
    def test_30mm_design_is_clean(self):
        res = validate_geometry(GEO_30MM)
        assert res.ok, res.summary()
        assert not res.errors
        assert res.min_air_gap_mm == pytest.approx(0.2, abs=0.01)

    def test_assert_valid_returns_the_result(self):
        res = assert_valid(GEO_30MM)
        assert res.ok

    def test_repo_committed_config_is_clean(self):
        """Whatever ships in git must pass — otherwise nobody can solve on a
        fresh clone."""
        import subprocess
        try:
            raw = subprocess.run(
                ["git", "show", "HEAD:config/motor_config.yaml"],
                cwd=_ROOT, capture_output=True, text=True, timeout=30, check=True
            ).stdout
        except Exception:
            pytest.skip("git not available")
        geo = yaml.safe_load(raw).get("geometry", {})
        res = validate_geometry(dict(geo))
        assert res.ok, res.summary()


class TestOverlapsAreCaught:
    def test_37mm_magnet58_config_names_its_violations(self):
        res = validate_geometry(GEO_37MM_BAD)
        assert not res.ok
        # The two coil sides of each slot share copper.
        assert "coil_overlaps_coil" in _error_codes(res)
        # …and the copper sits inside the slot liner.
        assert "coil_overlaps_liner" in _error_codes(res)
        # Every reported violation names both parts, an area and a place.
        for v in res.errors:
            assert v.part_a and v.message
            if v.overlap_area_mm2 > 0:
                assert v.x_mm is not None and v.y_mm is not None
        # …and points at parameters the user can actually turn.
        clash = next(v for v in res.errors if v.code == "coil_overlaps_coil")
        assert "wire_width" in clash.likely_params
        assert clash.overlap_area_mm2 > 0.0
        # A 96-way clash must not produce 96 rows in the UI.
        assert len([v for v in res.violations if v.code == "coil_overlaps_coil"]) <= 6
        assert res.hidden.get("coil_overlaps_coil", 0) > 0

    def test_solve_gate_raises_with_the_list(self):
        with pytest.raises(GeometryInvalid) as ei:
            assert_valid(GEO_37MM_BAD)
        payload = ei.value.to_dict()
        assert payload["ok"] is False
        assert payload["n_errors"] > 0
        assert payload["violations"], "the exception must carry the list"
        assert "not buildable" in str(ei.value)

    def test_winding_that_silently_loses_turns_is_rejected(self):
        geo = dict(GEO_37MM_BAD, num_wires_per_slot=200)
        res = validate_geometry(geo)
        assert "winding_does_not_fit" in _error_codes(res)
        msg = next(v for v in res.errors
                   if v.code == "winding_does_not_fit").message
        assert "200" in msg  # says how many were asked for


class TestSyntheticDefects:
    """Defects the parametric builder cannot express are driven straight through
    ``validate_polygons`` — the same entry point the real path uses once the
    polygons exist."""

    @staticmethod
    def _polys(magnet_xs=(4.0, 8.0)):
        from shapely.geometry import Point, Polygon
        shaft = Point(0, 0).buffer(6.0)
        rotor = Point(0, 0).buffer(20.0).difference(Point(0, 0).buffer(6.0))
        mag = Polygon([(magnet_xs[0], -1), (magnet_xs[1], -1),
                       (magnet_xs[1], 1), (magnet_xs[0], 1)])
        stator = Point(0, 0).buffer(40.0).difference(Point(0, 0).buffer(25.0))
        coil = Polygon([(26, -1), (30, -1), (30, 1), (26, 1)])
        return {
            "stator": stator, "rotor": rotor, "shaft": shaft,
            "magnets": [(mag, 1)], "coils": [coil], "slot_insulation": [],
            "in_band": None, "out_band": None,
            "n_wires_requested": 1, "n_wires_fit": 1, "coils_overflow": False,
        }

    _PARAMS = {"rotor_outer_radius": 20.0, "rotor_inner_radius": 6.0,
               "stator_inner_radius": 25.0, "stator_outer_radius": 40.0,
               "air_gap": 5.0}

    def test_magnet_driven_through_the_shaft_is_rejected(self):
        res = validate_polygons(self._polys(magnet_xs=(4.0, 8.0)),
                                params=self._PARAMS)
        assert not res.ok
        codes = _error_codes(res)
        assert "magnet_overlaps_shaft" in codes
        assert "magnet_overlaps_rotor" in codes     # it also cuts the iron
        assert "magnet_outside_rotor" in codes      # …and escapes the bore
        v = next(x for x in res.errors if x.code == "magnet_overlaps_shaft")
        assert v.overlap_area_mm2 > 0.1
        assert v.x_mm is not None and v.y_mm is not None
        assert "shaft" in v.message.lower()

    def test_coil_through_the_stator_iron_is_rejected(self):
        # The coil at r=26..30 sits inside the stator ring (25..40).
        res = validate_polygons(self._polys(), params=self._PARAMS)
        assert "coil_overlaps_stator" in _error_codes(res)

    def test_a_magnet_inside_its_pocket_is_clean(self):
        """Sanity in the other direction: a magnet that only TOUCHES the iron it
        sits in (shared boundary) must not be reported."""
        from shapely.geometry import Point, Polygon, box
        pocket = box(8.0, -1.0, 14.0, 1.0)
        rotor = (Point(0, 0).buffer(20.0)
                 .difference(Point(0, 0).buffer(6.0))
                 .difference(pocket))
        slot = box(29.0, -2.0, 35.0, 2.0)
        polys = {
            "stator": (Point(0, 0).buffer(40.0)
                       .difference(Point(0, 0).buffer(25.0))
                       .difference(slot)),
            "rotor": rotor, "shaft": Point(0, 0).buffer(6.0),
            "magnets": [(Polygon([(8, -1), (14, -1), (14, 1), (8, 1)]), 1)],
            "coils": [box(30.0, -1.0, 34.0, 1.0)], "slot_insulation": [],
            "in_band": None, "out_band": None,
            "n_wires_requested": 1, "n_wires_fit": 1, "coils_overflow": False,
        }
        res = validate_polygons(polys, params=self._PARAMS)
        assert res.ok, res.summary()

    def test_closed_air_gap_is_reported_with_the_number(self):
        from shapely.geometry import Point, box
        polys = {
            "stator": Point(0, 0).buffer(40.0).difference(Point(0, 0).buffer(19.0)),
            "rotor": Point(0, 0).buffer(20.0).difference(Point(0, 0).buffer(6.0)),
            "shaft": Point(0, 0).buffer(6.0), "magnets": [],
            "coils": [box(30.0, -1.0, 34.0, 1.0)], "slot_insulation": [],
            "in_band": None, "out_band": None,
            "n_wires_requested": 1, "n_wires_fit": 1, "coils_overflow": False,
        }
        res = validate_polygons(polys, params={**self._PARAMS,
                                               "stator_inner_radius": 19.0})
        assert "air_gap_not_positive" in _error_codes(res)
        assert res.min_air_gap_mm is not None and res.min_air_gap_mm < 0


class TestParameterSanity:
    def test_clean_geometry_has_no_field_errors(self):
        assert validate_parameter_values(dict(GEO_30MM)) == []
        assert validate_parameter_values(dict(GEO_37MM_BAD)) == []

    @pytest.mark.parametrize("patch,field", [
        ({"wire_width": -1.0}, "wire_width"),
        ({"tooth_width": 0.0}, "tooth_width"),
        ({"magnet_height": 0.0}, "magnet_height"),
        ({"air_gap": -0.5}, "air_gap"),
        ({"num_wires_per_slot": 0}, "num_wires_per_slot"),
        ({"num_seg": 0}, "num_seg"),
        ({"magnet_fill_up": 1.5}, "magnet_fill_up"),
        ({"rotor_hole": 0.0}, "rotor_hole"),
        ({"insulation_thickness": -0.1}, "insulation_thickness"),
        ({"stator_diameter": float("nan")}, "stator_diameter"),
        ({"stator_diameter": 1e6}, "stator_diameter"),
    ])
    def test_absurd_values_are_named(self, patch, field):
        bad = validate_parameter_values({**GEO_30MM, **patch})
        assert field in {b["field"] for b in bad}, bad
        assert all(b["message"] for b in bad)

    def test_slots_eating_through_the_bore_is_a_derived_error(self):
        bad = validate_parameter_values({**GEO_30MM, "slot_height": 50.0})
        assert bad
        assert any(b["kind"] == "derived" for b in bad)

    def test_magnets_reaching_through_the_rotor_centre(self):
        bad = validate_parameter_values({**GEO_30MM, "magnet_height": 40.0})
        assert any(b["field"] == "magnet_height" and b["kind"] == "derived"
                   for b in bad), bad

    def test_non_finite_value_stays_json_safe(self):
        import json
        bad = validate_parameter_values({**GEO_30MM,
                                         "stator_diameter": float("inf")})
        json.dumps(bad, allow_nan=False)   # must not raise


class TestApiWiring:
    """The two ends of the contract: PUT /api/geometry never blocks a save but
    reports what is wrong, and a SOLVE refuses with 422 and the same list."""

    _CONFIG = _ROOT / "config" / "motor_config.yaml"

    @pytest.fixture(autouse=True)
    def _preserve_config(self):
        """These tests write the REAL config the user's active design lives in.
        Put it back byte-for-byte, whatever happens."""
        backup = self._CONFIG.read_bytes() if self._CONFIG.exists() else None
        try:
            yield
        finally:
            if backup is not None:
                self._CONFIG.write_bytes(backup)
            from motor_ai_sim.config import clear_config_cache
            import motor_ai_sim.services.geometry_service as gs
            clear_config_cache()
            gs._current_geometry = None

    @staticmethod
    def _client():
        from fastapi.testclient import TestClient
        from motor_ai_sim.api import app
        return TestClient(app)

    def _load(self, client, geo: dict) -> None:
        """Push a whole design through the public PUT, as the UI would."""
        r = client.put("/api/geometry", json=dict(geo))
        assert r.status_code == 200, r.text

    def test_put_reports_violations_without_refusing_the_save(self):
        client = self._client()
        r = client.put("/api/geometry", json=dict(GEO_37MM_BAD))
        assert r.status_code == 200, "a mid-edit design must still save"
        body = r.json()
        assert "geometry_validation" in body
        val = body["geometry_validation"]
        assert val["ok"] is False
        assert val["n_errors"] > 0
        codes = {v["code"] for v in val["violations"]}
        assert "coil_overlaps_coil" in codes
        # the value really was written
        assert client.get("/api/geometry").json()["magnet_height"] == 5.8

    def test_put_rejects_an_unusable_value_with_422_naming_the_field(self):
        client = self._client()
        before = client.get("/api/geometry").json()["tooth_width"]
        r = client.put("/api/geometry", json={"tooth_width": -2.0})
        assert r.status_code == 422, r.text
        fields = {b["field"] for b in r.json()["detail"]["invalid_parameters"]}
        assert "tooth_width" in fields
        # nothing was persisted
        assert client.get("/api/geometry").json()["tooth_width"] == before

    def test_validation_endpoint_reads_the_live_design(self):
        client = self._client()
        self._load(client, GEO_30MM)
        assert client.get("/api/geometry/validation").json()["ok"] is True
        self._load(client, GEO_37MM_BAD)
        assert client.get("/api/geometry/validation").json()["ok"] is False

    def test_solve_is_refused_with_422_and_the_violation_list(self):
        client = self._client()
        self._load(client, GEO_37MM_BAD)
        r = client.get("/api/simulation/physics/fem_transient",
                       params={"n_steps_per_period": 4, "n_periods": 0.25,
                               "fresh": "true"})
        assert r.status_code == 422, r.text[:400]
        detail = r.json()["detail"]
        assert detail["geometry_validation"]["n_errors"] > 0
        assert "coil_overlaps_coil" in {
            v["code"] for v in detail["geometry_validation"]["violations"]}
        assert "not buildable" in detail["error"]


class TestResultShape:
    def test_to_dict_is_renderable_json(self):
        import json
        d = validate_geometry(GEO_37MM_BAD).to_dict()
        json.dumps(d, allow_nan=False)
        assert set(d) >= {"ok", "n_errors", "n_warnings", "violations",
                          "hidden", "min_air_gap_mm", "checks_run"}
        for v in d["violations"]:
            assert set(v) >= {"code", "severity", "part_a", "part_b", "message",
                              "overlap_area_mm2", "likely_params"}
            assert v["severity"] in ("error", "warning")

    def test_warnings_do_not_block_a_solve(self):
        """`ok` is about ERRORS only — a disconnected-iron note must not stop a
        run the user is entitled to."""
        res = validate_geometry(GEO_30MM)
        assert res.ok
        # synthesise a warning-only result
        from motor_ai_sim.geometry_validation import ValidationResult, Violation
        r = ValidationResult(violations=[Violation(
            code="iron_disconnected", severity="warning", part_a="The rotor core",
            part_b="", message="two pieces")])
        assert r.ok and r.warnings and not r.errors
