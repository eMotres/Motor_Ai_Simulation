"""Trace-only P2 L2 mortar experiment; never imported by the FEM solver."""

from __future__ import annotations

import math
import time
import tracemalloc

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.linalg import splu

from motor_ai_sim.simulation.p2_projection import SlipProjection
if __package__:
    from scripts.continuous_p2_projection_prototype import ContinuousSlipProjection
else:  # direct ``python scripts/continuous_p2_mortar_trace_prototype.py``
    from continuous_p2_projection_prototype import ContinuousSlipProjection


_GX = (-math.sqrt(3 / 5), 0., math.sqrt(3 / 5))
_GW = (5 / 9, 8 / 9, 5 / 9)
_P2_MASS = np.array([[4., 2., -1.], [2., 16., 2.], [-1., 2., 4.]]) / 30.


class TraceMortarProjection:
    """Keep overlap matrices on the rotor/stator trace, not the volume basis."""

    def __init__(self, discrete: SlipProjection, spacing_rad: float):
        self.trace = ContinuousSlipProjection(discrete, spacing_rad)
        self.discrete = discrete
        self.spacing_rad = self.trace.spacing_rad
        self.shape = self.trace.shape
        self.outer_columns = self.trace.outer_columns.copy()
        self.rotor_roots = sorted(self.trace.dependent)
        self.rotor_col = {root: i for i, root in enumerate(self.rotor_roots)}
        stator_roots = sorted({int(self.trace.root[d]) for d in
                               self.trace.stator_vertices + self.trace.stator_edges})
        self.stator_roots = stator_roots
        self.stator_col = {root: i for i, root in enumerate(stator_roots)}
        self.stator_global_cols = np.array(
            [self.trace.column[root] for root in stator_roots], dtype=int)
        self.n_rotor = len(self.rotor_roots)
        self.n_stator = len(stator_roots)
        self.rotor_edges = []
        for edge, (a, b) in enumerate(discrete._re_pairs):
            dofs = (int(discrete.vdof[int(discrete.rring[a]) + discrete.nsn]),
                    int(discrete._re_dofs[edge]),
                    int(discrete.vdof[int(discrete.rring[b]) + discrete.nsn]))
            self.rotor_edges.append(tuple(
                (self.rotor_col[int(self.trace.root[d])], self.trace.sign[d])
                for d in dofs))

        rows, cols, values = [], [], []
        for edge in self.rotor_edges:
            for i in range(3):
                for j in range(3):
                    rows.append(edge[i][0]); cols.append(edge[j][0])
                    values.append(edge[i][1]*edge[j][1]*_P2_MASS[i, j])
        self.Mrr = coo_matrix((values, (rows, cols)),
                              shape=(self.n_rotor, self.n_rotor)).tocsc()
        self._lu = splu(self.Mrr)

        self._static_rows = np.flatnonzero(
            ~np.isin(self.trace.root, self.rotor_roots))
        self._moving_rows = np.flatnonzero(
            np.isin(self.trace.root, self.rotor_roots))
        self._moving_indices = np.array([
            self.rotor_col[int(self.trace.root[row])]
            for row in self._moving_rows], dtype=int)

    def overlap_trace(self, theta_rad: float):
        """Exact-overlap mixed matrices with only stator trace columns."""
        if not math.isfinite(theta_rad):
            raise ValueError("theta_rad must be finite")
        shift = theta_rad / self.spacing_rad
        mixed = np.zeros((self.n_rotor, self.n_stator))
        derivative = np.zeros_like(mixed)
        for e, rotor_edge in enumerate(self.rotor_edges):
            left, right = float(e), float(e+1)
            crossing = math.floor(left + shift) + 1 - shift
            cuts = ([left, crossing, right] if left < crossing < right
                    else [left, right])
            ri = np.array([item[0] for item in rotor_edge], dtype=int)
            rs = np.array([item[1] for item in rotor_edge])
            for a, b in zip(cuts, cuts[1:]):
                half, centre = (b-a)/2, (a+b)/2
                for node, weight in zip(_GX, _GW):
                    slot = centre + half*node
                    rotor_w = rs * self.trace._weights(slot-e)[0]
                    dofs, stator_w, slopes, wrap = self.trace._stator_trace(
                        slot + shift)
                    si = np.array([self.stator_col[int(self.trace.root[d])]
                                   for d in dofs], dtype=int)
                    ss = np.array([self.trace.sign[d]*wrap for d in dofs])
                    stator_w = ss * stator_w
                    slope_w = ss * slopes / self.spacing_rad
                    factor = weight * half
                    for i in range(3):
                        for j in range(3):
                            mixed[ri[i], si[j]] += factor*rotor_w[i]*stator_w[j]
                            derivative[ri[i], si[j]] += factor*rotor_w[i]*slope_w[j]
        return mixed, derivative

    def _embed(self, transfer, *, derivative=False):
        static = np.array([], dtype=int) if derivative else self._static_rows
        moving = self._moving_rows
        row_values = (self.trace.sign[moving, None] *
                      transfer[self._moving_indices, :])
        rows = np.repeat(moving, self.n_stator)
        cols = np.tile(self.stator_global_cols, moving.size)
        data = row_values.ravel()
        # Exact zeros alone are omitted; there is no magnitude truncation.
        nonzero = data != 0.
        rows, cols, data = rows[nonzero], cols[nonzero], data[nonzero]
        if static.size:
            rows = np.concatenate([static, rows])
            cols = np.concatenate([
                np.array([self.trace.column[int(self.trace.root[r])]
                          for r in static], dtype=int), cols])
            data = np.concatenate([self.trace.sign[static], data])
        return coo_matrix((data, (rows, cols)), shape=self.shape).tocsr()

    def build(self, theta_rad: float):
        mixed, derivative = self.overlap_trace(theta_rad)
        transfer = self._lu.solve(mixed)
        dtransfer = self._lu.solve(derivative)
        return (self._embed(transfer), self._embed(dtransfer, derivative=True),
                self.outer_columns.copy())

    def rotor_coordinates(self, full_values):
        return np.asarray([
            full_values[self.trace.dependent[root][0]] /
            self.trace.sign[self.trace.dependent[root][0]]
            for root in self.rotor_roots
        ])


def synthetic_sector(n_ring=505, n_dof=17299, bc_sign=-1):
    """Algebraic P2 trace with run16-sized volume and no FEM mesh/solve."""
    n_segments = n_ring - 1
    stator_edges = [(k, k+1) for k in range(n_segments)]
    rotor_edges = [(n_ring+k, n_ring+k+1) for k in range(n_segments)]
    facets = np.asarray(stator_edges + rotor_edges, dtype=int).T
    vdof = np.arange(2*n_ring)
    fdof = np.arange(2*n_ring, 2*n_ring+2*n_segments)
    if n_dof <= fdof[-1]:
        raise ValueError("n_dof too small for trace")
    return SlipProjection(
        n_dof=n_dof, facets=facets, vdof=vdof, fdof=fdof,
        rring=np.arange(n_ring), sring=np.arange(n_ring), nsn=n_ring,
        n_ring=n_ring, full_ring=False, bc_sign=bc_sign,
        Mn=np.array([0, n_ring]), Sn=np.array([n_segments, n_ring+n_segments]),
        dirichlet_dofs=np.array([n_dof-1]))


def benchmark_real_trace_size():
    """Return bounded size/time observations; no physical FEM is executed."""
    tracemalloc.start()
    start = time.perf_counter()
    mortar = TraceMortarProjection(
        synthetic_sector(), spacing_rad=math.pi/504)
    init_s = time.perf_counter()-start
    start = time.perf_counter()
    p, dp, _ = mortar.build(.37*math.pi/504)
    build_s = time.perf_counter()-start
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    start = time.perf_counter()
    mortar.build(.37*math.pi/504)
    untraced_build_s = time.perf_counter()-start
    mixed, dmixed = mortar.overlap_trace(.37*math.pi/504)
    sparse_bytes = lambda a: a.data.nbytes+a.indices.nbytes+a.indptr.nbytes
    radius_m = .0091
    delta = math.pi/504
    sag_m = radius_m*(1-math.cos(delta/2))
    return dict(n_ring=505, n_dof=17299, n_reduced=mortar.shape[1],
                n_rotor=mortar.n_rotor, n_stator=mortar.n_stator,
                init_s=init_s, build_s=build_s,
                untraced_build_s=untraced_build_s,
                mrr_nnz=mortar.Mrr.nnz, p_nnz=p.nnz, dp_nnz=dp.nnz,
                mixed_nnz=int(np.count_nonzero(mixed)),
                dmixed_nnz=int(np.count_nonzero(dmixed)),
                mrr_bytes=sparse_bytes(mortar.Mrr),
                p_bytes=sparse_bytes(p), dp_bytes=sparse_bytes(dp),
                dense_global_one_bytes=mortar.n_rotor*mortar.shape[1]*8,
                dense_trace_one_bytes=mortar.n_rotor*mortar.n_stator*8,
                traced_peak_bytes=peak, chord_sag_nm=sag_m*1e9)


if __name__ == "__main__":
    import json
    print(json.dumps(benchmark_real_trace_size(), indent=2))
