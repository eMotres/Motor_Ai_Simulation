"""Gearbox + motor drivetrain check - POST /api/gears/drivetrain (stage 4 of the gear module; staff only, read only).

One gearbox (a saved record or a complete inline request) is put behind one motor configuration and checked at the
requested OUTPUT points (``load`` rows).  Nothing is stored, nothing is computed on the motor side: the motor is read only
through its SAVED duty results, and a mode with no saved result comes back as ``missing`` with the reason.

Request::

    {"gearbox": {"record_id": "gb_...", "project": "<id>"}          # saved record (project optional = workspace scope)
              | {"request": { ...complete GearboxRequest record... }},
     "motor":   {"die": "...", "configuration": "...", "duties": {"continuous": "<duty name>", "peak": "<duty name>"}},
     "load":    [{"name": "continuous", "mode": "continuous", "torque_out_Nm": 9.8, "speed_out_rpm": 247},
                 {"name": "peak", "mode": "peak", "torque_out_Nm": 20, "speed_out_rpm": 120}, ...]}

``motor.duties`` is optional: it names the saved duty that answers a mode.  Without it, ``continuous`` is the
configuration's rated duty (when that one is not short-time) or the first duty classified continuous, and ``peak`` is the
first duty classified short-time or, failing that, a duty whose name starts with ``peak``.  ``mode`` defaults to ``name``.

Per row (``status`` = ``ok`` | ``missing`` | ``gearbox_failed``): the motor shaft torque and speed the output point needs
through the gearbox (``torque_in_Nm``, ``speed_in_rpm``, the core's own point evaluation with the torque given at the
output), the loss by stage (mesh / bearings / churning), whether the one saved motor point covers it (``covered_by_saved_point``, ``margin`` in torque and
speed, ``limited_by`` named, never clamped), the two efficiency parts (``efficiency_parts``, each with its own point; ``chain_efficiency`` only when the
required point is within 2 % of the saved one, else null with ``chain_efficiency_reason``) and the
output torque per TOTAL mass (motor + gearbox).  Every answer says ``motoring_only: true`` and ``reverse_not_computed: true``.

A request the route or the core rejects answers 422 with ``{"detail": {"error": "validation", "errors": [{"path", "code",
"message"}]}}`` (RFC 6901 paths, as routes/gears.py).  Closed motor data never leaves: only the saved operating-point numbers
and the mass.  Access to the motor is the catalogue's own (``family._require_die_access``): a configuration the caller may
not see answers 404, like a missing one.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Body, Depends, Header

from motor_ai_sim import gear_store
from motor_ai_sim.gears import FieldValidationError, GearboxRequest
from motor_ai_sim.routes.gears import _invalid, require_staff

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/gears", tags=["gears"])
# POST under /api/gears is already gated by the entries of routes/gears.py; this route adds no PUT or DELETE.

SCHEMA = "gear_drivetrain-1"
MAX_ROWS = 20
_TOL = 1e-9
_W = math.pi / 30.0                                   # rpm -> rad/s (the constant of modules/drivetrain/system.py)
SHAFT_TORQUE_KEYS = ("T_coupling_Nm", "T_shaft_Nm", "T_shaft_em_Nm")   # in this order, read only with the shaft_convention marker
FIELD_TORQUE_KEYS = ("T_em_avg_Nm", "T_em_Nm", "T_avg_Nm", "torque_avg_Nm")
_MATCH_TOL = 0.02                                     # a required point within 2 % of the saved one counts as "the saved point"

CORE_LIMITATIONS = [
    "motoring only: power flows motor -> load; reverse / regeneration is not computed",
    "screening estimate, not a measured or certified rating; see docs/gear_module/CORE_API_AND_LIMITATIONS.md",
    "loss by stage is the core's own estimate: mesh-loss correlation, bearing friction and churning are not qualified against "
    "measurements; a loss the core could not compute for lack of inputs (bearing definitions, grease drag torque) is null "
    "with loss_inputs_missing and the totals are marked partial",
    "internal-root bending, signed-reversal fatigue and thin-rim gates are unsupported; a point result is never a qualification",
    "mass and inertia of the gearbox are analytical blank estimates",
]

ASSUMPTIONS = [
    "gearbox: the core's point evaluation with the torque given at the OUTPUT (torque_at='output', forward); the motor input "
    "speed is the output speed times the absolute overall ratio",
    "coverage basis (covered_by_saved_point): one saved motor point, not a map. A row is covered when the required motor shaft "
    "torque AND speed are both not above the saved point's; between points nothing is claimed",
    "chain efficiency (motor x gearbox) is given only when the required point matches the saved motor point within 2 % in torque "
    "AND speed; otherwise it is null (motor_point_differs). The two parts are always given separately, each with the point it "
    "belongs to; nothing from another point is multiplied silently",
    "motor torque basis: when the saved summary carries the shaft_convention marker, the marked shaft quantity (coupling torque "
    "when a bearing model exists, else T_shaft_Nm); otherwise the field torque of the legacy convention (torque_basis: field_legacy)",
    "gearbox losses are the core's stage results; a loss the core did not compute (bearings without bearing definitions, grease "
    "churning without a drag torque) is null with loss_inputs_missing, and the totals and the gearbox efficiency of that row are "
    "marked partial",
    "total mass = saved motor mass (full assembled mass when the catalogue can weigh the motor, else the active mass) + gearbox "
    "analytical mass",
]


# ------------------------------------------------------------------------------------------------ request parsing

def _bad(path: str, message: str, code: str = "invalid_value") -> FieldValidationError:
    return FieldValidationError(path, message, code)


def _obj(value: Any, path: str, allowed: Tuple[str, ...]) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise _bad(path, "Expected an object")
    for key in value:
        if key not in allowed:
            raise _bad(f"{path}/{key}", "Unknown field", "unknown_field")
    return value


def _name(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 120:
        raise _bad(path, "Expected a non-empty text of at most 120 characters")
    return value.strip()


def _positive(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _bad(path, "Expected a finite number greater than 0")
    try:
        number = float(value)
    except (ValueError, OverflowError):            # a JSON integer of 400+ digits
        raise _bad(path, "Expected a finite number greater than 0")
    if not math.isfinite(number) or number <= 0:
        raise _bad(path, "Expected a finite number greater than 0")
    return number


def parse_body(body: Any) -> Dict[str, Any]:
    if not isinstance(body, dict):
        raise _bad("", "Expected a JSON object")
    data = _obj(body, "", ("gearbox", "motor", "load"))
    for key in ("gearbox", "motor", "load"):
        if key not in data:
            raise _bad("/" + key, f"{key} is required", "required")
    gb = _obj(data["gearbox"], "/gearbox", ("record_id", "project", "request"))
    if ("record_id" in gb) == ("request" in gb):
        raise _bad("/gearbox", "Give exactly one of record_id or request")
    if "record_id" in gb:
        _name(gb["record_id"], "/gearbox/record_id")
        if gb.get("project") is not None:
            _name(gb["project"], "/gearbox/project")
    elif "project" in gb:
        raise _bad("/gearbox/project", "project goes with record_id only", "unknown_field")
    elif not isinstance(gb["request"], dict):
        raise _bad("/gearbox/request", "Expected a complete GearboxRequest object")
    motor = _obj(data["motor"], "/motor", ("die", "configuration", "duties"))
    _name(motor.get("die"), "/motor/die")
    _name(motor.get("configuration"), "/motor/configuration")
    duties = motor.get("duties")
    if duties is not None:
        duties = _obj(duties, "/motor/duties", tuple(duties) if isinstance(duties, dict) else ())
        for mode, duty in duties.items():
            _name(mode, f"/motor/duties/{mode}")
            _name(duty, f"/motor/duties/{mode}")
    rows = data["load"]
    if not isinstance(rows, list) or not rows:
        raise _bad("/load", "Expected a non-empty list of load rows")
    if len(rows) > MAX_ROWS:
        raise _bad("/load", f"At most {MAX_ROWS} rows")
    seen = set()
    for i, row in enumerate(rows):
        p = f"/load/{i}"
        r = _obj(row, p, ("name", "mode", "torque_out_Nm", "speed_out_rpm"))
        if r.get("name") is None and r.get("mode") is None:
            raise _bad(p + "/name", "Give a name or a mode", "required")
        for key in ("name", "mode"):
            if r.get(key) is not None:
                _name(r[key], f"{p}/{key}")
        for key in ("torque_out_Nm", "speed_out_rpm"):
            if key not in r:
                raise _bad(f"{p}/{key}", f"{key} is required", "required")
            _positive(r[key], f"{p}/{key}")
        label = str(r.get("name") or r.get("mode"))
        if label in seen:
            raise _bad(p + "/name", "Duplicate row name", "duplicate")
        seen.add(label)
    return data


# --------------------------------------------------------------------------------------------------- gearbox source

def load_gearbox_request(gb: Dict[str, Any]) -> Tuple[GearboxRequest, Dict[str, Any]]:
    """The point-evaluation request and where it came from.  A saved record that is damaged is refused (422); stale flags are
    passed on, not hidden."""
    if "record_id" in gb:
        try:
            rec = gear_store.load(gb.get("project"), gb["record_id"])
        except gear_store.StoreError as exc:
            if exc.status == 404:
                from fastapi import HTTPException
                raise HTTPException(404, detail={"error": exc.code, "message": exc.message})
            raise _bad("/gearbox/record_id" if exc.code != "invalid_scope" else "/gearbox/project", exc.message, exc.code)
        raw = rec.get("request")
        src = {"kind": "record", "record_id": rec.get("id"), "project": gb.get("project"), "record_version": rec.get("version"),
               "record_state": rec.get("state"), "record_flags": list(rec.get("flags") or []), "record_name": rec.get("name")}
        path = "/gearbox/record_id"
    else:
        raw = gb["request"]
        src = {"kind": "inline", "record_id": None, "project": None}
        path = "/gearbox/request"
    if not isinstance(raw, dict):
        raise _bad(path, "The stored request is not an object")
    data = dict(raw)
    data["operation"], data["spectrum"] = "point", None
    try:
        req = GearboxRequest.from_dict(data)
    except FieldValidationError as exc:
        raise _bad(path + exc.path, exc.message, exc.code)
    except (ValueError, TypeError, KeyError) as exc:
        raise _bad(path, str(exc))
    return req, src


# ----------------------------------------------------------------------------------------------- motor saved points

def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    try:
        f = float(v)
    except (ValueError, OverflowError):
        return None
    return f if math.isfinite(f) else None


def _pick(blocks: List[Any], keys: Tuple[str, ...]) -> Tuple[Optional[float], Optional[str]]:
    """First key (in the order given) found in any block (in the order given): the key's priority beats the block's."""
    lows = [{str(k).lower(): v for k, v in b.items()} for b in blocks if isinstance(b, dict)]
    for key in keys:
        for low in lows:
            n = _num(low.get(key.lower()))
            if n is not None:
                return n, key
    return None, None


def saved_point(duty: Dict[str, Any]) -> Dict[str, Any]:
    """The saved operating point of one duty entry (its ``result`` / ``summary``), or ``{"missing": reason}``.  Pure."""
    name = str(duty.get("name") or "")
    blocks = [duty.get("result"), duty.get("summary")]
    if not any(isinstance(b, dict) and b for b in blocks):
        return {"duty": name, "missing": "no saved result for this duty"}
    marker = next((b.get("shaft_convention") for b in blocks if isinstance(b, dict) and b.get("shaft_convention")), None)
    torque = tkey = None
    if marker:                       # the marked shaft quantities (PR #900): coupling torque with a bearing model, else the shaft torque
        torque, tkey = _pick(blocks, SHAFT_TORQUE_KEYS)
    if torque is None:
        torque, tkey = _pick(blocks, FIELD_TORQUE_KEYS)
    rpm, _ = _pick(blocks, ("rpm", "speed_rpm"))          # the saved blocks first: the duty's own rpm is editable
    if rpm is None:
        rpm, _ = _pick([duty], ("rpm", "speed_rpm"))
    if torque is None or rpm is None or torque <= 0 or rpm <= 0:
        return {"duty": name, "missing": "the saved result has no positive shaft torque and speed"}
    eff, ekey = _pick(blocks, ("efficiency_shaft", "eta_shaft"))
    if eff is None:
        pct, ekey = _pick(blocks, ("efficiency_pct", "efficiency_percent", "eta_pct"))
        eff = pct / 100.0 if pct is not None else None
    if eff is None:
        eff, ekey = _pick(blocks, ("efficiency", "eta_em"))
    if eff is not None and not (0.0 < eff <= 1.0):
        eff, ekey = None, None
    shaft = bool(marker) and tkey in SHAFT_TORQUE_KEYS
    return {"duty": name, "torque_Nm": torque, "speed_rpm": rpm, "efficiency": eff, "efficiency_key": ekey,
            "torque_basis": "shaft" if shaft else "field_legacy", "torque_key": tkey, "shaft_convention": marker}


def resolve_duty(motor: Dict[str, Any], mode: str, explicit: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """(duty name, None) or (None, reason).  Pure over ``motor['duties']`` (name -> saved point), ``classes``, ``rated``."""
    names = list(motor.get("duties") or {})
    if explicit:
        return (explicit, None) if explicit in names else (None, f"duty {explicit!r} does not exist in this configuration")
    classes = motor.get("classes") or {}
    cls = lambda n: (classes.get(n) or {}).get("duty_class")           # noqa: E731
    key = mode.strip().lower()
    if key == "continuous":
        rated = motor.get("rated")
        if rated in names and cls(rated) != "short_time":
            return rated, None
        for n in names:
            if cls(n) == "continuous":
                return n, None
        return None, "no duty of this configuration is classified continuous"
    if key == "peak":
        for n in names:
            if cls(n) == "short_time":
                return n, None
        for n in names:
            if n.strip().lower().startswith("peak"):
                return n, None
        return None, "no duty of this configuration is classified short-time or named peak"
    return None, f"no saved duty is mapped to mode {mode!r}; name it in motor.duties"


def motor_from_docs(die: str, cfg: str, cfg_doc: Dict[str, Any], classes: Dict[str, Any], rated: Optional[str]) -> Dict[str, Any]:
    """Saved points of every ordinary duty of a configuration document.  Pure (no reads).  Test duties are left out."""
    from motor_ai_sim.duty_kind import is_test_duty
    duties: Dict[str, Dict[str, Any]] = {}
    for d in cfg_doc.get("duties") or []:
        if isinstance(d, dict) and d.get("name") and not is_test_duty(d):
            duties[str(d["name"])] = saved_point(d)
    mass = cfg_doc.get("mass") if isinstance(cfg_doc.get("mass"), dict) else {}
    full, active = _num(mass.get("full_kg")), _num(mass.get("active_kg"))
    partial = bool(mass.get("partial"))
    if full is not None and not partial:
        mass_kg, source = full, "motor full assembled mass (catalogue mass summary)"
    elif active is not None:
        mass_kg, source = active, "motor active-parts mass (catalogue mass summary; partial)"
    else:
        mass_kg = next((_num((d.get("result") or {}).get("mass_kg")) for d in (cfg_doc.get("duties") or [])
                        if isinstance(d, dict) and _num((d.get("result") or {}).get("mass_kg")) is not None), None)
        source = "motor mass of the saved result (partial)" if mass_kg is not None else None
        partial = True
    return {"die": die, "configuration": cfg, "duties": duties, "classes": classes, "rated": rated,
            "mass_kg": mass_kg, "mass_source": source, "mass_partial": partial}


def read_saved_motor(die: str, cfg: str, authorization: Optional[str]) -> Dict[str, Any]:
    """READ ONLY.  The access check is the catalogue's own (404 for a configuration the caller may not see); the points are
    the duties' saved results.  Nothing is solved and nothing is written."""
    from motor_ai_sim import duty_results
    from motor_ai_sim.routes import family
    die, cfg = family._check_name(die, "die"), family._check_name(cfg, "configuration")
    family._require_die_access(die, authorization, cfg, "pair")
    cfile = family._cfg_file(die, cfg)
    if not cfile.is_file():
        from fastapi import HTTPException
        raise HTTPException(404, detail=family._config_missing_detail(die, cfg, "pair"))
    doc = dict(family._load_yaml(cfile, "configuration"))
    doc["die"], doc["name"] = die, cfg
    doc = family._overlay_mass(die, doc)               # a COPY with the full motor mass stamped; never saved
    try:
        stored = duty_results.get(die, cfg)
    except Exception:                                   # noqa: BLE001 - classes then stay unknown, never a failure
        stored = {}
    classes = family.config_classes(doc, stored)
    return motor_from_docs(die, cfg, doc, classes, family.config_rated_duty(doc, classes))


# ------------------------------------------------------------------------------------------------- row evaluation

def _stage_losses(res: Any) -> List[Dict[str, Any]]:
    """Loss by stage.  A loss the core did not compute (no bearing definitions, grease churning without a drag torque) is null
    and named in ``loss_inputs_missing``; ``total_W`` then holds only what was computed and ``total_partial`` is true."""
    out = []
    for s in res.stage_results:
        missing: List[str] = []
        brg, chn = s.power_loss_bearings_W, s.power_loss_churning_drag_W
        if not s.bearing_ratings:        # friction comes from the bearing ratings; loads alone give no loss
            brg = None
            missing.append("bearing_definitions")
        if "grease_drag_not_provided_assumed_zero" in s.flags or "grease_drag_torque_Nm" in s.missing_inputs:
            chn = None
            missing.append("grease_drag_torque_Nm")
        computed = s.power_loss_mesh_W + (brg or 0.0) + (chn or 0.0)
        out.append({"stage": s.stage_name, "stage_id": s.stage_id, "mesh_W": s.power_loss_mesh_W, "bearings_W": brg,
                    "churning_W": chn, "total_W": computed, "total_partial": bool(missing), "loss_inputs_missing": missing,
                    "efficiency": s.efficiency, "efficiency_partial": bool(missing), "state": s.state})
    return out


def evaluate_row(gb: Any, req: GearboxRequest, ratio_abs: float, motor: Dict[str, Any], row: Dict[str, Any]) -> Dict[str, Any]:
    label = str(row.get("name") or row.get("mode"))
    mode = str(row.get("mode") or row.get("name"))
    t_out, n_out = float(row["torque_out_Nm"]), float(row["speed_out_rpm"])
    out: Dict[str, Any] = {"name": label, "mode": mode, "torque_out_Nm": t_out, "speed_out_rpm": n_out,
                           "status": "ok", "covered_by_saved_point": None, "limited_by": None}
    n_in = n_out * ratio_abs
    try:
        res = gb.evaluate_point(input_speed_rpm=n_in, torque_Nm=t_out, torque_at="output", power_direction="forward",
                                temp_C=req.temp_C, preset_name=req.preset_name, compute_reverse_check=False)
    except (ValueError, TypeError, KeyError) as exc:
        return {**out, "status": "gearbox_failed", "reason": str(exc)}
    if res.state == "failed" or not res.chain_kinematics.all_assemblies_ok:
        return {**out, "status": "gearbox_failed", "reason": "gearbox core or assembly failed",
                "gearbox_state": res.state, "gearbox_flags": list(res.flags), "missing_inputs": list(res.missing_inputs),
                "chain_efficiency": None, "chain_efficiency_reason": "gearbox_failed"}
    t_in = abs(res.input_torque_Nm)
    if not (res.power_in_W > 0 and math.isfinite(t_in)):
        return {**out, "status": "gearbox_failed", "reason": "the gearbox point has no positive input power"}
    stages = _stage_losses(res)
    partial = any(st["total_partial"] for st in stages)
    out["gearbox"] = {"torque_in_Nm": t_in, "speed_in_rpm": res.input_speed_rpm, "power_in_W": res.power_in_W,
                      "power_out_W": res.power_out_useful_W, "loss_W": res.power_loss_W,
                      "efficiency": res.overall_efficiency, "loss_by_stage": stages, "loss_partial": partial,
                      "loss_inputs_missing": sorted({m for st in stages for m in st["loss_inputs_missing"]}),
                      "efficiency_partial": partial,
                      "power_balance_residual_W": res.power_in_W - res.power_out_useful_W - res.power_loss_W,
                      "mass_kg": res.mass_kg, "state": res.state, "flags": list(res.flags),
                      "missing_inputs": list(res.missing_inputs)}
    out["motor_required"] = {"torque_Nm": t_in, "speed_rpm": res.input_speed_rpm, "shaft_power_W": t_in * res.input_speed_rpm * _W}
    gb_mass = res.mass_kg
    motor_mass = motor.get("mass_kg")
    total = (motor_mass + gb_mass) if motor_mass is not None and gb_mass is not None else None
    out["mass"] = {"motor_kg": motor_mass, "gearbox_kg": gb_mass, "total_kg": total,
                   "sources": {"motor": motor.get("mass_source"), "gearbox": "core analytical mass of the gearbox"},
                   "motor_mass_partial": bool(motor.get("mass_partial"))}
    out["output_torque_per_total_mass_Nm_per_kg"] = (t_out / total) if total else None

    out["efficiency_parts"] = {
        "gearbox": {"efficiency": res.overall_efficiency, "partial_losses": partial,
                    "at": {"torque_in_Nm": t_in, "speed_in_rpm": res.input_speed_rpm}},
        "motor": None}
    out["chain_efficiency"] = None
    duty, why = resolve_duty(motor, mode, row.get("_duty"))
    point = (motor.get("duties") or {}).get(duty) if duty else None
    out["motor_duty"] = duty
    if point is None or point.get("missing"):
        out["status"] = "missing"
        out["reason"] = why or (point or {}).get("missing") or "no saved point"
        out["chain_efficiency_reason"] = "no_saved_point"
        return out
    out["motor_saved_point"] = {"torque_Nm": point["torque_Nm"], "speed_rpm": point["speed_rpm"], "efficiency": point["efficiency"],
                                "torque_basis": point["torque_basis"], "shaft_convention": point.get("shaft_convention")}
    out["efficiency_parts"]["motor"] = {"efficiency": point["efficiency"], "duty": duty,
                                        "at": {"torque_Nm": point["torque_Nm"], "speed_rpm": point["speed_rpm"]}}
    d_t = point["torque_Nm"] - t_in
    d_n = point["speed_rpm"] - res.input_speed_rpm
    over = []
    if d_t < -_TOL * point["torque_Nm"]:
        over.append(("motor_torque", -d_t / point["torque_Nm"]))
    if d_n < -_TOL * point["speed_rpm"]:
        over.append(("motor_speed", -d_n / point["speed_rpm"]))
    out["margin"] = {"torque_Nm": d_t, "torque_pct": 100.0 * d_t / point["torque_Nm"],
                     "speed_rpm": d_n, "speed_pct": 100.0 * d_n / point["speed_rpm"]}
    out["covered_by_saved_point"] = not over
    out["limited_by"] = max(over, key=lambda x: x[1])[0] if over else None
    out["limits_exceeded"] = [k for k, _ in over]
    match = (abs(t_in - point["torque_Nm"]) <= _MATCH_TOL * point["torque_Nm"]
             and abs(res.input_speed_rpm - point["speed_rpm"]) <= _MATCH_TOL * point["speed_rpm"])
    out["motor_point_matches"] = match
    eta_m, eta_g = point["efficiency"], res.overall_efficiency
    if not match:
        out["chain_efficiency_reason"] = "motor_point_differs"
    elif over:
        out["chain_efficiency_reason"] = "not_covered"
    elif eta_m is None or eta_g is None:
        out["chain_efficiency_reason"] = "efficiency_unavailable"
    else:
        out["chain_efficiency"] = eta_m * eta_g
        out["chain_efficiency_partial_losses"] = partial
    return out


@router.post("/drivetrain")
def drivetrain(body: Any = Body(...), authorization: Optional[str] = Header(default=None),
               _staff: Dict[str, Any] = Depends(require_staff)) -> Dict[str, Any]:
    """Gearbox behind a motor's saved points at the requested output rows.  Read only."""
    try:
        data = parse_body(body)
        req, src = load_gearbox_request(data["gearbox"])
        mt = data["motor"]
        motor = read_saved_motor(str(mt["die"]).strip(), str(mt["configuration"]).strip(), authorization)
        gb = req.build_gearbox()
        kin = gb.kinematics(1000.0)
        ratio_abs = kin.abs_overall_ratio
        explicit = mt.get("duties") or {}
        rows = []
        for r in data["load"]:
            mode = str(r.get("mode") or r.get("name")).strip().lower()
            rows.append(evaluate_row(gb, req, ratio_abs, motor, {**r, "_duty": explicit.get(mode) or explicit.get(str(r.get("mode") or r.get("name")))}))
    except FieldValidationError as exc:
        raise _invalid([exc.to_dict()])
    except (ValueError, TypeError, KeyError) as exc:
        raise _invalid([dict(path="", code="invalid_value", message=str(exc))])
    return {"schema": SCHEMA, "read_only": True, "motoring_only": True, "reverse_not_computed": True,
            "gearbox": {**src, "request_id": req.request_id, "name": req.name, "preset_name": req.preset_name,
                        "temp_C": req.temp_C, "ratio_abs": ratio_abs, "reverses_direction": kin.reverses_direction},
            "motor": {k: motor.get(k) for k in ("die", "configuration", "mass_kg", "mass_source", "mass_partial")},
            "rows": rows, "core_limitations": CORE_LIMITATIONS, "assumptions": ASSUMPTIONS}
