"""The 2D leg of ``k_T`` — the denominator, built the way the numerator was.

``stage_b.torque.k_T`` sat null for two passes because the 3D torque and the 2D
torque were not the same quantity: a frozen-current co-energy window mean
against a running machine's period mean, through two different functionals.
``static3d.band2d`` removes all three differences by computing the 2D leg on the
SAME cross-section, at the SAME rotor shifts, from the SAME winding field, with
the SAME co-energy functional — so that what is left in the ratio is the third
dimension.

Three things have to be true for that to be worth anything, and all three are
cheap to check on a tiny mesh because all three are identities:

* the 2D weld must be a RE-LABELLING — a whole-sector shift maps the machine
  onto itself anti-periodically, so the co-energy must come back bit-identical;
* the two torque functionals must be ONE number with the magnets off — the 2D
  twin of the measurement that settled the functional question in 3D;
* the co-energy the difference is taken of must be the co-energy of the state
  the solver actually stationarised.  With an element-wise ``nu(|B|)`` that is
  true exactly when ``|B|`` is constant per element (P1, and the 3D N0 leg) and
  false when it is not (P2) — which is not a small error but the difference
  between a torque and a number that is not even smooth in the step.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from motor_ai_sim.simulation.static3d import band, band2d
from motor_ai_sim.simulation.static3d import loaded as LD
from motor_ai_sim.simulation.static3d.motor_geometry import load_motor_section
from motor_ai_sim.simulation.static3d.winding3d import build_winding_T

PRESET = "my_40mm_last"
STAGE_A_MATERIALS = {"stator_core": "B15AHV950M", "rotor_core": "B15AHV950M",
                     "magnet": "F45SH_120C", "shaft": "Aluminium_6061"}
# the Stage B operating point, verbatim — the currents the 3D leg was solved at
I_PH = {"A": 21.204323896810546, "B": 39.85358585161611,
        "C": -61.05790974842665}
# deliberately tiny: every assertion below is an identity, so mesh quality buys
# nothing and costs seconds (the same reasoning, and the same numbers, as
# tests/test_static3d_band.py)
N_RING, H_GAP, H_SOLID, BOX = 84, 1.1, 3.0, 1.6


def _preset(name: str = PRESET) -> dict:
    root = Path(__file__).resolve().parents[1]
    with (root / "config" / "motor_presets.json").open(encoding="utf-8") as fh:
        return dict(json.load(fh)[name])


@pytest.fixture(scope="module")
def section():
    from motor_ai_sim.config import get_config
    mats = (get_config() or {}).get("materials", {})
    for k, v in STAGE_A_MATERIALS.items():
        mats[k] = v
    return load_motor_section(geo_override=_preset()["geometry"])


@pytest.fixture(scope="module")
def banded(section):
    return band.build_banded_section(section, n_ring=N_RING, box_factor=BOX,
                                     h_gap=H_GAP, h_solid=H_SOLID)


@pytest.fixture(scope="module")
def winding(section):
    h_ew = (0.5 * float(section.geo["tooth_width"])
            + 0.5 * float(section.geo["wire_width"]))
    return build_winding_T(section, I_PH, h_ew_mm=h_ew), h_ew


@pytest.fixture(scope="module")
def units(section, winding):
    """The three unit-current source fields, built ONCE.

    Sampling ``Psi`` is the expensive part of a winding, and these depend on the
    geometry and the layout only — not on the operating point, and not on the
    rotor angle."""
    return LD.unit_phase_windings(section, h_ew_mm=winding[1])


# --------------------------------------------------------------------------
# the weld
# --------------------------------------------------------------------------

def test_a_whole_sector_shift_returns_the_same_machine_bit_for_bit(section,
                                                                   banded):
    """Rotating by a whole sector maps this machine onto itself with every
    magnet reversed, so the co-energy — which is quadratic in the field — must
    come back IDENTICAL, not merely close.

    It is the sharpest available check that the anti-periodic wrap sign and the
    ring re-labelling compose: a weld that dropped or mis-signed one constraint
    would still solve, and would land somewhere else."""
    m = band2d.Banded2D(section, banded, element_order=1, linear_iron=True)
    n_cell = banded.n_sec_ring - 1
    W0 = m.co_energy(m.solve(0))
    Wn = m.co_energy(m.solve(n_cell))
    assert W0 == Wn, (W0, Wn)
    assert W0 > 0.0


def test_the_solved_field_is_anti_periodic_across_both_cut_planes(section,
                                                                  banded):
    """``A(theta + sector) = -A(theta)`` on the nodes the weld paired, to
    round-off.  The cross-section is TORN at the mid-gap radius, so this has to
    hold separately on the rotor piece and on the stator piece."""
    m = band2d.Banded2D(section, banded, element_order=1, linear_iron=True)
    sol = m.solve(1)
    A = sol.A
    s = banded.sect
    mm = np.asarray(s.masters, dtype=np.int64)
    ss = np.asarray(s.slaves, dtype=np.int64)
    scale = float(np.max(np.abs(A)))
    err = float(np.max(np.abs(A[m._nodal[ss]]
                              - banded.bc_sign * A[m._nodal[mm]])))
    assert err <= 1e-12 * scale, err / scale


# --------------------------------------------------------------------------
# the functionals
# --------------------------------------------------------------------------

@pytest.mark.parametrize("order", [1, 2])
def test_with_the_magnets_off_the_two_2d_torque_functionals_are_ONE_number(
        section, banded, winding, units, order):
    """The 2D twin of the magnets-off measurement that settled the 3D functional.

    With ``M = 0`` and linear iron the whole co-energy is the winding's own,
    ``W' = 1/2 i^T L i = 1/2 sum_ph i_ph psi_ph``, so ``dW'/dtheta`` and
    ``1/2 sum_ph i_ph dpsi_ph/dtheta`` are the same number from the same solves.
    A difference here would be an implementation error in ``co_energy`` or in
    ``flux_linkage`` and nothing else — which is exactly why the identity is
    asserted POINTWISE first and only then differenced.

    ``co_energy_from_load`` is the third route: ``1/2 int(S . B)``, the discrete
    pairing the load was assembled with, computed without keeping ``f`` around.
    All three must be the same number."""
    wt, _h_ew = winding
    m = band2d.Banded2D(section, banded, element_order=order, I_ph=I_PH,
                        winding=wt, linear_iron=True, magnets_off=True)
    cur = band2d.co_energy_curve(m, [-1, 0, 1], units=units)
    W = np.asarray(cur["W_J"])
    W_load = np.asarray(cur["W_load_J"])
    W_wind = np.asarray([0.5 * sum(I_PH[p] * ps[p] for p in ps)
                         for ps in cur["psi"]])
    assert np.max(np.abs(W - W_load) / np.abs(W)) < 1e-10
    assert np.max(np.abs(W - W_wind) / np.abs(W)) < 1e-10
    (row,) = band2d.central_differences(cur, 1, I_ph=I_PH)
    assert row["T_coenergy_Nm"] == pytest.approx(
        row["T_winding_functional_Nm"], rel=1e-6)
    # not a vacuous identity: the torque it agrees on is a real reluctance
    # torque, not a pair of zeros
    assert abs(row["T_coenergy_Nm"]) > 1e-3


def test_with_the_magnets_ON_the_winding_functional_is_blind_to_the_magnet_term(
        section, banded, winding, units):
    """And in 2D as in 3D the blind spot is a NAMED quantity, not a discrepancy:
    ``W' = 1/2 int(Hc . B) + 1/2 sum_ph i_ph psi_ph`` exactly, so the winding
    functional keeps the whole reluctance term, half the PM alignment term and
    none of the magnet's own co-energy."""
    wt, _h_ew = winding
    m = band2d.Banded2D(section, banded, element_order=1, I_ph=I_PH,
                        winding=wt, linear_iron=True)
    cur = band2d.co_energy_curve(m, [-1, 0, 1], units=units)
    W = np.asarray(cur["W_load_J"])
    W_wind = np.asarray([0.5 * sum(I_PH[p] * ps[p] for p in ps)
                         for ps in cur["psi"]])
    # the magnets are ON, so the two functionals must now DIFFER, and by the
    # magnet term rather than by noise
    assert np.min(np.abs(W - W_wind) / np.abs(W)) > 1e-3
    (row,) = band2d.central_differences(cur, 1, I_ph=I_PH)
    assert abs(row["winding_over_coenergy"] - 1.0) > 1e-3


# --------------------------------------------------------------------------
# stationarity: why the 2D leg is P1
# --------------------------------------------------------------------------

def test_at_P1_the_curve_is_read_at_the_same_place_however_you_ask(section,
                                                                  banded,
                                                                  winding):
    """P1 has one B per element, so "the curve at the element mean |B|" and "the
    curve at every quadrature point" are the SAME reading — bit for bit, not
    approximately.

    This is the premise the whole 2D leg rests on.  ``T = dW'/dtheta`` is a
    virtual-work result only if the state being differenced is a stationary
    point of the discrete co-energy, and the stationarity condition reads the
    curve POINTWISE.  An element-wise Picard therefore stationarises the
    co-energy exactly when the two readings coincide — which is this test, and
    which is equally the 3D leg's situation (N0: one B per tet)."""
    wt, _h = winding
    a = band2d.Banded2D(section, banded, element_order=1, I_ph=I_PH,
                        winding=wt, nu_pointwise=False)
    b = band2d.Banded2D(section, banded, element_order=1, I_ph=I_PH,
                        winding=wt, nu_pointwise=True)
    sa = a.solve(1, tol=3e-3, max_iter=60)
    sb = b.solve(1, tol=3e-3, max_iter=60)
    # the premise itself: at P1 |B| really is one number per element, so the
    # element mean and every quadrature point are the same place on the curve
    Bq = sa.B_at_quad()
    Babs = np.hypot(Bq[0], Bq[1])
    assert np.max(np.ptp(Babs, axis=1)) <= 1e-12 * float(Babs.max())
    # so the two drivers walk the same Picard and land on the same energy; what
    # is left between them is the ORDER the same terms are summed in
    assert sa.picard["iterations"] == sb.picard["iterations"]
    # ... so the two drivers walk the same Picard.  Not bit for bit over 60
    # sweeps — the two summation orders differ in the last digit and a damped
    # fixed-point iteration carries that forward — but to a relative 1e-8 on
    # every residual in the history, which is nine orders below the 3e-3 the
    # loop stops at.
    assert np.allclose(sa.picard["history"], sb.picard["history"],
                       rtol=1e-8, atol=0)
    assert np.allclose(sa.A, sb.A, rtol=1e-8, atol=1e-8 * float(np.abs(sb.A).max()))
    Wa, Wb = a.co_energy(sa), b.co_energy(sb)
    assert abs(Wa - Wb) < 1e-11 * abs(Wb), (Wa, Wb)


def test_at_P2_it_is_NOT_the_same_reading_which_is_why_P1_is_the_2d_leg(
        section, banded, winding):
    """The other half of the statement, so the one above is not vacuous.

    At P2 the element mean and the quadrature points are different places on
    the curve, the element-wise Picard is no longer stationarising the co-energy
    it is about to be differentiated through, and the difference is not a
    rounding detail.  Measured on the real cross-section (passport
    ``stage_b.torque.matched_2d_window.why_the_2d_leg_is_P1``) the element-wise
    P2 co-energy difference gives 0.350, 0.458, 0.425, 0.404 N.m at dm = 2, 4,
    6, 8 — not smooth in the step, therefore not a derivative of anything —
    while the pointwise one gives 0.569, 0.567, 0.562 and lands beside P1."""
    wt, _h = winding
    a = band2d.Banded2D(section, banded, element_order=2, I_ph=I_PH,
                        winding=wt, nu_pointwise=False)
    b = band2d.Banded2D(section, banded, element_order=2, I_ph=I_PH,
                        winding=wt, nu_pointwise=True)
    Wa = a.co_energy(a.solve(1, tol=3e-3, max_iter=60))
    Wb = b.co_energy(b.solve(1, tol=3e-3, max_iter=60))
    assert abs(Wa - Wb) > 1e-6 * abs(Wb), (Wa, Wb)


# --------------------------------------------------------------------------
# the source is the 3D leg's own
# --------------------------------------------------------------------------

def test_the_2d_leg_is_driven_by_the_3d_legs_own_winding_field(section,
                                                               winding):
    """Not an equivalent current distribution — the same object.

    ``winding_T_2d`` is ``WindingT.field()`` at ``beta = 1``, which is what the
    3D field IS inside the stack.  If these ever drifted apart the two legs
    would be excited by two different machines and their ratio would carry that
    instead of an end effect."""
    wt, _h = winding
    rng = np.random.default_rng(0)
    r = rng.uniform(float(wt.r_grid[0]), float(wt.r_grid[-1]), 500)
    th = rng.uniform(0.0, float(wt.sector_rad), 500)
    xy = np.vstack([r * np.cos(th), r * np.sin(th)])
    z_in_stack = 0.5 * float(wt.stack_half_m)
    xyz = np.vstack([xy, np.full(500, z_in_stack)])
    assert wt.beta(np.array([z_in_stack]))[0] == 1.0
    got3 = wt.field()(xyz)
    got2 = band2d.winding_T_2d(wt, xy)
    assert np.allclose(got3[:2], got2, rtol=0, atol=0)
    assert np.max(np.abs(got2)) > 0.0
