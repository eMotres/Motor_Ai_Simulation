"""Gear and gearbox routes - /api/gears (stage 2 of the gear module; staff only, no storage).

    POST /api/gears/evaluate    one operating point or a duty spectrum (operation "point" | "spectrum")
    POST /api/gears/assembly    outline description of the chain (outline_once / outline_stride shrink the payload)
    POST /api/gears/pose        every member's placement at input_angle_deg
    POST /api/gears/template    plain editor fields -> a COMPLETE canonical GearboxRequest record (built by the core constructors)
    GET  /api/gears/catalogues  the four lists below in one answer
    GET  /api/gears/catalogues/{materials|lubricants|bearings|presets}

The body of every POST is the schema-v1 ``GearboxRequest`` record (``GearboxRequest.to_dict()``); the answer is the schema-v1
``GearboxResponse`` record, unchanged (``result.passed`` and ``result.pass_reasons`` are the core's, never recomputed here).
The route fixes the operation: a body that omits ``operation`` gets the route's, a body that names another one is refused.
A body the core rejects answers 422 with ``{"detail": {"error": "validation", "errors": [{"path", "code", "message"}]}}``;
``path`` is the RFC 6901 pointer of the offending field (``/stages/0/gear1/z``).

Access: MOTRES staff (engineer or admin) only - the simulation is closed to clients.  Nothing here reads or writes a store.
The calculation is a screening estimate; see docs/gear_module/CORE_API_AND_LIMITATIONS.md for what the answer does not cover.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException

from motor_ai_sim import auth as _auth
from motor_ai_sim.gears import (DutyBin, DutySpectrum, FieldValidationError, GearBearingSpec, GearSpec, GearboxRequest,
                                PairStageSpec, PlanetaryStageSpec, list_bearing_catalogue,
                                list_lubricant_catalogue, list_material_catalogue, list_preset_catalogue)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/gears", tags=["gears"])

# Belt for RoleGateMiddleware: the same tier as the dependency below (engineer or admin), for every method.
for _method in ("GET", "POST"):
    if (_method, "/api/gears", "engineer") not in _auth._GATED_PREFIX:
        _auth._GATED_PREFIX.append((_method, "/api/gears", "engineer"))


def require_staff(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    """Dependency: engineer or admin account (MOTRES staff); anonymous 401, other roles 403."""
    is_admin, user = _auth._is_admin_caller(authorization)
    if not _auth.AUTH_ENFORCE and not _auth._ADMIN_EMAILS:
        return user or {"uid": "local-dev", "email": None, "role": "admin"}
    if user is None and not is_admin:
        raise HTTPException(status_code=401, detail="Sign in to use this feature.")
    role = "admin" if is_admin else (user.get("role") if user else "anon")
    if _auth._ROLE_RANK.get(role, -1) < _auth._ROLE_RANK["engineer"]:
        raise HTTPException(status_code=403, detail="The gear module is open to MOTRES staff only.")
    return user or {"uid": "local-dev", "email": None, "role": role}


def _invalid(errors) -> HTTPException:
    return HTTPException(status_code=422, detail={"error": "validation", "errors": errors})


def _run(body: Any, operation: str):
    if not isinstance(body, dict):
        raise _invalid([dict(path="", code="invalid_value", message="Expected a JSON object")])
    data = dict(body)
    named = data.setdefault("operation", operation)
    if named != operation:
        raise _invalid([dict(path="/operation", code="invalid_value",
                             message=f"This route computes operation {operation!r}, not {named!r}")])
    try:
        return GearboxRequest.from_dict(data).execute().to_dict()
    except FieldValidationError as exc:
        raise _invalid([exc.to_dict()])
    except (ValueError, TypeError, KeyError) as exc:
        # A legacy low-level constructor error without a pointer: the whole request is named.
        raise _invalid([dict(path="", code="invalid_value", message=str(exc))])


@router.post("/evaluate")
def evaluate(body: Any = Body(...), _staff: Dict[str, Any] = Depends(require_staff)) -> Dict[str, Any]:
    """Point (default) or spectrum evaluation; ``operation`` must be "point" or "spectrum" when given."""
    operation = body.get("operation") if isinstance(body, dict) else None
    if operation not in (None, "point", "spectrum"):
        raise _invalid([dict(path="/operation", code="invalid_value",
                             message="evaluate computes 'point' or 'spectrum'; use /assembly or /pose")])
    if operation is None:
        operation = "spectrum" if isinstance(body, dict) and body.get("spectrum") is not None else "point"
    return _run(body, operation)


@router.post("/assembly")
def assembly(body: Any = Body(...), _staff: Dict[str, Any] = Depends(require_staff)) -> Dict[str, Any]:
    return _run(body, "assembly")


@router.post("/pose")
def pose(body: Any = Body(...), _staff: Dict[str, Any] = Depends(require_staff)) -> Dict[str, Any]:
    return _run(body, "pose")


_CATALOGUES = {"materials": list_material_catalogue, "lubricants": list_lubricant_catalogue,
               "bearings": list_bearing_catalogue, "presets": list_preset_catalogue}


@router.get("/catalogues")
def catalogues(_staff: Dict[str, Any] = Depends(require_staff)) -> Dict[str, Any]:
    return {name: fn() for name, fn in _CATALOGUES.items()}


@router.get("/catalogues/{name}")
def catalogue(name: str, _staff: Dict[str, Any] = Depends(require_staff)) -> Dict[str, Any]:
    fn = _CATALOGUES.get(name)
    if fn is None:
        raise HTTPException(status_code=404, detail={"error": f"unknown catalogue {name!r}", "catalogues": sorted(_CATALOGUES)})
    return {name: fn()}


# ---------------------------------------------------------------------------------------------------- template
# The decoder wants complete canonical records (derived metric_availability included), so a client never writes request JSON
# by hand: it sends plain fields here and gets the record back, ready for /evaluate, /assembly or /pose.  Nothing is stored.

_PLANETARY_ROLES = ("sun", "planet", "planet2", "ring")
_PAIR_ROLES = ("gear1", "gear2")
_REQUEST_FIELDS = ("operation", "request_id", "name", "lubricant", "input_speed_rpm", "torque_Nm", "torque_at",
                   "power_direction", "temp_C", "preset_name", "housing_area_m2", "h_conv_W_m2K", "input_shaft_mm",
                   "output_shaft_mm", "input_angle_deg", "outline_stride", "outline_once")
_BEARING_SLOTS = ("planet_bearing", "input_bearing", "output_bearing",
                  "bearing_1a", "bearing_1b", "bearing_2a", "bearing_2b")


def _humanoid_default() -> Dict[str, Any]:
    """The humanoid-joint starting point: two planetary stages, 12.884:1 (9.8 N*m at 247 rpm out)."""
    def stage(name, zs, zp, zr, n, b):
        return {"kind": "planetary", "name": name, "n_planets": n,
                "gears": {"sun": {"z": zs, "m_n_mm": .5, "b_mm": b, "tooth_thickness_allowance_mm": .02},
                          "planet": {"z": zp, "m_n_mm": .5, "b_mm": b, "tooth_thickness_allowance_mm": .02},
                          "ring": {"z": zr, "m_n_mm": .5, "b_mm": b, "tooth_thickness_allowance_mm": .02}}}
    return {"preset_name": "humanoid_robot_joint", "lubricant": "ROBOT_GREASE_NLGI1_PAO", "name": "joint",
            "input_speed_rpm": 3182., "torque_Nm": 9.8, "torque_at": "output",
            "stages": [stage("s1", 40, 32, 104, 3, 10.), stage("s2", 38, 30, 98, 4, 12.)]}


def _build_stage(i: int, raw: Any):
    path = "/stages/" + str(i)
    if not isinstance(raw, dict):
        raise FieldValidationError(path, "Expected an object")
    data = dict(raw)
    kind = data.pop("kind", "planetary")
    gears_in = data.pop("gears", {}) or {}
    bearings_in = data.pop("bearings", {}) or {}
    roles = _PLANETARY_ROLES if kind in ("planetary", "compound_planetary") else _PAIR_ROLES
    gears = {}
    for role, spec in gears_in.items():
        if role not in roles:
            raise FieldValidationError(path + "/gears/" + str(role), "Unknown gear role for this stage kind", "unknown_field")
        if spec is None:
            continue
        if not isinstance(spec, dict):
            raise FieldValidationError(path + "/gears/" + role, "Expected an object")
        spec = dict(spec)
        if role == "ring" or (role == "gear2" and kind == "internal_pair"):
            spec["is_internal"] = True
        try:
            gears[role] = GearSpec(**spec)
        except FieldValidationError as exc:
            raise FieldValidationError(path + "/gears/" + role + exc.path, exc.message, exc.code) from exc
        except (TypeError, ValueError) as exc:
            raise FieldValidationError(path + "/gears/" + role, str(exc)) from exc
    for slot, name in bearings_in.items():
        if slot not in _BEARING_SLOTS:
            raise FieldValidationError(path + "/bearings/" + str(slot), "Unknown bearing slot", "unknown_field")
        if name:
            try:
                data[slot] = GearBearingSpec.from_library(name)
            except (KeyError, ValueError) as exc:
                raise FieldValidationError(path + "/bearings/" + slot, str(exc), "unknown_reference") from exc
    try:
        if kind in ("planetary", "compound_planetary"):
            if kind == "compound_planetary" and "planet2" not in gears:
                raise FieldValidationError(path + "/gears/planet2", "A compound planetary stage needs planet2", "missing_field")
            return PlanetaryStageSpec(**gears, **data)
        return PairStageSpec(**gears, kind="spur_pair" if kind == "pair" else kind, **data)
    except FieldValidationError:
        raise
    except (TypeError, ValueError) as exc:
        raise FieldValidationError(path, str(exc)) from exc


def build_template(body: Dict[str, Any]) -> Dict[str, Any]:
    body = dict(body or {})
    if "stages" not in body:
        body = {**_humanoid_default(), **body}
    unknown = sorted(set(body) - set(_REQUEST_FIELDS) - {"stages", "spectrum"})
    if unknown:
        raise FieldValidationError("/" + unknown[0], "Unknown field", "unknown_field")
    stages_in = body["stages"]
    if not isinstance(stages_in, list) or not stages_in:
        raise FieldValidationError("/stages", "Expected non-empty stage list")
    kwargs = {k: body[k] for k in _REQUEST_FIELDS if k in body}
    sp = body.get("spectrum")
    if sp is not None:
        if not isinstance(sp, dict) or not isinstance(sp.get("bins"), list) or not sp["bins"]:
            raise FieldValidationError("/spectrum/bins", "Expected non-empty bin list")
        sp = dict(sp)
        try:
            bins = [DutyBin(**b) for b in sp.pop("bins")]
            kwargs["spectrum"] = DutySpectrum(bins, **sp)
        except FieldValidationError:
            raise
        except (TypeError, ValueError) as exc:
            raise FieldValidationError("/spectrum", str(exc)) from exc
        kwargs.setdefault("operation", "spectrum")
    stages = [_build_stage(i, s) for i, s in enumerate(stages_in)]
    return GearboxRequest(stages, **kwargs).to_dict()


@router.post("/template")
def template(body: Any = Body(default=None), _staff: Dict[str, Any] = Depends(require_staff)) -> Dict[str, Any]:
    """Plain fields (an empty body gives the humanoid-joint default) -> complete canonical GearboxRequest record."""
    if body is not None and not isinstance(body, dict):
        raise _invalid([dict(path="", code="invalid_value", message="Expected a JSON object")])
    try:
        return build_template(body or {})
    except FieldValidationError as exc:
        raise _invalid([exc.to_dict()])
    except (ValueError, TypeError, KeyError) as exc:
        raise _invalid([dict(path="", code="invalid_value", message=str(exc))])
