"""GET /api/modules — the portal's module manifests.

The frontend reads this to build itself FROM modules (web interfaces are modules
too): each manifest carries its capability, dependencies and UI panel. Cheap +
read-only, so it stays outside the expensive-FEM auth gate.
"""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter

from motor_ai_sim.modules.bootstrap import default_registry

router = APIRouter(prefix="/api/modules", tags=["modules"])

_registry = None


def _get_registry():
    global _registry
    if _registry is None:
        _registry = default_registry()   # cheap: registers providers, runs no geometry
    return _registry


@router.get("")
def list_modules() -> Dict[str, Any]:
    """All registered module manifests + the capability graph."""
    reg = _get_registry()
    manifests = [m.model_dump() for m in reg.manifests()]
    return {
        "modules": manifests,
        "capabilities": reg.capabilities(),
        "count": len(manifests),
    }
