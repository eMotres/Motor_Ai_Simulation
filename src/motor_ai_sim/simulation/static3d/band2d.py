"""The 2D leg of ``k_T``, computed the SAME way the 3D one is.

Why this module exists
----------------------
``stage_b.torque.k_T`` was null for one reason, and it was not a solver reason:
the 3D number and the 2D number were not the same QUANTITY.  The 3D torque is a
co-energy central difference,

    T_3d(dm) = [ W'(+dm) - W'(-dm) ] / (2 dm * pitch)   at FROZEN currents,

which is exactly the mean of ``T`` over that rotor window; the 2D number it was
being divided by (``stage_b.torque.two_d.T_avg_Nm``) is ``hybrid_torque``'s
period mean of a running machine, with the currents rotating WITH the rotor and
a different functional (the alpha-beta power balance) underneath.  Dividing one
by the other mixes three differences — window, frozen current, functional —
into a single ratio, and the passport measured the resulting spread at 2 %,
which is the whole size of the effect ``k_T`` exists to carry.

So this module builds the 2D leg the same way as the 3D one, on purpose, term
for term:

* the SAME cross-section — ``band.BandedSection``'s three-piece mesh, the very
  triangulation ``motor_mesh.extrude_section`` extrudes into the 3D tets;
* the SAME rotor angles — integer ring shifts, welded by re-labelling which
  ring node is identified with which, so the two states being differenced sit
  on a bit-identical mesh (the 2D twin of ``band.SlipWeld``, on nodal dofs
  instead of edge ones);
* the SAME frozen currents — the very ``winding3d.WindingT`` the 3D solve is
  driven by, read at ``beta = 1`` (inside the stack), so the slot current
  distribution is not merely equivalent but literally the same object;
* the SAME functional — ``torque3d._curve_coenergy`` on the same B-H curve, the
  same ``W' = int(int_0^H B.dH')`` volume integral, the same central difference
  arithmetic.

What is left in the ratio ``T_3d(dm) / T_2d(dm)`` is then the third dimension,
and if it is an end effect it must be INDEPENDENT of ``dm``: the window mean,
the frozen-current torque-angle average and the functional are now common to
numerator and denominator and cancel exactly.  That is the test, and it is the
thing a single-window ratio could never be.

The weak form
-------------
Plain 2D magnetostatics in ``A_z``, with the source written the way the 3D leg
writes it so the two are the same statement:

    int( nu grad(A) . grad(v) )  =  int( S_x dv/dy - S_y dv/dx ),
    S = Hc + T,   Hc = M / mu_rec,   B = ( dA/dy, -dA/dx ).

``S = Hc + T`` is ``nedelec.solve_static3d_A``'s source term reduced to the
plane, and the magnet half of it is ``fem_solver_2d.solve_magnetostatics_fem``'s
own load, term for term (``Mx/mu_r`` against ``dv/dy``, ``-My/mu_r`` against
``dv/dx``).  The winding half is the same ``T`` with ``curl(T) = J``, which in
2D is ``J_z = -(1/r) dPsi/dtheta`` — ``winding3d``'s own construction.

Two identities come free and both are checked rather than assumed:

    W'_linear = 1/2 int( S . B )         (= 1/2 A^T f, no f kept around)
    psi_ph    = int( T_1ph . B )         (the flux linkage, same pairing)

so with the magnets OFF and linear iron ``W' = 1/2 sum_ph i_ph psi_ph``
POINTWISE and the two torque functionals are one number — the 2D twin of the
measurement that settled the functional question in 3D.

Element order
-------------
``element_order = 1`` is the DEFAULT here, and the reason is not that it is
cheap.  ``T = dW'/dtheta`` is a virtual-work result only when the state being
differenced is a STATIONARY point of the discrete co-energy, and stationarity of
``W'_h(A) - int(S.B)`` in ``A`` reads the B-H curve POINTWISE.  An element-wise
Picard — ``nu`` from the element's own ``|B|``, which is what this solver and
the 3D one both run — satisfies that exactly when ``|B|`` is constant per
element.  That is the P1 case, and it is the 3D leg's case (the curl of a
lowest-order edge element is constant per tet).  It is not the P2 case, and the
consequence is not a small error: measured on the real cross-section, the
element-wise P2 co-energy difference reads 0.350, 0.458, 0.426, 0.404 N.m at
``dm = 2, 4, 6, 8`` — not smooth in the step, therefore not the derivative of
anything.  :attr:`Banded2D.nu_pointwise` restores stationarity at P2 and the
same solves then give 0.569, 0.567, 0.562, beside P1's 0.585, 0.581, 0.576.
P1 needs no such switch because at P1 the two readings ARE the same reading,
and ``tests/test_static3d_band2d.py`` pins both halves of that statement.

What the ratio does NOT cancel, and this is stated because it is the error bar
rather than a caveat: the same triangulation is not the same discretisation
error.  The 2D leg alone moves 1.8 % between ``h_solid`` 1.7 and 0.8, and
whether the 3D leg (N0 on the tets that triangulation was extruded into) moves
with it is a question about two formulations, not about one mesh.  The passport
records the measurement, not an assumption:
``stage_b.torque.matched_2d_window.in_plane_mesh_control``.

Multipliers: the model is one anti-periodic sector of ``n_sectors``, per unit
axial length, so every energy and every flux linkage here is multiplied by
``n_sectors * stack_m``.  (The 3D leg's ``2 * n_sectors`` counts its axial
mirror; there is no half machine in 2D.)

Units: SI (joules, newton-metres, mechanical radians); the cross-section is
carried in mm, as everywhere in this package, and converted at the boundary.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from .band import BandedSection, SignedUF
from .motor_geometry import MM, MotorSection
from .solver import MU0
from .torque3d import _curve_coenergy
from .winding3d import WindingT

PHASES = ("A", "B", "C")


# --------------------------------------------------------------------------
# the source field, in the plane
# --------------------------------------------------------------------------

def winding_T_2d(wt: WindingT, xy: np.ndarray) -> np.ndarray:
    """``T = Psi(r, theta) e_r`` at 2D points ``xy`` (2, ...) [A/m].

    The axial profile is ``beta = 1``: inside the stack the 3D field IS this
    field, so a 2D model driven by it carries exactly the 3D model's slot
    current, element for element, rather than a re-derivation of it.
    """
    x = np.asarray(xy, dtype=float)
    r = np.hypot(x[0], x[1])
    safe = np.where(r > 0.0, r, 1.0)
    th = np.arctan2(x[1], x[0])
    th = np.where(th < 0.0, th + 2.0 * math.pi, th)
    amp = wt.psi_at(r, th)
    out = np.zeros_like(x[:2])
    out[0] = amp * x[0] / safe
    out[1] = amp * x[1] / safe
    return out


# --------------------------------------------------------------------------
# one solved rotor position
# --------------------------------------------------------------------------

@dataclass
class Solve2D:
    A: np.ndarray
    basis: object
    nu_el: np.ndarray                  # (nelem,) or (nelem, nqp) reluctivity used
    shift: int
    rotor_angle_deg: float
    wall_s: float
    picard: dict = field(default_factory=dict)
    nu_converged: Optional[np.ndarray] = None
    ndofs: int = 0
    n_reduced: int = 0

    def B_at_quad(self) -> np.ndarray:
        """(2, nelem, nqp) — ``B = (dA/dy, -dA/dx)`` at the quadrature points."""
        g = self.basis.interpolate(self.A).grad
        return np.stack([g[1], -g[0]])

    def nu_at_quad(self) -> np.ndarray:
        """(nelem, nqp) reluctivity actually used, however it was stored."""
        nu = np.asarray(self.nu_el, dtype=float)
        if nu.ndim == 2:
            return nu
        return np.repeat(nu[:, None], self.basis.dx.shape[1], axis=1)


# --------------------------------------------------------------------------
# the model: mesh, basis and weld built ONCE, rotor angle by re-labelling
# --------------------------------------------------------------------------

@dataclass
class Banded2D:
    """The banded cross-section as a 2D ``A_z`` problem, at any legal shift.

    Mirrors :class:`torque3d.BandedModel` field for field so a driver can hold
    the two side by side and see that they are the same run.
    """
    section: MotorSection
    banded: BandedSection
    stack_mm: Optional[float] = None
    element_order: int = 1
    I_ph: Optional[Dict[str, float]] = None
    winding: Optional[WindingT] = None
    linear_iron: bool = False
    magnets_off: bool = False
    flux_tight_outer: bool = True
    verbose: bool = False
    #: read the B-H curve at every QUADRATURE POINT rather than once per element.
    #:
    #: This is not a refinement knob, it is the STATIONARITY of the discrete
    #: co-energy, and the torque depends on it.  ``T = dW'/dtheta`` is a virtual
    #: work result only when the state being differenced is a stationary point of
    #: the discrete co-energy the difference is taken of; stationarity of
    #: ``W'_h(A) - int(S.B)`` in A is ``int( nu(|B_q|) B . dB/dA ) = int(S.dB/dA)``
    #: — with ``nu`` read POINTWISE.  An element-wise ``nu(|B|_element mean)``
    #: satisfies that only when ``|B|`` is constant per element, which is exactly
    #: the P1 (and the 3D N0) case and is exactly not the P2 one.  Measured
    #: consequence at P2 with element-wise nu: the co-energy central difference
    #: is not merely inaccurate, it is not even smooth in the step — 0.350,
    #: 0.458, 0.425, 0.404 N.m at dm = 2, 4, 6, 8 on this machine.
    nu_pointwise: bool = False

    def __post_init__(self):
        from skfem import (Basis, ElementTriP0, ElementTriP1, ElementTriP2,
                           MeshTri)

        t0 = time.perf_counter()
        s = self.banded.sect
        self.mesh = MeshTri(np.ascontiguousarray(s.p * MM),
                            np.ascontiguousarray(s.t))
        if int(self.element_order) not in (1, 2):
            raise ValueError("element_order must be 1 or 2")
        elem = ElementTriP1() if int(self.element_order) == 1 else ElementTriP2()
        self.basis = Basis(self.mesh, elem)
        self.b0 = self.basis.with_element(ElementTriP0())

        # -- materials, element by element, from the SAME RegionSpec list ----
        nt = s.t.shape[1]
        self.nu_default = np.full(nt, 1.0 / MU0)
        self.Hc = np.zeros((2, nt))
        self.nl: List[Tuple[np.ndarray, object]] = []
        covered = np.zeros(nt, dtype=bool)
        for r in self.section.regions:
            rid = s.names.get(r.name)
            if rid is None:
                raise ValueError(f"region {r.name!r} is not in the cross-section")
            idx = np.flatnonzero(s.tri_region == rid)
            if idx.size == 0:
                raise ValueError(f"region {r.name!r} has no triangles")
            mu_r = float(r.mu_r)
            if mu_r <= 0.0:
                raise ValueError(f"region {r.name!r} has mu_r = {mu_r}")
            self.nu_default[idx] = 1.0 / (MU0 * mu_r)
            if not self.magnets_off:
                M = np.asarray(r.M, dtype=float)[:2] / mu_r
                self.Hc[:, idx] = M[:, None]
            if r.bh_curve and len(r.bh_curve) >= 2 and not self.linear_iron:
                self.nl.append((idx, r.bh_curve))
            covered[idx] = True
        # Everything the region list does not claim is AIR (region id 0) and
        # keeps the mu_r = 1 defaults above.  Stated rather than left implicit:
        # the 3D leg's ``_regions_for`` raises when an element belongs to no
        # region, because a 3D mesh has an explicit air region; the banded
        # cross-section does not carry one, so here the complement IS the field
        # region and there is nothing to raise about.
        self.air = np.flatnonzero(~covered)
        if self.air.size == 0:
            raise ValueError("no air triangles — the cross-section has no gap")

        # -- the weld ---------------------------------------------------------
        self._nodal = np.asarray(self.basis.nodal_dofs[0], dtype=np.int64)
        self._facet = (np.asarray(self.basis.facet_dofs[0], dtype=np.int64)
                       if int(self.element_order) == 2 else None)
        self._facet_of = _facet_lookup(self.mesh)
        self._cut = _cut_dof_pairs(self)
        self.dirichlet = _outer_dofs(self) if self.flux_tight_outer else \
            np.empty(0, dtype=np.int64)
        self._proj: Dict[int, tuple] = {}

        # -- the load's magnet half (rotor-angle independent) -----------------
        self._f_mag = self._source_load(self.Hc)
        self._f_wind = (self._source_load(
            winding_T_2d(self.winding, np.asarray(
                self.basis.global_coordinates())))
            if self.winding is not None else 0.0)
        self.build_s = time.perf_counter() - t0

    # -- geometry ----------------------------------------------------------
    @property
    def pitch_rad(self) -> float:
        return self.banded.pitch_rad

    @property
    def stack_m(self) -> float:
        return float(self.stack_mm if self.stack_mm
                     else self.section.stack_mm) * MM

    @property
    def volume_factor(self) -> float:
        """cross-section integral -> whole machine: sector count x stack."""
        return max(int(self.section.n_sectors), 1) * self.stack_m

    def angle_deg(self, m: int) -> float:
        return self.banded.shift_angle_deg(m)

    # -- assembly ----------------------------------------------------------
    def _source_load(self, S) -> np.ndarray:
        """``int( S_x dv/dy - S_y dv/dx )`` — the load of a source field S.

        ``S`` is either (2, nelem) elementwise or (2, nelem, nqp).  Both are
        broadcast onto the quadrature; the elementwise form is what a magnet is
        and the quadrature form is what an interpolated ``Psi`` is.
        """
        from skfem import LinearForm, asm
        from skfem.helpers import grad

        S = np.asarray(S, dtype=float)
        nqp = self.basis.dx.shape[1]
        if S.ndim == 2:
            S = np.repeat(S[:, :, None], nqp, axis=2)

        @LinearForm
        def _src(v, w):
            return w["S"][0] * grad(v)[1] - w["S"][1] * grad(v)[0]

        return asm(_src, self.basis, S=S)

    def load(self) -> np.ndarray:
        return self._f_mag + self._f_wind

    def _stiffness(self, nu):
        """``int(nu grad(u).grad(v))`` — ``nu`` per element or per quadrature point."""
        from skfem import BilinearForm, asm
        from skfem.helpers import dot, grad

        @BilinearForm
        def _stiff(u, v, w):
            return w["nu"] * dot(grad(u), grad(v))

        nu = np.asarray(nu, dtype=float)
        if nu.ndim == 1:
            nq = np.repeat(nu[:, None], self.basis.dx.shape[1], axis=1)
        else:
            nq = nu
        return asm(_stiff, self.basis, nu=nq).tocsr()

    # -- the rotor angle ---------------------------------------------------
    def projection(self, m: int):
        """``(P, Dcols)`` with ``A = P @ x``, one +-1 per row — the 2D SlipWeld.

        Keyed on the shift itself for the reason ``torque3d.BandedModel`` states:
        shifting by a whole sector flips every ring weld's sign, and under load
        that is a different machine, not the same one relabelled.
        """
        from scipy.sparse import coo_matrix

        key = int(m)
        if key in self._proj:
            return self._proj[key]

        N = int(self.basis.N)
        nod = self._nodal
        rr = np.asarray(self.banded.rring, dtype=np.int64)
        sr = np.asarray(self.banded.sring, dtype=np.int64)
        Nr = int(rr.size)
        per = Nr - 1
        sign = float(self.banded.bc_sign)

        k = np.arange(Nr)
        q, j = np.divmod(k + int(m), per)
        sg_node = np.power(sign, q)

        A_ids = [nod[rr[k]]]
        B_ids = [nod[sr[j]]]
        S_ids = [sg_node]

        if self._facet is not None:
            e = np.arange(per)
            qe, je = np.divmod(e + int(m), per)
            sg_cell = np.power(sign, qe)
            fr = self._facet_of(rr[e], rr[e + 1])
            fs = self._facet_of(sr[je], sr[je + 1])
            A_ids.append(self._facet[fr])
            B_ids.append(self._facet[fs])
            S_ids.append(sg_cell)

        cm, cs, csg = self._cut
        A_ids.append(cs)
        B_ids.append(cm)
        S_ids.append(csg)

        Aa = np.concatenate(A_ids)
        Bb = np.concatenate(B_ids)
        Ss = np.concatenate(S_ids)

        uf = SignedUF(N)
        bad = 0
        for i in range(Aa.size):
            if not uf.union(int(Aa[i]), int(Bb[i]), int(round(Ss[i]))):
                bad += 1
        if bad:
            raise RuntimeError(
                f"{bad} of {Aa.size} weld constraints CONTRADICT the classes "
                "they landed in — the anti-periodic wrap sign and the ring "
                "re-labelling do not compose.  A silently dropped constraint is "
                "a torn air gap that still solves.")
        inv, sgn = uf.classes()
        err = np.abs(sgn[Aa] - Ss * sgn[Bb])
        if float(err.max()) > 0.0 or not np.array_equal(inv[Aa], inv[Bb]):
            raise RuntimeError("the signed union-find did not satisfy every "
                               "constraint it was given")
        P = coo_matrix((sgn, (np.arange(N), inv)),
                       shape=(N, int(inv.max()) + 1)).tocsr()
        D = (np.unique(inv[self.dirichlet]) if self.dirichlet.size
             else np.empty(0, dtype=np.int64))
        self._proj[key] = (P, D)
        return self._proj[key]

    # -- solve -------------------------------------------------------------
    def _linear_solve(self, m: int, nu_el: np.ndarray) -> np.ndarray:
        from scipy.sparse.linalg import spsolve

        P, D = self.projection(m)
        K = self._stiffness(nu_el)
        Kr = (P.T @ K @ P).tocsr()
        fr = P.T @ self.load()
        keep = np.setdiff1d(np.arange(Kr.shape[0]), D)
        x = np.zeros(Kr.shape[0])
        x[keep] = spsolve(Kr[keep][:, keep].tocsc(), fr[keep])
        return P @ x

    def solve(self, m: int = 0, nu_init: Optional[np.ndarray] = None,
              tol: float = 3e-3, max_iter: int = 45,
              damping: float = 0.35) -> Solve2D:
        """One rotor position.  Damped Picard on ``mu_r(|B|)``, 3D's own loop.

        Geometric damping in ``log(mu)``, the same adaptive halving, the same
        residual-scheduled first step and the same damping-independent stop —
        the CONSTITUTIVE residual in H, not the step size.  Copied in form from
        ``nedelec.solve_static3d_A_nonlinear`` so that "the same way" is a
        statement about the code and not only about the intention.
        """
        from motor_ai_sim.simulation.field_ops import _mu_r_from_bh_vec

        t0 = time.perf_counter()
        P, _D = self.projection(m)
        nqp = self.basis.dx.shape[1]
        mu_el = 1.0 / self.nu_default
        if self.nu_pointwise:
            mu_el = np.repeat(mu_el[:, None], nqp, axis=1)
        if nu_init is not None:
            nu_init = np.asarray(nu_init, dtype=float)
            if nu_init.shape != mu_el.shape:
                raise ValueError("nu_init has the wrong shape for this model")
            mu_el = 1.0 / nu_init
        if not self.nl:
            A = self._linear_solve(m, 1.0 / mu_el)
            return Solve2D(A=A, basis=self.basis, nu_el=1.0 / mu_el, shift=int(m),
                           rotor_angle_deg=self.angle_deg(m),
                           wall_s=time.perf_counter() - t0,
                           picard=dict(iterations=0, converged=True, history=[]),
                           nu_converged=1.0 / mu_el, ndofs=int(self.basis.N),
                           n_reduced=int(P.shape[1]))

        nlidx = np.concatenate([idx for idx, _c in self.nl])
        dxq = self.basis.dx
        area = dxq.sum(axis=1)
        hist: List[float] = []
        d_floor, good, prev_rel = 0.02, 0, float("inf")
        d_cur = float(damping)
        d_first = d_cur
        converged = False
        A = None
        for it in range(int(max_iter)):
            A = self._linear_solve(m, 1.0 / mu_el)
            Bq = np.stack([self.basis.interpolate(A).grad[1],
                           -self.basis.interpolate(A).grad[0]])
            Babs = np.hypot(Bq[0], Bq[1])
            # POINTWISE: the curve is read at every quadrature point, which is
            # what makes the solved state a stationary point of the discrete
            # co-energy.  ELEMENTWISE: the area-weighted mean |B| of the
            # element, which is the 3D leg's reading and is the same number
            # whenever B is constant per element.
            Bm = (Babs if self.nu_pointwise
                  else (Babs * dxq).sum(axis=1) / np.maximum(area, 1e-30))
            mu_new = mu_el.copy()
            for idx, curve in self.nl:
                mu_new[idx] = MU0 * np.maximum(
                    _mu_r_from_bh_vec(curve, Bm[idx]), 1.0)
            bm = Bm[nlidx]
            w = (dxq[nlidx] if self.nu_pointwise else area[nlidx])
            h_curve = bm / mu_new[nlidx]
            h_used = bm / mu_el[nlidx]
            num = float((((h_curve - h_used) ** 2) * w).sum())
            den = float(((h_curve ** 2) * w).sum())
            rel = math.sqrt(num / max(den, 1e-300))
            hist.append(rel)
            if it == 0:
                d_cur = d_first = max(d_floor, float(damping) * min(1.0, rel))
            if self.verbose:
                print(f"  2d picard {it:2d}  |dH|/|H| = {rel:.3e}  d = {d_cur:.3f}",
                      flush=True)
            if rel < tol:
                converged = True
                break
            if rel > prev_rel:
                d_cur, good = max(0.5 * d_cur, d_floor), 0
            else:
                good += 1
                if good >= 3:
                    d_cur, good = min(1.6 * d_cur, float(damping)), 0
            prev_rel = rel
            mu_el = np.exp((1.0 - d_cur) * np.log(mu_el)
                           + d_cur * np.log(mu_new))
        A = self._linear_solve(m, 1.0 / mu_el)
        return Solve2D(
            A=A, basis=self.basis, nu_el=1.0 / mu_el, shift=int(m),
            rotor_angle_deg=self.angle_deg(m),
            wall_s=time.perf_counter() - t0,
            picard=dict(iterations=len(hist), history=hist,
                        converged=bool(converged), tol=float(tol),
                        damping=float(damping), damping_first=float(d_first),
                        damping_final=float(d_cur),
                        warm_started=bool(nu_init is not None)),
            nu_converged=1.0 / mu_el, ndofs=int(self.basis.N),
            n_reduced=int(P.shape[1]))

    # -- energy ------------------------------------------------------------
    def co_energy(self, sol: Solve2D) -> float:
        """``W'`` of the whole machine [J] — the volume integral, iron off the curve."""
        Bq = sol.B_at_quad()
        dx = self.basis.dx
        w = 0.5 * sol.nu_at_quad() * (Bq[0] ** 2 + Bq[1] ** 2)
        for idx, curve in self.nl:
            w[idx] = _curve_coenergy(curve, np.hypot(Bq[0][idx], Bq[1][idx]))
        return float((w * dx).sum()) * self.volume_factor

    def co_energy_from_load(self, sol: Solve2D) -> float:
        """``1/2 int(S . B)`` [J] — the LINEAR co-energy, up to the same constant.

        The independent route to the same number, valid only when every region
        is linear.  It is ``1/2 A^T f`` without keeping ``f``: the discrete
        pairing the load was assembled with, so a drift between this and
        :meth:`co_energy` is a drift between two readings of the constitutive
        law and nothing else.
        """
        Bq = sol.B_at_quad()
        dx = self.basis.dx
        nqp = dx.shape[1]
        S = np.repeat(self.Hc[:, :, None], nqp, axis=2)
        if self.winding is not None:
            S = S + winding_T_2d(self.winding,
                                 np.asarray(self.basis.global_coordinates()))
        return float((((S * Bq).sum(axis=0)) * dx).sum()) * 0.5 \
            * self.volume_factor

    def flux_linkage(self, sol: Solve2D,
                     units: Dict[str, WindingT]) -> Dict[str, float]:
        """``psi_ph = int(T_1ph . B)`` [Wb] — ``loaded.flux_linkage`` in the plane."""
        Bq = sol.B_at_quad()
        dx = self.basis.dx
        xq = np.asarray(self.basis.global_coordinates())
        k = self.volume_factor
        out: Dict[str, float] = {}
        for ph, wt in units.items():
            Tq = winding_T_2d(wt, xq)
            out[ph] = float(((Tq * Bq).sum(axis=0) * dx).sum() * k)
        return out


# --------------------------------------------------------------------------
# the sweep and the central difference — torque3d's arithmetic, in 2D
# --------------------------------------------------------------------------

def co_energy_curve(model: Banded2D, shifts: Sequence[int],
                    warm_start: bool = True,
                    units: Optional[Dict[str, WindingT]] = None,
                    **kw) -> dict:
    """``W'(m)`` at a list of shifts, plus the flux linkages at each."""
    sh = [int(s) for s in shifts]
    W: List[float] = []
    W_load: List[float] = []
    psi: List[Dict[str, float]] = []
    cost: List[dict] = []
    nu = None
    for m in sh:
        s = model.solve(m, nu_init=(nu if warm_start else None), **kw)
        W.append(model.co_energy(s))
        W_load.append(model.co_energy_from_load(s))
        psi.append(model.flux_linkage(s, units) if units else {})
        p = s.picard
        cost.append(dict(shift=int(m), angle_deg=model.angle_deg(m),
                         wall_s=s.wall_s, ndofs=s.ndofs,
                         n_reduced=s.n_reduced,
                         picard_iterations=p.get("iterations"),
                         picard_converged=p.get("converged"),
                         picard_residual=(p.get("history") or [None])[-1],
                         warm_started=p.get("warm_started")))
        if warm_start:
            nu = s.nu_converged
    return dict(shifts=sh, angle_deg=[model.angle_deg(m) for m in sh],
                W_J=W, W_load_J=W_load, psi=psi,
                pitch_rad=model.pitch_rad, cost=cost)


def central_differences(curve: dict, i_centre: int,
                        I_ph: Optional[Dict[str, float]] = None) -> List[dict]:
    """Every step size the curve supports — ``torque3d.delta_sweep``'s arithmetic.

    ``I_ph`` also differences the WINDING functional ``1/2 sum_ph i_ph psi_ph``
    over the same solves, which is what makes the magnets-off identity a
    one-line comparison rather than a second sweep.
    """
    W = np.asarray(curve["W_J"], dtype=float)
    sh = np.asarray(curve["shifts"], dtype=int)
    pitch = float(curve["pitch_rad"])
    psi = curve.get("psi") or []
    out: List[dict] = []
    n = W.size
    for d in range(1, min(i_centre, n - 1 - i_centre) + 1):
        dm = int(sh[i_centre + d] - sh[i_centre - d])
        dth = dm * pitch
        row = dict(dm_total=dm, d_theta_deg=math.degrees(dth),
                   dW_J=float(W[i_centre + d] - W[i_centre - d]),
                   rel_dW=float(abs(W[i_centre + d] - W[i_centre - d])
                                / max(abs(W[i_centre]), 1e-30)),
                   T_coenergy_Nm=float((W[i_centre + d] - W[i_centre - d]) / dth))
        if I_ph and psi and psi[0]:
            def _wt(k):
                return 0.5 * sum(float(I_ph[p]) * float(psi[k][p])
                                 for p in psi[k])
            row["T_winding_functional_Nm"] = float(
                (_wt(i_centre + d) - _wt(i_centre - d)) / dth)
            row["winding_over_coenergy"] = float(
                row["T_winding_functional_Nm"] / row["T_coenergy_Nm"]) \
                if row["T_coenergy_Nm"] else float("nan")
        out.append(row)
    return out


# --------------------------------------------------------------------------
# weld plumbing
# --------------------------------------------------------------------------

def _facet_lookup(mesh):
    """``find(a, b) -> facet index`` for an unordered node pair."""
    f = np.asarray(mesh.facets, dtype=np.int64)
    nP = int(mesh.p.shape[1])
    key = np.minimum(f[0], f[1]) * nP + np.maximum(f[0], f[1])
    order = np.argsort(key, kind="stable")
    skey = key[order]

    def find(a, b):
        a = np.asarray(a, dtype=np.int64)
        b = np.asarray(b, dtype=np.int64)
        k = np.minimum(a, b) * nP + np.maximum(a, b)
        pos = np.clip(np.searchsorted(skey, k), 0, skey.size - 1)
        if not np.all(skey[pos] == k):
            raise RuntimeError(
                f"{int((skey[pos] != k).sum())} of {k.size} ring segments are "
                "not mesh facets — gmsh subdivided the ring and the two pieces "
                "no longer share it")
        return order[pos]

    return find


def _cut_dof_pairs(model: "Banded2D"):
    """(master dofs, slave dofs, signs) on the two radial cut planes.

    The 2D twin of ``band._blocked_edge_cut_pairs``, and it carries the same
    fix: the pairing is keyed on (piece, radius), because at exactly one radius
    — the mid-gap ring — the rotor piece and the stator piece both have a node
    at the same place on each cut plane, and a pairing that ignored which piece
    a node belongs to would weld a rotor cut dof to a stator one half the time.
    """
    s = model.banded.sect
    p = np.asarray(s.p)                                # mm
    a_sec = float(model.banded.sector_rad)
    sign = float(model.banded.bc_sign)
    nrot = int(model.banded.n_rotor_nodes)
    nod = model._nodal

    m_nodes = np.asarray(s.masters, dtype=np.int64)
    s_nodes = np.asarray(s.slaves, dtype=np.int64)
    if m_nodes.size != s_nodes.size:
        raise RuntimeError("the cut node lists do not correspond")
    A = [nod[s_nodes]]
    B = [nod[m_nodes]]
    S = [np.full(m_nodes.size, sign)]

    if model._facet is not None:
        ca, sa = math.cos(a_sec), math.sin(a_sec)
        f = np.asarray(model.mesh.facets, dtype=np.int64)
        mid = 0.5 * (p[:, f[0]] + p[:, f[1]])
        r = np.hypot(mid[0], mid[1])
        blk = (np.arange(p.shape[1]) >= nrot).astype(np.int64)
        if not np.array_equal(blk[f[0]], blk[f[1]]):
            raise RuntimeError("a facet joins the two pieces — the "
                               "cross-section is not torn at r_mid")
        tol = 1e-6
        on_m = (np.abs(p[1]) <= tol) & (p[0] > -tol)
        on_s = (np.abs(p[0] * sa - p[1] * ca) <= tol) & \
               ((p[0] * ca + p[1] * sa) > -tol)
        mm = np.flatnonzero(on_m[f[0]] & on_m[f[1]] & (r > tol))
        ss = np.flatnonzero(on_s[f[0]] & on_s[f[1]] & (r > tol))
        if mm.size != ss.size:
            raise RuntimeError(
                f"cut planes hold {mm.size} vs {ss.size} FACET dofs — the "
                "banded section is not rotationally periodic")
        if mm.size:
            fb = blk[f[0]]
            om = np.lexsort((r[mm], fb[mm]))
            os_ = np.lexsort((r[ss], fb[ss]))
            mm, ss = mm[om], ss[os_]
            err = float(np.max(np.abs(r[mm] - r[ss])))
            if err > 1e-6 or not np.array_equal(fb[mm], fb[ss]):
                raise RuntimeError(
                    f"periodic FACET pairing is off by {err:.3e} mm — the two "
                    "cut planes do not carry the same mesh")
            A.append(model._facet[ss])
            B.append(model._facet[mm])
            S.append(np.full(mm.size, sign))

    return (np.concatenate(B), np.concatenate(A), np.concatenate(S))


def _outer_dofs(model: "Banded2D") -> np.ndarray:
    """Dofs on the truncation circle ``r = r_box``.

    ``A_z = const`` there is ``B.n = 0``: the FLUX-TIGHT box, which is what
    ``nedelec.truncation_dofs`` imposes on the 3D leg (``A x n = 0``).  The two
    legs therefore truncate the same way, which is the only way the ratio can be
    read as an end effect rather than as two different boxes.
    """
    p = np.asarray(model.banded.sect.p)
    r = np.hypot(p[0], p[1])
    rmax = float(model.banded.sect.r_box_mm)
    on = np.flatnonzero(r >= rmax - 1e-3)
    if on.size == 0:
        raise RuntimeError("no node sits on the truncation circle")
    out = [model._nodal[on]]
    if model._facet is not None:
        f = np.asarray(model.mesh.facets, dtype=np.int64)
        sel = np.zeros(p.shape[1], dtype=bool)
        sel[on] = True
        out.append(model._facet[np.flatnonzero(sel[f[0]] & sel[f[1]])])
    return np.unique(np.concatenate(out)).astype(np.int64)
