"""Iron-loss model — the properties both element orders must share.

This is now ONE implementation used by P1 and P2, so a test here covers both.
That is the point: the previous duplicate copies let a flag gate one and not the
other, and P2 reported zero core loss for a while with nothing to catch it.
"""
from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
import pytest

from motor_ai_sim.simulation.losses import (
    DEFAULT_STACKING_FACTOR, TWO_PI_SQ, central_difference, iron_loss_series,
)

# Hand-picked coefficients so every expected number is computable on paper.
KH, KC, KE = 100.0, 0.5, 0.0


def _bertotti(_mat):
    return KH, KC, KE


def _sine_history(n: int, amp: float, n_elem: int = 3):
    """B(t) = amp*sin, identical in every element — one cycle over n frames."""
    t = np.arange(n) / n * 2 * np.pi
    X = np.outer(amp * np.sin(t), np.ones(n_elem))
    Y = np.zeros_like(X)
    return X, Y


def _steel(sf=None):
    m = SimpleNamespace()
    if sf is not None:
        m.stacking_factor = sf
    return m


class TestIronLoss:
    def test_zero_field_is_zero_loss(self):
        n = 32
        X = np.zeros((n, 3)); Y = np.zeros((n, 3))
        cl, pc = iron_loss_series(X, Y, np.arange(3), np.ones(3), _steel(0.95),
                                  0.01, 100.0, n, central_difference(1e-4), _bertotti)
        assert np.allclose(cl, 0.0) and pc == pytest.approx(0.0)

    def test_hysteresis_scales_with_B_squared(self):
        """k_h*f*B^2 — doubling the flux must quadruple the per-cycle term."""
        n, f = 64, 100.0
        out = []
        for amp in (0.5, 1.0):
            X, Y = _sine_history(n, amp)
            _, pc = iron_loss_series(X, Y, np.arange(3), np.ones(3), _steel(1.0),
                                     0.01, f, n, central_difference(1e-4), _bertotti)
            out.append(pc)
        assert out[1] / out[0] == pytest.approx(4.0, rel=1e-6)

    def test_classical_eddy_scales_with_frequency_squared(self):
        """k_c*<(dB/dt)^2> — same waveform at twice the speed is 4x the loss.

        Encoded by halving dt, which is what doubling rpm does to the history.
        """
        n = 128
        X, Y = _sine_history(n, 1.0)
        mean = []
        for dt in (1e-4, 0.5e-4):
            cl, _ = iron_loss_series(X, Y, np.arange(3), np.ones(3), _steel(1.0),
                                     0.01, 100.0, n, central_difference(dt), _bertotti)
            mean.append(float(np.mean(cl)))
        assert mean[1] / mean[0] == pytest.approx(4.0, rel=1e-6)

    def test_classical_eddy_matches_the_analytic_value(self):
        """For B = A*sin(wt): <(dB/dt)^2> = (A*w)^2/2, so

            P_cl = k_c/(2*pi^2) * (A*w)^2/2 * V

        Checks the constant, not just the scaling — a wrong 2*pi^2 would pass
        every ratio test above and still be wrong by a factor of twenty.
        """
        n, amp, dt = 256, 1.3, 1e-5
        X, Y = _sine_history(n, amp, n_elem=1)
        vol = 0.02 * 0.01 * 1.0                       # area * stack * sf
        w = 2 * np.pi / (n * dt)
        cl, _ = iron_loss_series(X, Y, np.array([0]), np.array([0.02]), _steel(1.0),
                                 0.01, 100.0, n, central_difference(dt), _bertotti)
        want = KC / TWO_PI_SQ * (amp * w) ** 2 / 2.0 * vol
        assert float(np.mean(cl)) == pytest.approx(want, rel=2e-3)

    def test_stacking_factor_scales_the_volume(self):
        """Only the steel dissipates; the insulation is dead volume."""
        n = 64
        X, Y = _sine_history(n, 1.0)
        loss = {}
        for sf in (1.0, 0.5):
            cl, pc = iron_loss_series(X, Y, np.arange(3), np.ones(3), _steel(sf),
                                      0.01, 100.0, n, central_difference(1e-4), _bertotti)
            loss[sf] = (float(np.mean(cl)), pc)
        assert loss[0.5][0] == pytest.approx(loss[1.0][0] * 0.5, rel=1e-9)
        assert loss[0.5][1] == pytest.approx(loss[1.0][1] * 0.5, rel=1e-9)

    def test_missing_stacking_factor_uses_one_documented_default(self):
        """The same constant used to be 0.95 in two places and 0.97 in a third.
        Whatever the value, there must be exactly one of it."""
        n = 64
        X, Y = _sine_history(n, 1.0)
        _, bare = iron_loss_series(X, Y, np.arange(3), np.ones(3), _steel(None),
                                   0.01, 100.0, n, central_difference(1e-4), _bertotti)
        _, told = iron_loss_series(X, Y, np.arange(3), np.ones(3),
                                   _steel(DEFAULT_STACKING_FACTOR),
                                   0.01, 100.0, n, central_difference(1e-4), _bertotti)
        assert bare == pytest.approx(told, rel=1e-12)

    def test_empty_inputs_are_survivable(self):
        """A design with no rotor iron must not crash the loss pass."""
        n = 16
        for args in (([], [], np.array([], int)),
                     (np.zeros((n, 0)), np.zeros((n, 0)), np.array([], int))):
            cl, pc = iron_loss_series(args[0], args[1], args[2], np.ones(0), _steel(1.0),
                                      0.01, 100.0, n, central_difference(1e-4), _bertotti)
            assert cl.shape == (n,) and pc == 0.0

    def test_no_material_is_zero_not_a_crash(self):
        n = 16
        X, Y = _sine_history(n, 1.0)
        cl, pc = iron_loss_series(X, Y, np.arange(3), np.ones(3), None,
                                  0.01, 100.0, n, central_difference(1e-4), _bertotti)
        assert np.allclose(cl, 0.0) and pc == 0.0


class TestProximityLoss:
    """AC copper — same model for both element orders now."""

    @staticmethod
    def _radial_history(n, amp, n_elem=4):
        """Purely RADIAL field: only the d_r term may contribute."""
        t = np.arange(n) / n * 2 * np.pi
        # elements on the +x axis, so r_hat = +x and B = (amp*sin, 0) is radial
        cen = np.vstack([np.linspace(1.0, 2.0, n_elem), np.zeros(n_elem)])
        X = np.outer(amp * np.sin(t), np.ones(n_elem))
        Y = np.zeros_like(X)
        return X, Y, cen

    def test_radial_field_uses_the_radial_dimension_only(self):
        """The direction split is the whole point: a field with no tangential
        component must not be charged the tangential conductor dimension. The
        single-d slab form gets this wrong and over-counts a tall thin bar."""
        n = 64
        X, Y, cen = self._radial_history(n, 1.0)
        from motor_ai_sim.simulation.losses import proximity_loss_series
        _, with_t = proximity_loss_series(
            X, Y, np.arange(4), cen, np.ones(4), 5.8e7, 2e-3, 9e-3,
            0.01, n, central_difference(1e-4))
        _, without_t = proximity_loss_series(
            X, Y, np.arange(4), cen, np.ones(4), 5.8e7, 2e-3, 0.0,
            0.01, n, central_difference(1e-4))
        assert with_t == pytest.approx(without_t, rel=1e-9)

    def test_loss_scales_with_the_dimension_squared(self):
        n = 64
        X, Y, cen = self._radial_history(n, 1.0)
        from motor_ai_sim.simulation.losses import proximity_loss_series
        out = []
        for d in (1e-3, 2e-3):
            _, avg = proximity_loss_series(
                X, Y, np.arange(4), cen, np.ones(4), 5.8e7, d, 0.0,
                0.01, n, central_difference(1e-4))
            out.append(avg)
        assert out[1] / out[0] == pytest.approx(4.0, rel=1e-9)

    def test_wire_split_cuts_the_width_term(self):
        """N transposed strips across the width -> that loss term falls as N^2."""
        from motor_ai_sim.simulation.losses import copper_ac_dims
        geo1 = {"wire_width": 2.0, "wire_height": 0.5, "wire_split": 1}
        geo4 = dict(geo1, wire_split=4)
        _, d1, _ = copper_ac_dims(geo1, 120.0, 50.0, 1.724e-8, 0.00393, 4e-7 * math.pi)
        _, d4, _ = copper_ac_dims(geo4, 120.0, 50.0, 1.724e-8, 0.00393, 4e-7 * math.pi)
        assert d4 == pytest.approx(d1 / 4.0, rel=1e-9)

    def test_skin_depth_caps_the_dimension(self):
        """At high frequency the field cannot reach the middle of the bar, so a
        wider conductor buys no extra loss — without the cap the model would
        keep charging for copper the field never sees."""
        from motor_ai_sim.simulation.losses import copper_ac_dims
        geo = {"wire_width": 50.0, "wire_height": 50.0, "wire_split": 1}
        _, d_r, d_t = copper_ac_dims(geo, 120.0, 20000.0, 1.724e-8, 0.00393,
                                     4e-7 * math.pi)
        assert d_r < 50e-3 and d_t < 50e-3, "the skin cap never engaged"
        assert d_r == pytest.approx(d_t, rel=1e-12), "both are capped by the same delta"

    def test_zero_conductivity_is_zero_loss(self):
        n = 16
        X, Y, cen = self._radial_history(n, 1.0)
        from motor_ai_sim.simulation.losses import proximity_loss_series
        ser, avg = proximity_loss_series(
            X, Y, np.arange(4), cen, np.ones(4), 0.0, 2e-3, 9e-3,
            0.01, n, central_difference(1e-4))
        assert avg == 0.0 and len(ser) == n


class TestRotorEddyInputs:
    """Tag assembly for the coupled rotor-eddy solve — was written out twice."""

    def test_tags_and_magnet_list(self):
        from motor_ai_sim.simulation.losses import rotor_eddy_tags
        cells = {5: np.array([0, 1]), 6: np.array([2]),
                 100: np.array([3, 4]), 101: np.array([5])}
        tags, mags = rotor_eddy_tags(cells, 6, dom_mag_base=100)
        assert list(tags) == [5, 5, 6, 100, 100, 101]
        assert mags == [100, 101], "each magnet must stay its own region"

    def test_untagged_elements_default_to_zero(self):
        """A gap in the cell map must not silently inherit a neighbour's tag —
        that would put air into the magnet loss."""
        from motor_ai_sim.simulation.losses import rotor_eddy_tags
        tags, mags = rotor_eddy_tags({100: np.array([2])}, 4, dom_mag_base=100)
        assert list(tags) == [0, 0, 100, 0] and mags == [100]

    def test_mu_lookup_routes_each_region(self):
        from motor_ai_sim.simulation.losses import rotor_mu_lookup
        mu = rotor_mu_lookup(mu_back_iron=850.0, dom_mag_base=100, dom_rotor=5)
        assert mu(100) == pytest.approx(1.05)     # magnet recoil
        assert mu(5) == pytest.approx(850.0)      # converged back iron
        assert mu(6) == pytest.approx(1.0)        # shaft / air, non-magnetic
        assert mu(0) == pytest.approx(1.0)

    def test_back_iron_uses_the_converged_value_not_a_default(self):
        """Feeding the eddy solve a linear 1000 would put it on a different
        machine than the transient that produced the field."""
        from motor_ai_sim.simulation.losses import rotor_mu_lookup
        assert rotor_mu_lookup(1234.0, 100, 5)(5) == pytest.approx(1234.0)


class TestLossDensityMap:
    """The spatial map: which component is MEASURED and which is a normalised
    model, and whether each still integrates to the watts the sidebar reports.

    The magnet term used to be the slab |dB/dt|² shape scaled to P_mag_avg —
    smooth by construction, so the map could never show the corner/edge
    crowding an Ansys Total-Loss plot shows.  When the coupled σ·∂A/∂t solve
    ran, the per-element σE² it produced IS the density and must be taken
    unrenormalised; these tests pin both halves of that rule.
    """

    @staticmethod
    def _kw(n_st, n_el, **over):
        """A map call with every component switched off, so each test turns on
        exactly the one it is about."""
        z = np.zeros((0, 0))
        kw = dict(
            n_stator_elems=n_st, n_elems=n_el,
            hist_sx=[], hist_sy=[], hist_rx=[], hist_ry=[],
            hist_mx=[], hist_my=[], hist_cx=[], hist_cy=[],
            iron_s_idx=np.array([], int), iron_r_idx=np.array([], int),
            mag_idx=np.array([], int), coil_idx=np.array([], int),
            areas_s=np.ones(n_st), areas_r=np.ones(n_el - n_st),
            coil_centroids=z, steel_s=None, steel_r=None, bertotti=_bertotti,
            f_elec_hz=100.0, stack_length_m=0.05, sector_scale=4.0,
            P_fe_avg=0.0, P_mag_avg=0.0, P_cu_dc=0.0, P_cu_ac_avg=0.0,
            sigma_cu=5.8e7, d_cu_r=1e-3, d_cu_t=1e-3,
            ddt=lambda X, qp=None: central_difference(1e-4)(X),
        )
        kw.update(over)
        return kw

    def test_solved_magnet_density_is_copied_not_renormalised(self):
        """σE² IS the density.  A map that rescaled it to the reported watts
        would flatten exactly the corner peak the solve was run to find."""
        from motor_ai_sim.simulation.losses import loss_density_map
        n_st, n_el = 2, 5
        mag_local = np.array([0, 1])            # rotor-half elements 0,1
        mag_glob = n_st + mag_local
        solved = np.zeros(n_el)
        solved[mag_glob] = [10.0, 1000.0]       # a 100:1 corner peak
        dens, label = loss_density_map(**self._kw(
            n_st, n_el, mag_idx=mag_local,
            # a DIFFERENT reported number — the map must ignore it
            P_mag_avg=999.0,
            solved_dens=solved, solved_groups=("mag",),
            solved_elems={"mag": mag_glob}))
        assert dens[mag_glob].tolist() == [10.0, 1000.0]
        assert "solved" in label and "unrenormalised" in label

    def test_solved_map_integrates_to_its_own_watts(self):
        """∫ρ dV over the machine = Σ dens·area·L·n_sectors."""
        from motor_ai_sim.simulation.losses import loss_density_map
        n_st, n_el = 2, 5
        mag_local = np.array([0, 1])
        mag_glob = n_st + mag_local
        solved = np.zeros(n_el)
        solved[mag_glob] = [10.0, 1000.0]
        lines = []
        dens, _ = loss_density_map(**self._kw(
            n_st, n_el, mag_idx=mag_local, P_mag_avg=1.0,
            solved_dens=solved, solved_groups=("mag",),
            solved_elems={"mag": mag_glob}, log_line=lines.append))
        # areas_r are 1.0, stack 0.05 m, sector_scale 4
        assert float(np.sum(dens[mag_glob])) * 0.05 * 4.0 == pytest.approx(202.0)
        assert any("magnet" in ln for ln in lines), "the cross-check must be logged"

    def test_modelled_magnet_shape_is_normalised_to_the_reported_watts(self):
        """With no coupled solve the slab shape stays — and stays normalised,
        so the picture still integrates to the sidebar number."""
        from motor_ai_sim.simulation.losses import loss_density_map
        n_st, n_el = 2, 4
        mag_local = np.array([0, 1])
        X, Y = _sine_history(16, 1.0, n_elem=2)
        X[:, 1] *= 3.0                       # element 1 sees 3x the dB/dt
        dens, label = loss_density_map(**self._kw(
            n_st, n_el, mag_idx=mag_local, hist_mx=X, hist_my=Y,
            P_mag_avg=2.0))
        integ = float(np.sum(dens[n_st + mag_local])) * 0.05 * 4.0
        assert integ == pytest.approx(2.0, rel=1e-9)
        assert "slab" in label and "normalised" in label
        # the SHAPE survives: 3x dB/dt is 9x the density
        assert dens[n_st + 1] / dens[n_st + 0] == pytest.approx(9.0, rel=1e-9)

    def test_end_winding_copper_is_uniform_and_declared(self):
        """The end turns are real watts outside the modelled plane.  They are
        spread uniformly and SAID so — never folded into the solved shape."""
        from motor_ai_sim.simulation.losses import loss_density_map
        n_st, n_el = 2, 3
        coil = np.array([0, 1])
        solved = np.zeros(n_el)
        solved[coil] = [100.0, 300.0]
        dens, label = loss_density_map(**self._kw(
            n_st, n_el, coil_idx=coil, solved_dens=solved,
            solved_groups=("cu",), solved_elems={"cu": coil},
            P_cu_end_winding_W=0.4))
        # V_cu = 2 elems * 1 m2 * 0.05 m * 4 = 0.4 m3  ->  +1.0 W/m3 each
        assert dens[coil].tolist() == pytest.approx([101.0, 301.0])
        assert "end-winding" in label

    def test_no_solved_groups_leaves_the_modelled_path_untouched(self):
        """A run with the coupled solve OFF must behave exactly as before."""
        from motor_ai_sim.simulation.losses import loss_density_map
        n_st, n_el = 2, 4
        coil = np.array([0, 1])
        dens, label = loss_density_map(**self._kw(
            n_st, n_el, coil_idx=coil, P_cu_dc=1.0))
        # uniform DC over V_cu = 0.4 m3
        assert dens[coil].tolist() == pytest.approx([2.5, 2.5])
        assert "uniform DC" in label and "solved" not in label
