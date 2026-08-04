"""Direct interpolation of a manufacturer's MEASURED core-loss surface P(B, f).

Why this module exists
----------------------
The three-coefficient Bertotti form

    P/V_steel = k_h·f·B² + k_c/(2π²)·⟨(dB/dt)²⟩ + k_e·f^1.5·B^1.5

carries a FIXED B² (and B^1.5) law.  Real non-oriented steel turns up above the
knee — the loss per cycle at 1.8 T is far more than (1.8/1.5)² times the loss at
1.5 T — and no choice of three constants can follow that and the low-induction
end at the same time.  Measured on B15AHV950M over the 150 mm 24s28p machine's
own flux distribution at 933 Hz, the fit integrates 53.6 W of per-cycle loss
where the manufacturer's measured surface integrates 62.3 W (−16.4 %), because
43 % of that machine's stator loss sits above 1.5 T.  The records now carry the
full multi-frequency tables (B15AHV950M 217 points over 15 curves, B10AHV900M
406 over 12, 20RSW175 505 over 23), so the surface can be asked directly instead
of being compressed into three numbers first.

The scheme
----------
Log-log, and C1 in both directions so the optimizer sees no fake gradients:

1. **In B, at each measured frequency** — PCHIP (Fritsch–Carlson monotone cubic
   Hermite) through (ln B, ln P).  It INTERPOLATES every measured point exactly,
   is C1, and cannot overshoot between points the way a natural cubic spline
   does.  Outside a curve's own measured B range it continues as a straight line
   in log-log, i.e. a power law P ∝ B^n with n the slope at the last point —
   which sends P → 0 as B → 0 and continues the observed saturation upturn
   upward.
2. **Across f** — PCHIP again, over (ln f_i, ln P_i(B)).  PCHIP slopes are
   LOCAL (node i uses only i−1, i, i+1), so the value on [f_i, f_{i+1}] needs
   only the four curves i−1 … i+2; evaluating just those is identical to the
   global PCHIP over all frequency nodes and keeps the cost independent of how
   many curves a record carries.  Beyond the first/last measured frequency the
   continuation is again a straight line in log-log with the end slope.

At every measured anchor (B_j, f_i) the surface therefore returns the
manufacturer's own number to floating-point precision, by construction.

The envelope, and what happens outside it
-----------------------------------------
The tables are a STAIRCASE, not a rectangle: a 0.15 mm steel is measured to
1.886 T at 50 Hz but only to 1.597 T at 1 kHz and 0.531 T at 10 kHz — the test
rig cannot push saturated flux through a thin lamination at 10 kHz.  So the
measured envelope is B ≤ B_env(f), with B_env interpolated (PCHIP, log-log)
through each curve's own highest measured induction.

Outside it — B above B_env(f), or f outside [f_min, f_max] of the table — the
answer is BLENDED to the Bertotti extrapolation rather than switched to it::

    ln P = (1 − s)·ln P_surface + s·ln P_bertotti,   s = 3u² − 2u³

with u the distance outside, 0 at the boundary and 1 at the far edge of the
blend band (a factor 1.15 in B, an octave in f).  ``s`` and ``ds/du`` are both
zero at u = 0, so the value and its first derivative are continuous across the
boundary: no jump, no kink, nothing for a gradient-based optimizer to trip on.
Inside the envelope the answer is the measured surface exactly (s ≡ 0).

Below the lowest measured induction (0.1 T on every record here) the surface's
own power-law continuation is used WITHOUT blending: it is well-behaved, it goes
to zero at zero induction, and Bertotti has no better claim down there.  That
region is not reported as an excursion.

Activation
----------
Per record, by ``core_loss_model: measured_surface`` in materials_library.yaml.
A steel that does not say so keeps the Bertotti path untouched even if it
carries curves — the surface is only claimed for records whose tables have been
audited against the datasheet's guaranteed anchors.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

log = logging.getLogger(__name__)

# The opt-in value of the record's ``core_loss_model`` field.
SURFACE_MODEL = "measured_surface"

# Blend band.  B: the weight reaches 1 (pure Bertotti) at 1.35·B_env(f).
# f: one octave beyond the table's edge.
#
# The width in B is set by MONOTONICITY, not by taste.  Handing over from a
# curve that reads high to one that reads low costs slope: the blended log-loss
# carries a term (ln P_bert − ln P_surf)·ds/dlnB, which is negative, and if the
# band is narrow enough that ds/dlnB is large it overwhelms the two curves' own
# positive slopes and the loss DIPS with rising induction.  Measured on
# 20RSW175 at 933 Hz (its table stops at 1.5 T, where the gap to the fit is
# 16 %): a 1.15 band dips between 1.62 and 1.68 T.  1.25 is already clean on
# all three records over 20 Hz-40 kHz and 0.02-2.6 T; 1.35 is that with margin.
# It is not a tuning knob — moving it over 1.15-1.45 changes the 150 mm core
# loss by 0.9 % total.
B_BLEND_FACTOR = 1.35
F_BLEND_OCTAVES = 1.0

# A record needs at least this much table to be interpolated as a surface.
MIN_FREQ_CURVES = 3
MIN_POINTS_PER_CURVE = 3

# Below this induction an element contributes nothing worth an interpolation.
B_FLOOR_T = 1e-6


# ---------------------------------------------------------------------------
# PCHIP pieces, vectorised over the QUERY array rather than the node index
# ---------------------------------------------------------------------------
def _pchip_edge_slope(h0: float, h1: float, d0: np.ndarray, d1: np.ndarray
                      ) -> np.ndarray:
    """One-sided end derivative — the same three-point rule SciPy's PCHIP uses.

    ``d0``/``d1`` are the first two secants (arrays over the query points).
    Shape-preserving corrections: never point against the first secant, and
    never exceed three times it when the curve turns.
    """
    d = ((2.0 * h0 + h1) * d0 - h0 * d1) / (h0 + h1)
    d = np.where(np.sign(d) != np.sign(d0), 0.0, d)
    turn = (np.sign(d0) != np.sign(d1)) & (np.abs(d) > 3.0 * np.abs(d0))
    return np.where(turn, 3.0 * d0, d)


def _pchip_interior_slope(h_prev: float, h_next: float,
                          d_prev: np.ndarray, d_next: np.ndarray) -> np.ndarray:
    """Fritsch–Carlson weighted harmonic mean of the two neighbouring secants.

    Zero at a local extremum (secants of opposite sign), which is what makes
    PCHIP monotone and overshoot-free.
    """
    same = (d_prev * d_next) > 0.0
    w1 = 2.0 * h_next + h_prev
    w2 = h_next + 2.0 * h_prev
    safe_p = np.where(same, d_prev, 1.0)
    safe_n = np.where(same, d_next, 1.0)
    return np.where(same, (w1 + w2) / (w1 / safe_p + w2 / safe_n), 0.0)


def _hermite(t: float, h: float, y0: np.ndarray, y1: np.ndarray,
             d0: np.ndarray, d1: np.ndarray) -> np.ndarray:
    """Cubic Hermite on a unit-parametrised interval of width ``h``."""
    t2 = t * t
    t3 = t2 * t
    return ((2 * t3 - 3 * t2 + 1) * y0
            + (t3 - 2 * t2 + t) * h * d0
            + (-2 * t3 + 3 * t2) * y1
            + (t3 - t2) * h * d1)


def _loglog_curve(x: np.ndarray, y: np.ndarray):
    """PCHIP through (x, y) with LINEAR end-slope continuation outside.

    Returns a callable on arrays.  SciPy's own extrapolation continues the end
    CUBIC, which off a saturation knee runs away within a few percent of B; the
    straight line in log-log is a power law with the measured end exponent,
    which is the physically defensible continuation.
    """
    from scipy.interpolate import PchipInterpolator

    pch = PchipInterpolator(x, y, extrapolate=False)
    der = pch.derivative()
    x0, x1 = float(x[0]), float(x[-1])
    y0, y1 = float(y[0]), float(y[-1])
    s0, s1 = float(der(x0)), float(der(x1))

    def _f(xq: np.ndarray) -> np.ndarray:
        xq = np.asarray(xq, float)
        out = np.empty(xq.shape, float)
        lo = xq < x0
        hi = xq > x1
        mid = ~(lo | hi)
        if mid.any():
            out[mid] = pch(xq[mid])
        if lo.any():
            out[lo] = y0 + s0 * (xq[lo] - x0)
        if hi.any():
            out[hi] = y1 + s1 * (xq[hi] - x1)
        return out

    return _f


def _smoothstep(u: np.ndarray) -> np.ndarray:
    """3u² − 2u³ on [0, 1], clamped.  Value AND slope are 0 at u = 0 and 1."""
    u = np.clip(u, 0.0, 1.0)
    return u * u * (3.0 - 2.0 * u)


# ---------------------------------------------------------------------------
# The surface
# ---------------------------------------------------------------------------
class MeasuredLossSurface:
    """P(B, f) [W/m³ OF STEEL] interpolated from a record's measured curves.

    ``B`` is the induction IN THE STEEL and ``f`` a SINUSOIDAL excitation
    frequency — the conditions the Epstein / single-sheet measurement behind
    the table was made at.  Turning a machine's real, non-sinusoidal B(t) into
    a sum of such points is the caller's job (see ``simulation.losses``).
    """

    def __init__(self, name: str, freqs_hz: Sequence[float],
                 curves: Sequence[Sequence[Tuple[float, float]]],
                 density: float, unit: str,
                 bertotti: Tuple[float, float, float]):
        order = np.argsort(np.asarray(freqs_hz, float))
        self.name = str(name)
        self.f = np.asarray(freqs_hz, float)[order]
        self.lnf = np.log(self.f)
        self.kh, self.kc, self.ke = (float(bertotti[0]), float(bertotti[1]),
                                     float(bertotti[2]))
        # The tables are W/kg on every record shipped here; kh/kc/ke are W/m³.
        # One conversion, at ingest, so nothing downstream has to remember.
        scale = float(density) if str(unit) == "w_per_kg" else 1.0
        self._eval: List[Any] = []
        b_max: List[float] = []
        b_min: List[float] = []
        for i in order:
            pts = sorted((float(b), float(p)) for b, p in curves[i]
                         if float(b) > 0 and float(p) > 0)
            b = np.array([q[0] for q in pts])
            p = np.array([q[1] for q in pts]) * scale
            self._eval.append(_loglog_curve(np.log(b), np.log(p)))
            b_min.append(float(b[0]))
            b_max.append(float(b[-1]))
        self.b_max = np.asarray(b_max)
        self.b_min = np.asarray(b_min)
        self.n_points = int(sum(len(c) for c in curves))
        # The measured envelope in B, as a function of f.  PCHIP through each
        # curve's own top point — NOT a running minimum, so every measured
        # anchor sits ON or inside the boundary and is returned exactly.
        self._ln_benv = _loglog_curve(self.lnf, np.log(self.b_max))
        self._ln_b_blend = math.log(B_BLEND_FACTOR)
        self._ln_f_blend = F_BLEND_OCTAVES * math.log(2.0)

    # -- geometry of the measured region ----------------------------------
    def envelope_b(self, f_hz: float) -> float:
        """Highest MEASURED induction at this frequency [T]."""
        lnf = math.log(max(float(f_hz), 1e-9))
        lnf = min(max(lnf, self.lnf[0]), self.lnf[-1])   # clamp, do not run out
        return float(np.exp(self._ln_benv(np.array([lnf]))[0]))

    def outsideness(self, B: np.ndarray, f_hz: float) -> np.ndarray:
        """0 inside the measured envelope, 1 at the far edge of the blend band."""
        # 1e-12 of slack so a point sitting exactly ON the boundary (every
        # top-of-curve measured anchor does) reads as inside rather than as a
        # rounding-width excursion.
        u_b = np.maximum(0.0, (np.log(np.maximum(B, B_FLOOR_T))
                               - math.log(self.envelope_b(f_hz)) - 1e-12)
                         ) / self._ln_b_blend
        lnf = math.log(max(float(f_hz), 1e-9))
        u_f = max(0.0, max(lnf - self.lnf[-1], self.lnf[0] - lnf)) / self._ln_f_blend
        return np.minimum(1.0, np.hypot(u_b, u_f))

    # -- the interpolation ------------------------------------------------
    def _ln_surface(self, lnB: np.ndarray, f_hz: float) -> np.ndarray:
        """ln P from the measured curves alone (no blend), for a SCALAR f."""
        lnf = math.log(max(float(f_hz), 1e-9))
        n = self.f.size
        if n == 1:
            return self._eval[0](lnB)
        i = int(np.clip(np.searchsorted(self.lnf, lnf) - 1, 0, n - 2))
        lo = max(0, i - 1)
        hi = min(n - 1, i + 2)
        vals = {j: self._eval[j](lnB) for j in range(lo, hi + 1)}
        h = np.diff(self.lnf)
        sec = {j: (vals[j + 1] - vals[j]) / h[j] for j in range(lo, hi)}

        def _slope(j: int) -> np.ndarray:
            if 0 < j < n - 1:
                return _pchip_interior_slope(h[j - 1], h[j], sec[j - 1], sec[j])
            if j == 0:
                return _pchip_edge_slope(h[0], h[1], sec[0], sec[1]) if n > 2 \
                    else sec[0]
            return _pchip_edge_slope(h[n - 2], h[n - 3], sec[n - 2], sec[n - 3]) \
                if n > 2 else sec[0]

        d_i, d_j = _slope(i), _slope(i + 1)
        if lnf < self.lnf[0]:                       # power-law continuation
            return vals[0] + d_i * (lnf - self.lnf[0])
        if lnf > self.lnf[-1]:
            return vals[n - 1] + d_j * (lnf - self.lnf[-1])
        return _hermite((lnf - self.lnf[i]) / h[i], h[i],
                        vals[i], vals[i + 1], d_i, d_j)

    def bertotti_w_per_m3(self, B: np.ndarray, f_hz: float) -> np.ndarray:
        """The three-coefficient form, at the SAME (B, f) — the fallback."""
        f = float(f_hz)
        return (self.kh * f * B ** 2 + self.kc * f ** 2 * B ** 2
                + self.ke * f ** 1.5 * np.power(np.maximum(B, 0.0), 1.5))

    def w_per_m3(self, B: np.ndarray, f_hz: float,
                 excursion: Optional[dict] = None,
                 weight: Optional[np.ndarray] = None) -> np.ndarray:
        """Sinusoidal loss density [W/m³ of steel] at peak induction ``B``.

        ``excursion``, when a dict is passed, accumulates what left the measured
        envelope so ONE line can be logged per solve instead of one per element.
        It is accounted in WATTS, not in volume: ``w_out`` is the part of the
        answer that came from the Bertotti extrapolation (each point's watts
        times its blend weight), ``w_all`` the total.  Volume-weighting would
        report an alarming fraction for a machine whose 20 kHz harmonics are
        outside the table at 8 mT and contribute nothing.
        """
        B = np.asarray(B, float)
        out = np.zeros(B.shape, float)
        live = B > B_FLOOR_T
        if not live.any():
            return out
        Bl = B[live]
        lnS = self._ln_surface(np.log(Bl), f_hz)
        u = self.outsideness(Bl, f_hz)
        s = _smoothstep(u)
        P_b = self.bertotti_w_per_m3(Bl, f_hz)
        # Blend in the LOG, so the result is positive by construction and the
        # blend is geometric (a ratio, not a difference) — the two models can
        # differ by tens of percent at the boundary and this cannot produce a
        # negative or a spike between them.
        usable = (s > 0.0) & (P_b > 0.0)
        lnP = lnS.copy()
        if usable.any():
            lnP[usable] = ((1.0 - s[usable]) * lnS[usable]
                           + s[usable] * np.log(P_b[usable]))
        out[live] = np.exp(lnP)

        if excursion is not None:
            w = (np.ones(B.shape) if weight is None
                 else np.asarray(weight, float) * np.ones(B.shape))
            watts = out[live] * w[live]
            blended = watts * s
            excursion["w_all"] = excursion.get("w_all", 0.0) + float(watts.sum())
            excursion["w_out"] = excursion.get("w_out", 0.0) + float(blended.sum())
            if blended.size and float(blended.max()) > excursion.get("w_worst", 0.0):
                k = int(np.argmax(blended))
                excursion.update({
                    "w_worst": float(blended[k]), "u": float(u[k]),
                    "B": float(Bl[k]), "f": float(f_hz),
                    "B_env": self.envelope_b(f_hz),
                    "f_lo": float(self.f[0]), "f_hi": float(self.f[-1])})
        return out


# ---------------------------------------------------------------------------
# Per-record activation + cache
# ---------------------------------------------------------------------------
_cache: Dict[tuple, Optional[MeasuredLossSurface]] = {}


def clear_cache() -> None:
    """Drop every built surface — the library file changed underneath us."""
    _cache.clear()


def wants_surface(steel: Any) -> bool:
    """Does this RECORD opt in to direct P(B, f) interpolation?

    Opt-in, per record, not "has curves": several library steels carry measured
    tables whose provenance has not been audited against a datasheet, and the
    Bertotti path they are validated on must not move under them.
    """
    return (steel is not None
            and str(getattr(steel, "core_loss_model", "")) == SURFACE_MODEL)


def get_surface(steel: Any, bertotti: Tuple[float, float, float]
                ) -> Optional[MeasuredLossSurface]:
    """The record's measured surface, or ``None`` to stay on Bertotti.

    ``bertotti`` is the (k_h, k_c, k_e) the caller would otherwise use — the
    surface carries it for the out-of-envelope blend, so the two models can
    never disagree about what the fallback is.
    """
    if not wants_surface(steel):
        return None
    curves = dict(getattr(steel, "core_loss_curves", {}) or {})
    key = (str(getattr(steel, "name", "?")), len(curves),
           sum(len(c) for c in curves.values()),
           round(float(getattr(steel, "density", 0.0)), 6),
           round(bertotti[0], 9), round(bertotti[1], 12), round(bertotti[2], 9))
    if key in _cache:
        return _cache[key]

    freqs: List[float] = []
    data: List[Sequence[Tuple[float, float]]] = []
    for fk, curve in curves.items():
        try:
            f = float(str(fk).lower().replace("hz", ""))
        except ValueError:
            continue
        pts = [(float(b), float(p)) for b, p in curve
               if float(b) > 0.0 and float(p) > 0.0]
        if f > 0 and len(pts) >= MIN_POINTS_PER_CURVE:
            freqs.append(f)
            data.append(pts)
    if len(freqs) < MIN_FREQ_CURVES:
        log.warning(
            "core loss | %s declares core_loss_model: %s but carries only %d "
            "usable frequency curve(s) (need %d) — falling back to the "
            "three-coefficient Bertotti fit",
            getattr(steel, "name", "?"), SURFACE_MODEL, len(freqs),
            MIN_FREQ_CURVES)
        _cache[key] = None
        return None
    surf = MeasuredLossSurface(
        getattr(steel, "name", "?"), freqs, data,
        float(getattr(steel, "density", 7650.0)),
        str(getattr(steel, "core_loss_curve_unit", "w_per_kg")), bertotti)
    _cache[key] = surf
    return surf
