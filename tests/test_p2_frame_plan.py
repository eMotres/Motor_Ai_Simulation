"""The frame plan assembles the reduced bordered system of the eddy Newton.

``p2_frame_plan`` maps every global entry straight to its position in the
upper triangle of ``[[Pfᵀ M Pf, −Pfᵀ G], [·, diag S]]`` — once per run for the
entries off the slip ring, once per frame for the ones on it.  These tests hold
it to the SciPy expression it replaces on real slip projections over many
rotor shifts (sector with both signs and the full ring), and hold the eddy
Newton on the plan to the SciPy-path Newton on a small saturating machine.
Small synthetic meshes only.
"""
from __future__ import annotations

import math
import os

import numpy as np
import pytest
import scipy.sparse as sp
from skfem import Basis, ElementTriP0, ElementTriP2, MeshTri, asm

from motor_ai_sim.simulation.field_ops import MU0
from motor_ai_sim.simulation.p2_frame_plan import (
    PlanMismatch, ReducedPlan, reduced_map)
from motor_ai_sim.simulation.p2_nonlinear import P2Nonlinear, stiff_nu2
from motor_ai_sim.simulation.p2_projection import SlipProjection

CURVE = [(0.0, 0.0), (1.2 / (500 * MU0), 1.2), (1e6, 1.2 + MU0 * 1e6)]


class _Log:
    def __init__(self):
        self.warnings = []

    def warning(self, *a, **k):
        self.warnings.append(a[0] % a[1:] if len(a) > 1 else a[0])

    def info(self, *a, **k):
        pass

    def debug(self, *a, **k):
        pass


def annulus(segments, full_ring, sign):
    """Stator and rotor annuli meshed apart, coincident ring vertices
    (the mesh of tests/test_p2_projection.annular_projection, with its basis)."""
    angle = 2 * math.pi if full_ring else math.pi / 2
    angles = (np.linspace(0., angle, segments, endpoint=False) if full_ring
              else np.linspace(0., angle, segments + 1))
    nv = len(angles)

    def half(radii):
        pts = np.array([(r * math.cos(a), r * math.sin(a))
                        for r in radii for a in angles]).T
        tri = []
        for layer in range(len(radii) - 1):
            for k in range(segments):
                j = (k + 1) % nv
                a, b, c, d = (layer * nv + k, layer * nv + j,
                              (layer + 1) * nv + k, (layer + 1) * nv + j)
                tri.extend([(a, c, d), (a, d, b)])
        return pts, np.array(tri).T

    stator, st = half((1., 1.2, 1.4))
    rotor, rt = half((.5, .8, 1.))
    nsn = stator.shape[1]
    mesh = MeshTri(np.hstack([stator, rotor]), np.hstack([st, rt + nsn]))
    basis = Basis(mesh, ElementTriP2())
    if full_ring:
        masters = slaves = np.array([], int)
    else:
        masters = np.array([nsn, nsn + nv, nsn + 2 * nv, 0, nv, 2 * nv])
        slaves = masters + nv - 1
    outer = np.arange(2 * nv, 3 * nv)
    oedges = np.flatnonzero(np.all(np.isin(mesh.facets, outer), axis=0))
    bnd = np.concatenate([basis.nodal_dofs[0, outer],
                          basis.facet_dofs[0, oedges]])
    proj = SlipProjection(
        n_dof=basis.N, facets=mesh.facets, vdof=basis.nodal_dofs[0],
        fdof=basis.facet_dofs[0], rring=np.arange(2 * nv, 3 * nv),
        sring=np.arange(nv), nsn=nsn, n_ring=nv, full_ring=full_ring,
        bc_sign=sign, Mn=masters, Sn=slaves, dirichlet_dofs=bnd)
    return mesh, basis, proj, st.shape[1]


def _machine(full_ring=False, sign=-1, segments=12):
    """A small saturating 'machine' on the annulus: iron in the outer stator
    and inner rotor layers, one solid conductor body in the rotor."""
    mesh, b, proj, nst = annulus(segments, full_ring, sign)
    b0 = b.with_element(ElementTriP0())
    n_el = mesh.t.shape[1]
    mid = mesh.p[:, mesh.t].mean(axis=1)
    rad = np.hypot(mid[0], mid[1])
    iron_s = np.flatnonzero((rad > 1.2) & (np.arange(n_el) < nst))
    iron_r = np.flatnonzero((rad < 0.8) & (np.arange(n_el) >= nst))
    body = np.flatnonzero((rad > 0.8) & (np.arange(n_el) >= nst))[::3]
    nu = np.full(n_el, 1.0 / MU0)
    nu[iron_s] = 0.0
    nu[iron_r] = 0.0
    K_const = asm(stiff_nu2, b, nu=b0.interpolate(nu)).tocsr()
    sat, sat_sub = [], []
    for ids in (iron_s, iron_r):
        sb = Basis(mesh, ElementTriP2(), elements=ids)
        sat.append((ids, CURVE))
        sat_sub.append((sb, sb.with_element(ElementTriP0()), ids, CURVE))
    return mesh, b, proj, K_const, sat, sat_sub, body


def _mass(mesh, ids, sigma):
    from skfem import BilinearForm

    @BilinearForm
    def m(u, v, w):
        return u * v

    sb = Basis(mesh, ElementTriP2(), elements=ids)
    return (asm(m, sb) * sigma).tocsr()


def _reference_upper(M, G, S, Pro, free):
    """The SciPy expression: reduced bordered matrix, upper triangle."""
    Pf = Pro.tocsc()[:, free].tocsr()
    Jff = (Pf.T @ M @ Pf).tocsr()
    Bf = (Pf.T @ G).tocsr()
    Mb = sp.bmat([[Jff, -Bf], [-Bf.T, sp.diags(S)]]).tocsr()
    return sp.triu(Mb).toarray()


@pytest.mark.parametrize("full_ring,sign", [(False, -1), (False, 1), (True, 1)])
def test_plan_equals_the_scipy_reduction_over_many_shifts(full_ring, sign):
    mesh, b, proj, K_const, sat, sat_sub, body = _machine(full_ring, sign)
    p2 = P2Nonlinear(basis=b, n_dof=b.N, K_const=K_const, sat=sat,
                     sat_sub=sat_sub, pardiso=None, log=_Log())
    rng = np.random.default_rng(3)
    Msig = _mass(mesh, body, 2e6)
    nU = 3
    G = sp.random(b.N, nU, density=0.05, random_state=4, format="csr")
    S = 1.0 + rng.random(nU)
    N = b.N
    Kc, Mc, Gc = K_const.tocoo(), Msig.tocoo(), G.tocoo()
    blocks = [(Kc.row, Kc.col), (Mc.row, Mc.col), (Gc.row, N + Gc.col),
              (N + np.arange(nU), N + np.arange(nU))] + p2.iron_blocks()
    plan = None
    for shift in [0, 1, 2, 5, -3, 11, 23, 24, 37, -41]:
        Pro, outer = proj.build(shift)
        free = np.setdiff1d(np.arange(Pro.shape[1]), outer)
        rr, sg = reduced_map(Pro, free)
        if plan is None:
            plan = ReducedPlan(N, len(free), nU, blocks, proj.slip_dofs, rr, sg)
        fs = plan.frame(rr, sg)
        # new element values per "iteration"
        for _ in range(2):
            A = rng.standard_normal(N) * 0.05
            el = p2.kpw_el(A)
            jv = p2.jac_el(el)
            data = fs.data([Kc.data, Mc.data, -Gc.data, S] + jv)
            U = sp.csr_matrix((data, fs.indices, fs.indptr),
                              shape=(fs.n, fs.n)).toarray()
            K, info = p2.Kpw(A)
            M = (K + p2.tangent2(info) + Msig).tocsr()
            want = _reference_upper(M, G, S, Pro, free)
            scale = np.abs(want).max()
            assert np.abs(U - want).max() <= 1e-13 * scale, shift
        # rows ascending, columns ascending, diagonal first in each row
        assert np.all(fs.indices[fs.indptr[:-1]] == np.arange(fs.n))


def test_a_moved_dof_outside_the_slip_set_is_refused():
    mesh, b, proj, K_const, sat, sat_sub, body = _machine()
    Pro, outer = proj.build(0)
    free = np.setdiff1d(np.arange(Pro.shape[1]), outer)
    rr, sg = reduced_map(Pro, free)
    plan = ReducedPlan(b.N, len(free), 0, [tuple(K_const.tocoo().coords)],
                       proj.slip_dofs, rr, sg)
    keep = np.flatnonzero(~np.isin(np.arange(b.N), proj.slip_dofs)
                          & (rr >= 0))
    rr2 = rr.copy()
    rr2[keep[0]], rr2[keep[1]] = rr2[keep[1]], rr2[keep[0]]
    with pytest.raises(PlanMismatch):
        plan.frame(rr2, sg)


def test_apply_K_is_the_matrix_product():
    mesh, b, proj, K_const, sat, sat_sub, body = _machine()
    p2 = P2Nonlinear(basis=b, n_dof=b.N, K_const=K_const, sat=sat,
                     sat_sub=sat_sub, pardiso=None, log=_Log())
    rng = np.random.default_rng(8)
    A = rng.standard_normal(b.N) * 0.05
    x = rng.standard_normal(b.N)
    K, _ = p2.Kpw(A)
    got = p2.apply_K(p2.kpw_el(A), x)
    want = K @ x
    assert np.abs(got - want).max() <= 1e-13 * np.abs(want).max()


def _pardiso_p2(mesh_bits):
    pypardiso = pytest.importorskip("pypardiso")
    if not os.environ.get("PYPARDISO_MKL_RT"):
        try:
            os.environ["PYPARDISO_MKL_RT"] = str(
                pypardiso.scipy_aliases.pypardiso_solver.libmkl._name)
        except Exception:
            pass
    mesh, b, proj, K_const, sat, sat_sub, body = mesh_bits
    return P2Nonlinear(basis=b, n_dof=b.N, K_const=K_const, sat=sat,
                       sat_sub=sat_sub, pardiso=pypardiso.PyPardisoSolver(),
                       log=_Log(),
                       pardiso_spd=pypardiso.PyPardisoSolver(mtype=2))


def _drive(p2, mesh_bits, dt):
    from motor_ai_sim.simulation.p2_drive import P2Drive
    mesh, b, proj, K_const, sat, sat_sub, body = mesh_bits
    Msig = _mass(mesh, body, 2e6)
    one = np.zeros(b.N)
    sb = Basis(mesh, ElementTriP2(), elements=body)
    one[np.unique(sb.element_dofs)] = 1.0
    g = Msig @ one
    G = sp.csr_matrix(g[:, None])
    S = np.array([float(one @ g)])
    rng = np.random.default_rng(21)
    # a 'magnet' source on the rotor: large enough to saturate the iron
    f_mag = np.zeros(b.N)
    f_mag[np.unique(sb.element_dofs)] = 40.0 * rng.random(
        np.unique(sb.element_dofs).size)
    return P2Drive(p2=p2, psi=None, f_mag=f_mag, Pa=None, Pb=None,
                   R_phase=0.0, v_phase_peak=0.0, n_dof=b.N, pic_tol=1e-3,
                   dt=dt, log=_Log(), G=G, Msig=Msig,
                   Msd=(Msig * (1.0 / dt)).tocsr(), Sdt=S * dt)


def _march(mesh_bits, use_plan, shifts, dt=1e-4):
    p2 = _pardiso_p2(mesh_bits)
    p2.slip_dofs = mesh_bits[2].slip_dofs if use_plan else None
    drv = _drive(p2, mesh_bits, dt)
    N = mesh_bits[1].N
    A = np.zeros(N); U = np.zeros(1); Ap = np.zeros(N)
    out = []
    for m in shifts:
        Pro, outer = mesh_bits[2].build(m)
        free = np.setdiff1d(np.arange(Pro.shape[1]), outer)
        ok, A, U, rr, nit = drv.eddy_solve(Pro, free, A, U, np.array([3.0]),
                                           Ap, None, 25)
        assert ok, (m, rr, nit)
        out.append((A.copy(), U.copy(), nit))
        Ap = A.copy()
    return out, p2, drv


@pytest.mark.parametrize("full_ring,sign", [(False, -1), (True, 1)])
def test_eddy_newton_on_the_plan_matches_the_scipy_path(full_ring, sign):
    bits = _machine(full_ring, sign, segments=16)
    shifts = [0, 1, 2, 3, 4, 17, 18]
    ref, p_ref, _ = _march(bits, False, shifts)
    new, p_new, drv = _march(bits, True, shifts)
    assert p_ref.plan_solves == 0
    assert p_new.plan_solves == p_new.spd_solves > 0
    assert drv._plan_dead is None
    assert p_new.spd_analyses == len(shifts)        # one analysis per frame
    # Both marches stop at the Newton residual 1e-7, from iterates that
    # differ by round-off (the sums are re-associated), so the converged
    # fields agree to the Newton tolerance, not to the last bit.  The body
    # voltage is held only through its constraint row, whose residual is
    # judged against the flux scale |GᵀA| (here ~100x S·U), hence the looser
    # bound on U.
    for (A0, U0, n0), (A1, U1, n1) in zip(ref, new):
        assert n0 == n1
        assert np.abs(A1 - A0).max() <= 1e-6 * np.abs(A0).max()
        assert np.abs(U1 - U0).max() <= 1e-5 * np.abs(U0).max()


def test_kill_switch_keeps_the_scipy_path(monkeypatch):
    from motor_ai_sim.simulation import p2_frame_plan
    monkeypatch.setattr(p2_frame_plan, "ENABLED", False)
    bits = _machine(False, -1)
    _out, p2, _drv = _march(bits, True, [0, 1])
    assert p2.plan_solves == 0
