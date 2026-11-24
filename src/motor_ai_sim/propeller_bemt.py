"""Blade-element momentum (BEMT) model of a propeller — stage A2 of docs/PROPELLER_MODULE_PLAN_2026-11-15.md.

OWN KERNEL.  The algorithm is the textbook one (blade element forces equated, annulus by annulus, to axial and
angular momentum with Prandtl tip and hub loss, solved as one scalar equation in the inflow angle with a bracketed
root, after the residual form of Ning, Wind Energy 2014).  No third-party aerodynamics code is used or linked.

MODEL ``bemt-1``
================
Annulus at radius r, B blades, chord c, geometric pitch angle theta (twist), rotation speed Om, free-stream V along the
axis.  Unknowns: axial induced velocity u_a and swirl u_t at the disc::

    Ua = V + u_a                  axial velocity through the blade
    Ut = Om r - u_t               tangential velocity relative to the blade
    phi = atan2(Ua, Ut),  alpha = theta - phi,  W^2 = Ua^2 + Ut^2,  Re = rho W c / mu
    cn = cl cos(phi) - cd sin(phi),   ct = cl sin(phi) + cd cos(phi)
    dT/dr = 1/2 rho W^2 B c cn          dQ/dr = 1/2 rho W^2 B c ct r        (blade element)
    dT/dr = 4 pi rho r F Ua u_a         dQ/dr = 4 pi rho r^2 F Ua u_t       (momentum, F = Prandtl tip x hub)

Eliminating u_a, u_t for a given phi leaves ONE equation ``(1 - Ka) Ua - V = 0`` with
``Ka = sigma' cn / sin^2 phi``, ``Kt = sigma' ct / sin^2 phi``, ``sigma' = B c / (8 pi r F)``,
``Ut = Om r / (1 + Kt tan phi)``, ``Ua = Ut tan phi``; its first sign change over phi in (0, pi/2) is taken.
It holds for V = 0 (hover) and for climb / axial forward flight.

WHAT IT DOES NOT DO (reported as flags, never hidden)
=====================================================
* no flow at the hub: the blade carries no load inside the root cut-out and this model has NO information there
  (``air_speed_ms`` is ``None`` for a band inside it);
* no wake contraction, no non-axial inflow, no compressibility correction (flag ``high_tip_mach`` above Mtip 0.6),
  no 3-D stall delay, no unsteady effects, no duct;
* the section polars decide the accuracy.  Provided here: ``FlatPlatePolar`` (an ESTIMATE: thin-airfoil lift,
  flat-plate friction drag and a Viterna post-stall extension — NOT a measured airfoil; every result using it carries
  the flag ``polar_estimated``) and ``TablePolar`` (tabulated cl/cd(alpha) per airfoil and Reynolds number from CSV
  files, Viterna beyond the table).  No airfoil data file ships with this stage.  Accuracy of this code on real
  propellers is NOT yet established (validation is stage A3).

SOURCES: Prandtl loss and the BEM equations — standard (Glauert 1935; Ning 2014); post-stall extension — Viterna and
Janetzke, NASA TM-82944 (1982), A1 = B1/2, A2 = (cl_s - B1 sin a_s cos a_s) sin a_s / cos^2 a_s,
B2 = (cd_s - B1 sin^2 a_s) / cos a_s, cdmax = 1.11 + 0.018 AR.
"""
from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from motor_ai_sim import propeller as _pp
from motor_ai_sim import propeller_inflow as _pi
from motor_ai_sim.propeller_blade import BladeGeometry

MODEL_BEMT = "bemt-1"

GAMMA_AIR = 1.4
HIGH_TIP_MACH = 0.6
DEFAULT_THICKNESS = 0.08             # used (and flagged) where a station states none
DEFAULT_N_ELEMENTS = 60
MAX_N_ELEMENTS = 2000
#: stall angle of the ESTIMATED flat-plate polar [deg] — an assumption, not data
FLATPLATE_ALPHA_STALL_DEG = 12.0
_PHI_SCAN = 200
_PHI_LO, _PHI_HI = 1e-4, math.pi / 2.0 - 1e-4
_F_MIN = 1e-6
_W_PASSES = 8                        # review 2026-11-24 (3a): W, Re and the polar converged together
_W_RTOL = 1e-6
#: below this share of a requested air band covered by the modelled blade the result carries ``band_low_coverage:x`` (review 2026-11-24, 1b);
#: the uncovered open part counts at the free-stream speed, so the value itself is already conservative
BAND_MIN_COVERAGE = 0.5

POLAR_DIR = Path(__file__).resolve().parents[2] / "config" / "propellers" / "_polars"
MFOIL_POLAR_DIR = Path(__file__).resolve().parents[2] / "config" / "propellers" / "_polars_mfoil"   # viscous model tables (mfoil, MIT), 2026-11-19


class BemtError(_pi.InflowModelError):
    """The blade-element model cannot answer this request."""


def air_viscosity(temp_c: float) -> float:
    """Dynamic viscosity of air [Pa s], Sutherland's law."""
    T = float(temp_c) + 273.15
    return 1.458e-6 * T ** 1.5 / (T + 110.4)


def sound_speed(temp_c: float) -> float:
    return math.sqrt(GAMMA_AIR * _pp.R_AIR * (float(temp_c) + 273.15))


# ---------------------------------------------------------------------------
# Polars
# ---------------------------------------------------------------------------

def _viterna(alpha: np.ndarray, a_s: float, cl_s: float, cd_s: float, cdmax: float) -> Tuple[np.ndarray, np.ndarray]:
    """Viterna-Corrigan post-stall cl, cd for alpha in [a_s, pi/2] (arrays)."""
    B1 = cdmax
    A1 = B1 / 2.0
    A2 = (cl_s - B1 * math.sin(a_s) * math.cos(a_s)) * math.sin(a_s) / math.cos(a_s) ** 2
    B2 = (cd_s - B1 * math.sin(a_s) ** 2) / math.cos(a_s)
    sa, ca = np.sin(alpha), np.cos(alpha)
    sa = np.where(np.abs(sa) < 1e-12, 1e-12, sa)
    return A1 * np.sin(2.0 * alpha) + A2 * ca ** 2 / sa, B1 * sa ** 2 + B2 * ca


def cd_max(aspect_ratio: float) -> float:
    """Flat-plate drag at 90 deg (Viterna): 1.11 + 0.018 AR, AR capped at 50."""
    return 1.11 + 0.018 * min(max(float(aspect_ratio), 1.0), 50.0)


class PolarProvider:
    """``eval(airfoil, alpha, Re, mach, thickness) -> (cl, cd, flags)``; alpha [rad] may be an array.

    The base class maps any angle onto [-pi/2, pi/2] by the flat-plate symmetry (odd lift, even drag) and calls
    ``_eval_half``; flags is a set of short strings.
    """

    estimated = False

    def eval(self, airfoil: str, alpha, Re, mach, thickness):
        a = np.asarray(alpha, dtype=float)
        w = (a + math.pi) % (2.0 * math.pi) - math.pi
        flip = np.where(np.abs(w) > math.pi / 2.0, -1.0, 1.0)
        w = np.where(np.abs(w) > math.pi / 2.0, np.sign(w) * math.pi - w, w)
        cl, cd, flags = self._eval_half(airfoil, w, np.asarray(Re, float), np.asarray(mach, float), thickness)
        return flip * cl, cd, flags

    def _eval_half(self, airfoil, alpha, Re, mach, thickness):               # pragma: no cover
        raise NotImplementedError


class FlatPlatePolar(PolarProvider):
    """ESTIMATED section polar: thin-airfoil lift to a stall angle, turbulent flat-plate friction drag with a
    thickness form factor, Viterna post-stall.  Not a measured airfoil; independent of the airfoil id."""

    estimated = True

    def __init__(self, aspect_ratio: float = 8.0, alpha_stall_deg: float = FLATPLATE_ALPHA_STALL_DEG):
        self.cdmax = cd_max(aspect_ratio)
        self.a_s = math.radians(alpha_stall_deg)

    def cd0(self, Re, thickness):
        Re = np.maximum(np.asarray(Re, float), 1e3)
        cf = 0.455 / np.log10(Re) ** 2.58
        t = float(thickness)
        return 2.0 * cf * (1.0 + 2.0 * t + 60.0 * t ** 4)

    def _eval_half(self, airfoil, alpha, Re, mach, thickness):
        alpha, Re = np.broadcast_arrays(alpha, Re)
        s = np.sign(alpha)
        a = np.abs(alpha)
        cd0 = self.cd0(Re, thickness)
        cl_att, cd_att = 2.0 * math.pi * a, cd0
        cl_s = 2.0 * math.pi * self.a_s
        cl_pst, cd_pst = self._post(a, cd0, cl_s)
        pre = a <= self.a_s
        return s * np.where(pre, cl_att, cl_pst), np.where(pre, cd_att, cd_pst), {"polar_estimated"}

    def _post(self, a, cd0, cl_s):
        # cd_s varies with Re through cd0, so the Viterna constants are built per element
        cd0 = np.broadcast_to(cd0, a.shape)
        B1 = self.cdmax
        sa_s, ca_s = math.sin(self.a_s), math.cos(self.a_s)
        A1 = B1 / 2.0
        A2 = (cl_s - B1 * sa_s * ca_s) * sa_s / ca_s ** 2
        B2 = (cd0 - B1 * sa_s ** 2) / ca_s
        ap = np.maximum(a, self.a_s)
        sa, ca = np.sin(ap), np.cos(ap)
        return A1 * np.sin(2.0 * ap) + A2 * ca ** 2 / sa, B1 * sa ** 2 + B2 * ca


def zero_lift_angle(x, yu, yl) -> float:
    """Thin-airfoil zero-lift angle [rad] of a section from its camber line z = (yu + yl) / 2 (x/c from 0 to 1):
    alpha_0 = -(1/pi) * integral_0^pi dz/dx (cos(theta) - 1) d(theta), x = (1 - cos(theta)) / 2; slope piecewise constant
    between the points, the theta integral exact per segment.  Negative for positive camber (NACA 4412: about -4.2 deg)."""
    x = np.asarray(x, float)
    z = 0.5 * (np.asarray(yu, float) + np.asarray(yl, float))
    th = np.arccos(np.clip(1.0 - 2.0 * x, -1.0, 1.0))
    slope = np.diff(z) / np.maximum(np.diff(x), 1e-12)
    g = np.sin(th) - th
    return float(-np.sum(slope * np.diff(g)) / math.pi)


#: lift ceiling of the ESTIMATED cambered-plate polar (review 2026-11-24 follow-up): 2 pi (alpha - alpha_0) to a 12 deg stall gives
#: cl 1.77 (4412) / 2.0 (6412); cambered 4-6 % sections at Re 1e5-3e5 reach about 1.2-1.5 (Selig et al., Summary of Low-Speed Airfoil
#: Data, vols 1-3, 1995-1997).  1.4 is an assumption inside that range, not data for a given airfoil.
CAMBER_CL_MAX = 1.4


class CamberedPlatePolar(FlatPlatePolar):
    """ESTIMATED section polar with camber: thin-airfoil lift 2 pi (alpha - alpha_0) to the generic stall angle, alpha_0 from thin-airfoil
    theory on the section's own camber line (``propeller_airfoil``: generated NACA 4-digit or a library file).  Lift slope, drag and the
    stall angle are the generic flat-plate ones; only the zero-lift angle depends on the airfoil."""

    estimated = True

    def __init__(self, aspect_ratio: float = 8.0, alpha_stall_deg: float = FLATPLATE_ALPHA_STALL_DEG):
        super().__init__(aspect_ratio, alpha_stall_deg)
        self._a0: Dict[str, float] = {}

    def alpha0(self, airfoil: str) -> float:
        if airfoil not in self._a0:
            from motor_ai_sim import propeller_airfoil as _af
            try:
                e = _af.get_airfoil(airfoil)
            except _pp.PropellerDataError as exc:
                raise BemtError("camber polar: %s" % exc)
            self._a0[airfoil] = zero_lift_angle(e.x, e.yu, e.yl)
        return self._a0[airfoil]

    def _eval_half(self, airfoil, alpha, Re, mach, thickness):
        # review 2026-11-24 (4c): the zero-lift shift moves the attached lift line only; stall stays at +-alpha_stall in the section's own
        # alpha (shifting the whole flat plate by alpha_0 stalled a NACA 6412 at +5.8 deg).  Post-stall: Viterna from the stall point.
        alpha, Re = np.broadcast_arrays(np.asarray(alpha, float), np.asarray(Re, float))
        a0 = self.alpha0(airfoil)
        cd0 = self.cd0(Re, thickness)
        pre = np.abs(alpha) <= self.a_s
        cl_pre = np.clip(2.0 * math.pi * (alpha - a0), -CAMBER_CL_MAX, CAMBER_CL_MAX)       # capped: no optimistic high-alpha lift
        cl_pos, cd_pos = self._post(np.abs(alpha), cd0, min(2.0 * math.pi * (self.a_s - a0), CAMBER_CL_MAX))
        cl_neg, cd_neg = self._post(np.abs(alpha), cd0, min(2.0 * math.pi * (self.a_s + a0), CAMBER_CL_MAX))
        cl = np.where(pre, cl_pre, np.where(alpha > 0, cl_pos, -cl_neg))
        cd = np.where(pre, cd0, np.where(alpha > 0, cd_pos, cd_neg))
        fl = {"polar_estimated", "camber_thin_airfoil"}
        if np.any(np.abs(2.0 * math.pi * (alpha - a0)) > CAMBER_CL_MAX) and np.any(pre):
            fl.add("camber_cl_max_capped")
        return cl, cd, fl


class PrandtlGlauertPolar(PolarProvider):
    """Wraps a polar: lift scaled by 1 / sqrt(1 - M^2) at the element Mach number (opt-in, 2026-11-20).  The correction is held at
    ``M_CAP`` above it (flag ``mach_above_prandtl_glauert``); drag is left as tabulated (no wave drag)."""

    M_CAP = 0.75

    def __init__(self, base: PolarProvider):
        self.base = base
        self.estimated = True

    #: review 2026-11-24 (8d): the correction is for attached flow; it is applied in full up to ATTACHED_DEG of |alpha| and faded linearly
    #: to none at SEPARATED_DEG (continuous, so the momentum root search sees no jump); the flag says when the fade was used
    ATTACHED_DEG, SEPARATED_DEG = 15.0, 25.0

    def eval(self, airfoil: str, alpha, Re, mach, thickness):
        cl, cd, flags = self.base.eval(airfoil, alpha, Re, mach, thickness)
        m = np.asarray(mach, float)
        flags = set(flags) | {"prandtl_glauert"}
        if np.any(m > self.M_CAP):
            flags.add("mach_above_prandtl_glauert")
        a = np.degrees(np.abs(np.asarray(alpha, float)))
        w = np.clip((self.SEPARATED_DEG - a) / (self.SEPARATED_DEG - self.ATTACHED_DEG), 0.0, 1.0)
        if np.any(w < 1.0):
            flags.add("prandtl_glauert_faded_post_stall")
        k = 1.0 / np.sqrt(1.0 - np.minimum(m, self.M_CAP) ** 2)
        return cl * (1.0 + (k - 1.0) * w), cd, flags


@dataclass(frozen=True)
class PolarTable:
    airfoil: str
    re: float
    alpha: np.ndarray            # rad, increasing
    cl: np.ndarray
    cd: np.ndarray
    source: str
    gaps: Tuple[float, ...] = ()     # rad; failed solutions bridged by interpolation (``# kept:`` header of a generated table)


def load_polar_csv(path: Path) -> PolarTable:
    """Read ``# key: value`` header lines (``airfoil``, ``re``, ``source`` required) and ``alpha_deg,cl,cd`` rows."""
    meta: Dict[str, str] = {}
    rows: List[Tuple[float, float, float]] = []
    where = str(path)
    with open(path, newline="", encoding="utf-8") as fh:
        body = []
        for line in fh:
            if line.startswith("#"):
                if ":" in line:
                    k, v = line[1:].split(":", 1)
                    meta[k.strip().lower()] = v.strip()
            elif line.strip():
                body.append(line)
    rd = csv.reader(body)
    head = [h.strip().lower() for h in next(rd, [])]
    if head[:3] != ["alpha_deg", "cl", "cd"]:
        raise BemtError(f"{where}: header must start with alpha_deg,cl,cd")
    for row in rd:
        try:
            rows.append((float(row[0]), float(row[1]), float(row[2])))
        except (ValueError, IndexError):
            raise BemtError(f"{where}: bad row {row!r}")
    for k in ("airfoil", "re", "source"):
        if not meta.get(k):
            raise BemtError(f"{where}: missing header '# {k}: ...'")
    if len(rows) < 5:
        raise BemtError(f"{where}: needs at least 5 rows")
    a = np.array([r[0] for r in rows]) * math.pi / 180.0
    if not np.all(np.diff(a) > 0) or not np.all(np.isfinite(np.array(rows))):
        raise BemtError(f"{where}: alpha must be finite and strictly increasing")
    if a[-1] >= math.pi / 2.0 or a[0] <= -math.pi / 2.0:
        raise BemtError(f"{where}: tabulate within +-90 deg; the post-stall range is extended by the model")
    re = float(meta["re"])
    if not re > 0:
        raise BemtError(f"{where}: re must be positive")
    gaps: Tuple[float, ...] = ()
    kept = meta.get("kept", "")
    if "interpolation):" in kept:                       # review 2026-11-21 H5: keep the failed angles, do not lose them
        txt = kept.split("interpolation):", 1)[1].split(";", 1)[0]
        gaps = tuple(float(g) * math.pi / 180.0 for g in txt.split() if g.lstrip("-").replace(".", "", 1).isdigit())
    return PolarTable(meta["airfoil"], re, a, np.array([r[1] for r in rows]), np.array([r[2] for r in rows]), meta["source"], gaps)


class TablePolar(PolarProvider):
    """Polars from tables: per airfoil, log-Re interpolation between tables (clamped, flagged outside), alpha
    interpolation inside the table, Viterna beyond its ends."""

    def __init__(self, tables: Sequence[PolarTable], aspect_ratio: float = 8.0):
        self.cdmax = cd_max(aspect_ratio)
        self._by: Dict[str, List[PolarTable]] = {}
        for t in tables:
            self._by.setdefault(t.airfoil, []).append(t)
        for v in self._by.values():
            v.sort(key=lambda t: t.re)
        self.estimated = False

    @classmethod
    def from_dir(cls, root: Path, aspect_ratio: float = 8.0) -> "TablePolar":
        files = sorted(Path(root).rglob("*.csv")) if Path(root).is_dir() else []
        return cls([load_polar_csv(f) for f in files if not f.name.startswith("_")], aspect_ratio)

    def airfoils(self) -> List[str]:
        return sorted(self._by)

    def _one(self, t: PolarTable, a: np.ndarray):
        cl = np.interp(a, t.alpha, t.cl)
        cd = np.interp(a, t.alpha, t.cd)
        hi = a > t.alpha[-1]
        if np.any(hi):
            v_cl, v_cd = _viterna(np.maximum(a, t.alpha[-1]), float(t.alpha[-1]), float(t.cl[-1]), float(t.cd[-1]), self.cdmax)
            cl, cd = np.where(hi, v_cl, cl), np.where(hi, v_cd, cd)
        lo = a < t.alpha[0]
        if np.any(lo):
            a_s, cl_s = float(t.alpha[0]), float(t.cl[0])
            if cl_s > 0.0 and len(t.alpha) > 1:
                # review 2026-11-24 (4b): the table ends before zero lift (cl > 0 at its first angle, e.g. naca4-6410 Re 4.5e5).  Extend the
                # first segment's slope linearly to cl = 0 and start the mirrored Viterna branch there, so cl keeps falling below the table.
                slope = (float(t.cl[1]) - cl_s) / (float(t.alpha[1]) - a_s)
                a_z = a_s - cl_s / slope if slope > 0 else a_s
                lin = (a >= a_z) & lo
                cl = np.where(lin, cl_s + slope * (a - a_s), cl)
                cd = np.where(lin, float(t.cd[0]), cd)
                lo = a < a_z
                a_s, cl_s = a_z, 0.0
            if np.any(lo):
                v_cl, v_cd = _viterna(np.maximum(-a, -a_s), -a_s, -cl_s, float(t.cd[0]), self.cdmax)
                cl, cd = np.where(lo, -v_cl, cl), np.where(lo, v_cd, cd)
        return cl, cd

    @staticmethod
    def gap_intervals(t: PolarTable) -> List[Tuple[float, float]]:
        """The open alpha intervals (rad) bridged by interpolation across a failed solution: from the converged angle below each failed
        angle to the converged angle above it (review 2026-11-22 H5: the whole bridge is unsupported, not only the failed angle)."""
        out = []
        for g in t.gaps:
            below, above = t.alpha[t.alpha < g], t.alpha[t.alpha > g]
            if len(below) and len(above):
                out.append((float(below[-1]), float(above[0])))
        return out

    def _table_flags(self, tabs, a) -> set:
        """``polar_alpha_gap`` when an angle falls inside a bridged interval of a table in use (``gap_intervals``);
        ``polar_beyond_table`` when it lies outside the converged range (the Viterna extension, not a computed stall)."""
        f = set()
        aa = np.ravel(a)
        for t in tabs:
            if any(np.any((aa > lo) & (aa < hi)) for lo, hi in self.gap_intervals(t)):
                f.add("polar_alpha_gap")
            if np.any(aa > t.alpha[-1]) or np.any(aa < t.alpha[0]):
                f.add("polar_beyond_table")
        return f

    def _eval_half(self, airfoil, alpha, Re, mach, thickness):
        tabs = self._by.get(airfoil)
        if not tabs:
            raise BemtError(f"no polar table for airfoil {airfoil!r}")
        a = np.asarray(alpha, float)
        flags = set()
        if len(tabs) == 1:
            cl, cd = self._one(tabs[0], a)
            flags.add("single_re_table")
            return cl, cd, flags | self._table_flags(tabs, a)
        logs = np.log([t.re for t in tabs])
        x = np.log(np.maximum(np.asarray(Re, float), 1.0))
        if np.any(x < logs[0]) or np.any(x > logs[-1]):
            flags.add("re_outside_tables")
        x = np.clip(x, logs[0], logs[-1])
        j = np.clip(np.searchsorted(logs, x) - 1, 0, len(tabs) - 2)
        a, x, j = np.broadcast_arrays(a, x, j)
        cl = np.empty(a.shape)
        cd = np.empty(a.shape)
        for k in np.unique(j):
            m = j == k
            flags |= self._table_flags((tabs[k], tabs[k + 1]), a[m])
            c1, d1 = self._one(tabs[k], a[m])
            c2, d2 = self._one(tabs[k + 1], a[m])
            w = (x[m] - logs[k]) / (logs[k + 1] - logs[k])
            cl[m] = (1 - w) * c1 + w * c2
            cd[m] = (1 - w) * d1 + w * d2
        return cl, cd, flags


# ---------------------------------------------------------------------------
# Blade geometry on the element grid
# ---------------------------------------------------------------------------

def _interp_fn(r: np.ndarray, y: np.ndarray, kind: str):
    if kind == "pchip":
        from scipy.interpolate import PchipInterpolator
        return PchipInterpolator(r, y, extrapolate=False)
    return lambda x: np.interp(x, r, y, left=np.nan, right=np.nan)


def element_grid(blade: BladeGeometry, n: int = DEFAULT_N_ELEMENTS):
    """Midpoint grid from the root cut-out (or the first station) to the tip with chord, twist, thickness, airfoil.

    Returns ``(r_mid, dr, chord, theta_rad, thickness, airfoils, flags)``.  Outside the stations the end value is held
    and the flag ``root_extrapolated`` / ``tip_extrapolated`` says so.
    """
    st = blade.stations
    rs = np.array([s.r_m for s in st])
    r0 = max(blade.root_cutout_m, rs[0])
    r1 = blade.tip_radius_m
    edges = np.linspace(r0, r1, n + 1)
    rm = 0.5 * (edges[1:] + edges[:-1])
    dr = np.diff(edges)
    flags = set()
    chord = _clamped(_interp_fn(rs, np.array([s.chord_m for s in st]), blade.interpolation), rm, rs, st, "chord_m")
    twist = _clamped(_interp_fn(rs, np.array([s.twist_deg for s in st]), blade.interpolation), rm, rs, st, "twist_deg")
    if r1 > rs[-1] + 1e-12:
        flags.add("tip_extrapolated")
    if blade.root_cutout_m < rs[0] - 1e-12:
        flags.add("root_not_covered_by_stations")
    ths = [s.thickness_rel for s in st]
    if all(t is None for t in ths):
        thick = np.full(n, DEFAULT_THICKNESS)
        flags.add("thickness_assumed")
    else:
        known = [(s.r_m, s.thickness_rel) for s in st if s.thickness_rel is not None]
        thick = np.interp(rm, [k[0] for k in known], [k[1] for k in known])
    idx = np.argmin(np.abs(rm[:, None] - rs[None, :]), axis=1)
    airfoils = [st[i].airfoil for i in idx]
    return rm, dr, chord, np.radians(twist), thick, airfoils, flags


def _clamped(fn, rm, rs, st, attr):
    y = np.asarray(fn(rm), float)
    lo = np.isnan(y) & (rm < rs[0])
    hi = np.isnan(y) & (rm > rs[-1])
    y[lo] = getattr(st[0], attr)
    y[hi] = getattr(st[-1], attr)
    if np.any(np.isnan(y)):
        raise BemtError("blade interpolation produced NaN")
    return y


# ---------------------------------------------------------------------------
# The kernel
# ---------------------------------------------------------------------------

def _prandtl(B: int, r: float, rhub: float, rtip: float, sphi):
    s = np.maximum(np.abs(sphi), 1e-9)
    ft = B / 2.0 * (rtip - r) / (r * s)
    Ft = 2.0 / math.pi * np.arccos(np.clip(np.exp(-ft), 0.0, 1.0))
    if rhub > 0.0:
        fh = B / 2.0 * (r - rhub) / (rhub * s)
        Fh = 2.0 / math.pi * np.arccos(np.clip(np.exp(-fh), 0.0, 1.0))
    else:
        Fh = 1.0
    return np.maximum(Ft * Fh, _F_MIN)


@dataclass
class _Ctx:
    B: int
    rhub: float
    rtip: float
    Om: float
    V: float
    rho: float
    mu: float
    a_snd: float
    polar: PolarProvider


def _state(phi, r, c, th, af, t, x: _Ctx):
    """Everything at inflow angle(s) ``phi`` for one element: residual and the force/velocity quantities.

    Momentum form (Glauert, Ning 2014): dT = 4 pi rho r F (V + u_a) u_a - the mass-flux factor is the blade-local V + u_a and F
    carries the loss; the azimuthal mean through-flow of the annulus is V + F u_a, which ``solve`` returns as ``axial_mean``."""
    phi = np.asarray(phi, float)
    sphi, cphi = np.sin(phi), np.cos(phi)
    tphi = sphi / cphi
    F = _prandtl(x.B, r, x.rhub, x.rtip, sphi)
    sig = x.B * c / (8.0 * math.pi * r * F)
    W = np.full_like(phi, math.hypot(x.V, x.Om * r))
    flags: set = set()
    for _ in range(_W_PASSES):                             # Re (hence the polar) depends on W: iterate until W is stationary
        Re = x.rho * W * c / x.mu                          # (review 2026-11-24, 3a: was two fixed passes; effect < 0.002 % on P1)
        cl, cd, fl = x.polar.eval(af, th - phi, Re, W / x.a_snd, t)
        flags |= fl
        cn = cl * cphi - cd * sphi
        ct = cl * sphi + cd * cphi
        Ka = sig * cn / sphi ** 2
        Kt = sig * ct / sphi ** 2
        den = 1.0 + Kt * tphi
        Ut = np.where(den > 1e-6, x.Om * r / np.where(den > 1e-6, den, 1.0), np.nan)
        W_new = np.abs(Ut) / cphi
        done = bool(np.all(~np.isfinite(W_new) | (np.abs(W_new - W) <= _W_RTOL * np.maximum(np.abs(W), 1e-9))))
        W = W_new
        if done:
            break
    else:
        flags.add("w_re_not_converged")                   # review 2026-11-24 follow-up: the 8 passes ran out (W and Re still moving)
    Ua = Ut * tphi
    return dict(phi=phi, F=F, Ka=Ka, Kt=Kt, Ua=Ua, Ut=Ut, W=W, Re=Re, cl=cl, cd=cd, cn=cn, ct=ct,
                resid=(1.0 - Ka) * Ua - x.V, flags=flags)


def _solve_element(r, c, th, af, t, x: _Ctx):
    phis = np.linspace(_PHI_LO, _PHI_HI, _PHI_SCAN)
    s = _state(phis, r, c, th, af, t, x)
    R = s["resid"]
    ok = np.isfinite(R)
    idx = None
    n_roots = 0
    for i in range(len(phis) - 1):
        if ok[i] and ok[i + 1] and R[i] < 0.0 <= R[i + 1]:
            n_roots += 1
            if idx is None:
                idx = i
    if idx is None:
        return None, n_roots
    lo, hi = float(phis[idx]), float(phis[idx + 1])
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        rm = float(_state(np.array([mid]), r, c, th, af, t, x)["resid"][0])
        if not math.isfinite(rm):
            break
        if rm < 0.0:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-13:
            break
    phi = 0.5 * (lo + hi)
    return _state(np.array([phi]), r, c, th, af, t, x), n_roots


@dataclass
class BemtResult:
    """Integrated loads and the per-element profile.  Arrays are over the element grid ``r_m``."""

    rpm: float
    rho: float
    v_inf: float
    thrust_N: float
    torque_Nm: float
    power_W: float
    ct: float
    cp: float
    J: Optional[float]
    efficiency: Optional[float]
    figure_of_merit: Optional[float]
    r_m: np.ndarray
    dr_m: np.ndarray
    phi: np.ndarray
    alpha: np.ndarray
    reynolds: np.ndarray
    u_axial: np.ndarray            # induced axial velocity at the disc, local to the blade (vortex sheet)
    u_swirl: np.ndarray            # induced swirl at the disc
    axial_total: np.ndarray        # V + u_axial
    wake_axial: np.ndarray         # V + 2 u_axial (fully developed, no contraction modelled)
    wake_swirl: np.ndarray         # 2 u_swirl
    loss_factor: np.ndarray
    cl: np.ndarray
    cd: np.ndarray
    dT_dr: np.ndarray
    dQ_dr: np.ndarray
    unconverged: List[int]
    flags: Tuple[str, ...]
    polar_estimated: bool
    root_m: float
    tip_m: float
    # review 2026-11-24 (1a): the azimuthal (annulus) means behind the disc - the momentum balance holds for F u_a, not u_a; these are the
    # velocities a cooling or wake consumer must use.  The blade-local arrays above are unchanged.
    axial_mean: Optional[np.ndarray] = None        # V + F u_a
    wake_axial_mean: Optional[np.ndarray] = None   # V + 2 F u_a


def solve(blade: BladeGeometry, blades: int, rpm: float, rho: float, v_inf: float = 0.0, *,
          polar: PolarProvider, temp_c: float = 15.0, n_elements: int = DEFAULT_N_ELEMENTS) -> BemtResult:
    """Blade-element momentum solution for ``blades`` blades of ``blade`` at ``rpm`` in air of density ``rho``."""
    if not (isinstance(blades, int) and blades >= 1):
        raise BemtError("blade count must be a positive integer")
    if not (rpm > 0 and math.isfinite(rpm)):
        raise BemtError("rpm must be positive")
    if not (rho > 0 and math.isfinite(rho)):
        raise BemtError("rho must be positive")
    if not (v_inf >= 0 and math.isfinite(v_inf)):
        raise BemtError("v_inf must be >= 0 (propeller in thrust mode; a windmilling case is out of scope)")
    if not (isinstance(n_elements, int) and 8 <= n_elements <= MAX_N_ELEMENTS):    # review 2026-11-24 (10c): bounded work
        raise BemtError("n_elements must be an integer 8..%d" % MAX_N_ELEMENTS)
    rm, dr, chord, th, thick, afs, gflags = element_grid(blade, n_elements)
    Om = rpm * 2.0 * math.pi / 60.0
    x = _Ctx(B=blades, rhub=blade.hub_radius_m, rtip=blade.tip_radius_m, Om=Om, V=float(v_inf), rho=float(rho),
             mu=air_viscosity(temp_c), a_snd=sound_speed(temp_c), polar=polar)
    n = len(rm)
    keys = ("phi", "alpha", "Re", "ua", "ut", "Ua", "F", "cl", "cd", "dT", "dQ")
    out = {k: np.zeros(n) for k in keys}
    bad: List[int] = []
    flags = set(gflags)
    multi = 0
    for i in range(n):
        s, nroots = _solve_element(float(rm[i]), float(chord[i]), float(th[i]), afs[i], float(thick[i]), x)
        if s is None:
            # review 2026-11-24 (3b): no momentum root.  The element is NOT summed as zero: its loads are the uninduced blade-element
            # estimate (relative speed hypot(V, Om r), no induction), so e.g. a zero-twist tip in hover still carries its profile-drag torque.
            # The element stays in ``unconverged`` and the result says so (flags unconverged_elements:n, uninduced_estimate:n).
            bad.append(i)
            ph = math.atan2(x.V, Om * rm[i]) if x.V > 0 else 0.0
            W0 = math.hypot(x.V, Om * rm[i])
            Re0 = rho * W0 * chord[i] / x.mu
            cl0, cd0, fl0 = polar.eval(afs[i], np.array([th[i] - ph]), np.array([Re0]), np.array([W0 / x.a_snd]), float(thick[i]))
            flags |= set(fl0)
            cl0, cd0 = float(np.asarray(cl0).ravel()[0]), float(np.asarray(cd0).ravel()[0])
            q = 0.5 * rho * W0 * W0 * blades * chord[i]
            out["phi"][i] = ph; out["alpha"][i] = th[i] - ph; out["Re"][i] = Re0; out["Ua"][i] = x.V; out["F"][i] = 1.0
            out["cl"][i] = cl0; out["cd"][i] = cd0
            out["dT"][i] = q * (cl0 * math.cos(ph) - cd0 * math.sin(ph))
            out["dQ"][i] = q * (cl0 * math.sin(ph) + cd0 * math.cos(ph)) * rm[i]
            continue
        multi += 1 if nroots > 1 else 0
        flags |= s["flags"]
        ph = float(s["phi"][0]); Ua = float(s["Ua"][0]); Ut = float(s["Ut"][0]); W = float(s["W"][0])
        cn = float(s["cn"][0]); ctt = float(s["ct"][0])
        out["phi"][i] = ph
        out["alpha"][i] = th[i] - ph
        out["Re"][i] = float(s["Re"][0])
        out["ua"][i] = Ua - x.V
        out["ut"][i] = Om * rm[i] - Ut
        out["Ua"][i] = Ua
        out["F"][i] = float(s["F"][0])
        out["cl"][i] = float(s["cl"][0]); out["cd"][i] = float(s["cd"][0])
        q = 0.5 * rho * W * W * blades * chord[i]
        out["dT"][i] = q * cn
        out["dQ"][i] = q * ctt * rm[i]
    if bad:
        flags.add("unconverged_elements:%d" % len(bad))
        flags.add("uninduced_estimate:%d" % len(bad))
    if multi:
        flags.add("multiple_roots:%d" % multi)
    T = float(np.sum(out["dT"] * dr))
    Q = float(np.sum(out["dQ"] * dr))
    if T < 0:
        flags.add("negative_thrust")                     # review 2026-11-24 (3c): braking / windmilling, outside the propulsive model
    wake_mean = x.V + 2.0 * out["F"] * out["ua"]
    if np.any(wake_mean < 0):
        flags.add("wake_reversal_not_modelled")          # review 2026-11-24 (3d): no high-induction (Buhl) branch in this model
    P = Q * Om
    n_rev = rpm / 60.0
    D = 2.0 * blade.tip_radius_m
    if om_tip_mach(Om, blade.tip_radius_m, x.V, x.a_snd) > HIGH_TIP_MACH:
        flags.add("high_tip_mach")
    eta = (T * x.V / P) if (x.V > 0 and P > 0) else None
    A = math.pi * blade.tip_radius_m ** 2
    fm = (T ** 1.5 / (math.sqrt(2.0 * rho * A) * P)) if (x.V == 0 and T > 0 and P > 0) else None
    return BemtResult(
        rpm=float(rpm), rho=float(rho), v_inf=x.V, thrust_N=T, torque_Nm=Q, power_W=P,
        ct=T / (rho * n_rev ** 2 * D ** 4), cp=P / (rho * n_rev ** 3 * D ** 5),
        J=(x.V / (n_rev * D)), efficiency=eta, figure_of_merit=fm,
        r_m=rm, dr_m=dr, phi=out["phi"], alpha=out["alpha"], reynolds=out["Re"], u_axial=out["ua"], u_swirl=out["ut"],
        axial_total=out["Ua"], wake_axial=x.V + 2.0 * out["ua"], wake_swirl=2.0 * out["ut"], loss_factor=out["F"],
        cl=out["cl"], cd=out["cd"], dT_dr=out["dT"], dQ_dr=out["dQ"], unconverged=bad,
        flags=tuple(sorted(flags)), polar_estimated=bool(polar.estimated), root_m=float(rm[0] - dr[0] / 2.0),
        tip_m=blade.tip_radius_m, axial_mean=x.V + out["F"] * out["ua"], wake_axial_mean=wake_mean)


def om_tip_mach(Om: float, rtip: float, V: float, a: float) -> float:
    return math.hypot(Om * rtip, V) / a


# ---------------------------------------------------------------------------
# The inflow model (registered as "bemt-1") and helpers for the catalogue
# ---------------------------------------------------------------------------

def polar_for(prop: _pp.Propeller, polar_dir: Optional[Path] = None) -> PolarProvider:
    """The polar provider a propeller-2 entry asks for (``blade.polars.source``, wrapped by ``blade.polars.compressibility``)."""
    base = _polar_base(prop, polar_dir)
    if getattr(prop.blade, "polars_compressibility", None) == "prandtl_glauert":
        return PrandtlGlauertPolar(base)
    return base


def _polar_base(prop: _pp.Propeller, polar_dir: Optional[Path] = None) -> PolarProvider:
    b = prop.blade
    src = b.polars_source or "flatplate_model"
    ar = max((b.tip_radius_m - b.root_cutout_m) / max(float(np.mean([s.chord_m for s in b.stations])), 1e-9), 1.0)
    if src == "flatplate_model":
        return FlatPlatePolar(ar)
    if src == "camber_model":
        return CamberedPlatePolar(ar)
    if src in ("xfoil_table", "measured", "mfoil_table"):
        root = polar_dir or (MFOIL_POLAR_DIR if src == "mfoil_table" else POLAR_DIR)
        tp = TablePolar.from_dir(root, ar)
        missing = sorted({s.airfoil for s in b.stations} - set(tp.airfoils()))
        if missing:
            raise BemtError("no polar table for airfoil(s) %s under %s" % (", ".join(missing), root))
        if src == "mfoil_table":
            tp.estimated = True                            # a viscous model (panel + integral boundary layer), not a measurement
        return tp
    raise BemtError("polar source %r is not available in this build (this stage ships the table / Viterna "
                    "providers only; a trained-surrogate provider needs its weights' licence verified first)" % src)


def evaluate(prop: _pp.Propeller, rpm: float, rho: float = _pp.RHO_ISA, v_inf: float = 0.0, *,
             temp_c: float = 15.0, polar: Optional[PolarProvider] = None, n_elements: int = DEFAULT_N_ELEMENTS) -> Dict[str, Any]:
    """Stable evaluation record for optimisers and reports (docs plan section 6C-4/5)."""
    if not prop.bemt_ready:
        raise BemtError("propeller %r has no complete blade geometry (propeller-2 `blade` block and blade count)" % prop.id)
    res = solve(prop.blade, prop.blades, rpm, rho, v_inf, polar=polar or polar_for(prop), temp_c=temp_c, n_elements=n_elements)
    return {
        "model_id": MODEL_BEMT, "propeller_id": prop.id,
        "operating_point": {"rpm": res.rpm, "rho_kg_m3": res.rho, "v_inf_ms": res.v_inf, "air_temp_c": float(temp_c)},
        "thrust_N": res.thrust_N, "torque_Nm": res.torque_Nm, "shaft_power_W": res.power_W,
        "ct": res.ct, "cp": res.cp, "J": res.J, "efficiency": res.efficiency, "figure_of_merit": res.figure_of_merit,
        "profile": {"r_m": res.r_m.tolist(), "axial_total_ms": res.axial_total.tolist(), "axial_mean_ms": res.axial_mean.tolist(),
                    "swirl_ms": res.u_swirl.tolist(), "wake_axial_ms": res.wake_axial.tolist(),
                    "wake_swirl_ms": res.wake_swirl.tolist()},
        "flags": list(res.flags), "polar_estimated": res.polar_estimated,
        "geometry_source": prop.blade.geometry_source, "geometry_estimated": prop.blade.estimated,
        "estimated": bool(res.polar_estimated or prop.blade.estimated), "validated_against": None,
    }


def design_variables(prop: _pp.Propeller, fractions: Sequence[float] = (0.25, 0.4, 0.6, 0.8, 1.0)) -> List[Dict[str, Any]]:
    """The design-variable set of a blade (plan 6C-3): chord and twist at radial control points, diameter, hub
    radius, blade count — each ``{name, unit, value, lo, hi}``.  ``lo`` / ``hi`` come ONLY from an optional
    ``blade.design_bounds`` mapping in the catalogue file (manufacturing limits are not invented here); otherwise
    ``None``."""
    if not prop.bemt_ready:
        raise BemtError("propeller %r has no complete blade geometry" % prop.id)
    b = prop.blade
    bounds = (prop.raw.get("blade") or {}).get("design_bounds") or {}
    rs = np.array([s.r_m for s in b.stations])
    fn_c = _interp_fn(rs, np.array([s.chord_m for s in b.stations]), b.interpolation)
    fn_t = _interp_fn(rs, np.array([s.twist_deg for s in b.stations]), b.interpolation)
    out: List[Dict[str, Any]] = []

    def add(name, unit, value):
        lo_hi = bounds.get(name) or {}
        out.append({"name": name, "unit": unit, "value": float(value), "lo": lo_hi.get("lo"), "hi": lo_hi.get("hi")})

    tip = b.tip_radius_m
    for f in fractions:
        r = float(min(max(f * tip, rs[0]), rs[-1]))
        add("chord_mm@r/R=%g" % f, "mm", float(fn_c(r)) * 1000.0)
        add("twist_deg@r/R=%g" % f, "deg", float(fn_t(r)))
    add("diameter_mm", "mm", 2.0 * tip * 1000.0)
    add("hub_radius_mm", "mm", b.hub_radius_m * 1000.0)
    add("blades", "-", prop.blades)
    return out


class BemtInflow:
    """Blade-element model ``bemt-1`` behind the A0 inflow interface (static or axial flight)."""

    model_id = MODEL_BEMT

    def evaluate(self, req: _pi.InflowRequest) -> _pi.AirField:
        prop = req.propeller
        if not prop.bemt_ready:
            raise _pi.InflowModelError("%s needs a propeller-2 entry with a complete blade geometry; %r has none"
                                       % (MODEL_BEMT, prop.id))
        temp = 15.0 if req.air_temp_c is None else float(req.air_temp_c)
        try:
            res = solve(prop.blade, prop.blades, req.rpm, req.rho, req.v_inf_ms, polar=polar_for(prop), temp_c=temp)
        except BemtError as exc:
            raise _pi.InflowModelError(str(exc))
        edges = np.concatenate([res.r_m - res.dr_m / 2.0, [res.r_m[-1] + res.dr_m[-1] / 2.0]])
        ok = np.ones(len(res.r_m), bool)
        ok[res.unconverged] = False
        # review 2026-11-24 (1a): bands, air speed and wake carry the ANNULUS-MEAN axial speed V + F u_a (blade-local V + u_a over-states
        # it where the hub / tip loss F is small); elements without a momentum solution give no band value.
        bands = tuple(_pi.Band(float(edges[i]), float(edges[i + 1]),
                               float(res.axial_mean[i]) if ok[i] else None,
                               float(res.u_swirl[i]) if ok[i] else None) for i in range(len(res.r_m)))
        flags = set(res.flags) | {"profile_at_disc", "no_wake_contraction"}
        lo = res.root_m if req.r_min_m is None else float(req.r_min_m)
        hi = res.tip_m if req.r_max_m is None else float(req.r_max_m)
        a_lo, a_hi = max(lo, res.root_m), min(hi, res.tip_m)
        r_hub = float(prop.blade.hub_radius_m)
        open_lo = max(lo, r_hub, 0.0)                      # the hub itself is solid: no air passes inside it
        open_area = np.pi * (hi ** 2 - open_lo ** 2) if hi > open_lo else 0.0
        if open_area > 0:
            # review 2026-11-24 (1a, 1b): exact clipped annulus areas; the open part of the band that the blade model does not cover (the
            # gap between hub and cut-out, failed elements) is taken at the free-stream speed V - no induced-flow credit - so a thin
            # loaded sliver cannot stand for a band that is mostly unmodelled, and the value has no jump when the cut-out passes the band edge
            e_lo, e_hi = np.maximum(edges[:-1], max(a_lo, open_lo)), np.minimum(edges[1:], a_hi)
            w = np.where(e_hi > e_lo, np.pi * (e_hi ** 2 - e_lo ** 2), 0.0) * ok
            covered = float(w.sum())
            coverage = covered / open_area
            air = float((np.sum(w * res.axial_mean) + (open_area - covered) * res.v_inf) / open_area)
            if lo < res.root_m - 1e-12:
                flags.add("band_partly_inside_hub")
            if coverage < BAND_MIN_COVERAGE:
                flags.add("band_low_coverage:%.2f" % coverage)
        else:
            air = None
            flags.add("band_inside_hub_not_modelled")
        area = np.pi * (edges[1:] ** 2 - edges[:-1] ** 2) * ok
        if area.sum() > 0:
            wake = float(np.sum(area * res.wake_axial_mean) / area.sum())
        else:                                              # review 2026-11-24 (11.6): no converged element is no wake, not a zero wake
            raise _pi.InflowModelError("%s: no blade element converged at %.0f rpm" % (MODEL_BEMT, res.rpm))
        assumptions = ("blade-element momentum theory with Prandtl tip and hub loss, no wake contraction, no hub flow",
                       "polars: %s" % ("ESTIMATED flat-plate + Viterna model, not a measured airfoil" if res.polar_estimated
                                       else "tabulated section data, Viterna beyond the table"),
                       "validation: tests/test_propeller_validation_uiuc.py against UIUC wind-tunnel data (APC 11x5.5E, airfoil "
                       "assumed); bias at J 0.2-0.35: mfoil tables CT -2 %, CP -7 %, efficiency +0.03; flat-plate model CT -38 %, "
                       "CP -44 %, efficiency +0.06; static CT 20-30 % low with both - hover numbers are not usable")
        return _pi.AirField(
            model_id=MODEL_BEMT, propeller_id=prop.id, rpm=res.rpm, rho_kg_m3=res.rho, thrust_N=res.thrust_N,
            torque_Nm=res.torque_Nm, shaft_power_W=res.power_W, wake_velocity_ms=wake, air_speed_ms=air,
            bands=bands, flags=tuple(sorted(flags)), assumptions=assumptions,
            extra={"ct": res.ct, "cp": res.cp, "J": res.J, "efficiency": res.efficiency,
                   "figure_of_merit": res.figure_of_merit})


_pi.register_model(BemtInflow())
