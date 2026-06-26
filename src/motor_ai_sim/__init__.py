"""Electric Motor AI Simulator.

2-D magnetostatics FEM (scikit-fem) for electric motor simulation.
"""

__version__ = "0.1.0"
__author__ = "Motor AI Team"

from motor_ai_sim.geometry import (
    MotorGeometryParams,
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

from motor_ai_sim import materials

__all__ = [
    # Geometry
    "MotorGeometryParams",
    "MagneticMaterial",
    "MaterialRegistry",
    # Config
    "get_config",
    "get_geometry_params",
    "get_mesh_params",
    "get_material_assignments",
    "get_simulation_params",
    "clear_config_cache",
    # Materials library
    "materials",
]
