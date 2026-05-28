"""2D Magnetostatics simulation module using NVIDIA Modulus (PINN).

Pipeline:
    motor_config.yaml
        ↓  geometry_2d.py   → CSG domains per material region
        ↓  pdes.py          → PDE nodes  (∇·ν∇A = −J)
        ↓  solver_2d.py     → Modulus Solver / training loop
        ↓  postprocess.py   → B, H, torque from trained A_z network
"""

from motor_ai_sim.simulation.pdes import (
    Magnetostatics2D,
    MagnetosticsNonlinear2D,
)
from motor_ai_sim.simulation.postprocess import (
    compute_flux_density,
    compute_torque_maxwell,
)

__all__ = [
    "Magnetostatics2D",
    "MagnetosticsNonlinear2D",
    "compute_flux_density",
    "compute_torque_maxwell",
]
