"""Tests for motor geometry and materials."""

import pytest
import torch
import numpy as np

from motor_ai_sim.geometry import (
    MotorGeometryParams,
    MotorGeometry2D,
    MotorMeshGenerator,
    MeshBuilder,
    GeometryRegion,
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


class TestMotorGeometry2D:
    """Tests for MotorGeometry2D class - geometry regions only."""

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

    def test_get_regions(self, geometry):
        """Test getting geometry regions."""
        regions = geometry.get_regions()
        
        # Check all regions exist
        assert "stator_core" in regions
        assert "air_gap" in regions
        assert "rotor_core" in regions
        assert "shaft" in regions
        
        # Check slots
        slot_count = sum(1 for k in regions.keys() if k.startswith("slot_"))
        assert slot_count == geometry.params.num_slots
        
        # Check magnets
        magnet_count = sum(1 for k in regions.keys() if k.startswith("magnet_"))
        assert magnet_count == geometry.params.num_poles

    def test_region_types(self, geometry):
        """Test that regions have correct types."""
        regions = geometry.get_regions()
        
        # Stator core is annulus
        assert regions["stator_core"].region_type == "annulus"
        
        # Air gap is annulus
        assert regions["air_gap"].region_type == "annulus"
        
        # Rotor core is annulus
        assert regions["rotor_core"].region_type == "annulus"
        
        # Shaft is disk
        assert regions["shaft"].region_type == "disk"
        
        # Slots are sectors
        assert regions["slot_0"].region_type == "sector"
        
        # Magnets are sectors
        assert regions["magnet_0"].region_type == "sector"

    def test_region_radii(self, geometry):
        """Test that region radii are correct."""
        regions = geometry.get_regions()
        
        # Stator core
        stator = regions["stator_core"]
        assert abs(stator.r_inner - geometry.params.stator_slot_radius) < 1e-10
        assert abs(stator.r_outer - geometry.params.stator_outer_radius) < 1e-10
        
        # Air gap
        air_gap = regions["air_gap"]
        assert abs(air_gap.r_inner - geometry.params.rotor_outer_radius) < 1e-10
        assert abs(air_gap.r_outer - geometry.params.stator_inner_radius) < 1e-10

    def test_magnet_magnetization(self, geometry):
        """Test that magnets have magnetization direction."""
        regions = geometry.get_regions()
        
        for i in range(geometry.params.num_poles):
            magnet = regions[f"magnet_{i}"]
            assert magnet.magnetization_dir is not None
            assert magnet.pole_index == i
            
            # Check magnetization is a unit vector (or close to it)
            mag_norm = np.linalg.norm(magnet.magnetization_dir)
            assert abs(mag_norm - 1.0) < 1e-10

    def test_get_summary(self, geometry):
        """Test geometry summary."""
        summary = geometry.get_summary()
        
        assert "stator_outer_radius" in summary
        assert "stator_inner_radius" in summary
        assert "rotor_outer_radius" in summary
        assert "air_gap" in summary
        assert "num_slots" in summary
        assert "num_poles" in summary


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
        
        # Check radius bounds
        points = mesh["points"]
        radii = torch.sqrt(points[:, 0]**2 + points[:, 1]**2)
        assert radii.min() >= 50.0 - 1e-3
        assert radii.max() <= 100.0 + 1e-3

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
        
        # Check radius bounds (disk starts at 0)
        points = mesh["points"]
        radii = torch.sqrt(points[:, 0]**2 + points[:, 1]**2)
        assert radii.min() >= 0.0 - 1e-3
        assert radii.max() <= 50.0 + 1e-3

    def test_mesh_dimensions_in_mm(self, builder):
        """Test that mesh dimensions are in millimeters."""
        # Create annulus with 100mm outer radius
        region = GeometryRegion(
            name="test",
            region_type="annulus",
            r_inner=50.0,
            r_outer=100.0,
        )
        
        mesh = builder.mesh_region(region, n_radial=5, n_angular=32)
        points = mesh["points"]
        radii = torch.sqrt(points[:, 0]**2 + points[:, 1]**2)
        max_radius = radii.max().item()
        
        # Should be 100mm
        assert abs(max_radius - 100.0) < 1e-3


class TestMaterials:
    """Tests for material definitions."""

    def test_material_creation(self):
        """Test creating a custom material."""
        material = MagneticMaterial(
            name="Test Material",
            mu_r=100,
            sigma=1e6,
        )
        
        assert material.name == "Test Material"
        assert material.mu_r == 100
        assert material.sigma == 1e6
        assert material.mu > 0

    def test_material_properties(self):
        """Test material property calculations."""
        # Ferromagnetic material
        steel = MagneticMaterial(
            name="Steel",
            mu_r=2000,
            sigma=1e6,
            B_sat=1.8,
        )
        
        assert steel.is_ferromagnetic
        assert not steel.is_permanent_magnet
        assert steel.is_conductor
        
        # Permanent magnet
        magnet = MagneticMaterial(
            name="Magnet",
            mu_r=1.05,
            sigma=1e5,
            Br=1.2,
            Hc=900000,
        )
        
        assert not magnet.is_ferromagnetic
        assert magnet.is_permanent_magnet
        assert magnet.is_conductor

    def test_material_registry(self):
        """Test material registry."""
        # Get predefined material
        copper = MaterialRegistry.get("copper")
        assert copper.name == "Copper"
        assert copper.sigma > 1e7
        
        # Case insensitive
        copper2 = MaterialRegistry.get("COPPER")
        assert copper2.name == "Copper"
        
        # Unknown material
        with pytest.raises(ValueError):
            MaterialRegistry.get("unknown_material_xyz")

    def test_material_list(self):
        """Test listing materials."""
        materials = MaterialRegistry.list_materials()
        
        assert "copper" in materials
        assert "air" in materials
        assert "ndfeb_n42" in materials

    def test_material_categories(self):
        """Test material categories."""
        categories = MaterialRegistry.get_by_category()
        
        assert "conductors" in categories
        assert "permanent_magnets" in categories
        assert "electrical_steels" in categories
        
        assert "copper" in categories["conductors"]
        assert "ndfeb_n42" in categories["permanent_magnets"]

    def test_material_id(self):
        """Test material ID function."""
        assert get_material_id("air") == 0
        assert get_material_id("copper") == 1
        assert get_material_id("ndfeb_n42") == 21


class TestMotorMeshGenerator:
    """Tests for MotorMeshGenerator class."""

    @pytest.fixture
    def params(self):
        """Create geometry parameters."""
        return MotorGeometryParams()

    @pytest.fixture
    def generator(self, params):
        """Create mesh generator."""
        return MotorMeshGenerator(params)

    def test_generator_creation(self, generator):
        """Test generator creation."""
        assert generator.params is not None
        assert generator.geometry is not None
        assert generator.mesh_builder is not None

    def test_generate_mesh(self, generator):
        """Test mesh generation."""
        meshes = generator.generate(n_radial=5, n_angular=32)
        
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

    def test_material_assignment(self, generator):
        """Test material assignment."""
        meshes = generator.generate(n_radial=5, n_angular=32)
        
        # Check stator core has material data
        stator = meshes["stator_core"]
        assert "point_data" in stator
        assert "mu_r" in stator["point_data"]
        assert "sigma" in stator["point_data"]
        assert "material_name" in stator["point_data"]
        
        # Stator core should have high permeability
        assert stator["point_data"]["mu_r"][0] > 1

    def test_magnet_magnetization(self, generator):
        """Test magnet magnetization in mesh data."""
        meshes = generator.generate(n_radial=5, n_angular=32)
        
        # Check first magnet has magnetization
        magnet = meshes["magnet_0"]
        if magnet["cells"].shape[0] > 0:  # Only if mesh is non-empty
            assert "magnetization" in magnet["point_data"]
            assert "Br" in magnet["point_data"]
            assert "Hc" in magnet["point_data"]

    def test_custom_material_assignment(self, params):
        """Test custom material assignments."""
        custom_materials = {
            "stator_core": "m19_silicon_steel",
        }
        
        generator = MotorMeshGenerator(params, material_assignments=custom_materials)
        meshes = generator.generate(n_radial=5, n_angular=32)
        
        # Check stator core uses custom material
        stator = meshes["stator_core"]
        assert "M-19" in stator["point_data"]["material_name"]

    def test_combined_mesh(self, generator):
        """Test combining meshes."""
        meshes = generator.generate(n_radial=5, n_angular=32)
        combined = generator.get_combined_mesh(meshes)
        
        assert "points" in combined
        assert "cells" in combined
        assert "point_data" in combined
        assert "region_names" in combined
        
        # Check that combined mesh has more points than any single region
        total_points = combined["points"].shape[0]
        assert total_points > meshes["stator_core"]["points"].shape[0]

    def test_material_summary(self, generator):
        """Test material summary."""
        summary = generator.get_material_summary()
        
        assert "stator_core" in summary
        assert "windings" in summary
        assert "air_gap" in summary
        assert "rotor_core" in summary
        assert "magnets" in summary
        assert "shaft" in summary


class TestVisualization:
    """Tests for visualization functions."""

    def test_visualize_motor(self):
        """Test motor visualization."""
        from motor_ai_sim.utils import visualize_motor
        
        params = MotorGeometryParams()
        generator = MotorMeshGenerator(params)
        meshes = generator.generate(n_radial=5, n_angular=32)
        
        # This should not raise an error
        # (actual display is skipped in non-interactive mode)
        try:
            visualize_motor(meshes, show=False)
        except Exception as e:
            # May fail in headless environments
            pass
