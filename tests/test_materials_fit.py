"""Tests for the Maxwell-style Bertotti fit from measured core-loss curves.

Fast (no FEM): exercises materials.fit_bertotti_from_curves /
effective_bertotti on the real library data against the MANUFACTURER'S OWN
anchor points.

Two tiers, deliberately. B15AHV950M is the steel the active 150 mm 24s28p is
built from, so it gets the full ladder: every measured frequency curve
reproduced, the datasheet's guaranteed points hit, the 933 Hz operating point
checked against the measured surface, and the fit's known saturation under-read
pinned. B10AHV900M and 20RSW175 are INGESTED — their records are here so they
can be selected — and get smoke-level checks only: the data arrived intact, the
B(H) is usable, the fit is sane, the coefficients belong to the curves under
them. Their full validation belongs to the day a machine is actually built from
them, run against that machine's operating point rather than in the abstract.

The bar exists because a Bertotti fit is three numbers standing in for a
measured surface, and the only honest way to know whether it stands in well is
to ask the surface. The library used to carry a 5-frequency SUBSET of
B15AHV950M's 15 measured curves under a description naming a different steel
entirely (Nippon Steel 23ZDKH75, grain-oriented — B15AHV950M is Baosteel NGO).

Run:  python -m pytest tests/test_materials_fit.py -q
"""
import math

import numpy as np
import pytest

from motor_ai_sim.materials import (
    SteelMaterial, fit_bertotti_from_curves, effective_bertotti, get_steel,
    material_from_dict,
)

# Steels whose records are manufacturer data (curve workbook + product
# datasheet), with the datasheet's guaranteed / typical anchors:
#   P(1.0 T, 400 Hz) in W/kg, and B at H = 5000 A/m in T.
MANUFACTURER_STEELS = {
    # Baosteel B15AHV950M, 0.15 mm NGO — "Product info 2025"
    "B15AHV950M": dict(density=7600.0, k_f=0.92, n_freqs=15,
                       p10_400_max=9.50, p10_400_typ=9.00,
                       b5000_min=1.64, b5000_typ=1.66),
    # Baosteel B10AHV900M, 0.10 mm NGO — "Product info 2025"
    "B10AHV900M": dict(density=7600.0, k_f=0.92, n_freqs=12,
                       p10_400_max=9.00, p10_400_typ=8.50,
                       b5000_min=1.64, b5000_typ=1.66),
    # Shougang 20RSW175 (VHs), 0.20 mm NGO
    "20RSW175": dict(density=7750.0, k_f=0.95, n_freqs=23,
                     p10_400_max=15.00, p10_400_typ=14.4,
                     b5000_min=1.75, b5000_typ=1.76),
}


def _p_wkg(steel, f, b):
    kh, kc, ke = effective_bertotti(steel)
    return (kh * f * b ** 2 + kc * f ** 2 * b ** 2
            + ke * f ** 1.5 * b ** 1.5) / steel.density


def _curve(steel, f_hz, b):
    c = steel.core_loss_curves[f"{int(f_hz)}Hz"]
    return float(np.interp(b, [p[0] for p in c], [p[1] for p in c]))


class TestIngestedRecords:
    """Smoke level, all three: the manufacturer data arrived intact and usable.

    These are the checks that catch an INGEST fault — a truncated workbook, a
    datasheet mismatch, a B(H) the solver cannot invert, coefficients that do
    not belong to the curves under them. Cheap, and they run for every
    manufacturer record. The depth lives in TestB15AtTheOperatingPoint below,
    for the one steel a machine is currently built from.
    """

    @pytest.mark.parametrize("name", sorted(MANUFACTURER_STEELS))
    def test_record_carries_every_measured_curve(self, name):
        spec = MANUFACTURER_STEELS[name]
        s = get_steel(name)
        assert s.density == spec["density"]
        assert s.stacking_factor == spec["k_f"]
        assert len(s.core_loss_curves) == spec["n_freqs"], (
            f"{name} must carry ALL {spec['n_freqs']} measured frequency curves "
            f"— it carries {len(s.core_loss_curves)}")

    @pytest.mark.parametrize("name", sorted(MANUFACTURER_STEELS))
    def test_measured_curves_hit_the_datasheet_anchors(self, name):
        """P(1.0 T, 400 Hz) and B(5000 A/m) — the two numbers the datasheet
        guarantees.  If the curve file and the datasheet disagree, the record is
        two materials stitched together and nothing below it means anything."""
        spec = MANUFACTURER_STEELS[name]
        s = get_steel(name)
        p = _curve(s, 400, 1.0)
        assert p <= spec["p10_400_max"], "worse than the guaranteed maximum"
        assert p == pytest.approx(spec["p10_400_typ"], rel=0.05)
        bh = s.bh_curve
        b5000 = float(np.interp(5000.0, [h for h, _ in bh], [b for _, b in bh]))
        assert b5000 >= spec["b5000_min"], "below the guaranteed induction"
        assert b5000 == pytest.approx(spec["b5000_typ"], rel=0.02)

    @pytest.mark.parametrize("name", sorted(MANUFACTURER_STEELS))
    def test_bh_curve_is_monotone(self, name):
        """B(H) must rise everywhere.  The raw workbooks plateau and then DIP at
        the top (B15AHV950M: 1.945 T at 49 kA/m down to 1.939 T at 80 kA/m —
        noise on a saturated sample), and a non-monotone B(H) is not something a
        Picard / Newton iteration can invert."""
        bh = get_steel(name).bh_curve
        assert bh[0] == (0.0, 0.0)
        for (h0, b0), (h1, b1) in zip(bh, bh[1:]):
            assert h1 > h0 and b1 > b0, f"{name}: not monotone at H={h1}"

    # Electrical frequency a machine in this tool actually runs at, slot and
    # PWM-ripple harmonics included.  The records reach 10-20 kHz; the fit is
    # held to a tighter bar inside this band than outside it, because the three
    # coefficients are shared across the whole surface and cannot be excellent
    # everywhere — so the test says WHERE it demands accuracy instead of
    # loosening the bar until everything passes.
    MOTOR_BAND_HZ = 5000.0

    @pytest.mark.parametrize("name", sorted(MANUFACTURER_STEELS))
    def test_fit_is_sane_over_the_motor_band(self, name):
        """Smoke: the three coefficients reproduce the measured surface to
        engineering accuracy across the frequencies a motor runs at."""
        s = get_steel(name)
        errs = {}
        for fk, curve in s.core_loss_curves.items():
            f = float(fk[:-2])
            rel = [abs(_p_wkg(s, f, b) - p) / p for b, p in curve if b >= 0.05]
            errs[f] = float(np.mean(rel))
        band = [e for f, e in errs.items() if f <= self.MOTOR_BAND_HZ]
        assert len(band) >= 8, "the motor band must be measured, not extrapolated"
        assert float(np.mean(band)) < 0.08


class TestB15AtTheOperatingPoint:
    """FULL ladder — B15AHV950M is the steel the active 150 mm is built from.

    Every claim here is about the number that machine's core loss is actually
    made of: 933.3 Hz (24s/28p, 4000 rpm) at 0.5-1.5 T.
    """

    MOTOR_BAND_HZ = TestIngestedRecords.MOTOR_BAND_HZ

    def test_fit_reproduces_every_frequency_curve(self):
        """effective_bertotti against EVERY measured (f, B) point of the record.

        Per-frequency means, not one global mean: a fit can average well and
        still be systematically wrong on the one curve the machine runs on. The
        three-term Bertotti form cannot follow the saturation upturn above
        ~1.5 T, so the bar is engineering accuracy (mean < 12 % on every curve
        in the motor band, < 8 % over the band) rather than exactness.
        """
        s = get_steel("B15AHV950M")
        errs = {}
        for fk, curve in s.core_loss_curves.items():
            f = float(fk[:-2])
            rel = [abs(_p_wkg(s, f, b) - p) / p for b, p in curve if b >= 0.05]
            errs[f] = float(np.mean(rel))
        band = {f: e for f, e in errs.items() if f <= self.MOTOR_BAND_HZ}
        worst = max(band, key=band.get)
        assert band[worst] < 0.12, (
            f"worst in-band curve {worst:.0f} Hz at {band[worst]*100:.1f} %")
        assert float(np.mean(list(band.values()))) < 0.08
        # Outside the band the same three coefficients still have to be usable —
        # a fit that has gone to pieces at 10 kHz would show up here first.
        assert max(errs.values()) < 0.20

    def test_933hz_is_interpolation_not_extrapolation(self):
        """933.3 Hz is INSIDE the measured range — the record brackets it with
        800 Hz and 1000 Hz curves, so the operating point is interpolation, not
        f^2 / f^1.5 extrapolation off a 50 Hz anchor.  This was the suspected
        cause of the low core loss, and it is not it: the record already
        covered the point."""
        s = get_steel("B15AHV950M")
        assert "800Hz" in s.core_loss_curves and "1000Hz" in s.core_loss_curves
        f0 = 933.33
        for b0 in (0.5, 1.0, 1.25):
            p800, p1000 = _curve(s, 800, b0), _curve(s, 1000, b0)
            p_meas = p800 * (p1000 / p800) ** (math.log(f0 / 800)
                                               / math.log(1000 / 800))
            assert abs(_p_wkg(s, f0, b0) - p_meas) / p_meas < 0.10

    def test_the_fit_under_reads_the_measured_surface_in_saturation(self):
        """The KNOWN, REPORTED limitation, pinned so it cannot drift silently.

        Above ~1.5 T the measured curve turns up and a fixed B^2 cannot follow
        it, so the fit reads LOW exactly where 43 % of this machine's stator
        loss sits.  Pinned as a bracket, not an equality: it is a real gap
        (-10 to -15 % at 1.5 T, 933 Hz), it is the largest single piece of the
        remaining difference to the customer's Ansys figure, and no correction
        coefficient is applied to hide it.  A change that closes it (measured
        P(B, f) interpolation, or a free B exponent) is expected to break this
        test — that is what it is for.
        """
        s = get_steel("B15AHV950M")
        f0 = 933.33
        p800, p1000 = _curve(s, 800, 1.5), _curve(s, 1000, 1.5)
        p_meas = p800 * (p1000 / p800) ** (math.log(f0 / 800)
                                           / math.log(1000 / 800))
        err = _p_wkg(s, f0, 1.5) / p_meas - 1.0
        assert -0.20 < err < -0.05, (
            f"the saturation under-read moved to {err*100:.1f} % — if that is "
            f"an improvement, re-cut this bracket and the gap table in "
            f"docs/LOSS_MODEL.md with it")


class TestFitMechanics:
    def test_fit_20sw1200_quality(self):
        """The fit over the REAL measured curves must stay within engineering
        accuracy (mean relative error < 10 %, p90 < 25 %)."""
        s = get_steel("20SW1200")
        fit = fit_bertotti_from_curves(s)
        assert fit is not None, "20SW1200 has measured curves — fit must succeed"
        kh, kc, ke, report = fit
        assert kh > 0 and kc > 0          # hysteresis + classical must be present
        assert report["n_points"] > 100   # all 10 frequency curves used
        assert report["rel_err_mean"] < 0.10
        assert report["rel_err_p90"] < 0.25

    def test_fit_matches_measured_at_operating_point(self):
        """Fitted Bertotti at (466.7 Hz, 1.5 T) must be within 20 % of the
        measured-curve interpolation (the truth)."""
        s = get_steel("20SW1200")
        f0, b0 = 466.67, 1.5
        p_fit = _p_wkg(s, f0, b0)
        p400, p500 = _curve(s, 400, b0), _curve(s, 500, b0)
        p_meas = p400 * (p500 / p400) ** (math.log(f0 / 400) / math.log(500 / 400))
        assert abs(p_fit - p_meas) / p_meas < 0.20

    def test_effective_bertotti_fallback_to_yaml(self):
        """A steel WITHOUT measured curves must fall back to its YAML coefficients."""
        s = SteelMaterial(name="dummy", core_loss_kh=11.0, core_loss_kc=2.0,
                          core_loss_ke=0.5, core_loss_curves={})
        assert effective_bertotti(s) == (11.0, 2.0, 0.5)

    def test_effective_bertotti_none(self):
        assert effective_bertotti(None) == (0.0, 0.0, 0.0)

    def test_fit_is_cached(self):
        s = get_steel("20SW1200")
        a = fit_bertotti_from_curves(s)
        b = fit_bertotti_from_curves(s)
        assert a is b                      # same tuple object → cache hit

    @pytest.mark.parametrize("name", sorted(MANUFACTURER_STEELS))
    def test_stored_coefficients_agree_with_the_fit(self, name):
        """The record's kh/kc/ke ARE the fit over its own curves.  They are the
        fallback for every path that cannot reach the curves, and a fallback
        that disagrees with the primary is a silent second answer."""
        s = get_steel(name)
        assert effective_bertotti(s) == pytest.approx(
            (s.core_loss_kh, s.core_loss_kc, s.core_loss_ke), rel=1e-6)

    def test_global_layer_record_keeps_its_measured_curves(self):
        """A steel served from the admin/global layer must fit from ITS curves.

        material_from_dict used to drop core_loss_curves as "display-only", so
        the same steel gave two different core losses depending on which layer
        answered.  Round-trip the real record through that path — under a name
        the fit cache has never seen — and require the SAME coefficients."""
        s = get_steel("B15AHV950M")
        d = {"category": "steel", "name": s.name, "density": s.density,
             "stacking_factor": s.stacking_factor,
             # deliberately WRONG stored coefficients: only the curves may win
             "core_loss_kh": 1.0, "core_loss_kc": 1.0, "core_loss_ke": 1.0,
             "core_loss_curve_unit": s.core_loss_curve_unit,
             "bh_curve": [list(p) for p in s.bh_curve],
             "core_loss_curves": {k: [list(p) for p in v]
                                  for k, v in s.core_loss_curves.items()}}
        g = material_from_dict("steel", "B15AHV950M__global_roundtrip", d)
        assert len(g.core_loss_curves) == len(s.core_loss_curves)
        assert effective_bertotti(g) == pytest.approx(
            effective_bertotti(s), rel=1e-9)
