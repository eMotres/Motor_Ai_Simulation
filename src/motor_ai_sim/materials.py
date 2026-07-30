"""Materials library loader for electric motor EM simulation.

Loads material data from config/materials_library.yaml (extracted from
Ansys Maxwell PersonalLib) and exposes typed dataclasses for use in the
simulation pipeline.

Usage
-----
>>> from motor_ai_sim.materials import get_material, list_materials
>>> steel = get_material('steel', 'JFE_10JNEX900')
>>> steel.sigma, steel.stacking_factor
(2000000.0, 0.9)
>>> steel.bh_curve   # list of [H, B] pairs
>>> magnet = get_material('magnet', 'Arnold_N52UH_100C')
>>> magnet.Br, magnet.mu_rec
(1.30, 1.052)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field, fields as dataclass_fields
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple, Literal

import yaml

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path
# ---------------------------------------------------------------------------
_LIB_PATH = Path(__file__).parent.parent.parent / "config" / "materials_library.yaml"

# Module-level cache
_library: Optional[dict] = None
_lib_mtime: float = 0.0      # mtime of the YAML the cached copy was parsed from
_lib_checked: float = 0.0    # monotonic clock of the last mtime probe
# The library used to be read exactly ONCE per process, so a material added or
# edited on disk stayed invisible — to the API *and* to the in-process solver —
# until a restart, which silently made a run use the wrong magnet.  The cache now
# follows the file.  The mtime is probed at most this often so the solver's
# per-element lookups don't turn into a syscall storm.
_MTIME_PROBE_S = 1.0


def _load() -> dict:
    """Load the materials library YAML, re-reading it when the file changes."""
    global _library, _lib_mtime, _lib_checked
    now = time.monotonic()
    if _library is not None and (now - _lib_checked) < _MTIME_PROBE_S:
        return _library
    if not _LIB_PATH.exists():
        if _library is not None:
            return _library          # file vanished mid-session: keep serving it
        raise FileNotFoundError(f"Materials library not found: {_LIB_PATH}")
    _lib_checked = now
    mtime = _LIB_PATH.stat().st_mtime
    if _library is not None and mtime == _lib_mtime:
        return _library
    try:
        with _LIB_PATH.open("r", encoding="utf-8") as f:
            parsed = yaml.safe_load(f)
    except Exception as e:                      # noqa: BLE001
        # Half-written or malformed edit: keep the last good copy rather than
        # taking the whole solver down mid-run.  The next probe retries.
        if _library is not None:
            _log.warning("materials_library.yaml unreadable (%s) — keeping the "
                         "previously loaded copy", e)
            return _library
        raise
    _library = parsed
    _lib_mtime = mtime
    _bertotti_fit_cache.clear()      # derived fits belong to the old file
    return _library


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

BHPoint = Tuple[float, float]       # (H [A/m], B [T])
CoreLossCurve = List[Tuple[float, float]]  # (B [T], P [unit])


@dataclass
class SteelMaterial:
    """Electrical steel / SMC core material."""
    name: str
    description: str = ""
    form: str = "laminated"          # 'laminated' | 'solid'
    sigma: float = 0.0               # S/m
    density: float = 7650.0          # kg/m³
    thermal_conductivity: Optional[float] = None   # W/(m·K)
    specific_heat: Optional[float] = None          # J/(kg·K)
    stacking_factor: float = 0.97

    # Bertotti model
    core_loss_model: str = "bertotti"
    core_loss_kh: float = 0.0        # W/(m³·Hz·T²)
    core_loss_kc: float = 0.0        # W/(m³·Hz²·T²)
    core_loss_ke: float = 0.0        # W/(m³·Hz^1.5·T^1.5)
    # Unit for the measured core_loss_curves tabular data (NOT for kh/kc/ke).
    # kh/kc/ke always give W/m³ (see header comments in materials_library.yaml).
    core_loss_curve_unit: str = "w_per_kg"

    bh_curve: List[BHPoint] = field(default_factory=list)
    core_loss_curves: Dict[str, CoreLossCurve] = field(default_factory=dict)

    @property
    def mu_r_initial(self) -> float:
        """Approximate initial relative permeability from first BH point."""
        mu0 = 4e-7 * 3.14159265
        if len(self.bh_curve) >= 2:
            h, b = self.bh_curve[1]
            if h > 0:
                return b / (mu0 * h)
        return 1000.0

    def core_loss_w_per_m3(self, f_hz: float, b_peak_t: float) -> float:
        """Bertotti core loss in **W/m³**.

        P = kh·f·B² + kc·f²·B² + ke·f^1.5·B^1.5

        Coefficients kh/kc/ke are in W/(m³·Hz·T²), W/(m³·Hz²·T²),
        W/(m³·Hz^1.5·T^1.5) respectively.
        """
        return (
            self.core_loss_kh * f_hz * b_peak_t ** 2
            + self.core_loss_kc * f_hz ** 2 * b_peak_t ** 2
            + self.core_loss_ke * f_hz ** 1.5 * b_peak_t ** 1.5
        )

    def core_loss_w_per_kg(self, f_hz: float, b_peak_t: float) -> float:
        """Bertotti core loss in **W/kg** (divides by density)."""
        if self.density <= 0:
            raise ValueError("Density must be > 0 to convert W/m³ → W/kg")
        return self.core_loss_w_per_m3(f_hz, b_peak_t) / self.density

    # Keep a convenience alias matching the old name
    def core_loss(self, f_hz: float, b_peak_t: float) -> float:
        """Alias for core_loss_w_per_m3 (backward compat)."""
        return self.core_loss_w_per_m3(f_hz, b_peak_t)


@dataclass
class MagnetMaterial:
    """Permanent magnet material."""
    name: str
    description: str = ""
    Br: float = 0.0        # Remanence [T]
    Hc: float = 0.0        # Coercivity [A/m]
    mu_rec: float = 1.0    # Recoil permeability (dimensionless)
    sigma: float = 0.0     # S/m (for eddy-current loss)
    density: float = 7500.0  # kg/m³
    thermal_conductivity: Optional[float] = None   # W/(m·K)  (NdFeB ≈ 8)
    specific_heat: Optional[float] = None           # J/(kg·K) (NdFeB ≈ 460)

    bh_curve: List[BHPoint] = field(default_factory=list)

    @property
    def energy_product_kj_m3(self) -> float:
        """Maximum energy product BHmax ≈ Br·Hc/4  [kJ/m³]."""
        return self.Br * self.Hc / 4 / 1000


@dataclass
class ConductorMaterial:
    """Winding conductor (copper / aluminium)."""
    name: str
    description: str = ""
    sigma: float = 58e6     # S/m
    density: float = 8960.0  # kg/m³
    thermal_conductivity: Optional[float] = None  # W/(m·K)
    specific_heat: Optional[float] = None         # J/(kg·K)
    thermal_alpha: Optional[float] = None         # 1/K  (temperature coeff of resistance)
    wire_width_mm: Optional[float] = None         # for rectangular wire
    wire_height_mm: Optional[float] = None

    @property
    def resistivity(self) -> float:
        """Electrical resistivity [Ω·m]."""
        return 1.0 / self.sigma if self.sigma > 0 else float("inf")


@dataclass
class InsulatorMaterial:
    """Electrical insulator / dielectric (slot liner, wire enamel).

    EM-inert (sigma≈0, mu_r≈1 → acts like air in the magnetic solve); matters for
    the thermal model (heat path copper→iron) and for mass/cost.
    """
    name: str
    description: str = ""
    sigma: float = 0.0                              # S/m  (≈0, dielectric)
    density: float = 1400.0                         # kg/m³
    thermal_conductivity: Optional[float] = None    # W/(m·K)
    specific_heat: Optional[float] = None           # J/(kg·K)
    mu_r: float = 1.0                               # relative permeability (~1)


@dataclass
class CoolantMaterial:
    """Cooling working fluid (liquid coolant or air) for the thermal model.

    EM-inert (sigma≈0, mu_r≈1).  Carries the thermophysical properties consumed
    by the cooling boundary condition in the thermal solver: the convection
    coefficient h(v) (air cross-flow) and the coolant energy balance
    T_out = T_in + P/(ṁ·cp) (liquid).  This is the single source of truth — the
    cooling model reads rho/cp/k/nu/Pr from here, no hard-coded fluid tables.
    """
    name: str
    description: str = ""
    phase: str = "liquid"                           # 'liquid' | 'gas'
    density: float = 1000.0                         # rho  kg/m³
    specific_heat: float = 4186.0                   # cp   J/(kg·K)
    thermal_conductivity: float = 0.60              # k    W/(m·K)
    kinematic_viscosity: float = 1.0e-6             # nu   m²/s
    prandtl: float = 7.0                            # Pr   (dimensionless)
    sigma: float = 0.0                              # S/m  (EM-inert)
    mu_r: float = 1.0                               # relative permeability (~1)

    @property
    def props_tuple(self) -> Tuple[float, float, float, float, float]:
        """(rho, cp, k, nu, Pr) — the tuple consumed by the cooling BC."""
        return (self.density, self.specific_heat, self.thermal_conductivity,
                self.kinematic_viscosity, self.prandtl)


# ---------------------------------------------------------------------------
# Internal parsers
# ---------------------------------------------------------------------------

def _parse_steel(name: str, raw: dict) -> SteelMaterial:
    bh = [tuple(p) for p in raw.get("bh_curve", [])]
    # Sort frequencies numerically ("50Hz" → 50, "1000Hz" → 1000, …)
    # so JSON response preserves ascending order for the frontend.
    raw_curves = raw.get("core_loss_curves", {})
    sorted_freq_keys = sorted(raw_curves.keys(), key=lambda k: int(k.replace("Hz", "")))
    cls_curves = {k: [tuple(p) for p in raw_curves[k]] for k in sorted_freq_keys}

    return SteelMaterial(
        name=name,
        description=raw.get("description", ""),
        form=raw.get("form", "laminated"),
        sigma=float(raw.get("sigma") or 0),
        density=float(raw.get("density") or 7650),
        thermal_conductivity=raw.get("thermal_conductivity"),
        specific_heat=raw.get("specific_heat"),
        stacking_factor=float(raw.get("stacking_factor", 0.97)),
        core_loss_model=raw.get("core_loss_model", "bertotti"),
        core_loss_kh=float(raw.get("core_loss_kh") or 0),
        core_loss_kc=float(raw.get("core_loss_kc") or 0),
        core_loss_ke=float(raw.get("core_loss_ke") or 0),
        core_loss_curve_unit=raw.get("core_loss_curve_unit", "w_per_kg"),
        bh_curve=bh,
        core_loss_curves=cls_curves,
    )


def _parse_magnet(name: str, raw: dict) -> MagnetMaterial:
    bh = [tuple(p) for p in raw.get("bh_curve", [])]
    return MagnetMaterial(
        name=name,
        description=raw.get("description", ""),
        Br=float(raw.get("Br") or 0),
        Hc=float(raw.get("Hc") or 0),
        mu_rec=float(raw.get("mu_rec") or 1),
        sigma=float(raw.get("sigma") or 0),
        density=float(raw.get("density") or 7500),
        thermal_conductivity=raw.get("thermal_conductivity"),
        specific_heat=raw.get("specific_heat"),
        bh_curve=bh,
    )


def _parse_conductor(name: str, raw: dict) -> ConductorMaterial:
    return ConductorMaterial(
        name=name,
        description=raw.get("description", ""),
        sigma=float(raw.get("sigma") or 58e6),
        density=float(raw.get("density") or 8960),
        thermal_conductivity=raw.get("thermal_conductivity"),
        specific_heat=raw.get("specific_heat"),
        thermal_alpha=raw.get("thermal_alpha"),
        wire_width_mm=raw.get("wire_width_mm"),
        wire_height_mm=raw.get("wire_height_mm"),
    )


def _parse_insulator(name: str, raw: dict) -> InsulatorMaterial:
    return InsulatorMaterial(
        name=name,
        description=raw.get("description", ""),
        sigma=float(raw.get("sigma") or 0.0),
        density=float(raw.get("density") or 1400),
        thermal_conductivity=raw.get("thermal_conductivity"),
        specific_heat=raw.get("specific_heat"),
        mu_r=float(raw.get("permeability", 1.0) or 1.0),
    )


def _parse_coolant(name: str, raw: dict) -> CoolantMaterial:
    return CoolantMaterial(
        name=name,
        description=raw.get("description", ""),
        phase=raw.get("phase", "liquid"),
        density=float(raw.get("density") or 1000.0),
        specific_heat=float(raw.get("specific_heat") or 4186.0),
        thermal_conductivity=float(raw.get("thermal_conductivity") or 0.60),
        kinematic_viscosity=float(raw.get("kinematic_viscosity") or 1.0e-6),
        prandtl=float(raw.get("prandtl") or 7.0),
        sigma=float(raw.get("sigma") or 0.0),
        mu_r=float(raw.get("permeability", 1.0) or 1.0),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

Category = Literal["steel", "magnet", "conductor", "insulator", "coolant"]

_PARSERS = {
    "steel": _parse_steel,
    "magnet": _parse_magnet,
    "conductor": _parse_conductor,
    "insulator": _parse_insulator,
    "coolant": _parse_coolant,
}

_TYPE_MAP = {
    "steel": SteelMaterial,
    "magnet": MagnetMaterial,
    "conductor": ConductorMaterial,
    "insulator": InsulatorMaterial,
    "coolant": CoolantMaterial,
}


def list_materials(category: Optional[Category] = None) -> Dict[str, List[str]]:
    """Return available material names.

    Parameters
    ----------
    category:
        If given, returns only that category.  Otherwise returns all.

    Returns
    -------
    dict mapping category → list of material names.

    Example
    -------
    >>> list_materials('steel')
    {'steel': ['Somaloy_700HR_5P', 'VACODUR_49_0p20mm_390MPa', ...]}
    """
    lib = _load()
    if category:
        return {category: list(lib.get(category, {}).keys())}
    return {cat: list(lib.get(cat, {}).keys())
            for cat in ("steel", "magnet", "conductor", "insulator", "coolant")}


_DATACLASS_BY_CAT = {
    "steel": SteelMaterial, "magnet": MagnetMaterial, "conductor": ConductorMaterial,
    "insulator": InsulatorMaterial, "coolant": CoolantMaterial,
}


def material_from_dict(category: Category, name: str, d: dict):
    """Build a material dataclass from a GET-library-format dict (the shape served
    by /api/materials/library — i.e. the admin-managed GLOBAL layer). Robust to
    extra/missing keys; pure properties (resistivity, energy_product, mu_r_initial)
    are not fields and are ignored. core_loss_curves (display-only) is dropped — the
    solver uses the Bertotti kh/kc/ke + bh_curve."""
    cls = _DATACLASS_BY_CAT[category]
    names = {f.name for f in dataclass_fields(cls)}
    kwargs: dict = {}
    for k, v in (d or {}).items():
        if k == "name" or k == "core_loss_curves" or k not in names:
            continue
        if k == "bh_curve" and isinstance(v, list):
            kwargs[k] = [(float(p[0]), float(p[1])) for p in v
                         if isinstance(p, (list, tuple)) and len(p) >= 2]
        else:
            kwargs[k] = v
    return cls(name=name, **kwargs)


def get_material(
    category: Category,
    name: str,
) -> SteelMaterial | MagnetMaterial | ConductorMaterial | InsulatorMaterial:
    """Retrieve a material by category and name.

    Parameters
    ----------
    category : 'steel' | 'magnet' | 'conductor' | 'insulator'
    name : material key as it appears in materials_library.yaml

    Raises
    ------
    KeyError if the material is not found.

    Example
    -------
    >>> m = get_material('steel', 'JFE_10JNEX900')
    >>> m.stacking_factor
    0.9
    """
    lib = _load()
    cat_data = lib.get(category)
    if cat_data is None:
        raise KeyError(f"Unknown category '{category}'. Valid: steel, magnet, conductor")
    # Admin-managed GLOBAL layer (Firestore) overrides / extends the built-in YAML,
    # so an edited built-in or a newly-added shared material resolves everywhere the
    # solver looks up materials. Best-effort: any problem (no SDK, malformed doc)
    # falls through to the built-in library and never breaks resolution.
    try:
        from motor_ai_sim import materials_store
        gdoc = materials_store.global_material(category, name)
        if gdoc is not None:
            return material_from_dict(category, name, gdoc)
    except Exception:  # noqa: BLE001
        pass
    raw = cat_data.get(name)
    if raw is None:
        available = list(cat_data.keys())
        raise KeyError(f"Material '{name}' not found in '{category}'. Available: {available}")
    return _PARSERS[category](name, raw)


class UnknownMaterialError(ValueError):
    """An assigned material name that resolves to nothing usable.

    Raised where the ASSIGNMENT is read, not where a property is wanted, so a
    typo'd or stale name fails the request instead of quietly changing the
    physics.  Measured cost of the silent path it replaces: a config holding
    ``magnet: N42SH`` (not in the library) logged one WARNING, fell back to the
    analytic magnet — no BH curve and, crucially, no demag knee — and returned
    an EMPTY demag map with the shaft eddy loss at 63.9 W instead of 7.0 W, a
    factor 9 (docs/SOLVER_TRIALS_2026-07-30.md F6).  Every number in that run
    looked plausible.
    """


# Which library categories a motor part's material may legitimately come from.
# A part not listed here is checked against every category (the name just has to
# exist somewhere).
PART_CATEGORIES: Dict[str, tuple] = {
    "stator_core":     ("steel",),
    "rotor_core":      ("steel",),
    "magnet":          ("magnet",),
    "slot":            ("conductor",),
    # The shaft is aluminium on this machine and solid steel on others.
    "shaft":           ("conductor", "steel"),
    "slot_insulation": ("insulator",),
    "wire_insulation": ("insulator",),
    "air_gap":         ("coolant",),
    "in_band":         ("coolant",),
    "out_band":        ("coolant",),
}

_ALL_CATEGORIES = ("steel", "magnet", "conductor", "insulator", "coolant")


def resolve_assigned(part: str, name: str):
    """The material assigned to ``part``, or raise ``UnknownMaterialError``.

    Searches the categories the part may legitimately use (``PART_CATEGORIES``),
    falling back to every category for an unknown part key.
    """
    cats = PART_CATEGORIES.get(part) or _ALL_CATEGORIES
    for cat in cats:
        try:
            return get_material(cat, name)  # type: ignore[arg-type]
        except Exception:  # noqa: BLE001 - wrong category / missing entry
            continue
    # Not where it belongs. Say whether it exists at all, because "typo" and
    # "assigned to the wrong part" need different fixes.
    elsewhere = [c for c in _ALL_CATEGORIES
                 if c not in cats and name in list_materials(c).get(c, [])]
    if elsewhere:
        raise UnknownMaterialError(
            f"material {name!r} is assigned to {part!r}, which takes a "
            f"{'/'.join(cats)} material, but {name!r} exists only in "
            f"{'/'.join(elsewhere)}")
    avail = sorted({n for c in cats for n in list_materials(c).get(c, [])})
    raise UnknownMaterialError(
        f"unknown material {name!r} assigned to {part!r}. "
        f"Available {'/'.join(cats)}: {avail}")


def validate_assignment(assignments: Optional[dict],
                        known_extra: Optional[Iterable[str]] = None) -> None:
    """Raise ``UnknownMaterialError`` naming EVERY assignment that resolves to
    nothing.  ``known_extra`` are names supplied verbatim by a per-request
    material override, which are real by definition (their props travel with
    the request and never go through the library)."""
    extra = {str(n) for n in (known_extra or ())}
    bad: List[str] = []
    for part, name in (assignments or {}).items():
        if not name or not isinstance(name, str) or name in extra:
            continue
        try:
            resolve_assigned(str(part), name)
        except UnknownMaterialError as exc:
            bad.append(str(exc))
    if bad:
        raise UnknownMaterialError("; ".join(bad))


def get_steel(name: str) -> SteelMaterial:
    """Shorthand for ``get_material('steel', name)``."""
    return get_material("steel", name)  # type: ignore[return-value]


def get_magnet(name: str) -> MagnetMaterial:
    """Shorthand for ``get_material('magnet', name)``."""
    return get_material("magnet", name)  # type: ignore[return-value]


def get_conductor(name: str) -> ConductorMaterial:
    """Shorthand for ``get_material('conductor', name)``."""
    return get_material("conductor", name)  # type: ignore[return-value]


def get_insulator(name: str) -> InsulatorMaterial:
    """Shorthand for ``get_material('insulator', name)``."""
    return get_material("insulator", name)  # type: ignore[return-value]


def get_coolant(name: str) -> CoolantMaterial:
    """Shorthand for ``get_material('coolant', name)``."""
    return get_material("coolant", name)  # type: ignore[return-value]


def all_steels() -> Dict[str, SteelMaterial]:
    """Return all steel materials as a dict {name: SteelMaterial}."""
    lib = _load()
    return {n: _parse_steel(n, raw) for n, raw in lib.get("steel", {}).items()}


def all_magnets() -> Dict[str, MagnetMaterial]:
    """Return all magnet materials as a dict {name: MagnetMaterial}."""
    lib = _load()
    return {n: _parse_magnet(n, raw) for n, raw in lib.get("magnet", {}).items()}


def all_conductors() -> Dict[str, ConductorMaterial]:
    """Return all conductor materials as a dict {name: ConductorMaterial}."""
    lib = _load()
    return {n: _parse_conductor(n, raw) for n, raw in lib.get("conductor", {}).items()}


def all_insulators() -> Dict[str, InsulatorMaterial]:
    """Return all insulator materials as a dict {name: InsulatorMaterial}."""
    lib = _load()
    return {n: _parse_insulator(n, raw) for n, raw in lib.get("insulator", {}).items()}


def all_coolants() -> Dict[str, CoolantMaterial]:
    """Return all coolant fluids (liquids + air) as a dict {name: CoolantMaterial}."""
    lib = _load()
    return {n: _parse_coolant(n, raw) for n, raw in lib.get("coolant", {}).items()}


def reload() -> None:
    """Force reload of the library from disk, bypassing the mtime probe."""
    global _library, _lib_mtime, _lib_checked
    _library = None
    _lib_mtime = 0.0
    _lib_checked = 0.0
    _load()
    _bertotti_fit_cache.clear()


# ---------------------------------------------------------------------------
# Maxwell-style Bertotti coefficient fit from the MEASURED loss curves
# ---------------------------------------------------------------------------
# Ansys Maxwell takes the manufacturer's P(B) curves at several frequencies and
# least-squares fits the three Bertotti coefficients; the transient solver then
# applies them in the time domain.  We do the same: a non-negative LS fit of
#     P [W/m³] = kh·f·B² + kc·f²·B² + ke·f^1.5·B^1.5
# over EVERY measured (f, B, P) point, weighted by 1/P so the relative error is
# minimised across the whole (f, B) range (an unweighted fit would only care
# about the highest-loss corner).  Verified on 20SW1200 (10 curves, 30–800 Hz):
# mean relative error 6.4 %, p90 15.6 % — versus the hand-set YAML coefficients
# which carry no excess term at all (ke = 0).

_bertotti_fit_cache: Dict[str, tuple] = {}


def fit_bertotti_from_curves(steel: "SteelMaterial"):
    """Fit (kh, kc, ke) [W/m³ units] from ``steel.core_loss_curves``.

    Returns ``(kh, kc, ke, report)`` or ``None`` when the material has no
    usable measured curves.  ``report`` = {n_points, rel_err_mean, rel_err_p90}.
    Cached per material name.
    """
    if steel is None or not steel.core_loss_curves:
        return None
    hit = _bertotti_fit_cache.get(steel.name)
    if hit is not None:
        return hit
    import numpy as np
    from scipy.optimize import nnls

    dens = steel.density if steel.core_loss_curve_unit == "w_per_kg" else 1.0
    rows: list = []
    pv: list = []
    for fk, curve in steel.core_loss_curves.items():
        try:
            f = float(str(fk).replace("Hz", ""))
        except ValueError:
            continue
        for b, p in curve:
            if b < 0.05 or p <= 0:
                continue
            rows.append([f * b ** 2, f ** 2 * b ** 2, f ** 1.5 * b ** 1.5])
            pv.append(p * dens)
    if len(pv) < 6:
        return None
    A = np.asarray(rows, float)
    P = np.asarray(pv, float)
    w = 1.0 / P                                   # relative-error weighting
    coef, _ = nnls(A * w[:, None], P * w)
    pred = A @ coef
    rel = np.abs(pred - P) / P
    report = {
        "n_points": int(P.size),
        "rel_err_mean": float(rel.mean()),
        "rel_err_p90": float(np.percentile(rel, 90)),
    }
    out = (float(coef[0]), float(coef[1]), float(coef[2]), report)
    _bertotti_fit_cache[steel.name] = out
    return out


def effective_bertotti(steel: Optional["SteelMaterial"]) -> Tuple[float, float, float]:
    """(kh, kc, ke) to USE for this steel: fitted from its measured loss curves
    when available (Maxwell-style), else the YAML coefficients, else zeros."""
    if steel is None:
        return 0.0, 0.0, 0.0
    fit = fit_bertotti_from_curves(steel)
    if fit is not None:
        return fit[0], fit[1], fit[2]
    return steel.core_loss_kh, steel.core_loss_kc, steel.core_loss_ke


# ---------------------------------------------------------------------------
# CLI quick-check
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=== Materials Library ===\n")
    for cat, names in list_materials().items():
        print(f"[{cat}]")
        for n in names:
            m = get_material(cat, n)  # type: ignore[arg-type]
            if isinstance(m, SteelMaterial):
                print(f"  {n}: sigma={m.sigma:.3g} S/m, kf={m.stacking_factor}, "
                      f"kh={m.core_loss_kh:.4g}, BH pts={len(m.bh_curve)}")
            elif isinstance(m, MagnetMaterial):
                print(f"  {n}: Br={m.Br:.3f} T, Hc={m.Hc:.0f} A/m, "
                      f"mu_rec={m.mu_rec:.3f}, BH pts={len(m.bh_curve)}")
            elif isinstance(m, ConductorMaterial):
                print(f"  {n}: sigma={m.sigma:.3g} S/m, rho={m.density} kg/m3")
        print()
