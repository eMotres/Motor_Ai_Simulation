"""Tests for motor geometry and materials."""

import pytest
import numpy as np

from motor_ai_sim.geometry import (
    MotorGeometryParams,
    GeometryRegion,  # Deprecated but still available
    MagneticMaterial,
    MaterialRegistry,
    get_material_id,
)


class TestMotorGeometryParams:
    """Tests for MotorGeometryParams dataclass."""

    def test_default_params(self):
        """Test default parameter values."""
        params = MotorGeometryParams()
        
        # Check primary parameters (all in mm)
        assert params.stator_diameter == 200.0  # mm
        assert params.slot_height == 16.0  # mm
        assert params.core_thickness == 3.8  # mm
        assert params.num_seg == 6
        assert params.num_slots_per_segment == 6
        assert params.num_poles_per_segment == 7
        assert params.air_gap == 0.65  # mm
        assert params.magnet_height == 13.8  # mm
        assert params.rotor_house_height == 1.2  # mm
        assert params.stator_width == 30.0  # mm

    def test_derived_params(self):
        """Test derived parameters are computed correctly."""
        params = MotorGeometryParams()
        
        # Stator radii
        assert params.stator_outer_radius == params.stator_diameter / 2  # 100 mm
        expected_inner = params.stator_outer_radius - params.core_thickness - params.slot_height
        assert abs(params.stator_inner_radius - expected_inner) < 1e-10
        
        # Slot and pole counts
        assert params.num_slots == params.num_seg * params.num_slots_per_segment  # 36
        assert params.num_poles == params.num_seg * params.num_poles_per_segment  # 42
        
        # Angles in degrees
        assert abs(params.angle_slot - 360.0 / params.num_slots) < 1e-10
        assert abs(params.angle_pole - 360.0 / params.num_poles) < 1e-10
        
        # Angular pitches in radians
        expected_slot_pitch = 2 * np.pi / params.num_slots
        assert abs(params.slot_pitch - expected_slot_pitch) < 1e-10
        
        # Rotor radii
        expected_rotor_outer = (
            params.stator_outer_radius - params.core_thickness - params.slot_height - params.air_gap
        )
        assert abs(params.rotor_outer_radius - expected_rotor_outer) < 1e-10
        
        expected_rotor_inner = params.rotor_outer_radius - params.magnet_height - params.rotor_house_height
        assert abs(params.rotor_inner_radius - expected_rotor_inner) < 1e-10

    def test_invalid_params(self):
        """Test that invalid parameters raise errors."""
        # Negative air gap (rotor larger than stator bore)
        with pytest.raises(ValueError):
            MotorGeometryParams(
                stator_diameter=50.0,
                slot_height=5.0,
                core_thickness=5.0,
                air_gap=-5.0,  # Negative
            )
        
        # Invalid pole count (too few)
        with pytest.raises(ValueError):
            MotorGeometryParams(
                num_seg=1,
                num_slots_per_segment=6,
                num_poles_per_segment=1,  # Only 1 pole
            )

    def test_custom_params(self):
        """Test custom parameter values."""
        params = MotorGeometryParams(
            stator_diameter=100.0,
            slot_height=10.0,
            core_thickness=5.0,
            num_seg=4,
            num_slots_per_segment=8,
            num_poles_per_segment=6,
        )
        
        assert params.stator_diameter == 100.0
        assert params.stator_outer_radius == 50.0
        assert params.num_slots == 32
        assert params.num_poles == 24

    def test_unit_conversion(self):
        """Test degree to radian conversion."""
        # Test deg_to_rad
        assert abs(MotorGeometryParams.deg_to_rad(180.0) - np.pi) < 1e-10
        assert abs(MotorGeometryParams.deg_to_rad(90.0) - np.pi/2) < 1e-10
        assert abs(MotorGeometryParams.deg_to_rad(0.0)) < 1e-10
        
        # Test rad_to_deg
        assert abs(MotorGeometryParams.rad_to_deg(np.pi) - 180.0) < 1e-10
        assert abs(MotorGeometryParams.rad_to_deg(np.pi/2) - 90.0) < 1e-10


class TestGeometryRegionDeprecated:
    """Tests for deprecated GeometryRegion class."""

    def test_geometry_region_deprecation_warning(self):
        """Test that GeometryRegion emits deprecation warning."""
        import warnings
        
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            region = GeometryRegion(
                name="test",
                region_type="annulus",
                r_inner=50.0,
                r_outer=100.0,
            )
            
            # Check deprecation warning was raised
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert "deprecated" in str(w[0].message).lower()


class TestMaterials:
    """Tests for material definitions."""

    def test_material_registry(self):
        """Test material registry."""
        # Get known material
        steel = MaterialRegistry.get("m27_silicon_steel")
        # Material name is human-readable, not the key
        assert "silicon" in steel.name.lower() or "m27" in steel.name.lower() or "steel" in steel.name.lower()
        assert steel.mu_r > 1.0
        assert steel.sigma > 0

    def test_material_id(self):
        """Test material ID generation."""
        id1 = get_material_id("m27_silicon_steel")
        id2 = get_material_id("copper")
        id3 = get_material_id("m27_silicon_steel")
        
        # Same material should have same ID
        assert id1 == id3
        # Different materials should have different IDs
        assert id1 != id2

    def test_permanent_magnet_material(self):
        """Test permanent magnet material properties."""
        magnet = MaterialRegistry.get("ndfeb_n42")
        
        assert magnet.is_permanent_magnet
        assert magnet.Br > 0
        # Hc can be positive or negative depending on convention
        assert magnet.Hc is not None
        assert magnet.get_magnetization() > 0

    def test_unknown_material_raises_error(self):
        """Test getting unknown material raises ValueError."""
        with pytest.raises(ValueError, match="Unknown material"):
            MaterialRegistry.get("unknown_material_xyz")


class TestYAMLLoading:
    """Tests for YAML configuration loading."""

    def test_load_from_yaml(self, tmp_path):
        """Test loading parameters from YAML file."""
        yaml_content = """
geometry:
  stator_diameter: 150.0
  slot_height: 12.0
  core_thickness: 4.0
  num_seg: 4
  num_slots_per_segment: 6
  num_poles_per_segment: 5
  air_gap: 0.5
  magnet_height: 10.0
  rotor_house_height: 1.0
"""
        config_file = tmp_path / "test_config.yaml"
        config_file.write_text(yaml_content)
        
        params = MotorGeometryParams.from_yaml(config_file)
        
        assert params.stator_diameter == 150.0
        assert params.slot_height == 12.0
        assert params.num_seg == 4
        assert params.num_slots == 24
        assert params.num_poles == 20

    def test_load_missing_file(self):
        """Test loading from non-existent file raises error."""
        with pytest.raises(FileNotFoundError):
            MotorGeometryParams.from_yaml("nonexistent_config.yaml")


class TestMergeGeoOverride:
    """The per-request geometry override must define the motor's topology.

    Invariant: the merged dict's num_slots/num_poles always describe the
    OVERRIDE's motor — explicit counts first, else the override's own
    num_seg × *_per_segment product; the base config's counts survive only
    when the override says nothing about topology.  (A plain dict-update let
    the base's counts pair with the override's mesh geometry — a chimera
    that mis-phased the winding layout and pole-pair drive: psi ~ 0, zero-
    mean torque on the full ring, wrong sector BC sign on wedges.)
    """

    BASE = {"num_slots": 24, "num_poles": 20, "num_seg": 4,
            "num_slots_per_segment": 6, "num_poles_per_segment": 5,
            "stator_diameter": 150.0}

    def test_explicit_counts_win(self):
        from motor_ai_sim.simulation.geometry_2d import merge_geo_override
        g = merge_geo_override(self.BASE, {"num_slots": 12, "num_poles": 14})
        assert (g["num_slots"], g["num_poles"]) == (12, 14)

    def test_segment_form_beats_base_counts(self):
        from motor_ai_sim.simulation.geometry_2d import merge_geo_override
        ov = {"num_seg": 2, "num_slots_per_segment": 6,
              "num_poles_per_segment": 7, "stator_diameter": 40.0}
        g = merge_geo_override(self.BASE, ov)
        assert (g["num_slots"], g["num_poles"]) == (12, 14)
        assert g["stator_diameter"] == 40.0

    def test_null_counts_fall_back_to_segment_form(self):
        from motor_ai_sim.simulation.geometry_2d import merge_geo_override
        ov = {"num_slots": None, "num_poles": None, "num_seg": 4,
              "num_slots_per_segment": 6, "num_poles_per_segment": 7}
        g = merge_geo_override(self.BASE, ov)
        assert (g["num_slots"], g["num_poles"]) == (24, 28)

    def test_topology_silent_override_keeps_base_counts(self):
        from motor_ai_sim.simulation.geometry_2d import merge_geo_override
        g = merge_geo_override(self.BASE, {"air_gap": 0.3})
        assert (g["num_slots"], g["num_poles"]) == (24, 20)

    def test_no_override_returns_base(self):
        from motor_ai_sim.simulation.geometry_2d import merge_geo_override
        g = merge_geo_override(self.BASE, None)
        assert g == self.BASE

    def test_inconsistent_base_follows_segment_form(self):
        # A half-applied config can carry stale explicit counts next to the
        # primary segment form; the CAD (MotorGeometryParams) meshes the
        # segment form, so the resolved counts must match it.
        from motor_ai_sim.simulation.geometry_2d import merge_geo_override
        base = dict(self.BASE, num_seg=2, num_slots_per_segment=6,
                    num_poles_per_segment=7)   # stale explicit 24/20
        g = merge_geo_override(base, None)
        assert (g["num_slots"], g["num_poles"]) == (12, 14)
        g = merge_geo_override(base, {"air_gap": 0.3})
        assert (g["num_slots"], g["num_poles"]) == (12, 14)


class TestDerivedFieldsFollowTheOverride:
    """A DERIVED field must describe the merged motor, never the base config.

    The counts were only the first member of this family.  ``motor_config.yaml``
    also stores ``slot_width``, the four radii and the four angles/pitches — and a
    per-request override supplies PRIMARIES only, so a plain merge left all nine
    describing the base.

    That was live: ``fem_transient_sliding_band`` sizes its mesh from
    ``geo["slot_width"]`` (element = slot_width/2), so the SAME candidate request
    was meshed at 1.25 mm while the user's config held the 40 mm design
    (slot_width 2.5) and at 1.15 mm after it moved to the 30 mm one (2.3) — six
    pinned physics cases red with nothing wrong in the code.  A per-request
    evaluation whose MESH depends on somebody else's saved design is not an
    evaluation of the design that was asked for.
    """

    # A full base config, carrying the derived fields exactly as the app writes
    # them — and describing a DIFFERENT machine from the override below.
    BASE_40MM = {
        "stator_diameter": 40.0, "core_thickness": 3.0, "slot_height": 6.0,
        "air_gap": 0.3, "magnet_height": 5.0, "rotor_house_height": 1.0,
        "wire_width": 2.4, "wire_spacing_x": 0.1, "insulation_thickness": 0.05,
        "num_seg": 4, "num_slots_per_segment": 6, "num_poles_per_segment": 5,
        "num_slots": 24, "num_poles": 20,
        # derived, as stored
        "slot_width": 2.7, "stator_outer_radius": 20.0,
        "stator_inner_radius": 11.0, "rotor_outer_radius": 10.7,
        "rotor_inner_radius": 4.7, "angle_slot": 15.0, "angle_pole": 18.0,
        "slot_pitch": 0.2617993877991494, "pole_pitch": 0.3141592653589793,
    }
    # The 30 mm 12s14p machine, primaries only — what a geo= override looks like.
    OV_30MM = {
        "stator_diameter": 30.0, "core_thickness": 1.5, "slot_height": 4.3,
        "air_gap": 0.2, "magnet_height": 4.5, "rotor_house_height": 0.8,
        "wire_width": 2.0, "wire_spacing_x": 0.1, "insulation_thickness": 0.05,
        "num_seg": 2, "num_slots_per_segment": 6, "num_poles_per_segment": 7,
    }

    def test_slot_width_follows_the_override_not_the_config(self):
        from motor_ai_sim.simulation.geometry_2d import merge_geo_override
        g = merge_geo_override(self.BASE_40MM, self.OV_30MM)
        # 2.0 + 2*0.1 + 2*0.05, from the OVERRIDE's wire pitch — not the 2.7 the
        # base stored, and not the base's own 2.4-derived 2.7 either.
        assert g["slot_width"] == pytest.approx(2.3, abs=1e-12)

    def test_every_derived_field_follows_the_override(self):
        from motor_ai_sim.simulation.geometry_2d import merge_geo_override
        g = merge_geo_override(self.BASE_40MM, self.OV_30MM)
        assert g["stator_outer_radius"] == pytest.approx(15.0)
        assert g["stator_inner_radius"] == pytest.approx(9.2)
        assert g["rotor_outer_radius"] == pytest.approx(9.0)
        assert g["rotor_inner_radius"] == pytest.approx(3.7)
        assert (g["num_slots"], g["num_poles"]) == (12, 14)
        assert g["angle_slot"] == pytest.approx(30.0)
        assert g["angle_pole"] == pytest.approx(360.0 / 14)
        assert g["slot_pitch"] == pytest.approx(2 * np.pi / 12)
        assert g["pole_pitch"] == pytest.approx(2 * np.pi / 14)

    def test_result_is_independent_of_the_base_config(self):
        """THE acceptance property, in one cheap assertion.

        Two completely different saved configs, the same override → byte-identical
        geometry.  This is what makes a pinned physics run reproducible while the
        user edits their design in another tab.
        """
        from motor_ai_sim.simulation.geometry_2d import merge_geo_override
        other = dict(self.BASE_40MM, stator_diameter=200.0, wire_width=5.0,
                     slot_width=5.5, num_seg=6, num_slots_per_segment=6,
                     num_poles_per_segment=7, num_slots=36, num_poles=42,
                     stator_outer_radius=100.0, angle_slot=10.0)
        assert (merge_geo_override(self.BASE_40MM, self.OV_30MM)
                == merge_geo_override(other, self.OV_30MM))

    def test_stale_derived_in_the_base_is_refreshed_with_no_override(self):
        """No override at all is still a leak: the file's stored derived value
        can lag its own primaries (HEAD's config stored slot_width 2.5 next to a
        wire pitch of 2.3), and the solver meshed the config's OWN machine at the
        stale size."""
        from motor_ai_sim.simulation.geometry_2d import merge_geo_override
        base = dict(self.BASE_40MM, slot_width=2.5)     # 2.4 + 0.2 + 0.1 = 2.7
        assert merge_geo_override(base, None)["slot_width"] == pytest.approx(2.7)

    def test_absent_derived_fields_are_not_invented(self):
        """Refresh what the dict CARRIES; do not grow fields nobody asked for."""
        from motor_ai_sim.simulation.geometry_2d import merge_geo_override
        bare = {"stator_diameter": 30.0, "num_slots": 12, "num_poles": 14,
                "num_seg": 2, "num_slots_per_segment": 6,
                "num_poles_per_segment": 7}
        assert merge_geo_override(bare, None) == bare

    def test_cadquery_parameters_do_not_leak_the_config_slot_width(self):
        """The Mesh tab sizes its element from ``motor.parameters['slot_width']``
        so it can draw the mesh the solver builds; that dict is seeded from the
        shared config, and slot_width was the one derived field the mapping did
        not recompute."""
        from motor_ai_sim.cadquery_geometry import CadQueryMotor
        motor = CadQueryMotor()
        motor.set_parameters(dict(self.OV_30MM))
        assert motor.parameters["slot_width"] == pytest.approx(2.3, abs=1e-12)
        assert motor.parameters["stator_outer_radius"] == pytest.approx(15.0)
        assert (motor.parameters["num_slots"],
                motor.parameters["num_poles"]) == (12, 14)
