"""The reduced Newton system of a sliding-band frame, assembled in place.

WHY.  Every Newton iteration of the coupled eddy solve used to build its
linear system from scratch, in SciPy:

    K   = K_const + Σ_k scatter(stiffness of iron sub-basis k)   (N×N CSR adds)
    T   = Σ_k scatter(tangent of iron sub-basis k)               (COO → CSR)
    Jff = Pfᵀ·(K + T)·Pf + Msd_ff                                (2 matmuls)
    Mb  = bmat([[Jff, −Bf], [−Bfᵀ, diag S]]).tocsc()             (COO → CSC)

and the Cholesky path then re-derived, per solve, what it needed of that
matrix: a pattern comparison, the symmetry check on every stored pair and the
upper triangle.  Profiled on the owner's L90 point (cold, 650 frames, 2 889
factorizations, 2026-11-02): ~85 s of the run's 375 s is that bookkeeping,
against 99 s for the numeric factorizations themselves.

None of it moves inside a frame.  The mesh, the element dofs, the constant
stiffness, the conductor mass, the border G and the projection are fixed; an
iteration changes one number per element entry of the saturable iron.  And
between frames only the rotor-side slip dofs change their reduced index (the
union-find roots are stator dofs, so every other dof keeps its column).

So the map "global entry → position in the reduced upper triangle" is built
once per run for every entry that cannot move, and once per frame for the few
entries that touch a slip dof.  An iteration is then one ``bincount`` of the
iron element data into the frame's upper-triangle CSR, which goes to PARDISO
(mtype 2) as it is.

WHAT IS EXACT.  Every reduced entry is the same sum of the same signed global
entries as ``(Proᵀ·M·Pro)[free][:, free]`` restricted to its upper triangle —
the triangle PARDISO's Cholesky reads.  For a reduced pair (a, b), a < b, the
contributions are the global entries (i, j) with r(i) = a, r(j) = b; their
mirrors (j, i) map to (b, a) and are not read, exactly as before.  Only the
ORDER of the floating-point additions differs, so the matrix agrees to
round-off (≤ 1e-15 relative, ``tests/test_p2_frame_plan.py``), not bit for
bit.  The pattern may keep an entry whose terms cancel to an exact zero; that
is a valid Cholesky pattern.

The symmetry check the SciPy path ran on every solve is not needed: the
operator is symmetric by construction (Gram forms and the bordered block),
and only the upper triangle exists.  A non-positive diagonal is still refused
and a Cholesky failure still falls back to the SciPy path, loudly.
``SB_NO_FRAME_PLAN=1`` keeps every solve on the SciPy path.
"""
from __future__ import annotations

import os as _os

import numpy as np

ENABLED = _os.environ.get("SB_NO_FRAME_PLAN") != "1"
#: frames per PARDISO analysis (1 = one analysis per frame, as before).
#: The slip pattern changes every frame (14 298 of 400 752 upper entries on the
#: L90), so one analysis serves a window of frames on the union of their
#: patterns.  Measured, L90 cold, 650 frames: W = 1 / 2 / 3 / 4 -> wall 350 /
#: 325 / 325 / 322 s, factor fill +0 / +6 / +11 / +13 %.  On a repeat pass the
#: orderings are cached and only the fill is left, which W = 2 pays least.
WINDOW = max(1, int(_os.environ.get("SB_PLAN_WINDOW", "2") or 1))


def window_shifts(m, d, w):
    """The ``w`` shifts of the analysis window that holds shift ``m`` when the
    march steps by ``d``: aligned on multiples of ``w·|d|`` (on ``m``'s residue
    class mod ``|d|``), so the same rotor positions always meet the same
    window — and a later pass finds its ordering in the cache."""
    d = abs(int(d)); m = int(m)
    if w <= 1 or d == 0:
        return [m]
    r = m % d
    s0 = r + ((m - r) // (w * d)) * (w * d)
    return [s0 + k * d for k in range(w)]


class PlanMismatch(Exception):
    """The frame cannot use the plan (caller takes the SciPy path)."""


def reduced_map(Pro, free):
    """(rr, sg) of every field dof under ``Pro``/``free``: the reduced index
    (−1 = Dirichlet column) and the weld sign.  ``Pro`` must have one ±1 per
    row (every slip projection does); anything else raises PlanMismatch."""
    P = Pro.tocsr()
    if not (P.shape[0] > 0 and bool(np.all(np.diff(P.indptr) == 1))
            and bool(np.all(np.abs(P.data) == 1.0))):
        raise PlanMismatch("projection is not one +-1 per row")
    pos = np.full(P.shape[1], -1, dtype=np.int64)
    pos[np.asarray(free, dtype=np.int64)] = np.arange(len(free))
    return pos[P.indices], np.asarray(P.data, dtype=float)


class ReducedPlan:
    """Run-level half: the entry → reduced-position map of every entry that
    does not touch a slip dof, for one fixed set of contribution blocks.

    ``n_free``: the reduced field unknowns (``len(free)``); the border
    unknowns follow them.
    ``blocks``: list of (rows, cols) int arrays in the EXTENDED index space —
    field dofs ``0..N-1``, border unknowns ``N..N+nb-1`` — each the COO layout
    of one contribution whose values the caller supplies later in the same
    order.  ``slip``: the global field dofs whose reduced index may change
    between frames.  ``rr0``/``sg0``: the reduced index (−1 = not free) and
    sign of every field dof at the reference frame.
    """

    def __init__(self, n_dof, n_free, n_border, blocks, slip, rr0, sg0):
        self.N = int(n_dof)
        self.nb = int(n_border)
        self.nf = int(n_free)
        self.n = self.nf + self.nb
        self.slip = np.zeros(self.N + self.nb, dtype=bool)
        self.slip[np.asarray(slip, dtype=np.int64)] = True
        self.slip_idx = np.flatnonzero(self.slip[:self.N])
        self.rr0 = np.asarray(rr0, dtype=np.int64).copy()
        self.sg0 = np.asarray(sg0, dtype=float).copy()
        rrE, sgE = self._ext(self.rr0, self.sg0)
        nn = np.int64(self.n)
        self.fixed = []        # per block: (entry ids, position in F, sign)
        self.moving = []       # per block: (entry ids, rows, cols)
        keys = []
        for rows, cols in blocks:
            rows = np.asarray(rows, dtype=np.int64)
            cols = np.asarray(cols, dtype=np.int64)
            mov = self.slip[rows] | self.slip[cols]
            fid = np.flatnonzero(~mov)
            R = rrE[rows[fid]]; C = rrE[cols[fid]]
            ok = (R >= 0) & (C >= 0) & (R <= C)
            fid = fid[ok]
            keys.append(R[ok] * nn + C[ok])
            self.fixed.append([fid, None,
                               sgE[rows[fid]] * sgE[cols[fid]]])
            mid = np.flatnonzero(mov)
            self.moving.append((mid, rows[mid], cols[mid]))
        self.F = np.unique(np.concatenate(keys)) if keys else np.zeros(0, np.int64)
        for fx, k in zip(self.fixed, keys):
            fx[1] = np.searchsorted(self.F, k)
        self.n_fixed_keys = int(self.F.size)

    def _ext(self, rr, sg):
        """Reduced index / sign over the extended space (border appended)."""
        rrE = np.concatenate([rr, self.nf + np.arange(self.nb, dtype=np.int64)])
        sgE = np.concatenate([sg, np.ones(self.nb)])
        return rrE, sgE

    def frame(self, rr, sg):
        """The frame half for this frame's map, or PlanMismatch when a dof
        outside the slip set moved (then the plan is not valid for it)."""
        return self.window([(rr, sg)])[0]

    def window(self, maps):
        """One pattern for several frames' maps (the union of their slip-ring
        entries) and one :class:`FrameSystem` per map on it.  PlanMismatch
        when any map moves a dof outside the slip set."""
        keep = ~self.slip[:self.N]
        ext = []
        for rr, sg in maps:
            rr = np.asarray(rr, dtype=np.int64)
            sg = np.asarray(sg, dtype=float)
            if (rr.shape != self.rr0.shape
                    or not np.array_equal(rr[keep], self.rr0[keep])
                    or not np.array_equal(sg[keep], self.sg0[keep])):
                raise PlanMismatch(
                    "a dof outside the slip set changed its column")
            ext.append(self._ext(rr, sg))
        pat = FramePattern(self, ext)
        return pat.members

    def fixed_sum(self, k, values):
        """Σ of block ``k``'s fixed entries into the run's fixed key space."""
        fid, pos, sgn = self.fixed[k]
        return np.bincount(pos, weights=sgn * np.ravel(values)[fid],
                           minlength=self.F.size)


class FramePattern:
    """The reduced upper-triangle CSR pattern shared by one or more frames:
    the run's fixed keys plus the union of the members' slip-ring keys.
    ``indptr``/``indices``: rows ascending, columns ascending within a row,
    diagonal first.  A member's entries absent from another member's frame
    are explicit zeros there — a valid Cholesky pattern, the same answer."""

    def __init__(self, plan: ReducedPlan, maps_ext):
        self.plan = plan
        nn = np.int64(plan.n)
        per = []               # per member, per block: (entry ids, key, sign)
        mk = []
        for rrE, sgE in maps_ext:
            mv = []
            for mid, rows, cols in plan.moving:
                R = rrE[rows]; C = rrE[cols]
                # an entry mapped below the diagonal is read through its mirror
                ok = (R >= 0) & (C >= 0) & (R <= C)
                k = R[ok] * nn + C[ok]
                mv.append((mid[ok], k, sgE[rows[ok]] * sgE[cols[ok]]))
                mk.append(k)
            per.append(mv)
        F = plan.F
        mk = np.unique(np.concatenate(mk)) if mk else np.zeros(0, np.int64)
        at = np.searchsorted(F, mk)
        new = mk[(at >= F.size) | (F[np.minimum(at, F.size - 1)] != mk)]             if F.size else mk
        # merged sorted keys: F with the window's new keys slotted in
        self.posF = np.arange(F.size) + np.searchsorted(new, F)
        keys = np.empty(F.size + new.size, dtype=np.int64)
        keys[self.posF] = F
        keys[np.searchsorted(F, new) + np.arange(new.size)] = new
        rows = keys // nn
        self.indices = (keys - rows * nn).astype(np.int32)
        self.indptr = np.concatenate(
            [[0], np.cumsum(np.bincount(rows, minlength=plan.n))]).astype(np.int32)
        self.diag = np.flatnonzero(rows == self.indices)
        self.n = plan.n
        self.nnz = int(keys.size)
        self.perm_key = None
        if self.diag.size != self.n:
            raise PlanMismatch("reduced system has an empty diagonal entry")
        self.members = [
            FrameSystem(self, [(mid, np.searchsorted(keys, k), sg)
                               for mid, k, sg in mv])
            for mv in per]


class FrameSystem:
    """One frame on a :class:`FramePattern`: where its slip-ring entries go.
    :meth:`data` turns per-block values into the pattern's CSR ``data``."""

    def __init__(self, pattern: FramePattern, mov):
        self.pattern = pattern
        self.plan = pattern.plan
        self.mov = mov         # per block: (entry ids, position, sign)
        self.n = pattern.n
        self.nnz = pattern.nnz
        self.indices = pattern.indices
        self.indptr = pattern.indptr
        self.diag = pattern.diag
        self.posF = pattern.posF

    def fixed_to_frame(self, fixed_sum):
        """A vector in the run's fixed key space, placed in this frame."""
        out = np.zeros(self.nnz)
        out[self.posF] = fixed_sum
        return out

    def moving_sum(self, k, values):
        """Σ of block ``k``'s moving entries into this frame's ``data``."""
        mid, pos, sgn = self.mov[k]
        return np.bincount(pos, weights=sgn * np.ravel(values)[mid],
                           minlength=self.nnz)

    def data(self, values, const=None):
        """``data`` of the CSR: ``const`` (a frame-space vector, may be None)
        plus every block whose values are given (``values[k]`` or None)."""
        plan = self.plan
        acc = None
        for k, v in enumerate(values):
            if v is None:
                continue
            fs = plan.fixed_sum(k, v)
            acc = fs if acc is None else acc + fs
        out = (self.fixed_to_frame(acc) if acc is not None
               else np.zeros(self.nnz))
        for k, v in enumerate(values):
            if v is not None and self.mov[k][0].size:
                out += self.moving_sum(k, v)
        if const is not None:
            out += const
        return out
