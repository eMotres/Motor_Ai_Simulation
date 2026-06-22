"""cost-basic — capability `cost`: GeometryIR/masses -> CostIR.

A first real cost module: active-material mass x unit price + a flat labor line.
Prices are defaults (USD/kg) overridable via payload. Masses come from the payload
(a ResultIR's mass_components) or, best-effort, from the live geometry.

Agent brief: own cost only. Input = masses (kg per material) + optional prices;
output = contracts.CostIR. Keep prices data-driven so a real BOM/quote engine can
replace the defaults without touching callers.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from ..contracts import CONTRACTS_VERSION, CostIR, CostLine
from ..contracts.adapters import stamp
from .base import ModuleManifest, UIContribution

# Rough default unit prices (USD/kg). Overridable via payload["prices"].
_DEFAULT_PRICES = {"copper": 9.0, "magnet": 80.0, "steel": 3.0, "shaft": 2.0}
# Insulator prices keyed by MATERIAL NAME (USD/kg) — slot liner cost depends
# strongly on the chosen material (Nomex vs AlN), so price per material, not per
# generic bucket.  Overridable via payload["prices"].  NOTE: wire enamel
# (polyimide) is NOT priced here — it comes pre-applied on the magnet wire, so its
# cost is bundled into the copper line (it's thermal-only, see _COSTED_INSULATION).
_INSULATOR_PRICES = {"Nomex": 50.0, "Al2O3": 25.0, "AlN": 250.0}
# Map a mass-component key -> a price bucket.
_BUCKET = {
    "copper": "copper", "stranded": "copper", "winding": "copper",
    "magnet": "magnet", "magnets": "magnet",
    "steel": "steel", "stator": "steel", "rotor": "steel", "core": "steel", "lamination": "steel",
    "shaft": "shaft",
}
# Material densities (kg/m^3) for deriving masses from a GeometryIR.
_DENSITY = {"copper": 8960.0, "magnet": 7500.0, "steel": 7650.0, "shaft": 7850.0}
# RegionRole -> mass bucket (air/band have no mass).
_ROLE_BUCKET = {"coil": "copper", "magnet": "magnet", "stator": "steel",
                "rotor": "steel", "shaft": "shaft"}
# Insulation roles that get a COST line: only the slot liner (Nomex/ceramic) is a
# separate purchased material.  Wire enamel (wire_insulation/polyimide) is bundled
# into the wire → thermal-only, NOT costed.  Maps role -> default material name.
_COSTED_INSULATION = {"slot_insulation": "Nomex"}


def _insulator_density(name: str) -> float:
    """Density [kg/m³] of an insulator from the materials library (fallback 1400)."""
    try:
        from ..materials import get_material
        return float(getattr(get_material("insulator", name), "density", 0.0)) or 1400.0
    except Exception:
        return 1400.0


def masses_from_geometry_ir(gir: Any, *, length_mm: Optional[float] = None) -> Dict[str, float]:
    """Active-material masses (kg) from a GeometryIR: region area x stack length x density.
    This is the real geometry.2d -> cost handoff (no FEM)."""
    from shapely.geometry import Polygon
    L_mm = float(length_mm if length_mm is not None
                 else (gir.parameters or {}).get("motor_length", 50.0))
    masses: Dict[str, float] = {}
    for r in gir.regions:
        role = getattr(r.role, "value", r.role)
        try:
            area = Polygon([(p[0], p[1]) for p in r.exterior.points]).area
            for h in r.holes:
                area -= Polygon([(p[0], p[1]) for p in h.points]).area
        except Exception:
            continue
        vol_m3 = max(area, 0.0) * L_mm * 1e-9          # mm^2 * mm -> mm^3 -> m^3
        if role == "wire_insulation":
            continue   # enamel cost is bundled into the (enamelled) wire — thermal-only
        if role in _COSTED_INSULATION:
            # key by the assigned material name; density from the library.
            matname = getattr(r, "material", None) or _COSTED_INSULATION[role]
            masses[matname] = masses.get(matname, 0.0) + vol_m3 * _insulator_density(matname)
            continue
        bucket = _ROLE_BUCKET.get(role)
        if not bucket:
            continue
        masses[bucket] = masses.get(bucket, 0.0) + vol_m3 * _DENSITY[bucket]
    return {k: round(v, 4) for k, v in masses.items()}


class BasicCost:
    NAME, CAPABILITY, VERSION = "cost-basic", "cost", "0.1.0"

    def manifest(self) -> ModuleManifest:
        return ModuleManifest(
            name=self.NAME, version=self.VERSION, capability=self.CAPABILITY, kind="compute",
            contracts_version=CONTRACTS_VERSION, depends_on=["geometry.2d"],
            inputs=["GeometryIR"], outputs=["CostIR"],
            summary="Active-material mass x unit price (+labor) -> CostIR",
            ui=UIContribution(panel_id="cost", title="Cost",
                              frontend_module="components/cost/CostPanel", order=70, as_tab=True))

    def build(self, masses_kg: Dict[str, float], *, prices: Optional[Dict[str, float]] = None,
              labor_usd: float = 25.0) -> CostIR:
        px = {**_DEFAULT_PRICES, **_INSULATOR_PRICES, **(prices or {})}
        lines = []
        total = 0.0
        for key, mass in (masses_kg or {}).items():
            k = str(key)
            if k in _INSULATOR_PRICES:           # insulation: priced by material name
                unit = float(px.get(k, 0.0))
            else:
                unit = float(px.get(_BUCKET.get(k.lower(), "steel"), 0.0))
            cost = float(mass) * unit
            total += cost
            lines.append(CostLine(item=k, mass_kg=float(mass), unit_price_per_kg=unit, cost=round(cost, 2)))
        if labor_usd:
            total += labor_usd
            lines.append(CostLine(item="labor", cost=round(float(labor_usd), 2)))
        return CostIR(currency="USD", total=round(total, 2), lines=lines,
                      provenance=stamp(self.NAME, version=self.VERSION))

    def run(self, payload: Optional[Dict[str, Any]] = None) -> CostIR:
        p = payload or {}
        masses = p.get("masses_kg") or p.get("masses") or {}
        if not masses:
            # pipeline handoff: derive masses from an upstream GeometryIR — the
            # built-in geometry.2d OR any geometry.* plugin (Plugin-SDK).
            from ._geo_util import upstream_geometry
            gir = upstream_geometry(p)
            if gir is not None and hasattr(gir, "regions"):
                masses = masses_from_geometry_ir(gir, length_mm=p.get("length_mm"))
        if not masses:
            # or off a provided ResultIR summary's mass_components
            res = p.get("result") or {}
            masses = (res.get("mass_components") or {}) if isinstance(res, dict) else {}
        return self.build(masses, prices=p.get("prices"), labor_usd=float(p.get("labor_usd", 25.0)))
