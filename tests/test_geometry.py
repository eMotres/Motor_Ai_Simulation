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
