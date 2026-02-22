"""Tests for motor geometry and materials."""

import pytest
import torch
import numpy as np

from motor_ai_sim.geometry import (
    MotorGeometryParams,
    MotorGeometry2D,
    MotorMeshGenerator,
    MeshBuilder,
    GeometryRegion,  # Deprecated but still available
    MagneticMaterial,
    MaterialRegistry,
    get_material_id,
)

# Check if Modulus is available
try:
    from modulus.geometry.primitives_2d import Circle, Rectangle
    HAS_MODULUS = True
except ImportError:
    try:
        from physicsnemo.geometry.primitives_2d import Circle, Rectangle
        HAS_MODULUS = True
    except ImportError:
        HAS_MODULUS = False


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


@pytest.mark.skipif(not HAS_MODULUS, reason="NVIDIA Modulus not installed")
class TestMotorGeometry2DModulus:
    """Tests for MotorGeometry2D class with Modulus CSG primitives."""

    @pytest.fixture
    def params(self):
        """Create default geometry parameters."""
        return MotorGeometryParams()

    @pytest.fixture
    def geometry(self, params):
        """Create geometry generator."""
        return MotorGeometry2D(params)

    def test_create_geometry(self, geometry):
        """Test geometry creation."""
        assert geometry.params is not None

    def test_get_modulus_geometries(self, geometry):
        """Test getting Modulus CSG geometry objects."""
        geometries = geometry.get_modulus_geometries()
        
        # Check all regions exist
        assert "stator_core" in geometries
        assert "air_gap" in geometries
        assert "rotor_core" in geometries
        assert "shaft" in geometries
        assert "magnets" in geometries
        assert "slots" in geometries

    def test_geometry_is_csg_object(self, geometry):
        """Test that returned geometries are actual CSG objects."""
        geometries = geometry.get_modulus_geometries()
        
        # Shaft should be a Circle
        from modulus.geometry.primitives_2d import Circle
        assert isinstance(geometries['shaft'], Circle)

    def test_get_magnetization_directions(self, geometry):
        """Test that magnetization directions are computed correctly."""
        directions = geometry.get_magnetization_directions()
        
        # Should have one direction per pole
        assert len(directions) == geometry.params.num_poles
        
        # Each direction should be a unit vector
        for pole_idx, mag_dir in directions.items():
            mag_norm = np.linalg.norm(mag_dir)
            assert abs(mag_norm - 1.0) < 1e-10
            
            # Alternating signs
            expected_sign = 1.0 if pole_idx % 2 == 0 else -1.0
            theta_center = pole_idx * geometry.params.pole_pitch
            expected_dir = expected_sign * np.array([
                np.cos(theta_center),
                np.sin(theta_center),
            ])
            assert np.allclose(mag_dir, expected_dir)

    def test_get_individual_slots(self, geometry):
        """Test getting individual slot geometries."""
        slots = geometry.get_individual_slot_geometries()
        
        # Should have one geometry per slot
        assert len(slots) == geometry.params.num_slots

    def test_get_individual_magnets(self, geometry):
        """Test getting individual magnet geometries."""
        magnets = geometry.get_individual_magnet_geometries()
        
        # Should have one geometry per pole
        assert len(magnets) == geometry.params.num_poles

    def test_get_summary(self, geometry):
        """Test geometry summary."""
        summary = geometry.get_summary()
        
        assert "stator_outer_radius" in summary
        assert "stator_inner_radius" in summary
        assert "rotor_outer_radius" in summary
        assert "air_gap" in summary
        assert "num_slots" in summary
        assert "num_poles" in summary


class TestMotorGeometry2DWithoutModulus:
    """Tests for MotorGeometry2D when Modulus is not available."""

    def test_import_error_without_modulus(self, monkeypatch):
        """Test that ImportError is raised when Modulus is not available."""
        # This test only runs when Modulus is not installed
        if HAS_MODULUS:
            pytest.skip("Modulus is installed, skipping test for missing Modulus")
        
        params = MotorGeometryParams()
        
        with pytest.raises(ImportError, match="NVIDIA Modulus is required"):
            MotorGeometry2D(params)


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


class TestMeshBuilder:
    """Tests for MeshBuilder class."""

    @pytest.fixture
    def builder(self):
        """Create mesh builder."""
        return MeshBuilder(device="cpu")

    def test_mesh_annulus(self, builder):
        """Test meshing an annulus region."""
        region = GeometryRegion(
            name="test_annulus",
            region_type="annulus",
            r_inner=50.0,
            r_outer=100.0,
        )
        
        mesh = builder.mesh_region(region, n_radial=5, n_angular=32)
        
        assert "points" in mesh
        assert "cells" in mesh
        assert mesh["region"] == "test_annulus"
        
        # Check radius bounds
        points = mesh["points"]
        radii = torch.sqrt(points[:, 0]**2 + points[:, 1]**2)
        assert radii.min() >= 50.0 - 1e-3
        assert radii.max() <= 100.0 + 1e-3

    def test_mesh_sector(self, builder):
        """Test meshing a sector region."""
        region = GeometryRegion(
            name="test_sector",
            region_type="sector",
            r_inner=50.0,
            r_outer=100.0,
            theta_start=0.0,
            theta_end=np.pi / 4,
        )
        
        mesh = builder.mesh_region(region, n_radial=5, n_angular=8)
        
        assert "points" in mesh
        assert "cells" in mesh
        assert mesh["region"] == "test_sector"
        
        # Check angular bounds
        points = mesh["points"]
        angles = torch.atan2(points[:, 1], points[:, 0])
        assert angles.min() >= 0.0 - 1e-3
        assert angles.max() <= np.pi / 4 + 1e-3

    def test_mesh_disk(self, builder):
        """Test meshing a disk region."""
        region = GeometryRegion(
            name="test_disk",
            region_type="disk",
            r_outer=50.0,
        )
        
        mesh = builder.mesh_region(region, n_radial=5, n_angular=32)
        
        assert "points" in mesh
        assert "cells" in mesh
        assert mesh["region"] == "test_disk"
        
        # Check radius bounds (disk includes center)
        points = mesh["points"]
        radii = torch.sqrt(points[:, 0]**2 + points[:, 1]**2)
        assert radii.min() >= 0.0 - 1e-3
        assert radii.max() <= 50.0 + 1e-3


class TestMotorMeshGenerator:
    """Tests for MotorMeshGenerator class."""

    @pytest.fixture
    def params(self):
        """Create geometry parameters."""
        return MotorGeometryParams(
            num_seg=3,
            num_slots_per_segment=4,
            num_poles_per_segment=4,
        )

    @pytest.fixture
    def generator(self, params):
        """Create mesh generator."""
        return MotorMeshGenerator(params)

    def test_generate_mesh(self, generator):
        """Test mesh generation with materials."""
        meshes = generator.generate(n_radial=3, n_angular=16, n_angular_slots=4)
        
        # Check all regions exist
        assert "stator_core" in meshes
        assert "air_gap" in meshes
        assert "rotor_core" in meshes
        assert "shaft" in meshes
        
        # Check slots
        slot_count = sum(1 for k in meshes.keys() if k.startswith("slot_"))
        assert slot_count == generator.params.num_slots
        
        # Check magnets
        magnet_count = sum(1 for k in meshes.keys() if k.startswith("magnet_"))
        assert magnet_count == generator.params.num_poles

    def test_material_properties(self, generator):
        """Test that material properties are assigned."""
        meshes = generator.generate(n_radial=3, n_angular=16, n_angular_slots=4)
        
        # Check stator core has material properties
        stator = meshes["stator_core"]
        assert "point_data" in stator
        assert "mu_r" in stator["point_data"]
        assert "sigma" in stator["point_data"]
        assert "material_id" in stator["point_data"]
        assert "material_name" in stator["point_data"]
        
        # Check material name (human-readable name)
        assert "silicon" in stator["point_data"]["material_name"].lower() or "steel" in stator["point_data"]["material_name"].lower()

    def test_magnet_magnetization(self, generator):
        """Test that magnets have magnetization vectors."""
        meshes = generator.generate(n_radial=3, n_angular=16, n_angular_slots=4)
        
        for i in range(generator.params.num_poles):
            magnet = meshes[f"magnet_{i}"]
            assert "point_data" in magnet
            assert "magnetization" in magnet["point_data"]
            
            # Magnetization should be a 2D vector
            mag = magnet["point_data"]["magnetization"]
            assert mag.shape[1] == 2  # 2D vectors


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
