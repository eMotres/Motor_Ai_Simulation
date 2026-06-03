"""Motor design optimization (analytical surrogate + Pareto search).

This package is deliberately ISOLATED from the FEM/simulation stack: it never
reads or writes the global motor_config.yaml, never touches the simulation
caches, and imports nothing from routes.*.  Every candidate design is built and
evaluated purely in-memory, so running an optimization cannot disturb the
Simulation tab's state.
"""

from .design_eval import evaluate_design, DesignMetrics  # noqa: F401
from .optimizer import run_pareto_search                  # noqa: F401
