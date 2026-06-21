"""Kernel HTTP surface — run a capability through the orchestrator.

POST /api/kernel/run {capability, payload} resolves the capability to a module
and runs it with fault isolation, returning the (serialized) IR or a graceful
error. This is the seam the app migrates onto: tabs will call the kernel instead
of solver functions directly.

NOTE: heavy capabilities (solver.*) run real FEM — in prod they belong on the
expensive-endpoint auth gate (added when the app is switched over).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from motor_ai_sim.modules.kernel import Kernel

router = APIRouter(prefix="/api/kernel", tags=["kernel"])

_kernel: Optional[Kernel] = None


def _get_kernel() -> Kernel:
    global _kernel
    if _kernel is None:
        _kernel = Kernel()
    return _kernel


class KernelRunRequest(BaseModel):
    capability: str
    payload: Optional[Dict[str, Any]] = None


def _serialize(result: Any) -> Any:
    """JSON-safe view of a module result. MeshIR (numpy) -> compact summary;
    Pydantic IR -> model_dump; everything else passes through."""
    if hasattr(result, "n_nodes") and hasattr(result, "n_cells"):  # MeshIR
        return {"_type": "MeshIR", "n_nodes": result.n_nodes, "n_cells": result.n_cells,
                "tag_names": result.tag_names, "slip_radius_mm": result.slip_radius_mm}
    if hasattr(result, "model_dump"):
        try:
            return result.model_dump(mode="json")
        except Exception as e:  # noqa: BLE001
            return {"_type": type(result).__name__, "_note": f"non-serializable: {e}"}
    return result


@router.get("/capabilities")
def capabilities() -> Dict[str, Any]:
    k = _get_kernel()
    return {"capabilities": k.capabilities()}


@router.post("/run")
def run(req: KernelRunRequest) -> Dict[str, Any]:
    out = _get_kernel().run(req.capability, req.payload)
    if "result" in out:
        out["result"] = _serialize(out["result"])
    return out
