"""Fill-reducing orderings PARDISO already computed, remembered per pattern.

WHY.  The slip weld changes the sparsity pattern of every frame's reduced
operator, so the Cholesky handle re-runs PARDISO phase 11 (ordering + symbolic
factorization) once per frame.  With the deploy image's
``MKL_THREADING_LAYER=SEQUENTIAL`` that analysis is several numeric
factorizations' worth of work, almost all of it the METIS nested dissection.
Measured on a 66 k-dof P2 operator, one thread (scratch benchmark,
2026-10-30):

    phase 11, MKL's own METIS ordering      407 ms
    phase 11, the SAME ordering handed in    84 ms   (iparm[4] = 1)
    phase 23, numeric + solve               118 ms

and the factor fill and the solution were bit-identical between the two
analyses: the ordering is the only thing METIS contributes, and it is a
function of the pattern alone.

The patterns REPEAT.  The rotor shift wraps every ring period, so a run longer
than one ring period (eddy warm-up, demag pre-pass, TDM orbit, the reported
window after them) sees the same patterns again, and every pass of a coupled
or held-torque run re-solves the SAME mesh at the SAME shifts.  This cache
keeps the ordering METIS produced for a pattern and hands it back on the next
analysis of that pattern, so the repeat costs the symbolic pass only.

EXACTNESS.  A cached ordering is METIS's own ordering for that exact pattern
(the key is a digest of ``indptr``/``indices`` and ``n``), so the analysis is
the one MKL would have computed: same permutation, same fill, same numeric
factorization.  Even a digest collision could not produce a wrong answer —
any permutation of the right size is a valid ordering — only a different
round-off.  ``SB_PARDISO_PERM_CACHE_MB=0`` turns the cache off (default 256 MB
per process; one entry is 4 bytes per equation).
"""
from __future__ import annotations

import ctypes
import hashlib
import os
from collections import OrderedDict
from threading import Lock

import numpy as np

_MB = float(os.environ.get("SB_PARDISO_PERM_CACHE_MB", "256") or 0.0)


class PermCache:
    """Process-wide LRU of PARDISO orderings, keyed by (mtype, pattern)."""

    def __init__(self, budget_bytes: float):
        self.budget = float(budget_bytes)
        self._d: "OrderedDict[tuple, np.ndarray]" = OrderedDict()
        self._bytes = 0
        self._lock = Lock()
        self.hits = 0
        self.misses = 0

    @property
    def enabled(self) -> bool:
        return self.budget > 0

    @staticmethod
    def key(mtype: int, indptr, indices) -> tuple:
        h = hashlib.blake2b(digest_size=16)
        h.update(np.ascontiguousarray(indptr).view(np.uint8))
        h.update(np.ascontiguousarray(indices).view(np.uint8))
        return (int(mtype), int(np.asarray(indptr).size) - 1,
                int(np.asarray(indices).size), h.digest())

    def get(self, key):
        with self._lock:
            p = self._d.get(key)
            if p is None:
                self.misses += 1
                return None
            self._d.move_to_end(key)
            self.hits += 1
            return p

    def put(self, key, perm) -> None:
        perm = np.array(perm, dtype=np.int32, copy=True)
        perm.flags.writeable = False
        with self._lock:
            if key in self._d or perm.nbytes > self.budget:
                return
            self._d[key] = perm
            self._bytes += perm.nbytes
            while self._bytes > self.budget and self._d:
                _, old = self._d.popitem(last=False)
                self._bytes -= old.nbytes

    def clear(self) -> None:
        with self._lock:
            self._d.clear()
            self._bytes = 0


#: the one cache of this process (sweep workers each have their own)
PERM_CACHE = PermCache(_MB * 1024 * 1024)


def init_iparm(solver) -> bool:
    """Fill a FRESH handle's ``iparm`` with MKL's defaults (``pardisoinit``).

    pypardiso hands MKL an all-zero ``iparm``, which tells PARDISO to use its
    defaults and IGNORE every other entry — including ``iparm[4]``, the
    user-ordering switch.  ``pardisoinit`` writes the same defaults with
    ``iparm[0] = 1``, after which ``iparm[4]`` is honoured.  Measured: the
    analysis and the solution are bit-identical to the all-zero call.
    Returns False (and leaves the handle as it was) when that is not possible.
    """
    if int(solver.iparm[0]) != 0:
        return True                       # MKL already wrote its defaults back
    try:
        lib = solver.libmkl
        fn = lib.pardisoinit
        fn.argtypes = [ctypes.POINTER(solver._pt_type[0]),
                       ctypes.POINTER(ctypes.c_int32),
                       ctypes.POINTER(ctypes.c_int32)]
        fn.restype = None
        ip = np.zeros(64, dtype=np.int32)
        fn(solver.pt.ctypes.data_as(ctypes.POINTER(solver._pt_type[0])),
           ctypes.byref(ctypes.c_int32(int(solver.mtype))),
           ip.ctypes.data_as(ctypes.POINTER(ctypes.c_int32)))
    except Exception:                     # noqa: BLE001 — no cache, same solve
        return False
    if int(ip[0]) != 1:
        return False
    solver.iparm[:] = ip
    return True


def analyse(solver, A, key, cache: "PermCache | None" = None) -> bool:
    """PARDISO phase 11 of ``A`` on ``solver``, through the ordering cache.

    A cached ordering for ``key`` is handed in (``iparm[4] = 1``); otherwise
    MKL computes its own and returns it (``iparm[4] = 2``), and it is stored.
    Returns True when the ordering came from the cache.
    """
    if cache is None:
        cache = PERM_CACHE
    n = int(A.shape[0])
    zero = np.zeros((n, 1))
    if not cache.enabled or not init_iparm(solver):
        solver.set_phase(11)
        solver._call_pardiso(A, zero)
        return False
    perm = cache.get(key)
    hit = perm is not None and perm.size == n
    if hit:
        solver.iparm[4] = 1
        solver.perm = np.array(perm, dtype=np.int32, copy=True)
    else:
        solver.iparm[4] = 2
        solver.perm = np.zeros(n, dtype=np.int32)
    try:
        solver.set_phase(11)
        solver._call_pardiso(A, zero)
        if not hit:
            p = solver.perm
            if p.size == n and int(p.min()) == 1 and int(p.max()) == n:
                cache.put(key, p)
    finally:
        # back to MKL's own ordering for anything else that uses this handle
        solver.iparm[4] = 0
        solver.perm = np.zeros(0, dtype=np.int32)
    return hit
