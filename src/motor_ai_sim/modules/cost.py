"""cost-basic — capability `cost`: GeometryIR/masses -> CostIR.

A first real cost module: active-material mass x unit price + a flat labor line.
Prices are defaults (USD/kg) overridable via payload. Masses come from the payload
(a ResultIR's mass_components) or, best-effort, from the live geometry.

Agent brief: own cost only. Input = masses (kg per material) + optional prices;
output = contracts.CostIR. Keep prices data-driven so a real BOM/quote engine can
replace the defaults without touching callers.

MASS IS NOT THIS MODULE'S TO MODEL.  It used to be: a private density table
(copper 8960 / magnet 7500 / steel 7650 / shaft 7850) applied to each GeometryIR
region's area x stack length.  That is a SECOND mass model, and it disagreed with
``masses.compute_masses`` — the one the Simulation card, torque-per-mass and every
optimizer objective use — in three ways that all point the same direction:

  * NO END WINDINGS.  Cost billed the in-slot copper only, while the machine is
    bought with its end turns on: k_end is 1.41 on the 150 mm and 1.73 on the
    40 mm, so the copper line was 29-42 % short of the copper anyone buys.
  * NO LAMINATION FACTOR.  A laminated core is k_f steel and (1-k_f) insulation
    by volume; billing the geometric section as solid steel over-read the iron.
  * DENSITIES FROM A LITERAL, not from the material ASSIGNED to the part.  The
    shaft was priced as 7850 kg/m3 steel whatever the config says it is made of
    (aluminium, on both live machines), and the magnet's density never followed
    the grade the user picked.

So the table is gone and the four structural buckets come from
``masses.compute_masses``.  What stays here is the part cost is genuinely about:
the SLOT LINER, which is a purchased insulator with its own price per material
name and no place in an EM mass model.
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
# masses.compute_masses component key -> price bucket.  There is no density table
# here any more (see the module docstring): the mass model is one function, and
# this is only the naming seam between its components and the price list.
_MASS_BUCKET = {"stator": "steel", "rotor": "steel", "cu": "copper",
                "mag": "magnet", "shaft": "shaft"}
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


def structural_masses(geo: Dict[str, Any]) -> Dict[str, float]:
    """The four priced structural buckets (kg) for a geometry dict, from the
    SINGLE mass source ``masses.compute_masses``.

    ``geo`` is a geometry parameter set in mm (a GeometryIR's ``parameters``).
    Everything that makes a mass a mass — the CAD cross-sections the mesher gets,
    the lamination fill factor, the end-winding factor on the copper, and the
    density of the material each part is ASSIGNED in the config — is decided
    there, once, so a price never disagrees with a torque density about how much
    steel the machine has.
    """
    from ..config import get_config
    from ..masses import compute_masses
    from ..simulation.geometry_2d import merge_geo_override, params_from_config

    over = dict(geo or {})
    p = params_from_config(geo_override=over or None)
    # merge_geo_override, not dict.update: it re-derives the counts/radii the
    # CAD would, so a partial override cannot bill a chimera (the same rule
    # routes/simulation._build_transient_summary follows for the card's mass).
    g = merge_geo_override(dict(get_config().get("geometry", {}) or {}), over or None)
    m = compute_masses(p, g)
    out: Dict[str, float] = {}
    for key, bucket in _MASS_BUCKET.items():
        out[bucket] = out.get(bucket, 0.0) + float(m[key])
    return out


def masses_from_geometry_ir(gir: Any, *, length_mm: Optional[float] = None) -> Dict[str, float]:
    """Priced masses (kg) for a GeometryIR — the real geometry.2d -> cost handoff.

    Two sources, deliberately:

      * the four STRUCTURAL buckets come from ``masses.compute_masses`` through
        this GeometryIR's own ``parameters`` (see ``structural_masses``);
      * the SLOT LINER is measured on this GeometryIR's regions, because it is a
        purchased insulator that no EM mass model carries — priced by MATERIAL
        NAME (Nomex vs AlN is 5x), with the density from the materials library.

    Wire enamel is skipped: it comes pre-applied on the magnet wire, so its cost
    is already inside the copper line.
    """
    from shapely.geometry import Polygon
    geo = dict(getattr(gir, "parameters", None) or {})
    if length_mm is not None:
        # An explicit stack length is the caller quoting a DIFFERENT machine
        # length than the geometry was drawn at; it has to reach the mass model,
        # not just the liner loop below.
        geo["motor_length"] = float(length_mm)
    L_mm = float(geo.get("motor_length", 50.0))
    masses: Dict[str, float] = dict(structural_masses(geo))
    for r in gir.regions:
        role = getattr(r.role, "value", r.role)
        if role not in _COSTED_INSULATION:
            continue                # structural mass + enamel: not measured here
        try:
            area = Polygon([(p[0], p[1]) for p in r.exterior.points]).area
            for h in r.holes:
                area -= Polygon([(p[0], p[1]) for p in h.points]).area
        except Exception:
            continue
        vol_m3 = max(area, 0.0) * L_mm * 1e-9          # mm^2 * mm -> mm^3 -> m^3
        # key by the assigned material name; density from the library.
        matname = getattr(r, "material", None) or _COSTED_INSULATION[role]
        masses[matname] = masses.get(matname, 0.0) + vol_m3 * _insulator_density(matname)
    return {k: round(v, 4) for k, v in masses.items() if v > 0.0}


class BasicCost:
    NAME, CAPABILITY, VERSION = "cost-basic", "cost", "0.1.0"

    def manifest(self) -> ModuleManifest:
        return ModuleManifest(
            name=self.NAME, version=self.VERSION, capability=self.CAPABILITY, kind="compute",
            contracts_version=CONTRACTS_VERSION, depends_on=["geometry.2d"],
            inputs=["GeometryIR"], outputs=["CostIR"],
            summary=("Active-material mass (masses.compute_masses) x unit price "
                     "(+ slot liner, + labor) -> CostIR"),
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
