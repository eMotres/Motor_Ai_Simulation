"""Isolated 1-D L2 mortar experiment for the existing P2 slip ring.

No FEM production path imports this module.  Integration uses the uniform ring
slot coordinate; its constant physical arclength factor cancels from the mortar
map on the structured circular belt.
"""

from __future__ import annotations

import math

import numpy as np
from scipy.linalg import cho_factor, cho_solve
from scipy.sparse import coo_matrix

from scripts.continuous_p2_projection_prototype import ContinuousSlipProjection


_GAUSS_X = (-math.sqrt(3 / 5), 0., math.sqrt(3 / 5))
_GAUSS_W = (5 / 9, 8 / 9, 5 / 9)


class ContinuousMortarProjection:
    """Fixed cut-reduced coordinates and exact-overlap P2 trace projection."""

    def __init__(self, discrete, spacing_rad: float):
        self.trace = ContinuousSlipProjection(discrete, spacing_rad)
        self.discrete = discrete
        self.spacing_rad = self.trace.spacing_rad
        self.rotor_roots = sorted(self.trace.dependent)
        self.rotor_column = {root: col for col, root in
                             enumerate(self.rotor_roots)}
        self.n_rotor = len(self.rotor_roots)
        self.shape = self.trace.shape
        self.outer_columns = self.trace.outer_columns.copy()

        # Each P2 edge has endpoint, midpoint, endpoint dofs. Sector cut
        # endpoints are already one signed coordinate in the static cut UF.
        self.rotor_edges = []
        for edge, (a, b) in enumerate(discrete._re_pairs):
            dofs = (int(discrete.vdof[int(discrete.rring[a]) + discrete.nsn]),
                    int(discrete._re_dofs[edge]),
                    int(discrete.vdof[int(discrete.rring[b]) + discrete.nsn]))
            self.rotor_edges.append(tuple(
                (self.rotor_column[int(self.trace.root[dof])],
                 self.trace.sign[dof]) for dof in dofs))

        mass = np.zeros((self.n_rotor, self.n_rotor))
        for edge in self.rotor_edges:
            for node, weight in zip(_GAUSS_X, _GAUSS_W):
                u = (node + 1) / 2
                basis = self._basis(edge, self.trace._weights(u)[0],
                                    self.n_rotor)
                mass += (weight / 2) * np.outer(basis, basis)
        self.Mrr = mass
        self._factor = cho_factor(mass, lower=True, check_finite=True)

    @staticmethod
    def _basis(edge, weights, size):
        result = np.zeros(size)
        for (column, sign), weight in zip(edge, weights):
            result[column] += sign * weight
        return result

    def _stator_basis(self, slot):
        dofs, weights, slopes, wrap_sign = self.trace._stator_trace(slot)
        edge = tuple((self.trace.column[int(self.trace.root[dof])],
                      self.trace.sign[dof] * wrap_sign) for dof in dofs)
        return (self._basis(edge, weights, self.shape[1]),
                self._basis(edge, slopes, self.shape[1]) / self.spacing_rad)

    def overlap(self, theta_rad: float):
        """Return exact P2 overlap Mrs(theta) and dMrs/dtheta.

        The moving split-point boundary terms cancel because the signed stator
        trace is continuous at each vertex, including the sector wrap.
        """
        if not math.isfinite(theta_rad):
            raise ValueError("theta_rad must be finite")
        shift = theta_rad / self.spacing_rad
        mixed = np.zeros((self.n_rotor, self.shape[1]))
        derivative = np.zeros_like(mixed)
        for e, rotor_edge in enumerate(self.rotor_edges):
            left, right = float(e), float(e + 1)
            crossing = math.floor(left + shift) + 1 - shift
            cuts = ([left, crossing, right] if left < crossing < right
                    else [left, right])
            for a, b in zip(cuts, cuts[1:]):
                half, centre = (b-a)/2, (a+b)/2
                for node, weight in zip(_GAUSS_X, _GAUSS_W):
                    slot = centre + half*node
                    rotor = self._basis(
                        rotor_edge, self.trace._weights(slot-e)[0],
                        self.n_rotor)
                    stator, slope = self._stator_basis(slot + shift)
                    mixed += (weight*half) * np.outer(rotor, stator)
                    derivative += (weight*half) * np.outer(rotor, slope)
        return mixed, derivative

    def build(self, theta_rad: float):
        """Return fixed-coordinate P(theta), analytic P'(theta), outer columns."""
        mixed, derivative = self.overlap(theta_rad)
        transfer = cho_solve(self._factor, mixed)
        dtransfer = cho_solve(self._factor, derivative)
        rows, cols, vals = [], [], []
        drows, dcols, dvals = [], [], []
        for row in range(self.shape[0]):
            root = int(self.trace.root[row])
            sign = self.trace.sign[row]
            if root in self.rotor_column:
                index = self.rotor_column[root]
                for col, value in enumerate(transfer[index]):
                    if value != 0.:
                        rows.append(row); cols.append(col); vals.append(sign*value)
                for col, value in enumerate(dtransfer[index]):
                    if value != 0.:
                        drows.append(row); dcols.append(col)
                        dvals.append(sign*value)
            else:
                rows.append(row); cols.append(self.trace.column[root])
                vals.append(sign)
        p = coo_matrix((vals, (rows, cols)), shape=self.shape).tocsr()
        dp = coo_matrix((dvals, (drows, dcols)), shape=self.shape).tocsr()
        return p, dp, self.outer_columns.copy()

    def rotor_coordinates(self, full_values):
        """Read cut-reduced rotor trace coefficients from a full P2 vector."""
        return np.asarray([
            full_values[self.trace.dependent[root][0]] /
            self.trace.sign[self.trace.dependent[root][0]]
            for root in self.rotor_roots
        ])
