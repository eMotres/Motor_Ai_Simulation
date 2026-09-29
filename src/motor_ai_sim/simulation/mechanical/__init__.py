"""Mechanical (structural) solvers.

Added 2026-09-05 on the user's request: "we need to build a mechanical
calculation module — let's start with the rotor centrifugal force
calculation ... to estimate what band is needed to retain the magnets and
the rotor, that is, compute all the stresses and deformations".  The first module is the rotor centrifugal
stress/deformation solve (``rotor_stress``); the package exists so the thermal
and modal work that will follow has an obvious home.

``contact`` (2026-09-05) is the node-to-node unilateral contact between the
parts, ``modal`` / ``rotordynamics`` the vibration side, and ``symmetry``
(2026-09-09) the cyclic-symmetry sector — one pole tied to itself, the way the
user builds it in Fusion.
"""
from __future__ import annotations

#: Left as it was on purpose: this list is what ``from ... import *`` pulls in,
#: and the other modules are imported by name where they are needed rather than
#: dragged in (and their gmsh/scipy cost paid) by anyone touching the package.
__all__ = ["rotor_stress"]
