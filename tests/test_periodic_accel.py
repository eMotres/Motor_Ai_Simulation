"""RRE period-map accelerator (simulation/periodic_accel.py), 2026-09-26."""
import numpy as np
import pytest

from motor_ai_sim.simulation.periodic_accel import (
    MIN_VERIFY_PERIODS, STEP_MAX, rre_extrapolate)


def _affine_map(n, lams, seed=0, complex_pair=None):
    """x -> A x + b with a few slow modes (real λ, optional rotation pair)."""
    rng = np.random.default_rng(seed)
    V = np.linalg.qr(rng.standard_normal((n, n)))[0]
    D = np.diag(np.r_[lams, 0.05 * rng.random(n - len(lams))])
    if complex_pair is not None:           # r·e^{±iφ} as a 2x2 rotation block
        r, phi = complex_pair
        i = len(lams)
        D[i:i + 2, i:i + 2] = r * np.array([[np.cos(phi), -np.sin(phi)],
                                            [np.sin(phi), np.cos(phi)]])
    A = V @ D @ V.T
    b = rng.standard_normal(n)
    return A, b, np.linalg.solve(np.eye(n) - A, b)


def _iterates(A, b, m, x0=None):
    x = np.zeros(b.size) if x0 is None else x0
    out = []
    for _ in range(m):
        x = A @ x + b
        out.append(x.copy())
    return out


def test_exact_for_k_slow_modes_plus_fast_noise_free():
    # 3 slow modes (0.94 real + a q=5 rotation pair 0.9·e^{±2πi/5}), the rest
    # tiny: RRE over k+2 = 6 states removes the slow ones.
    A, b, xs = _affine_map(120, [0.94], complex_pair=(0.9, 2 * np.pi / 5))
    S = _iterates(A, b, 12)
    e_march = np.linalg.norm(S[-1] - xs)
    s, info = rre_extrapolate(S[-6:])
    assert s is not None, info
    e_acc = np.linalg.norm(s - xs)
    assert e_acc < 0.02 * e_march
    assert info["resid_ratio"] < 0.1


def test_pure_affine_low_rank_is_exact():
    # the error lives in exactly 2 modes: 4 states suffice, to round-off
    n = 50
    rng = np.random.default_rng(3)
    V = np.linalg.qr(rng.standard_normal((n, n)))[0]
    A = V[:, :2] @ np.diag([0.95, 0.7]) @ V[:, :2].T
    b = rng.standard_normal(n)
    xs = np.linalg.solve(np.eye(n) - A, b)
    S = _iterates(A, b, 4, x0=xs + V[:, :2] @ np.array([1.0, -2.0]))
    s, info = rre_extrapolate(S)
    assert np.linalg.norm(s - xs) < 1e-8 * np.linalg.norm(xs)


def test_weights_are_the_norm_and_invariant_to_uniform_scale():
    A, b, xs = _affine_map(60, [0.9, 0.8], seed=5)
    S = _iterates(A, b, 8)
    w = np.linspace(1.0, 3.0, 60)
    s1, _ = rre_extrapolate(S[-5:], w)
    s2, _ = rre_extrapolate(S[-5:], 7.0 * w)
    np.testing.assert_allclose(s1, s2, rtol=1e-9, atol=1e-12)


def test_refusals_are_loud_not_silent():
    s, info = rre_extrapolate([np.ones(3), np.ones(3)])
    assert s is None and "fewer" in info["refused"]
    s, info = rre_extrapolate([np.ones(3)] * 4)
    assert s is None and "already periodic" in info["refused"]
    with pytest.raises(ValueError):
        rre_extrapolate([np.ones(3), np.ones(4), np.ones(3)])
    # a map with no contraction (λ = 1: pure drift) cannot be extrapolated
    S = [np.array([float(j), 1.0 + 1e-9 * j * j]) for j in range(5)]
    s, info = rre_extrapolate(S)
    assert s is None or info["step_over_last_change"] <= STEP_MAX


def test_owner_rule_constant():
    assert MIN_VERIFY_PERIODS >= 4


# ── symmetry sectors (the L155 structure: q = 5 image map, rotating pairs) ──
from motor_ai_sim.simulation.periodic_accel import (  # noqa: E402
    extrapolate_by_sector, restrict_shift, sector_components, shift_cycles,
    single_mode_extrapolate)


def _cyclic_perm(n_cell, q, sign_last=1.0):
    """Image map on q cells of n_cell dofs: cell k -> cell k-1 (a pole pair)."""
    n = n_cell * q
    perm = (np.arange(n) + n_cell) % n
    sign = np.ones(n)
    sign[n - n_cell:] = sign_last
    return perm, sign


def test_shift_cycles_and_components_sum_exactly():
    perm, sign = _cyclic_perm(7, 5, -1.0)
    # one tail dof (a slaved copy): maps into a cycle, is never mapped to
    perm = np.r_[perm, 3]
    sign = np.r_[sign, 1.0]
    q, sg, reg = shift_cycles(perm, sign)
    assert (q, sg) == (5, -1.0) and int(np.sum(~reg)) == 1
    x = np.random.default_rng(0).standard_normal(perm.size)
    comps = sector_components(x, perm, sign, q, sg, reg)
    np.testing.assert_allclose(sum(comps.values()), x, atol=1e-12)
    # each component is an eigen-component: S c = 2cos(θ)c − S⁻¹c (real pair)
    for key, c in comps.items():
        k = int(key[1:])
        th = (2 * np.pi * k + np.pi) / 5
        Sc = sign * c[perm]
        Sc_inv = np.zeros_like(c)
        Sc_inv[perm[reg]] = (c * sign)[reg]
        lhs = Sc[reg] + Sc_inv[reg]
        rhs = (2 * np.cos(th) * c)[reg] if abs(np.sin(th)) > 1e-9 else 2 * np.cos(th) * c[reg]
        np.testing.assert_allclose(lhs, rhs, atol=1e-10)


def test_restrict_shift_matches_the_solver_convention():
    rdf = np.array([10, 11, 12, 13, 14, 15])
    jj = np.array([2, 3, 4, 5, 0, 1])            # out[rdf] = ss*vec[rdf[jj]]
    ss = np.ones(6)
    perm, sign = restrict_shift(rdf, jj, ss, np.array([10, 12, 14]))
    np.testing.assert_array_equal(perm, [1, 2, 0])
    with pytest.raises(ValueError):
        restrict_shift(rdf, jj, ss, np.array([10, 11]))


def test_single_mode_is_exact_for_one_real_mode_and_refuses_rotation():
    rng = np.random.default_rng(2)
    xs, v = rng.standard_normal(30), rng.standard_normal(30)
    S = [xs + 0.95 ** j * v for j in range(3)]
    s, info = single_mode_extrapolate(S)
    np.testing.assert_allclose(s, xs, atol=1e-10)
    assert abs(info["lambda"] - 0.95) < 1e-12
    # a rotating pair: consecutive changes are not aligned -> refused
    w2 = rng.standard_normal(30)
    R = [xs + 0.95 ** j * (np.cos(1.2566 * j) * v + np.sin(1.2566 * j) * w2)
         for j in range(4)]
    s, info = single_mode_extrapolate(R)
    assert s is None and "aligned" in info["refused"]


def test_by_sector_removes_rotating_pairs_and_the_real_mode():
    """Synthetic period map with the L155 structure: a rotor-fixed pattern
    relabelled by the q = 5 image map each period (rotating pairs, |λ| 0.96)
    and a pole-pair periodic real mode (λ 0.95) 30x smaller in norm."""
    n_cell, q = 40, 5
    perm, sign = _cyclic_perm(n_cell, q)
    rng = np.random.default_rng(4)
    xs = rng.standard_normal(n_cell * q)
    P = rng.standard_normal(n_cell * q)                    # rotor-fixed pattern
    P -= sector_components(P, perm, sign, q, 1.0)["m0"]    # non-periodic part
    D = np.tile(rng.standard_normal(n_cell), q) * 0.03     # periodic real mode

    def state(j):
        Pj = P.copy()
        for _ in range(j):                                 # relabel j times
            Pj = sign * Pj[perm]
        return xs + 0.96 ** j * Pj + 0.95 ** j * D * np.linalg.norm(P) / np.linalg.norm(D) / 30
    X = [state(j) for j in range(2, 8)]
    e_last = np.linalg.norm(X[-1] - xs)
    s, info = extrapolate_by_sector(X, None, perm, sign)
    assert s is not None
    assert info["sectors"]["m0"]["method"] == "single_mode"
    assert np.linalg.norm(s - xs) < 1e-6 * e_last



def test_slow_tail_uses_the_given_ratio():
    from motor_ai_sim.simulation.periodic_accel import slow_tail_resid
    N = 10
    means = [5.0, 4.9, 4.905, 4.9]           # ripple-masked slow tail
    s = np.repeat(means, N)
    r09 = slow_tail_resid({"shaft": s}, N, 0.9)["shaft"]
    r095 = slow_tail_resid({"shaft": s}, N, 0.95)["shaft"]
    assert abs(r09 - 0.005 * 9 / 4.9) < 1e-12
    assert abs(r095 / r09 - 19 / 9) < 1e-9


def test_secant_calibrates_a_short_single_mode_jump_exactly():
    """Scalar slow mode λ = 0.95; the first jump used a biased λ̂ = 0.93
    (S_p = 13.3 instead of 19); one post-jump change fixes it along u_pre."""
    from motor_ai_sim.simulation.periodic_accel import secant_step_scale
    lam, lam_hat = 0.95, 0.93
    S_true, S_p = lam / (1 - lam), lam_hat / (1 - lam_hat)
    v = np.random.default_rng(3).standard_normal(50)
    xs = np.random.default_rng(4).standard_normal(50)
    a_prev = v / lam                             # error one period before
    u_pre = (lam - 1) * a_prev
    a_jump = lam * a_prev + S_p * u_pre          # after x += S_p·u_pre
    x = [xs + lam ** j * a_jump for j in (2, 3)]  # states 2 and 3 periods on
    S, info = secant_step_scale(u_pre, x[1] - x[0], S_p, 2)
    assert abs(S - S_true) < 1e-9 * S_true
    x_star = x[1] + (S / (1 + S)) ** 3 * (S - S_p) * u_pre
    np.testing.assert_allclose(x_star, xs, atol=1e-10)
