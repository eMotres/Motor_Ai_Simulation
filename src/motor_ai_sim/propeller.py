"""Propeller catalogue, thrust/torque model and slipstream cooling air.

WHY THIS MODULE EXISTS
======================
The Ø40 motors (die "CIANO14 40 new", configurations L12 6S and L20 12S) are
drone motors: air-cooled only, and the only airflow is the propeller's own
slipstream (owner, 2026-10-05).  So three things that used to be typed in must
come from the propeller instead:

  * the LOAD   — at a given rpm the propeller sets the shaft torque and power;
  * the OPERATING POINT — the rpm where the motor's torque capability meets the
    propeller's torque;
  * the COOLING AIR SPEED — computed from rpm and the chosen propeller, not typed.

The catalogue lives in ``config/propellers/<vendor>/<model>.yaml`` (schema in
``config/propellers/README.md``); on the server ``<shared>/propellers/`` is read
too and wins per id (the device-card / passport-store rule).  Nothing here
writes anything.

THE MODEL
=========
STATIC (hover) coefficients, from bench tests at zero airspeed::

    T   = C_T(n) · ρ · n² · D⁴            thrust  [N]
    P   = C_P(n) · ρ · n³ · D⁵            shaft power [W]
    τ   = P / (2π n)                       shaft torque [N·m]        (n in rev/s)

``C_T`` and ``C_P`` are fitted to the published test points as a power law in
rpm, ``C(n) = C_ref · (n/n_ref)^k``: the exponent is the Reynolds-number trend
(about +0.02 … +0.17 on these props) and is the only reason it is not a constant.
The fit is a Huber-weighted least squares on log C, so a mis-recorded row in a
vendor table (there are some) cannot drag it.  Outside the tested rpm range the
coefficient is HELD at its edge value and the result says ``extrapolated``.

SHAFT POWER IS NEVER TAKEN FROM ELECTRICAL POWER.  T-Motor's "Power (W)" column
is the electrical input of motor + ESC.  C_P comes from the published torque
(τ·ω); where a prop has no torque column the catalogue entry carries an explicit
``power_estimate`` (``estimated: true`` + method) or no power at all, and this
module reports which (``power_data``).  A prop with no usable test data
(``selectable`` False) raises ``PropellerDataError`` instead of guessing.

COOLING AIR (momentum theory)
=============================
The fully developed slipstream of an actuator disc of area A = πD²/4 at thrust T
moves at::

    v_wake = sqrt( 2 T / (ρ A) )            (twice the induced velocity at the disc)

The motor does not sit in the developed wake: it is right behind (or under) the
hub, where the blades carry no load and the contraction has not happened yet.  The
air speed AT THE MOTOR is therefore::

    v_cool = factor · v_wake

``factor`` is an ENGINEERING ASSUMPTION TO BE CALIBRATED (against a hover bench run
with a thermocouple on the winding).  The named positions are
``developed_wake`` 1.0, ``disc_plane`` 0.5 (momentum theory: the induced velocity AT
the disc is half the far-wake velocity) and ``behind_hub`` 0.4 — the default —
which is the disc-plane value times 0.8 for hub blockage.  The film coefficient
that follows is h ∝ v^0.5…0.6 (cross-flow cylinder correlation already used by the
air mode), so a ±50 % error in the factor is about ±25-30 % in h.  Static
conditions only: no forward flight or climb inflow is modelled.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import yaml

log = logging.getLogger(__name__)

G0 = 9.80665                 # m/s²
R_AIR = 287.058              # J/(kg·K) dry air
T_ISA_K = 288.15
P_ISA_PA = 101325.0
LAPSE_K_PER_M = 0.0065

#: Air density at ISA sea level [kg/m³] — the default and the density the
#: catalogue's bench numbers are reduced with (T-Motor states no ambient).
RHO_ISA = P_ISA_PA / (R_AIR * T_ISA_K)

#: Fraction of the highest tested rpm below which test rows are not used in the
#: fit: at 10 % throttle the bench measures a few tenths of a newton and a torque
#: of 0.02 N·m, i.e. quantisation and bench friction, not the propeller.
RPM_FIT_FLOOR_FRACTION = 0.30

#: A torque row is used only when half its last printed digit is at most this
#: fraction of the value (0.01 N·m resolution → torque >= 0.05 N·m).
TORQUE_MAX_QUANT_REL = 0.10

#: The rpm exponent of a fit is kept only when |k| exceeds this many standard
#: errors; otherwise the coefficient is a constant.
SLOPE_SIGNIFICANCE = 2.5

#: Slipstream factor (air speed at the motor / developed-wake speed).  See the
#: module docstring: an engineering assumption to be calibrated.
POSITION_FACTORS: Dict[str, float] = {
    "developed_wake": 1.0,
    "disc_plane": 0.5,
    "behind_hub": 0.4,
}
DEFAULT_POSITION = "behind_hub"
POSITION_NOTES: Dict[str, str] = {
    "developed_wake": "fully developed slipstream, v = sqrt(2T/(rho A)) — an upper bound for a motor body",
    "disc_plane": "momentum theory: induced velocity at the disc is half the far-wake velocity",
    "behind_hub": "disc-plane value x 0.8 for hub blockage — motor right behind/under the hub (engineering assumption, to be calibrated)",
}

_REPO_DIR = Path(__file__).resolve().parents[2] / "config" / "propellers"
#: Overridden by tests; a moved path wins outright (the passport-store rule).
_DIR: Path = _REPO_DIR


class PropellerError(Exception):
    """Base class of every error this module raises on purpose."""


class PropellerNotFound(PropellerError, KeyError):
    """No catalogue entry with that id."""

    def __str__(self) -> str:                      # KeyError would repr() it
        return str(self.args[0]) if self.args else "propeller not found"


class PropellerDataError(PropellerError, ValueError):
    """The entry exists but cannot be used for the requested computation."""


# ---------------------------------------------------------------------------
# Air
# ---------------------------------------------------------------------------

def air_density(temp_c: Optional[float] = None, altitude_m: float = 0.0,
                pressure_pa: Optional[float] = None) -> float:
    """Dry-air density [kg/m³] from temperature, altitude or pressure.

    Defaults are ISA: with no arguments this is 1.225 kg/m³ at sea level.
    ``altitude_m`` sets the ISA pressure (troposphere) and, when ``temp_c`` is
    not given, the ISA temperature too; a given ``temp_c`` is the actual air
    temperature at that altitude.  ``pressure_pa`` overrides the ISA pressure
    (a measured QNH-corrected value, say).  Humidity is ignored (< 1 % at drone
    temperatures).
    """
    h = float(altitude_m or 0.0)
    if not (-500.0 <= h <= 11000.0):
        raise ValueError("altitude_m must be within -500 .. 11000 m (ISA troposphere)")
    if pressure_pa is None:
        p = P_ISA_PA * (1.0 - 2.25577e-5 * h) ** 5.25588
    else:
        p = float(pressure_pa)
        if not (1.0e4 <= p <= 1.2e5):
            raise ValueError("pressure_pa must be within 1e4 .. 1.2e5 Pa")
    t_k = (T_ISA_K - LAPSE_K_PER_M * h) if temp_c is None else float(temp_c) + 273.15
    if t_k < 150.0 or t_k > 400.0:
        raise ValueError("air temperature out of range (-120 .. 125 degC)")
    return p / (R_AIR * t_k)


# ---------------------------------------------------------------------------
# Coefficient fits
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CoeffFit:
    """``C(n) = c_ref · (rpm / rpm_ref)^k`` valid on ``[rpm_min, rpm_max]``."""
    name: str
    c_ref: float
    rpm_ref: float
    k: float
    rpm_min: float
    rpm_max: float
    n_used: int = 0
    n_rejected: int = 0
    rms_rel: float = 0.0          # rms of (fit/measured - 1) over the used points
    max_abs_rel: float = 0.0
    estimated: bool = False
    method: str = "fitted to published test points"
    rel_uncertainty: float = 0.0  # estimated entries: the stated 1-sigma-ish band

    def value(self, rpm: float) -> Tuple[float, bool]:
        """(C, extrapolated).  Held at the edge value outside the tested range."""
        r = abs(float(rpm))
        extrap = r < self.rpm_min or r > self.rpm_max
        r_eff = min(max(r, self.rpm_min), self.rpm_max)
        return self.c_ref * (r_eff / self.rpm_ref) ** self.k, extrap

    def summary(self) -> Dict[str, Any]:
        return {"c_ref": self.c_ref, "rpm_ref": self.rpm_ref, "exponent": self.k,
                "rpm_min": self.rpm_min, "rpm_max": self.rpm_max,
                "n_points_used": self.n_used, "n_points_rejected": self.n_rejected,
                "rms_rel_residual": self.rms_rel, "max_abs_rel_residual": self.max_abs_rel,
                "estimated": self.estimated, "method": self.method,
                "rel_uncertainty": self.rel_uncertainty}


def _fit_power_law(pts: Sequence[Tuple[float, float]], *, name: str) -> Optional[Tuple[CoeffFit, List[bool]]]:
    """Huber-weighted fit of log C = log c_ref + k log(rpm/rpm_ref).

    Returns ``(fit, used_flags)`` or None when there are fewer than 3 points.
    ``used_flags[i]`` is False for points rejected as outliers (> 3.5 robust
    sigma).  The exponent is bounded to [-0.1, 0.4] and forced to 0 when the
    points span less than a 1.3x rpm range (the slope would be noise).
    """
    import numpy as np

    if len(pts) < 3:
        return None
    rpm = np.array([p[0] for p in pts], dtype=float)
    c = np.array([p[1] for p in pts], dtype=float)
    ok = (rpm > 0) & (c > 0) & np.isfinite(c)
    if ok.sum() < 3:
        return None
    rpm_ref = float(np.median(rpm[ok]))
    x = np.log(rpm / rpm_ref)
    y = np.log(np.where(ok, c, 1.0))
    span = float(rpm[ok].max() / rpm[ok].min())
    free_slope = span >= 1.3
    w = np.where(ok, 1.0, 0.0)
    a, b = float(np.log(np.median(c[ok]))), 0.0
    for _ in range(30):
        if free_slope:
            sw = w.sum()
            mx = (w * x).sum() / sw
            my = (w * y).sum() / sw
            sxx = (w * (x - mx) ** 2).sum()
            b = float((w * (x - mx) * (y - my)).sum() / sxx) if sxx > 1e-12 else 0.0
            b = min(max(b, -0.1), 0.4)
            a = float(my - b * mx)
        else:
            b = 0.0
            a = float((w * y).sum() / w.sum())
        r = y - (a + b * x)
        s = max(1.4826 * float(np.median(np.abs(r[ok] - np.median(r[ok])))), 0.01)
        w_new = np.where(ok, np.minimum(1.0, 1.345 * s / np.maximum(np.abs(r), 1e-12)), 0.0)
        if float(np.max(np.abs(w_new - w))) < 1e-6:
            w = w_new
            break
        w = w_new
    r = y - (a + b * x)
    s = max(1.4826 * float(np.median(np.abs(r[ok] - np.median(r[ok])))), 0.01)
    used = ok & (np.abs(r) <= 3.5 * s)
    # refit on the inliers with plain least squares (Huber weights only guided the rejection)
    if used.sum() >= 3:
        if free_slope:
            xm, ym = x[used].mean(), y[used].mean()
            sxx = ((x[used] - xm) ** 2).sum()
            b = float(((x[used] - xm) * (y[used] - ym)).sum() / sxx) if sxx > 1e-12 else 0.0
            b = min(max(b, -0.1), 0.4)
            a = float(ym - b * xm)
        else:
            b = 0.0
            a = float(y[used].mean())
    # A slope that is not clearly different from zero is noise (torque tables are
    # printed to 0.01 N*m): fall back to a constant coefficient.
    if free_slope and used.sum() >= 3:
        xm = x[used].mean()
        sxx = float(((x[used] - xm) ** 2).sum())
        res = y[used] - (a + b * x[used])
        s_res = float(np.sqrt(np.sum(res ** 2) / max(int(used.sum()) - 2, 1)))
        se_b = s_res / math.sqrt(sxx) if sxx > 1e-12 else float("inf")
        if abs(b) < SLOPE_SIGNIFICANCE * se_b:
            b = 0.0
            a = float(y[used].mean())
    fitted = np.exp(a + b * x)
    rel = fitted / c - 1.0
    fit = CoeffFit(name=name, c_ref=float(math.exp(a)), rpm_ref=rpm_ref, k=float(b),
                   rpm_min=float(rpm[used].min()), rpm_max=float(rpm[used].max()),
                   n_used=int(used.sum()), n_rejected=int((ok & ~used).sum()),
                   rms_rel=float(np.sqrt(np.mean(rel[used] ** 2))),
                   max_abs_rel=float(np.max(np.abs(rel[used]))))
    return fit, [bool(u) for u in used]


# ---------------------------------------------------------------------------
# The catalogue entry
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Propeller:
    id: str
    vendor: str
    model: str
    series: str
    status: str
    diameter_m: float                 # the diameter the coefficients and the disc area use
    diameter_nominal_in: float
    pitch_in: Optional[float]
    blades: Optional[int]
    data_quality: str                 # torque_measured | thrust_measured_power_estimated | geometry_only
    ct: Optional[CoeffFit]
    cp: Optional[CoeffFit]
    test_density: float
    raw: Dict[str, Any] = field(repr=False, default_factory=dict)
    fit_points: List[Dict[str, Any]] = field(repr=False, default_factory=list)
    source_path: str = ""

    @property
    def selectable(self) -> bool:
        """True when both C_T and C_P exist (measured or explicitly estimated)."""
        return self.ct is not None and self.cp is not None

    @property
    def power_data(self) -> str:
        """``measured_torque`` | ``estimated`` | ``none`` — how C_P was obtained."""
        if self.cp is None:
            return "none"
        return "estimated" if self.cp.estimated else "measured_torque"

    @property
    def disc_area_m2(self) -> float:
        return math.pi * self.diameter_m ** 2 / 4.0

    @property
    def rpm_range(self) -> Optional[Tuple[float, float]]:
        """The rpm range both coefficients are backed by (None when unusable)."""
        if not self.selectable:
            return None
        lo = max(self.ct.rpm_min, self.cp.rpm_min)
        hi = min(self.ct.rpm_max, self.cp.rpm_max)
        return (lo, hi) if hi > lo else (self.ct.rpm_min, self.ct.rpm_max)


def _require(d: Dict[str, Any], key: str, where: str) -> Any:
    if key not in d or d[key] is None:
        raise PropellerDataError(f"{where}: missing '{key}'")
    return d[key]


def _table_rows(raw: Dict[str, Any]) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    perf = raw.get("performance") or {}
    out = []
    for t in perf.get("tables") or []:
        for r in t.get("rows") or []:
            out.append((t, r))
    return out


def _build(raw: Dict[str, Any], path: str) -> Propeller:
    where = path
    if raw.get("schema") != "propeller-1":
        raise PropellerDataError(f"{where}: unsupported schema {raw.get('schema')!r}")
    pid = str(_require(raw, "id", where))
    g = _require(raw, "geometry", where)
    d_in = float(_require(g, "diameter_in", where))
    d_mm = float(g.get("disc_diameter_mm") or _require(g, "diameter_mm", where))
    if not (50.0 <= d_mm <= 2000.0):
        raise PropellerDataError(f"{where}: implausible diameter {d_mm} mm")
    perf = raw.get("performance") or {}
    dq = str(perf.get("data_quality") or "geometry_only")
    rho_t = float(perf.get("test_density_kg_m3") or RHO_ISA)
    D = d_mm / 1000.0

    # --- C_T and C_P points ---------------------------------------------------
    ct_pts: List[Tuple[float, float]] = []
    cp_pts: List[Tuple[float, float]] = []
    ct_idx: List[Dict[str, Any]] = []
    cp_idx: List[Dict[str, Any]] = []
    rows = [(t, r) for t, r in _table_rows(raw) if t.get("use_in_fit", True)]
    rpm_max_all = max([float(r["rpm"]) for _, r in rows if r.get("rpm")] or [0.0])
    floor = RPM_FIT_FLOOR_FRACTION * rpm_max_all
    for t, r in rows:
        rpm = r.get("rpm")
        if not rpm or float(rpm) < floor:
            continue
        n = float(rpm) / 60.0
        if r.get("thrust_g") is not None:
            T = float(r["thrust_g"]) * G0 / 1000.0
            ct_pts.append((float(rpm), T / (rho_t * n ** 2 * D ** 4)))
            ct_idx.append({"table": t.get("id"), "rpm": float(rpm), "throttle_pct": r.get("thr")})
        tq = r.get("torque_Nm")
        if tq is not None and float(tq) > 0:
            res = float(t.get("torque_resolution_Nm") or 0.01)
            if 0.5 * res / float(tq) <= TORQUE_MAX_QUANT_REL:
                P = float(tq) * 2.0 * math.pi * n
                cp_pts.append((float(rpm), P / (rho_t * n ** 3 * D ** 5)))
                cp_idx.append({"table": t.get("id"), "rpm": float(rpm), "throttle_pct": r.get("thr")})

    ct_fit = cp_fit = None
    fit_points: List[Dict[str, Any]] = []
    if dq != "geometry_only":
        got = _fit_power_law(ct_pts, name="ct")
        if got:
            ct_fit, used = got
            for i, (p, u) in enumerate(zip(ct_pts, used)):
                fit_points.append({**ct_idx[i], "coefficient": "ct", "value": p[1], "used": u})
        got = _fit_power_law(cp_pts, name="cp")
        if got:
            cp_fit, used = got
            for i, (p, u) in enumerate(zip(cp_pts, used)):
                fit_points.append({**cp_idx[i], "coefficient": "cp", "value": p[1], "used": u})
        est = perf.get("power_estimate")
        if cp_fit is None and isinstance(est, dict) and est.get("estimated") is True and ct_fit is not None:
            cp_fit = CoeffFit(
                name="cp", c_ref=float(_require(est, "cp_ref", where + " power_estimate")),
                rpm_ref=ct_fit.rpm_ref, k=float(est.get("rpm_exponent") or 0.0),
                rpm_min=ct_fit.rpm_min, rpm_max=ct_fit.rpm_max, estimated=True,
                method=str(est.get("method") or "estimated"),
                rel_uncertainty=max(2.0 * float(est.get("rel_spread") or 0.0), 0.15))
    if dq == "torque_measured" and (ct_fit is None or cp_fit is None):
        raise PropellerDataError(f"{where}: data_quality torque_measured but too few usable points")

    return Propeller(
        id=pid, vendor=str(raw.get("vendor") or ""), model=str(raw.get("model") or pid),
        series=str(raw.get("series") or ""), status=str(raw.get("status") or "current"),
        diameter_m=D, diameter_nominal_in=d_in,
        pitch_in=(None if g.get("pitch_in") is None else float(g["pitch_in"])),
        blades=(None if g.get("blades") is None else int(g["blades"])),
        data_quality=dq, ct=ct_fit, cp=cp_fit, test_density=rho_t, raw=raw,
        fit_points=fit_points, source_path=path)


# ---------------------------------------------------------------------------
# Catalogue loading (repo + server layer, mtime-cached)
# ---------------------------------------------------------------------------

_cache: Dict[Tuple, Dict[str, Propeller]] = {}


def catalog_dirs() -> List[Path]:
    """Repo folder first, then ``<shared>/propellers`` (which wins per id)."""
    dirs = [_DIR]
    if _DIR == _REPO_DIR:
        try:
            from motor_ai_sim.workspace import shared_root
            p = Path(str(shared_root())) / "propellers"
            if p.is_dir() and p.resolve() != _REPO_DIR.resolve():
                dirs.append(p)
        except Exception:                                   # noqa: BLE001
            pass
    return dirs


def _files() -> List[Path]:
    out: List[Path] = []
    for d in catalog_dirs():
        if d.is_dir():
            out += sorted(p for p in d.rglob("*.yaml") if not p.name.startswith("_"))
    return out


def load_catalog() -> Dict[str, Propeller]:
    """``{id: Propeller}`` for every readable entry; an unreadable file is logged
    and skipped (one bad vendor file must not take the catalogue down)."""
    files = _files()
    key = tuple((str(p), p.stat().st_mtime_ns) for p in files)
    hit = _cache.get(key)
    if hit is not None:
        return hit
    out: Dict[str, Propeller] = {}
    for p in files:
        try:
            raw = yaml.safe_load(p.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise PropellerDataError(f"{p}: not a mapping")
            prop = _build(raw, str(p))
            out[prop.id] = prop            # later directory (server) overrides
        except Exception as exc:                            # noqa: BLE001
            log.warning("propeller catalogue: %s skipped: %s", p, exc)
    _cache.clear()
    _cache[key] = out
    return out


def list_propellers() -> List[Propeller]:
    """All entries sorted by nominal diameter, then model."""
    return sorted(load_catalog().values(), key=lambda p: (p.diameter_nominal_in, p.model))


def get_propeller(prop_id: str) -> Propeller:
    p = load_catalog().get(str(prop_id))
    if p is None:
        raise PropellerNotFound(f"unknown propeller id: {prop_id!r}")
    return p


def _need_model(prop: Propeller) -> None:
    if not prop.selectable:
        raise PropellerDataError(
            f"{prop.id} ({prop.model}) has no usable thrust/power test data "
            f"(data_quality={prop.data_quality}); it is listed for reference only")


# ---------------------------------------------------------------------------
# Thrust, torque, power
# ---------------------------------------------------------------------------

def coefficients(prop: Propeller, rpm: float) -> Dict[str, Any]:
    """``{ct, cp, extrapolated, power_estimated}`` at ``rpm`` (static)."""
    _need_model(prop)
    ct, e1 = prop.ct.value(rpm)
    cp, e2 = prop.cp.value(rpm)
    return {"ct": ct, "cp": cp, "extrapolated": bool(e1 or e2),
            "power_estimated": prop.cp.estimated}


def thrust_N(prop: Propeller, rpm: float, rho: float = RHO_ISA) -> float:
    """Static thrust [N] at ``rpm``."""
    _need_model(prop)
    n = abs(float(rpm)) / 60.0
    if n == 0.0:
        return 0.0
    ct, _ = prop.ct.value(rpm)
    return ct * float(rho) * n ** 2 * prop.diameter_m ** 4


def shaft_power_W(prop: Propeller, rpm: float, rho: float = RHO_ISA) -> float:
    """Shaft power [W] the propeller absorbs at ``rpm`` (mechanical, not electrical)."""
    _need_model(prop)
    n = abs(float(rpm)) / 60.0
    if n == 0.0:
        return 0.0
    cp, _ = prop.cp.value(rpm)
    return cp * float(rho) * n ** 3 * prop.diameter_m ** 5


def torque_Nm(prop: Propeller, rpm: float, rho: float = RHO_ISA) -> float:
    """Shaft torque [N·m] the propeller needs at ``rpm``."""
    n = abs(float(rpm)) / 60.0
    if n == 0.0:
        _need_model(prop)
        return 0.0
    return shaft_power_W(prop, rpm, rho) / (2.0 * math.pi * n)


# ---------------------------------------------------------------------------
# Slipstream cooling air
# ---------------------------------------------------------------------------

def wake_velocity_ms(prop: Propeller, rpm: float, rho: float = RHO_ISA) -> float:
    """Developed-slipstream speed ``sqrt(2T/(rho A))`` [m/s] (momentum theory)."""
    T = thrust_N(prop, rpm, rho)
    return math.sqrt(max(2.0 * T / (float(rho) * prop.disc_area_m2), 0.0))


def position_factor(position: Optional[str] = None, factor: Optional[float] = None) -> Tuple[float, str]:
    """(factor, label).  An explicit ``factor`` (0 < f <= 1.5) wins over a name."""
    if factor is not None:
        f = float(factor)
        if not (0.0 < f <= 1.5):
            raise ValueError("slipstream factor must be in (0, 1.5]")
        return f, "custom"
    name = str(position or DEFAULT_POSITION).strip().lower()
    if name not in POSITION_FACTORS:
        raise ValueError(f"unknown motor position {position!r}; use one of {sorted(POSITION_FACTORS)}")
    return POSITION_FACTORS[name], name


def cooling_air_speed_ms(prop: Propeller, rpm: float, rho: float = RHO_ISA,
                         position: Optional[str] = None,
                         factor: Optional[float] = None) -> float:
    """Air speed over the motor [m/s] = factor x developed-wake speed.

    ``position`` picks a documented factor (default ``behind_hub``, 0.4);
    ``factor`` overrides it.  The factor is an engineering assumption to be
    calibrated — see the module docstring.
    """
    f, _ = position_factor(position, factor)
    return f * wake_velocity_ms(prop, rpm, rho)


def operating_point(prop: Propeller, rpm: float, rho: float = RHO_ISA,
                    position: Optional[str] = None,
                    factor: Optional[float] = None) -> Dict[str, Any]:
    """Everything the propeller says at ``rpm``: thrust, torque, shaft power,
    wake and cooling air speed, with the assumptions that produced them."""
    _need_model(prop)
    f, label = position_factor(position, factor)
    co = coefficients(prop, rpm)
    T = thrust_N(prop, rpm, rho)
    vw = wake_velocity_ms(prop, rpm, rho)
    return {
        "propeller_id": prop.id, "rpm": float(rpm), "rho_kg_m3": float(rho),
        "thrust_N": T, "torque_Nm": torque_Nm(prop, rpm, rho),
        "shaft_power_W": shaft_power_W(prop, rpm, rho),
        "ct": co["ct"], "cp": co["cp"],
        "disc_area_m2": prop.disc_area_m2,
        "wake_velocity_ms": vw,
        "slipstream_position": label, "slipstream_factor": f,
        "air_speed_ms": f * vw,
        "extrapolated": co["extrapolated"], "power_estimated": co["power_estimated"],
        "rpm_range_tested": list(prop.rpm_range) if prop.rpm_range else None,
        "assumptions": [
            "static (hover) coefficients, no inflow",
            "wake speed from momentum theory sqrt(2T/(rho A))",
            "slipstream factor %.2f (%s): %s" % (f, label, POSITION_NOTES.get(label, "caller-supplied factor")),
        ] + (["C_P is an ESTIMATE (no published torque for this prop)"] if co["power_estimated"] else []),
    }


def air_speed_for_thermal(propeller_id: str, rpm: float, ambient_c: float = 15.0,
                          altitude_m: float = 0.0, position: Optional[str] = None,
                          factor: Optional[float] = None) -> Dict[str, Any]:
    """The hook the thermal model uses: ``air_speed_mps`` for the housing film.

    Density comes from the thermal model's own ambient temperature and the
    altitude (ISA pressure), so a hot day lowers both the thrust-driven wake and
    the cooling.  Returns the ``operating_point`` dict (key ``air_speed_mps`` is
    the number to hand to ``cooling_models.outer_air``).
    """
    prop = get_propeller(propeller_id)
    rho = air_density(temp_c=ambient_c, altitude_m=altitude_m)
    op = operating_point(prop, rpm, rho, position=position, factor=factor)
    op["air_speed_mps"] = op["air_speed_ms"]
    op["ambient_c"] = float(ambient_c)
    op["altitude_m"] = float(altitude_m)
    return op


# ---------------------------------------------------------------------------
# Operating point from the propeller
# ---------------------------------------------------------------------------

def rpm_for_torque(prop: Propeller, torque_nm: float, rho: float = RHO_ISA,
                   rpm_hi: Optional[float] = None) -> float:
    """The rpm at which the propeller absorbs ``torque_nm`` [N·m] (bisection).

    The propeller torque is monotone in rpm (C_P is held or slowly rising), so
    the root is unique.  Raises ``PropellerDataError`` if ``torque_nm`` exceeds
    what the propeller absorbs at ``rpm_hi`` (default 3 x the tested maximum).
    """
    _need_model(prop)
    tq = float(torque_nm)
    if tq <= 0.0:
        return 0.0
    hi = float(rpm_hi) if rpm_hi else 3.0 * max(prop.ct.rpm_max, prop.cp.rpm_max)
    if torque_Nm(prop, hi, rho) < tq:
        raise PropellerDataError(
            f"torque {tq:.4g} N*m is more than {prop.id} absorbs at {hi:.0f} rpm")
    lo = 0.0
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if torque_Nm(prop, mid, rho) < tq:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def _interp(xs: Sequence[float], ys: Sequence[float], x: float) -> float:
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    lo, hi = 0, len(xs) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if xs[mid] <= x:
            lo = mid
        else:
            hi = mid
    t = (x - xs[lo]) / (xs[hi] - xs[lo])
    return ys[lo] + t * (ys[hi] - ys[lo])


def equilibrium_rpm(prop: Propeller, motor_rpm: Sequence[float], motor_torque_Nm: Sequence[float],
                    rho: float = RHO_ISA, position: Optional[str] = None,
                    factor: Optional[float] = None) -> Dict[str, Any]:
    """Where the motor's torque capability meets the propeller's torque.

    ``motor_rpm`` / ``motor_torque_Nm`` is the motor's torque-speed capability
    (the passport's feasible envelope: the most torque it can give at each rpm).
    Between the points it is interpolated linearly; below the first point the
    first torque is held.  The operating point is the HIGHEST rpm at which the
    motor still has at least the propeller's torque (full throttle).

    ``limited_by`` says what stopped the speed:

      * ``torque_equilibrium`` — a genuine crossing inside the table;
      * ``motor_speed_limit`` — the motor out-torques the propeller up to the
        last tabulated rpm, so the speed is capped by the table (voltage /
        field-weakening limit), not by the propeller;
      * ``no_motor_torque`` — the motor torque is zero or negative at every
        tabulated rpm: nothing turns (rpm 0).

    The propeller's torque is zero at rest, so a motor with any positive torque
    always starts; the crossing is where the (rising) propeller torque overtakes
    the (usually falling) motor curve.

    The result is the ``operating_point`` dict plus those keys.
    """
    _need_model(prop)
    pts = sorted(zip((float(r) for r in motor_rpm), (float(t) for t in motor_torque_Nm)))
    if len(pts) < 2:
        raise ValueError("motor torque-speed capability needs at least 2 points")
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    if xs[0] < 0 or any(y < 0 for y in ys):
        raise ValueError("motor rpm and torque must be non-negative")

    def f(r: float) -> float:
        return _interp(xs, ys, r) - torque_Nm(prop, r, rho)

    n_grid = 4000
    top = xs[-1]
    if max(ys) <= 0.0:
        limited = "no_motor_torque"
        r_star = 0.0
    else:
        limited = "motor_speed_limit"
        r_star = top
        r_prev_ok = 0.0
        for i in range(1, n_grid + 1):
            r = top * i / n_grid
            fv = f(r)
            if fv < 0:
                a, b = r_prev_ok, r
                for _ in range(60):
                    mid = 0.5 * (a + b)
                    if f(mid) >= 0:
                        a = mid
                    else:
                        b = mid
                r_star = 0.5 * (a + b)
                limited = "torque_equilibrium"
                break
            r_prev_ok = r
    op = operating_point(prop, r_star, rho, position=position, factor=factor) if r_star > 0 else {
        "propeller_id": prop.id, "rpm": 0.0, "rho_kg_m3": float(rho), "thrust_N": 0.0,
        "torque_Nm": 0.0, "shaft_power_W": 0.0, "air_speed_ms": 0.0, "wake_velocity_ms": 0.0,
        "extrapolated": False, "power_estimated": prop.cp.estimated,
        "rpm_range_tested": list(prop.rpm_range) if prop.rpm_range else None, "assumptions": []}
    op["limited_by"] = limited
    op["motor_torque_at_point_Nm"] = _interp(xs, ys, r_star)
    return op


# ---------------------------------------------------------------------------
# Serialisation for the API
# ---------------------------------------------------------------------------

def summary(prop: Propeller) -> Dict[str, Any]:
    """The list-view record."""
    g = prop.raw.get("geometry") or {}
    return {
        "id": prop.id, "vendor": prop.vendor, "model": prop.model, "series": prop.series,
        "status": prop.status,
        "diameter_in": prop.diameter_nominal_in, "diameter_mm": g.get("diameter_mm"),
        "disc_diameter_mm": round(prop.diameter_m * 1000.0, 1),
        "pitch_in": prop.pitch_in, "blades": prop.blades,
        "material": prop.raw.get("material"),
        "mass_g": prop.raw.get("mass_g"),
        "mass_single_blade_g": prop.raw.get("mass_single_blade_g"),
        "product_url": prop.raw.get("product_url"),
        "data_quality": prop.data_quality, "power_data": prop.power_data,
        "selectable": prop.selectable,
        "rpm_range_tested": list(prop.rpm_range) if prop.rpm_range else None,
        "published_limits": prop.raw.get("published_limits") or {},
    }


def curves(prop: Propeller, rho: float = RHO_ISA, n: int = 41,
           position: Optional[str] = None, factor: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Fitted curves over the tested rpm range (None for a reference-only prop)."""
    if not prop.selectable:
        return None
    lo, hi = prop.rpm_range
    rpms = [lo + (hi - lo) * i / (n - 1) for i in range(n)]
    f, label = position_factor(position, factor)
    return {
        "rho_kg_m3": float(rho), "rpm": rpms,
        "thrust_N": [thrust_N(prop, r, rho) for r in rpms],
        "torque_Nm": [torque_Nm(prop, r, rho) for r in rpms],
        "shaft_power_W": [shaft_power_W(prop, r, rho) for r in rpms],
        "wake_velocity_ms": [wake_velocity_ms(prop, r, rho) for r in rpms],
        "air_speed_ms": [f * wake_velocity_ms(prop, r, rho) for r in rpms],
        "slipstream_position": label, "slipstream_factor": f,
    }


def detail(prop: Propeller, rho: float = RHO_ISA) -> Dict[str, Any]:
    """The full record: the catalogue entry as published, the fits, the curves."""
    out = summary(prop)
    out["geometry"] = prop.raw.get("geometry")
    out["hub"] = prop.raw.get("hub")
    out["source_urls"] = prop.raw.get("source_urls")
    out["accessed"] = prop.raw.get("accessed")
    perf = prop.raw.get("performance") or {}
    out["performance"] = {
        "data_quality": prop.data_quality, "static_hover_only": True,
        "test_density_kg_m3": prop.test_density,
        "power_estimate": perf.get("power_estimate"),
        "tables": perf.get("tables") or [],
    }
    out["fit"] = {"ct": prop.ct.summary() if prop.ct else None,
                  "cp": prop.cp.summary() if prop.cp else None,
                  "points": prop.fit_points}
    out["curves"] = curves(prop, rho)
    out["slipstream"] = {"default_position": DEFAULT_POSITION, "factors": POSITION_FACTORS,
                         "notes": POSITION_NOTES}
    out["notes"] = prop.raw.get("notes") or []
    return out
