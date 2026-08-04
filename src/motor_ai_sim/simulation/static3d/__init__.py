"""Stage-0 spike: 3D magnetostatics on tetrahedra for motor END EFFECTS.

Scope: magnet-driven (current-free) fields, total magnetic scalar potential,
scikit-fem on ``MeshTet``, gmsh/OCC meshing, linear and B-H-nonlinear iron.

The whole package exists to answer four questions before any motor geometry is
extruded, and to answer them against EXACT solutions rather than against
another code's opinion:

  * does the scalar-potential formulation give the right B, and how fast does
    it converge on P1 vs P2 (``exact.sphere_B``),
  * does it capture flux spilling out of a magnet's end faces
    (``exact.cylinder_axis_Bz``),
  * how far out does the air-box truncation have to sit,
  * does a Picard loop on a real B-H curve close in 3D.

Read ``solver.py``'s module docstring for the formulation and why it was
chosen over a vector potential.  ``benchmarks.py`` is runnable:

    python -m motor_ai_sim.simulation.static3d.benchmarks --all

This package is self-contained: it imports the 2D materials/B-H helpers
READ-ONLY and modifies nothing in the 2D pipeline.
"""
from motor_ai_sim.simulation.static3d.exact import (
    MU0, cylinder_axis_Bz, cylinder_end_to_mid_ratio, sphere_B,
    sphere_B_inside, sphere_in_shell_B, sphere_in_shell_B_inside,
    thick_solenoid_axis_Bz,
)
from motor_ai_sim.simulation.static3d.meshes import (
    TaggedTetMesh, cylinder_in_box, cylinder_with_iron_ring, sphere_in_box,
    sphere_with_iron_shell, tube_in_box,
)
from motor_ai_sim.simulation.static3d.nedelec import (
    ASolution, azimuthal_J, solve_static3d_A, solve_static3d_A_nonlinear,
)
from motor_ai_sim.simulation.static3d.solver import (
    Region, Solution, solve_static3d, solve_static3d_nonlinear,
)

__all__ = [
    "ASolution", "MU0", "Region", "Solution", "TaggedTetMesh",
    "azimuthal_J", "cylinder_axis_Bz", "cylinder_end_to_mid_ratio",
    "cylinder_in_box", "cylinder_with_iron_ring", "solve_static3d",
    "solve_static3d_A", "solve_static3d_A_nonlinear",
    "solve_static3d_nonlinear", "sphere_B", "sphere_B_inside", "sphere_in_box",
    "sphere_in_shell_B", "sphere_in_shell_B_inside", "sphere_with_iron_shell",
    "thick_solenoid_axis_Bz", "tube_in_box",
]
