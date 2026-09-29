"""Eddy warm-up stop rule, fix 2026-09-28 (docs/EDDY_TIME_INTEGRATION_2026-09-25.md).

Synthetic per-frame series only (no FEM).  The Ø12 12s10p production log:
the machine settled after period 1 yet the warm-up ran 146 frames, because
(1) an 8 mW shaft was judged against its own 8 mW and (2) three periods used
the cap q = 0.9 on the larger of the two last changes, blind to a last
change of zero.
"""
import numpy as np

from motor_ai_sim.simulation.sb_postproc import eddy_period_resid

TOL = 0.02          # fem_solver_2d._EDDY_SETTLE_TOL
N = 37              # frames per electrical period in the Ø12 run (74 = 2 periods)
CU = 5.62903        # copper, identical in every period
SHAFT = [0.00824257, 0.00821184, 0.00821184, 0.00821184]
MAG = [0.0328032, 0.0327882, 0.0327882, 0.0327882]


def _flat(means, n=N, ripple=0.0):
    k = np.arange(n)
    shape = 1.0 + ripple * np.cos(2 * np.pi * 6 * k / n)
    return np.concatenate([m * shape for m in means])


def _stop_period(groups, extra, n=N, max_p=4):
    """First whole-period count at which the gauge passes (None = never)."""
    for p in range(2, max_p + 1):
        r, _, _ = eddy_period_resid(
            {k: v[:p * n] for k, v in groups.items()}, n, machine_extra_W=extra)
        if r <= TOL:
            return p
    return None


def test_d12_log_stops_at_two_periods():
    g = {"cu": _flat([CU] * 4), "shaft": _flat(SHAFT, ripple=0.2),
         "mag": _flat(MAG, ripple=0.1)}
    assert _stop_period(g, CU) == 2                       # 74 warm-up frames
    r, per, nP = eddy_period_resid({k: v[:2 * N] for k, v in g.items()}, N,
                                   machine_extra_W=CU)
    assert nP == 2 and per["shaft"] < 1e-3 and per["mag"] < 1e-3


def test_d12_old_rule_did_not_stop_at_two():
    """Before the fix (every body own-relative): the shaft reads ~3.4 %."""
    g = {"shaft": _flat(SHAFT[:2]), "mag": _flat(MAG[:2])}
    r, per, _ = eddy_period_resid(g, N)                   # no machine total
    assert per["shaft"] > TOL and 0.03 < per["shaft"] < 0.04


def test_big_slow_shaft_keeps_own_relative_test():
    """L155-like: 15 W shaft beside kW, decaying slowly.  Its share of the
    machine is < 2 %, but at >= 1 W it keeps the own-relative test and the
    march continues until that alone is below 2 %."""
    n = 36
    means = [15.0 + 10.0 * 0.7 ** j for j in range(30)]
    g = {"shaft": _flat(means, n), "mag": _flat([3880.0] * 30, n)}
    extra = 2000.0
    stop = _stop_period(g, extra, n, max_p=30)
    assert stop is not None and stop > 4
    for p in range(2, stop):
        _, per, _ = eddy_period_resid({k: v[:p * n] for k, v in g.items()}, n,
                                      machine_extra_W=extra)
        assert per["shaft"] > TOL
    # own-relative at the stop: the tail vs the shaft's own level
    _, per, _ = eddy_period_resid({k: v[:stop * n] for k, v in g.items()}, n,
                                  machine_extra_W=extra)
    assert per["shaft"] <= TOL


def test_three_periods_measured_q():
    # last change zero -> q = 0 -> settled, not 9x the first change
    r, per, _ = eddy_period_resid({"g": _flat([1.0, 1.1, 1.1])}, N)
    assert r == 0.0
    # q = 0.5 measured: tail = |d2| * 1 = 0.05 of 1.15
    r, _, _ = eddy_period_resid({"g": _flat([1.0, 1.1, 1.15])}, N)
    assert abs(r - 0.05 / 1.15) < 1e-12
    # q >= cap: the cap and the larger change, as before
    r, _, _ = eddy_period_resid({"g": _flat([1.0, 1.1, 1.195])}, N)
    assert abs(r - 0.1 * 9.0 / 1.195) < 1e-9
