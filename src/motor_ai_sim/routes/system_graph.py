"""System Graph routes — view-only schemas (stage 1) + user-composed editable graphs (stage 2).

Endpoints:
- GET /api/system/graph?view=modules|propeller_s1
- GET /api/system/graphs
- GET /api/system/graphs/{name}
- PUT /api/system/graphs/{name}
- DELETE /api/system/graphs/{name}
- POST /api/system/graphs/validate
- POST /api/system/run

Engineer and admin only (enforced by RoleGateMiddleware and route-level auth dependencies).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from motor_ai_sim import auth as _auth
from motor_ai_sim import system_graph as _SG
from motor_ai_sim import system_graph_edit as _SGE

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/system", tags=["system"])

# Ensure RoleGateMiddleware gates all system endpoints to engineer / admin
for _method in ("GET", "PUT", "DELETE", "POST"):
    if (_method, "/api/system/graphs", "engineer") not in _auth._GATED_PREFIX:
        _auth._GATED_PREFIX.append((_method, "/api/system/graphs", "engineer"))
    if (_method, "/api/system/run", "engineer") not in _auth._GATED_PREFIX:
        _auth._GATED_PREFIX.append((_method, "/api/system/run", "engineer"))
    if (_method, "/api/system/validate", "engineer") not in _auth._GATED_PREFIX:
        _auth._GATED_PREFIX.append((_method, "/api/system/validate", "engineer"))


def require_engineer(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    """Dependency: require engineer or admin role when auth enforcement is on."""
    is_admin, user = _auth._is_admin_caller(authorization)
    if not _auth.AUTH_ENFORCE and not _auth._ADMIN_EMAILS:
        return user or {"uid": "local-dev", "email": None, "role": "admin"}
    if user is None and not is_admin:
        raise HTTPException(status_code=401, detail="Sign in to use this feature.")
    role = "admin" if is_admin else (user.get("role") if user else "anon")
    if _auth._ROLE_RANK.get(role, -1) < _auth._ROLE_RANK["engineer"]:
        raise HTTPException(status_code=403, detail="This feature requires a simulation (engineer) account.")
    return user or {"uid": "local-dev", "email": None, "role": role}


# ---------------------------------------------------------------------------
# Stage 1: View-only Schema Graph
# ---------------------------------------------------------------------------

@router.get("/graph")
def get_graph(view: str = Query("modules", description="modules | propeller_s1"),
              die: Optional[str] = Query(None), config: Optional[str] = Query(None),
              duty: Optional[str] = Query(None),
              authorization: Optional[str] = Header(default=None),
              _user: Dict[str, Any] = Depends(require_engineer)) -> Dict[str, Any]:
    if view not in _SG.VIEWS:
        raise HTTPException(status_code=400, detail={
            "error": f"unknown view {view!r}", "views": list(_SG.VIEWS)})
    # The status layer names a machine: a PRIVATE configuration the caller has no
    # grant for is reported like one that does not exist.
    from motor_ai_sim import motor_access as _ma
    visible = not (die and config and not _ma.may_see_config(
        _ma.catalog_access(authorization), die, config))
    try:
        return _SG.build(view, die=die, config=config, duty=duty, visible=visible)
    except LookupError as exc:
        raise HTTPException(status_code=500, detail={"error": f"system graph out of date: {exc}"})


# ---------------------------------------------------------------------------
# Stage 2: User-Composed Graphs (Save / Load / Delete / List)
# ---------------------------------------------------------------------------

@router.get("/graphs")
def list_graphs(_user: Dict[str, Any] = Depends(require_engineer)) -> Dict[str, Any]:
    """List all saved graphs in the caller's workspace, plus available templates."""
    try:
        return {"graphs": _SGE.list_user_graphs()}
    except Exception as exc:  # noqa: BLE001
        log.warning("list_graphs error: %s", exc)
        raise HTTPException(status_code=500, detail={"error": f"failed to list graphs: {exc}"})


@router.get("/graphs/{name}")
def get_user_graph(name: str, _user: Dict[str, Any] = Depends(require_engineer)) -> Dict[str, Any]:
    """Retrieve a saved or template graph by name."""
    try:
        _SGE.validate_graph_name(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)})

    try:
        return _SGE.load_user_graph(name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": str(exc)})
    except Exception as exc:  # noqa: BLE001
        log.warning("get_user_graph error: %s", exc)
        raise HTTPException(status_code=500, detail={"error": f"failed to load graph: {exc}"})


@router.put("/graphs/{name}")
def put_user_graph(name: str, doc: Dict[str, Any] = Body(...),
                   _user: Dict[str, Any] = Depends(require_engineer)) -> Dict[str, Any]:
    """Save or update a graph in the caller's workspace. Validates wiring and cycles."""
    try:
        _SGE.validate_graph_name(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)})

    try:
        return _SGE.save_user_graph(name, doc)
    except (_SGE.WiringError, _SGE.CycleRefusalError, ValueError) as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)})
    except Exception as exc:  # noqa: BLE001
        log.warning("put_user_graph error: %s", exc)
        raise HTTPException(status_code=500, detail={"error": f"failed to save graph: {exc}"})


@router.delete("/graphs/{name}")
def delete_user_graph_endpoint(name: str, _user: Dict[str, Any] = Depends(require_engineer)) -> Dict[str, Any]:
    """Delete a graph from the caller's workspace."""
    try:
        _SGE.validate_graph_name(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)})

    try:
        _SGE.delete_user_graph(name)
        return {"status": "ok", "deleted": name}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error": str(exc)})
    except Exception as exc:  # noqa: BLE001
        log.warning("delete_user_graph error: %s", exc)
        raise HTTPException(status_code=500, detail={"error": f"failed to delete graph: {exc}"})


# ---------------------------------------------------------------------------
# Stage 2: Validation & Run
# ---------------------------------------------------------------------------

class ValidateGraphRequest(BaseModel):
    nodes: List[Dict[str, Any]] = Field(default_factory=list)
    edges: List[Dict[str, Any]] = Field(default_factory=list)
    loops: List[Dict[str, Any]] = Field(default_factory=list)


@router.post("/graphs/validate")
@router.post("/validate")
def validate_graph_endpoint(req: ValidateGraphRequest,
                            _user: Dict[str, Any] = Depends(require_engineer)) -> Dict[str, Any]:
    """Validate a graph's wiring (type checking) and cycle freedom."""
    try:
        _SGE.validate_graph_wiring(req.nodes, req.edges)
        ordered = _SGE.topological_sort(req.nodes, req.edges, req.loops)
        return {"valid": True, "order": [n.get("id") for n in ordered], "errors": []}
    except (_SGE.WiringError, _SGE.CycleRefusalError, ValueError) as exc:
        raise HTTPException(status_code=400, detail={"valid": False, "error": str(exc)})


class RunGraphRequest(BaseModel):
    graph: Optional[Dict[str, Any]] = None
    name: Optional[str] = None


@router.post("/run")
@router.post("/graphs/run")
def run_graph_endpoint(req: RunGraphRequest,
                       _user: Dict[str, Any] = Depends(require_engineer)) -> Dict[str, Any]:
    """Run a graph study: sorts topologically, executes via kernel, updates per-block status."""
    doc = req.graph
    if doc is None and req.name:
        try:
            doc = _SGE.load_user_graph(req.name)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail={"error": str(exc)})

    if not doc or not isinstance(doc, dict):
        raise HTTPException(status_code=400, detail={"error": "a 'graph' object or valid 'name' must be provided"})

    try:
        return _SGE.run_graph_study(doc)
    except (_SGE.WiringError, _SGE.CycleRefusalError, ValueError) as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)})
    except Exception as exc:  # noqa: BLE001
        log.warning("run_graph error: %s", exc)
        raise HTTPException(status_code=500, detail={"error": f"graph execution failed: {exc}"})
