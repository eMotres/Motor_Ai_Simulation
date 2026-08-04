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

    def test_kf_one_is_plain_bertotti(self):
        """k_f = 1 (no insulation): the closed form with no lamination algebra.

        P_hyst = k_h*f*B^2*V, and the classical term is the analytic
        k_c/(2*pi^2)*(A*w)^2/2*V.  Anchors the whole k_f family below to a
        number computable on paper.
        """
        n, amp, dt, f = 256, 1.3, 1e-5, 100.0
        X, Y = _sine_history(n, amp, n_elem=1)
        vol = 0.02 * 0.01                              # area * stack, k_f = 1
        w = 2 * np.pi / (n * dt)
        cl, pc = iron_loss_series(X, Y, np.array([0]), np.array([0.02]),
                                  _steel(1.0), 0.01, f, n,
                                  central_difference(dt), _bertotti)
        assert pc == pytest.approx(KH * f * amp ** 2 * vol, rel=1e-9)
        assert float(np.mean(cl)) == pytest.approx(
            KC / TWO_PI_SQ * (amp * w) ** 2 / 2.0 * vol, rel=2e-3)

    def test_kf_algebra_is_the_closed_form_ratio(self):
        """The steel carries B/k_f over a volume k_f*V, so against k_f = 1:

            B^2 terms (hysteresis, classical eddy)  ->  1/k_f
            excess term (B^1.5)                     ->  1/sqrt(k_f)

        This is the bug that made the core loss read low: the old code billed
        the reduced steel VOLUME but evaluated Bertotti at the homogenised
        B — i.e. k_f applied once, downwards, instead of k_f up-and-down.  It
        scaled the loss BY k_f (0.92 -> -8 %) where the physics says 1/k_f
        (+8.7 %): a factor 1/k_f^2 = 1.182 wrong.
        """
        n, amp, dt, f = 128, 1.0, 1e-4, 100.0
        X, Y = _sine_history(n, amp)

        def _run(kf, ke):
            def _b(_m):
                return KH, KC, ke
            cl, pc = iron_loss_series(X, Y, np.arange(3), np.ones(3), _steel(kf),
                                      0.01, f, n, central_difference(dt), _b)
            return float(np.mean(cl)), pc

        for kf in (0.925, 0.92, 0.5):
            # B^2 terms: hysteresis (ke = 0) and the classical eddy series.
            eddy1, hyst1 = _run(1.0, 0.0)
            eddyk, hystk = _run(kf, 0.0)
            assert hystk == pytest.approx(hyst1 / kf, rel=1e-12)
            assert eddyk == pytest.approx(eddy1 / kf, rel=1e-12)
            # excess term alone (kh = 0 via a coefficient set with only ke):
            _, exc1 = _run(1.0, 7.0)
            _, exck = _run(kf, 7.0)
            exc1 -= hyst1
            exck -= hystk
            assert exck == pytest.approx(exc1 / math.sqrt(kf), rel=1e-12)

    def test_kf_terms_breakdown_sums_to_the_reported_loss(self):
        """The reported per-term split IS the number, not a parallel estimate."""
        n = 64
        X, Y = _sine_history(n, 1.1)

        def _b(_m):
            return KH, KC, 3.0
        terms: dict = {}
        cl, pc = iron_loss_series(X, Y, np.arange(3), np.ones(3), _steel(0.92),
                                  0.01, 100.0, n, central_difference(1e-4), _b,
                                  terms=terms)
        assert terms["k_f"] == 0.92
        assert terms["hysteresis_W"] + terms["excess_W"] == pytest.approx(pc, rel=1e-12)
        assert terms["eddy_W"] == pytest.approx(float(np.mean(cl)), rel=1e-12)

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
        dens, label, unmod = loss_density_map(**self._kw(
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
        dens, _, unmod = loss_density_map(**self._kw(
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
        dens, label, unmod = loss_density_map(**self._kw(
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
        dens, label, unmod = loss_density_map(**self._kw(
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
        dens, label, unmod = loss_density_map(**self._kw(
            n_st, n_el, coil_idx=coil, P_cu_dc=1.0))
        # uniform DC over V_cu = 0.4 m3
        assert dens[coil].tolist() == pytest.approx([2.5, 2.5])
        assert "uniform DC" in label and "solved" not in label


class TestAirCarriesNoLoss:
    """Air is not a material with a small loss — it has NO loss model at all.

    σ=0 (no eddy current), no hysteresis loop, and windage is not part of a 2-D
    magnetic solve.  So every element that is not iron / magnet / copper / shaft
    must be EXACTLY zero in the map, and must be flagged so the view can leave
    it blank: on the log colour scale the view uses, zero is a colour (band 0),
    and an air gap painted band 0 is indistinguishable from a measured small
    loss.  That is what put a coloured ring in the gap of the 150 mm 24s28p
    machine.
    """

    # the same "everything off, switch on one component" builder
    _kw = staticmethod(TestLossDensityMap._kw)

    def test_every_element_outside_a_modelled_material_is_exactly_zero(self):
        """The full map with all four components on: everything else is 0.0."""
        from motor_ai_sim.simulation.losses import loss_density_map
        n_st, n_el = 6, 12
        iron_s = np.array([0, 1])            # stator half: iron 0-1, coil 2-3
        coil = np.array([2, 3])              #              air    4-5
        iron_r = np.array([0, 1])            # rotor half : iron, then magnets
        mag = np.array([2, 3])               #              air    4-5
        Xs, Ys = _sine_history(16, 1.0, n_elem=2)
        Xm, Ym = _sine_history(16, 0.5, n_elem=2)
        solved = np.zeros(n_el)
        solved[coil] = [100.0, 300.0]
        dens, label, unmod = loss_density_map(**self._kw(
            n_st, n_el,
            iron_s_idx=iron_s, iron_r_idx=iron_r, hist_sx=Xs, hist_sy=Ys,
            hist_rx=Xs, hist_ry=Ys, steel_s=_steel(1.0), steel_r=_steel(1.0),
            P_fe_avg=3.0,
            mag_idx=mag, hist_mx=Xm, hist_my=Ym, P_mag_avg=1.0,
            coil_idx=coil, solved_dens=solved, solved_groups=("cu",),
            solved_elems={"cu": coil}))
        modelled = np.concatenate([iron_s, coil, n_st + iron_r, n_st + mag])
        air = np.setdiff1d(np.arange(n_el), modelled)
        assert air.size == 4                          # the test means to have air
        # EXACTLY zero — not "small", not "below the legend floor".
        assert dens[air].tolist() == [0.0] * air.size
        assert np.all(dens[modelled] > 0.0)

    def test_air_is_always_declared_unmodelled(self):
        """So the renderer can draw it blank instead of band 0 of the scale."""
        from motor_ai_sim.simulation.losses import loss_density_map
        dens, label, unmod = loss_density_map(**self._kw(2, 4))
        assert "air" in unmod
        assert "no loss model" in label and "BLANK" in label

    def test_an_unsolved_shaft_is_unmodelled_not_zero_loss(self):
        """Without the coupled solve there is no shaft term to draw at all."""
        from motor_ai_sim.simulation.losses import loss_density_map
        n_st, n_el = 2, 4
        _, _, unmod = loss_density_map(**self._kw(n_st, n_el, coil_idx=np.array([0]),
                                                  P_cu_dc=1.0))
        assert "shaft" in unmod
        # ...and a SOLVED one is not flagged
        solved = np.zeros(n_el); solved[3] = 7.0
        _, _, unmod2 = loss_density_map(**self._kw(
            n_st, n_el, solved_dens=solved, solved_groups=("shaft",),
            solved_elems={"shaft": np.array([3])}))
        assert "shaft" not in unmod2

    def test_a_misindexed_solved_set_cannot_leak_into_air(self):
        """The failure mode this guards: solved σE² ids that miss their material
        (a half-mesh offset applied once too often) used to be copied straight
        in, painting loss into air.  Zeroed, and said out loud in the log."""
        from motor_ai_sim.simulation.losses import loss_density_map
        n_st, n_el = 4, 8
        mag_local = np.array([0, 1])             # true magnets = global 4, 5
        solved = np.zeros(n_el)
        solved[[2, 3]] = [10.0, 1000.0]          # ...but the ids say 2, 3 (air)
        lines: list = []
        dens, _, unmod = loss_density_map(**self._kw(
            n_st, n_el, mag_idx=mag_local, P_mag_avg=1.0,
            solved_dens=solved, solved_groups=("mag",),
            solved_elems={"mag": np.array([2, 3])}, log_line=lines.append))
        assert dens.tolist() == [0.0] * n_el
        assert any("OUTSIDE every modelled material" in ln for ln in lines)
        # and the magnets, having received nothing, are declared unmodelled
        assert "magnets" in unmod


class TestServedLossMapDeclaresItsBlanks:
    """The field-view payload's ``loss_density_unmodelled``, which is the only
    thing telling the renderer apart a modelled zero from an absent model.

    Both loss paths funnel through this: the transient's own normalised map and
    the single-frame analytic estimate, which has never had a shaft term or an
    air term.  Air is in the list unconditionally — there is no air-loss model
    to run — and any loss found in an air element is a bug, so it is zeroed
    here rather than served and painted.
    """

    @staticmethod
    def _tags():
        from motor_ai_sim.simulation.sb_domains import (
            DOM_AIRGAP, DOM_COIL_BASE, DOM_MAG_BASE, DOM_ROTOR, DOM_SHAFT,
            DOM_STATOR)
        return np.array([DOM_STATOR, DOM_ROTOR, DOM_MAG_BASE, DOM_COIL_BASE,
                         DOM_SHAFT, DOM_AIRGAP, DOM_AIRGAP], int)

    def test_air_is_declared_even_when_every_material_is_modelled(self):
        from motor_ai_sim.routes.simulation import _unmodelled_loss_classes
        t = self._tags()
        d = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 0.0, 0.0])
        out = _unmodelled_loss_classes(t, d)
        assert out == ["air"]

    def test_a_class_with_no_value_anywhere_is_declared_blank(self):
        from motor_ai_sim.routes.simulation import _unmodelled_loss_classes
        t = self._tags()
        d = np.array([1.0, 2.0, 0.0, 4.0, 0.0, 0.0, 0.0])   # no magnet, no shaft
        out = _unmodelled_loss_classes(t, d)
        assert set(out) == {"magnets", "shaft", "air"}
        assert "iron" not in out and "copper" not in out

    def test_loss_in_air_is_zeroed_before_it_can_be_painted(self):
        """An air element with a value is an index bug — never a small loss."""
        from motor_ai_sim.routes.simulation import _unmodelled_loss_classes
        t = self._tags()
        d = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 7.7e3, 9.9e3])
        _unmodelled_loss_classes(t, d)
        assert d[-2:].tolist() == [0.0, 0.0]
        assert d[:5].tolist() == [1.0, 2.0, 3.0, 4.0, 5.0]

    def test_the_solvers_own_declaration_is_kept(self):
        from motor_ai_sim.routes.simulation import _unmodelled_loss_classes
        t = self._tags()
        d = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 0.0, 0.0])
        out = _unmodelled_loss_classes(t, d, ["magnets", "air"])
        assert set(out) == {"magnets", "air"} and out.count("air") == 1
