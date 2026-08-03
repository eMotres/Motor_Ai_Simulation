"""3D magnetostatics on tets by the TOTAL magnetic scalar potential.

Formulation
-----------
There is no current anywhere in this problem (permanent magnets only), so
curl H = 0 on the whole domain and H derives from a single-valued scalar
potential:

    H = -grad(phi)

The constitutive law, region by region, is

    B = mu * H + mu0 * M ,        mu = mu0 * mu_r

with M the permanent magnetisation in A/m (zero outside the magnets) and mu_r
the recoil permeability in a magnet, the B-H permeability in iron, 1 in air.
Feeding that into div B = 0:

    div( mu grad phi ) = div( mu0 M )

Weak form, with v vanishing on the outer boundary:

    integral( mu grad(phi) . grad(v) )  =  integral( mu0 M . grad(v) )

The right-hand side is a VOLUME integral of a piecewise-constant M.  Integrating
it by parts is what produces the classical magnetic surface charge sigma = M.n
on the magnet faces plus the volume charge -div M inside; keeping it in the
volume form means the code never has to find, orient or integrate over those
faces, and a piecewise-constant M contributes exactly its surface charge because
the mesh is conformal across the magnet boundary.  That conformity is a hard
requirement, not a nicety — see ``meshes.py``.

Why the total scalar potential and not a reduced potential or a vector
potential:
  * ONE unknown per node instead of three, which on a 3D motor mesh is the
    difference between a solve that fits in memory and one that does not;
  * no gauge, no null space, no tree-cotree bookkeeping — the stiffness matrix
    is symmetric positive definite and PARDISO eats it;
  * the current-free condition makes the total potential single-valued, so the
    usual objection to it (multiply-connected current-carrying regions) does not
    apply to the magnet-only problem this spike is scoped to.
The known weakness is cancellation inside HIGH mu_r iron, where B = -mu grad phi
is a large permeability times a small gradient.  ``benchmarks.py`` exercises that
case; the linear benchmarks (magnet in air) are free of it.

Boundary condition: phi = 0 on the outer box.  That is an equipotential, i.e. it
forces B normal to the truncation surface.  The alternative natural condition
(dphi/dn = 0, flux-tight box) brackets the true answer from the other side.  The
truncation study in ``benchmarks.py`` measures how far out either has to sit.

Every number this module produces is checked against ``exact.py``.  There are no
calibration constants anywhere in this file, and there never will be.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import numpy as np
from skfem import (Basis, BilinearForm, ElementTetP1, ElementTetP2, LinearForm,
                   asm, condense)
from skfem.helpers import dot, grad

MU0 = 4e-7 * math.pi


# --------------------------------------------------------------------------
# forms
# --------------------------------------------------------------------------

@BilinearForm
def _stiff(u, v, w):
    return w["mu"] * dot(grad(u), grad(v))


@LinearForm
def _magsrc(v, w):
    return MU0 * dot(w["M"], grad(v))


# --------------------------------------------------------------------------
# regions
# --------------------------------------------------------------------------

@dataclass
class Region:
    """One material block: which elements, what mu, what magnetisation.

    ``bh_curve`` (a list of (H [A/m], B [T]) pairs, exactly the format the 2D
    solver's materials carry) makes the region NONLINEAR; ``mu_r`` is then only
    the starting guess for the Picard sweep.
    """
    name: str
    elements: np.ndarray
    mu_r: float = 1.0
    M: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    bh_curve: Optional[Sequence[Tuple[float, float]]] = None

    @property
    def nonlinear(self) -> bool:
        return bool(self.bh_curve) and len(self.bh_curve) >= 2


# --------------------------------------------------------------------------
# linear solve
# --------------------------------------------------------------------------

@dataclass
class Solution:
    """A solved potential plus everything needed to interrogate it."""
    phi: np.ndarray
    basis: object
    mesh: object
    regions: List[Region]
    mu_el: np.ndarray               # (nelem,) permeability actually used
    M_el: np.ndarray                # (3, nelem) magnetisation actually used
    order: int
    ndofs: int
    solve_time: float
    solver: str
    assemble_time: float = 0.0
    picard: dict = field(default_factory=dict)
    mu_converged: Optional[np.ndarray] = None
    _finder_cache: Optional[tuple] = None

    # -- field recovery ----------------------------------------------------
    def B_elementwise(self) -> np.ndarray:
        """Element-mean B (3, nelem), from B = -mu grad(phi) + mu0 M.

        For P1 the gradient is constant per element so this is exact for the
        discrete solution; for P2 it is the quadrature average, which is the
        honest element representative (the pointwise value is available through
        ``B_at_points``)."""
        gp = self.basis.interpolate(self.phi).grad     # (3, nelem, nqp)
        wq = self.basis.dx                             # (nelem, nqp)
        vol = wq.sum(axis=1)
        gmean = (gp * wq[None]).sum(axis=2) / vol[None]
        return -self.mu_el[None] * gmean + MU0 * self.M_el

    def B_quadrature(self, elements: Optional[np.ndarray] = None):
        """B at every quadrature point of (a subset of) the elements, with the
        matching integration weights: ((3, nel, nqp), (nel, nqp)).

        This is what the L2 error norms integrate — averaging to element means
        first would hide exactly the sub-element structure P2 is bought for."""
        if elements is None:
            b = self.basis
            idx = np.arange(self.mesh.t.shape[1])
        else:
            idx = np.asarray(elements, dtype=np.int64)
            b = Basis(self.mesh, self.basis.elem, elements=idx)
        gp = b.interpolate(self.phi).grad
        mu = self.mu_el[idx][:, None]
        M = self.M_el[:, idx][:, :, None]
        return -mu[None] * gp + MU0 * M, b.dx

    def B_at_points(self, pts: np.ndarray) -> np.ndarray:
        """B at arbitrary points (3, N) -> (3, N).

        Uses skfem's own element finder and reference-element gradients, so the
        P2 evaluation is the genuine quadratic field at that point and not a
        centroid stand-in.  Points outside the mesh come back as NaN."""
        pts = np.asarray(pts, dtype=float)
        # The element finder builds a KD-tree over every element; on a 170k-tet
        # motor mesh that is ~1 s, and the gap profile calls this once per axial
        # station.  Cache it — the mesh does not move.
        cached = getattr(self, "_finder_cache", None)
        if cached is None:
            mapping = self.mesh._mapping()
            cached = (mapping, self.mesh.element_finder(mapping))
            self._finder_cache = cached
        mapping, finder = cached
        tind = finder(pts[0], pts[1], pts[2]).astype(np.int64)

        X = mapping.invF(pts[:, :, None], tind=tind)   # (3, npts, 1) local
        elem = self.basis.elem
        edofs = self.basis.element_dofs[:, tind]       # (nbfun, npts)
        gphi = np.zeros((3, pts.shape[1]))
        for i in range(edofs.shape[0]):
            f = elem.gbasis(mapping, X, i, tind=tind)[0]
            gphi += f.grad[:, :, 0] * self.phi[edofs[i]]

        mu = self.mu_el[tind]
        M = self.M_el[:, tind]
        return -mu[None] * gphi + MU0 * M

    # -- integrals ---------------------------------------------------------
    def region_basis(self, name: str) -> object:
        reg = self._region(name)
        return Basis(self.mesh, self.basis.elem, elements=reg.elements)

    def _region(self, name: str) -> Region:
        for r in self.regions:
            if r.name == name:
                return r
        raise KeyError(name)

    def region_mean_B(self, name: str) -> np.ndarray:
        """Volume-averaged B vector over one region."""
        reg = self._region(name)
        B = self.B_elementwise()[:, reg.elements]
        vol = self.basis.dx[reg.elements].sum(axis=1)
        return (B * vol[None]).sum(axis=1) / vol.sum()

    def boundary_flux(self) -> float:
        """Net outward flux of B through the outer boundary.

        Physically it must be zero (div B = 0 in a closed surface); numerically
        it is a hard, exact-value check that needs no reference solution, which
        is why the nonlinear smoke test leans on it."""
        from skfem import FacetBasis
        fb = FacetBasis(self.mesh, self.basis.elem)
        gp = fb.interpolate(self.phi).grad              # (3, nfacet, nqp)
        tind = fb.tind
        mu = self.mu_el[tind][:, None]
        M = self.M_el[:, tind][:, :, None]
        B = -mu[None] * gp + MU0 * M
        n = fb.normals                                  # (3, nfacet, nqp)
        return float(((B * n).sum(axis=0) * fb.dx).sum())


def _linear_solver():
    """PARDISO if the project's pypardiso is importable, SuperLU otherwise.

    Returns (name, factory) where factory(A) -> solve(b)."""
    try:
        from pypardiso import spsolve as _pspsolve

        def _fac(A):
            Ac = A.tocsr()

            def _solve(b):
                return _pspsolve(Ac, np.asarray(b, dtype=float))
            return _solve
        return "pypardiso(MKL PARDISO)", _fac
    except Exception:
        from scipy.sparse.linalg import splu

        def _fac(A):
            lu = splu(A.tocsc())
            return lu.solve
        return "scipy.splu(SuperLU)", _fac


def _region_arrays(mesh, regions: Sequence[Region], mu_r_override=None):
    n = mesh.t.shape[1]
    mu = np.full(n, MU0)
    M = np.zeros((3, n))
    covered = np.zeros(n, dtype=bool)
    for r in regions:
        idx = np.asarray(r.elements, dtype=np.int64)
        mu[idx] = MU0 * r.mu_r
        M[:, idx] = np.asarray(r.M, dtype=float)[:, None]
        covered[idx] = True
    if not covered.all():
        raise ValueError(
            f"{int((~covered).sum())} of {n} elements belong to no Region — "
            "every element must carry a material or the field it sees is a "
            "silent guess")
    if mu_r_override is not None:
        mu = MU0 * mu_r_override
    return mu, M


def solve_static3d(mesh, regions: Sequence[Region], order: int = 1,
                   mu_el: Optional[np.ndarray] = None,
                   neumann_outer: bool = False,
                   basis: Optional[object] = None,
                   periodic: Optional[object] = None,
                   dirichlet_dofs: Optional[np.ndarray] = None,
                   linear_solver: Optional[str] = None) -> Solution:
    """Linear magnetostatic solve.  ``order`` is 1 (ElementTetP1) or 2 (P2).

    ``mu_el`` lets the nonlinear driver inject a per-element permeability that
    came out of a B-H curve; ``neumann_outer`` swaps the phi=0 truncation for a
    flux-tight box (pinned at one node to fix the constant), which is the other
    half of the truncation study.

    Stage A additions, all optional and all no-ops when omitted:

    ``basis``
        a prebuilt ``Basis`` to reuse.  Building the P2 basis of a 300k-element
        mesh is seconds; a Picard loop rebuilding it every sweep is minutes.
    ``periodic``
        a :class:`periodic.Periodic` — eliminate ``phi_slave = sign*phi_master``
        so a symmetry sector may be solved instead of the full ring.
    ``dirichlet_dofs``
        clamp exactly these dofs instead of the whole mesh boundary.  Required
        for any model whose boundary is not all truncation surface (mirror
        planes, periodic cuts).
    ``linear_solver``
        ``"direct"`` (default) or ``"cg"`` — diagonally preconditioned CG for
        systems past what a direct factorisation will hold.
    """
    if order not in (1, 2):
        raise ValueError(f"order must be 1 or 2, got {order}")
    elem = ElementTetP1() if order == 1 else ElementTetP2()
    if basis is None:
        basis = Basis(mesh, elem)

    mu_default, M_el = _region_arrays(mesh, regions)
    mu_used = mu_default if mu_el is None else np.asarray(mu_el, dtype=float)
    if mu_used.shape != (mesh.t.shape[1],):
        raise ValueError("mu_el must be one value per element")

    nqp = basis.dx.shape[1]
    mu_qp = np.repeat(mu_used[:, None], nqp, axis=1)
    M_qp = np.repeat(M_el[:, :, None], nqp, axis=2)

    t0 = time.perf_counter()
    K = asm(_stiff, basis, mu=mu_qp)
    f = asm(_magsrc, basis, M=M_qp)
    t_asm = time.perf_counter() - t0

    if neumann_outer:
        # Pure Neumann: the potential is defined up to a constant.  Pin ONE dof
        # (the node nearest the outer corner) instead of adding a Lagrange
        # multiplier, which would break the SPD structure PARDISO exploits.
        # An ANTI-periodic constraint already removes the constant (a nonzero
        # constant is not anti-periodic), so pinning on top of it would clamp a
        # real dof to a value the physics did not choose.
        if periodic is not None and getattr(periodic, "kills_constant", False):
            D = np.empty(0, dtype=np.int64)
        else:
            corner = int(np.argmax(np.abs(basis.doflocs).sum(axis=0)))
            D = np.array([corner], dtype=np.int64)
    elif dirichlet_dofs is not None:
        D = np.asarray(dirichlet_dofs, dtype=np.int64)
    else:
        D = basis.get_dofs().all()

    name, fac = _linear_solver()
    t0 = time.perf_counter()
    if periodic is not None and getattr(periodic, "masters", np.empty(0)).size:
        from .periodic import elimination
        T, full2red = elimination(int(basis.N), periodic)
        Kr = (T.T @ K @ T).tocsr()
        fr = T.T @ f
        Dr = full2red[D]
        Dr = np.unique(Dr[Dr >= 0])
        if Dr.size:
            A, b, xr, I = condense(Kr, fr, D=Dr)
        else:
            A, b, xr, I = Kr, fr, np.zeros(Kr.shape[0]), np.arange(Kr.shape[0])
        if linear_solver == "cg":
            name, xI = _solve_cg(A, b)
        else:
            xI = fac(A)(b)
        xr = np.asarray(xr, dtype=float)
        xr[I] = xI
        x = T @ xr
    else:
        A, b, x, I = condense(K, f, D=D)
        if linear_solver == "cg":
            name, xI = _solve_cg(A, b)
        else:
            xI = fac(A)(b)
        x[I] = xI
    t_solve = time.perf_counter() - t0

    return Solution(phi=x, basis=basis, mesh=mesh, regions=list(regions),
                    mu_el=mu_used, M_el=M_el, order=order, ndofs=int(basis.N),
                    solve_time=t_solve, assemble_time=t_asm, solver=name)


def _solve_cg(A, b, tol: float = 1e-10, maxiter: int = 20000):
    """Diagonally preconditioned CG — the fallback past a direct factorisation.

    pyamg would be the right preconditioner and is tried first; without it this
    is Jacobi-CG, which converges but slowly on a graded magnetostatic mesh, so
    the iteration count is reported rather than hidden.
    """
    import scipy.sparse as sp
    from scipy.sparse.linalg import LinearOperator, cg

    Ac = A.tocsr()
    name = "scipy.cg(jacobi)"
    Mop = None
    try:
        import pyamg
        ml = pyamg.smoothed_aggregation_solver(Ac)
        Mop = ml.aspreconditioner()
        name = "scipy.cg(pyamg-SA)"
    except Exception:
        d = Ac.diagonal()
        d[np.abs(d) < 1e-300] = 1.0
        Mop = LinearOperator(Ac.shape, matvec=lambda v: v / d)
    it = [0]

    def _cb(_x):
        it[0] += 1
    try:
        x, info = cg(Ac, b, rtol=tol, atol=0.0, maxiter=maxiter, M=Mop, callback=_cb)
    except TypeError:                                # scipy < 1.12
        x, info = cg(Ac, b, tol=tol, atol=0.0, maxiter=maxiter, M=Mop, callback=_cb)
    if info != 0:
        raise RuntimeError(f"CG did not converge (info={info}, {it[0]} iters)")
    return f"{name}[{it[0]} it]", x


# --------------------------------------------------------------------------
# nonlinear (Picard) solve
# --------------------------------------------------------------------------

def solve_static3d_nonlinear(mesh, regions: Sequence[Region], order: int = 1,
                             tol: float = 1e-3, max_iter: int = 60,
                             damping: float = 0.5,
                             verbose: bool = False,
                             basis: Optional[object] = None,
                             periodic: Optional[object] = None,
                             dirichlet_dofs: Optional[np.ndarray] = None,
                             neumann_outer: bool = False,
                             linear_solver: Optional[str] = None,
                             mu_init: Optional[np.ndarray] = None) -> Solution:
    """Damped Picard on mu_r(|B|) for every region carrying a ``bh_curve``.

    The permeability update reuses the 2D solver's OWN B-H inversion
    (``field_ops._mu_r_from_bh_vec``) rather than a private copy, so 3D iron and
    2D iron are the same iron by construction — if the curve handling is ever
    fixed, both move together.

    Two choices here were forced by measurement, not taste:

    * The damping is GEOMETRIC, mu <- mu_old^(1-d) * mu_new^d, and ADAPTIVE:
      any sweep that makes the residual worse halves d.  Starting from a
      mu_r = 1000 guess, a saturating ring lands near mu_r ~ 100; arithmetic
      damping crawls across that factor of 10 (30 sweeps and still moving),
      while the same relaxation in log-mu closes it in a handful.  Undamped
      Picard does not converge at all: an element pushed over the knee is handed
      a far smaller mu, which drops its B back under the knee, and it flips
      forever.  Even damped, the stability edge is sharp — on the benchmark ring
      d = 0.15 converges in 16 sweeps and d = 0.20 locks into a 2-cycle at
      residual 0.98 — so the fixed d is a starting guess and the halving is what
      makes the loop usable without per-geometry tuning.

    * The convergence test is the CONSTITUTIVE residual, not the step size.
      The obvious test — "did B stop moving between sweeps" — is proportional to
      the damping factor, so turning the damping down makes any Picard loop
      declare victory wherever it happens to be standing.  That is measured, not
      hypothetical: at damping 0.2 the step-size test stopped after 10 sweeps
      with the ring at mu_r ~ 4000, while the true answer was mu_r ~ 100.  What
      is tested instead is how far the state violates the B-H curve,

          r = || |B| (nu_curve(|B|) - nu_used) ||  /  || |B| nu_curve(|B|) ||

      volume weighted over the nonlinear elements — i.e. the relative error in H
      between the curve and the permeability the solve actually used.  It does
      not shrink just because the steps got smaller, and unlike max|d(mu)/mu| it
      is not hostage to one element at a re-entrant corner where B is a genuine
      geometric singularity.  All three numbers go into ``picard``.
    """
    from motor_ai_sim.simulation.field_ops import _mu_r_from_bh_vec

    kw = dict(basis=basis, periodic=periodic, dirichlet_dofs=dirichlet_dofs,
              neumann_outer=neumann_outer, linear_solver=linear_solver)
    nl = [r for r in regions if r.nonlinear]
    if not nl:
        return solve_static3d(mesh, regions, order=order, **kw)
    nlidx = np.concatenate([np.asarray(r.elements, dtype=np.int64) for r in nl])

    mu_el, _ = _region_arrays(mesh, regions)
    if mu_init is not None:
        # Warm start: a converged permeability from a NEARBY model (the previous
        # stack length in the L-sweep) is a far better first guess than mu_r of
        # the curve's initial slope, and cuts the sweep from ~15 sweeps per point
        # to ~4.  It cannot change the answer: the loop still converges on the
        # constitutive residual, which does not know where it started.
        mu_init = np.asarray(mu_init, dtype=float)
        if mu_init.shape != mu_el.shape:
            raise ValueError("mu_init must be one value per element")
        mu_el = mu_init.copy()
    hist: List[float] = []
    mu_hist: List[float] = []
    dB_hist: List[float] = []
    B_prev: Optional[np.ndarray] = None
    sol: Optional[Solution] = None
    t_start = time.perf_counter()
    converged = False
    d_cur = float(damping)
    d_floor = 0.02
    good = 0
    prev_rel = float("inf")
    for it in range(max_iter):
        sol = solve_static3d(mesh, regions, order=order, mu_el=mu_el, **kw)
        basis = sol.basis
        kw["basis"] = basis
        B = sol.B_elementwise()
        Bmag = np.linalg.norm(B, axis=0)
        w = sol.basis.dx.sum(axis=1)[nlidx]

        mu_new = mu_el.copy()
        for r in nl:
            idx = np.asarray(r.elements, dtype=np.int64)
            mur = np.maximum(_mu_r_from_bh_vec(r.bh_curve, Bmag[idx]), 1.0)
            mu_new[idx] = MU0 * mur
        mu_hist.append(float(np.max(np.abs(mu_new[nlidx] - mu_el[nlidx])
                                    / np.maximum(mu_el[nlidx], 1e-30))))

        # constitutive residual in H (damping independent — see the docstring)
        bm = Bmag[nlidx]
        h_curve = bm / mu_new[nlidx]
        h_used = bm / mu_el[nlidx]
        num = float((((h_curve - h_used) ** 2) * w).sum())
        den = float(((h_curve ** 2) * w).sum())
        rel = math.sqrt(num / max(den, 1e-300))
        hist.append(rel)

        if B_prev is not None:
            n2 = float((((B - B_prev)[:, nlidx] ** 2).sum(axis=0) * w).sum())
            d2 = float(((B[:, nlidx] ** 2).sum(axis=0) * w).sum())
            dB_hist.append(math.sqrt(n2 / max(d2, 1e-300)))
        B_prev = B

        if verbose:
            print(f"  picard {it:2d}  |dH|/|H| = {rel:.3e}   "
                  f"max d(mu)/mu = {mu_hist[-1]:.3e}   d = {d_cur:.3f}")
        if rel < tol:
            converged = True
            break

        if rel > prev_rel:                 # overshot — back the step off
            d_cur = max(0.5 * d_cur, d_floor)
            good = 0
        else:
            # ...and give it back once the loop has proved it is descending.
            # Halving ONLY is a ratchet: the saturating rotor bridges of a spoke
            # rotor (mu_r 4600 -> ~5 across a 0.4 mm bridge) make the residual
            # bounce for a dozen sweeps, and the d that survives that transient
            # is far too small for the smooth descent afterwards — measured on
            # the 30 mm machine, d collapsed to 0.044 and the loop then needed
            # ~60 more sweeps for a factor 5 in residual.  Recovering d after
            # three clean sweeps cuts that to single digits and cannot change
            # the fixed point, only how fast it is reached.
            good += 1
            if good >= 3:
                d_cur = min(1.6 * d_cur, float(damping))
                good = 0
        prev_rel = rel
        mu_el = np.exp((1.0 - d_cur) * np.log(mu_el)
                       + d_cur * np.log(mu_new))

    # one final solve on the converged permeability so the returned field and
    # the returned mu are the same state (a Picard loop that reports the field
    # from the PREVIOUS mu is the classic way to publish a non-solution)
    sol = solve_static3d(mesh, regions, order=order, mu_el=mu_el, **kw)
    sol.picard = dict(iterations=len(hist), history=hist, mu_history=mu_hist,
                      dB_history=dB_hist, converged=converged, tol=tol,
                      damping=damping, damping_final=d_cur,
                      warm_started=bool(mu_init is not None),
                      wall_time=time.perf_counter() - t_start)
    sol.mu_converged = mu_el
    return sol
