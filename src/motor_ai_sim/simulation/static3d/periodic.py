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
    """Eliminated dof pairs plus the sign of the coupling.

    ``signs``, when present, is a PER-PAIR multiplier on top of ``sign``.  A
    scalar dof needs nothing more than ``sign``; an EDGE dof also picks up the
    sign of its own global orientation against the rotated master edge, and that
    is per pair — see ``edge_dof_pairs``.
    """
    masters: np.ndarray
    slaves: np.ndarray
    sign: float
    sector_rad: float
    signs: Optional[np.ndarray] = None

    @property
    def kills_constant(self) -> bool:
        return self.sign < 0 and self.masters.size > 0

    def pair_signs(self) -> np.ndarray:
        """The multiplier actually applied to each (master -> slave) pair."""
        if self.signs is None:
            return np.full(self.slaves.size, float(self.sign))
        return float(self.sign) * np.asarray(self.signs, dtype=float)


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


def edge_dof_pairs(basis, sector_rad: float, sign: float,
                   tol: float = 1e-9, atol: float = 1e-10) -> Periodic:
    """The same pairing for NEDELEC EDGE dofs, with the orientation sign.

    What the constraint is
    ----------------------
    The scalar statement ``phi(R x) = s phi(x)`` becomes, for a vector field,

        A(R x) = s * R A(x)

    (R the rotation by the sector angle).  An edge dof is the tangential line
    integral of A along that edge, so for a slave edge e' = R e with tangent
    t' = R t,

        dof(e') = int_{e'} A . t'  =  s * int_e A . t  =  s * dof(e)

    — the rotation cancels between the field and the tangent, which is exactly
    why edge elements carry rotational symmetry as cleanly as nodes do.

    The one thing that is NOT automatic
    -----------------------------------
    scikit-fem orients every global edge from the LOWER vertex index to the
    higher one, and vertex numbering knows nothing about the rotation.  So the
    slave edge's own orientation may be the reverse of R applied to the master
    edge's, and then the pair carries an extra factor -1.  That is what
    ``Periodic.signs`` holds.  Getting it wrong does not blow up: it quietly
    solves a machine with a few reversed edges on the cut plane, which is why
    the direction test below is exact (a dot product that must be +1 or -1 to
    within round-off) and raises rather than choosing the nearer one.

    Pairing is by edge MIDPOINT, rotated — the same nearest-neighbour lookup and
    the same "must be exact" discipline as the nodal version, and it rests on
    the same property of the mesh: the two cut planes are exact rotations of each
    other, so every master edge has a slave edge, not merely a nearby one.
    """
    m = basis.mesh
    e = np.asarray(m.edges)
    if int(basis.N) != e.shape[1]:
        raise RuntimeError(
            f"{basis.N} dofs for {e.shape[1]} edges — edge_dof_pairs is for the "
            "lowest-order Nedelec element, one dof per edge in edge order")
    p = np.asarray(m.p)
    mid = 0.5 * (p[:, e[0]] + p[:, e[1]])
    tan = p[:, e[1]] - p[:, e[0]]

    x, y, z = mid[0], mid[1], mid[2]
    r = np.hypot(x, y)
    ca, sa = math.cos(sector_rad), math.sin(sector_rad)
    scale = float(np.max(r)) if r.size else 1.0
    t = max(tol, 1e-12 * scale)

    # BOTH endpoints on the plane, not just the midpoint: an edge that merely
    # crosses the plane can have its midpoint on it and is not a cut-plane edge.
    on_m0 = (np.abs(p[1]) <= t) & (p[0] > -t)
    on_s0 = (np.abs(p[0] * sa - p[1] * ca) <= t) & ((p[0] * ca + p[1] * sa) > -t)
    mm = np.flatnonzero(on_m0[e[0]] & on_m0[e[1]] & (r > t))
    ss = np.flatnonzero(on_s0[e[0]] & on_s0[e[1]] & (r > t))
    if mm.size != ss.size:
        raise RuntimeError(
            f"periodic cut planes hold {mm.size} vs {ss.size} EDGE dofs — the "
            "sector mesh is not rotationally periodic, so a sector solve would "
            "be a different machine")
    if mm.size == 0:
        return Periodic(mm, ss, float(sign), float(sector_rad))

    key_m = np.stack([r[mm], z[mm]])
    key_s = np.stack([r[ss], z[ss]])
    om = np.lexsort((key_m[1], key_m[0]))
    os_ = np.lexsort((key_s[1], key_s[0]))
    mm, ss = mm[om], ss[os_]
    err = float(np.max(np.abs(np.stack([r[mm] - r[ss], z[mm] - z[ss]]))))
    a = max(atol, 1e-9 * scale)
    if err > a:
        raise RuntimeError(
            f"periodic EDGE pairing is off by {err:.3e} m (tol {a:.1e}) — "
            "the two cut planes do not carry the same mesh")

    # rotate the master tangent and compare with the slave's own orientation
    tm_ = tan[:, mm]
    rot = np.vstack([tm_[0] * ca - tm_[1] * sa,
                     tm_[0] * sa + tm_[1] * ca,
                     tm_[2]])
    ts = tan[:, ss]
    num = (rot * ts).sum(axis=0)
    den = np.linalg.norm(rot, axis=0) * np.linalg.norm(ts, axis=0)
    cosang = num / np.maximum(den, 1e-300)
    if float(np.max(np.abs(np.abs(cosang) - 1.0))) > 1e-7:
        raise RuntimeError(
            "a paired edge is not the rotation of its master (worst |cos| = "
            f"{float(np.max(np.abs(np.abs(cosang) - 1.0))):.2e}) — the cut "
            "planes share midpoints but not edges")
    signs = np.where(cosang > 0.0, 1.0, -1.0)
    return Periodic(mm.astype(np.int64), ss.astype(np.int64), float(sign),
                    float(sector_rad), signs=signs)


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
    vals = np.concatenate([np.ones(free.size), per.pair_signs()])
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
