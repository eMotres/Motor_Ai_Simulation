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
    """
    H_end = Hc_knee * 1.05
    bh = [
        (H_end, Br + MU0 * H_end - Br),        # J = 0 at the far end
        (Hc_knee, Br + MU0 * Hc_knee),         # J = Br at the knee
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
