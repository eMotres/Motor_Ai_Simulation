"""Integration tests for the FastAPI endpoints."""

import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from motor_ai_sim.api import app

client = TestClient(app)

# The file the APP writes (tests/conftest.py points it at a sandbox copy).
from motor_ai_sim.config import DEFAULT_CONFIG_PATH as _CONFIG


@pytest.fixture(autouse=True, scope="module")
def _preserve_working_config():
    """Put ``config/motor_config.yaml`` back the way we found it.

    These tests PUT to /api/geometry and POST to /api/geometry/reset, and both
    write the real config file — the same one the user's active design lives in.
    Running the suite silently replaced a 30 mm 12s14p machine with the 200 mm
    defaults, which is a nasty way to lose an afternoon's work and gives anyone
    a good reason never to run the tests.

    The right fix is to point the API at a temp config during tests; until the
    config layer takes an injectable path, save and restore.
    """
    backup = _CONFIG.read_bytes() if _CONFIG.exists() else None
    try:
        yield
    finally:
        if backup is not None:
            _CONFIG.write_bytes(backup)


def _config_geometry() -> dict:
    """Read the ``geometry`` section straight off disk.

    ``config/motor_config.yaml`` is the source of truth the geometry API serves
    and resets to, so expectations are derived from it rather than written as
    literals.  Hard-coded numbers here rot the moment the active design changes
    (they were 200 mm-era values long after the design moved to 30 mm), and a
    stale assertion at the top of the file aborts the whole run under ``-x``,
    hiding the physics regression tests behind an irrelevant failure.

    Reads the file directly instead of going through ``motor_ai_sim.config``:
    that module caches with a 1 s mtime probe (``_MTIME_PROBE_S``), so a cached
    copy can lag the file a test just wrote.
    """
    with open(_CONFIG, "r", encoding="utf-8") as f:
        return yaml.safe_load(f).get("geometry", {})


def _write_config_geometry(**values) -> None:
    """Edit the geometry section on disk behind the API's back.

    Deliberately does NOT flush ``motor_ai_sim.config``'s cache or the geometry
    service's in-process ``_current_geometry`` — that is what makes the state
    stale, which is exactly what ``POST /api/geometry/reset`` is supposed to
    discard.
    """
    with open(_CONFIG, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg.setdefault("geometry", {}).update(values)
    with open(_CONFIG, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


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
        # Perturb the *current* design rather than jumping to a fixed diameter:
        # a literal here is both a stale-value trap and a scale mismatch (a
        # 220 mm stator wrapped around 4 mm slots is not a machine anyone is
        # testing).
        target = float(_config_geometry()["stator_diameter"]) + 10.0
        r = client.put("/api/geometry", json={"stator_diameter": target})
        assert r.status_code == 200
        data = r.json()
        assert data["stator_diameter"] == pytest.approx(target)
        assert data["stator_outer_radius"] == pytest.approx(target / 2.0)

    def test_update_geometry_multiple_params(self):
        target = float(_config_geometry()["stator_diameter"]) - 5.0
        r = client.put("/api/geometry", json={"stator_diameter": target, "num_seg": 4})
        assert r.status_code == 200
        data = r.json()
        assert data["stator_diameter"] == pytest.approx(target)
        assert data["num_seg"] == 4

    def test_update_geometry_partial_preserves_others(self):
        baseline = client.get("/api/geometry").json()
        slot_height_before = baseline["slot_height"]

        client.put("/api/geometry", json={"stator_diameter": baseline["stator_diameter"] + 1.0})
        after = client.get("/api/geometry").json()
        assert after["slot_height"] == slot_height_before

    def test_reset_geometry(self):
        """``POST /reset`` re-reads config/motor_config.yaml — it is a *reload*,
        not a restore-to-factory-defaults.

        ``reset_geometry()`` (src/motor_ai_sim/services/geometry_service.py:82)
        clears the config cache and rebuilds the geometry from the tracked YAML,
        and ``PUT /api/geometry`` persists into that same YAML.  So the only
        state a reset can discard is an in-process copy that has drifted from
        the file — which is what this test sets up, by editing the file
        directly after the PUT.

        The old version PUT 300 and then asserted 200: it never exercised the
        reload at all, it just read back the value it had itself written, and
        the 200 was a leftover from the 200 mm-era config.
        """
        on_disk = float(_config_geometry()["stator_diameter"])

        # In-process geometry (and the file) now say on_disk + 7 …
        drifted = on_disk + 7.0
        put = client.put("/api/geometry", json={"stator_diameter": drifted})
        assert put.status_code == 200
        assert put.json()["stator_diameter"] == pytest.approx(drifted)

        # … then put the file back without telling the API, so the cached
        # geometry is stale by exactly 7 mm.
        _write_config_geometry(stator_diameter=on_disk)

        r = client.post("/api/geometry/reset")
        assert r.status_code == 200
        data = r.json()
        assert data["stator_diameter"] == pytest.approx(on_disk)
        assert data["stator_outer_radius"] == pytest.approx(on_disk / 2.0)

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
        assert "cache_enabled" in data
        assert isinstance(data["cache_enabled"], bool)


class TestUnknownMaterialIsVisible:
    """F6: an assigned material nobody can resolve must FAIL, not fall back.

    The path this guards used to log one WARNING and drop to the analytic
    magnet — no BH curve and no demag knee — which returned an empty demag map
    and a shaft eddy loss of 63.9 W where the real magnet gives 7.0 W, a factor
    9 with every number on screen looking plausible
    (docs/SOLVER_TRIALS_2026-07-30.md F6).
    """

    def test_request_material_override_with_unknown_name_is_400(self):
        import json
        bad = json.dumps({"assignment": {"magnet": "N42SH"}})
        r = client.get("/api/simulation/physics/fem_transient",
                       params={"mat": bad, "restore": "true"})
        assert r.status_code == 400
        # the offending name AND what is available, or the message is useless
        assert "N42SH" in r.json()["detail"]
        assert "F45SH_120C" in r.json()["detail"]

    def test_material_assigned_to_the_wrong_part_is_400(self):
        import json
        bad = json.dumps({"assignment": {"magnet": "copper"}})
        r = client.get("/api/simulation/physics/fem_transient",
                       params={"mat": bad, "restore": "true"})
        assert r.status_code == 400
        assert "copper" in r.json()["detail"]

    def test_override_carrying_its_own_props_is_accepted(self):
        """A name the library never heard of is fine when the request BRINGS it."""
        import json
        ov = json.dumps({"assignment": {"magnet": "MyLabMagnet"},
                         "materials": {"MyLabMagnet": {"category": "magnet",
                                                       "Br": 1.2}}})
        r = client.get("/api/simulation/physics/fem_transient",
                       params={"mat": ov, "restore": "true"})
        assert r.status_code == 200

    def test_build_materials_raises_on_an_unknown_assignment(self):
        """The EVAL paths (optimizer, trials, regression) have no HTTP layer —
        they must see an exception, not a warning."""
        from motor_ai_sim.materials import UnknownMaterialError
        from motor_ai_sim.material_context import set_request_materials
        from motor_ai_sim.simulation.fem_solver_2d import build_materials
        set_request_materials({"assignment": {"magnet": "N42SH"},
                               "materials": {}})
        try:
            with pytest.raises(UnknownMaterialError):
                build_materials({"A": 0.0, "B": 0.0, "C": 0.0}, [],
                                {"magnets": [], "coils": []}, 0.0, 1e-6, 6)
        finally:
            set_request_materials(None)

    def test_live_config_assignment_resolves(self):
        """The shared config's own assignment must be valid — this is the check
        that turns a stale name in motor_config.yaml into a red test instead of
        a plausible-looking wrong answer."""
        from motor_ai_sim.config import get_material_assignments
        from motor_ai_sim.materials import validate_assignment
        validate_assignment(get_material_assignments())


class TestWindingConnectionArgument:
    """F3: the per-request winding channel, and its refusal to guess."""

    def test_unreadable_connection_is_400(self):
        r = client.get("/api/simulation/physics/fem_transient",
                       params={"connection": "N42SH", "restore": "true"})
        assert r.status_code == 400
        assert "N42SH" in r.json()["detail"]

    def test_readable_connection_is_accepted(self):
        r = client.get("/api/simulation/physics/fem_transient",
                       params={"connection": "2S-2P", "restore": "true"})
        assert r.status_code == 200

    def test_connection_parses_to_the_right_parallel_paths(self):
        from motor_ai_sim.winding import parse_connection
        assert parse_connection("4S") == (1, 4)
        assert parse_connection("4P") == (4, 1)
        assert parse_connection("2S-2P") == (2, 2)
        assert parse_connection("2P2S") == (2, 2)      # legacy spelling
        with pytest.raises(ValueError):
            parse_connection("nonsense")
