"""Capture the Picard iron-saturation log for sector vs full at open circuit.

The solver logs 'FEM iter %d: tag=%d B_p90=%.2fT mu_r ...' each iteration.  If at
iter 0 the rotor/stator B_p90 is already ~10x weaker in the full disk, the field
is broken from the linear solve (source/BC), not from saturation convergence."""
import logging, numpy as np
logging.basicConfig(level=logging.INFO, format="%(message)s")
# silence gmsh/other noise by raising their levels
for noisy in ("gmsh",):
    logging.getLogger(noisy).setLevel(logging.WARNING)

import motor_ai_sim.config as C
import motor_ai_sim.simulation.fem_solver_2d as fs

for ns in (4, 1):
    print("\n########## n_sectors=%d ##########" % ns, flush=True)
    fs.fem_solve_for_sim(rotor_angle_deg=0.0, gamma_deg=0.0,
                         mesh_size_mm=4.0, n_sectors=ns, I_phase_rms=0.0)
