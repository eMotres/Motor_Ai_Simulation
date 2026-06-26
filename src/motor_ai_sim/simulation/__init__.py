"""2D Magnetostatics simulation module (scikit-fem).

Pipeline:
    motor_config.yaml
        ↓  geometry_2d.py   → sub-domain definitions per material region
        ↓  pdes.py          → sympy PDE residuals  (∇·ν∇A = −J)
        ↓  fem_solver_2d.py → scikit-fem field solve
        ↓  postprocess.py   → B, H, torque from the A_z field
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
