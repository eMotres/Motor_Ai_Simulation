"""Demagnetisation rule, checked against a hand calculation.

The whole point of lifting MagnetDemag out of the transient: the load-line
construction can now be verified in milliseconds against an answer computed on
paper, instead of by staring at a colour map after a 90 s solve. Two of this
project's demag bugs — a magnet wiped to Br 0.016, and every healthy element
losing 5 % — would have been caught here immediately.
"""
from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
import pytest

from motor_ai_sim.simulation.demag import MU0, MagnetDemag


class _Mesh:
    """Smoothing needs element->node connectivity and node coordinates. A single
    strip of triangles is enough: the rule under test is per-element."""

    def __init__(self, n_tri: int):
        # n_tri triangles sharing a spine, area 0.5 each
        p = [[0.0, 0.0], [1.0, 0.0]]
        t = []
        for i in range(n_tri):
            p.append([float(i) * 0.5 + 0.5, 1.0])
            t.append([0, 1, len(p) - 1])
        self.p = np.asarray(p, float).T
        self.t = np.asarray(t, int).T


def _magnet(Br: float, Hc_knee: float, n_tri: int = 4, mu_rec: float = 1.0):
    """A square-ish demag curve: J flat at Br until the knee, then to zero.

    Shaped like the library's own entries (F45SH holds J to -649 kA/m then
    collapses within 3 kA/m), so the test exercises the same geometry of curve
    the solver meets in practice.

    ``mu_rec`` sets BOTH the declared recoil permeability and the slope of the
    curve's reversible branch (B = mu0*mu_rec*H + Br), so the material is
    self-consistent: the law the solver assembles and the curve the rule reads
    are the same magnet. At mu_rec = 1 the curve is bit-identical to the one
    every case below was written against.
    """
    H_end = Hc_knee * 1.05
    bh = [
        (H_end, MU0 * H_end),                        # J = 0 at the far end
        (Hc_knee, Br + MU0 * mu_rec * Hc_knee),      # reversible branch, at the knee
        (0.0, Br),
    ]
    mat = SimpleNamespace(Mx=Br / MU0, My=0.0, mu_r=mu_rec, bh_curve=bh)
    idx = np.arange(n_tri)
    return {1: idx}, {1: mat}, _Mesh(n_tri)


class TestLoadLine:
    def test_healthy_magnet_is_left_alone(self):
        """A well-kept magnet (B_par close to Br) must lose NOTHING.

        Booking a loss here is the 5 %-on-every-element bug: it came from taking
        J at the operating point instead of the recoil intercept.
        """
        cells, mats, mesh = _magnet(Br=1.2, Hc_knee=-900e3)
        br = np.ones(4)
        dm = MagnetDemag(cells, mats, mesh, br)
        # B along M near the remanence → the element sits high on the curve
        moved = dm.update(np.full(4, 1.15), np.zeros(4))
        assert not moved
        assert np.allclose(dm.br, 1.0)

    def test_self_demagnetising_magnet_settles_where_the_paper_says(self):
        """Fe16N2-like: low coercivity, and B_par only ~0.42 of mu0*M.

        Load line J = mu0*H/(alpha-1) with alpha = 0.42 crosses the curve at
        Br ~ 0.26 of full strength. That number was computed by hand while
        debugging and is the reason the rule was rewritten; pin it.
        """
        Br0 = 1.2
        cells, mats, mesh = _magnet(Br=Br0, Hc_knee=-120e3)
        br = np.ones(4)
        dm = MagnetDemag(cells, mats, mesh, br)
        dm.update(np.full(4, 0.42 * Br0), np.zeros(4))
        got = float(dm.br.mean())
        assert 0.15 < got < 0.40, f"settled at {got:.3f}, expected ~0.26"

    def test_loss_is_irreversible(self):
        """Once weakened, a magnet must not recover when the field relaxes —
        that is what makes it IRREVERSIBLE demagnetisation and not a reversible
        excursion along the recoil line."""
        cells, mats, mesh = _magnet(Br=1.2, Hc_knee=-120e3)
        br = np.ones(4)
        dm = MagnetDemag(cells, mats, mesh, br)
        dm.update(np.full(4, 0.42 * 1.2), np.zeros(4))
        hurt = dm.br.copy()
        dm.update(np.full(4, 1.19), np.zeros(4))      # field relaxes right back
        assert np.all(dm.br <= hurt + 1e-12), "the magnet healed itself"

    def test_br_stays_in_range(self):
        cells, mats, mesh = _magnet(Br=1.2, Hc_knee=-120e3)
        br = np.ones(4)
        dm = MagnetDemag(cells, mats, mesh, br)
        for bpar in (2.0, 1.0, 0.5, 0.0, -0.5, -2.0):
            dm.update(np.full(4, bpar), np.zeros(4))
            assert np.all(dm.br >= 0.0) and np.all(dm.br <= 1.0)

    def test_deeper_field_hurts_more(self):
        """Monotonic in the driving field — a magnet driven harder must not come
        out stronger. The non-monotonic version of this rule (np.interp clamping
        past the tabulated curve) made a magnet get STRONGER the deeper it was
        driven, which is how the map ended up uniform."""
        out = []
        for bpar in (0.9, 0.6, 0.3):
            cells, mats, mesh = _magnet(Br=1.2, Hc_knee=-120e3)
            dm = MagnetDemag(cells, mats, mesh, np.ones(4))
            dm.update(np.full(4, bpar * 1.2), np.zeros(4))
            out.append(float(dm.br.mean()))
        assert out[0] >= out[1] >= out[2] - 1e-12, f"non-monotonic: {out}"


class TestFieldExtraction:
    """H is INVERTED out of B, so it must be inverted out of the law that was
    solved: B = mu0*mu_rec*H + Br_vec*br.

    Reading it back as if the magnet were air (H = B/mu0 - M*br) is the mu_rec=1
    special case; on a real NdFeB (mu_rec 1.05) it over-reads |H| by exactly
    mu_rec and pushes every element that much closer to its knee than the solved
    field puts it.
    """

    @staticmethod
    def _hand_H(B_par, Br0, br, mu_rec):
        return (B_par - Br0 * br) / (MU0 * mu_rec)

    @pytest.mark.parametrize("mu_rec", [1.0, 1.05, 1.083, 1.2])
    def test_h_matches_the_hand_inversion_of_the_solver_law(self, mu_rec):
        """One element, one number, computed on paper.

        B_par = 0.30 T along M on a Br0 = 1.20 T pristine magnet:
            mu_rec 1.00 -> H = (0.30 - 1.20)/mu0        = -716.20 kA/m
            mu_rec 1.05 -> H = (0.30 - 1.20)/(mu0*1.05) = -682.09 kA/m
        The knee is far away (-2 MA/m) so nothing de-rates and H_first is the
        pristine extraction, untouched by the load-line construction.
        """
        Br0 = 1.2
        cells, mats, mesh = _magnet(Br=Br0, Hc_knee=-2.0e6, mu_rec=mu_rec)
        dm = MagnetDemag(cells, mats, mesh, np.ones(4))
        dm.update(np.full(4, 0.30), np.zeros(4))
        want = self._hand_H(0.30, Br0, 1.0, mu_rec)
        assert np.allclose(dm.H_first, want, rtol=1e-12, atol=0.0)
        # the smoothing is a spatial filter on a uniform field: a no-op
        assert float(dm.H_worst.min()) == pytest.approx(want, rel=1e-12)
        # ...and the old air-magnet reading is exactly mu_rec times deeper
        assert self._hand_H(0.30, Br0, 1.0, 1.0) == pytest.approx(want * mu_rec,
                                                                  rel=1e-12)

    def test_the_derated_remanence_is_what_scales_not_the_field(self):
        """br multiplies Br_vec INSIDE the inversion, so a half-strength magnet
        in the same B sees a different H — the term that scales is the source,
        not the whole expression."""
        Br0, mu_rec = 1.2, 1.05
        cells, mats, mesh = _magnet(Br=Br0, Hc_knee=-2.0e6, mu_rec=mu_rec)
        dm = MagnetDemag(cells, mats, mesh, np.full(4, 0.5))
        dm.update(np.full(4, 0.30), np.zeros(4))
        assert np.allclose(dm.H_first,
                           self._hand_H(0.30, Br0, 0.5, mu_rec), rtol=1e-12)

    def test_a_recoil_permeability_moves_the_knee_margin_the_safe_way(self):
        """Same field, same curve, mu_rec 1 vs 1.05: the honest inversion must
        report a SHALLOWER H and therefore a smaller knee proximity.

        Direction matters as much as size — the old reading was pessimistic, so
        anything that came out of it was a margin the machine actually had."""
        Br0 = 1.2
        got = {}
        for mu_rec in (1.0, 1.05):
            cells, mats, mesh = _magnet(Br=Br0, Hc_knee=-700e3, mu_rec=mu_rec)
            dm = MagnetDemag(cells, mats, mesh, np.ones(4))
            dm.update(np.full(4, 0.30), np.zeros(4))
            rep = dm.report()
            got[mu_rec] = (float(dm.H_first.min()), rep[0]["knee_proximity"])
        assert got[1.05][0] > got[1.0][0]                     # shallower H
        assert got[1.05][0] == pytest.approx(got[1.0][0] / 1.05, rel=1e-9)
        assert got[1.05][1] < got[1.0][1]                     # more margin

    def test_the_load_line_carries_the_recoil_polarisation(self):
        """J at the operating point is Br0*br + mu0*(mu_rec-1)*H, not Br0*br.

        A magnet driven to a load line that lands ON the knee must settle at the
        knee's own intrinsic polarisation; getting J wrong by the recoil term
        moves the crossing and therefore the permanent loss. Checked as an
        identity the construction has to satisfy: at mu_rec = 1 the whole rule
        reduces to the pre-existing one, element for element.
        """
        Br0 = 1.2
        cells, mats, mesh = _magnet(Br=Br0, Hc_knee=-300e3, mu_rec=1.0)
        dm = MagnetDemag(cells, mats, mesh, np.ones(4))
        dm.update(np.full(4, 0.42 * Br0), np.zeros(4))
        # the pre-change formula, hand-rolled: alpha = B/(mu0*M*br), k = mu0/(a-1)
        H = 0.42 * Br0 / MU0 - Br0 / MU0
        alpha = (H + Br0 / MU0) / (Br0 / MU0)
        k_old = MU0 / min(alpha - 1.0, -1e-9)
        k_new = (Br0 * 1.0 + MU0 * (1.0 - 1.0) * H) / min(H, -1e-9)
        assert k_new == pytest.approx(k_old, rel=1e-12)
        # ...and the crossing that follows, on paper: k = Br0/H = -2.1667e-6,
        # which misses the flat top (it would want H = -554 kA/m) and lands on
        # the collapsing segment [-315, -300] kA/m, slope 8e-5 T/(A/m):
        #   8e-5*(H + 315e3) = k*H  ->  H_op = -306.69 kA/m,  J_op = 0.6645 T
        # and with mu_rec_c = 1 the recoil intercept IS J_op, so br = 0.5537.
        assert float(dm.br.mean()) == pytest.approx(0.5537, rel=2e-4)


class TestDiagnostics:
    def test_h_first_records_the_pristine_field_only(self):
        """H_first must stay at the FIRST evaluation — it is the reference the
        final map is judged against, so a later overwrite would erase the
        evidence."""
        cells, mats, mesh = _magnet(Br=1.2, Hc_knee=-120e3)
        dm = MagnetDemag(cells, mats, mesh, np.ones(4))
        dm.update(np.full(4, 0.5), np.zeros(4))
        first = dm.H_first.copy()
        dm.update(np.full(4, 0.1), np.zeros(4))
        assert np.allclose(dm.H_first, first, equal_nan=True)

    def test_h_worst_tracks_the_minimum(self):
        cells, mats, mesh = _magnet(Br=1.2, Hc_knee=-900e3)
        dm = MagnetDemag(cells, mats, mesh, np.ones(4))
        dm.update(np.full(4, 1.1), np.zeros(4))
        mild = dm.H_worst.copy()
        dm.update(np.full(4, 0.2), np.zeros(4))
        assert np.all(dm.H_worst <= mild + 1e-9)

    def test_a_non_magnet_domain_is_ignored(self):
        mesh = _Mesh(4)
        iron = SimpleNamespace(Mx=0.0, My=0.0, mu_r=5000.0, bh_curve=[(0, 0), (1e3, 1.5)])
        dm = MagnetDemag({1: np.arange(4)}, {1: iron}, mesh, np.ones(4))
        assert not dm.active
        assert not dm.update(np.zeros(4), np.zeros(4))
