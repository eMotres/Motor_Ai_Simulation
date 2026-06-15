"""Electric Motor AI Simulator.

Physics-Informed Neural Network for Electric Motor Simulation.
"""

__version__ = "0.1.0"
__author__ = "Motor AI Team"

# ── Block a broken optional native dependency (NVIDIA Warp) ────────────────────
# Warp's native warp.dll is missing/corrupt on some Windows machines and pops a
# BLOCKING "Bad Image" dialog the instant `import warp` runs — it cannot be caught in
# Python and stalls the backend + every FEM subprocess.  Warp is only pulled in
# transitively by the LEGACY physicsnemo/modulus CSG fallbacks; the active scikit-fem
# solver never uses it.  Pre-empt the import here (before any submodule) so those
# fallbacks raise a clean ImportError and drop to their stubs instead of hanging on
# the dialog.  Remove this line if/when a working Warp build is installed.
import sys as _sys
_sys.modules.setdefault("warp", None)

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

from motor_ai_sim import materials

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
    # Materials library
    "materials",
]
