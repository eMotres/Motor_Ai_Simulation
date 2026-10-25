"""Globalized correction of a stalled penalty-contact iterate.

The active-set trial may release almost every pocket wall at once. Its next
unrestricted linear solve then crosses other contact planes or follows a rigid
mechanism. Repeating/freezing that set is not convergence. Here a convex
normal-contact/lagged-friction energy controls the step length, followed by a
coupled semismooth Newton correction of the actual regularized Coulomb law.
Only the original force balance and bilateral constraints certify success;
regularization belongs to the Newton direction, never the reported equilibrium.
"""
from __future__ import annotations

import numpy as np
import scipy.sparse as sp

EQUILIBRIUM_RTOL = 1e-8


def equilibrate(K, f, cs, basis, g0, u_seat, hold, solve_fn):
    """Return a force-balanced state, or None without changing the caller.

    Seating offsets and its tangential reference are preserved. No new contact
    surfaces, glue, frictionless rescue, or artificial final supports are added.
    """
    from . import contact as c
    from motor_ai_sim import jobs

    # Reject a genuine loaded rigid escape before trying to minimize an
    # unbounded contact energy. This is a necessary support-cone condition,
    # not a strength or convergence pass. Include both Coulomb cone edges.
    from scipy.optimize import linprog
    import warnings
    body = c._part_bodies(cs)
    body_dof = c._dof_components(basis, body, cs.mesh)
    fixed = set(body[hold.vertices]) if hold is not None else set()
    if cs.cyclic is not None:
        fixed.update(body[cs.cyclic.vertices])
    for b in np.unique(body):
        if b in fixed:
            continue
        ina = body_dof[cs.pair_dofs[:, 0]] == b
        inb = body_dof[cs.pair_dofs[:, 2]] == b
        pairs = np.flatnonzero(ina ^ inb)
        if np.any(~cs.pair_unilateral[pairs]):
            continue  # bilateral connection already supplies the restraint
        dx = np.flatnonzero((body_dof == b) & (np.arange(basis.N) % 2 == 0))
        xy = basis.doflocs[:, dx].T
        center = xy.mean(axis=0)
        rr = xy - center
        arm = max(float(np.linalg.norm(rr, axis=1).max()), 1e-12)
        Q = np.array([f[dx].sum(), f[dx+1].sum(),
                      (rr[:, 0] @ f[dx+1] - rr[:, 1] @ f[dx]) / arm])
        if np.linalg.norm(Q) <= 1e-9 * max(float(np.abs(f[body_dof == b]).sum()), 1e-30):
            continue
        if not len(pairs):
            return None
        G = c._body_rows(cs, pairs, ina[pairs], center, arm)
        T = c._body_rows(cs, pairs, ina[pairs], center, arm, tangential=True)
        friction = cs.pair_mu[pairs, None] * T
        directions = np.vstack([G + friction, G - friction])
        # SciPy forwards the single-thread option to HiGHS.
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="Unrecognized options detected")
            support = linprog(np.zeros(len(directions)), A_eq=-directions.T,
                              b_eq=Q / np.linalg.norm(Q), bounds=(0., None),
                              method="highs", options={"threads": 1})
        if support.status == 2:  # certified infeasible, not an optimizer failure
            return None

    ndof = basis.N
    scale = float(K.diagonal().mean())
    row_scale = np.sqrt(scale)
    weights = c.pair_weights(cs)
    kp = c.CONTACT_STIFFNESS * scale * weights
    uni = cs.pair_unilateral
    ids = np.flatnonzero(uni)
    kn = kp[ids]
    mu = cs.pair_mu[ids]

    def rows(mask, tangent):
        r, col, val, pair = c._pair_rows(cs, mask, tangent)
        return sp.coo_matrix((val, (r, col)), shape=(len(pair), ndof)).tocsr(), pair

    Bn, _ = rows(uni, False)
    Bt, _ = rows(uni, True)
    no_row = cs.pair_no_row if cs.pair_no_row.size else np.zeros(cs.n_pairs, bool)
    Gn, pn = rows(~uni & ~no_row, False)
    Gt, pt = rows(cs.pair_tie_tangent & ~no_row, True)
    lab = c._components(cs, np.ones(cs.n_pairs, bool), cs.mesh.p.shape[1])
    cyc = cs.cyclic
    R, _ = c._rigid_basis(basis, lab, cs.mesh,
                         hold.vertices if hold is not None else None,
                         cyc.vertices if cyc is not None else None)
    # A border may choose a reference for a self-equilibrated body. It may
    # never absorb a real applied force or couple on an unsupported body.
    if R.shape[1] and np.linalg.norm(R.T @ f) > 1e-9 * max(np.linalg.norm(f), 1e-30):
        return None
    blocks = [sp.csr_matrix(R.T), Gn, Gt]
    nh = hold.n_rows if hold is not None else 0
    if nh:
        blocks.append(sp.coo_matrix((hold.dirs.ravel(),
                      (np.repeat(np.arange(nh), 2), hold.dofs.ravel())),
                      shape=(nh, ndof)))
    nc = 0
    if cyc is not None and cyc.n_rows:
        da, db, rot = cyc.dofs_a, cyc.dofs_b, cyc.R
        m = len(da)
        nc = 2 * m
        col = np.r_[np.c_[db[:, 0], da[:, 0], da[:, 1]].ravel(),
                    np.c_[db[:, 1], da[:, 0], da[:, 1]].ravel()]
        val = np.r_[np.tile([1, -rot[0, 0], -rot[0, 1]], m),
                    np.tile([1, -rot[1, 0], -rot[1, 1]], m)]
        blocks.append(sp.coo_matrix((val, (np.repeat(np.arange(nc), 3), col)),
                                    shape=(nc, ndof)))
    C = sp.vstack(blocks, format="csr") * row_scale
    D = sp.diags(np.maximum(K.diagonal(), scale * .01))
    u = np.zeros(ndof)
    mult = np.zeros(C.shape[0])
    cone = np.zeros(len(ids))
    norm = max(float(np.linalg.norm(f)), 1e-30)
    radius = float(np.hypot(*cs.mesh.p).max())
    iterations = 0

    def state(x, lm, cap):
        gap = Bn @ x + g0[ids]
        slip = Bt @ x
        fn = kn * np.minimum(gap, 0.)
        ft = np.clip(kn * slip, -cap, cap)
        residual = np.r_[K @ x + Bn.T @ fn + Bt.T @ ft - f + C.T @ lm, C @ x]
        return gap, slip, fn, ft, residual

    def energy(x, gap, slip, cap):
        pen = np.minimum(gap, 0.)
        return float(.5 * x @ (K @ x) - f @ x + .5 * (kn * pen) @ pen
                     + np.sum(np.where(np.abs(kn * slip) <= cap,
                              .5 * kn * slip**2,
                              cap * (np.abs(slip) - .5 * cap / kn))))

    def direction(J, residual, regularization):
        A = sp.bmat([[J + regularization * D, C.T], [C, None]], format="csr")
        delta = np.asarray(solve_fn(A, -residual))
        return delta if np.isfinite(delta).all() else None

    # Lag the pressure-dependent cone only while globalizing. For fixed cone
    # the normal penalty plus tangential Huber energy is convex. Open pairs
    # can reclose during a line search; no flip-count exclusion is permitted.
    for _outer in range(10):
        for _inner in range(35):
            jobs.check_cancelled()
            iterations += 1
            gap, slip, fn, ft, residual = state(u, mult, cone)
            if np.linalg.norm(residual) <= 1e-6 * norm:
                break
            active = gap <= 0.
            sticking = np.abs(kn * slip) <= cone
            J = K + Bn.T @ sp.diags(kn * active) @ Bn + Bt.T @ sp.diags(kn * sticking) @ Bt
            value = energy(u, gap, slip, cone)
            accepted = False
            for reg in (1e-10, 1e-8, 1e-6, 1e-4, 1e-2, 1.):
                delta = direction(J, residual, reg)
                if delta is None:
                    continue
                slope = float(residual[:ndof] @ delta[:ndof])
                if slope >= 0:
                    continue
                for ls in range(40):
                    alpha = .5**ls
                    trial = u + alpha * delta[:ndof]
                    lm = mult + alpha * delta[ndof:]
                    if np.abs(trial + u_seat).max() > .01 * radius:
                        continue
                    gn, sn, _, _, _ = state(trial, lm, cone)
                    if energy(trial, gn, sn, cone) <= value + 1e-4 * alpha * slope + 1e-13 * max(abs(value), 1e-8):
                        u, mult = trial, lm
                        accepted = True
                        break
                if accepted:
                    break
            if not accepted:
                break
        gap, slip, fn, ft, residual = state(u, mult, cone)
        cone = .5 * cone + .5 * (-mu * fn)

    # Use the consistent derivative, including d(mu*p)/du on sliding pairs.
    # This removes the lagged-cone error rather than clipping an out-of-cone
    # force after solving a different equilibrium.
    for _ in range(30):
        jobs.check_cancelled()
        iterations += 1
        gap = Bn @ u + g0[ids]
        cone = -mu * kn * np.minimum(gap, 0.)
        gap, slip, fn, ft, residual = state(u, mult, cone)
        err = float(np.linalg.norm(residual) / norm)
        if err <= EQUILIBRIUM_RTOL:
            break
        active = gap <= 0.
        sticking = active & (np.abs(kn * slip) <= cone)
        sliding = active & ~sticking
        J = (K + Bn.T @ sp.diags(kn * active) @ Bn
             + Bt.T @ sp.diags(kn * sticking) @ Bt
             - Bt.T @ sp.diags(kn * mu * np.sign(slip) * sliding) @ Bn)
        accepted = False
        for reg in (0., 1e-10, 1e-8, 1e-6):
            delta = direction(J, residual, reg)
            if delta is None:
                continue
            for ls in range(25):
                alpha = .5**ls
                trial = u + alpha * delta[:ndof]
                lm = mult + alpha * delta[ndof:]
                if np.abs(trial + u_seat).max() > .01 * radius:
                    continue
                cap = -mu * kn * np.minimum(Bn @ trial + g0[ids], 0.)
                rn = state(trial, lm, cap)[-1]
                if np.linalg.norm(rn) < np.linalg.norm(residual) * (1 - 1e-4 * alpha):
                    u, mult = trial, lm
                    accepted = True
                    break
            if accepted:
                break
        if not accepted:
            return None
    else:
        return None

    # Independent force/constraint check uses the unregularized original law.
    if not np.isfinite(err) or err > EQUILIBRIUM_RTOL:
        return None
    rel = u[cs.pair_dofs[:, 2:]] - u[cs.pair_dofs[:, :2]]
    all_gap = np.einsum('ij,ij->i', rel, cs.pair_n) + g0
    tangent = np.c_[-cs.pair_n[:, 1], cs.pair_n[:, 0]]
    all_slip = np.einsum('ij,ij->i', rel, tangent)
    ln = np.zeros(cs.n_pairs)
    lt = np.zeros(cs.n_pairs)
    off = R.shape[1]
    lm = mult * row_scale
    ln[pn] = lm[off:off + len(pn)]
    off += len(pn)
    lt[pt] = lm[off:off + len(pt)]
    off += len(pt)
    ln[ids], lt[ids] = fn, ft
    return dict(u=u, gap=all_gap, slip=all_slip, lam_n=ln, lam_t=lt,
                hold_mult=lm[off:off + nh], cyclic_mult=lm[off + nh:],
                iterations=iterations, equilibrium_residual_rel=err)
