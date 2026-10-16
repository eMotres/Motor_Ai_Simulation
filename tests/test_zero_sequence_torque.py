"""Zero-sequence torque on per-coil / open-winding drives (2026-09-27).

The flux-linkage space-vector mean drops the zero sequence, so a triplen
current's torque was invisible on eddy/demag runs (six-coil study, case (c),
-0.92 N·m).  Algebra only, no FEM: with a zero-sequence 3rd harmonic in both
ψ and i, space vector + zero-sequence term must equal the all-phase terminal
work ``n_par·<Σ i·dψ/dθ_m>`` exactly; a three-wire call is unchanged bit for
bit.
"""
from __future__ import annotations

import numpy as np
import pytest

from motor_ai_sim.simulation import sb_postproc as sp

P = 5
N = 360


def _machine(a3_i: float, a3_psi: float = 0.004, n: int = N):
    th_m = 2.0 * np.pi / P * np.arange(n) / n          # one electrical period
    th_e = P * th_m
    off = (0.0, 2.0 * np.pi / 3.0, -2.0 * np.pi / 3.0)
    psi = [0.02 * np.cos(th_e - o) + a3_psi * np.cos(3.0 * (th_e - o) + 0.4)
           for o in off]
    cur = [-100.0 * np.sin(th_e - o) + a3_i * 100.0 * np.sin(3.0 * (th_e - o) + 1.0)
           for o in off]
    mx = np.full(n, 3.0) + 0.1 * np.cos(6.0 * th_e)
    return psi, cur, mx, th_m


@pytest.mark.parametrize("npar", [1, 2])
def test_zero_sequence_term_closes_on_terminal_work(npar):
    psi, cur, mx, th = _machine(a3_i=0.05)
    tw = sp.terminal_work_mean(*psi, *cur, th, P, npar)
    sv, m_sv = sp.space_vector_hybrid_torque(*psi, *cur, mx, P, n_parallel=npar)
    zs, m_zs = sp.space_vector_hybrid_torque(*psi, *cur, mx, P, n_parallel=npar,
                                             zero_sequence=True,
                                             mechanical_angle_rad=th)
    assert m_sv == "energy_mean+maxwell_ripple"
    assert m_zs == "energy_mean+zero_sequence+maxwell_ripple"
    assert sp.TORQUE_MEAN_SOURCE[m_zs] == "flux_linkage_space_vector+zero_sequence"
    # the plain space vector misses a material share …
    assert abs(np.mean(sv) - tw) > 1e-3 * abs(tw)
    # … which the zero-sequence term restores to round-off
    assert np.mean(zs) == pytest.approx(tw, rel=1e-10, abs=1e-12)
    # the AC is still the raw Maxwell AC
    np.testing.assert_allclose(np.asarray(zs) - np.mean(zs), mx - mx.mean(),
                               atol=1e-12)


def test_no_zero_sequence_current_adds_nothing():
    psi, cur, mx, th = _machine(a3_i=0.0)
    sv, _ = sp.space_vector_hybrid_torque(*psi, *cur, mx, P)
    zs, _ = sp.space_vector_hybrid_torque(*psi, *cur, mx, P, zero_sequence=True,
                                          mechanical_angle_rad=th)
    assert np.mean(zs) == pytest.approx(np.mean(sv), abs=1e-9)


def test_three_wire_path_is_bit_identical():
    """hybrid_torque without the flag is the pre-2026-09-27 call exactly."""
    psi, cur, mx, th = _machine(a3_i=0.05)
    kw = dict(mechanical_angle_rad=th, imposed_current_drive=True, eddy=True,
              all_frames_converged=True, integer_period_window=True)
    a, ma = sp.hybrid_torque(*psi, *cur, mx, P, n_parallel=2, **kw)
    b, mb = sp.space_vector_hybrid_torque(*psi, *cur, mx, P, n_parallel=2)
    assert ma == mb == "energy_mean+maxwell_ripple"
    assert a == b                                      # exact, list equality
    c, mc = sp.hybrid_torque(*psi, *cur, mx, P, n_parallel=2,
                             zero_sequence=True, **kw)
    assert mc == "energy_mean+zero_sequence+maxwell_ripple"
    assert np.mean(c) == pytest.approx(
        sp.terminal_work_mean(*psi, *cur, th, P, 2), rel=1e-10)


def test_non_uniform_angles_fall_back_to_differences():
    psi, cur, mx, th = _machine(a3_i=0.05, n=2000)
    tw = sp.terminal_work_mean(*psi, *cur, th, P, 1)
    th2 = th.copy()
    th2[1:-1] += 1e-9 * np.sin(np.arange(1, th.size - 1))   # break uniformity
    zs, _ = sp.space_vector_hybrid_torque(*psi, *cur, mx, P, zero_sequence=True,
                                          mechanical_angle_rad=th2)
    assert np.mean(zs) == pytest.approx(tw, rel=2e-3)


def test_zero_sequence_needs_angles():
    psi, cur, mx, _ = _machine(a3_i=0.05)
    with pytest.raises(ValueError):
        sp.space_vector_hybrid_torque(*psi, *cur, mx, P, zero_sequence=True)


def test_only_the_per_coil_source_declares_a_zero_sequence_path():
    from motor_ai_sim.simulation import excitation as ex
    from motor_ai_sim.simulation.per_coil import PerCoilCurrentSource
    assert PerCoilCurrentSource.zero_sequence_path is True
    for cls in (ex.SineCurrentSource, ex.CustomCurrentSource,
                ex.BldcCurrentSource, ex.SineVoltageSource,
                ex.PwmVoltageSource):
        assert not getattr(cls, "zero_sequence_path", False), cls.__name__
