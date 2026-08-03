"""Rotational (anti-)periodicity for the scalar potential, as a dof constraint.

The sector model is only worth anything if the two radial cut planes really do
carry the machine's symmetry.  For the total scalar potential that reads

    phi(R_a x) = s * phi(x),      s = -1 anti-periodic, +1 periodic

with R_a the rotation by the sector angle.  It is the exact 3D analogue of the
2D solver's ``A_z(r, 2pi/N) = sign * A_z(r, 0)``
(``mesher._apply_anti_periodic``), and it is imposed the same way: eliminate
every slave dof, ``phi_slave = s * phi_master``, and solve the reduced system

    (T^T K T) x_red = T^T f ,      phi = T x_red

The elimination is built here rather than reused from the 2D mesher because that
one fills a ``lil_matrix`` in a Python loop over dofs — fine for a 2D wedge, ten
seconds of pure overhead at 300k 3D dofs.  Same matrix, built with three numpy
arrays.

Pairing dofs
------------
The mesh comes from ``motor_mesh``, where the cross-section is meshed with gmsh
rotational periodicity and then extruded on shared z levels.  So a dof on the
slave plane sits EXACTLY on the rotation of a dof on the master plane — vertex
dofs because the 2D nodes match, P2 edge dofs because the extruded quad faces
are parallelograms and both of their diagonals share the same midpoint.  The
pairing is therefore a nearest-neighbour lookup that must come out exact; a
tolerance failure here means the symmetry has quietly been lost, so it raises
instead of pairing "close enough" dofs.

One consequence worth stating: with s = -1 the constraint removes the constant
null space by itself (a nonzero constant is not anti-periodic), so a pure
Neumann outer boundary needs no pinned node.  With s = +1 it does.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np


@dataclass
class Periodic:
    """Eliminated dof pairs plus the sign of the coupling."""
    masters: np.ndarray
    slaves: np.ndarray
    sign: float
    sector_rad: float

    @property
    def kills_constant(self) -> bool:
        return self.sign < 0 and self.masters.size > 0


def dof_pairs(basis, sector_rad: float, sign: float,
              tol: float = 1e-9, atol: float = 1e-10) -> Periodic:
    """Pair the dofs on theta = 0 with those on theta = sector_rad.

    ``tol`` is the half-plane membership tolerance in metres (dofs are on the
    plane by construction, so it only has to beat round-off); ``atol`` is the
    match tolerance after rotation.
    """
    P = np.asarray(basis.doflocs)                  # (3, ndof)
    x, y, z = P[0], P[1], P[2]
    r = np.hypot(x, y)
    ca, sa = math.cos(sector_rad), math.sin(sector_rad)

    scale = float(np.max(r)) if r.size else 1.0
    t = max(tol, 1e-12 * scale)
    m = np.flatnonzero((np.abs(y) <= t) & (x > t))
    s = np.flatnonzero((np.abs(x * sa - y * ca) <= t) & ((x * ca + y * sa) > t))
    if m.size != s.size:
        raise RuntimeError(
            f"periodic cut planes hold {m.size} vs {s.size} dofs — the sector "
            "mesh is not rotationally periodic, so a sector solve would be a "
            "different machine")
    if m.size == 0:
        return Periodic(m, s, float(sign), float(sector_rad))

    # sort both sides by the same rotation-invariant key
    key_m = np.stack([r[m], z[m]])
    key_s = np.stack([r[s], z[s]])
    om = np.lexsort((key_m[1], key_m[0]))
    os_ = np.lexsort((key_s[1], key_s[0]))
    m, s = m[om], s[os_]
    err = float(np.max(np.abs(np.stack([r[m] - r[s], z[m] - z[s]]))))
    a = max(atol, 1e-9 * scale)
    if err > a:
        raise RuntimeError(
            f"periodic dof pairing is off by {err:.3e} m (tol {a:.1e}) — "
            "the two cut planes do not carry the same mesh")
    return Periodic(m.astype(np.int64), s.astype(np.int64),
                    float(sign), float(sector_rad))


def elimination(n: int, per: Periodic):
    """T (n x n_red) with ``phi = T @ phi_red`` and ``full2red`` (-1 on slaves)."""
    from scipy.sparse import csr_matrix

    is_slave = np.zeros(n, dtype=bool)
    is_slave[per.slaves] = True
    free = np.flatnonzero(~is_slave)
    full2red = -np.ones(n, dtype=np.int64)
    full2red[free] = np.arange(free.size, dtype=np.int64)

    rows = np.concatenate([free, per.slaves])
    cols = np.concatenate([np.arange(free.size, dtype=np.int64),
                           full2red[per.masters]])
    vals = np.concatenate([np.ones(free.size),
                           np.full(per.slaves.size, per.sign)])
    if np.any(cols < 0):
        raise RuntimeError("a master dof was itself eliminated as a slave — "
                           "the cut planes overlap")
    T = csr_matrix((vals, (rows, cols)), shape=(n, free.size))
    return T, full2red


def outer_boundary_dofs(basis, r_box: float, z_box: float,
                        rtol: float = 1e-6) -> np.ndarray:
    """Dofs on the TRUNCATION surface only: r = r_box or z = z_box.

    Deliberately not ``basis.get_dofs().all()``.  On this half-model the mesh
    boundary also contains the z = 0 mirror plane (where dphi/dn = 0 is the
    physics: the machine is mirror symmetric and the magnetisation is in-plane,
    so B_z = 0 there) and the two periodic cut planes.  Clamping either of those
    to phi = 0 would impose a wall the machine does not have, and the answer
    would be wrong in exactly the region the whole study is about.
    """
    m = basis.mesh
    bf = m.boundary_facets()
    fp = m.p[:, m.facets[:, bf]]                    # (3, nvert, nfacet)
    c = fp.mean(axis=1)
    rc = np.hypot(c[0], c[1])
    tol_r = rtol * max(r_box, z_box) + 1e-9
    sel = bf[(rc > r_box - 1e-3 * r_box) | (c[2] > z_box - 1e-3 * z_box)]
    if sel.size == 0:
        raise RuntimeError("no truncation facets found — check r_box / z_box")
    return basis.get_dofs(facets=sel).all()
