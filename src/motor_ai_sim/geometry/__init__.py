"""Geometry module for electric motor simulation.

This module provides:
- Material definitions and registry
- Parametric motor geometry parameters

Architecture:
1. motor_geometry.py - Defines geometry parameters
2. motor_material.py - Defines magnetic materials

Usage:
    from motor_ai_sim.geometry import MotorGeometryParams

    params = MotorGeometryParams.from_yaml("config/motor_config.yaml")
"""

from motor_ai_sim.geometry.motor_geometry import (
    MotorGeometryParams,
    GeometryRegion,  # Deprecated, kept for backward compatibility
    HAS_MODULUS,  # Always False — NVIDIA Modulus path removed
)

from motor_ai_sim.geometry.motor_material import (
    MagneticMaterial,
    MaterialRegistry,
    get_material_id,
)

__all__ = [
    # Materials
    "MagneticMaterial",
    "MaterialRegistry",
    "get_material_id",
    # Geometry parameters
    "MotorGeometryParams",
    "GeometryRegion",  # Deprecated
    "HAS_MODULUS",
]
