"""Commensurate rotor-frame window for the rotor iron loss — exact, no filter.

WHY. The solver reports ONE electrical period. On the STATOR that window is
closed: after one electrical period the rotor has advanced one pole pair, which
is a symmetry of the rotor, and the currents repeat, so the stator-frame field
repeats. A ROTOR element does not come back to the same place relative to the
slots: on 12s/10p it has slid 12/5 = 2.4 slot pitches, on 12s/14p and 24s/28p
12/7 = 1.71. Its one-period B(t) therefore ends mid slot-passing cycle, and a
DFT (or the periodic dB/dt, or a peak-to-peak) of that open window reads the
end-to-start step as broadband content billed up to Nyquist — which is why the
raw rotor loss grew with the step count (×1.18 @ 12 → ×3.35 @ 120 samples) and
why 68de0ca hid it with a ramp-removal "leakage guard" (a filter, now retired).

THE EXACT CURE, AT ZERO SOLVE COST. The stator-frame field F is periodic in one
electrical period Θe = 2π/p (mechanical) — F(x, θ + Θe) = F(x, θ). With the
rotor mesh fixed in rotor coordinates y (the sliding band encodes rotation in
the slip pairing, not in coordinates), the rotor-frame field is
G(y, θ) = R(−θ) F(R(θ) y, θ), and therefore

    G(y, θ + k·Θw) = R(−k·Θw) · G(R(k·Θw) y, θ)                    (1)

for a captured window of Θw = M·Θe (M whole electrical periods): what element
y sees k windows later is what the element one (k·M) pole pairs further on
sees NOW, rotated back. If R(k·Θw) y leaves the modelled sector (angle Φ =
2π/NS, boundary sign s = ±1), the sector condition G(R(Φ) z) = s R(Φ) G(z)
folds it back: B_y(θ + kΘw) = s^f · R(fΦ − kΘw) · B_z(θ), z = R(kΘw − fΦ) y.

So the rotor history over q windows is assembled from the SOLVED frames of the
image elements — no extra solve, no interpolation — provided the rotor mesh is
itself pole-pair periodic, i.e. every rotated iron-element centroid lands on an
iron-element centroid. That is checked per element (tolerance 1e-6 of the
smallest element size); the iron-template and geo meshes the product builds
pass it to ~1e-13 (measured on the 30 mm fixture, the Ø40 L12 and the L155,
sector and full ring). If it fails — a mesh that is not periodic — this module
says so and the caller keeps the raw one-period window with the flag
``closed = False``: an honest open window, never a filtered one.

HOW MANY WINDOWS. q is the smallest integer for which q·M·Θe is a symmetry of
the MODEL (a whole number f of sector angles with s^f = +1). Then the element
maps onto itself with no rotation and the assembled window closes exactly:

    q·M·NS / p = f,   s^f = +1.

12s/14p, NS = 2 (s = −1): q = 7 (f = 2). 12s/10p, NS = 2: q = 5. 24s/28p,
NS = 4 (7 poles per sector, s = −1): q = 7 (f = 2). Full ring: q = p.

The PHYSICAL rotor-frame period can be shorter (the winding's phase
permutation symmetry): 7/6 of an electrical period on 12s/14p and 24s/28p,
5/6 on 12s/10p — so the smallest WHOLE number of electrical periods over which
the rotor field repeats is 7 and 5 respectively, which is exactly the q above
for the sector models (full-ring 24s/28p takes 14, twice the minimum: the DFT
of any multiple of the true period gives the same amplitudes, the extra bins
are empty). ``observed_period_electrical`` reports the period read off the
assembled data (the gcd of the populated DFT bins), so this is checked on
every run rather than asserted.

Only the rotor IRON loss uses it. Totals that are instantaneous sums over the
whole rotor (magnet / shaft σE², torque) are already exact over one period:
the sum over all rotor elements is invariant under the pole-pair image map.
"""
from __future__ import annotations

import math
from fractions import Fraction
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

__all__ = ["model_window_count", "pole_pair_image_maps", "assemble_history",
           "observed_period_electrical", "commensurate_rotor_window"]

#: Centroid match tolerance, relative to the smallest iron element's size.
MATCH_REL_TOL = 1e-6


def model_window_count(pole_pairs: int, n_sectors: int, bc_sign: int,
                       window_periods: int) -> int:
    """q — windows to chain so the rotor returns to a model-equivalent place.

    Solves ``q·M·NS/p = f`` (f whole sector angles) with ``s^f = +1``.
    """
    p, ns, m = int(pole_pairs), max(1, int(n_sectors)), int(window_periods)
    if p <= 0 or m <= 0:
        raise ValueError("pole_pairs and window_periods must be positive")
    r = Fraction(m * ns, p)                 # sector angles advanced per window
    q = r.denominator                       # smallest q with q·r whole
    f = (r * q).numerator
    if int(bc_sign) < 0 and ns > 1 and f % 2:
        q *= 2                               # s^f = +1 needs an even fold count
    return int(q)


def pole_pair_image_maps(centroids: np.ndarray, iron_idx: np.ndarray,
                         areas: np.ndarray, n_sectors: int, bc_sign: int,
                         window_rot_rad: float, q: int
                         ) -> Tuple[Optional[List[Tuple[np.ndarray, np.ndarray,
                                                        np.ndarray]]], Dict]:
    """Per k = 1..q−1: (image index, rotation angle, sign) for every iron element.

    ``centroids`` (2, E) of the rotor mesh (rotor coordinates), ``iron_idx`` the
    iron elements (their order is the history's column order), ``areas`` (E,)
    the element areas (for the tolerance). Returns ``(None, info)`` when any
    image is missing or ambiguous — the caller then keeps the open window.
    """
    from scipy.spatial import cKDTree
    idx = np.asarray(iron_idx, int)
    info: Dict = {"n_elements": int(idx.size)}
    if idx.size == 0 or q <= 1:
        return [], info
    cen = np.asarray(centroids, float)[:, idx]
    h = float(np.sqrt(np.min(np.asarray(areas, float)[idx])))
    tol = MATCH_REL_TOL * max(h, 1e-12)
    tree = cKDTree(cen.T)
    ns = max(1, int(n_sectors))
    phi = 2.0 * math.pi / ns
    s = -1 if (int(bc_sign) < 0 and ns > 1) else 1
    maps = []
    worst = 0.0
    for k in range(1, int(q)):
        a = k * float(window_rot_rad)
        c, sn = math.cos(a), math.sin(a)
        xr = np.vstack([c * cen[0] - sn * cen[1], sn * cen[0] + c * cen[1]])
        best_d = np.full(idx.size, np.inf)
        best_j = np.zeros(idx.size, int)
        best_f = np.zeros(idx.size, int)
        for f in range(ns):
            b = -f * phi
            cb, sb = math.cos(b), math.sin(b)
            z = np.vstack([cb * xr[0] - sb * xr[1], sb * xr[0] + cb * xr[1]])
            d, j = tree.query(z.T)
            better = d < best_d
            best_d[better] = d[better]
            best_j[better] = j[better]
            best_f[better] = f
        worst = max(worst, float(best_d.max()))
        if not np.all(best_d < tol) or np.unique(best_j).size != idx.size:
            info.update({"failed_k": k, "max_match_distance_m": worst,
                         "tolerance_m": tol,
                         "unmatched": int(np.sum(best_d >= tol))})
            return None, info
        ang = best_f * phi - a
        sign = np.where(best_f % 2 == 1, s, 1).astype(float) if s < 0 \
            else np.ones(idx.size)
        maps.append((best_j, ang, sign))
    info.update({"max_match_distance_m": worst, "tolerance_m": tol})
    return maps, info


def assemble_history(X: np.ndarray, Y: np.ndarray,
                     maps: Sequence[Tuple[np.ndarray, np.ndarray, np.ndarray]]
                     ) -> Tuple[np.ndarray, np.ndarray]:
    """Chain the solved window with its q−1 images — equation (1). No filter."""
    X = np.asarray(X, float); Y = np.asarray(Y, float)
    xs, ys = [X], [Y]
    for j, ang, sign in maps:
        c = np.cos(ang)[None, :]; sn = np.sin(ang)[None, :]
        bx = X[:, j]; by = Y[:, j]
        xs.append(sign[None, :] * (c * bx - sn * by))
        ys.append(sign[None, :] * (sn * bx + c * by))
    return np.vstack(xs), np.vstack(ys)


def observed_period_electrical(X: np.ndarray, Y: np.ndarray,
                               window_periods: float,
                               rel_floor: float = 1e-3) -> Optional[Fraction]:
    """Rotor-frame period read off the data: gcd of the populated DFT bins.

    Bin m of an L-sample window of W electrical periods sits at m/W f_e; if
    only multiples of g are populated (above ``rel_floor`` of the largest AC
    amplitude) the waveform repeats every W/g electrical periods.
    """
    A = np.abs(np.fft.rfft(np.hstack([np.asarray(X, float),
                                      np.asarray(Y, float)]), axis=0))
    if A.shape[0] < 2:
        return None
    amp = A[1:].max(axis=1)
    top = float(amp.max()) if amp.size else 0.0
    if top <= 0.0:
        return None
    live = np.flatnonzero(amp > rel_floor * top) + 1
    g = 0
    for m in live:
        g = math.gcd(g, int(m))
    if g == 0:
        return None
    return Fraction(Fraction(window_periods).limit_denominator(1000), g)


def commensurate_rotor_window(hist_x: Sequence, hist_y: Sequence,
                              centroids: np.ndarray, iron_idx: np.ndarray,
                              areas: np.ndarray, *, pole_pairs: int,
                              n_sectors: int, bc_sign: int,
                              theta_rad: Sequence[float],
                              window_periods: float) -> Dict:
    """Everything the solver needs: the assembled history or why not.

    Returns ``{"closed": bool, "X", "Y", "q", "window_periods_total",
    "method", "reason", ...}``. ``closed=False`` means: keep the raw one-period
    window and say so — never a filter.
    """
    out: Dict = {"closed": False, "q": 1, "method": "open_one_period_window",
                 "reason": None, "window_periods_captured": float(window_periods)}
    X = np.asarray(hist_x, float); Y = np.asarray(hist_y, float)
    n = int(X.shape[0]) if X.ndim == 2 else 0
    th = np.asarray(theta_rad, float)
    if n < 2 or X.shape != Y.shape or th.size != n:
        out["reason"] = "rotor history and angle samples are not aligned"
        return out
    m = float(window_periods)
    if not (m > 0 and abs(m - round(m)) < 1e-9):
        out["reason"] = "captured window is not a whole number of electrical periods"
        return out
    M = int(round(m))
    steps = np.diff(th)
    step = float(steps[0])
    if step == 0.0 or not np.allclose(steps, step, rtol=1e-9,
                                      atol=1e-12 * max(1.0, abs(step))):
        out["reason"] = "rotor angles are not uniformly spaced"
        return out
    theta_w = n * step                                    # signed, mechanical
    if not math.isclose(abs(theta_w), M * 2.0 * math.pi / int(pole_pairs),
                        rel_tol=1e-9, abs_tol=1e-12):
        out["reason"] = ("rotor angles do not span the %d electrical period(s) "
                         "the window claims" % M)
        return out
    q = model_window_count(pole_pairs, n_sectors, bc_sign, M)
    out.update({"q": int(q), "window_periods_total": float(q * M),
                "window_rotation_mech_deg": math.degrees(theta_w)})
    if q == 1:
        out.update({"closed": True, "X": X, "Y": Y,
                    "method": "captured_window_already_commensurate"})
    else:
        maps, info = pole_pair_image_maps(centroids, iron_idx, areas,
                                          n_sectors, bc_sign, theta_w, q)
        out["mesh_match"] = info
        if maps is None:
            out["reason"] = ("rotor mesh is not pole-pair periodic (%d iron "
                             "elements without an exact image at k=%s)"
                             % (info.get("unmatched", -1), info.get("failed_k")))
            return out
        Xq, Yq = assemble_history(X, Y, maps)
        out.update({"closed": True, "X": Xq, "Y": Yq,
                    "method": "pole_pair_image_mapping"})
    per = observed_period_electrical(out["X"], out["Y"], q * M)
    out["observed_period_electrical"] = (None if per is None else str(per))
    return out
