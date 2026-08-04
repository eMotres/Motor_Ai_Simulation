"""The measured P(B, f) surface: anchors, envelope, blend, and the sum over harmonics.

The three-coefficient Bertotti fit is a compression of a measured surface, and
it loses exactly the part this tool's machines work in: above ~1.5 T a fixed B²
cannot follow the saturation upturn, so the fit read 10-15 % low where 43 % of
the 150 mm's stator loss sits. This file guards the replacement.

Three tiers, in this order:

* **Anchors** — at every measured (B, f) point of all three audited records the
  model must return the manufacturer's own number. That is near-exact BY
  CONSTRUCTION (PCHIP interpolates its data), which is the point: the pin says
  the construction is still the one that has that property, so a future
  "smoothing" that starts averaging the data away fails here first.
* **The envelope** — the tables are a staircase, not a rectangle. Outside them
  the answer is a BLEND into the Bertotti extrapolation, and the thing that has
  to hold is that the value and its first derivative cross the boundary without
  a step. A kink there is a fake gradient, and the optimizer would find it.
* **The sum over harmonics** — a measured point is a SINUSOID. Turning a
  machine's real locus into a sum of them is an assumption (loss superposition)
  and these tests state what it does and does not preserve: a pure sinusoid
  must give the surface value exactly, and the classical part must stay
  identical to the ⟨(dB/dt)²⟩ time integral.

Run:  python -m pytest tests/test_core_loss_surface.py -q
"""
import math

import numpy as np
import pytest

from motor_ai_sim.core_loss_surface import get_surface, wants_surface
from motor_ai_sim.materials import (
    effective_bertotti, effective_loss_surface, get_steel,
)
from motor_ai_sim.simulation.losses import (
    TWO_PI_SQ, central_difference, iron_loss_series,
)

# The records that opted in — audited tables, datasheet anchors verified in
# tests/test_materials_fit.py. Everything else in the library stays on Bertotti.
SURFACE_STEELS = ["B15AHV950M", "B10AHV900M", "20RSW175"]

# The 150 mm 24s28p's own electrical frequency (28 poles, 4000 rpm).
F_OP = 933.33


class TestAnchors:
    """Every measured point of every opted-in record, reproduced."""

    @pytest.mark.parametrize("name", SURFACE_STEELS)
    def test_every_measured_point_is_returned_exactly(self, name):
        s = get_steel(name)
        surf = effective_loss_surface(s)
        assert surf is not None, f"{name} opted in but built no surface"
        n = 0
        worst = 0.0
        for fk, curve in s.core_loss_curves.items():
            f = float(fk[:-2])
            B = np.array([p[0] for p in curve])
            P = np.array([p[1] for p in curve]) * s.density   # W/kg -> W/m³
            got = surf.w_per_m3(B, f)
            worst = max(worst, float(np.max(np.abs(got / P - 1.0))))
            n += B.size
        assert n == surf.n_points
        assert worst < 1e-9, (
            f"{name}: the surface no longer INTERPOLATES its own data — worst "
            f"anchor off by {worst*100:.3g} % over {n} points")

    def test_the_tally_is_the_whole_measured_body(self):
        """All three records together — a truncated ingest shows up here."""
        counts = {n: effective_loss_surface(get_steel(n)).n_points
                  for n in SURFACE_STEELS}
        assert counts == {"B15AHV950M": 217, "B10AHV900M": 406,
                          "20RSW175": 505}
        assert sum(counts.values()) == 1128

    def test_the_saturation_under_read_is_closed(self):
        """The gap this change exists to close, measured on the steel and the
        operating point it was measured on.

        At 1.5 T / 933 Hz the three-coefficient fit read 10-15 % below the
        manufacturer's own surface (tests/test_materials_fit.py pins that gap on
        the FIT, which still has it — it is still the out-of-envelope
        fallback). The model actually in use must now BE the measurement.
        """
        s = get_steel("B15AHV950M")
        surf = effective_loss_surface(s)
        c800 = dict(s.core_loss_curves["800Hz"])
        c1000 = dict(s.core_loss_curves["1000Hz"])
        for b in (1.0, 1.25, 1.5):
            p800 = float(np.interp(b, list(c800), list(c800.values())))
            p1000 = float(np.interp(b, list(c1000), list(c1000.values())))
            # log-log in f between the two bracketing measured curves
            p_meas = p800 * (p1000 / p800) ** (math.log(F_OP / 800.0)
                                               / math.log(1000.0 / 800.0))
            got = float(surf.w_per_m3(np.array([b]), F_OP)[0]) / s.density
            assert got == pytest.approx(p_meas, rel=0.02), (
                f"at {b} T the surface says {got:.2f} W/kg where the two "
                f"bracketing measured curves say {p_meas:.2f}")
        # …and it is materially ABOVE what the fit says, up in saturation.
        kh, kc, ke = effective_bertotti(s)
        b = 1.5
        p_fit = (kh * F_OP * b ** 2 + kc * F_OP ** 2 * b ** 2
                 + ke * F_OP ** 1.5 * b ** 1.5) / s.density
        p_surf = float(surf.w_per_m3(np.array([b]), F_OP)[0]) / s.density
        assert p_surf / p_fit - 1.0 > 0.10


class TestEnvelope:
    """The staircase edge, and the blend that crosses it without a kink."""

    def test_the_envelope_is_the_tables_own_top_point(self):
        s = get_steel("B15AHV950M")
        surf = effective_loss_surface(s)
        for fk, curve in s.core_loss_curves.items():
            f = float(fk[:-2])
            b_top = max(p[0] for p in curve)
            assert surf.envelope_b(f) == pytest.approx(b_top, rel=1e-12)
        # 933 Hz sits between the 800 Hz (1.696 T) and 1000 Hz (1.597 T) curves,
        # so this machine's teeth leave the measured region above ~1.63 T.
        assert 1.59 < surf.envelope_b(F_OP) < 1.70

    def test_measured_anchors_are_inside_the_envelope(self):
        """Not a tautology: the envelope is built from each curve's OWN top
        point rather than from a running minimum across frequency, precisely so
        that no measured point is treated as an extrapolation and blended."""
        for name in SURFACE_STEELS:
            s = get_steel(name)
            surf = effective_loss_surface(s)
            for fk, curve in s.core_loss_curves.items():
                f = float(fk[:-2])
                B = np.array([p[0] for p in curve])
                assert float(np.max(surf.outsideness(B, f))) == 0.0

    @pytest.mark.parametrize("name", SURFACE_STEELS)
    def test_the_blend_crosses_the_b_edge_with_no_jump_and_no_kink(self, name):
        """Value AND slope continuous at B = B_env(f).

        The blend weight is a smoothstep of the distance outside, whose value
        and derivative are both zero at the boundary — so the surface hands over
        to the Bertotti extrapolation tangentially. A step here would be a
        discontinuity in the objective; a kink would be a discontinuity in its
        gradient, which is worse, because a descent method follows it.
        """
        surf = effective_loss_surface(get_steel(name))
        for f in (200.0, F_OP, 2000.0):
            b0 = surf.envelope_b(f)
            h = 1e-5
            grid = b0 * np.exp(np.array([-3, -2, -1, 1, 2, 3]) * h)
            p = np.log(surf.w_per_m3(grid, f))
            # one-sided slopes in log-log, from either side of the boundary
            d_in = (p[2] - p[0]) / (2 * h)
            d_out = (p[5] - p[3]) / (2 * h)
            p_in = p[2] + d_in * h            # extrapolate both sides TO b0
            p_out = p[3] - d_out * h
            assert abs(p_in - p_out) < 1e-6, f"{name} @ {f} Hz: value steps"
            assert abs(d_in - d_out) < 5e-3 * max(1.0, abs(d_in)), (
                f"{name} @ {f} Hz: slope kinks ({d_in:.4f} -> {d_out:.4f})")

    def test_the_blend_crosses_the_f_edge_with_no_jump_and_no_kink(self):
        """Same, at the top of the frequency table (B15AHV950M stops at 10 kHz).

        A 933 Hz machine's 12th harmonic is already past it, so this edge is
        crossed on every real solve, not in the abstract.
        """
        surf = effective_loss_surface(get_steel("B15AHV950M"))
        f0 = float(surf.f[-1])
        b = 0.2
        h = 1e-5
        grid = f0 * np.exp(np.array([-3, -2, -1, 1, 2, 3]) * h)
        p = np.array([math.log(surf.w_per_m3(np.array([b]), ff)[0]) for ff in grid])
        d_in = (p[2] - p[0]) / (2 * h)
        d_out = (p[5] - p[3]) / (2 * h)
        assert abs((p[2] + d_in * h) - (p[3] - d_out * h)) < 1e-6
        assert abs(d_in - d_out) < 5e-3 * max(1.0, abs(d_in))

    def test_far_outside_the_answer_IS_the_bertotti_extrapolation(self):
        """The blend completes: past B_BLEND_FACTOR·B_env the measured surface
        has no say left, so nothing is claimed as measured that was not."""
        from motor_ai_sim.core_loss_surface import B_BLEND_FACTOR
        for name in SURFACE_STEELS:
            surf = effective_loss_surface(get_steel(name))
            for f, mult in ((F_OP, B_BLEND_FACTOR * 1.01),
                            (F_OP, 2.0), (30.0, 3.0)):
                b = surf.envelope_b(f) * mult
                assert float(surf.w_per_m3(np.array([b]), f)[0]) == pytest.approx(
                    float(surf.bertotti_w_per_m3(np.array([b]), f)[0]), rel=1e-9)
        # …and an octave past the top of the frequency table, at any induction.
        surf = effective_loss_surface(get_steel("B15AHV950M"))
        f = float(surf.f[-1]) * 2.5
        assert float(surf.w_per_m3(np.array([0.3]), f)[0]) == pytest.approx(
            float(surf.bertotti_w_per_m3(np.array([0.3]), f)[0]), rel=1e-9)

    # The ONE place the manufacturer's own table goes down: B10AHV900M's 50 Hz
    # curve reads 2.361 W/kg at 1.717 T and 2.346 W/kg at 1.737 T — a 0.6 %
    # wobble on a saturated sample at the very top of the curve. The surface
    # INTERPOLATES its data, so it reproduces the wobble; it is named here
    # rather than smoothed away, because smoothing it would break the anchors
    # above and hide a data defect instead of reporting one. No machine in this
    # tool runs a 0.10 mm lamination at 1.72 T and 50 Hz.
    KNOWN_DIP = {"B10AHV900M": (50.0, 1.70, 1.75)}

    def test_the_only_non_monotone_measured_segment_is_the_known_one(self):
        found = {}
        for name in SURFACE_STEELS:
            s = get_steel(name)
            for fk, curve in s.core_loss_curves.items():
                pts = sorted(curve)
                for (b0, p0), (b1, p1) in zip(pts, pts[1:]):
                    if p1 <= p0:
                        found.setdefault(name, []).append((fk, b0, b1))
        assert found == {"B10AHV900M": [("50Hz", 1.717, 1.737)]}, found

    def test_the_surface_rises_with_induction_and_with_frequency(self):
        """Monotonicity, over the whole (B, f) box a machine can reach —
        including the blend band and both extrapolations. A loss model that
        dips somewhere hands the optimizer a free lunch in that dip.

        This is what sets ``B_BLEND_FACTOR``: the handover from the measured
        surface down to the (lower) Bertotti extrapolation costs slope, and a
        band narrower than ~1.25 makes 20RSW175 DIP between 1.62 and 1.68 T at
        933 Hz. A change that narrows the band is expected to fail here.
        """
        for name in SURFACE_STEELS:
            surf = effective_loss_surface(get_steel(name))
            dip = self.KNOWN_DIP.get(name)
            B = np.exp(np.linspace(math.log(0.02), math.log(2.6), 2000))
            for f in (20.0, 50.0, 100.0, 400.0, F_OP, 2000.0, 5000.0,
                      12000.0, 40000.0):
                p = surf.w_per_m3(B, f)
                ok = np.diff(p) > 0
                if dip and f == dip[0]:
                    ok |= (B[:-1] > dip[1]) & (B[1:] < dip[2])
                assert np.all(ok), f"{name}: dP/dB <= 0 at {f} Hz"
            for b in (0.1, 0.5, 1.0, 1.5, 1.9):
                f = np.exp(np.linspace(math.log(20.0), math.log(40000.0), 400))
                p = np.array([surf.w_per_m3(np.array([b]), ff)[0] for ff in f])
                assert np.all(np.diff(p) > 0), f"{name}: dP/df <= 0 at {b} T"


class TestActivation:
    """Per record, by the record's own field — nothing else moves."""

    def test_only_the_audited_records_opt_in(self):
        from motor_ai_sim.materials import list_materials
        on = [n for n in list_materials()["steel"] if wants_surface(get_steel(n))]
        assert sorted(on) == sorted(SURFACE_STEELS)

    def test_a_steel_with_curves_but_no_opt_in_stays_on_bertotti(self):
        """20SW1200 carries 10 measured curves and 269 points. It is NOT opted
        in — its table has not been audited against a datasheet — so it must
        still get exactly the three-coefficient answer it was validated on."""
        s = get_steel("20SW1200")
        assert len(s.core_loss_curves) == 10
        assert effective_loss_surface(s) is None

    def test_an_opt_in_without_enough_table_falls_back_loudly(self, caplog):
        s = get_steel("B15AHV950M")
        thin = type(s)(**{**s.__dict__,
                          "name": "thin_record",
                          "core_loss_curves": {"50Hz": s.core_loss_curves["50Hz"]}})
        with caplog.at_level("WARNING"):
            assert get_surface(thin, effective_bertotti(s)) is None
        assert "falling back" in caplog.text


# ---------------------------------------------------------------------------
# The consumer: simulation/losses.iron_loss_series
# ---------------------------------------------------------------------------
def _sine_history(n, amp_by_harmonic, phase=0.0):
    """(n_frames, 1) history: Σ A_k·cos(2πk t/T + φ) over one period."""
    t = np.arange(n) / float(n)
    x = np.zeros((n, 1))
    for k, a in amp_by_harmonic.items():
        x[:, 0] += a * np.cos(2 * math.pi * k * t + phase)
    return x


class TestHarmonicSummation:
    """What the sum over harmonics preserves, and what it assumes."""

    N = 64
    F = 500.0

    def _run(self, X, Y, steel, kf_area=1.0, n_periods=1.0):
        dt = 1.0 / (self.F * self.N / n_periods)
        terms = {}
        cl, pc = iron_loss_series(
            X, Y, np.arange(X.shape[1]), np.full(X.shape[1], kf_area),
            steel, 1.0, self.F, X.shape[0], central_difference(dt),
            lambda m: effective_bertotti(m), terms=terms, n_periods=n_periods)
        return float(np.mean(cl)) + pc, terms

    def test_a_pure_sinusoid_gives_the_surface_value(self):
        """One component, one harmonic, unit volume: the answer must BE
        P_meas(B/k_f, f)·k_f·V and nothing else — no equivalent amplitude, no
        fitted coefficient in the path."""
        s = get_steel("B15AHV950M")
        surf = effective_loss_surface(s)
        kf = s.stacking_factor
        for b in (0.3, 1.0, 1.4):
            X = _sine_history(self.N, {1: b})
            tot, terms = self._run(X, np.zeros_like(X), s)
            want = float(surf.w_per_m3(np.array([b / kf]), self.F)[0]) * kf
            assert tot == pytest.approx(want, rel=1e-10)
            assert terms["model"] == "measured_surface"
            assert terms["fundamental_only_W"] == pytest.approx(want, rel=1e-10)

    def test_the_two_axes_are_summed_independently(self):
        """The axis decomposition of the Bertotti path, unchanged: X and Y are
        decomposed and evaluated separately and their losses added. Rotating
        loci still read low — that is a stated under-read, not a bug fixed here.
        """
        s = get_steel("B15AHV950M")
        X = _sine_history(self.N, {1: 0.8})
        Y = _sine_history(self.N, {1: 0.8}, phase=math.pi / 2)   # circular locus
        both, _ = self._run(X, Y, s)
        one, _ = self._run(X, np.zeros_like(X), s)
        assert both == pytest.approx(2.0 * one, rel=1e-10)

    def test_harmonics_add_and_are_reported_against_the_fundamental(self):
        """A slot-ripple harmonic costs extra, and the run says how much."""
        s = get_steel("B15AHV950M")
        X1 = _sine_history(self.N, {1: 1.0})
        X2 = _sine_history(self.N, {1: 1.0, 12: 0.06})
        base, _ = self._run(X1, np.zeros_like(X1), s)
        rich, terms = self._run(X2, np.zeros_like(X2), s)
        assert rich > base
        assert terms["fundamental_only_W"] == pytest.approx(base, rel=1e-10)
        assert terms["surface_W"] == pytest.approx(rich, rel=1e-12)

    def test_n_periods_sets_the_harmonic_frequencies(self):
        """The same physical waveform captured over two electrical periods must
        cost the same. It does not if the DFT bins are read as harmonics of
        f_elec instead of f_elec/n_periods — which is why n_periods is an
        argument rather than an assumption."""
        s = get_steel("B15AHV950M")
        one = _sine_history(self.N, {1: 1.0})
        two = _sine_history(2 * self.N, {2: 1.0})       # 2 periods of the same
        a, _ = self._run(one, np.zeros_like(one), s, n_periods=1.0)
        b, _ = self._run(two, np.zeros_like(two), s, n_periods=2.0)
        assert b == pytest.approx(a, rel=1e-10)

    def test_the_classical_series_is_still_the_ddt_integral(self):
        """The reported eddy term is unchanged: it is the true ⟨(dB/dt)²⟩ of the
        captured waveform, not a per-harmonic reconstruction. Only the TOTAL
        comes from the surface; the remainder goes to the per-cycle bucket."""
        s = get_steel("B15AHV950M")
        kh, kc, ke = effective_bertotti(s)
        kf = s.stacking_factor
        X = _sine_history(self.N, {1: 1.0, 5: 0.1})
        tot, terms = self._run(X, np.zeros_like(X), s)
        dt = 1.0 / (self.F * self.N)
        d = central_difference(dt)(X) / kf
        want_eddy = (kc / TWO_PI_SQ) * float(np.mean(np.sum(d ** 2, axis=1))) * kf
        assert terms["eddy_W"] == pytest.approx(want_eddy, rel=1e-10)
        assert terms["hysteresis_W"] + terms["excess_W"] + terms["eddy_W"] \
            == pytest.approx(tot, rel=1e-10)

    def test_the_kf_algebra_survives(self):
        """B_steel = B/k_f evaluated on the steel volume k_f·V — the lamination
        algebra of 3495373, unchanged by the model swap. Checked as a RATIO
        against k_f = 1, which is the only form that does not re-derive the
        surface to test the surface."""
        s = get_steel("B15AHV950M")
        surf = effective_loss_surface(s)
        X = _sine_history(self.N, {1: 1.0})
        for kf in (0.85, 0.92, 1.0):
            st = type(s)(**{**s.__dict__, "stacking_factor": kf})
            tot, _ = self._run(X, np.zeros_like(X), st)
            want = float(surf.w_per_m3(np.array([1.0 / kf]), self.F)[0]) * kf
            assert tot == pytest.approx(want, rel=1e-10)

    def test_a_non_opted_in_steel_is_bit_identical_to_the_old_path(self):
        s = get_steel("20SW1200")
        kh, kc, ke = effective_bertotti(s)
        kf = s.stacking_factor
        X = _sine_history(self.N, {1: 1.0, 7: 0.05})
        tot, terms = self._run(X, np.zeros_like(X), s)
        dt = 1.0 / (self.F * self.N)
        d = central_difference(dt)(X) / kf
        eddy = (kc / TWO_PI_SQ) * float(np.mean(np.sum(d ** 2, axis=1))) * kf
        b2 = ((X.max(0) - X.min(0)) * 0.5) ** 2 / kf ** 2
        hyst = float(np.sum(kh * self.F * b2)) * kf
        exc = float(np.sum(ke * self.F ** 1.5 * b2 ** 0.75)) * kf
        assert terms["model"] == "bertotti"
        assert tot == pytest.approx(eddy + hyst + exc, rel=1e-12)

    def test_the_leakage_guard_does_nothing_to_a_closed_window(self):
        """A capture that closes on itself must go through the DFT untouched.

        This is the property that makes the guard safe to apply everywhere: the
        stator half IS periodic in one electrical period, and an unconditional
        ramp removal would have quietly taken 2 % off it (the naive end-to-start
        difference on a sampled sinusoid is one sample of SLOPE, not a step).
        """
        from motor_ai_sim.simulation.losses import harmonic_amplitudes
        for n in (12, 36, 40, 64):
            t = np.arange(n) / float(n)
            X = (np.cos(2 * math.pi * t)
                 + 0.1 * np.cos(2 * math.pi * 5 * t + 0.7))[:, None]
            wrap = {}
            amp, freqs = harmonic_amplitudes(X, 500.0, 1.0, wrap)
            assert wrap["weight"] == 0.0, f"guard fired on a closed window (n={n})"
            assert amp[0, 0] == pytest.approx(1.0, abs=1e-12)
            assert amp[4, 0] == pytest.approx(0.1, abs=1e-12)

    def test_the_leakage_guard_removes_an_open_window_step(self):
        """A window that ends mid-cycle: the raw DFT smears the step across
        every bin, and because loss rises with frequency the tail is billed at
        the top of the spectrum. The guard has to take that out."""
        from motor_ai_sim.simulation.losses import harmonic_amplitudes
        n = 40
        t = np.arange(n) / float(n)
        # 1.71 cycles in the window — a 24-slot / 14-pole-pair rotor point.
        X = np.cos(2 * math.pi * 1.714 * t)[:, None]
        wrap = {}
        got, _ = harmonic_amplitudes(X, 933.33, 1.0, wrap)
        assert wrap["frac"] > 0.3 and wrap["weight"] > 0.9
        # the unguarded transform, for comparison
        C = np.fft.rfft(X, axis=0) / n
        raw = 2.0 * np.abs(C)
        raw[-1] = np.abs(C[-1])
        raw = raw[1:]
        tail_raw = float(np.sum(raw[7:, 0] ** 2))
        tail_got = float(np.sum(got[7:, 0] ** 2))
        assert tail_got < 0.25 * tail_raw, (
            "the high-order leakage tail survived the guard")

    def test_the_loss_density_map_uses_the_same_model(self):
        """The PICTURE and the NUMBER must be one thing.

        The map's iron shape is normalised to the reported watts, so this
        cannot move P_fe — it moves the tooth-vs-yoke contrast and the
        stator/rotor split, which is what the map is read for, and those DO
        change when a saturating tooth stops obeying B². A map still drawn
        from the three coefficients while the sidebar bills the surface would
        be a quietly wrong picture of a right number.
        """
        from motor_ai_sim.simulation.losses import loss_density_map
        s = get_steel("B15AHV950M")
        n = 32
        n_st = 2
        # element 0 saturated (1.8 T), element 1 not (0.6 T) — the pair whose
        # RATIO the two models disagree about
        X = np.concatenate([_sine_history(n, {1: 1.8}),
                            _sine_history(n, {1: 0.6})], axis=1)
        kw = dict(
            n_stator_elems=n_st, n_elems=n_st + 1,
            hist_sx=X, hist_sy=np.zeros_like(X),
            hist_rx=[], hist_ry=[], hist_mx=[], hist_my=[],
            hist_cx=[], hist_cy=[],
            iron_s_idx=np.array([0, 1]), iron_r_idx=np.array([], int),
            mag_idx=np.array([], int), coil_idx=np.array([], int),
            areas_s=np.ones(n_st), areas_r=np.ones(1),
            coil_centroids=np.zeros((0, 0)), steel_r=None,
            bertotti=lambda m: effective_bertotti(m),
            f_elec_hz=100.0, stack_length_m=0.05, sector_scale=1.0,
            P_fe_avg=10.0, P_mag_avg=0.0, P_cu_dc=0.0, P_cu_ac_avg=0.0,
            sigma_cu=5.8e7, d_cu_r=1e-3, d_cu_t=1e-3,
            ddt=lambda A, qp=None: central_difference(1e-4)(A))
        from motor_ai_sim.simulation.losses import surface_loss_density
        dens, _, _ = loss_density_map(steel_s=s, **kw)
        # the map's iron shape IS the surface, element for element…
        want = surface_loss_density(X, np.zeros_like(X),
                                    effective_loss_surface(s),
                                    1.0 / s.stacking_factor, 100.0, 1.0)
        assert dens[0] / dens[1] == pytest.approx(want[0] / want[1], rel=1e-12)
        # …normalised to the reported watts, so the total is untouched
        assert float(np.sum(dens[:2])) * kw["stack_length_m"] == pytest.approx(
            kw["P_fe_avg"], rel=1e-12)
        # …and it is NOT the Bertotti shape: at 1.8 T against 0.6 T the two
        # models disagree about the contrast, which is what the map shows.
        kh, kc, ke = effective_bertotti(s)
        kf = s.stacking_factor
        b2 = (((X.max(0) - X.min(0)) * 0.5) ** 2) / kf ** 2
        d = central_difference(1e-4)(X) / kf
        bert = kf * (kh * 100.0 * b2 + ke * 100.0 ** 1.5 * b2 ** 0.75
                     + (kc / TWO_PI_SQ) * np.mean(d ** 2, axis=0))
        assert abs(want[0] / want[1] - bert[0] / bert[1]) / (bert[0] / bert[1]) > 0.05

    def test_leaving_the_envelope_is_logged_once_and_loudly(self, caplog):
        """A tooth driven past the table's top point must not be reported as if
        it had been measured. One WARNING per half per solve, naming the worst
        point and the boundary it crossed."""
        s = get_steel("B15AHV950M")
        X = _sine_history(self.N, {1: 1.9})       # well past B_env at 500 Hz
        with caplog.at_level("WARNING"):
            self._run(X, np.zeros_like(X), s)
        assert "OUTSIDE the measured envelope" in caplog.text
        assert len([r for r in caplog.records
                    if "OUTSIDE the measured envelope" in r.message]) == 1

    def test_staying_inside_the_envelope_says_so_quietly(self, caplog):
        s = get_steel("B15AHV950M")
        X = _sine_history(self.N, {1: 0.9})
        with caplog.at_level("INFO"):
            _, terms = self._run(X, np.zeros_like(X), s)
        assert "entirely INSIDE the measured envelope" in caplog.text
        assert terms["envelope_out_frac"] == 0.0
