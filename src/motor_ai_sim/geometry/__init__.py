"""Geometry module for electric motor simulation.

This module provides:
- Material definitions and registry
- Parametric motor geometry parameters and regions
- Mesh generation from geometry regions

Architecture:
1. motor_geometry.py - Defines geometry parameters and regions (NO meshing)
2. motor_material.py - Defines magnetic materials
3. motor_mesh.py - Creates triangular meshes from geometry regions
"""

from motor_ai_sim.geometry.motor_material import (
    MagneticMaterial,
    MaterialRegistry,
    get_material_id,
)
from motor_ai_sim.geometry.motor_geometry import (
    MotorGeometryParams,
    MotorGeometry2D,
    GeometryRegion,
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
    # Geometry parameters and regions
    "MotorGeometryParams",
    "MotorGeometry2D",
    "GeometryRegion",
    # Mesh generation
    "MeshBuilder",
    "MotorMeshGenerator",
    "MaterialAssignment",
    "DEFAULT_MATERIAL_ASSIGNMENTS",
]
