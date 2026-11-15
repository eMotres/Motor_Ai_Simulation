"""Airfoil library and manufacturability of the own propeller — stage P0 of docs/PROPELLER_MODULE_PLAN_2026-11-15.md (section 9).

The designer works on the airfoil AS MANUFACTURED, never on the ideal coordinates.  This module holds:

* the airfoil shape source: coordinates in Selig format, or the published NACA 4-digit formula (no data licence involved);
* the terms record of each airfoil family (``terms_status``): a shape from a file is offered for commercial use only when its
  family's terms are recorded as ``permitted`` and confirmed by a person;
* the manufacturing profile (``blade.manufacturing`` of a propeller-2 file): the limits a mould shop states, each with its
  source and who confirmed it.  A limit without a value is not applied; a limit with a value but no confirmation IS applied
  and the result carries the flag ``manufacturing_limits_unconfirmed`` — no default number is shipped;
* the manufactured section (thickened trailing edge, minimum thickness, minimum leading-edge radius) with a report of which
  limit is active, and blade-level checks (twist the mould can release, thickness along the blade);
* the tolerance perturbations for the robustness evaluation.

Pure numpy, no I/O except ``load_library``.
"""
from __future__ import annotations

import itertools
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import yaml

from motor_ai_sim.propeller import PropellerDataError, seeded_config_path

AIRFOIL_DIR = seeded_config_path("propellers", "_airfoils")

#: the limits a manufacturing profile may state; name -> unit.  Order = order of application in ``manufactured_section``.
LIMIT_UNITS: Dict[str, str] = {
    "wall_min_mm": "mm",              # absolute minimum blade thickness (process wall limit)
    "thickness_ratio_min": "-",       # minimum t/c along the blade
    "le_radius_min_mm": "mm",         # minimum leading-edge radius
    "te_thickness_min_mm": "mm",      # minimum trailing-edge thickness
    "twist_release_max_deg": "deg",   # total twist (root to tip) the mould can release
    "draft_min_deg": "deg",           # draft against the parting direction (recorded; geometric check in a later stage)
    "tol_chord_mm": "mm",             # +- tolerance on chord
    "tol_twist_deg": "deg",           # +- tolerance on twist
    "tol_thickness_mm": "mm",         # +- tolerance on thickness
    "tol_te_mm": "mm",                # +- tolerance on trailing-edge thickness
}
TERMS_STATUSES = ("formula", "permitted", "restricted", "unknown")
FLAG_UNCONFIRMED = "manufacturing_limits_unconfirmed"


# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------

def cosine_grid(n: int = 101) -> np.ndarray:
    """x/c from 0 to 1 with cosine spacing (dense at the leading edge)."""
    return 0.5 * (1.0 - np.cos(np.linspace(0.0, math.pi, int(n))))


def naca4(code: str, n: int = 101, closed_te: bool = True) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """``(x, y_upper, y_lower)`` of a NACA 4-digit section from the published formula (Abbott and von Doenhoff, NACA Report 460 / 824).

    ``code`` is e.g. ``"4412"``: max camber 4 % at 40 % chord, 12 % thick.  ``closed_te`` uses the closed-trailing-edge coefficient
    (-0.1036) instead of the open one (-0.1015)."""
    if not re.fullmatch(r"\d{4}", str(code)):
        raise PropellerDataError("NACA 4-digit code must be four digits, got %r" % (code,))
    m, p, t = int(code[0]) / 100.0, int(code[1]) / 10.0, int(code[2:]) / 100.0
    if t <= 0:
        raise PropellerDataError("NACA 4-digit code %r has zero thickness" % (code,))
    x = cosine_grid(n)
    a4 = -0.1036 if closed_te else -0.1015
    yt = 5.0 * t * (0.2969 * np.sqrt(x) - 0.1260 * x - 0.3516 * x ** 2 + 0.2843 * x ** 3 + a4 * x ** 4)
    if m == 0.0 or p == 0.0:
        yc = np.zeros_like(x)
        dyc = np.zeros_like(x)
    else:
        yc = np.where(x < p, m / p ** 2 * (2 * p * x - x ** 2), m / (1 - p) ** 2 * ((1 - 2 * p) + 2 * p * x - x ** 2))
        dyc = np.where(x < p, 2 * m / p ** 2 * (p - x), 2 * m / (1 - p) ** 2 * (p - x))
    th = np.arctan(dyc)
    return x, yc + yt * np.cos(th), yc - yt * np.cos(th)


def parse_selig(text: str) -> Tuple[str, np.ndarray, np.ndarray, np.ndarray]:
    """Parse a Selig-format ``.dat`` (name line, then x y from the upper trailing edge round the leading edge to the lower one).

    Returns ``(name, x, y_upper, y_lower)`` resampled on a common cosine grid."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) < 12:
        raise PropellerDataError("airfoil file too short")
    name, rows = lines[0], []
    for ln in lines[1:]:
        parts = ln.replace(",", " ").split()
        try:
            rows.append((float(parts[0]), float(parts[1])))
        except (ValueError, IndexError):
            raise PropellerDataError("airfoil file: bad coordinate line %r" % ln)
    pts = np.array(rows)
    if not np.all(np.isfinite(pts)):
        raise PropellerDataError("airfoil file: non-finite coordinate")
    if pts[:, 0].min() < -1e-6 or pts[:, 0].max() > 1.0 + 1e-3:
        raise PropellerDataError("airfoil file: x/c must lie in [0, 1]")
    i_le = int(np.argmin(pts[:, 0]))
    if i_le in (0, len(pts) - 1):
        raise PropellerDataError("airfoil file: leading edge must be inside the list (Selig order)")
    up, lo = pts[: i_le + 1][::-1], pts[i_le:]
    if np.any(np.diff(up[:, 0]) <= 0) or np.any(np.diff(lo[:, 0]) <= 0):
        raise PropellerDataError("airfoil file: x must increase on both surfaces from the leading edge")
    x = cosine_grid(101)
    xmax = min(up[-1, 0], lo[-1, 0])
    xs = x * xmax
    yu, yl = np.interp(xs, up[:, 0], up[:, 1]), np.interp(xs, lo[:, 0], lo[:, 1])
    scale = 1.0 / xmax if xmax > 0 else 1.0
    return name, xs * scale, yu * scale, yl * scale


# ---------------------------------------------------------------------------
# Section properties
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SectionProps:
    t_c: float            # max thickness / chord
    camber: float         # max camber / chord
    te_gap_c: float       # trailing-edge gap / chord
    le_radius_c: float    # leading-edge radius / chord


def section_props(x: np.ndarray, yu: np.ndarray, yl: np.ndarray) -> SectionProps:
    yt, yc = 0.5 * (yu - yl), 0.5 * (yu + yl)
    # near the leading edge half-thickness ~ sqrt(2 r x); g = yt^2 / (2 x) is linear in sqrt(x) to first order, its intercept is r_le
    sel = (x >= 1e-4) & (x <= 0.01)
    if sel.sum() >= 3:
        r_le = float(max(np.polyfit(np.sqrt(x[sel]), yt[sel] ** 2 / (2.0 * x[sel]), 1)[1], 0.0))
    else:
        r_le = 0.0
    return SectionProps(t_c=float(2.0 * yt.max()), camber=float(np.abs(yc).max()),
                        te_gap_c=float(yu[-1] - yl[-1]), le_radius_c=r_le)


# ---------------------------------------------------------------------------
# Library with terms records
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AirfoilEntry:
    id: str
    family: str
    x: np.ndarray = field(repr=False)
    yu: np.ndarray = field(repr=False)
    yl: np.ndarray = field(repr=False)
    terms_status: str
    terms_source: str
    confirmed_by: Optional[str]

    @property
    def commercial_use_ok(self) -> bool:
        """A formula shape is free; a file shape needs terms recorded as permitted AND confirmed by a named person."""
        return self.terms_status == "formula" or (self.terms_status == "permitted" and bool(self.confirmed_by))


def generated(airfoil_id: str, n: int = 101) -> AirfoilEntry:
    """``naca4-4412`` -> the formula section (no data licence involved)."""
    m = re.fullmatch(r"naca4-(\d{4})", str(airfoil_id))
    if not m:
        raise PropellerDataError("not a generated airfoil id: %r (expected naca4-XXXX)" % (airfoil_id,))
    x, yu, yl = naca4(m.group(1), n)
    return AirfoilEntry(airfoil_id, "naca4", x, yu, yl, "formula",
                        "NACA Report 460 / 824 thickness and camber formula (published; a formula, not a data file)", None)


def load_library(root: Path = AIRFOIL_DIR) -> Dict[str, AirfoilEntry]:
    """Airfoils from ``<root>/<family>/<name>.dat`` with ``<root>/<family>/_terms.yaml`` (``terms_status``, ``source_url``,
    ``accessed``, ``terms_quote``, ``confirmed_by``).  A family without a terms file, or with an unknown status, loads as
    ``unknown`` (never usable commercially).  A bad file is skipped, not fatal."""
    out: Dict[str, AirfoilEntry] = {}
    root = Path(root)
    if not root.is_dir():
        return out
    for fam in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("_")):
        terms = {}
        tf = fam / "_terms.yaml"
        if tf.is_file():
            try:
                terms = yaml.safe_load(tf.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                terms = {}
        status = terms.get("terms_status") if terms.get("terms_status") in TERMS_STATUSES else "unknown"
        for f in sorted(fam.glob("*.dat")):
            try:
                _, x, yu, yl = parse_selig(f.read_text(encoding="utf-8"))
            except (PropellerDataError, UnicodeDecodeError):
                continue
            aid = "%s-%s" % (fam.name, f.stem)
            out[aid] = AirfoilEntry(aid, fam.name, x, yu, yl, status,
                                    str(terms.get("source_url") or ""), terms.get("confirmed_by") or None)
    return out


def get_airfoil(airfoil_id: str, library: Optional[Dict[str, AirfoilEntry]] = None) -> AirfoilEntry:
    lib = load_library() if library is None else library
    if airfoil_id in lib:
        return lib[airfoil_id]
    return generated(airfoil_id)


# ---------------------------------------------------------------------------
# Manufacturing profile
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Limit:
    value: Optional[float]
    unit: str
    source: Optional[str]
    confirmed_by: Optional[str]

    @property
    def confirmed(self) -> bool:
        return self.value is not None and bool(self.confirmed_by)


@dataclass(frozen=True)
class ManufacturingProfile:
    process: Optional[str]
    limits: Dict[str, Limit]

    def value(self, name: str) -> Optional[float]:
        lim = self.limits.get(name)
        return None if lim is None else lim.value

    def unconfirmed(self) -> List[str]:
        """Names of the limits that carry a value but no confirmation (the value is applied, flagged)."""
        return sorted(n for n, l in self.limits.items() if l.value is not None and not l.confirmed)

    def unset(self) -> List[str]:
        return sorted(n for n in LIMIT_UNITS if self.value(n) is None)

    def flags(self) -> List[str]:
        return [FLAG_UNCONFIRMED] if self.unconfirmed() or self.unset() else []


def parse_manufacturing(raw: Any, where: str = "blade.manufacturing") -> ManufacturingProfile:
    """``blade.manufacturing``: ``{process: ..., limits: {name: {value, source, confirmed_by}}}``; None / missing -> no limits."""
    if raw is None:
        return ManufacturingProfile(None, {})
    if not isinstance(raw, dict):
        raise PropellerDataError(f"{where}: must be a mapping")
    lim_raw = raw.get("limits") or {}
    if not isinstance(lim_raw, dict):
        raise PropellerDataError(f"{where}: limits must be a mapping")
    limits: Dict[str, Limit] = {}
    for name, d in lim_raw.items():
        if name not in LIMIT_UNITS:
            raise PropellerDataError(f"{where}: unknown limit {name!r}; known: {sorted(LIMIT_UNITS)}")
        d = {} if d is None else d
        if not isinstance(d, dict):
            raise PropellerDataError(f"{where} {name}: must be a mapping")
        v = d.get("value")
        if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)) or float(v) < 0):
            raise PropellerDataError(f"{where} {name}: value must be a non-negative number, got {v!r}")
        src, conf = d.get("source"), d.get("confirmed_by")
        if v is not None and not (isinstance(src, str) and src.strip()):
            raise PropellerDataError(f"{where} {name}: a limit with a value needs a source (the shop, a datasheet or 'owner placeholder')")
        limits[name] = Limit(None if v is None else float(v), LIMIT_UNITS[name], src.strip() if isinstance(src, str) else None,
                             conf.strip() if isinstance(conf, str) and conf.strip() else None)
    proc = raw.get("process")
    return ManufacturingProfile(proc.strip() if isinstance(proc, str) and proc.strip() else None, limits)


EMPTY_PROFILE = ManufacturingProfile(None, {})


# ---------------------------------------------------------------------------
# The manufactured section
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ManufacturedSection:
    x: np.ndarray = field(repr=False)
    yu: np.ndarray = field(repr=False)
    yl: np.ndarray = field(repr=False)
    ideal: SectionProps
    made: SectionProps
    chord_mm: float
    active: Tuple[str, ...]            # limits that changed the shape, in the order applied
    notes: Dict[str, Any]
    flags: Tuple[str, ...]


def manufactured_section(x: np.ndarray, yu: np.ndarray, yl: np.ndarray, chord_mm: float,
                         profile: ManufacturingProfile = EMPTY_PROFILE) -> ManufacturedSection:
    """The section a mould can produce.  Order: thickness (wall minimum, t/c minimum, leading-edge radius), then the trailing edge.

    Thickness is scaled about the camber line (camber unchanged); the trailing edge gets a linear wedge so that the gap equals the
    minimum.  A limit that is not set is skipped (and ``flags`` says the profile is incomplete)."""
    if not (chord_mm > 0 and math.isfinite(chord_mm)):
        raise PropellerDataError("chord must be positive")
    x = np.asarray(x, float)
    yt, yc = 0.5 * (np.asarray(yu, float) - np.asarray(yl, float)), 0.5 * (np.asarray(yu, float) + np.asarray(yl, float))
    ideal = section_props(x, yc + yt, yc - yt)
    active: List[str] = []
    notes: Dict[str, Any] = {}
    # thickness: the largest scale any limit demands
    demands: Dict[str, float] = {}
    w = profile.value("wall_min_mm")
    if w is not None and ideal.t_c > 0:
        demands["wall_min_mm"] = (w / chord_mm) / ideal.t_c
    r = profile.value("thickness_ratio_min")
    if r is not None and ideal.t_c > 0:
        demands["thickness_ratio_min"] = r / ideal.t_c
    rl = profile.value("le_radius_min_mm")
    if rl is not None and ideal.le_radius_c > 0:
        demands["le_radius_min_mm"] = math.sqrt((rl / chord_mm) / ideal.le_radius_c)       # r_le scales with the square of the thickness
    k = max([1.0] + list(demands.values()))
    if k > 1.0 + 1e-12:
        yt = yt * k
        governing = max(demands, key=demands.get)
        active.append(governing)
        notes["thickness_scale"] = k
        notes["thickness_governing_limit"] = governing
        notes["thickness_demands"] = {n: v for n, v in demands.items() if v > 1.0 + 1e-12}
    te_min = profile.value("te_thickness_min_mm")
    if te_min is not None:
        gap = 2.0 * float(yt[-1])
        need = te_min / chord_mm
        if gap < need - 1e-12:
            yt = yt + 0.5 * (need - gap) * x
            active.append("te_thickness_min_mm")
            notes["te_added_c"] = need - gap
    yu2, yl2 = yc + yt, yc - yt
    made = section_props(x, yu2, yl2)
    flags = tuple(profile.flags())
    return ManufacturedSection(x, yu2, yl2, ideal, made, float(chord_mm), tuple(active), notes, flags)


# ---------------------------------------------------------------------------
# Blade-level checks and tolerance perturbations
# ---------------------------------------------------------------------------

def check_blade(twist_deg: Sequence[float], chord_mm: Sequence[float], thickness_rel: Sequence[float],
                profile: ManufacturingProfile) -> List[Dict[str, Any]]:
    """Violations of the blade-level limits for stations given root to tip (thickness AFTER manufacturing).

    Each record: ``{limit, station, value, limit_value, unit}``; ``station`` is None for a whole-blade limit."""
    out: List[Dict[str, Any]] = []
    rel = profile.value("twist_release_max_deg")
    if rel is not None and twist_deg:
        rng = float(max(twist_deg) - min(twist_deg))
        if rng > rel + 1e-9:
            out.append({"limit": "twist_release_max_deg", "station": None, "value": rng, "limit_value": rel, "unit": "deg"})
    w, r = profile.value("wall_min_mm"), profile.value("thickness_ratio_min")
    for i, (c, t) in enumerate(zip(chord_mm, thickness_rel)):
        if w is not None and t * c < w - 1e-9:
            out.append({"limit": "wall_min_mm", "station": i, "value": t * c, "limit_value": w, "unit": "mm"})
        if r is not None and t < r - 1e-12:
            out.append({"limit": "thickness_ratio_min", "station": i, "value": t, "limit_value": r, "unit": "-"})
    return out


def tolerance_perturbations(profile: ManufacturingProfile) -> List[Dict[str, float]]:
    """Corner cases of the stated tolerances for the robustness evaluation: every combination of +-chord, +-twist, +-thickness (up to 8)
    with the trailing edge at its minimum (-tol_te).  A tolerance that is not stated is not perturbed.  The unperturbed case is first."""
    base = {"d_chord_mm": 0.0, "d_twist_deg": 0.0, "d_thickness_mm": 0.0, "d_te_mm": 0.0}
    axes = []
    for key, name in (("d_chord_mm", "tol_chord_mm"), ("d_twist_deg", "tol_twist_deg"), ("d_thickness_mm", "tol_thickness_mm")):
        v = profile.value(name)
        axes.append((key, [-v, v] if v else [0.0]))
    te = profile.value("tol_te_mm")
    out = [dict(base)]
    for combo in itertools.product(*[a[1] for a in axes]):
        d = dict(base)
        for (key, _), val in zip(axes, combo):
            d[key] = float(val)
        d["d_te_mm"] = -float(te) if te else 0.0
        if d != base:
            out.append(d)
    return out
