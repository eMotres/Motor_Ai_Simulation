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

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Literal

import yaml

# ---------------------------------------------------------------------------
# Path
# ---------------------------------------------------------------------------
_LIB_PATH = Path(__file__).parent.parent.parent / "config" / "materials_library.yaml"

# Module-level cache
_library: Optional[dict] = None


def _load() -> dict:
    """Load and cache the materials library YAML."""
    global _library
    if _library is None:
        if not _LIB_PATH.exists():
            raise FileNotFoundError(f"Materials library not found: {_LIB_PATH}")
        with _LIB_PATH.open("r", encoding="utf-8") as f:
            _library = yaml.safe_load(f)
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


# ---------------------------------------------------------------------------
# Internal parsers
# ---------------------------------------------------------------------------

def _parse_steel(name: str, raw: dict) -> SteelMaterial:
    bh = [tuple(p) for p in raw.get("bh_curve", [])]
    cls_curves = {}
    for freq_key, pts in raw.get("core_loss_curves", {}).items():
        cls_curves[freq_key] = [tuple(p) for p in pts]

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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

Category = Literal["steel", "magnet", "conductor"]

_PARSERS = {
    "steel": _parse_steel,
    "magnet": _parse_magnet,
    "conductor": _parse_conductor,
}

_TYPE_MAP = {
    "steel": SteelMaterial,
    "magnet": MagnetMaterial,
    "conductor": ConductorMaterial,
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
    return {cat: list(lib.get(cat, {}).keys()) for cat in ("steel", "magnet", "conductor")}


def get_material(
    category: Category,
    name: str,
) -> SteelMaterial | MagnetMaterial | ConductorMaterial:
    """Retrieve a material by category and name.

    Parameters
    ----------
    category : 'steel' | 'magnet' | 'conductor'
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
    raw = cat_data.get(name)
    if raw is None:
        available = list(cat_data.keys())
        raise KeyError(f"Material '{name}' not found in '{category}'. Available: {available}")
    return _PARSERS[category](name, raw)


def get_steel(name: str) -> SteelMaterial:
    """Shorthand for ``get_material('steel', name)``."""
    return get_material("steel", name)  # type: ignore[return-value]


def get_magnet(name: str) -> MagnetMaterial:
    """Shorthand for ``get_material('magnet', name)``."""
    return get_material("magnet", name)  # type: ignore[return-value]


def get_conductor(name: str) -> ConductorMaterial:
    """Shorthand for ``get_material('conductor', name)``."""
    return get_material("conductor", name)  # type: ignore[return-value]


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


def reload() -> None:
    """Force reload of the library from disk (useful during development)."""
    global _library
    _library = None
    _load()


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
