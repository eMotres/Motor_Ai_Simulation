"""DELTA under an imposed-voltage / PWM source, run on the STAR circuit.

``drive.circuit_residual_ll`` is the isolated-neutral STAR circuit, so the route
used to REFUSE ``drive='pwm_voltage'`` on a delta winding — which left L155,
L180 and the whole Ø200 line with no PWM answer at all.  The substitution that
replaced the refusal (user 2026-09-14 / PWM study §0.3, B1/B2) rests on two
claims, and this file pins both as ALGEBRA — no mesh, no FEM, milliseconds:

* the star model's BRANCH voltage on a bus of √3·V_dc is the real bridge's
  LINE-TO-LINE voltage, harmonic for harmonic (exact when the carrier count per
  electrical period divides by 3, ≤ 0.1 % otherwise);
* the modulation index the gate checks is the REAL bridge's — in delta the
  drive is handed the BRANCH voltage, which IS the line voltage, so the
  per-phase fundamental it faces is V₁/√3.

The numbers below are the measured L155 peak duty (CIANO10 200 opt, 20 000 rpm,
f_el 1666.67 Hz, V₁ 571.478 V peak on the branch, pack 549.6 / 750.4 / 850.4 V).
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from motor_ai_sim.simulation.pwm import (
    MAX_MODULATION_INDEX, ExcitationError, build_pwm_source,
    carriers_per_period, is_delta, modulation_index, star_equivalent_bus)

POLE_PAIRS = 5
F_ELEC = 20000.0 * POLE_PAIRS / 60.0      # 1666.667 Hz
V1_BRANCH = 571.4778                      # V peak, delta branch = line voltage
V_DELTA = 35.73
SQ3 = math.sqrt(3.0)


def _branch_spectrum(src, n_per_carrier: int = 64):
    """Amplitude spectrum of ONE branch of the source, over one electrical
    period, from the source's own exact per-interval volt-second means.

    For the real bridge the branch is v_A − v_B (a delta winding sits across two
    poles); for the star model it is v_A − common mode (the neutral floats).
    Which one is taken is decided by the caller through ``pick``.
    """
    n = n_per_carrier * src.carriers
    th = np.linspace(0.0, 360.0 / POLE_PAIRS, n + 1)     # mech deg, 1 el period
    out = {k: np.zeros(n) for k in "ABC"}
    for k in range(n):
        m = src.mean_voltages(th[k], th[k + 1])
        for p in "ABC":
            out[p][k] = m[p]
    return out


def _spec(x: np.ndarray) -> np.ndarray:
    return np.abs(np.fft.rfft(x) / len(x) * 2.0)


@pytest.mark.parametrize("f_sw, exact", [(24000.0, False), (48000.0, False),
                                         (25000.0, True), (50000.0, True)])
def test_star_on_root3_bus_is_the_delta_branch(f_sw: float, exact: bool):
    """The whole substitution, measured: same fundamental, same ripple."""
    v_dc = 750.4
    real = build_pwm_source(pole_pairs=POLE_PAIRS, daxis_deg=0.0,
                            v_phase_peak=V1_BRANCH / SQ3, v_delta_deg=V_DELTA,
                            v_bus=v_dc, f_switch_hz=f_sw, f_elec_hz=F_ELEC)
    model = build_pwm_source(pole_pairs=POLE_PAIRS, daxis_deg=0.0,
                             v_phase_peak=V1_BRANCH, v_delta_deg=V_DELTA,
                             v_bus=star_equivalent_bus(v_dc, "delta"),
                             f_switch_hz=f_sw, f_elec_hz=F_ELEC,
                             v_bus_real=v_dc)
    assert model.carriers == real.carriers == carriers_per_period(f_sw, F_ELEC)

    vr = _branch_spectrum(real)
    v_ab = vr["A"] - vr["B"]                       # the DELTA winding's voltage
    vm = _branch_spectrum(model)
    cm = (vm["A"] + vm["B"] + vm["C"]) / 3.0
    v_y = vm["A"] - cm                             # the star model's branch

    s_ab, s_y = _spec(v_ab), _spec(v_y)
    # Fundamental: the line voltage the machine is asked for, either way.
    assert s_ab[1] == pytest.approx(V1_BRANCH, rel=2e-4)
    assert s_y[1] == pytest.approx(s_ab[1], rel=2e-4)
    # …and the SWITCHING content, which is what the run is actually for.
    rip_ab = math.sqrt(float(np.sum(s_ab[2:] ** 2)) / 2.0)
    rip_y = math.sqrt(float(np.sum(s_y[2:] ** 2)) / 2.0)
    assert rip_ab > 100.0                          # there IS a carrier here
    # EXACT when the three legs are true time-shifted copies on the shared
    # carrier (carriers per period divisible by 3).  Otherwise the edges fall
    # differently in each leg and the ripple rms differs by an amount that
    # depends on the load angle: measured 0.03-0.07 % at the study's 34.26° and
    # 0.19-0.37 % at this duty's 35.73°, i.e. ≲ 0.8 % on the carrier's added
    # loss (which goes as the ripple squared).
    assert rip_y == pytest.approx(rip_ab, rel=(1e-9 if exact else 5e-3))
    # …and the rms, which is what every Σ|I_h|²·R loss is built on.
    rms_ab = float(np.sqrt(np.mean(v_ab ** 2)))
    rms_y = float(np.sqrt(np.mean(v_y ** 2)))
    assert rms_y == pytest.approx(rms_ab, rel=(1e-9 if exact else 5e-3))
    if exact:
        # Per ORDER as well, not just in aggregate.  (At a carrier count NOT
        # divisible by 3 the sidebands redistribute — a single order can differ
        # by over 100 % — which is why the assertions above are on the rms.)
        n_h = min(len(s_ab), 61)
        for h in range(1, n_h):
            assert s_y[h] == pytest.approx(s_ab[h], abs=1e-3 * s_ab[1])
    # WHAT IS NOT PRESERVED, pinned so nobody "fixes" it silently: the
    # per-harmonic phases carry the star-delta rotation, so the two are not the
    # same waveform in time.  A two-level bridge's line voltage peaks at V_dc;
    # the model branch peaks at ⅔ of the model bus = 1.1547·V_dc.  Anything
    # built on the solved INSTANTANEOUS peak is the model's, not the bridge's.
    assert float(np.max(np.abs(v_ab))) == pytest.approx(v_dc, rel=1e-9)
    assert float(np.max(np.abs(v_y))) == pytest.approx(
        2.0 / 3.0 * star_equivalent_bus(v_dc, "delta"), rel=1e-9)
    assert (model.carriers % 3 == 0) is exact


def test_modulation_index_is_the_real_bridges():
    """The B2 gate: √3 too large when fed the branch voltage of a delta."""
    assert is_delta("delta") and not is_delta("star") and not is_delta(None)
    # Star: unchanged, byte for byte.
    assert modulation_index(V1_BRANCH, 750.4) == pytest.approx(1.5231, abs=5e-4)
    # Delta: the pack's three corners, as the study measured them.
    assert modulation_index(V1_BRANCH, 549.6, star_delta="delta") == \
        pytest.approx(1.2007, abs=5e-4)
    assert modulation_index(V1_BRANCH, 750.4, star_delta="delta") == \
        pytest.approx(0.8794, abs=5e-4)
    assert modulation_index(V1_BRANCH, 850.4, star_delta="delta") == \
        pytest.approx(0.7760, abs=5e-4)
    # v_nom is INSIDE the linear limit and v_min is outside it — the two
    # statements the old gate got backwards on every delta machine.
    assert modulation_index(V1_BRANCH, 750.4, star_delta="delta") < MAX_MODULATION_INDEX
    assert modulation_index(V1_BRANCH, 549.6, star_delta="delta") > MAX_MODULATION_INDEX
    # The branch V₁ against the MODEL bus is the same number, which is why the
    # source needs no second, delta-aware gate of its own.
    assert modulation_index(V1_BRANCH, 750.4, star_delta="delta") == \
        pytest.approx(2.0 * V1_BRANCH / star_equivalent_bus(750.4, "delta"))


def test_refusal_names_the_real_dc_link():
    """A refused point must name the link the user can change, not the model."""
    with pytest.raises(ExcitationError) as ei:
        build_pwm_source(pole_pairs=POLE_PAIRS, daxis_deg=0.0,
                         v_phase_peak=V1_BRANCH, v_delta_deg=V_DELTA,
                         v_bus=star_equivalent_bus(549.6, "delta"),
                         f_switch_hz=24000.0, f_elec_hz=F_ELEC,
                         v_bus_real=549.6)
    msg = str(ei.value)
    assert "1.201" in msg                     # the REAL bridge's index
    assert "549.6 V DC link" in msg           # the pack minimum, not 951.9
    assert "951.9" in msg                     # …and the model bus, named
    # Star keeps the message it has always had.
    with pytest.raises(ExcitationError) as ei2:
        build_pwm_source(pole_pairs=POLE_PAIRS, daxis_deg=0.0,
                         v_phase_peak=7.0, v_delta_deg=10.0, v_bus=10.0,
                         f_switch_hz=28000.0, f_elec_hz=1500.0)
    assert "on a 10.0 V bus" in str(ei2.value)


def test_star_equivalent_bus_is_a_noop_in_star():
    assert star_equivalent_bus(750.4, "star") == 750.4
    assert star_equivalent_bus(750.4, None) == 750.4
    assert star_equivalent_bus(750.4, "delta") == pytest.approx(1299.73, abs=0.01)
