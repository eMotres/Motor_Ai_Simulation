"""3D magnetostatics on tets by the VECTOR potential, on Nedelec edge elements.

Why this module exists, and what it turned out to be for
--------------------------------------------------------
It was written to test one hypothesis and it falsified it.

``solver.py`` solves the same physics with the TOTAL magnetic scalar potential.
The suspicion was that it must lose accuracy inside high-mu iron, because it
recovers B = -mu grad(phi) as a huge permeability times a tiny gradient; Stage A
had measured a 3D-vs-2D residual on the 40 mm machine that grew with mu_r and
vanished without iron, which is what that would look like.  This module is the
other formulation, whose primary unknown is B itself (through its curl), so the
amplification cannot be there.

Measured, on a magnetised sphere inside an iron shell where the answer is known
in closed form at EVERY permeability (``exact.sphere_in_shell_B``), from
mu_r = 1 to mu_r = 1e6: NEITHER formulation degrades.  Every error — in the
iron most of all — is flat to within about 15 % of itself once mu_r > 10, and
the scalar P2 is roughly six times MORE accurate in the iron than the lowest
order edge element on the same mesh.  The suspicion had the two scalar
potentials swapped: it is the REDUCED potential that cancels in iron (its H must
annihilate a large applied field), which is exactly why the total potential is
the standard cure and not the disease.  The 40 mm residual was the cross-section
mesh's grip on the saturating rotor bridges — see
``config/end_effect_3d.json::consistency_investigation``.

So this module is NOT the fix for that, and Stage A's scalar solver stays.  What
it IS for is the thing the total scalar potential genuinely cannot do: carry a
CURRENT.  curl H = J makes the total potential multivalued around any current
loop, and torque under load is a current problem.  The current-sheet benchmark
below (``benchmarks.nedelec_coil_case``) is the one that matters for Stage B.

Formulation
-----------
B = curl(A) satisfies div B = 0 identically, whatever A is.  Ampere's law
curl H = J then gives, with the constitutive law below,

    curl( nu curl A )  =  J  +  curl( nu mu0 M )

and the weak form on H(curl), with v tangentially vanishing on the Dirichlet
part of the boundary,

    integral( nu curl(A) . curl(v) )  =  integral( J . v )
                                       + integral( (M / mu_r) . curl(v) )

THE CONSTITUTIVE CONVENTION, stated once and tested (see the module tests):

    B = mu0 * mu_r * H + mu0 * M ,      remanence Br = mu0 * M ,
    so  H = nu * (B - mu0 M) ,          nu = 1 / (mu0 * mu_r) ,

which is the project's law — the same one ``solver.py`` integrates and the same
one ``fem_solver_2d.build_materials`` means by ``Mx``/``My``.  Feeding it into
the A-formulation makes the magnet source the EQUIVALENT COERCIVITY

    nu * mu0 * M  =  M / mu_r      (A/m),

not M.  Assembling M itself would model a magnet of remanence mu_r*Br — 5 % too
strong for NdFeB — and every benchmark written at mu_r = 1 would still pass,
because mu_r = 1 is precisely where the two conventions agree.  That is not a
hypothetical: it is commit 40b7de7, where the 2D solver had assembled M for a
whole stage.  So ``tests/test_static3d_nedelec.py`` pins the law at mu_r != 1,
as a RATIO between two solves on the same mesh, where the discretisation error
cancels and mu_rec is the only thing the ratio can see.

Boundary conditions, and how they map onto the scalar solver's
-------------------------------------------------------------
The two formulations' boundary conditions are DUALS of each other, and getting
this backwards silently solves a different problem:

    A x n = 0 on a surface   <=>   B . n = 0 there   (flux-tight wall)
                                   == the scalar solver's dphi/dn = 0
    natural (do nothing)     <=>   n x H = 0, i.e. B normal to the surface
                                   == the scalar solver's phi = 0

So the truncation bracket flips sides between the two modules, and an axial
MIRROR plane (B_z = 0, which the scalar solver gets for free as its natural
condition) is a DIRICHLET condition here.  ``mirror_plane_dofs`` exists for
exactly that.

Gauge
-----
curl(grad p) = 0, so the discrete operator has the whole space of nodal
gradients in its null space and the matrix is singular.  Two ways out, both
implemented, because which one is needed is a measurement and not a preference:

``gauge="none"``
    Leave the system singular and solve it with CG.  This is legitimate — not a
    trick — because the right-hand side is CONSISTENT: the magnet term is
    ``integral( Hc . curl v )``, which vanishes identically for v = grad p since
    the discrete gradient of a P1 nodal function has exactly zero curl in the
    Nedelec space.  CG never leaves the range it starts in, so the null-space
    component is whatever the initial guess had (zero), and curl(A) — the only
    thing anyone reads — is unaffected.  A genuine J is consistent only if it is
    discretely solenoidal; ``source_consistency`` measures that and the solver
    reports it rather than letting a bad J show up as a stalled iteration.

``gauge="tree"``
    Tree-cotree: pin A to zero on a spanning tree of the mesh graph (with all
    Dirichlet-constrained vertices contracted to one node first).  That removes
    the null space exactly — one constraint per free vertex — and leaves a
    nonsingular SPD system a direct factorisation can eat, which on the
    high-contrast iron problems is worth far more than it costs.  The cotree
    system is still an A-formulation; nothing about B changes.

Only the LOWEST-order element is available: in scikit-fem 12.0.1
``ElementTetN1`` IS ``ElementTetN0`` (both are the first-kind Nedelec edge
element, one dof per edge, ``maxdeg = 1``), so curl(A) is piecewise CONSTANT and
B is first-order accurate.  That is not a choice this module made and it is the
dominant term in every accuracy number it reports — see ``nedelec_bench``.

Units: SI throughout, metres, A/m, tesla.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence, Union

import numpy as np
from skfem import Basis, BilinearForm, LinearForm, asm, condense
from skfem.element import ElementTetN0
from skfem.helpers import curl, dot

from .solver import MU0, Region

#: What a caller may hand in as a current density: nothing, a piecewise-constant
#: (3, nelem) array, or a callable evaluated at the quadrature points and given
#: their global coordinates (3, nelem, nqp).
JSource = Union[None, np.ndarray, Callable[[np.ndarray], np.ndarray]]


# --------------------------------------------------------------------------
# forms
# --------------------------------------------------------------------------

@BilinearForm
def _curlcurl(u, v, w):
    return w["nu"] * dot(curl(u), curl(v))


@LinearForm
def _magsrc(v, w):
    # (M / mu_r) . curl(v) — the equivalent coercivity, see the module docstring
    return dot(w["Hc"], curl(v))


@LinearForm
def _cursrc(v, w):
    return dot(w["J"], v)


@BilinearForm
def _mass(u, v, w):
    return w["nu"] * dot(u, v)


# --------------------------------------------------------------------------
# material arrays
# --------------------------------------------------------------------------

def _region_arrays(mesh, regions: Sequence[Region]):
    """(nu, Hc) per element: reluctivity and equivalent coercivity M/mu_r.

    Deliberately built from the SAME ``Region`` objects the scalar solver takes,
    so a benchmark can hand both formulations one list and no reading of the
    material data can differ between them.
    """
    n = mesh.t.shape[1]
    nu = np.full(n, 1.0 / MU0)
    Hc = np.zeros((3, n))
    covered = np.zeros(n, dtype=bool)
    for r in regions:
        idx = np.asarray(r.elements, dtype=np.int64)
        mu_r = float(r.mu_r)
        if mu_r <= 0.0:
            raise ValueError(f"region {r.name!r} has mu_r = {mu_r}")
        if getattr(r, "laminated", False):
            # This assembly carries a SCALAR nu.  Silently dropping stack_kf
            # would solve a solid block and then be compared against a scalar
            # solve of a laminated one — and on this machine that difference is
            # 2.5 % of the gap fundamental, i.e. bigger than the disagreement
            # the two formulations exist to bracket.
            raise NotImplementedError(
                f"region {r.name!r} is a lamination stack (k_f = "
                f"{r.stack_kf:.3f}) and the A-formulation here assembles an "
                "ISOTROPIC nu. Solve it with the scalar potential, or make "
                "this weak form carry the diagonal nu too — do not compare a "
                "solid-iron A-solve against a laminated scalar solve.")
        nu[idx] = 1.0 / (MU0 * mu_r)
        Hc[:, idx] = (np.asarray(r.M, dtype=float) / mu_r)[:, None]
        covered[idx] = True
    if not covered.all():
        raise ValueError(
            f"{int((~covered).sum())} of {n} elements belong to no Region — "
            "every element must carry a material or the field it sees is a "
            "silent guess")
    return nu, Hc


# --------------------------------------------------------------------------
# solution object
# --------------------------------------------------------------------------

@dataclass
class ASolution:
    """A solved edge-dof vector potential plus everything to interrogate it."""
    A: np.ndarray
    basis: object
    mesh: object
    regions: List[Region]
    nu_el: np.ndarray               # (nelem,) reluctivity actually used
    Hc_el: np.ndarray               # (3, nelem) equivalent coercivity used
    ndofs: int
    solve_time: float
    assemble_time: float
    solver: str
    gauge: str
    iterations: Optional[int] = None
    source_residual: float = 0.0
    source_residual_projected: float = 0.0
    picard: dict = field(default_factory=dict)
    mu_converged: Optional[np.ndarray] = None
    _cache: Optional[tuple] = None
    _Bel: Optional[np.ndarray] = None

    # -- field recovery ----------------------------------------------------
    def B_elementwise(self) -> np.ndarray:
        """Element B = curl(A), (3, nelem).

        For the lowest-order Nedelec element curl(A) is CONSTANT on each element,
        so the quadrature average below is that constant exactly — there is no
        sub-element structure being averaged away, which is also the honest
        statement of this element's spatial resolution."""
        if self._Bel is None:
            cq = self.basis.interpolate(self.A).curl     # (3, nelem, nqp)
            wq = self.basis.dx
            vol = wq.sum(axis=1)
            self._Bel = (cq * wq[None]).sum(axis=2) / vol[None]
        return self._Bel

    def H_elementwise(self) -> np.ndarray:
        """H = nu (B - mu0 M) = nu B - Hc, elementwise."""
        return self.nu_el[None] * self.B_elementwise() - self.Hc_el

    def B_quadrature(self, elements: Optional[np.ndarray] = None):
        """(B, dx) at every quadrature point of (a subset of) the elements."""
        if elements is None:
            b = self.basis
        else:
            b = Basis(self.mesh, self.basis.elem,
                      elements=np.asarray(elements, dtype=np.int64))
        return b.interpolate(self.A).curl, b.dx

    def B_at_points(self, pts: np.ndarray) -> np.ndarray:
        """B at arbitrary points (3, N) -> (3, N).

        curl(A) is elementwise constant here, so this is a genuine point
        evaluation of the discrete field and not an interpolation of it — and
        equally, it is a STAIRCASE in space.  A profile sampled from it is
        exactly as smooth as the mesh, which is the honest picture; smoothing it
        would report an accuracy the element does not have."""
        pts = np.asarray(pts, dtype=float)
        cached = getattr(self, "_cache", None)
        if cached is None:
            mapping = self.mesh._mapping()
            cached = (mapping, self.mesh.element_finder(mapping))
            self._cache = cached
        _mapping, finder = cached
        tind = finder(pts[0], pts[1], pts[2]).astype(np.int64)
        return self.B_elementwise()[:, tind]

    # -- integrals ---------------------------------------------------------
    def _region(self, name: str) -> Region:
        for r in self.regions:
            if r.name == name:
                return r
        raise KeyError(name)

    def region_mean_B(self, name: str) -> np.ndarray:
        reg = self._region(name)
        B = self.B_elementwise()[:, reg.elements]
        vol = self.basis.dx[reg.elements].sum(axis=1)
        return (B * vol[None]).sum(axis=1) / vol.sum()

    def boundary_flux(self) -> float:
        """Net outward flux of B through the whole outer surface.

        For this formulation it is not a physics check but an IDENTITY check:
        B = curl(A) is divergence-free to round-off by construction, so anything
        other than ~0 here means the recovery or the facet integration is wrong,
        not that the physics is.  That is precisely why it is worth running — it
        isolates the plumbing from the model."""
        from skfem import FacetBasis
        fb = FacetBasis(self.mesh, self.basis.elem)
        B = fb.interpolate(self.A).curl
        n = fb.normals
        return float(((B * n).sum(axis=0) * fb.dx).sum())


# --------------------------------------------------------------------------
# gauge
# --------------------------------------------------------------------------

def tree_dofs(mesh, dirichlet_dofs: Optional[np.ndarray] = None) -> np.ndarray:
    """Edge dofs forming a spanning tree — the tree-cotree gauge constraint set.

    Every vertex touched by a Dirichlet edge is contracted into ONE supernode
    first (those edges already carry A = 0, so their endpoints are at the same
    "potential" as far as the gauge freedom is concerned), and the tree is then
    grown over the remaining graph.  Without that contraction the tree would
    fight the boundary condition on a closed box and the gauged system would be
    over-determined.

    Kruskal with union-find, no weights: an edge joins the tree iff it connects
    two components.  The result is a spanning FOREST if the mesh graph is
    disconnected, which is the right answer in that case too.

    For the lowest-order element the dof index IS the edge index (checked in
    ``solve_static3d_A``), so the returned indices are usable directly.
    """
    edges = np.asarray(mesh.edges)                    # (2, nE)
    nv = int(mesh.p.shape[1])
    parent = np.arange(nv + 1, dtype=np.int64)        # nv = the supernode
    SUPER = nv

    def find(a: int) -> int:
        root = a
        while parent[root] != root:
            root = parent[root]
        while parent[a] != root:                      # path compression
            parent[a], a = root, parent[a]
        return root

    def union(a: int, b: int) -> bool:
        ra, rb = find(a), find(b)
        if ra == rb:
            return False
        # keep the supernode as the root so it never gets re-parented
        if rb == SUPER:
            ra, rb = rb, ra
        parent[rb] = ra
        return True

    e0 = edges[0].astype(np.int64)
    e1 = edges[1].astype(np.int64)
    if dirichlet_dofs is not None and np.asarray(dirichlet_dofs).size:
        D = np.asarray(dirichlet_dofs, dtype=np.int64)
        for k in D:
            union(SUPER, int(e0[k]))
            union(SUPER, int(e1[k]))
        skip = np.zeros(edges.shape[1], dtype=bool)
        skip[D] = True
    else:
        skip = np.zeros(edges.shape[1], dtype=bool)

    tree: List[int] = []
    for k in range(edges.shape[1]):
        if skip[k]:
            continue
        if union(int(e0[k]), int(e1[k])):
            tree.append(k)
    return np.asarray(tree, dtype=np.int64)


def discrete_gradient(mesh, dirichlet_dofs: Optional[np.ndarray] = None):
    """(G, free_nodes): the incidence matrix whose columns span null(curl-curl).

    ``G[e, v] = +1`` at an edge's higher-numbered endpoint and ``-1`` at its
    lower one, which is exactly the coefficient vector of ``grad(hat_v)`` in the
    lowest-order Nedelec space — so ``curl(G psi) = 0`` to round-off for every
    nodal psi, and ``K G = 0`` exactly.

    Every vertex touched by a Dirichlet edge is dropped from the column set (the
    same contraction the tree gauge does, for the same reason): those are the
    gradients the boundary condition has already removed, and keeping them would
    project away part of the load the problem legitimately has.
    """
    from scipy.sparse import csr_matrix
    edges = np.asarray(mesh.edges)
    nE = edges.shape[1]
    nV = int(mesh.p.shape[1])
    rows = np.concatenate([np.arange(nE), np.arange(nE)])
    cols = np.concatenate([edges[1], edges[0]])
    vals = np.concatenate([np.ones(nE), -np.ones(nE)])
    G = csr_matrix((vals, (rows, cols)), shape=(nE, nV))
    if dirichlet_dofs is None or np.asarray(dirichlet_dofs).size == 0:
        return G, np.arange(nV, dtype=np.int64)
    D = np.asarray(dirichlet_dofs, dtype=np.int64)
    pinned = np.zeros(nV, dtype=bool)
    pinned[edges[0][D]] = True
    pinned[edges[1][D]] = True
    free = np.flatnonzero(~pinned).astype(np.int64)
    return G[:, free], free


def source_consistency(f, mesh, dirichlet_dofs=None) -> float:
    """||G^T f|| / (sqrt(2) ||f||): how far the load vector is from the range.

    A magnet source is consistent to round-off BY CONSTRUCTION — its load is
    ``integral(Hc . curl v)``, which is zero for every v = grad(psi) because the
    discrete gradient has exactly zero curl.  A hand-built current density is
    consistent only if it is discretely solenoidal, and quadrature on a curved
    e_phi is not exact, so it never quite is.  An ungauged CG then does not
    converge at all — measured: 40000 iterations without convergence at a
    residual of 9e-3 on the coil benchmark.  Reporting the number is what turns
    that stall into a diagnosis.
    """
    G, _ = discrete_gradient(mesh, dirichlet_dofs)
    r = float(np.linalg.norm(G.T @ np.asarray(f, dtype=float)))
    return r / max(float(np.linalg.norm(f)) * math.sqrt(2.0), 1e-300)


def project_source(f, mesh, dirichlet_dofs: Optional[np.ndarray] = None
                   ) -> np.ndarray:
    """Remove the discrete-gradient component of the load: f <- f - G psi.

    Solve the graph Laplacian ``G^T G psi = G^T f`` and subtract.  Afterwards
    ``G^T f = 0`` to round-off, so the load is exactly in the range of the
    singular curl-curl operator and CG converges on the ungauged system.

    This does NOT change the physics.  ``K G = 0``, so ``G psi`` is a null-space
    vector of the constrained system (the contraction in ``discrete_gradient``
    is what makes its Dirichlet entries vanish, which is what makes that true);
    subtracting it moves the load onto the range without moving the solution's
    curl.  What it removes is the part of a discretised current that is not
    divergence-free — i.e. the quadrature's opinion that charge is piling up
    somewhere, which no magnetostatic solution can satisfy and which would
    otherwise be silently absorbed by whatever gauge is imposed.
    """
    import scipy.sparse as sp
    from scipy.sparse.csgraph import connected_components

    G, free = discrete_gradient(mesh, dirichlet_dofs)
    if G.shape[1] == 0:
        return np.asarray(f, dtype=float)
    L = (G.T @ G).tocsr()
    rhs = G.T @ np.asarray(f, dtype=float)

    # L is the graph Laplacian of the FREE nodes, and it is singular by one
    # constant per connected component that has no edge to a contracted
    # (Dirichlet) node.  Components that DO touch one are already grounded and
    # must not be pinned — pinning them anyway leaves exactly the residual this
    # function exists to remove, which is how the first version of it only got
    # the coil source from 9.3e-3 down to 1.6e-4 instead of to round-off.
    #
    # A free node is grounded iff it has fewer free neighbours than edges: the
    # diagonal of L counts ALL its edges, the off-diagonals only the free ones.
    off = L - sp.diags(L.diagonal())
    nfree_nb = np.asarray(abs(off).sum(axis=1)).ravel()
    grounded = L.diagonal() > nfree_nb + 0.5
    ncomp, lab = connected_components(L, directed=False)
    pin: List[int] = []
    for c in range(ncomp):
        idx = np.flatnonzero(lab == c)
        if not grounded[idx].any():
            pin.append(int(idx[0]))
    keep = np.setdiff1d(np.arange(L.shape[0], dtype=np.int64),
                        np.asarray(pin, dtype=np.int64))
    psi = np.zeros(L.shape[0])
    if keep.size:
        psi[keep], _ = _direct(L[keep][:, keep].tocsr(), rhs[keep])
    return np.asarray(f, dtype=float) - G @ psi


# --------------------------------------------------------------------------
# linear solve
# --------------------------------------------------------------------------

#: Weight of the regularising mass term in the CG preconditioner, as a fraction
#: of the median diag(K)/diag(M) ratio.  Measured on the 40 mm sector at
#: mu_r = 4600, ungauged, anti-periodic (60394 free dofs): Jacobi needed 17348
#: iterations; this preconditioner at 1e-2 / 1e-3 / 1e-4 needed 152 / 49 / 17.
#: Smaller is better for the iteration count and worse for the conditioning of
#: the factorisation that provides it, and 1e-4 is where those cross on this
#: family of problems.
REG_PRECOND_C = 1e-4


def _regularised_preconditioner(A, Mm, c: float = REG_PRECOND_C):
    """A factorisation of ``K + eps M`` used ONLY as a preconditioner.

    The ungauged curl-curl matrix is singular — its whole gradient null space
    sits at eigenvalue zero — and a Jacobi-preconditioned CG has to crawl
    through the resulting spectrum.  Adding a small multiple of the mass matrix
    lifts that null space away from zero and makes the result factorisable; used
    as a PRECONDITIONER rather than as the operator, it costs one factorisation
    and changes nothing about the answer, because CG is still iterating on the
    true singular system.  (Solving ``K + eps M`` directly instead would be the
    penalty gauge, and it would perturb B by O(eps).  This does not.)

    ``eps`` is set from the median ratio of the two diagonals, so it carries the
    problem's own units and scales and needs no per-geometry tuning.
    """
    dK = np.asarray(A.diagonal(), dtype=float)
    dM = np.asarray(Mm.diagonal(), dtype=float)
    ok = dM > 0
    if not ok.any():
        return None
    eps = float(c) * float(np.median(dK[ok] / dM[ok]))
    Areg = (A + eps * Mm).tocsr()
    try:
        from pypardiso import PyPardisoSolver
        ps = PyPardisoSolver()
        ps.factorize(Areg)

        def _apply(v):
            return ps.solve(Areg, np.ascontiguousarray(v, dtype=float))

        def _release():
            # MKL PARDISO holds its factorisation in memory OUTSIDE the Python
            # heap, so letting the solver object fall out of scope does not
            # return it.  A Picard loop builds one of these per sweep; without
            # this the 40 mm nonlinear sector run reached 11 GB by sweep 33 and
            # was heading for the machine's limit.  Measured, not feared.
            try:
                ps.free_memory(everything=True)
            except Exception:
                pass
        return _apply, _release
    except Exception:
        try:
            from scipy.sparse.linalg import splu
            lu = splu(Areg.tocsc())
            return lu.solve, lambda: None
        except Exception:
            return None, None


def _cg(A, b, tol: float = 1e-10, maxiter: int = 40000, Mm=None):
    from scipy.sparse.linalg import LinearOperator, cg
    Ac = A.tocsr()
    apply, release = (_regularised_preconditioner(Ac, Mm) if Mm is not None
                      else (None, None))
    if apply is None:
        d = Ac.diagonal()
        d[np.abs(d) < 1e-300] = 1.0
        Mop = LinearOperator(Ac.shape, matvec=lambda v: v / d)
    else:
        Mop = LinearOperator(Ac.shape, matvec=apply)
    it = [0]
    try:
        try:
            x, info = cg(Ac, b, rtol=tol, atol=0.0, maxiter=maxiter, M=Mop,
                         callback=lambda _x: it.__setitem__(0, it[0] + 1))
        except TypeError:                               # scipy < 1.12
            x, info = cg(Ac, b, tol=tol, atol=0.0, maxiter=maxiter, M=Mop,
                         callback=lambda _x: it.__setitem__(0, it[0] + 1))
    finally:
        if release is not None:
            release()
    return x, int(it[0]), int(info)


def _direct(A, b):
    try:
        from pypardiso import spsolve as _ps
        return np.asarray(_ps(A.tocsr(), np.asarray(b, dtype=float))), \
            "pypardiso(MKL PARDISO)"
    except Exception:
        from scipy.sparse.linalg import splu
        return splu(A.tocsc()).solve(b), "scipy.splu(SuperLU)"


def solve_static3d_A(mesh, regions: Sequence[Region],
                     J: JSource = None,
                     basis: Optional[object] = None,
                     dirichlet_dofs: Optional[np.ndarray] = None,
                     natural_outer: bool = False,
                     periodic: Optional[object] = None,
                     gauge: str = "tree",
                     linear_solver: Optional[str] = None,
                     nu_el: Optional[np.ndarray] = None,
                     project: bool = True,
                     cg_tol: float = 1e-10,
                     cg_maxiter: int = 40000) -> ASolution:
    """One linear magnetostatic solve in the A-formulation on Nedelec edges.

    ``regions``   the SAME ``solver.Region`` list the scalar solver takes.
    ``J``         optional current density: ``None``, a (3, nelem) piecewise
                  constant array, or ``f(x) -> (3, nelem, nqp)`` evaluated at the
                  quadrature points (which is what an azimuthal coil needs, since
                  e_phi is not piecewise constant).
    ``dirichlet_dofs``  clamp exactly these dofs (A.t = 0, i.e. B.n = 0 there).
                  Default: the whole mesh boundary.
    ``natural_outer``   clamp NOTHING on the outer boundary, i.e. n x H = 0,
                  i.e. B normal to it.  This is the dual of the scalar solver's
                  ``phi = 0`` and the two together bracket the truncation error
                  from opposite sides — see the module docstring.
    ``gauge``     ``"tree"`` (tree-cotree, nonsingular, direct factorisation) or
                  ``"none"`` (singular but consistent, CG).
    ``project``   remove the discrete-gradient component of the load before
                  solving (see ``project_source``).  A no-op for magnets, which
                  are consistent to round-off already; REQUIRED for a current
                  source, whose quadrature is not exactly solenoidal.
    ``nu_el``     per-element reluctivity injected by the Picard driver.
    ``cg_tol`` / ``cg_maxiter``
                  the LINEAR solver's tolerance, named apart from the Picard
                  driver's ``tol`` on purpose: they are different convergence
                  tests on different quantities, and a single ``tol`` forwarded
                  through ``**kw`` silently collides with the other one.
    """
    if gauge not in ("tree", "none"):
        raise ValueError(f"gauge must be 'tree' or 'none', got {gauge!r}")
    if basis is None:
        basis = Basis(mesh, ElementTetN0())
    if int(basis.N) != int(mesh.edges.shape[1]):
        raise RuntimeError(
            f"{basis.N} dofs for {mesh.edges.shape[1]} edges — this module's "
            "tree gauge and periodic pairing both assume one dof per edge, in "
            "edge order, which is what the lowest-order Nedelec element gives")

    nu_default, Hc_el = _region_arrays(mesh, regions)
    nu_used = nu_default if nu_el is None else np.asarray(nu_el, dtype=float)
    if nu_used.shape != (mesh.t.shape[1],):
        raise ValueError("nu_el must be one value per element")

    nqp = basis.dx.shape[1]
    nu_qp = np.repeat(nu_used[:, None], nqp, axis=1)
    t0 = time.perf_counter()
    K = asm(_curlcurl, basis, nu=nu_qp)
    Mm = asm(_mass, basis, nu=nu_qp) if gauge == "none" else None
    f = asm(_magsrc, basis, Hc=np.repeat(Hc_el[:, :, None], nqp, axis=2))
    if J is not None:
        if callable(J):
            Jq = np.asarray(J(np.asarray(basis.global_coordinates())),
                            dtype=float)
        else:
            Jq = np.repeat(np.asarray(J, dtype=float)[:, :, None], nqp, axis=2)
        if Jq.shape != (3, mesh.t.shape[1], nqp):
            raise ValueError(
                f"J must evaluate to (3, {mesh.t.shape[1]}, {nqp}), got "
                f"{Jq.shape}")
        f = f + asm(_cursrc, basis, J=Jq)
    t_asm = time.perf_counter() - t0

    if natural_outer:
        D = np.empty(0, dtype=np.int64)
    elif dirichlet_dofs is not None:
        D = np.unique(np.asarray(dirichlet_dofs, dtype=np.int64))
    else:
        D = basis.get_dofs().all()

    resid = source_consistency(f, mesh, D)
    if project and resid > 1e-12:
        f = project_source(f, mesh, D)
        resid_after = source_consistency(f, mesh, D)
    else:
        resid_after = resid

    name = ""
    iters = None
    t0 = time.perf_counter()
    if periodic is not None and getattr(periodic, "masters", np.empty(0)).size:
        from .periodic import elimination
        T, full2red = elimination(int(basis.N), periodic)
        Kr = (T.T @ K @ T).tocsr()
        fr = T.T @ f
        Dr = full2red[D] if D.size else np.empty(0, dtype=np.int64)
        Dr = np.unique(Dr[Dr >= 0]) if Dr.size else Dr
        if gauge == "tree":
            raise NotImplementedError(
                "tree-cotree on a periodically reduced system is not "
                "implemented: the eliminated system's null space is the "
                "gradients of ANTI-periodic nodal fields, and a spanning tree "
                "of the un-reduced mesh graph does not span it. Use "
                "gauge='none' (CG) for sector models — it is consistent there "
                "for the same reason it is consistent anywhere.")
        Mr = (T.T @ Mm @ T).tocsr()
        if Dr.size:
            Ar, br, xr, I = condense(Kr, fr, D=Dr)
            Mrc = Mr[I][:, I].tocsr()
        else:
            Ar, br = Kr, fr
            xr = np.zeros(Kr.shape[0])
            I = np.arange(Kr.shape[0])
            Mrc = Mr
        xI, iters, info = _cg(Ar, br, tol=cg_tol, maxiter=cg_maxiter,
                              Mm=Mrc)
        name = f"scipy.cg(reg-precond)[{iters} it, info={info}]"
        if info != 0:
            raise RuntimeError(
                f"CG did not converge on the periodic system (info={info}, "
                f"{iters} iterations, source residual {resid:.2e})")
        xr = np.asarray(xr, dtype=float)
        xr[I] = xI
        x = T @ xr
    else:
        if gauge == "tree":
            Dall = np.unique(np.concatenate([D, tree_dofs(mesh, D)]))
            Ac, b, x, I = condense(K, f, D=Dall)
            if linear_solver == "cg":
                xI, iters, info = _cg(Ac, b, tol=cg_tol, maxiter=cg_maxiter)
                name = f"scipy.cg(jacobi)[{iters} it, info={info}]"
                if info != 0:
                    raise RuntimeError(f"CG did not converge (info={info})")
            else:
                xI, name = _direct(Ac, b)
            x[I] = xI
        else:
            Ac, b, x, I = condense(K, f, D=D)
            xI, iters, info = _cg(Ac, b, tol=cg_tol, maxiter=cg_maxiter,
                                  Mm=Mm[I][:, I].tocsr())
            name = f"scipy.cg(reg-precond)[{iters} it, info={info}]"
            if info != 0:
                raise RuntimeError(
                    f"CG did not converge (info={info}, {iters} iterations, "
                    f"source residual {resid:.2e}) — a source that is not "
                    "discretely solenoidal is the usual cause; gauge='tree' "
                    "removes the null space instead of tolerating it")
            x[I] = xI
    t_solve = time.perf_counter() - t0

    return ASolution(A=x, basis=basis, mesh=mesh, regions=list(regions),
                     nu_el=nu_used, Hc_el=Hc_el, ndofs=int(basis.N),
                     solve_time=t_solve, assemble_time=t_asm, solver=name,
                     gauge=gauge, iterations=iters, source_residual=resid,
                     source_residual_projected=resid_after)


# --------------------------------------------------------------------------
# nonlinear (Picard) solve
# --------------------------------------------------------------------------

def solve_static3d_A_nonlinear(mesh, regions: Sequence[Region],
                               tol: float = 1e-3, max_iter: int = 60,
                               damping: float = 0.5, verbose: bool = False,
                               mu_init: Optional[np.ndarray] = None,
                               **kw) -> ASolution:
    """Damped Picard on mu_r(|B|), the A-formulation twin of
    ``solver.solve_static3d_nonlinear``.

    Same B-H inversion (the 2D solver's own ``_mu_r_from_bh_vec``), same
    geometric damping with the same adaptive halving, and the same
    damping-independent CONVERGENCE TEST — the constitutive residual in H, not
    the step size.  The reasons are in ``solver.py``'s docstring and none of them
    are formulation specific; what IS specific is that here the state carried
    between sweeps is nu = 1/(mu0 mu_r), so a permeability handed in or out is
    converted at the boundary and the two drivers can warm-start each other.
    """
    from motor_ai_sim.simulation.field_ops import _mu_r_from_bh_vec

    nl = [r for r in regions if r.nonlinear]
    if not nl:
        return solve_static3d_A(mesh, regions, **kw)
    nlidx = np.concatenate([np.asarray(r.elements, dtype=np.int64) for r in nl])

    nu_def, _ = _region_arrays(mesh, regions)
    mu_el = 1.0 / nu_def
    if mu_init is not None:
        mu_init = np.asarray(mu_init, dtype=float)
        if mu_init.shape != mu_el.shape:
            raise ValueError("mu_init must be one value per element")
        mu_el = mu_init.copy()

    hist: List[float] = []
    mu_hist: List[float] = []
    sol: Optional[ASolution] = None
    t_start = time.perf_counter()
    converged = False
    d_cur = float(damping)
    d_floor, good, prev_rel = 0.02, 0, float("inf")
    for it in range(max_iter):
        sol = solve_static3d_A(mesh, regions, nu_el=1.0 / mu_el, **kw)
        kw["basis"] = sol.basis
        Bmag = np.linalg.norm(sol.B_elementwise(), axis=0)
        w = sol.basis.dx.sum(axis=1)[nlidx]

        mu_new = mu_el.copy()
        for r in nl:
            idx = np.asarray(r.elements, dtype=np.int64)
            mur = np.maximum(_mu_r_from_bh_vec(r.bh_curve, Bmag[idx]), 1.0)
            mu_new[idx] = MU0 * mur
        mu_hist.append(float(np.max(np.abs(mu_new[nlidx] - mu_el[nlidx])
                                    / np.maximum(mu_el[nlidx], 1e-30))))

        bm = Bmag[nlidx]
        h_curve = bm / mu_new[nlidx]
        h_used = bm / mu_el[nlidx]
        num = float((((h_curve - h_used) ** 2) * w).sum())
        den = float(((h_curve ** 2) * w).sum())
        rel = math.sqrt(num / max(den, 1e-300))
        hist.append(rel)
        if verbose:
            print(f"  picard {it:2d}  |dH|/|H| = {rel:.3e}   d = {d_cur:.3f}",
                  flush=True)
        if rel < tol:
            converged = True
            break
        if rel > prev_rel:
            d_cur = max(0.5 * d_cur, d_floor)
            good = 0
        else:
            good += 1
            if good >= 3:
                d_cur, good = min(1.6 * d_cur, float(damping)), 0
        prev_rel = rel
        mu_el = np.exp((1.0 - d_cur) * np.log(mu_el) + d_cur * np.log(mu_new))

    sol = solve_static3d_A(mesh, regions, nu_el=1.0 / mu_el, **kw)
    sol.picard = dict(iterations=len(hist), history=hist, mu_history=mu_hist,
                      converged=converged, tol=tol, damping=damping,
                      damping_final=d_cur,
                      warm_started=bool(mu_init is not None),
                      wall_time=time.perf_counter() - t_start)
    sol.mu_converged = mu_el
    return sol


# --------------------------------------------------------------------------
# boundary-dof helpers
# --------------------------------------------------------------------------

def mirror_plane_dofs(basis, z: float = 0.0, atol: float = 1e-9) -> np.ndarray:
    """Edge dofs lying IN the plane z = const.

    An axial mirror plane of a machine whose magnetisation is in-plane carries
    B_z = 0.  For the scalar potential that is the natural condition and costs
    nothing; here it is A x n = 0, i.e. every edge whose BOTH endpoints sit on
    the plane must be clamped.  Edges merely touching the plane at one end have
    a tangential component out of it and must not be.
    """
    e = np.asarray(basis.mesh.edges)
    p = np.asarray(basis.mesh.p)
    on = np.abs(p[2] - float(z)) <= atol
    return np.flatnonzero(on[e[0]] & on[e[1]]).astype(np.int64)


def truncation_dofs(basis, r_box: float, z_box: float,
                    rtol: float = 1e-3) -> np.ndarray:
    """Edge dofs on the TRUNCATION surface r = r_box or z = z_box.

    The A-formulation counterpart of ``periodic.outer_boundary_dofs``, and it
    means the opposite thing: clamping here is the FLUX-TIGHT box (B.n = 0),
    which is the scalar solver's Neumann case.  Leaving it unclamped is the
    scalar solver's phi = 0.
    """
    e = np.asarray(basis.mesh.edges)
    p = np.asarray(basis.mesh.p)
    r = np.hypot(p[0], p[1])
    on = (r > (1.0 - rtol) * r_box) | (p[2] > (1.0 - rtol) * z_box)
    sel = np.flatnonzero(on[e[0]] & on[e[1]]).astype(np.int64)
    if sel.size == 0:
        raise RuntimeError("no truncation edges found — check r_box / z_box")
    return sel


# --------------------------------------------------------------------------
# ready-made current sources
# --------------------------------------------------------------------------

def azimuthal_J(elements: np.ndarray, n_elem: int, J_phi: float
                ) -> Callable[[np.ndarray], np.ndarray]:
    """A genuine current density J = J_phi * e_phi inside ``elements``.

    Returned as a CALLABLE evaluated at the quadrature points, not as a
    piecewise-constant element array, and that is the whole point: e_phi turns
    with position, so a per-element constant would be a different (and not
    divergence-free) source.  As written, div J = 0 pointwise and J.n = 0 on
    every face of an annulus, so the load vector is consistent up to quadrature
    — ``ASolution.source_residual`` reports how far up.
    """
    sel = np.zeros(int(n_elem), dtype=bool)
    sel[np.asarray(elements, dtype=np.int64)] = True

    def _J(x: np.ndarray) -> np.ndarray:
        r = np.hypot(x[0], x[1])
        r = np.where(r > 0.0, r, 1.0)
        out = np.zeros_like(x)
        out[0] = -x[1] / r
        out[1] = x[0] / r
        return out * (float(J_phi) * sel[:, None][None])

    return _J
