"""materials — capability `materials`: the library + active assignment -> MaterialIR.

Resolves the per-region material assignment (config `materials:` section, or an
explicit `assignment` in the payload) against the materials library into a single
typed MaterialIR: the assignment, the resolved properties of every material in use,
and the full catalogue. This is the seam that lets solvers and the analytical path
read material properties through the contract instead of poking the YAML directly.

Agent brief: own material resolution only. Input = an assignment (region -> name) +
the library; output = contracts.MaterialIR. Keep it data-driven so a supplier BOM /
quote service can replace the YAML library without touching callers.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from ..contracts import CONTRACTS_VERSION, MaterialIR, MaterialProps
from ..contracts.adapters import stamp
from .base import ModuleManifest

# MaterialProps fields read straight off a library dataclass via getattr — absent
# attrs (e.g. a steel has no `Br`) simply resolve to None.
_PROP_FIELDS = (
    "description", "density", "sigma", "thermal_conductivity", "specific_heat", "mu_r",
    "stacking_factor", "core_loss_kh", "core_loss_kc", "core_loss_ke",
    "Br", "Hc", "mu_rec", "phase", "kinematic_viscosity", "prandtl",
)


def _props_from_obj(category: str, name: str, obj: Any) -> MaterialProps:
    vals: Dict[str, Any] = {k: getattr(obj, k, None) for k in _PROP_FIELDS}
    vals["description"] = vals.get("description") or ""
    return MaterialProps(name=name, category=category, **vals)


class MaterialsModule:
    NAME, CAPABILITY, VERSION = "materials-library", "materials", "0.1.0"

    def manifest(self) -> ModuleManifest:
        return ModuleManifest(
            name=self.NAME, version=self.VERSION, capability=self.CAPABILITY, kind="compute",
            contracts_version=CONTRACTS_VERSION, depends_on=[],
            inputs=["MaterialRef"], outputs=["MaterialIR"],
            summary="Materials library + region assignment -> resolved MaterialIR")

    def build(self, assignment: Optional[Dict[str, str]] = None) -> MaterialIR:
        from .. import materials as mat_lib
        library = mat_lib.list_materials()                     # {category: [names]}
        name_to_cat = {n: c for c, names in library.items() for n in names}
        resolved: Dict[str, MaterialProps] = {}
        for name in (assignment or {}).values():
            if not name or name in resolved:
                continue
            cat = name_to_cat.get(name)
            if cat is None:
                continue                                       # name not in the library — skip
            try:
                resolved[name] = _props_from_obj(cat, name, mat_lib.get_material(cat, name))
            except Exception:                                  # noqa: BLE001 — best-effort resolve
                continue
        return MaterialIR(
            contracts_version=CONTRACTS_VERSION,
            assignment=dict(assignment or {}),
            materials=resolved,
            library=library,
            provenance=stamp(self.NAME, version=self.VERSION))

    def run(self, payload: Optional[Dict[str, Any]] = None) -> MaterialIR:
        p = payload or {}
        assignment = p.get("assignment")
        if assignment is None:
            try:
                from ..config import get_material_assignments
                assignment = get_material_assignments() or {}
            except Exception:                                  # noqa: BLE001
                assignment = {}
        return self.build(assignment)
