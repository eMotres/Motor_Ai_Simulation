"""Mesh generator for electric motor cross-section.

This module provides:
1. MeshBuilder - Create triangular meshes from geometry regions
2. MotorMeshGenerator - Generate meshes with material properties

The mesh generator creates triangular meshes suitable for FEM simulation.
This is a legacy module - for PINN training, use MotorGeometry2D with
NVIDIA Modulus CSG primitives instead.

Units:
- All linear dimensions are in millimeters [mm]
- All angles are in degrees [deg]
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import torch

from motor_ai_sim.geometry.motor_material import (
    MagneticMaterial,
    MaterialRegistry,
    get_material_id,
)
from motor_ai_sim.geometry.motor_geometry import (
    MotorGeometryParams,
    GeometryRegion,
)


@dataclass
class MaterialAssignment:
    """Material assignment for a motor region.

    Attributes:
        region_pattern: Pattern to match region names (e.g., 'slot_*', 'magnet_0')
        material_name: Name of material to assign
    """

    region_pattern: str
    material_name: str


# Default material assignments for motor regions
DEFAULT_MATERIAL_ASSIGNMENTS = {
    "stator_core": "m27_silicon_steel",
    "slot": "copper",
    "air_gap": "air",
    "rotor_core": "m27_silicon_steel",
    "magnet": "ndfeb_n42",
    "shaft": "carbon_steel",
}


class MeshBuilder:
    """Create triangular meshes from geometry regions.

    This class handles the meshing of geometric regions without
    knowledge of materials or motor-specific logic.

    Example:
        >>> builder = MeshBuilder(device="cpu")
        >>> region = GeometryRegion(name="test", region_type="annulus",
        ...                         r_inner=50, r_outer=100)
        >>> mesh = builder.mesh_region(region, n_radial=10, n_angular=64)
        >>> print(mesh.keys())
        dict_keys(['points', 'cells', 'region'])
    """

    def __init__(self, device: torch.device | str = "cpu"):
        """Initialize mesh builder.

        Args:
            device: Torch device for tensor creation
        """
        self.device = torch.device(device) if isinstance(device, str) else device

    def mesh_region(
        self,
        region: GeometryRegion,
        n_radial: int = 10,
        n_angular: int = 64,
    ) -> Dict:
        """Create mesh for a single geometry region.

        Args:
            region: GeometryRegion to mesh
            n_radial: Number of radial divisions
            n_angular: Number of angular divisions

        Returns:
            Dictionary with 'points', 'cells', and 'region' keys
        """
        if region.region_type == "annulus":
            points, cells = self._create_annulus_mesh(
                region.r_inner, region.r_outer, n_radial, n_angular
            )
        elif region.region_type == "sector":
            points, cells = self._create_sector_mesh(
                region.r_inner, region.r_outer,
                region.theta_start, region.theta_end,
                n_radial, n_angular
            )
        elif region.region_type == "disk":
            points, cells = self._create_disk_mesh(
                region.r_outer, n_radial, n_angular
            )
        else:
            raise ValueError(f"Unknown region type: {region.region_type}")

        result = {
            "points": points,
            "cells": cells,
            "region": region.name,
        }

        # Add extra data for magnets
        if region.magnetization_dir is not None:
            result["magnetization_dir"] = region.magnetization_dir
            result["pole_index"] = region.pole_index

        return result

    def mesh_all_regions(
        self,
        regions: Dict[str, GeometryRegion],
        n_radial: int = 10,
        n_angular: int = 64,
        n_angular_slots: int = 8,
    ) -> Dict[str, Dict]:
        """Create meshes for all geometry regions.

        Args:
            regions: Dictionary of GeometryRegion objects
            n_radial: Number of radial divisions
            n_angular: Number of angular divisions (per full circle)
            n_angular_slots: Number of angular divisions per slot

        Returns:
            Dictionary mapping region names to mesh data
        """
        meshes = {}

        for region_name, region in regions.items():
            # Use fewer angular divisions for slots and magnets
            if region_name.startswith("slot_"):
                n_ang = n_angular_slots
            elif region_name.startswith("magnet_"):
                n_ang = max(2, n_angular // regions.get("num_poles", 
                         len([r for r in regions if r.startswith("magnet_")])))
            else:
                n_ang = n_angular

            meshes[region_name] = self.mesh_region(region, n_radial, n_ang)

        return meshes

    def _create_annulus_mesh(
        self,
        r_inner: float,
        r_outer: float,
        n_radial: int,
        n_angular: int,
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        """Create a triangular mesh for an annulus (ring).

        Args:
            r_inner: Inner radius [mm]
            r_outer: Outer radius [mm]
            n_radial: Number of radial divisions
            n_angular: Number of angular divisions

        Returns:
            Tuple of (points, cells) tensors
        """
        # Generate radial and angular coordinates
        r = torch.linspace(r_inner, r_outer, n_radial, device=self.device)
        theta = torch.linspace(0, 2 * np.pi, n_angular + 1, device=self.device)[:-1]

        # Create meshgrid
        r_grid, theta_grid = torch.meshgrid(r, theta, indexing="ij")

        # Convert to Cartesian
        x = r_grid * torch.cos(theta_grid)
        y = r_grid * torch.sin(theta_grid)
        points = torch.stack([x.flatten(), y.flatten()], dim=1)

        # Generate triangle cells
        cells = self._triangulate_annulus(n_radial, n_angular)

        return points, cells

    def _create_sector_mesh(
        self,
        r_inner: float,
        r_outer: float,
        theta_start: float,
        theta_end: float,
        n_radial: int,
        n_angular: int,
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        """Create a triangular mesh for an angular sector.

        Args:
            r_inner: Inner radius [mm]
            r_outer: Outer radius [mm]
            theta_start: Starting angle [rad]
            theta_end: Ending angle [rad]
            n_radial: Number of radial divisions
            n_angular: Number of angular divisions

        Returns:
            Tuple of (points, cells) tensors
        """
        # Generate radial and angular coordinates
        r = torch.linspace(r_inner, r_outer, n_radial, device=self.device)
        theta = torch.linspace(theta_start, theta_end, n_angular, device=self.device)

        # Create meshgrid
        r_grid, theta_grid = torch.meshgrid(r, theta, indexing="ij")

        # Convert to Cartesian
        x = r_grid * torch.cos(theta_grid)
        y = r_grid * torch.sin(theta_grid)
        points = torch.stack([x.flatten(), y.flatten()], dim=1)

        # Generate triangle cells
        cells = self._triangulate_grid(n_radial, n_angular)

        return points, cells

    def _create_disk_mesh(
        self,
        radius: float,
        n_radial: int,
        n_angular: int,
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        """Create a triangular mesh for a disk (filled circle).

        Args:
            radius: Disk radius [mm]
            n_radial: Number of radial divisions
            n_angular: Number of angular divisions

        Returns:
            Tuple of (points, cells) tensors
        """
        # Generate radial and angular coordinates
        r = torch.linspace(0, radius, n_radial, device=self.device)
        theta = torch.linspace(0, 2 * np.pi, n_angular + 1, device=self.device)[:-1]

        # Create meshgrid
        r_grid, theta_grid = torch.meshgrid(r, theta, indexing="ij")

        # Convert to Cartesian
        x = r_grid * torch.cos(theta_grid)
        y = r_grid * torch.sin(theta_grid)
        points = torch.stack([x.flatten(), y.flatten()], dim=1)

        # Generate triangle cells (special handling for center)
        cells = self._triangulate_disk(n_radial, n_angular)

        return points, cells

    def _triangulate_annulus(self, n_radial: int, n_angular: int) -> torch.Tensor:
        """Generate triangle connectivity for an annulus mesh."""
        cells = []
        for i in range(n_radial - 1):
            for j in range(n_angular):
                j_next = (j + 1) % n_angular

                # Vertex indices
                v0 = i * n_angular + j
                v1 = i * n_angular + j_next
                v2 = (i + 1) * n_angular + j
                v3 = (i + 1) * n_angular + j_next

                # Two triangles per quad
                cells.append([v0, v1, v2])
                cells.append([v1, v3, v2])

        return torch.tensor(cells, dtype=torch.long, device=self.device)

    def _triangulate_grid(self, n_radial: int, n_angular: int) -> torch.Tensor:
        """Generate triangle connectivity for a regular grid mesh."""
        cells = []
        for i in range(n_radial - 1):
            for j in range(n_angular - 1):
                # Vertex indices
                v0 = i * n_angular + j
                v1 = i * n_angular + (j + 1)
                v2 = (i + 1) * n_angular + j
                v3 = (i + 1) * n_angular + (j + 1)

                # Two triangles per quad
                cells.append([v0, v1, v2])
                cells.append([v1, v3, v2])

        return torch.tensor(cells, dtype=torch.long, device=self.device)

    def _triangulate_disk(self, n_radial: int, n_angular: int) -> torch.Tensor:
        """Generate triangle connectivity for a disk mesh.

        Special handling for the center point (first ring).
        """
        cells = []

        # First ring (connect center to first ring)
        for j in range(n_angular):
            j_next = (j + 1) % n_angular
            v_center = j  # Points on innermost ring (r=0)
            v1 = n_angular + j  # First ring
            v2 = n_angular + j_next
            cells.append([v_center, v2, v1])

        # Remaining rings
        for i in range(1, n_radial - 1):
            for j in range(n_angular):
                j_next = (j + 1) % n_angular

                v0 = i * n_angular + j
                v1 = i * n_angular + j_next
                v2 = (i + 1) * n_angular + j
                v3 = (i + 1) * n_angular + j_next

                cells.append([v0, v1, v2])
                cells.append([v1, v3, v2])

        return torch.tensor(cells, dtype=torch.long, device=self.device)


class MotorMeshGenerator:
    """Generate motor mesh with material properties.

    This class:
    1. Creates geometry regions from MotorGeometryParams
    2. Creates meshes using MeshBuilder
    3. Assigns materials to each region

    This is a legacy class for FEM-style mesh generation.
    For PINN training, use MotorGeometry2D with NVIDIA Modulus CSG primitives.

    Example:
        >>> params = MotorGeometryParams(num_slots=12, num_poles=4)
        >>> generator = MotorMeshGenerator(params)
        >>> meshes = generator.generate()
        >>> print(meshes['stator_core']['point_data'].keys())
        dict_keys(['mu_r', 'sigma', 'material_id', 'material_name'])
    """

    def __init__(
        self,
        params: MotorGeometryParams,
        material_assignments: Optional[Dict[str, str]] = None,
        device: torch.device | str = "cpu",
    ):
        """Initialize mesh generator.

        Args:
            params: Motor geometry parameters
            material_assignments: Custom material assignments (optional)
            device: Torch device for tensor creation
        """
        self.params = params
        self.mesh_builder = MeshBuilder(device)
        self.device = torch.device(device) if isinstance(device, str) else device

        # Merge default and custom material assignments
        self.material_assignments = {**DEFAULT_MATERIAL_ASSIGNMENTS}
        if material_assignments:
            self.material_assignments.update(material_assignments)

    def _get_regions(self) -> Dict[str, GeometryRegion]:
        """Create geometry regions from parameters.

        This is an internal method that creates GeometryRegion objects
        directly from MotorGeometryParams without requiring Modulus.

        Returns:
            Dictionary mapping region names to GeometryRegion objects
        """
        regions = {}
        params = self.params

        # 1. Stator core (annulus)
        regions["stator_core"] = GeometryRegion(
            name="stator_core",
            region_type="annulus",
            r_inner=params.stator_slot_radius,
            r_outer=params.stator_outer_radius,
        )

        # 2. Stator slots (sectors)
        for i in range(params.num_slots):
            regions[f"slot_{i}"] = self._get_slot_region(i)

        # 3. Air gap (annulus)
        regions["air_gap"] = GeometryRegion(
            name="air_gap",
            region_type="annulus",
            r_inner=params.rotor_outer_radius,
            r_outer=params.stator_inner_radius,
        )

        # 4. Rotor core (annulus)
        regions["rotor_core"] = GeometryRegion(
            name="rotor_core",
            region_type="annulus",
            r_inner=params.shaft_radius,
            r_outer=params.rotor_core_radius,
        )

        # 5. Permanent magnets (sectors)
        for i in range(params.num_poles):
            regions[f"magnet_{i}"] = self._get_magnet_region(i)

        # 6. Shaft (disk)
        regions["shaft"] = GeometryRegion(
            name="shaft",
            region_type="disk",
            r_inner=0.0,
            r_outer=params.shaft_radius,
        )

        return regions

    def _get_slot_region(self, slot_idx: int) -> GeometryRegion:
        """Get geometry region for a stator slot."""
        theta_center = slot_idx * self.params.slot_pitch
        half_width_rad = MotorGeometryParams.deg_to_rad(self.params.angle_slot) / 2
        theta_start = theta_center - half_width_rad
        theta_end = theta_center + half_width_rad

        return GeometryRegion(
            name=f"slot_{slot_idx}",
            region_type="sector",
            r_inner=self.params.stator_inner_radius,
            r_outer=self.params.stator_slot_radius,
            theta_start=theta_start,
            theta_end=theta_end,
        )

    def _get_magnet_region(self, pole_idx: int) -> GeometryRegion:
        """Get geometry region for a permanent magnet."""
        theta_center = pole_idx * self.params.pole_pitch
        half_width_rad = MotorGeometryParams.deg_to_rad(self.params.angle_pole) / 2
        theta_start = theta_center - half_width_rad
        theta_end = theta_center + half_width_rad

        # Magnetization direction (alternating)
        sign = 1.0 if pole_idx % 2 == 0 else -1.0
        magnetization_dir = sign * np.array([
            np.cos(theta_center),
            np.sin(theta_center),
        ])

        return GeometryRegion(
            name=f"magnet_{pole_idx}",
            region_type="sector",
            r_inner=self.params.rotor_core_radius,
            r_outer=self.params.rotor_outer_radius,
            theta_start=theta_start,
            theta_end=theta_end,
            magnetization_dir=magnetization_dir,
            pole_index=pole_idx,
        )

    def generate(
        self,
        n_radial: int = 10,
        n_angular: int = 64,
        n_angular_slots: int = 8,
    ) -> Dict[str, Dict]:
        """Generate mesh with material properties.

        Args:
            n_radial: Number of radial divisions
            n_angular: Number of angular divisions
            n_angular_slots: Number of angular divisions per slot

        Returns:
            Dictionary mapping region names to mesh data with materials
        """
        # Step 1: Get geometry regions (suppress deprecation warning)
        import warnings
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=DeprecationWarning)
            regions = self._get_regions()

        # Step 2: Create meshes from regions
        meshes = self.mesh_builder.mesh_all_regions(
            regions, n_radial, n_angular, n_angular_slots
        )

        # Step 3: Add material properties to each region
        for region_name, mesh_data in meshes.items():
            material = self._get_material_for_region(region_name)
            self._add_material_data(mesh_data, material, region_name)

        return meshes

    def _get_material_for_region(self, region_name: str) -> MagneticMaterial:
        """Get material for a given region name.

        Args:
            region_name: Name of the region (e.g., 'stator_core', 'slot_0')

        Returns:
            MagneticMaterial instance
        """
        # Check for exact match first
        if region_name in self.material_assignments:
            material_name = self.material_assignments[region_name]
            return MaterialRegistry.get(material_name)

        # Check for pattern match (e.g., 'slot_0' matches 'slot')
        for pattern, material_name in self.material_assignments.items():
            if region_name.startswith(pattern):
                return MaterialRegistry.get(material_name)

        # Default to air
        return MaterialRegistry.get("air")

    def _add_material_data(
        self,
        mesh_data: Dict,
        material: MagneticMaterial,
        region_name: str,
    ) -> None:
        """Add material properties to mesh data.

        Args:
            mesh_data: Mesh dictionary to modify
            material: Material to assign
            region_name: Name of the region
        """
        n_points = mesh_data["points"].shape[0]

        # Initialize point_data if not present
        if "point_data" not in mesh_data:
            mesh_data["point_data"] = {}

        point_data = mesh_data["point_data"]

        # Add scalar material properties
        point_data["mu_r"] = torch.full(
            (n_points,),
            material.mu_r,
            dtype=torch.float32,
            device=self.device,
        )

        point_data["sigma"] = torch.full(
            (n_points,),
            material.sigma,
            dtype=torch.float32,
            device=self.device,
        )

        point_data["material_id"] = torch.full(
            (n_points,),
            get_material_id(material.name),
            dtype=torch.long,
            device=self.device,
        )

        # Add material name as string
        point_data["material_name"] = material.name

        # Add saturation flux density if available
        if material.B_sat is not None:
            point_data["B_sat"] = torch.full(
                (n_points,),
                material.B_sat,
                dtype=torch.float32,
                device=self.device,
            )

        # Add permanent magnet properties
        if material.is_permanent_magnet:
            # Get magnetization direction from geometry
            if "magnetization_dir" in mesh_data:
                mag_dir = mesh_data["magnetization_dir"]
                M_mag = float(material.get_magnetization())

                # Create magnetization vector for each point
                magnetization = torch.zeros((n_points, 2), device=self.device)
                magnetization[:, 0] = M_mag * mag_dir[0]
                magnetization[:, 1] = M_mag * mag_dir[1]
                point_data["magnetization"] = magnetization

            # Add remanence and coercivity
            point_data["Br"] = torch.full(
                (n_points,),
                material.Br,
                dtype=torch.float32,
                device=self.device,
            )
            point_data["Hc"] = torch.full(
                (n_points,),
                material.Hc,
                dtype=torch.float32,
                device=self.device,
            )

        # Add color for visualization
        point_data["color"] = material.color

    def get_combined_mesh(self, meshes: Optional[Dict[str, Dict]] = None) -> Dict:
        """Combine all region meshes into a single mesh.

        This is useful for visualization and some simulation methods.

        Args:
            meshes: Mesh dictionary (if None, generates new)

        Returns:
            Combined mesh dictionary
        """
        if meshes is None:
            meshes = self.generate()

        all_points = []
        all_cells = []
        all_mu_r = []
        all_sigma = []
        all_material_id = []
        all_region_id = []
        all_colors = []

        point_offset = 0
        region_id = 0

        # Region name to ID mapping
        region_names = []

        for region_name, mesh_data in meshes.items():
            points = mesh_data["points"]
            cells = mesh_data["cells"]
            point_data = mesh_data["point_data"]

            n_points = points.shape[0]

            # Skip empty regions
            if n_points == 0 or cells.shape[0] == 0:
                continue

            # Append points
            all_points.append(points)

            # Offset cell indices
            offset_cells = cells + point_offset
            all_cells.append(offset_cells)
            point_offset += n_points

            # Append point data
            all_mu_r.append(point_data["mu_r"])
            all_sigma.append(point_data["sigma"])
            all_material_id.append(point_data["material_id"])
            all_region_id.append(torch.full((n_points,), region_id, dtype=torch.long, device=self.device))
            all_colors.append(point_data["color"])

            region_names.append(region_name)
            region_id += 1

        # Concatenate all
        combined = {
            "points": torch.cat(all_points, dim=0),
            "cells": torch.cat(all_cells, dim=0),
            "point_data": {
                "mu_r": torch.cat(all_mu_r, dim=0),
                "sigma": torch.cat(all_sigma, dim=0),
                "material_id": torch.cat(all_material_id, dim=0),
                "region_id": torch.cat(all_region_id, dim=0),
            },
            "region_names": region_names,
        }

        return combined

    def get_material_summary(self) -> Dict[str, Dict]:
        """Get summary of materials used in the motor.

        Returns:
            Dictionary mapping region names to material properties
        """
        summary = {}

        # Stator core
        mat = self._get_material_for_region("stator_core")
        summary["stator_core"] = {
            "material": mat.name,
            "mu_r": mat.mu_r,
            "sigma": mat.sigma,
            "B_sat": mat.B_sat,
        }

        # Slots (windings)
        mat = self._get_material_for_region("slot_0")
        summary["windings"] = {
            "material": mat.name,
            "mu_r": mat.mu_r,
            "sigma": mat.sigma,
        }

        # Air gap
        mat = self._get_material_for_region("air_gap")
        summary["air_gap"] = {
            "material": mat.name,
            "mu_r": mat.mu_r,
        }

        # Rotor core
        mat = self._get_material_for_region("rotor_core")
        summary["rotor_core"] = {
            "material": mat.name,
            "mu_r": mat.mu_r,
            "sigma": mat.sigma,
            "B_sat": mat.B_sat,
        }

        # Magnets
        mat = self._get_material_for_region("magnet_0")
        summary["magnets"] = {
            "material": mat.name,
            "mu_r": mat.mu_r,
            "Br": mat.Br,
            "Hc": mat.Hc,
        }

        # Shaft
        mat = self._get_material_for_region("shaft")
        summary["shaft"] = {
            "material": mat.name,
            "mu_r": mat.mu_r,
            "sigma": mat.sigma,
        }

        return summary

    def print_summary(self) -> None:
        """Print a summary of the motor configuration."""
        print("=" * 60)
        print("Electric Motor Configuration Summary")
        print("=" * 60)

        # Geometry (all dimensions are in mm)
        print("\nGeometry:")
        print(f"  Stator outer radius: {self.params.stator_outer_radius:.1f} mm")
        print(f"  Stator inner radius: {self.params.stator_inner_radius:.1f} mm")
        print(f"  Rotor outer radius:  {self.params.rotor_outer_radius:.1f} mm")
        print(f"  Air gap:             {self.params.air_gap:.2f} mm")
        print(f"  Number of slots:     {self.params.num_slots}")
        print(f"  Number of poles:     {self.params.num_poles}")

        # Materials
        print("\nMaterials:")
        summary = self.get_material_summary()

        for region, props in summary.items():
            print(f"\n  {region}:")
            print(f"    Material: {props['material']}")
            print(f"    mu_r = {props['mu_r']:.1f}")
            if "sigma" in props:
                print(f"    sigma = {props['sigma']:.2e} S/m")
            if "B_sat" in props:
                print(f"    B_sat = {props['B_sat']:.2f} T")
            if "Br" in props:
                print(f"    Br = {props['Br']:.2f} T")
            if "Hc" in props:
                print(f"    Hc = {props['Hc']:.0f} A/m")

        print("\n" + "=" * 60)
