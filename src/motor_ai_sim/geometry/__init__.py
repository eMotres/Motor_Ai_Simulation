"""Geometry module for electric motor simulation.

This module provides:
- Material definitions and registry
- Parametric motor geometry parameters and Modulus CSG geometries
- Mesh generation from geometry regions (legacy)

Architecture:
1. motor_geometry.py - Defines geometry parameters and Modulus CSG geometries
2. motor_material.py - Defines magnetic materials
3. motor_mesh.py - Creates triangular meshes (legacy, for backward compatibility)

New API (recommended):
    from motor_ai_sim.geometry import MotorGeometryParams, MotorGeometry2D
    
    params = MotorGeometryParams.from_yaml("config/motor_config.yaml")
    geometry = MotorGeometry2D(params)
    geometries = geometry.get_modulus_geometries()
    
    # Sample points for PINN training
    stator_points = geometries['stator_core'].sample_interior(1000)

Legacy API (deprecated):
    regions = geometry.get_regions()  # Returns GeometryRegion objects
"""

from motor_ai_sim.geometry.motor_material import (
    MagneticMaterial,
    MaterialRegistry,
    get_material_id,
)
from motor_ai_sim.geometry.motor_geometry import (
    MotorGeometryParams,
    MotorGeometry2D,
    GeometryRegion,  # Deprecated, kept for backward compatibility
    HAS_MODULUS,  # Flag indicating if Modulus is available
)
from motor_ai_sim.geometry.motor_mesh import (
    MeshBuilder,
    MotorMeshGenerator,
    MaterialAssignment,
    DEFAULT_MATERIAL_ASSIGNMENTS,
)

__all__ = [
    # Materials
    "MagneticMaterial",
    "MaterialRegistry",
    "get_material_id",
    # Geometry parameters and Modulus CSG geometries
    "MotorGeometryParams",
    "MotorGeometry2D",
    "GeometryRegion",  # Deprecated
    "HAS_MODULUS",
    # Mesh generation (legacy)
    "MeshBuilder",
    "MotorMeshGenerator",
    "MaterialAssignment",
    "DEFAULT_MATERIAL_ASSIGNMENTS",
]
