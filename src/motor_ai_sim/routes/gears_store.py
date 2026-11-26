"""Saved gearbox records - /api/gears/boxes (stage 3 of the gear module; staff only).

    GET    /api/gears/boxes?project=&state=active|archived|all    summaries (a damaged file is listed under "unreadable")
    POST   /api/gears/boxes?project=                              create: {"name", "request", "response"?}
    GET    /api/gears/boxes/{id}?project=                         the full record with "flags" and "response_stale"
    PUT    /api/gears/boxes/{id}?project=                         update: {"version", "name"?, "request"?, "response"?}
    DELETE /api/gears/boxes/{id}?project=&version=                archive (never a hard delete)
    POST   /api/gears/boxes/{id}/restore?project=                 back to active

``project`` is the project id the record belongs to; without it the record belongs to the workspace.  A save whose
``version`` is not the stored one answers 409 ``stale_version`` with ``current_version``.  A request the core rejects
answers 422 with the field paths of routes/gears.py.  Storage and integrity checks: ``motor_ai_sim/gear_store.py``.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import JSONResponse

from motor_ai_sim import auth as _auth
from motor_ai_sim import gear_store as store
from motor_ai_sim.gears import FieldValidationError
from motor_ai_sim.routes.gears import _invalid, require_staff

router = APIRouter(prefix="/api/gears/boxes", tags=["gears"])

# Belt for RoleGateMiddleware: PUT and DELETE are not covered by the /api/gears entries of routes/gears.py.
for _method in ("GET", "POST", "PUT", "DELETE"):
    if (_method, "/api/gears", "engineer") not in _auth._GATED_PREFIX:
        _auth._GATED_PREFIX.append((_method, "/api/gears", "engineer"))


def _author(staff: Dict[str, Any]) -> str:
    return str(staff.get("email") or staff.get("uid") or "unknown")


def _fail(exc: store.StoreError) -> JSONResponse:
    detail: Dict[str, Any] = {"error": exc.code, "message": exc.message}
    detail.update(exc.extra)
    return JSONResponse(status_code=exc.status, content={"detail": detail})


def _guard(fn):
    try:
        return fn()
    except store.StoreError as exc:
        return _fail(exc)
    except FieldValidationError as exc:
        raise _invalid([exc.to_dict()])
    except (ValueError, TypeError, KeyError) as exc:
        raise _invalid([dict(path="", code="invalid_value", message=str(exc))])


def _object(body: Any) -> Dict[str, Any]:
    if not isinstance(body, dict):
        raise _invalid([dict(path="", code="invalid_value", message="Expected a JSON object")])
    return body


@router.get("")
def list_boxes(project: Optional[str] = Query(None), state: str = Query("active"),
               _staff: Dict[str, Any] = Depends(require_staff)):
    return _guard(lambda: store.list_records(project, state))


@router.post("")
def create_box(body: Any = Body(...), project: Optional[str] = Query(None),
               staff: Dict[str, Any] = Depends(require_staff)):
    data = _object(body)
    if "request" not in data:
        raise _invalid([dict(path="/request", code="required", message="request is required")])
    return _guard(lambda: store.create(project, data.get("name"), data["request"], data.get("response"),
                                       _author(staff)))


@router.get("/{rid}")
def get_box(rid: str, project: Optional[str] = Query(None), _staff: Dict[str, Any] = Depends(require_staff)):
    return _guard(lambda: store.load(project, rid))


@router.put("/{rid}")
def update_box(rid: str, body: Any = Body(...), project: Optional[str] = Query(None),
               staff: Dict[str, Any] = Depends(require_staff)):
    data = _object(body)
    return _guard(lambda: store.update(project, rid, data.get("version"), _author(staff), name=data.get("name"),
                                       request=data.get("request"), response=data.get("response"),
                                       response_given="response" in data))


@router.delete("/{rid}")
def archive_box(rid: str, project: Optional[str] = Query(None), version: Optional[int] = Query(None),
                staff: Dict[str, Any] = Depends(require_staff)):
    """Archive, never delete: the file stays and ``restore`` brings it back."""
    return _guard(lambda: store.set_state(project, rid, "archived", _author(staff), version))


@router.post("/{rid}/restore")
def restore_box(rid: str, project: Optional[str] = Query(None), version: Optional[int] = Query(None),
                staff: Dict[str, Any] = Depends(require_staff)):
    return _guard(lambda: store.set_state(project, rid, "active", _author(staff), version))
