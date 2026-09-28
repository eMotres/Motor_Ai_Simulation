"""Periodic-steady-state accelerator for slow eddy bodies (2026-09-26).

The coupled eddy march is a fixed-point iteration of the ELECTRICAL-PERIOD MAP
Φ: the rotor-conductor state at θ = −dθ, carried back one period with the exact
pole-pair map (``rotor_window.period_shift_map``), goes to the same state one
period later.  On the periodic steady state x* = Φ(x*).  A slow body (the L155
hollow 42CrMo4 shaft: τ ≈ 17-18 electrical periods through μ(B)) makes Φ a
slowly contracting map whose error x_j − x* is dominated by a few eigenvectors
(one real mode λ ≈ 0.94, a complex pair from the q = 5 rotor period).

Reduced-rank extrapolation (RRE, Eddy 1979 / Mešina 1977) of the sequence of
period states removes those modes: with u_j = x_{j+1} − x_j,

    s = Σ_j γ_j x_j,   Σ_j γ_j = 1,   γ = argmin ‖Σ_j γ_j u_j‖_W .

For an affine map with k dominant modes it is exact from k + 2 states; for the
nonlinear map it is the first step of a cycled (Newton-like) method.  RRE, not
MPE: RRE's least-squares problem is always solvable (MPE divides by the last
coefficient), and it minimises the residual norm of the linearised map.  Not
shooting (Newton–Krylov on Φ): every Krylov vector costs one whole solved
period (36 nonlinear steps) and the directional derivative of a nonlinear
march needs its own tangent march — at least as many periods as RRE, with
far more code in the solver loop.

HONESTY.  This only moves the state in the DISCARDED warm-up prefix.  The
reported values come from the march that follows, judged by the unchanged
per-body whole-period gauge (``sb_postproc.eddy_period_resid``) on at least
``MIN_VERIFY_PERIODS`` continuous periods solved AFTER the last jump.  A wrong
extrapolation cannot pass: it would read as a transient in those periods.
Nothing here touches a reported value.

The norm W is the σ-mass diagonal (lumped) of the rotor conductors: the
residual is measured in the ohmic energy the transient carries, so a slow
milliwatt shaft is not drowned by kilowatt magnets merely by dof count.
"""
from __future__ import annotations

from typing import Dict, Optional, Sequence, Tuple

import numpy as np

# Continuous periods that must be solved after the last jump before the gauge
# may call the march settled (the owner's rule: ≥ 4).
MIN_VERIFY_PERIODS = 4
# A sector is extrapolated only when its own linear model explains its
# sequence: ‖Σγu‖ ≤ RESID_MAX·‖u_last‖.  Above that the sector is not yet a
# sum of a few geometric modes (a transient still being shed, or leakage
# from a larger sector) and a jump would kick it (measured on the L180:
# resid 0.81 in the pole-pair-periodic sector → a 3× loss spike after it).
RESID_MAX = 0.3
# Period changes this aligned are one real mode (see single_mode_extrapolate).
ALIGN_COS_MIN = 0.95
# Refuse a jump longer than this many last-period changes.  A mode with
# multiplier λ moves the state λ/(1−λ) of its last change on the way to the
# limit (λ = 0.94: 16×), so 100× is a mode with τ ≈ 100 periods; a longer
# step says the differences are dependent (noise amplified by the
# conditioning), not that a mode was removed.  The weights γ themselves are
# naturally large (∝ 1/(1−λ)) and are not the test.
STEP_MAX = 100.0


def rre_extrapolate(states: Sequence[np.ndarray],
                    weights: Optional[np.ndarray] = None,
                    ) -> Tuple[Optional[np.ndarray], Dict[str, object]]:
    """RRE limit of a fixed-point sequence ``states`` (k + 2 vectors, k ≥ 0).

    Returns ``(s, info)``; ``s`` is None when the problem is refused (too few
    states, non-finite data, a step > STEP_MAX last changes), with the reason in ``info``.
    ``info`` carries the weights γ, the residual ratio ‖Σγu‖/‖u_last‖ (the
    linearised prediction of how much of the last period change is left) and
    the step size relative to the last period change.
    """
    X = [np.asarray(x, float).ravel() for x in states]
    info: Dict[str, object] = {"n_states": len(X)}
    if len(X) < 3:
        info["refused"] = "fewer than 3 states"
        return None, info
    n = X[0].size
    if any(x.size != n for x in X):
        raise ValueError("period states differ in length")
    w = (np.ones(n) if weights is None
         else np.sqrt(np.maximum(np.asarray(weights, float).ravel(), 0.0)))
    if w.size != n:
        raise ValueError("weights and states differ in length")
    U = np.stack([(X[j + 1] - X[j]) * w for j in range(len(X) - 1)], axis=1)
    if not np.all(np.isfinite(U)):
        info["refused"] = "non-finite period states"
        return None, info
    u_last = float(np.linalg.norm(U[:, -1]))
    info["u_norms"] = [float(np.linalg.norm(U[:, j])) for j in range(U.shape[1])]
    if u_last == 0.0:
        info["refused"] = "already periodic (last change 0)"
        return None, info
    # min ‖Uγ‖ s.t. 1ᵀγ = 1  ⇔  γ = G⁻¹1 / 1ᵀG⁻¹1, G = UᵀU; solved by QR
    # (R⁻ᵀ1 then R⁻¹) with columns scaled to unit norm for conditioning.
    sc = np.array([max(v, 1e-300) for v in info["u_norms"]])
    Q, R = np.linalg.qr(U / sc)
    ones = 1.0 / sc
    try:
        z = np.linalg.solve(R.T, ones)
        y = np.linalg.solve(R, z)
    except np.linalg.LinAlgError:
        info["refused"] = "singular difference matrix"
        return None, info
    ssum = float(np.sum(y / sc))
    if not np.isfinite(ssum) or ssum == 0.0:
        info["refused"] = "degenerate normal equations"
        return None, info
    gamma = (y / sc) / ssum
    info["gamma"] = [float(g) for g in gamma]
    if not np.all(np.isfinite(gamma)):
        info["refused"] = "non-finite weights"
        return None, info
    s = np.zeros(n)
    for g, x in zip(gamma, X[:-1]):
        s += g * x
    info["resid_ratio"] = float(np.linalg.norm(U @ gamma) / u_last)
    info["step_over_last_change"] = float(
        np.linalg.norm((s - X[-1]) * w) / u_last)
    if info["step_over_last_change"] > STEP_MAX:
        info["refused"] = ("step %.3g x the last period change > %g"
                           % (info["step_over_last_change"], STEP_MAX))
        return None, info
    return s, info


# ── symmetry sectors of the pole-pair image map (2026-09-26) ─────────────────
# Measured on the L155 shaft (DMD of 26 period iterates): the slow modes of
# the period map are one real mode and two complex pairs whose angles are
# EXACTLY ±2π/5 and ±4π/5 — rotor-fixed DC patterns without pole-pair
# periodicity, which the per-period pole-pair relabelling S turns by one
# q-th of a turn (12s/10p, NS = 2: q = 5).  In the σ-norm the pairs are ~30×
# larger than the real (pole-pair periodic) DC mode, so ONE least-squares
# over the whole state removes the pairs and leaves the real mode — the one
# that carries the loss through μ(B) — untouched (measured: the extrapolated
# state's error along it equals the marched state's).  S is an exact symmetry
# (S^q = ±I on the rotor dofs), so the state splits into its eigenspaces
# (discrete Fourier over the q images) with no approximation, and RRE is run
# PER SECTOR, each on its own scale.


def restrict_shift(rdf: np.ndarray, jj: np.ndarray, ss: np.ndarray,
                   idx: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """The image map on the dof subset ``idx`` as (perm, sign) over positions
    in ``idx``: (S x)[i] = sign[i] · x[perm[i]].  ``rdf``/``jj``/``ss`` are the
    solver's rotor map (out[rdf] = ss · vec[rdf[jj]]).  Raises if ``idx`` is not
    closed under the map (a conductor dof whose image is not a conductor dof).
    """
    rdf = np.asarray(rdf, int)
    idx = np.asarray(idx, int)
    pos_r = {int(d): i for i, d in enumerate(rdf)}
    pos_i = {int(d): i for i, d in enumerate(idx)}
    perm = np.empty(idx.size, int)
    sign = np.empty(idx.size, float)
    for i, d in enumerate(idx):
        r = pos_r.get(int(d))
        if r is None:
            raise ValueError("dof %d is not a rotor dof" % int(d))
        src = int(rdf[int(jj[r])])
        p = pos_i.get(src)
        if p is None:
            raise ValueError("the image of dof %d leaves the subset" % int(d))
        perm[i] = p
        sign[i] = float(ss[r])
    return perm, sign


def shift_cycles(perm: np.ndarray, sign: np.ndarray, max_q: int = 256
                 ) -> Tuple[int, float, np.ndarray]:
    """Order q and sign σ of the image map, S^q = σ·I, on its REGULAR dofs.

    A dof is regular when it lies on a cycle of the map (perm^k(i) = i); the
    map is built non-bijectively by the solver, so a dof on the sector cut
    whose image is its partner's (a slaved copy) can lie on a tail instead.
    q is the most common cycle length (its lcm with the others must equal it)
    and σ the product of the signs around each cycle, which must agree.
    Returns ``(q, sigma, regular_mask)``; raises if the regular dofs do not
    share one (q, σ)."""
    perm = np.asarray(perm, int)
    sign = np.asarray(sign, float)
    n = perm.size
    qi = np.zeros(n, int)
    si = np.zeros(n)
    for i in range(n):
        j, s = i, 1.0
        for k in range(1, int(max_q) + 1):
            s *= sign[j]
            j = int(perm[j])
            if j == i:
                qi[i], si[i] = k, s
                break
    reg = qi > 0
    if not np.any(reg):
        raise ValueError("image map has no cycles <= %d" % max_q)
    vals, cnt = np.unique(qi[reg], return_counts=True)
    q = int(vals[np.argmax(cnt)])
    # a shorter cycle must divide q; its sign raised to q/len must agree
    sig = None
    for i in np.flatnonzero(reg):
        if q % qi[i]:
            raise ValueError("cycle lengths %s do not divide one order"
                             % sorted(set(vals.tolist())))
        s_q = si[i] ** (q // qi[i])
        if sig is None:
            sig = s_q
        elif s_q != sig:
            raise ValueError("cycles disagree on the sign of S^q")
    return q, float(sig), reg


def sector_components(x: np.ndarray, perm: np.ndarray, sign: np.ndarray,
                      q: int, sigma: float,
                      regular: Optional[np.ndarray] = None
                      ) -> Dict[str, np.ndarray]:
    """Real components of ``x`` in the eigenspaces of S (conjugate pairs
    merged); they sum to ``x`` exactly.  Keys ``"m<k>"`` with the angle
    θ_k = (2πk + φ0)/q, φ0 = 0 (σ = +1) or π (σ = −1).  Dofs outside
    ``regular`` (tails of the map, see :func:`shift_cycles`) are put whole
    into the first sector."""
    x = np.asarray(x, float)
    imgs = [x]
    for _ in range(q - 1):
        imgs.append(sign * imgs[-1][perm])
    phi0 = 0.0 if sigma > 0 else np.pi
    out: Dict[str, np.ndarray] = {}
    done = set()
    for k in range(q):
        th = (2.0 * np.pi * k + phi0) / q
        kc = (q - k - (0 if sigma > 0 else 1)) % q      # conjugate's index
        if k in done:
            continue
        done.update({k, kc})
        fac = 1.0 if kc == k else 2.0
        c = np.zeros_like(x)
        for j, im in enumerate(imgs):
            # Π_k + Π_k̄ = (fac/q) Σ_j cos(jθ_k) S^j
            c += np.cos(j * th) * im
        out["m%d" % k] = fac * c / q
    if regular is not None and not np.all(regular):
        irr = ~np.asarray(regular, bool)
        first = next(iter(out))
        for key in out:
            out[key][irr] = x[irr] if key == first else 0.0
    return out


def single_mode_extrapolate(states: Sequence[np.ndarray],
                            weights: Optional[np.ndarray] = None,
                            cos_min: float = ALIGN_COS_MIN,
                            ) -> Tuple[Optional[np.ndarray], Dict[str, object]]:
    """Limit of a sequence dominated by ONE real slow mode (vector Aitken).

    Used where the period changes u_j are aligned (cos ≥ ``cos_min``): the
    L155/L180 pole-pair-periodic shaft sector after the rotating sectors are
    removed (measured cos 0.986-1.000).  λ is the POOLED ratio of the change
    norms Σ‖u_{j+1}‖/Σ‖u_j‖ and s = x_last + λ/(1−λ)·u_last.  Not RRE/MPE
    there: for λ ≈ 0.95 their least squares trades the step against the
    residual of the few-percent non-modal part of u and comes out biased
    short (measured: RRE removed 20-50 %, MPE 60-75 %, this 70 % of the error
    from three states); the norm ratio is not biased by a component
    orthogonal to the mode."""
    X = [np.asarray(x, float).ravel() for x in states]
    info: Dict[str, object] = {"n_states": len(X), "method": "single_mode"}
    if len(X) < 3:
        info["refused"] = "fewer than 3 states"
        return None, info
    w = (np.ones(X[0].size) if weights is None
         else np.sqrt(np.maximum(np.asarray(weights, float).ravel(), 0.0)))
    U = [(X[j + 1] - X[j]) * w for j in range(len(X) - 1)]
    nu = [float(np.linalg.norm(u)) for u in U]
    if min(nu) == 0.0:
        info["refused"] = "a period change is 0"
        return None, info
    cs = [float(U[j] @ U[j + 1] / (nu[j] * nu[j + 1])) for j in range(len(U) - 1)]
    info["cos"] = [float("%.4g" % c) for c in cs]
    if min(cs) < cos_min:
        info["refused"] = "changes not aligned (cos %.3g < %g)" % (min(cs), cos_min)
        return None, info
    lam = sum(nu[1:]) / sum(nu[:-1])
    info["lambda"] = float(lam)
    if not (0.0 < lam < 1.0) or lam / (1.0 - lam) > STEP_MAX:
        info["refused"] = "lambda %.4g outside (0, %.4g)" % (
            lam, STEP_MAX / (1.0 + STEP_MAX))
        return None, info
    s = X[-1] + lam / (1.0 - lam) * (X[-1] - X[-2])
    info["step_over_last_change"] = float(lam / (1.0 - lam))
    return s, info


def extrapolate_by_sector(states: Sequence[np.ndarray], weights: Optional[np.ndarray],
                  perm: np.ndarray, sign: np.ndarray,
                  m0_secant: Optional[Dict[str, object]] = None,
                  ) -> Tuple[Optional[np.ndarray], Dict[str, object]]:
    """Extrapolation run separately in every symmetry sector of the image map
    S, summed.  Per sector: one real slow mode (aligned changes) →
    :func:`single_mode_extrapolate`; otherwise RRE, applied only when its
    linear model explains the sector.  A refused sector keeps its last marched
    component (loudly, in ``info``); the call is refused only if every sector
    is.

    ``m0_secant`` (after a single-mode jump of the first sector): {"u_pre":
    that sector's change before the jump, "S_p": the step taken, "n_after":
    periods from the jump to the first of ``states``}.  The first sector is
    then corrected ALONG u_pre by the calibrated step (secant_step_scale):
    x* = x_last + λ^m (S − S_p)·u_pre, m = n_after + len(states) − 1 — the
    clean pre-jump direction, so the small post-jump change only sets one
    scalar and its noise is not multiplied by S."""
    X = [np.asarray(s, float).ravel() for s in states]
    q, sigma, reg = shift_cycles(perm, sign)
    comps = [sector_components(x, perm, sign, q, sigma, reg) for x in X]
    s = np.zeros_like(X[0])
    info: Dict[str, object] = {"n_states": len(X), "q": int(q),
                               "sigma": float(sigma),
                               "irregular_dofs": int(np.sum(~reg)),
                               "sectors": {}}
    any_ok = False
    w = None if weights is None else np.asarray(weights, float)
    num = den = 0.0
    sw = np.ones_like(X[0]) if w is None else np.sqrt(np.maximum(w, 0.0))
    for key in comps[0]:
        seq = [c[key] for c in comps]
        sk = None
        ik: Dict[str, object] = {}
        if m0_secant is not None and key == next(iter(comps[0])):
            _up = np.asarray(m0_secant["u_pre"], float)
            _na = int(m0_secant["n_after"])
            S, ik = secant_step_scale(_up, seq[-1] - seq[-2], float(m0_secant["S_p"]),
                                      _na + len(seq) - 2, w)
            ik["method"] = "single_mode_secant"
            if S is not None:
                lam = S / (1.0 + S)
                m = _na + len(seq) - 1
                sk = seq[-1] + lam ** m * (S - float(m0_secant["S_p"])) * _up
                ik["lambda"] = lam
        if sk is None and len(seq) >= 3:
            sk, ik = single_mode_extrapolate(seq, w)
        if sk is None and len(seq) >= 3:
            why_single = ik.get("refused")
            sk, ik = rre_extrapolate(seq, w)
            ik["method"] = "rre"
            ik["single_mode_refused"] = why_single
            if sk is not None and ik.get("resid_ratio", 0.0) > RESID_MAX:
                ik["refused"] = ("linear model does not explain the sector "
                                 "(resid %.3g of the last change > %g)"
                                 % (ik["resid_ratio"], RESID_MAX))
                sk = None
        ent = {kk: (float("%.4g" % vv) if isinstance(vv, float) else vv)
               for kk, vv in ik.items() if kk in ("resid_ratio",
                                                   "step_over_last_change",
                                                   "refused", "method",
                                                   "lambda", "S_first",
                                                   "S_calibrated",
                                                   "response_ratio")}
        ent["last_change"] = float("%.4g" % np.linalg.norm((seq[-1] - seq[-2]) * sw))
        info["sectors"][key] = ent
        ent["applied"] = sk is not None
        if sk is not None and ik.get("method") == "single_mode":
            ent["_u_pre"] = seq[-1] - seq[-2]          # for a secant next cycle
            ent["S_step"] = float(ik["step_over_last_change"])
        if sk is None:
            s += seq[-1]
        else:
            any_ok = True
            s += sk
            num += float(np.linalg.norm((sk - seq[-1]) * sw)) ** 2
        den += float(np.linalg.norm((seq[-1] - seq[-2]) * sw)) ** 2
    if not any_ok:
        info["refused"] = "every sector refused"
        return None, info
    info["step_over_last_change"] = float(np.sqrt(num / max(den, 1e-300)))
    return s, info



def slow_tail_resid(groups: Dict[str, Sequence[float]], n_per_period: int,
                    q: float, floor_frac: float = 1e-3) -> Dict[str, float]:
    """The whole-period tail of ``sb_postproc.eddy_period_resid`` with the
    tail ratio q GIVEN instead of read off the loss means.

    Why: the gauge caps q at 0.9 (``EDDY_PERIOD_Q_CAP``) and reads it from the
    ratios of period-mean changes, which a few-mW period-to-period ripple
    (the residue of the rotating sectors) randomises.  After a jump the error
    that is left sits in the slow mode whose lambda the accelerator has
    MEASURED on the state (0.94-0.96 on L155/L180), so the tail is
    dmax*q/(1-q), up to 2x the gauge's.  This is an ADDITIONAL test after an
    accelerated march; the gauge itself is unchanged.  Returns {group: resid}.
    """
    N = max(1, int(n_per_period))
    out: Dict[str, float] = {}
    series = {k: np.asarray(v, float) for k, v in groups.items()}
    n = min((s.size for s in series.values()), default=0)
    nP = n // N
    if nP < 3:
        return {k: float("inf") for k in series}
    use = min(nP, 4)
    means = {k: [float(np.mean(s[s.size - (j + 1) * N: s.size - j * N]))
                 for j in range(use - 1, -1, -1)] for k, s in series.items()}
    total = sum(abs(m[-1]) for m in means.values())
    qq = float(q)
    for k, m in means.items():
        ref = max(abs(m[-1]), floor_frac * total, 1e-30)
        d = [m[i + 1] - m[i] for i in range(len(m) - 1)]
        dmax = max(abs(d[-1]), abs(d[-2]))
        out[k] = float(dmax * qq / (1.0 - qq) / ref)
    return out


def secant_step_scale(u_pre: np.ndarray, u_post: np.ndarray, S_p: float,
                      n_after: int, weights: Optional[np.ndarray] = None
                      ) -> Tuple[Optional[float], Dict[str, object]]:
    """True step factor S = λ/(1−λ) of a single slow mode, from the march's
    response to a jump x += S_p·u_pre along it.

    Scalar model along the mode: a_{j+1} = λ a_j, u = a_{j+1} − a_j, so the
    error at the jump is a = −S·u_pre and after it a' = (S_p − S)·u_pre.  The
    change measured ``n_after`` periods after the jump is
    u_post = −(1−λ)λ^{n_after}·a', hence

        r = ⟨u_post, u_pre⟩/⟨u_pre, u_pre⟩ = λ^{n_after+1}·(1 − S_p/S).

    The jump is a lever arm: r is O(0.1-1) where the ratio of two free-decay
    changes is 1 − 0.05 and has to be resolved to a per cent (measured on
    L155/L180: three free periods gave λ = 0.84-0.94 against 0.95-0.96; one
    response to a jump gave S within 1-3 % of the 70-period fit).
    Returns (S or None, info)."""
    w = (np.ones(np.asarray(u_pre).size) if weights is None
         else np.maximum(np.asarray(weights, float).ravel(), 0.0))
    a = np.asarray(u_pre, float).ravel()
    b = np.asarray(u_post, float).ravel()
    den = float(np.sum(w * a * a))
    info: Dict[str, object] = {"S_first": float(S_p)}
    if den <= 0.0 or not np.isfinite(den):
        info["refused"] = "no pre-jump change"
        return None, info
    r = float(np.sum(w * a * b)) / den
    info["response_ratio"] = r
    S = float(S_p)
    for _ in range(200):
        lam = S / (1.0 + S)
        d = 1.0 - r / lam ** (int(n_after) + 1)
        if not (d > 0.0) or not np.isfinite(d):
            info["refused"] = "response ratio %.3g inconsistent with a decay" % r
            return None, info
        S_new = float(S_p) / d
        if abs(S_new - S) <= 1e-12 * S:
            S = S_new
            break
        S = S_new
    if not (0.0 < S <= STEP_MAX):
        info["refused"] = "calibrated step %.3g outside (0, %g]" % (S, STEP_MAX)
        return None, info
    info["S_calibrated"] = S
    return S, info
