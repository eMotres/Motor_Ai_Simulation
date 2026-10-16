"""Isolated continuous-angle P2 slip interpolation experiment.

This is a research prototype, not selected by the production FEM solver.
Angles are measured in radians.  The P2 trace is parameterised by the uniform
ring slot coordinate (the same coordinate used by integer SlipProjection).
"""

from __future__ import annotations

import math

import numpy as np
from scipy.sparse import coo_matrix

from motor_ai_sim.simulation.p2_projection import SignedUF, SlipProjection


class ContinuousSlipProjection:
    """Fixed reduced coordinates with moving rotor P2 trace interpolation."""

    def __init__(self, discrete: SlipProjection, spacing_rad: float):
        if not math.isfinite(spacing_rad) or spacing_rad <= 0:
            raise ValueError("spacing_rad must be finite and positive")
        if discrete.Nring < (3 if discrete.full_ring else 2):
            raise ValueError("the slip ring has too few intervals")
        self.discrete = discrete
        self.spacing_rad = float(spacing_rad)
        self.period = discrete.Nring if discrete.full_ring else discrete.Nring - 1
        self.stator_vertices = [int(discrete.vdof[int(v)]) for v in discrete.sring]
        self.stator_edges = []
        for a, b in discrete._re_pairs:
            edge = discrete.edge_dof(int(discrete.sring[a]),
                                     int(discrete.sring[b]))
            if edge is None:
                raise ValueError("incomplete stator P2 ring trace")
            self.stator_edges.append(edge)
        if any(edge is None for edge in discrete._re_dofs):
            raise ValueError("incomplete rotor P2 ring trace")

        cuts = SignedUF(discrete.N)
        for slave, master, sign in discrete._cut_v + discrete._cut_e:
            cuts.union(slave, master, sign)
        roots = [cuts.find(i) for i in range(discrete.N)]
        self.root = np.asarray([r for r, _ in roots], dtype=int)
        self.sign = np.asarray([s for _, s in roots], dtype=float)

        rotor_samples = [
            (int(discrete.vdof[int(discrete.rring[k]) + discrete.nsn]), float(k))
            for k in range(discrete.Nring)
        ] + [(int(edge), e + .5) for e, edge in enumerate(discrete._re_dofs)]
        # One representative per cut-equivalent rotor ring dof.  In a sector,
        # its two endpoint vertices belong to one signed class.
        self.dependent = {}
        for dof, slot in rotor_samples:
            self.dependent.setdefault(int(self.root[dof]), (dof, slot))
        stator_roots = {int(self.root[dof]) for dof in
                        self.stator_vertices + self.stator_edges}
        if stator_roots.intersection(self.dependent):
            raise ValueError("a cut constraint joins rotor and stator traces")
        if any(int(self.root[dof]) in self.dependent for dof in discrete.D_ids):
            raise ValueError("outer Dirichlet condition touches moving rotor trace")

        self.free_roots = sorted(set(self.root) - self.dependent.keys())
        self.column = {root: j for j, root in enumerate(self.free_roots)}
        self.shape = (discrete.N, len(self.free_roots))
        self.outer_columns = np.unique([
            self.column[int(self.root[dof])] for dof in discrete.D_ids
        ])

    @staticmethod
    def _weights(u):
        return ((1-u)*(1-2*u), 4*u*(1-u), u*(2*u-1)), (
            4*u-3, 4-8*u, 4*u-1)

    def _stator_trace(self, slot):
        wraps, local = divmod(slot, self.period)
        segment = math.floor(local)
        u = local - segment
        if self.discrete.full_ring:
            end = (segment + 1) % self.period
            factor = 1.
        else:
            end = segment + 1
            factor = float(self.discrete.bc_sign if wraps % 2 else 1.)
        dofs = (self.stator_vertices[segment], self.stator_edges[segment],
                self.stator_vertices[end])
        weights, derivatives = self._weights(u)
        return dofs, weights, derivatives, factor

    def build(self, theta_rad: float):
        """Return P(theta), dP/dtheta, and fixed outer Dirichlet columns.

        At an exact stator knot the derivative is the right-hand derivative.
        The two derivatives need not coincide for a merely C0 P2 trace.
        """
        if not math.isfinite(theta_rad):
            raise ValueError("theta_rad must be finite")
        shift = theta_rad / self.spacing_rad
        rows, cols, values = [], [], []
        drows, dcols, dvalues = [], [], []
        mappings = {}
        for root, (representative, base_slot) in self.dependent.items():
            dofs, weights, derivatives, factor = self._stator_trace(
                base_slot + shift)
            scale = factor / self.sign[representative]
            mapping, derivative = {}, {}
            for dof, weight, slope in zip(dofs, weights, derivatives):
                col = self.column[int(self.root[dof])]
                sign = self.sign[dof] * scale
                mapping[col] = mapping.get(col, 0.) + sign * weight
                derivative[col] = (derivative.get(col, 0.) +
                                   sign * slope / self.spacing_rad)
            mappings[root] = (mapping, derivative)
        for row in range(self.shape[0]):
            root = int(self.root[row])
            if root in mappings:
                mapping, derivative = mappings[root]
                factor = self.sign[row]
                for col, value in mapping.items():
                    if value != 0.:
                        rows.append(row); cols.append(col); values.append(factor*value)
                for col, value in derivative.items():
                    if value != 0.:
                        drows.append(row); dcols.append(col)
                        dvalues.append(factor*value)
            else:
                rows.append(row); cols.append(self.column[root])
                values.append(self.sign[row])
        p = coo_matrix((values, (rows, cols)), shape=self.shape).tocsr()
        dp = coo_matrix((dvalues, (drows, dcols)), shape=self.shape).tocsr()
        return p, dp, self.outer_columns.copy()
