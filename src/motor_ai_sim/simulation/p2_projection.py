"""The per-frame P2 slip projection: which dofs are welded to which, with what
sign, at rotor shift m.

The rotor moves in exactly one place in this solver — here.  Everything else in
the frame loop (the stiffness, the sources, the eddy blocks, the flux
functional) is assembled once on a mesh that never rotates, and the rotation
enters only as the projection matrix ``Pro`` this module builds.  That is why
the pairing is worth isolating: a sign or an off-by-one in the weld is
indistinguishable, downstream, from a physics error, and it is the only thing
that changes between frames.

Two constraint families are combined in ONE signed union-find rather than
applied in sequence:

  * the anti-periodic RADIAL CUT (sector wedges only) — m-independent, and
  * the SLIP RING itself — rotor node kk welded to stator node kk+m, with a
    ``bc_sign`` flip per wrap of the open wedge.

Both vertices AND P2 edge midpoints are welded.  Welding only the vertices
leaves the quadratic bubbles on the two sides of the gap independent, which is
not a coarse coupling but a torn one.

Extracted verbatim from ``fem_transient_sliding_band``, where it was three
closures plus a module-level union-find.  Every comment recording why an
expression is written the way it is moves unchanged.
"""
from __future__ import annotations

import numpy as np
from scipy.sparse import coo_matrix as _coo


class SignedUF:
    """Signed union-find for combining anti-periodic + slip master-slave
    constraints.  union(a,b,s) means dof_a == s·dof_b; find returns (root, sign)."""
    __slots__ = ("par", "sgn")

    def __init__(self, n):
        self.par = list(range(n)); self.sgn = [1] * n

    def find(self, x):
        p = self.par[x]
        if p == x:
            return x, 1
        r, s = self.find(p)
        self.par[x] = r; self.sgn[x] *= s
        return r, self.sgn[x]

    def union(self, a, b, sign):
        ra, sa = self.find(a); rb, sb = self.find(b)
        if ra == rb:
            return
        self.par[ra] = rb; self.sgn[ra] = sign * sa * sb


class SlipProjection:
    """Builds the P2 projection ``Pro`` at any rotor shift.

    ``vdof``/``fdof`` map global vertex / facet ids to P2 dofs; ``rring`` and
    ``sring`` are the index-aligned rotor and stator ring node ids (both sorted
    by grid slot 0..n_ring−1) and ``nsn`` is the stator node offset applied to
    the rotor block.  ``Mn``/``Sn`` are the matched master/slave radial-cut
    vertices, sorted by radius.  ``dirichlet_dofs`` are the outer-boundary P2
    dofs, returned per frame as their post-projection column ids.

    The counts ``n_redge``/``n_re_pairs``/``n_cut_v``/``n_cut_e`` are exposed
    for the belt log line: how many welds were actually made versus attempted
    is the first thing to look at when a sector run disagrees with a full ring.
    """

    def __init__(self, *, n_dof: int, facets, vdof, fdof, rring, sring,
                 nsn: int, n_ring: int, full_ring: bool, bc_sign,
                 Mn, Sn, dirichlet_dofs) -> None:
        self.N = int(n_dof)
        self.vdof = vdof
        self.fdof = fdof
        self.rring = rring
        self.sring = sring
        self.nsn = int(nsn)
        self.Nring = int(n_ring)
        self.full_ring = bool(full_ring)
        self.bc_sign = bc_sign
        self.D_ids = dirichlet_dofs

        _fa = np.minimum(facets[0], facets[1])
        _fb = np.maximum(facets[0], facets[1])
        self._emap = {(int(_fa[i]), int(_fb[i])): i
                      for i in range(facets.shape[1])}

        Nring = self.Nring
        _full_ring = self.full_ring
        _bc_sign = self.bc_sign

        # ── per-frame P2 projection: weld ring + (for sector) radial-cut, both
        #    VERTICES and EDGE midpoints, with the anti-periodic sign ──────────
        # Ring edges are between angularly-consecutive ring nodes: for the FULL
        # ring the ring is CLOSED (Nring edges, kk→(kk+1)%Nring); for a SECTOR
        # wedge it is OPEN (Nring−1 edges, kk→kk+1).  rring/sring are index-
        # aligned (both sorted by grid slot 0..Nring−1).
        if _full_ring:
            _re_pairs = [(kk, (kk + 1) % Nring) for kk in range(Nring)]
        else:
            _re_pairs = [(kk, kk + 1) for kk in range(Nring - 1)]
        # rotor ring-edge midpoint dof for each segment (constant across frames)
        _re_dofs = [self.edge_dof(int(rring[a]) + nsn, int(rring[b]) + nsn)
                    for a, b in _re_pairs]
        self._re_pairs = _re_pairs
        self._re_dofs = _re_dofs
        self.n_redge = int(sum(x is not None for x in _re_dofs))
        self.n_re_pairs = len(_re_pairs)

        # Anti-periodic RADIAL-CUT welds (sector only) — VERTICES and the cut-
        # boundary EDGE midpoints.  Mn/Sn are matched master/slave cut vertices
        # sorted by radius, so consecutive entries (i, i+1) delimit a cut edge on
        # each side; the slave DOF = _bc_sign · master DOF, same as P1's vertex BC.
        _cut_v = []          # (slave_dof, master_dof, sign) for vertices
        _cut_e = []          # (slave_edge_dof, master_edge_dof, sign) for edges
        if not _full_ring and Mn.size:
            for i in range(Mn.size):
                _cut_v.append((int(vdof[int(Sn[i])]), int(vdof[int(Mn[i])]),
                               float(_bc_sign)))
            for i in range(Mn.size - 1):
                em = self.edge_dof(int(Mn[i]), int(Mn[i + 1]))
                es = self.edge_dof(int(Sn[i]), int(Sn[i + 1]))
                if em is not None and es is not None:
                    _cut_e.append((int(es), int(em), float(_bc_sign)))
        self._cut_v = _cut_v
        self._cut_e = _cut_e
        self.n_cut_v = len(_cut_v)
        self.n_cut_e = len(_cut_e)

    def edge_dof(self, va, vb):
        fi = self._emap.get((va, vb) if va < vb else (vb, va))
        return None if fi is None else int(self.fdof[fi])

    def ring_map(self, m_shift):
        # rotor ring node kk -> (stator node j, sign).  Full ring: periodic
        # mod Nring, sign +1.  Sector: open wedge of Nring nodes, wrap period
        # Nring−1 with a _bc_sign flip per wrap (identical to the P1 loop).
        Nring, _bc_sign = self.Nring, self.bc_sign
        if self.full_ring:
            j = (np.arange(Nring) + int(m_shift)) % Nring
            return j.astype(int), np.ones(Nring)
        j = np.empty(Nring, int); sg = np.ones(Nring)
        for kk in range(Nring):
            jj = kk + int(m_shift); s = 1.0
            while jj > Nring - 1: jj -= (Nring - 1); s *= _bc_sign
            while jj < 0:         jj += (Nring - 1); s *= _bc_sign
            j[kk] = jj; sg[kk] = s
        return j, sg

    def build(self, m_shift):
        """Return (Pro, outer_column_ids) at rotor shift ``m_shift``."""
        N2 = self.N
        Nring, nsn = self.Nring, self.nsn
        vdof, rring, sring = self.vdof, self.rring, self.sring
        suf = SignedUF(N2)
        # radial-cut anti-periodic welds (sector) — m-independent
        for sd, md, sgn in self._cut_v:
            suf.union(sd, md, sgn)
        for sd, md, sgn in self._cut_e:
            suf.union(sd, md, sgn)
        # slip-ring welds (vertices + edge midpoints), rotor shifted by m
        j, sg = self.ring_map(m_shift)
        for kk in range(Nring):
            suf.union(int(vdof[int(rring[kk]) + nsn]),
                      int(vdof[int(sring[j[kk]])]), float(sg[kk]))
        for e, (a, b) in enumerate(self._re_pairs):
            re = self._re_dofs[e]
            if re is None:
                continue
            # the rotor edge (a,b) maps to the stator edge (j[a], j[b]); it is
            # a real mesh edge only when the two endpoints stay angularly
            # consecutive with the SAME sign (i.e. no wrap between them).
            if sg[a] != sg[b]:
                continue
            se = self.edge_dof(int(sring[j[a]]), int(sring[j[b]]))
            if se is not None:
                suf.union(int(re), int(se), float(sg[a]))
        roots = [suf.find(i) for i in range(N2)]
        rid = np.array([r for r, _ in roots])
        rsg = np.array([s for _, s in roots], float)
        uniq, inv = np.unique(rid, return_inverse=True)
        Pro = _coo((rsg, (np.arange(N2), inv)),
                   shape=(N2, uniq.size)).tocsr()
        return Pro, np.unique(inv[self.D_ids])
