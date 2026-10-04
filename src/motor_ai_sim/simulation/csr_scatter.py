"""Cache exact SciPy COO reduction order for an unchanged zero mask.
Only immutable geometry/plans are shared; each call owns its CSR buffers.
"""
from threading import Lock
import numpy as np
from scipy.sparse import csr_matrix, coo_matrix, _sparsetools

class CsrScatter:
    def __init__(self, indices, shape):
        self.shape = tuple(shape)
        self.idx = np.asarray(indices, dtype=np.int32 if max(shape) < 2**31 else np.int64).copy()
        self.idx.flags.writeable = False
        self._plans = {}
        self._lock = Lock()

    def _plan(self, mask):
        original = np.flatnonzero(mask)
        n = len(original)
        ptr = np.empty(self.shape[0]+1, dtype=self.idx.dtype)
        cols = np.empty(n, dtype=self.idx.dtype)
        tags = np.empty(n, dtype=float)
        _sparsetools.coo_tocsr(*self.shape, n, self.idx[0, mask], self.idx[1, mask],
                              original.astype(float), ptr, cols, tags)
        # csr_matrix.sum_duplicates skips sorting when all rows are already
        # sorted. Sorting even then would reorder equal-column contributions.
        if not _sparsetools.csr_has_sorted_indices(self.shape[0], ptr, cols):
            _sparsetools.csr_sort_indices(self.shape[0], ptr, cols, tags)
        rows = np.repeat(np.arange(self.shape[0]), np.diff(ptr))
        start = np.ones(n, dtype=bool)
        if n > 1:
            start[1:] = (rows[1:] != rows[:-1]) | (cols[1:] != cols[:-1])
        groups = np.cumsum(start)-1
        outptr = np.r_[0, np.cumsum(np.bincount(rows[start], minlength=self.shape[0]))]
        plan = (tags.astype(np.intp), groups, cols[start].copy(), outptr)
        for a in plan: a.flags.writeable = False
        return plan

    def assemble(self, data):
        data = np.asarray(data, dtype=float).ravel(order='C')
        if len(data) != self.idx.shape[1]: raise ValueError('element contribution count changed')
        mask = data != 0
        key = np.packbits(mask).tobytes()
        with self._lock:
            plan = self._plans.get(key)
            if plan is None:
                # Do not keep rebuilding/sorting plans when saturation changes
                # the exact-zero pattern. Use the original COO path instead.
                if len(self._plans) < 2:
                    plan = self._plan(mask)
                    self._plans[key] = plan
        if plan is None:
            matrix = coo_matrix((data, self.idx), shape=self.shape)
            matrix.eliminate_zeros()
            return matrix.tocsr()
        order, groups, cols, ptr = plan
        values = np.bincount(groups, weights=data[order], minlength=len(cols))
        return csr_matrix((values, cols.copy(), ptr.copy()), shape=self.shape)
