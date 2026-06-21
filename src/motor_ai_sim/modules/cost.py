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
# Map a mass-component key -> a price bucket.
_BUCKET = {
    "copper": "copper", "stranded": "copper", "winding": "copper",
    "magnet": "magnet", "magnets": "magnet",
    "steel": "steel", "stator": "steel", "rotor": "steel", "core": "steel", "lamination": "steel",
    "shaft": "shaft",
}


class BasicCost:
    NAME, CAPABILITY, VERSION = "cost-basic", "cost", "0.1.0"

    def manifest(self) -> ModuleManifest:
        return ModuleManifest(
            name=self.NAME, version=self.VERSION, capability=self.CAPABILITY, kind="compute",
            contracts_version=CONTRACTS_VERSION, depends_on=["geometry.2d"],
            inputs=["GeometryIR"], outputs=["CostIR"],
            summary="Active-material mass x unit price (+labor) -> CostIR",
            ui=UIContribution(panel_id="cost", title="Cost", frontend_module=None, order=70, as_tab=False))

    def build(self, masses_kg: Dict[str, float], *, prices: Optional[Dict[str, float]] = None,
              labor_usd: float = 25.0) -> CostIR:
        px = {**_DEFAULT_PRICES, **(prices or {})}
        lines = []
        total = 0.0
        for key, mass in (masses_kg or {}).items():
            bucket = _BUCKET.get(str(key).lower(), "steel")
            unit = float(px.get(bucket, 0.0))
            cost = float(mass) * unit
            total += cost
            lines.append(CostLine(item=str(key), mass_kg=float(mass), unit_price_per_kg=unit, cost=round(cost, 2)))
        if labor_usd:
            total += labor_usd
            lines.append(CostLine(item="labor", cost=round(float(labor_usd), 2)))
        return CostIR(currency="USD", total=round(total, 2), lines=lines,
                      provenance=stamp(self.NAME, version=self.VERSION))

    def run(self, payload: Optional[Dict[str, Any]] = None) -> CostIR:
        p = payload or {}
        masses = p.get("masses_kg") or p.get("masses") or {}
        if not masses:
            # best-effort: pull mass_components off a provided ResultIR summary
            res = p.get("result") or {}
            masses = (res.get("mass_components") or {}) if isinstance(res, dict) else {}
        return self.build(masses, prices=p.get("prices"), labor_usd=float(p.get("labor_usd", 25.0)))
