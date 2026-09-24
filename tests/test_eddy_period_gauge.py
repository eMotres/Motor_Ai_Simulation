"""Whole-period, per-body eddy settle gauge + the period shift map
(no-filter pass 2026-09-24, docs/NO_FILTERS_2026-09-24.md item 7).

No FEM: synthetic per-frame σE² series with a strong angular ripple and a
slow decaying start-up tail, the situation the old 3-sample / half-period
block gauge read as "settled" on the owner's L155 and L13 duties.
"""
import math

import numpy as np

from motor_ai_sim.simulation.sb_postproc import (
    EDDY_PERIOD_Q_CAP, eddy_period_resid, eddy_settle_resid)
from motor_ai_sim.simulation.rotor_window import period_shift_map


def _series(n_per, periods, level, ripple, tail, tau_periods):
    k = np.arange(n_per * periods)
    ph = 2 * np.pi * k / n_per
    return (level * (1.0 + ripple * np.cos(6 * ph) + 0.5 * ripple * np.cos(12 * ph))
            + tail * np.exp(-k / (tau_periods * n_per)))


def test_ripple_alone_reads_settled_to_round_off():
    s = _series(36, 3, 100.0, 0.3, 0.0, 1.0)
    r, per, nP = eddy_period_resid({"mag": s}, 36)
    assert nP == 3 and r < 1e-12 and per["mag"] < 1e-12


def test_slow_shaft_behind_fast_magnets_is_caught_per_group():
    """The L155 pattern: magnets kW and settled, the shaft tens of watts still
    decaying.  The SUM looks quiet; the shaft does not."""
    mag = _series(36, 4, 3880.0, 0.2, 0.0, 1.0)
    shaft = _series(36, 4, 15.0, 0.1, 10.0, 1.4)
    summed, _, _ = eddy_period_resid({"solid": mag + shaft}, 36)
    per_body, per, _ = eddy_period_resid({"mag": mag, "shaft": shaft}, 36)
    assert summed < 0.02                      # the sum gauge would pass it
    assert per_body > 0.02 and per["shaft"] > 0.02 and per["mag"] < 1e-9


def test_geometric_tail_is_extrapolated_from_four_periods():
    s = _series(40, 4, 10.0, 0.25, 4.0, 0.8)
    r, per, _ = eddy_period_resid({"g": s}, 40)
    m = [s[j * 40:(j + 1) * 40].mean() for j in range(4)]
    d = np.diff(m)
    q = min(max(d[1] / d[0], d[2] / d[1]), EDDY_PERIOD_Q_CAP)
    want = max(abs(d[2]), abs(d[1])) * q / (1 - q) / abs(m[3])
    assert math.isclose(r, want, rel_tol=1e-12)
    # three periods: the ratio is not trusted, the cap bounds the tail
    r3, _, _ = eddy_period_resid({"g": s[:120]}, 40)
    m3 = m[:3]
    want3 = max(abs(m3[2] - m3[1]), abs(m3[1] - m3[0])) * 9.0 / abs(m3[2])
    assert math.isclose(r3, want3, rel_tol=1e-9)


def test_one_flat_period_between_steep_ones_is_not_settled():
    """The L155 shaft pattern (W per period): 10.96, 8.68, 7.58, 7.45, then
    steeper again — a single small step must not pass the gauge."""
    n = 36
    base = np.ones(n)
    means = [10.96, 8.68, 7.58, 7.45]
    s = np.concatenate([mm * base for mm in means])
    r, per, nP = eddy_period_resid({"shaft": s, "mag": 3870.0 * np.ones(4 * n)},
                                   n)
    assert nP == 4 and per["shaft"] > 0.02


def test_two_periods_bound_the_tail_and_one_is_not_measurable():
    s = _series(24, 2, 5.0, 0.3, 0.01, 1.0)
    r2, _, n2 = eddy_period_resid({"g": s}, 24)
    d = abs(s[24:].mean() - s[:24].mean()) / abs(s[24:].mean())
    assert n2 == 2 and math.isclose(r2, 9.0 * d, rel_tol=1e-12)
    r1, per1, n1 = eddy_period_resid({"g": s[:30]}, 24)
    assert n1 == 1 and math.isinf(r1) and per1["g"] is None


def test_three_probe_samples_never_give_a_verdict():
    """The retired probe verdict judged three samples (eddy_settle_resid gives
    them a finite number); the period gauge refuses to — nothing about a slow
    body can be known from a fraction of a period."""
    s = _series(40, 3, 0.44, 0.3, 0.4, 1.4)
    r_old, _ = eddy_settle_resid(list(s[:3]), 40, 1e-4)
    r_new, _, _ = eddy_period_resid({"shaft": s[:3]}, 40)
    assert math.isfinite(r_old) and math.isinf(r_new)


def test_period_shift_map_carries_a_periodic_state_exactly():
    """A pole-pair periodic rotor node set with an antiperiodic half model:
    the state at θ − Θe is the image of the state at θ (equation (1))."""
    pp, ns = 7, 2
    aj = np.arange(0, 2 * pp // ns + 1) * math.pi / pp
    rings = [0.010, 0.013]
    pts = np.hstack([r * np.vstack([np.cos(aj + 0.05), np.sin(aj + 0.05)])
                     for r in rings]
                    + [0.012 * np.vstack([np.cos(aj), np.sin(aj)])])

    def field(x, y, th):
        phi = np.arctan2(y, x)
        return np.hypot(x, y) * (np.cos(pp * phi - pp * th)
                                 + 0.3 * np.cos((pp - 2) * phi + pp * th + .4))

    def rotor_state(th):
        c, s = math.cos(th), math.sin(th)
        return field(c * pts[0] - s * pts[1], s * pts[0] + c * pts[1], th)

    th1 = 0.37
    theta_e = 2 * math.pi / pp
    mp, info = period_shift_map(pts, 1e-4, n_sectors=ns, bc_sign=-1,
                                rot_rad=-theta_e)
    assert mp is not None, info
    j, sign = mp
    old = rotor_state(th1)
    np.testing.assert_allclose(sign * old[j], rotor_state(th1 - theta_e),
                               atol=1e-12)
