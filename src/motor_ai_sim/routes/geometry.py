import math
import os
import re
from pathlib import Path
from typing import Any, Optional

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from motor_ai_sim.config import get_config, clear_config_cache, DEFAULT_CONFIG_PATH
from motor_ai_sim.routes._validation import (
    DERIVED_GEOMETRY_NAMES,
    MAX_POINTCLOUD_POINTS,
    SOLVER_REQUIRED_PARAMS,
    check_schema_bounds,
    check_unknown_geometry_keys,
    known_geometry_keys,
    param_error,
    parse_geo_override,
    reject,
)
from motor_ai_sim.services.geometry_service import (
    generate_synthetic_pointcloud,
    get_current_geometry,
    params_to_dict,
    reset_geometry,
    update_current_geometry,
)

router = APIRouter(prefix="/api/geometry")


class GeometryUpdateModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class AddParameterRequest(BaseModel):
    name: str
    label: str
    unit: str = ""
    type: str = "float"
    group: str = "custom"
    min: float = 0.0
    max: float = 1000.0
    step: float = 0.1
    default_value: float = 0.0
    description: str = ""
    #: Opt-in for overwriting a parameter that already exists.  Absent (the
    #: default) an existing name is a 422 — creating "wire_width" a second time
    #: used to silently overwrite the real one's bounds and value.
    update: bool = False


_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


@router.get("")
def get_geometry():
    try:
        return params_to_dict(get_current_geometry(reload=True))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("")
def update_geometry(update: GeometryUpdateModel):
    # ── Input guard 1: the NAME has to be a parameter this server knows ──────
    # An unknown key was accepted twice over and applied zero times: the live
    # geometry object got the attribute, the YAML writer (`if key in
    # geometry_section`) dropped it, and the next restart silently reverted the
    # "saved" edit.  A typo therefore looked like it worked, and the process and
    # the file disagreed in between.  422 with the nearest real name.
    _submitted_raw = {k: v for k, v in update.model_dump().items() if v is not None}
    _unknown = check_unknown_geometry_keys(_submitted_raw)
    if _unknown:
        raise reject("unknown geometry field", _unknown)

    # ── Input guard 2: the schema's OWN min/max, enforced server-side ────────
    # The frontend clamps its sliders to these numbers (GET /api/geometry/schema
    # serves them); anything that is not the frontend did not.  GEO_UNBOUNDED=1
    # is the documented escape hatch and logs a warning while it is on.
    _out_of_range = check_schema_bounds(_submitted_raw)
    if _out_of_range:
        raise reject("geometry parameter out of range", _out_of_range)

    # ── Input guard 3: reject unusable VALUES before anything is written ─────
    # A zero / negative / non-finite dimension does not produce an interesting
    # cross-section, it produces a crash several layers down in the mesher (or,
    # worse, a mirrored polygon that meshes fine and solves to nonsense).  422
    # names the field so the client can highlight it, and nothing is persisted.
    # Judged on the MERGED geometry, but only the fields this request actually
    # touched are held against it — plus every multi-parameter ("derived") rule,
    # since a bore radius that comes out negative is broken regardless of which
    # knob was the last one typed.
    try:
        from motor_ai_sim.geometry_validation import validate_parameter_values
        _submitted = _submitted_raw
        _merged = {**get_current_geometry().to_dict(), **_submitted}
        _bad = [r for r in validate_parameter_values(_merged)
                if r.get("kind") == "derived" or r.get("field") in _submitted]
    except HTTPException:
        raise
    except Exception:
        _bad = []   # the guard must never be the reason an edit cannot be saved
    if _bad:
        raise HTTPException(status_code=422, detail={
            "error": "invalid geometry parameter value",
            "invalid_parameters": _bad,
        })

    try:
        # Who is changing the live machine, and into what.  Cheap, write-only,
        # and the only thing that can answer "the motor changed under me — who
        # did it?" after the fact.  See motor_ai_sim/audit.py.
        try:
            from motor_ai_sim.audit import record_write as _audit
            _geo_before = dict(get_current_geometry().to_dict())
        except Exception:
            _audit, _geo_before = None, {}

        from motor_ai_sim.cadquery_geometry import CadQueryCache
        CadQueryCache().clear_all()
        _mesh_cache["hash"] = None
        # Geometry changed → flush every simulation-side cache (2-D polys,
        # build2d / sliding-band meshes, field, transient, frame) so the
        # Mesh tab and Simulation re-solve on the NEW cross-section.
        try:
            from motor_ai_sim.routes.simulation import clear_simulation_caches
            clear_simulation_caches()
        except Exception:
            pass
        params = update_current_geometry(**update.model_dump())

        # Persist changes to YAML so they survive server restarts
        config_path = Path(DEFAULT_CONFIG_PATH)
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        geometry_section = config.setdefault("geometry", {})
        for key, value in update.model_dump().items():
            if value is not None and key in geometry_section:
                geometry_section[key] = value
        # Recompute the DERIVED raw fields that shadow the formulas.  The YAML
        # stores both num_seg/num_*_per_segment AND flattened num_poles/
        # num_slots/angle_* — the solver path reads the flattened ones, so a
        # segment-count edit that skips this left e.g. num_poles at the OLD
        # value: a 20-pole rotor driven at the 28-pole frequency (T garbage)
        # with the 28-pole winding layout.
        try:
            _ns = float(geometry_section.get("num_seg", 0) or 0)
            _pps = float(geometry_section.get("num_poles_per_segment", 0) or 0)
            _sps = float(geometry_section.get("num_slots_per_segment", 0) or 0)
            if _ns > 0 and _pps > 0 and "num_poles" in geometry_section:
                geometry_section["num_poles"] = int(round(_ns * _pps))
                if "angle_pole" in geometry_section:
                    geometry_section["angle_pole"] = 360.0 / (_ns * _pps)
            if _ns > 0 and _sps > 0 and "num_slots" in geometry_section:
                geometry_section["num_slots"] = int(round(_ns * _sps))
                if "angle_slot" in geometry_section:
                    geometry_section["angle_slot"] = 360.0 / (_ns * _sps)
        except Exception:
            pass
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

        if _audit is not None:
            _audit("live_geometry", _geo_before, geometry_section,
                   note="PUT /api/geometry")

        # Flush the config cache so get_config()-based consumers (the analytical
        # torque_sweep, params_from_config, winding calc, …) read the NEW
        # geometry. Without this they keep returning the stale cross-section,
        # which silently invalidates geometry optimization.
        try:
            from motor_ai_sim.config import clear_config_cache
            clear_config_cache()
        except Exception:
            pass

        # ── Enforce geometry feasibility constraints ─────────────────────────
        # Clamp any knob that now violates a physical constraint (e.g. a winding
        # that no longer fits the slot) and persist the clamped value, so the
        # simulation geometry is ALWAYS valid.  Report what was clamped.
        applied: list = []
        try:
            from motor_ai_sim.config import get_config, clear_config_cache
            from motor_ai_sim.geometry_constraints import clamp as _clamp_geo
            full_geo = dict(get_config().get("geometry", {}))
            _clamped, applied = _clamp_geo(full_geo)
            if applied:
                fixes = {a["target"]: _clamped[a["target"]] for a in applied}
                params = update_current_geometry(**fixes)
                with open(config_path, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f)
                gs = config.setdefault("geometry", {})
                for k, v in fixes.items():
                    gs[k] = v
                with open(config_path, "w", encoding="utf-8") as f:
                    yaml.dump(config, f, allow_unicode=True,
                              default_flow_style=False, sort_keys=False)
                clear_config_cache()
        except Exception:
            pass

        result = params_to_dict(params)
        if applied:
            result["constraints_applied"] = [
                {"target": a["target"], "clamped_to": a["clamped_to"],
                 "bound": a["bound"], "label": a["label"]} for a in applied]

        # ── Real geometry validation, AFTER the clamp ───────────────────────
        # The clamp fixes ONE knob at a time against an analytic bound; it can
        # not see where the finished regions land.  Build the same 2-D polygons
        # the mesher consumes and check them for overlaps / escapes / collapses.
        #
        # The result travels as WARNINGS: the user may well be mid-edit (change
        # tooth_width, then wire_width), and refusing to save the intermediate
        # state would make the form unusable.  Solving is what gets blocked —
        # see the gate in routes/simulation.get_fem_transient.
        try:
            from motor_ai_sim.geometry_validation import validate_geometry
            _vres = validate_geometry(dict(get_config().get("geometry", {})))
            result["geometry_validation"] = _vres.to_dict()
        except Exception as _ve:      # never block a save on the validator
            result["geometry_validation"] = {
                "ok": True, "n_errors": 0, "n_warnings": 0, "violations": [],
                "hidden": {}, "checks_run": [], "min_air_gap_mm": None,
                "unavailable": str(_ve)}
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/constraints")
def get_geometry_constraints():
    """Dynamic feasibility bounds (e.g. max wire_height) for the CURRENT geometry
    so the UI can show the effective limit next to a field."""
    try:
        from motor_ai_sim.config import get_config
        from motor_ai_sim.geometry_constraints import bounds, evaluate
        geo = dict(get_config().get("geometry", {}))
        return {"bounds": bounds(geo), "checks": evaluate(geo)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/validation")
def get_geometry_validation(geo: Optional[str] = None):
    """Region-level validation of the CURRENT cross-section (or of a per-request
    ``geo`` override): which domains overlap, where, and by how much.

    Same polygons the mesher consumes, so what is reported here is what would be
    solved.  The Geometry tab calls this on load; a PUT returns the same payload
    under ``geometry_validation`` so an edit updates the list without a refetch.
    """
    try:
        from motor_ai_sim.geometry_validation import validate_geometry
        return validate_geometry(_resolve_geo_dict(geo)).to_dict()
    except HTTPException:
        raise          # a 422 from `geo=` must not be laundered into a 500
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/parameter")
def add_parameter(req: AddParameterRequest):
    """Add a new parameter to motor_config.yaml and reload the schema.

    Everything below the first ``try`` used to run unconditionally: ``min=10,
    max=1`` produced a slider with no reachable value, ``name="num_slots"``
    created a schema entry for a number the code DERIVES (silently overwritten
    on the next reload, so the field did nothing), and re-adding an existing
    name overwrote the real parameter's bounds and value with the defaults of
    this request.  All three now 422 with the offending field named.
    """
    name = req.name.strip().lower().replace(" ", "_").replace("-", "_")
    bad: list = []

    # ── name ────────────────────────────────────────────────────────────────
    if not name:
        bad.append(param_error("name", req.name, "empty",
                               "name must not be empty."))
    elif not _NAME_RE.match(name):
        bad.append(param_error(
            "name", req.name, "bad_identifier",
            f"'{name}' is not a usable parameter name: it must start with a "
            f"letter and contain only lowercase letters, digits and "
            f"underscores (it becomes a YAML key and a Python attribute)."))
    elif name in DERIVED_GEOMETRY_NAMES:
        bad.append(param_error(
            "name", name, "reserved",
            f"'{name}' is DERIVED by the geometry code (see "
            f"MotorGeometryParams._compute_derived) — a parameter of that name "
            f"is recomputed and overwritten on every reload, so it would never "
            f"do anything. Reserved names: "
            f"{', '.join(sorted(DERIVED_GEOMETRY_NAMES))}.",
            reserved_names=sorted(DERIVED_GEOMETRY_NAMES)))
    elif not req.update and name in known_geometry_keys():
        bad.append(param_error(
            "name", name, "already_exists",
            f"'{name}' already exists — adding it again would overwrite the "
            f"live parameter's bounds and value. Pass \"update\": true to "
            f"change it deliberately."))

    # ── type ────────────────────────────────────────────────────────────────
    if req.type not in ("float", "int"):
        bad.append(param_error("type", req.type, "bad_value",
                               f"type must be 'float' or 'int', got "
                               f"'{req.type}'."))

    # ── range ───────────────────────────────────────────────────────────────
    for _f, _v in (("min", req.min), ("max", req.max), ("step", req.step),
                   ("default_value", req.default_value)):
        if not math.isfinite(float(_v)):
            bad.append(param_error(_f, _v, "not_finite",
                                   f"{_f} must be a finite number, got {_v!r}."))
    if math.isfinite(req.min) and math.isfinite(req.max) and req.min >= req.max:
        bad.append(param_error(
            "min", req.min, "bad_range",
            f"min ({req.min:g}) must be strictly less than max ({req.max:g}) — "
            f"otherwise the parameter has no value it is allowed to take.",
            min=req.min, max=req.max))
    if math.isfinite(req.step) and req.step <= 0:
        bad.append(param_error("step", req.step, "bad_value",
                               f"step must be > 0, got {req.step:g}."))
    if (math.isfinite(req.default_value) and math.isfinite(req.min)
            and math.isfinite(req.max) and req.min < req.max
            and not (req.min <= req.default_value <= req.max)):
        bad.append(param_error(
            "default_value", req.default_value, "out_of_range",
            f"default_value ({req.default_value:g}) is outside the range this "
            f"same request declares [{req.min:g}, {req.max:g}].",
            min=req.min, max=req.max))

    if bad:
        raise reject("invalid parameter definition", bad)

    try:
        config_path = Path(DEFAULT_CONFIG_PATH)
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        # Add to geometry section with default value
        value = int(req.default_value) if req.type == "int" else float(req.default_value)
        config.setdefault("geometry", {})[name] = value

        # Add to geometry_schema section
        config.setdefault("geometry_schema", {})[name] = {
            "label": req.label,
            "unit": req.unit,
            "type": req.type,
            "min": int(req.min) if req.type == "int" else float(req.min),
            "max": int(req.max) if req.type == "int" else float(req.max),
            "step": int(req.step) if req.type == "int" else float(req.step),
            "group": req.group,
            "description": req.description,
        }

        # If group is new, add it to parameter_groups
        groups = config.setdefault("parameter_groups", {})
        if req.group not in groups:
            max_order = max((g.get("order", 0) for g in groups.values()), default=0)
            groups[req.group] = {
                "label": req.group.replace("_", " ").title(),
                "order": max_order + 1,
            }

        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

        # Reload everything
        clear_config_cache()
        from motor_ai_sim.services.geometry_service import _current_geometry
        import motor_ai_sim.services.geometry_service as gs
        gs._current_geometry = None

        return {"success": True, "name": name}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/parameter/{name}")
def delete_parameter(name: str):
    """Remove a parameter from motor_config.yaml.

    A parameter the solver reads is NOT deletable: the endpoint used to return
    ``{"success": true}`` for ``DELETE /api/geometry/parameter/air_gap`` and the
    next solve died with a KeyError three layers down in
    ``params_from_config`` — with nothing to connect the crash to the delete.
    """
    if name in SOLVER_REQUIRED_PARAMS or name in DERIVED_GEOMETRY_NAMES:
        _why = ("is DERIVED from other parameters and is recreated on every "
                "reload" if (name in DERIVED_GEOMETRY_NAMES
                             and name not in SOLVER_REQUIRED_PARAMS)
                else "is required by the solver")
        raise reject(f"'{name}' is required by the solver", [param_error(
            name, None, "protected",
            f"'{name}' {_why} — it cannot be deleted. Removing it would break "
            f"the next mesh build and FEM solve (params_from_config reads it "
            f"directly). Change its value instead.",
            protected=True)])
    try:
        config_path = Path(DEFAULT_CONFIG_PATH)
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        removed = False
        if name in config.get("geometry", {}):
            del config["geometry"][name]
            removed = True
        if name in config.get("geometry_schema", {}):
            del config["geometry_schema"][name]
            removed = True

        if not removed:
            raise HTTPException(status_code=404, detail=f"Parameter '{name}' not found")

        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

        clear_config_cache()
        import motor_ai_sim.services.geometry_service as gs
        gs._current_geometry = None

        return {"success": True, "name": name}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/reset")
def reset_geometry_endpoint():
    try:
        return params_to_dict(reset_geometry())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/summary")
def get_geometry_summary():
    params = get_current_geometry()
    return {
        "stator_outer_radius": params.stator_outer_radius,
        "stator_inner_radius": params.stator_inner_radius,
        "rotor_outer_radius": params.rotor_outer_radius,
        "rotor_inner_radius": params.rotor_inner_radius,
        "air_gap": params.air_gap,
        "num_slots": params.num_slots,
        "num_poles": params.num_poles,
        "shaft_radius": params.shaft_radius,
    }


@router.get("/schema")
def get_geometry_schema():
    try:
        config = get_config(reload=True)
        schema = config.get("geometry_schema", {})
        groups = config.get("parameter_groups", {})
        # Only parameters in the whitelist may be used as Sweep/Optimize
        # variables.  Empty/missing list → all parameters allowed (back-compat).
        whitelist = config.get("sweep_whitelist", None)
        allow_all = not whitelist
        whitelist_set = set(whitelist or [])
        # GEO_UNBOUNDED=1 lifts every parameter's min/max cap (debug/exploration
        # across motor scales 40 mm ↔ 450 mm) so the field clamp never blocks a
        # value.  The yaml bounds are preserved; this only overrides them at serve
        # time while the flag is on — unset the env var to restore the caps.
        _unbounded = os.environ.get("GEO_UNBOUNDED", "0") == "1"

        parameters = [
            {
                "name": name,
                "label": meta.get("label", name.replace("_", " ").title()),
                "unit": meta.get("unit", ""),
                "type": meta.get("type", "float"),
                "min": 0 if _unbounded else meta.get("min", 0),
                "max": 1_000_000 if _unbounded else meta.get("max", 1000),
                "step": meta.get("step", 0.1),
                "group": meta.get("group", "other"),
                "description": meta.get("description", ""),
                "optimizable": bool(allow_all or name in whitelist_set),
                "hidden": bool(meta.get("hidden", False)),
            }
            for name, meta in schema.items()
        ]

        group_list = sorted(
            [
                {"id": gid, "label": gmeta.get("label", gid.title()), "order": gmeta.get("order", 99)}
                for gid, gmeta in groups.items()
            ],
            key=lambda g: g["order"],
        )

        return {"parameters": parameters, "groups": group_list}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


_mesh_cache: dict           = {"hash": None, "data": None, "build_time_s": None}
_mesh2d_cache: dict         = {"hash": None, "data": None, "build_time_s": None}
_mesh_extruded_cache: dict  = {"hash": None, "data": None, "build_time_s": None}

def _resolve_geo_dict(geo: Optional[str]) -> dict:
    """Geometry for a mesh request: the global config, optionally overlaid with a
    per-request ``geo`` override (a JSON dict of geometry params).

    Step 1 of the multi-user migration (docs/MULTI_USER_PLAN.md): a signed-in
    client can compute the mesh for ITS OWN design by passing ``geo``, without
    mutating the shared global config.

    A MALFORMED ``geo`` is a 422, not a fallback.  It used to be
    ``except Exception: pass``, which handed the client the shared global
    config — someone else's design — rendered as its own mesh, with a 200 and no
    way to tell.  Absent ``geo`` still means "the global config", as before.
    """
    params_dict = get_current_geometry().to_dict()
    override = parse_geo_override(geo)
    # merge_geo_override, not a dict-update: ``to_dict`` carries the DERIVED
    # fields (counts, slot_width, radii, angles) and the override carries
    # PRIMARIES, so an update returns one motor's primaries under another's
    # derived values — and this dict is both hashed for the mesh cache and handed
    # to the CAD.
    from motor_ai_sim.simulation.geometry_2d import merge_geo_override
    return merge_geo_override(params_dict, override)


@router.get("/mesh")
def get_geometry_mesh(geo: Optional[str] = None):
    try:
        from motor_ai_sim.cadquery_geometry import CadQueryMotor
        import hashlib, json, time
        params_dict = _resolve_geo_dict(geo)
        params_hash = hashlib.md5(json.dumps(params_dict, sort_keys=True).encode()).hexdigest()

        if _mesh_cache["hash"] == params_hash and _mesh_cache["data"] is not None:
            return _mesh_cache["data"]

        motor = CadQueryMotor()
        motor.set_parameters(params_dict)
        t0 = time.perf_counter()
        motor.build_all()
        data = motor.get_all_mesh_data()

        # rotor_fill_r: the 3D SOLID rotor cannot take a BRep fillet at the full
        # radius near the thin bridges (it falls back to a smaller r).  The 2D
        # physics geometry IS filleted at the requested radius (per-edge clamp),
        # and get_extruded_mesh_data() extrudes that filleted 2D rotor to 3D for
        # free — no OCC fillet.  Swap the viewer's rotor_core to that mesh so the
        # 3D view shows the SAME rounding as the physics.  The extruded mesh is
        # z∈[-w,0]; the solids are z∈[0,w], so shift z by +w to align.  Guarded:
        # any failure keeps the (smaller-radius but smooth) solid rotor.
        try:
            _rfr = float(params_dict.get('rotor_fill_r', 0.0) or 0.0)
        except Exception:
            _rfr = 0.0
        if _rfr > 1e-4:
            try:
                _w = float(params_dict.get('motor_length') or 45.0)
                _rc = motor.get_extruded_mesh_data().get('rotor_core')
                if _rc and _rc.get('vertices'):
                    _v = _rc['vertices']
                    if _v and isinstance(_v[0], (list, tuple)):
                        _nv = [[float(x), float(y), float(z) + _w] for (x, y, z) in _v]
                    else:                       # flat [x,y,z,x,y,z,...]
                        _nv = [float(c) for c in _v]
                        for _i in range(2, len(_nv), 3):
                            _nv[_i] += _w
                    _rc2 = dict(_rc); _rc2['vertices'] = _nv
                    data['rotor_core'] = _rc2
            except Exception as _e:
                print(f"rotor_core extruded-fillet override skipped: {_e}")

        build_time = time.perf_counter() - t0

        _mesh_cache["hash"] = params_hash
        _mesh_cache["data"] = data
        _mesh_cache["build_time_s"] = round(build_time, 3)
        return data
    except HTTPException:
        raise          # a 422 from `geo=` must not be laundered into a 500
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/mesh2d")
def get_geometry_mesh2d(geo: Optional[str] = None):
    """Return flat 2-D cross-section meshes for all motor components (shapely+earcut, no CadQuery)."""
    try:
        from motor_ai_sim.cadquery_geometry import CadQueryMotor
        import hashlib, json, time
        params_dict = _resolve_geo_dict(geo)
        params_hash = hashlib.md5(json.dumps(params_dict, sort_keys=True).encode()).hexdigest()

        if _mesh2d_cache["hash"] == params_hash and _mesh2d_cache["data"] is not None:
            return _mesh2d_cache["data"]

        motor = CadQueryMotor()
        motor.set_parameters(params_dict)
        t0 = time.perf_counter()
        data = motor.get_2d_mesh_data()
        build_time = time.perf_counter() - t0

        _mesh2d_cache["hash"] = params_hash
        _mesh2d_cache["data"] = data
        _mesh2d_cache["build_time_s"] = round(build_time, 3)
        return data
    except HTTPException:
        raise          # a 422 from `geo=` must not be laundered into a 500
    except Exception as e:
        import traceback
        raise HTTPException(status_code=500, detail=f"{e}\n{traceback.format_exc()}")


@router.get("/mesh_extruded")
def get_geometry_mesh_extruded(depth: Optional[float] = None, geo: Optional[str] = None):
    """
    Return 3-D meshes built by extruding 2-D Shapely cross-sections (no CadQuery).

    Query param ``depth`` overrides the motor_length config parameter.
    Response includes ``_timing`` metadata with build times for both this
    endpoint and the cached CadQuery 3D build (if available).
    """
    try:
        from motor_ai_sim.cadquery_geometry import CadQueryMotor
        import hashlib, json, time
        params_dict = _resolve_geo_dict(geo)
        cache_key = hashlib.md5(
            json.dumps({**params_dict, "_depth": depth}, sort_keys=True).encode()
        ).hexdigest()

        if _mesh_extruded_cache["hash"] == cache_key and _mesh_extruded_cache["data"] is not None:
            return _mesh_extruded_cache["data"]

        motor = CadQueryMotor()
        motor.set_parameters(params_dict)

        t0 = time.perf_counter()
        data = motor.get_extruded_mesh_data(depth=depth)
        build_time = round(time.perf_counter() - t0, 3)

        # attach timing metadata (not a mesh component — prefixed with _)
        data["_timing"] = {
            "extruded_s":  build_time,
            "cadquery_3d_s": _mesh_cache.get("build_time_s"),   # None if never built
            "mesh2d_s":    _mesh2d_cache.get("build_time_s"),
        }

        _mesh_extruded_cache["hash"] = cache_key
        _mesh_extruded_cache["data"] = data
        _mesh_extruded_cache["build_time_s"] = build_time
        return data
    except HTTPException:
        raise          # a 422 from `geo=` must not be laundered into a 500
    except Exception as e:
        import traceback
        raise HTTPException(status_code=500, detail=f"{e}\n{traceback.format_exc()}")


@router.get("/pointcloud")
def get_geometry_pointcloud(n_points: int = 20000):
    # ``n_points`` sizes five numpy allocations and the JSON body directly.  It
    # was passed through unchecked: n_points=0 returned five empty regions with a
    # 200 (an empty viewer nobody could explain), a negative value made
    # np.random.uniform raise a 500, and n_points=1e9 asked the server for ~24 GB
    # and a multi-GB response — one URL, one process gone.  Bounded and named.
    if n_points < 1 or n_points > MAX_POINTCLOUD_POINTS:
        raise reject(
            "n_points out of range",
            [param_error(
                "n_points", n_points, "out_of_range",
                f"n_points must be between 1 and {MAX_POINTCLOUD_POINTS:,} "
                f"(got {n_points:,}). Above that the point cloud is larger "
                f"than any viewer can draw and the response would not fit in "
                f"memory.",
                min=1, max=MAX_POINTCLOUD_POINTS)])
    try:
        params = get_current_geometry(reload=True)

        pointcloud_data = generate_synthetic_pointcloud(params, n_points)

        return {'n_points': n_points, 'has_modulus': False, 'regions': pointcloud_data}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
