"""Bearing friction and rotor windage — the MECHANICAL half of the loss picture.

Until this module existed the app computed copper, iron, magnet, shaft and
sleeve loss and then said, in the datasheet's own words, that "bearing and
windage losses belong to the assembled machine and are not included".  On the
real 150 mm free run those two were **43 - 170 W out of 83 - 319 W measured** —
the single largest term below 3000 rpm.  A loss picture that leaves them out is
not a loss picture.

WHAT IS MODELLED
================

**Bearings — the SKF frictional-moment model.**  Source: SKF, *The SKF model for
calculating the frictional moment*
(``cdn.skfmediahub.skf.com/api/public/0901d1968065e9e7``), tables 2 and 3::

    M = M_rr + M_sl + M_seal + M_drag          [N.mm]

    M_rr   = Phi_ish . Phi_rs . G_rr . (n.nu)^0.6      rolling
    M_sl   = G_sl . mu_sl                              sliding
    M_seal = K_S1 . d_s^beta + K_S2                    both seals of one bearing
    M_drag = 0                                         grease: no oil bath to churn

with ``n`` in rpm, ``nu`` in mm^2/s, every length in mm, and the whole thing in
N.mm — SKF's own units, kept internally so a constant can be read straight off
the table and dropped in.  Only the public answer is converted to N.m.

``G_rr`` and ``G_sl`` come from the CARD (``config/bearings_library.yaml``), not
from a table lookup hidden in here: each bearing states which SKF row applies to
it, because a 618-series thin section and a 70-series angular contact use
different constants *and different formulas*, and a catalogue that does not say
which is a catalogue nobody can check.

**Validated.**  ``docs/measurements/2026-08-04_noload_decomposition_150mm.md``
§4.1 pinned this model (nothing fitted) on 2 x 61811-2RS1 and the measured
free-run curve returned the bearing torque to 3 %: 0.387 +/- 0.025 N.m measured
against 0.400 N.m predicted at 2000 rpm.  ``tests/test_bearings.py`` asserts it.

**Windage.**  Air-gap Couette friction plus the two rotor end faces as rotating
discs.  The gap's laminar/turbulent decision reuses the Taylor-number regime
logic of ``simulation.cooling_models.taylor_couette_gap`` — one machine must not
have a gap that is laminar for heat and turbulent for drag — and the air
properties come from the same ``air_properties`` at a stated temperature.

WHAT IS **NOT** MODELLED, AND MUST NOT BE READ INTO THE ANSWER
==============================================================

* **Unbalanced magnetic pull.**  A rotor that is not perfectly centred is pulled
  toward the short side by a force that can dwarf its own weight, and it goes
  straight into F_r.  Nothing here models it.  ``machine_bearing_losses`` says so
  in its ``note`` and the UI repeats it.
* Belt / gear / coupling side loads, a third bearing, a brake, an encoder, a
  radial lip seal on the shaft — any one of those changes the answer completely.
  F_r here is the rotor's own weight plus whatever preload the user states.
* Run-in state, fill ratio, cage friction beyond SKF's own coefficients.

ISOLATION: nothing in this module solves a field, touches the shared config, or
writes anything.  It is arithmetic over a catalogue and an operating point.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path + module cache
# ---------------------------------------------------------------------------
_LIB_PATH = Path(__file__).parent.parent.parent / "config" / "bearings_library.yaml"

_library: Optional[dict] = None
_lib_mtime: float = 0.0      # mtime of the YAML the cached copy was parsed from
_lib_checked: float = 0.0    # monotonic clock of the last mtime probe
# Same bargain as materials._load (see materials.py lines 34-77): the cache
# FOLLOWS the file, so a bearing added or corrected on disk is visible without a
# restart, but the mtime is probed at most once a second so a per-point sweep
# does not turn into a syscall storm.  A half-written edit keeps the last good
# copy rather than taking the process down.
_MTIME_PROBE_S = 1.0

_CARD_CACHE: Dict[str, "BearingCard"] = {}
_LUBE_CACHE: Dict[str, "Lubricant"] = {}


class UnknownBearingError(KeyError):
    """A bearing or lubricant name that is not in the library."""


def _load() -> dict:
    """Load the bearing library YAML, re-reading it when the file changes."""
    global _library, _lib_mtime, _lib_checked
    now = time.monotonic()
    if _library is not None and (now - _lib_checked) < _MTIME_PROBE_S:
        return _library
    if not _LIB_PATH.exists():
        if _library is not None:
            return _library          # file vanished mid-session: keep serving it
        raise FileNotFoundError(f"Bearing library not found: {_LIB_PATH}")
    _lib_checked = now
    mtime = _LIB_PATH.stat().st_mtime
    if _library is not None and mtime == _lib_mtime:
        return _library
    try:
        with _LIB_PATH.open("r", encoding="utf-8") as f:
            parsed = yaml.safe_load(f)
    except Exception as e:                      # noqa: BLE001
        # Half-written or malformed edit: keep the last good copy rather than
        # taking the whole app down mid-request.  The next probe retries.
        if _library is not None:
            _log.warning("bearings_library.yaml unreadable (%s) — keeping the "
                         "previously loaded copy", e)
            return _library
        raise
    if not isinstance(parsed, dict):
        if _library is not None:
            _log.warning("bearings_library.yaml is not a mapping — keeping the "
                         "previously loaded copy")
            return _library
        raise ValueError(f"{_LIB_PATH} does not parse to a mapping")
    _library = parsed
    _lib_mtime = mtime
    _CARD_CACHE.clear()          # the dataclasses belong to the OLD file
    _LUBE_CACHE.clear()
    return _library


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Lubricant:
    """A grease or oil, as the friction model needs it: two viscosity points."""
    name: str
    description: str = ""
    kind: str = "grease"                 # 'grease' | 'oil_air'
    nu40: float = 100.0                  # mm^2/s at 40 C
    nu100: float = 10.0                  # mm^2/s at 100 C
    temp_range_c: Optional[List[float]] = None
    note: str = ""

    def nu_at(self, temp_c: float) -> float:
        """Base-oil viscosity [mm^2/s] at ``temp_c``, Walther / ASTM D341."""
        return walther_nu(self.nu40, self.nu100, temp_c)


@dataclass
class BearingCard:
    """One catalogue bearing.  Everything the SKF model needs, stated by the
    card itself rather than looked up behind its back."""
    name: str
    description: str = ""
    type: str = "deep_groove"            # 'deep_groove' | 'angular_contact'
    d: float = 0.0                       # bore [mm]
    D: float = 0.0                       # outside diameter [mm]
    B: float = 0.0                       # width [mm]
    d_m: Optional[float] = None          # mean diameter [mm]; (d+D)/2 when absent
    contact_angle_deg: Optional[float] = None
    balls: str = "steel"                 # 'steel' | 'ceramic'
    seals: str = "none"                  # 'none' | 'shields' | 'contact' | 'low_friction'
    seal_ks1: Optional[float] = None
    seal_ks2: Optional[float] = None
    seal_beta: Optional[float] = None
    seal_ds: Optional[float] = None      # counterface diameter [mm]
    default_lubricant: Optional[str] = None
    n_limit_grease_rpm: Optional[float] = None
    n_limit_oil_air_rpm: Optional[float] = None
    C_kn: Optional[float] = None
    C0_kn: Optional[float] = None
    stiffness_n_per_m: Optional[float] = None
    friction: Dict[str, float] = field(default_factory=dict)
    note: str = ""

    # ── derived ──────────────────────────────────────────────────────────────
    @property
    def dm(self) -> float:
        v = self.d_m if self.d_m else 0.5 * (float(self.d) + float(self.D))
        return float(v)

    @property
    def ds(self) -> float:
        """Seal counterface diameter.  SKF does not publish it for most
        bearings; d + 0.25(D - d) is the usual estimate and is worth +/-8 % on
        M_seal, which is why a card that KNOWS it states it."""
        if self.seal_ds:
            return float(self.seal_ds)
        return float(self.d) + 0.25 * (float(self.D) - float(self.d))

    def limit_rpm(self, lubrication: str) -> Optional[float]:
        v = (self.n_limit_oil_air_rpm if lubrication == "oil_air"
             else self.n_limit_grease_rpm)
        return None if v in (None, 0) else float(v)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name, "description": self.description, "type": self.type,
            "d": self.d, "D": self.D, "B": self.B, "d_m": self.dm,
            "contact_angle_deg": self.contact_angle_deg, "balls": self.balls,
            "seals": self.seals, "seal_ds": self.ds,
            "default_lubricant": self.default_lubricant,
            "n_limit_grease_rpm": self.n_limit_grease_rpm,
            "n_limit_oil_air_rpm": self.n_limit_oil_air_rpm,
            "C_kn": self.C_kn, "C0_kn": self.C0_kn,
            "stiffness_n_per_m": self.stiffness_n_per_m,
            "friction": dict(self.friction), "note": self.note,
        }


# ---------------------------------------------------------------------------
# Library access
# ---------------------------------------------------------------------------

_NUMERIC_CARD_FIELDS = (
    "d", "D", "B", "d_m", "contact_angle_deg", "seal_ks1", "seal_ks2",
    "seal_beta", "seal_ds", "n_limit_grease_rpm", "n_limit_oil_air_rpm",
    "C_kn", "C0_kn", "stiffness_n_per_m",
)


def _num(v: Any, where: str, key: str) -> Any:
    """A catalogue number, or a loud refusal.

    This library is HAND-EDITED, and YAML 1.1 quietly parses ``1.0e8`` as the
    STRING "1.0e8" (a float literal needs a signed exponent).  A stiffness that
    is secretly a string reaches the rotordynamics default as a string and blows
    up three layers away from the typo, so it is caught here, at the card, with
    the field named.
    """
    if v is None or isinstance(v, (int, float)) and not isinstance(v, bool):
        return v
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        raise ValueError(
            f"bearings_library.yaml: {where}.{key} = {v!r} is not a number "
            f"(YAML needs a SIGNED exponent: write 1.0e+8, not 1.0e8)")


def _card_from(name: str, raw: dict) -> BearingCard:
    known = {f.name for f in BearingCard.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    kw = {k: v for k, v in (raw or {}).items() if k in known}
    kw["name"] = name
    for k in _NUMERIC_CARD_FIELDS:
        if k in kw:
            kw[k] = _num(kw[k], name, k)
    fx = dict(kw.get("friction") or {})
    kw["friction"] = {k: _num(v, name, f"friction.{k}") for k, v in fx.items()}
    return BearingCard(**kw)


def _lube_from(name: str, raw: dict) -> Lubricant:
    known = {f.name for f in Lubricant.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    kw = {k: v for k, v in (raw or {}).items() if k in known}
    kw["name"] = name
    for k in ("nu40", "nu100"):
        if k in kw:
            kw[k] = _num(kw[k], name, k)
    return Lubricant(**kw)


def list_bearings() -> List[str]:
    return sorted((_load().get("bearings") or {}).keys())


def list_lubricants() -> List[str]:
    return sorted((_load().get("lubricants") or {}).keys())


def get_bearing(name: str) -> BearingCard:
    """The card called ``name``.  Raises ``UnknownBearingError`` naming what IS
    available — a silent fallback to "some bearing" would be a wrong number with
    a confident face on it."""
    lib = _load()
    if name in _CARD_CACHE:
        return _CARD_CACHE[name]
    raw = (lib.get("bearings") or {}).get(name)
    if raw is None:
        raise UnknownBearingError(
            f"unknown bearing '{name}' — the library has: "
            + ", ".join(list_bearings()))
    card = _card_from(name, raw)
    _CARD_CACHE[name] = card
    return card


def get_lubricant(name: str) -> Lubricant:
    lib = _load()
    if name in _LUBE_CACHE:
        return _LUBE_CACHE[name]
    raw = (lib.get("lubricants") or {}).get(name)
    if raw is None:
        raise UnknownBearingError(
            f"unknown lubricant '{name}' — the library has: "
            + ", ".join(list_lubricants()))
    lube = _lube_from(name, raw)
    _LUBE_CACHE[name] = lube
    return lube


def library() -> Dict[str, Any]:
    """The whole catalogue as plain dicts — what ``GET /api/bearings/library``
    serves."""
    lib = _load()
    return {
        "bearings": {n: get_bearing(n).to_dict() for n in list_bearings()},
        "lubricants": {n: {
            "name": n, "description": get_lubricant(n).description,
            "kind": get_lubricant(n).kind, "nu40": get_lubricant(n).nu40,
            "nu100": get_lubricant(n).nu100,
            "temp_range_c": get_lubricant(n).temp_range_c,
            "note": get_lubricant(n).note,
        } for n in list_lubricants()},
        "source": (lib.get("source")
                   or "SKF, 'The SKF model for calculating the frictional "
                      "moment', tables 2 and 3"),
    }


# ---------------------------------------------------------------------------
# Viscosity
# ---------------------------------------------------------------------------

def walther_nu(nu40: float, nu100: float, temp_c: float) -> float:
    """Kinematic viscosity [mm^2/s] at ``temp_c``, Walther / ASTM D341.

        log10(log10(nu + 0.7)) = A - B . log10(T_K)

    Two datasheet points (40 C and 100 C, which is how every lubricant is
    quoted) fix A and B; everything else is interpolation on that line.  This is
    not a nicety: M_rr goes as nu^0.6 and BOTH correction factors go as a power
    of nu, so quoting a grease at 40 C and running the bearing at 90 C is a
    factor of ~2 on the rolling term.
    """
    n40, n100 = float(nu40), float(nu100)
    if not (n40 > 0.31 and n100 > 0.31):
        # Below nu = 0.3 the log-log line is undefined (log10 of a number < 1
        # is negative, and the outer log10 then fails).  No real lubricant is
        # there; refuse rather than return a complex number.
        raise ValueError(
            f"Walther interpolation needs nu40/nu100 above ~0.3 mm^2/s; "
            f"got {nu40} / {nu100}")
    if n100 >= n40:
        raise ValueError(
            f"nu100 ({nu100}) must be BELOW nu40 ({nu40}) — viscosity falls "
            f"with temperature; the two points look swapped")
    t1, t2 = 313.15, 373.15
    z1 = math.log10(math.log10(n40 + 0.7))
    z2 = math.log10(math.log10(n100 + 0.7))
    b = (z1 - z2) / (math.log10(t2) - math.log10(t1))
    a = z1 + b * math.log10(t1)
    tk = max(float(temp_c) + 273.15, 200.0)     # clamp: no negative-K nonsense
    z = a - b * math.log10(tk)
    # 10**z can overflow for an absurd extrapolation; clamp the inner exponent.
    nu = 10.0 ** min(10.0 ** min(z, 1.5), 12.0) - 0.7
    return max(float(nu), 1e-3)


# ---------------------------------------------------------------------------
# The SKF frictional-moment model
# ---------------------------------------------------------------------------

_MU_SL_DEFAULT = 0.05        # grease-lubricated ball bearing (SKF)
# Replenishment / starvation constant, SKF "The SKF model for calculating the
# frictional moment", p. 4: K_rs = 6e-8 for GREASE and OIL-AIR lubrication
# (the two this app models), 3e-8 for low-level oil bath and oil jet.  The
# first cut carried 3e-8 for grease (a slip in the brief); corrected
# 2026-09-08 against the document — Phi_rs falls 0.84 -> 0.70 at
# 23 000 rpm on the 71910, i.e. the rolling term gets SMALLER, not larger.
_K_RS_DEFAULT = 6.0e-8       # grease / oil-air starvation constant (SKF p. 4)
_K_RS_OIL_BATH = 3.0e-8      # low-level oil bath / oil jet (not modelled yet)


def _seal_moment(card: BearingCard) -> Tuple[float, bool, str]:
    """``(M_seal [N.mm], is_estimate, note)`` for ONE bearing, both seals.

    SKF table 3.  Shields (2Z/Z) and open bearings are exactly zero — a shield
    does not touch the inner ring, so it has no drag to model; the 2RZ
    low-friction seal has no row of its own here and is taken as half the RS1
    value and flagged.
    """
    s = (card.seals or "none").lower()
    if s in ("none", "open", ""):
        return 0.0, False, "open bearing — no seal drag"
    if s in ("shields", "shield", "2z", "z"):
        return 0.0, False, "metal shields do not touch the inner ring — no seal drag"
    ks1, ks2, beta = card.seal_ks1, card.seal_ks2, card.seal_beta
    if ks1 is None or ks2 is None or beta is None:
        return 0.0, True, (
            f"card '{card.name}' says seals='{s}' but carries no K_S1/K_S2/beta "
            f"(SKF table 3) — seal drag is reported as ZERO and this answer is "
            f"an UNDER-read; add the constants to the card")
    m = float(ks1) * card.ds ** float(beta) + float(ks2)
    if s in ("low_friction", "2rz", "rz"):
        return 0.5 * m, True, (
            "2RZ low-friction seal: SKF publishes no row for it, so HALF the "
            "RS1 value is used — an ESTIMATE")
    return m, False, "SKF table 3, contact seals, both seals of one bearing"


def friction(card: BearingCard, rpm: float, f_r_n: float,
             f_a_n: float = 0.0, temp_c: float = 70.0,
             lubrication: str = "grease",
             lubricant: Optional[str] = None,
             nu_mm2_s: Optional[float] = None) -> Dict[str, Any]:
    """Frictional moment and power of ONE bearing, SKF model.

    Parameters
    ----------
    rpm        shaft speed [rpm]
    f_r_n      radial load on this bearing [N]
    f_a_n      axial load / preload on this bearing [N]
    temp_c     bearing temperature — the lubricant is interpolated to it
    lubrication  ``'grease'`` (M_drag = 0) or ``'oil_air'`` (also M_drag = 0:
               an oil-air jet leaves no bath to churn)
    lubricant  library name; defaults to the card's ``default_lubricant``
    nu_mm2_s   override the interpolated viscosity outright.  This is how the
               validated 150 mm case is reproduced bit for bit (it was computed
               at nu = 30 mm^2/s), and how a measured grease temperature can be
               fed in directly.

    Returns a dict in N.m and W, plus every intermediate the answer rides on —
    nu, Phi_ish, Phi_rs, F_g — because a friction number whose viscosity is
    hidden cannot be argued with.
    """
    n = abs(float(rpm))
    dm = card.dm
    fr = max(float(f_r_n), 0.0)
    fa = max(float(f_a_n), 0.0)
    fx = dict(card.friction or {})
    notes: List[str] = []

    # ── viscosity ────────────────────────────────────────────────────────────
    lube_name = lubricant or card.default_lubricant
    if nu_mm2_s is not None:
        nu = max(float(nu_mm2_s), 1e-3)
        nu_src = f"stated outright ({nu:.4g} mm^2/s)"
    elif lube_name:
        lube = get_lubricant(lube_name)
        nu = lube.nu_at(temp_c)
        nu_src = (f"{lube.name} base oil, Walther from {lube.nu40}/{lube.nu100} "
                  f"mm^2/s at 40/100 C, evaluated at {float(temp_c):.0f} C")
        if lubrication == "oil_air" and lube.kind == "grease":
            notes.append(f"lubrication is oil-air but '{lube.name}' is a grease "
                         f"— pick an oil, or the viscosity is the wrong one")
        rng = lube.temp_range_c
        if rng and len(rng) == 2 and not (float(rng[0]) <= float(temp_c) <= float(rng[1])):
            notes.append(f"{float(temp_c):.0f} C is outside {lube.name}'s stated "
                         f"range {rng[0]}…{rng[1]} C")
    else:
        raise ValueError(
            f"bearing '{card.name}' has no default_lubricant and none was "
            f"given — the friction model cannot run without a viscosity")

    # ── rolling, M_rr = Phi_ish . Phi_rs . G_rr . (n.nu)^0.6 ─────────────────
    r1 = float(fx.get("R1", 0.0))
    is_ac = (card.type or "").lower().startswith("angular")
    ball_ratio = float(fx.get("ball_density_ratio", 1.0))
    f_g = 0.0
    if is_ac or ("R3" in fx and fa > 0.0):
        # SKF table 2 axial forms.  F_g is the ball CENTRIFUGAL load and it goes
        # as n^2 — above ~15 000 rpm on a 60 mm d_m it is the whole answer.
        # ball_density_ratio is OUR extension for hybrids (Si3N4 is 0.41x the
        # density of bearing steel, and F_g is a ball-mass term); SKF publishes
        # no separate R3 for hybrids, so it is applied openly and reported.
        f_g = float(fx.get("R3", 0.0)) * dm ** 4 * n ** 2 * ball_ratio
        r2 = float(fx.get("R2", 0.0))
        g_rr = r1 * dm ** 1.97 * max(fr + f_g + r2 * fa, 0.0) ** 0.54
        rr_form = "SKF table 2 axial form: R1.d_m^1.97.[F_r + F_g + R2.F_a]^0.54"
    else:
        g_rr = r1 * dm ** 1.96 * max(fr, 1e-9) ** 0.54
        rr_form = "SKF table 2 radial form: R1.d_m^1.96.F_r^0.54"
        if fa > 0.0:
            # Never silently drop a load the user typed.
            g_rr = r1 * dm ** 1.96 * max(fr + fa, 1e-9) ** 0.54
            rr_form += " with F_a carried as an EQUIVALENT RADIAL load"
            notes.append(
                f"'{card.name}' is a deep groove card with no axial constants "
                f"(R2/R3/S2) on it, and an axial load of {fa:.0f} N was given: "
                f"it is carried as an equivalent radial load, which OVER-reads "
                f"the rolling term and UNDER-reads the sliding one. Add the SKF "
                f"axial row to the card for a proper answer.")

    # Inlet shear heating: some of the oil pushed toward the contact flows back.
    phi_ish = 1.0 / (1.0 + 1.84e-9 * (n * dm) ** 1.28 * nu ** 0.64) if n > 0 else 1.0
    # Kinematic replenishment / starvation: at speed the track is not refilled
    # between passes.  K_z is a bearing-type constant (3.1 deep groove, 4.4
    # angular contact); K_rs = 6e-8 for grease and oil-air (SKF p. 4).
    k_z = float(fx.get("K_z", 3.1 if not is_ac else 4.4))
    k_rs = float(fx.get("K_rs", _K_RS_DEFAULT))
    d_span = max(float(card.D) - float(card.d), 1e-6)
    phi_rs = math.exp(-k_rs * nu * n * (float(card.d) + float(card.D))
                      * math.sqrt(k_z / (2.0 * d_span))) if n > 0 else 1.0
    m_rr = phi_ish * phi_rs * g_rr * (n * nu) ** 0.6 if n > 0 else 0.0

    # ── sliding, M_sl = G_sl . mu_sl ─────────────────────────────────────────
    s1 = float(fx.get("S1", 0.0))
    if is_ac or ("S2" in fx and fa > 0.0):
        s2 = float(fx.get("S2", 0.0))
        g_sl = s1 * dm ** 0.26 * ((fr + f_g) ** (4.0 / 3.0)
                                  + s2 * fa ** (4.0 / 3.0))
        sl_form = "SKF table 2 axial form: S1.d_m^0.26.[(F_r+F_g)^(4/3) + S2.F_a^(4/3)]"
    else:
        fr_eff = fr + fa if fa > 0.0 else fr
        g_sl = s1 * dm ** -0.26 * max(fr_eff, 1e-9) ** (5.0 / 3.0)
        sl_form = "SKF table 2 radial form: S1.d_m^-0.26.F_r^(5/3)"
    mu_sl = float(fx.get("mu_sl", _MU_SL_DEFAULT))
    m_sl = g_sl * mu_sl

    # ── seals, and the drag that is not there ────────────────────────────────
    m_seal, seal_est, seal_note = _seal_moment(card)
    if seal_est and seal_note:
        notes.append(seal_note)
    # M_drag is the loss of churning a bath of oil.  Grease has no bath, and an
    # oil-air jet delivers droplets, not a level — SKF's own model puts it at
    # zero for both, which is why every card here is grease or oil-air.
    m_drag = 0.0

    m_total_nmm = m_rr + m_sl + m_seal + m_drag
    m_total = m_total_nmm * 1e-3                       # N.m
    omega = n * 2.0 * math.pi / 60.0
    p_w = m_total * omega

    return {
        "bearing": card.name,
        "rpm": n,
        "M_rr_Nm": m_rr * 1e-3,
        "M_sl_Nm": m_sl * 1e-3,
        "M_seal_Nm": m_seal * 1e-3,
        "M_drag_Nm": m_drag,
        "M_total_Nm": m_total,
        "P_W": p_w,
        "F_r_N": fr,
        "F_a_N": fa,
        "F_g_N": f_g,
        "ball_density_ratio": ball_ratio,
        "nu_mm2_s": nu,
        "nu_source": nu_src,
        "Phi_ish": phi_ish,
        "Phi_rs": phi_rs,
        "d_m_mm": dm,
        "d_s_mm": card.ds,
        "lubrication": lubrication,
        "lubricant": lube_name,
        "temp_c": float(temp_c),
        "seal_estimate": seal_est,
        "rolling_form": rr_form,
        "sliding_form": sl_form,
        "notes": notes,
    }


def speed_check(card: BearingCard, rpm: float,
                lubrication: str = "grease") -> Dict[str, Any]:
    """Is this bearing allowed to turn this fast on this lubricant?

    Two readings, because they say different things.  ``n.d_m`` [mm/min] is the
    speed parameter the whole industry compares bearings with — it is what
    decides whether a spindle needs oil-air at all.  The card's own rpm limit is
    the specific answer for THIS bearing, and on a sealed bearing it is set by
    the SEAL, not by the balls (compare 6010-2RZ/HC5C3 with 7010 CE/HCP4A: the
    same envelope, a 3x difference in permissible speed).
    """
    n = abs(float(rpm))
    dm = card.dm
    lim = card.limit_rpm(lubrication)
    n_dm = n * dm
    if lim is None:
        other = "oil_air" if lubrication == "grease" else "grease"
        alt = card.limit_rpm(other)
        return {
            "rpm": n, "d_m_mm": dm, "n_dm": n_dm, "lubrication": lubrication,
            "limit_rpm": None, "n_dm_limit": None, "ratio": None, "ok": None,
            "verdict": (f"no {lubrication.replace('_', '-')} limit on this card"
                        + (f" (it is a sealed, grease-for-life bearing; the "
                           f"{other} limit is {alt:,.0f} rpm)" if alt else "")),
        }
    ratio = n / lim if lim > 0 else None
    if ratio is None:
        verdict, ok = "no usable limit", None
    elif ratio <= 0.9:
        verdict, ok = "ok", True
    elif ratio <= 1.0:
        verdict, ok = "at the limit", True
    else:
        verdict, ok = "OVER the limit", False
    return {
        "rpm": n, "d_m_mm": dm, "n_dm": n_dm,
        "lubrication": lubrication,
        "limit_rpm": lim, "n_dm_limit": lim * dm,
        "ratio": ratio, "ok": ok, "verdict": verdict,
    }


# ---------------------------------------------------------------------------
# Windage
# ---------------------------------------------------------------------------

def windage(*, rpm: float, r_rotor_m: float, r_bore_m: float, length_m: float,
            temp_c: float = 40.0, n_faces: int = 2,
            r_face_m: Optional[float] = None) -> Dict[str, Any]:
    """Air drag on the rotor: the gap, and the two end faces, reported apart.

    **Gap** — Couette friction between the rotor OD (including any retaining
    sleeve: delta is the MECHANICAL clearance, the same rule
    ``taylor_couette_gap`` is strict about) and the stator bore::

        M_gap = C_f . pi . rho . omega^2 . r^4 . L

    with the regime chosen by the SAME Taylor number the cooling model uses, so
    one machine cannot have a laminar gap for heat and a turbulent one for drag:

      * ``Ta < 1700`` — laminar Couette, ``C_f = 2/Re``.  This reduces exactly to
        the textbook ``M = 2.pi.mu.omega.r^3.L/delta``, so the torque is LINEAR
        in speed and the power quadratic.
      * above it — Wendt / Bilgen-Boulos: ``C_f = 0.46.(delta/r)^0.3.Re^-0.5``
        for ``500 < Re < 1e4`` and ``0.073.(delta/r)^0.3.Re^-0.3`` beyond, never
        below the laminar value.

    **Faces** — each rotor end as a rotating disc, ``M = 0.5.C_M.rho.omega^2.r^5``
    with ``C_M = 3.87/Re_r^0.5`` laminar and ``0.146/Re_r^0.2`` turbulent
    (``Re_r = omega.r^2/nu``).  The free-disc coefficient is an UPPER bound for a
    face enclosed by an end plate.

    Cross-check: the real 150 mm (r = 56.3 mm, delta = 0.5 mm, L = 35 mm) comes
    out at ~1.0 W at 4000 rpm against the 1.12 W of the validated decomposition
    — the two use different gap models and agree to 8 %.
    """
    from motor_ai_sim.simulation.cooling_models import air_properties

    n = abs(float(rpm))
    omega = n * 2.0 * math.pi / 60.0
    props = air_properties(temp_c)
    rho, nu_air = float(props.rho), float(props.nu)
    r = max(float(r_rotor_m), 1e-6)
    r_face = max(float(r_face_m if r_face_m is not None else r_rotor_m), 1e-6)
    delta = max(float(r_bore_m) - float(r_rotor_m), 1e-9)
    length = max(float(length_m), 0.0)

    # ── gap ─────────────────────────────────────────────────────────────────
    re_gap = omega * r * delta / nu_air
    r_mean = 0.5 * (float(r_bore_m) + r)
    ta = (omega ** 2) * r_mean * (delta ** 3) / (nu_air ** 2)
    if n <= 0 or re_gap <= 0:
        c_f, regime = 0.0, "at rest"
    elif ta < 1700.0:
        c_f, regime = 2.0 / re_gap, "laminar (Ta < 1700)"
    else:
        lam = 2.0 / re_gap
        if re_gap < 1.0e4:
            turb = 0.46 * (delta / r) ** 0.3 * re_gap ** -0.5
            regime = "vortex / transitional (Wendt)"
        else:
            turb = 0.073 * (delta / r) ** 0.3 * re_gap ** -0.3
            regime = "turbulent (Bilgen-Boulos)"
        # The laminar value is a FLOOR: a correlation dipping below viscous
        # Couette at the regime boundary would make drag fall as speed rises.
        c_f = max(lam, turb)
        if c_f == lam:
            regime += " — held at the laminar floor"
    m_gap = c_f * math.pi * rho * omega ** 2 * r ** 4 * length

    # ── end faces ───────────────────────────────────────────────────────────
    re_r = omega * r_face ** 2 / nu_air
    if n <= 0 or re_r <= 0:
        c_m, face_regime = 0.0, "at rest"
    elif re_r < 3.0e5:
        c_m, face_regime = 3.87 / math.sqrt(re_r), "laminar disc"
    else:
        c_m, face_regime = 0.146 / re_r ** 0.2, "turbulent disc"
    m_face_one = 0.5 * c_m * rho * omega ** 2 * r_face ** 5
    m_faces = int(max(n_faces, 0)) * m_face_one

    m_total = m_gap + m_faces
    return {
        "rpm": n,
        "M_gap_Nm": m_gap, "P_gap_W": m_gap * omega,
        "M_faces_Nm": m_faces, "P_faces_W": m_faces * omega,
        "M_total_Nm": m_total, "P_W": m_total * omega,
        "C_f": c_f, "Re_gap": re_gap, "Ta": ta, "gap_regime": regime,
        "C_M": c_m, "Re_face": re_r, "face_regime": face_regime,
        "n_faces": int(max(n_faces, 0)),
        "delta_mm": delta * 1e3, "r_rotor_mm": r * 1e3,
        "r_bore_mm": float(r_bore_m) * 1e3, "length_mm": length * 1e3,
        "rho_air": rho, "nu_air": nu_air, "T_air_c": float(temp_c),
        "note": ("air-gap Couette + rotor end faces as rotating discs; delta is "
                 "the MECHANICAL clearance (stator bore - rotor OD including any "
                 "sleeve). Free-disc face coefficients are an upper bound for an "
                 "enclosed face."),
    }


def _geom_radii_m(geometry: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """``r_rotor`` (incl. sleeve), ``r_bore`` and the stack length, in metres.

    ``None`` when the geometry does not carry what windage needs — a missing
    dimension is "no windage number", never a guessed one.
    """
    g = geometry or {}

    def _f(key: str) -> Optional[float]:
        v = g.get(key)
        try:
            return float(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

    r_ro = _f("rotor_outer_radius")
    gap = _f("air_gap")
    length = _f("motor_length")
    if r_ro is None or gap is None or length is None or r_ro <= 0 or length <= 0:
        return None
    sleeve = _f("sleeve_thickness") or 0.0
    return {
        "r_rotor_m": (r_ro + sleeve) * 1e-3,
        "r_bore_m": (r_ro + gap) * 1e-3,
        "length_m": length * 1e-3,
        "sleeve_mm": sleeve,
    }


def windage_from_geometry(geometry: Dict[str, Any], rpm: float,
                          temp_c: float = 40.0) -> Optional[Dict[str, Any]]:
    """``windage()`` fed from a motor geometry dict, or ``None`` when the
    geometry is missing a dimension it needs."""
    r = _geom_radii_m(geometry)
    if r is None:
        return None
    out = windage(rpm=rpm, r_rotor_m=r["r_rotor_m"], r_bore_m=r["r_bore_m"],
                  length_m=r["length_m"], temp_c=temp_c)
    out["sleeve_mm"] = r["sleeve_mm"]
    return out


# ---------------------------------------------------------------------------
# The machine's own bearings
# ---------------------------------------------------------------------------

_UMP_NOTE = ("Unbalanced magnetic pull is NOT modelled: an off-centre rotor is "
             "pulled toward the short side by a force that can exceed its own "
             "weight, and it would go straight into F_r. Nor are coupling, belt "
             "or gear side loads, or any seal on the shaft itself.")


def resolve_assignment(assignment: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Normalise a machine's ``bearings`` block into the shape this module
    takes.  Missing halves fall back to the other one — a machine with one card
    named has two of that bearing, which is what a symmetric shaft line is."""
    a = dict(assignment or {})
    end_a = dict(a.get("A") or {})
    end_b = dict(a.get("B") or {})
    if not end_a.get("card") and end_b.get("card"):
        end_a = dict(end_b)
    if not end_b.get("card") and end_a.get("card"):
        end_b = dict(end_a)
    lub = str(a.get("lubrication") or "grease")
    if lub not in ("grease", "oil_air"):
        lub = "grease"
    return {
        "A": end_a, "B": end_b,
        "lubrication": lub,
        "preload_n": float(a.get("preload_n") or 0.0),
        "temp_source": str(a.get("temp_source") or "manual"),
        "temp_c": (None if a.get("temp_c") in (None, "")
                   else float(a.get("temp_c"))),
    }


def has_bearings(assignment: Optional[Dict[str, Any]]) -> bool:
    r = resolve_assignment(assignment)
    return bool(r["A"].get("card") or r["B"].get("card"))


def _load_split(weight_n: float, beam: Optional[Dict[str, Any]]) -> Tuple[float, float, str]:
    """Split the rotor weight between bearing A and bearing B.

    With a span and a stack offset the lever rule places it; without them each
    bearing carries half, which is what a symmetric machine gives anyway.  This
    barely matters to the answer — M_rr goes as F_r^0.54 and the seal term does
    not depend on load at all — but a stated split is checkable and a hidden one
    is not.
    """
    span = None
    off = 0.0
    if beam:
        try:
            span = float(beam.get("bearing_span_mm"))
        except (TypeError, ValueError):
            span = None
        try:
            off = float(beam.get("stack_offset_mm") or 0.0)
        except (TypeError, ValueError):
            off = 0.0
    if span is None or not (span > 0):
        return 0.5 * weight_n, 0.5 * weight_n, "half each (no bearing span given)"
    # Stack CG at a = span/2 + offset from bearing A; clamp inside the span so a
    # nonsense offset cannot produce a negative reaction.
    a = min(max(0.5 * span + off, 0.0), span)
    f_b = weight_n * a / span
    f_a = weight_n - f_b
    return f_a, f_b, (f"lever rule over a {span:.0f} mm span with the stack "
                      f"{off:+.0f} mm off centre")


def machine_bearing_losses(assignment: Optional[Dict[str, Any]],
                           rpm: float,
                           temp_c: float = 70.0,
                           rotor_mass_kg: float = 0.0,
                           geometry: Optional[Dict[str, Any]] = None,
                           preload_n: Optional[float] = None,
                           beam: Optional[Dict[str, Any]] = None,
                           windage_temp_c: Optional[float] = None) -> Dict[str, Any]:
    """The whole mechanical loss of one machine: two bearings plus windage.

    ``assignment`` is the machine's ``bearings`` block (see
    ``routes/family.py::set_bearings``).  When it names no card the answer is
    ``has_bearings: False`` and a note saying where to assign them — never a
    zero pretending to be a computed number.

    F_r is the rotor's own WEIGHT, split between the two bearings; ``preload_n``
    (the assignment's, or this argument overriding it) is applied as an axial
    load on each.  Read ``_UMP_NOTE``: the load set here is deliberately the
    smallest honest one.
    """
    res = resolve_assignment(assignment)
    if not (res["A"].get("card") or res["B"].get("card")):
        return {
            "has_bearings": False,
            "rpm": float(rpm),
            "note": ("this machine has no bearings assigned — set them in "
                     "Mechanical -> Shaft & bearings"),
        }

    t_c = float(temp_c if temp_c is not None else (res["temp_c"] or 70.0))
    pre = float(preload_n if preload_n is not None else res["preload_n"])
    weight_n = max(float(rotor_mass_kg or 0.0), 0.0) * 9.80665
    f_a, f_b, split_note = _load_split(weight_n, beam)

    out_bearings: List[Dict[str, Any]] = []
    notes: List[str] = [_UMP_NOTE, f"radial load = rotor weight, {split_note}"]
    p_bearings = 0.0
    m_bearings = 0.0
    for end, f_r in (("A", f_a), ("B", f_b)):
        spec = res[end]
        name = spec.get("card")
        if not name:
            continue
        card = get_bearing(str(name))
        fr = friction(card, rpm=rpm, f_r_n=f_r, f_a_n=pre, temp_c=t_c,
                      lubrication=res["lubrication"],
                      lubricant=spec.get("grease") or spec.get("lubricant"))
        sc = speed_check(card, rpm, res["lubrication"])
        row = {"end": end, **fr, "speed": sc,
               "stiffness_n_per_m": card.stiffness_n_per_m,
               "description": card.description}
        out_bearings.append(row)
        p_bearings += float(fr["P_W"])
        m_bearings += float(fr["M_total_Nm"])
        for nt in fr.get("notes") or []:
            notes.append(f"bearing {end}: {nt}")
        if sc.get("ok") is False:
            notes.append(
                f"bearing {end} ({card.name}) is {sc['verdict']} at "
                f"{float(rpm):,.0f} rpm on {res['lubrication'].replace('_', '-')} "
                f"(limit {sc['limit_rpm']:,.0f} rpm)")

    wind = None
    if geometry:
        wind = windage_from_geometry(
            geometry, rpm,
            temp_c=float(windage_temp_c if windage_temp_c is not None else t_c))
        if wind is None:
            notes.append("windage not computed: the geometry does not carry "
                         "rotor_outer_radius / air_gap / motor_length")
    p_wind = float(wind["P_W"]) if wind else 0.0

    return {
        "has_bearings": True,
        "rpm": float(rpm),
        "temp_c": t_c,
        "lubrication": res["lubrication"],
        "preload_n": pre,
        "rotor_mass_kg": float(rotor_mass_kg or 0.0),
        "F_r_total_N": weight_n,
        "bearings": out_bearings,
        "windage": wind,
        "P_bearings_W": p_bearings,
        "P_windage_W": p_wind,
        "P_mech_extra_W": p_bearings + p_wind,
        "M_bearings_Nm": m_bearings,
        "M_windage_Nm": float(wind["M_total_Nm"]) if wind else 0.0,
        "notes": notes,
        "model": ("SKF frictional-moment model (tables 2 and 3) + analytic "
                  "windage — ANALYTIC, not FEM"),
    }
