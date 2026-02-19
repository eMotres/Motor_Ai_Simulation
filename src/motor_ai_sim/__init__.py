"""Electric Motor AI Simulator.

Physics-Informed Neural Network for Electric Motor Simulation.
"""

__version__ = "0.1.0"
__author__ = "Motor AI Team"

from motor_ai_sim.geometry import (
    MotorGeometryParams,
    MotorGeometry2D,
    MotorMeshGenerator,
    MagneticMaterial,
    MaterialRegistry,
)

from motor_ai_sim.config import (
    get_config,
    get_geometry_params,
    get_mesh_params,
    get_material_assignments,
    get_simulation_params,
    clear_config_cache,
)

__all__ = [
    # Geometry
    "MotorGeometryParams",
    "MotorGeometry2D",
    "MotorMeshGenerator",
    "MagneticMaterial",
    "MaterialRegistry",
    # Config
    "get_config",
    "get_geometry_params",
    "get_mesh_params",
    "get_material_assignments",
    "get_simulation_params",
    "clear_config_cache",
]
