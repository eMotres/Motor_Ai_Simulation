"""Commensurate rotor-frame window (held-item fix of c582449, 2026-09-24).

No FEM: a synthetic stator-frame field with the machine's symmetries is
sampled in the rotor frame over the long window directly and compared with the
window ``rotor_window`` assembles from ONE electrical period — they must be the
same numbers, because equation (1) of the module is an identity.
"""
from fractions import Fraction
import math

import numpy as np
import pytest

from motor_ai_sim.simulation import losses
from motor_ai_sim.simulation.rotor_window import (
    assemble_history, commensurate_rotor_window, model_window_count,
    observed_period_electrical, pole_pair_image_maps)


@pytest.mark.parametrize("pp,ns,sign,m,q", [
    (7, 2, -1, 1, 7),     # 12s/14p half model (7 poles per sector)
    (5, 2, -1, 1, 5),     # 12s/10p half model
    (14, 4, -1, 1, 7),    # 24s/28p quarter model
    (7, 1, 1, 1, 7),      # full ring: one revolution
    (14, 1, 1, 1, 14),
    (4, 2, 1, 1, 2),      # 12s/8p half model, periodic sector
    (7, 2, -1, 7, 1),     # a 7-period capture is already commensurate
])
def test_window_count(pp, ns, sign, m, q):
    assert model_window_count(pp, ns, sign, m) == q


def _field(x, y, theta, pp):
    """Stator-frame B (x, y) at mechanical rotor angle theta.

    Radial field of odd spatial orders (anti-periodic over pi), time only via
    pp*theta (periodic in one electrical period): synchronous order pp, a
    backward order pp-2 and a slot-modulated order pp+12.
    """
    phi = np.arctan2(y, x)
    br = (np.cos(pp * phi - pp * theta)
          + 0.3 * np.cos((pp - 2) * phi + pp * theta + 0.4)
          + 0.2 * np.cos((pp + 12) * phi - pp * theta - 1.1))
    return br * np.cos(phi), br * np.sin(phi)


def _rotor_points(pp, ns):
    base = np.array([0.013, 0.071, 0.101]) * (math.pi / pp)
    radii = (0.010, 0.013)
    ang = np.concatenate([base + i * math.pi / pp
                          for i in range(2 * pp // ns)])
    pts = np.array([[r * math.cos(a), r * math.sin(a)]
                    for r in radii for a in ang]).T
    return pts


def _rotor_history(pts, thetas, pp):
    X, Y = [], []
    for th in thetas:
        c, s = math.cos(th), math.sin(th)
        xs = c * pts[0] - s * pts[1]; ys = s * pts[0] + c * pts[1]
        fx, fy = _field(xs, ys, th, pp)
        # back to rotor coordinates
        X.append(c * fx + s * fy); Y.append(-s * fx + c * fy)
    return np.array(X), np.array(Y)


@pytest.mark.parametrize("pp,ns,sign", [(7, 2, -1), (5, 2, -1), (7, 1, 1)])
def test_assembled_window_equals_the_long_solve(pp, ns, sign):
    pts = _rotor_points(pp, ns)
    n = 24
    step = 2 * math.pi / pp / n
    q = model_window_count(pp, ns, sign, 1)
    long_th = np.arange(q * n) * step
    Xl, Yl = _rotor_history(pts, long_th, pp)
    X1, Y1 = Xl[:n], Yl[:n]
    idx = np.arange(pts.shape[1])
    maps, info = pole_pair_image_maps(pts, idx, np.full(idx.size, 1e-8),
                                      ns, sign, n * step, q)
    assert maps is not None, info
    Xq, Yq = assemble_history(X1, Y1, maps)
    np.testing.assert_allclose(Xq, Xl, atol=1e-12)
    np.testing.assert_allclose(Yq, Yl, atol=1e-12)
    # ...and the window CLOSES: one more window maps every element onto itself.
    Xn, Yn = _rotor_history(pts, np.arange(q * n, q * n + n) * step, pp)
    np.testing.assert_allclose(Xn, X1, atol=1e-12)
    np.testing.assert_allclose(Yn, Y1, atol=1e-12)


def test_one_period_is_open_and_the_commensurate_window_converges():
    """The open window's raw loss grows with Nyquist; the closed one does not."""
    pp, ns, sign = 7, 2, -1
    pts = _rotor_points(pp, ns)

    class Surface:
        n_points = 3
        f = np.array([100.0, 1000.0])

        def w_per_m3(self, a, f, exc=None, w=None):     # classical-like: A²f²
            return np.asarray(a) ** 2 * (f / 1000.0) ** 2

    def losses_at(n):
        step = 2 * math.pi / pp / n
        X, Y = _rotor_history(pts, np.arange(n) * step, pp)
        r = commensurate_rotor_window(
            list(X), list(Y), pts, np.arange(pts.shape[1]),
            np.full(pts.shape[1], 1e-8), pole_pairs=pp, n_sectors=ns,
            bc_sign=sign, theta_rad=np.arange(n) * step, window_periods=1.0)
        assert r["closed"] and r["q"] == 7
        closed = losses.surface_loss_density(
            r["X"], r["Y"], Surface(), 1.0, 1000.0, r["window_periods_total"],
            legacy_comparator=False).sum()
        opened = losses.surface_loss_density(
            X, Y, Surface(), 1.0, 1000.0, 1.0, legacy_comparator=False).sum()
        return closed, opened, r

    c12, o12, r12 = losses_at(24)
    c72, o72, _ = losses_at(72)
    c144, o144, _ = losses_at(144)
    assert abs(c72 / c144 - 1.0) < 1e-9 and abs(c12 / c144 - 1.0) < 1e-9
    assert o144 > 1.5 * c144            # the open window over-reads, growing
    assert o144 > o72 > o12 * 0.999
    # rotor-frame content: orders pp-2 backward and pp+12 → 12/7 f_e only
    assert Fraction(r12["observed_period_electrical"]) == Fraction(7, 12)


def test_non_periodic_mesh_keeps_the_open_window_and_says_why():
    pp, ns, sign = 7, 2, -1
    pts = _rotor_points(pp, ns)
    pts[:, 3] *= 1.001                     # one element off its image
    n = 12
    step = 2 * math.pi / pp / n
    X, Y = _rotor_history(pts, np.arange(n) * step, pp)
    r = commensurate_rotor_window(
        list(X), list(Y), pts, np.arange(pts.shape[1]),
        np.full(pts.shape[1], 1e-8), pole_pairs=pp, n_sectors=ns,
        bc_sign=sign, theta_rad=np.arange(n) * step, window_periods=1.0)
    assert not r["closed"]
    assert "not pole-pair periodic" in r["reason"]


def test_misaligned_or_partial_windows_are_refused():
    pp = 7
    pts = _rotor_points(pp, 2)
    X, Y = _rotor_history(pts, np.arange(12) * 0.01, pp)
    kw = dict(pole_pairs=pp, n_sectors=2, bc_sign=-1)
    r = commensurate_rotor_window(list(X), list(Y), pts,
                                  np.arange(pts.shape[1]),
                                  np.ones(pts.shape[1]), theta_rad=np.arange(12) * 0.01,
                                  window_periods=1.0, **kw)
    assert not r["closed"] and "span" in r["reason"]
    r = commensurate_rotor_window(list(X), list(Y), pts,
                                  np.arange(pts.shape[1]),
                                  np.ones(pts.shape[1]),
                                  theta_rad=np.arange(12) * 0.01,
                                  window_periods=1.5, **kw)
    assert not r["closed"] and "whole number" in r["reason"]


def test_observed_period_of_a_pure_fundamental():
    t = np.arange(35) / 35.0 * 7.0                  # 7 periods, 5 per period
    X = np.cos(2 * np.pi * t)[:, None]
    assert observed_period_electrical(X, np.zeros_like(X), 7) == Fraction(1)
