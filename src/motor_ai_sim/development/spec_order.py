"""Ordering a motor through an AI agent (owner 2026-11-02): requirements -> a development project with a filled, submitted
specification.  The MCP tools ``get_spec_form``, ``create_order``, ``update_spec``, ``submit_spec`` and ``get_order_status``
(``mcp_app``) are thin wrappers around the functions here.

The "order" ends at a SUBMITTED SPECIFICATION.  Our process after that is: full simulation -> agreement with the customer ->
MOTRES computes and assesses all costs -> offer -> payment -> production.  So nothing here gives, estimates or discusses money
or delivery dates: the commercial field of the form (6_3) and the production-launch section are not offered to agents, and
every answer that would name them is refused.

Nothing is validated twice.  Every write goes through the web's own service functions (``routes.create_project_for``,
``routes.save_spec_for``, ``routes.submit_spec_for``: the same access rules, platform-agreement gate, per-account project
limit, optimistic revision, ``models.normalize_draft`` and audit rows as the REST routes), and ``needs_input`` is
``models.validation_errors`` - the exact list ``POST /spec/submit`` refuses with - in words an agent can read to its user.
What this module adds is agent-side convenience only: the form generated from ``schema.json``, merging partial answers into
the stored draft, reading "400 V" as 400 when the field is in V (and refusing "4 kW" for a field in W), and splitting an
answer set into accepted and rejected fields so one bad value does not lose the rest.

Access: only the caller's OWN projects (project membership; global staff rights do not widen what an agent sees), and the
specification is written only where ``access.can_edit_spec`` allows (the project owner).
"""
from __future__ import annotations

import copy
import json
import re
from typing import Any, Iterable

from fastapi import HTTPException

from motor_ai_sim.development import access, models, spec_proposals, store, unread

SCHEMA_VERSION = 2
LANGS = ("en", "zh-CN")

#: Not offered to agents: the commercial target (6_3), the production-launch section and the catalogue-linked load curve
#: (it carries catalogue digests only the web's load picker can produce).  Stored values stay untouched.
EXCLUDED_FIELDS = frozenset({"6_3"})
EXCLUDED_SECTIONS = frozenset({"production"})
WEB_ONLY_FIELDS = frozenset({"load_characteristic"})
#: S1 and peak point: derived from the key operating points on every save (``models._apply_key_point_derivation``).
DERIVED_FIELDS = frozenset(spec_proposals.DERIVED_V2)
#: Properties an agent does not write: catalogue links set by the web pickers, legacy copies and moved values.
EXCLUDED_PROPERTIES = frozenset({("2_4", "catalog_id"), ("2_4", "supplier_legacy"), ("3_1", "depth_m")})
#: Columns of a mission phase an agent gives (all five are required for a complete phase).
MISSION_COLUMNS = ("phase", "torque_Nm", "speed_rpm", "duration_s", "battery_voltage_V")

MAX_ANSWERS = 150
MAX_ANSWERS_BYTES = 64_000
MAX_TEXT = 4_000
MAX_TITLE = 120
MAX_ORG = 120
MAX_APPLICATION = 500
MAX_PROJECTS_LISTED = 50

NOT_KNOWN_YET = "not_known_yet"

#: What the project status means for the customer.  The internal commercial status is shown as MOTRES's review step.
STAGES = {
    "spec": ("specification", "The specification is being filled in."),
    "design": ("design", "MOTRES engineers are designing the motor."),
    "simulation": ("simulation", "MOTRES engineers are running the full simulation."),
    "quote": ("motres_review", "MOTRES is reviewing the results and will contact you about the next step."),
    "prototype": ("prototype", "Prototypes are being built."),
    "production": ("production", "The motor is in production."),
    "closed": ("closed", "The project is closed."),
}

NEXT_STEPS = [
    "MOTRES engineers review the submitted specification.",
    "They run the full simulation of the motor at your key points (electromagnetic, coupled thermal and mechanics).",
    "They contact you in the project's messages on aerostator.com with the results and questions; you agree the design "
    "together before anything else happens.",
    "You can keep refining the draft with update_spec and submit again: every submission is a new specification version.",
]


class OrderError(ValueError):
    """A caller mistake the agent can fix (unknown project, not the owner, bad argument) - reported as a tool error."""


# ── who is calling ────────────────────────────────────────────────────────────────────────────────────────────────────

def user_for(email: str) -> dict:
    """The account an MCP call acts for, in the shape the web's auth layer resolves (``{uid, email, role}``)."""
    from motor_ai_sim import auth
    user = auth.agent_user_for(email)
    if user is None:
        raise OrderError("this AeroStator account is disabled")
    if not str(user.get("email") or "").strip():
        raise OrderError("development projects need a personal AeroStator account; sign in with it")
    return user


def _email(user: dict) -> str:
    return str(user.get("email") or "").strip().lower()


def _own_project(user: dict, project_id: str) -> dict:
    """The caller's own project (a member of it), else ``OrderError`` - the same answer for "missing" and "not yours"."""
    pid = str(project_id or "").strip().lower()
    project = store.get_project(pid) if store.valid_id(pid) else None
    if project is None or access.membership(_email(user), project) is None:
        raise OrderError(f"project '{project_id}' not found among your projects (get_order_status lists them)")
    return project


def project_link(project_id: str, view: str = "specification") -> str:
    from motor_ai_sim import oauth, return_path
    return oauth.base_url() + (return_path.project_path(project_id, view) or "/")


# ── the form, generated from schema.json ─────────────────────────────────────────────────────────────────────────────

def _lang(raw: Any) -> str:
    text = str(raw or "en").strip()
    if text.lower() in ("zh", "zh-cn", "zh_cn", "cn", "chinese"):
        return "zh-CN"
    if text.lower() in ("", "en", "en-us", "en-gb", "english"):
        return "en"
    raise OrderError("lang must be en or zh-CN")


def _text(d: Any, lang: str) -> str | None:
    if isinstance(d, dict):
        return d.get(lang) or d.get("en")
    return d if isinstance(d, str) and d else None


def _choices(raw: Iterable, lang: str) -> list:
    out = []
    for item in raw or []:
        if isinstance(item, dict):
            row = {"value": item.get("value")}
            label = _text(item.get("label"), lang)
            if label and label != item.get("value"):
                row["label"] = label
            out.append(row)
        else:
            out.append({"value": item})
    return out


def _kind(spec: dict) -> str:
    if spec.get("properties"):
        return "object"
    return {"string": "text", "table": "list", "array": "list"}.get(spec.get("type", "string"), spec.get("type", "string"))


def _condition_text(spec: dict) -> str | None:
    if spec.get("applies_when_cooling_medium"):
        media = " or ".join(spec["applies_when_cooling_medium"])
        return f"only for {media} cooling (3_1.cooling_medium), or while no cooling medium is chosen"
    if spec.get("only_when_cooling_medium"):
        return "only when 3_1.cooling_medium is " + " or ".join(spec["only_when_cooling_medium"])
    if spec.get("required_when_cooling_medium"):
        return f"required when 3_1.cooling_medium is {spec['required_when_cooling_medium']}"
    if isinstance(spec.get("required_if_value"), dict):
        r = spec["required_if_value"]
        return f"required when {r.get('property')} is {r.get('equals')}"
    if isinstance(spec.get("required_unless_value"), dict):
        r = spec["required_unless_value"]
        return f"not required when {r.get('property')} is {r.get('equals')}"
    return None


def _describe(spec: dict, lang: str, *, field_id: str = "", top: bool = False) -> dict:
    """One field / property / column of the schema in machine-readable shape (only what the schema says)."""
    out: dict = {"id": spec["id"]}
    label = _text(spec.get("label"), lang) or _text(spec.get("short_label"), lang)
    if label:
        out["label"] = label
    out["type"] = _kind(spec)
    if spec.get("unit"):
        out["unit"] = spec["unit"]
    out["required"] = bool(spec.get("required"))
    cond = _condition_text(spec)
    if cond:
        out["condition"] = cond
    if top:
        out["accepts_not_known_yet"] = bool(spec.get("allow_not_known_yet"))
    help_text = _text(spec.get("help"), lang)
    if help_text:
        out["help"] = help_text
    for key in ("min", "max", "default", "min_filled_properties"):
        if key in spec:
            out[key] = spec[key]
    if spec.get("allow_range"):
        out["accepts_range"] = True
    if spec.get("multiline"):
        out["multiline"] = True
    if spec.get("ui_hidden"):
        out["advanced"] = True
    if spec.get("read_only"):
        out["read_only"] = True
    if spec.get("continuous_value"):
        out["or_text"] = spec["continuous_value"]
    if spec.get("options"):
        out["choices"] = _choices(spec["options"], lang)
    elif spec.get("suggestions"):
        out["suggestions"] = [c["value"] for c in _choices(spec["suggestions"], lang)]
    if spec.get("required_any"):
        out["at_least_one_of"] = spec["required_any"]
    if spec.get("ordered_pairs"):
        out["ordered_pairs"] = spec["ordered_pairs"]
    if spec.get("properties"):
        out["properties"] = [_describe(p, lang, field_id=field_id) for p in spec["properties"]
                             if (field_id, p["id"]) not in EXCLUDED_PROPERTIES]
        if top and _single_property(spec):
            out["plain_value_goes_to"] = _single_property(spec)
    if isinstance(spec.get("items"), dict):
        item = spec["items"]
        out["item"] = (_describe({"id": "row", **item}, lang, field_id=field_id) if item.get("properties")
                       else {"type": _kind(item)})
    if spec.get("columns"):
        out["row"] = [_describe({**c, "required": True}, lang) for c in spec["columns"]]
    return out


def _schema() -> dict:
    return models.schema(SCHEMA_VERSION)


def writable_fields() -> dict[str, tuple[dict, dict]]:
    """``{field_id: (section, field)}`` an agent may answer."""
    out = {}
    for fid, (section, field) in models._field_map(SCHEMA_VERSION).items():
        if (section["id"] in EXCLUDED_SECTIONS or fid in EXCLUDED_FIELDS or fid in WEB_ONLY_FIELDS
                or fid in DERIVED_FIELDS or field.get("handled_by") or fid == "mission_profile"):
            continue
        out[fid] = (section, field)
    return out


def _required_ids() -> list[str]:
    return [fid for fid, (_, field) in writable_fields().items() if field.get("required")]


GUIDANCE = [
    "Units: send bare numbers in the unit the form states for each field (N·m, rpm, V, A, W, °C, mm, kg, s, m/s, L/min, Pa). "
    "A string such as '400 V' is read only when its unit is exactly the field's unit; convert anything else first "
    "(e.g. 4 kW -> 4000 for a field in W). Decimal point, no thousands separators.",
    "Key operating points (key_points): one row per operating point the motor must reach - each continuous point "
    "(duration_s = 'continuous') and each peak or short point (duration_s = seconds > 0), with a name, torque_Nm and "
    "speed_rpm. The longest-running point becomes the continuous (S1) point, the highest-torque point the peak point. "
    "Send the whole list each time; it replaces the stored one.",
    "Every required field needs a value or the mark not_known_yet (answer 'not_known_yet'). Never invent a value: ask "
    "the user, and mark a field not_known_yet only when the user says it is not known yet.",
    "Object fields (power supply, cooling, envelope, ...): send the properties you know; update_spec merges them into "
    "the stored answer, and a property sent as null is cleared.",
    "Ranges: where a field says accepts_range, a value may be {'minimum': x, 'maximum': y}.",
    "Mission profile (mission_profile): a list of phases, each with phase, torque_Nm, speed_rpm, duration_s and "
    "battery_voltage_V; or 'not_known_yet'.",
    "Torque ripple (1_8) applies to the continuous point. A lower ripple costs torque and efficiency: tell the user and, "
    "if the user is unsure, let MOTRES offer several ripple variants rather than guessing a strict number.",
    "After every create_order / update_spec read needs_input: ask the user for each listed field (label, unit and reason "
    "are written to be read out), then call update_spec again; submit_spec when needs_input is empty.",
]


def spec_form(lang: Any = "en") -> dict:
    """The specification form for an agent: sections, fields, types, units, required flags, choices, not-known-yet rules
    and the key point / mission phase row shapes - all generated from ``schema.json``."""
    lang = _lang(lang)
    sch = _schema()
    writable = writable_fields()
    sections = []
    for section in sorted(sch["sections"], key=lambda s: s.get("presentation_order", 99)):
        if section["id"] in EXCLUDED_SECTIONS:
            continue
        fields = [_describe(f, lang, field_id=f["id"], top=True) for f in section["fields"] if f["id"] in writable
                  or (section["id"] == "mission_profile" and f["id"] == "mission_profile")]
        if fields:
            sections.append({"id": section["id"], "title": _text(section.get("title"), lang), "fields": fields})
    return {
        "schema_version": sch.get("schema_version", SCHEMA_VERSION),
        "language": lang,
        "languages": list(LANGS),
        "sections": sections,
        "required_fields": _required_ids() + ["mission_profile"],
        "not_known_yet": NOT_KNOWN_YET,
        "derived_fields": {fid: "derived from key_points on every save; set key_points instead"
                           for fid in sorted(DERIVED_FIELDS)},
        "answer_format": {
            "answers": "an object {field_id: answer}; flat keys 'field_id.property' (e.g. '2_1.voltage_nominal_V') work too",
            "answer": ("a plain value (filled); 'not_known_yet' (or {'status': 'not_known_yet'}); "
                       "null clears the field"),
            "key_points_row": {"name": "text", "torque_Nm": "number, N·m", "speed_rpm": "number, rpm",
                               "duration_s": "'continuous' or seconds > 0", "voltage_V": "optional number, V"},
            "mission_profile_row": {"phase": "text", "torque_Nm": "number, N·m", "speed_rpm": "number, rpm",
                                    "duration_s": "number, s", "battery_voltage_V": "number, V"},
            "example": {"1_1": "Drone propulsion", "1_2": "Motor, one direction",
                        "key_points": [{"name": "Hover", "torque_Nm": 4.2, "speed_rpm": 5200, "duration_s": "continuous"},
                                       {"name": "Climb", "torque_Nm": 7.5, "speed_rpm": 6100, "duration_s": 30}],
                        "2_1": {"supply_type": "battery", "chemistry": "Li-ion", "series_cells": 12,
                                "voltage_nominal_V": 44.4, "voltage_min_V": 39.6},
                        "3_1": {"cooling_medium": "air", "airflow": {"mode": "propeller wash"}},
                        "4_6": NOT_KNOWN_YET},
        },
        "guidance": GUIDANCE,
        "flow": ["get_spec_form", "create_order", "update_spec (until needs_input is empty)", "submit_spec",
                 "get_order_status"],
    }


# ── answers -> draft ──────────────────────────────────────────────────────────────────────────────────────────────────

_NUMBER = re.compile(r"\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)\s*(.*?)\s*")
_RANGE = re.compile(r"\s*([-+]?\d+(?:\.\d+)?)\s*(?:-|–|\.\.|to)\s*([-+]?\d+(?:\.\d+)?)\s*(.*?)\s*")
_UNIT_ALIASES = {
    "nm": {"nm", "n-m", "newtonmetre", "newtonmeter", "newtonmetres", "newtonmeters"},
    "rpm": {"rpm", "r/min", "1/min", "/min", "min-1", "min^-1"},
    "c": {"c", "degc", "celsius", "degreesc", "deg"},
    "s": {"s", "sec", "secs", "second", "seconds"},
    "v": {"v", "volt", "volts", "vdc"},
    "w": {"w", "watt", "watts"},
    "a": {"a", "amp", "amps", "ampere", "amperes"},
    "arms": {"arms", "a", "amps", "a(rms)"},
    "kgm2": {"kgm2", "kgm^2"},
    "%pk-pk": {"%pk-pk", "%", "%pkpk", "percent"},
    "%": {"%", "percent"},
    "db(a)": {"db(a)", "dba", "db"},
    "units": {"units", "unit", "pcs", "pieces", ""},
    "cells": {"cells", "cell", "s", ""},
    "cycles": {"cycles", "cycle", ""},
    "phases": {"phases", "phase", ""},
}


def _canon_unit(unit: str) -> str:
    u = str(unit or "").strip().lower().replace("°", "").replace("·", "").replace("*", "").replace("⋅", "")
    u = u.replace("²", "2").replace(" ", "")
    return u


def _unit_ok(given: str, field_unit: str) -> bool:
    want, got = _canon_unit(field_unit), _canon_unit(given)
    if not got:
        return True
    return got == want or got in _UNIT_ALIASES.get(want, set())


def _parse_number(text: str, spec: dict, path: str) -> Any:
    unit = str(spec.get("unit") or "")
    m = _NUMBER.fullmatch(text)
    if m and _unit_ok(m.group(2), unit):
        value = float(m.group(1))
        return int(value) if value.is_integer() and re.fullmatch(r"[-+]?\d+", m.group(1)) else value
    r = _RANGE.fullmatch(text)
    if r and _unit_ok(r.group(3), unit):
        if spec.get("allow_range"):
            return {"minimum": float(r.group(1)), "maximum": float(r.group(2))}
        raise OrderError(f"{path}: '{text}' is a range; this field takes one number{' in ' + unit if unit else ''}")
    if m:
        raise OrderError(f"{path}: '{text}' - this field is in {unit or 'a plain number'}; convert it and send the bare "
                         "number (e.g. 4 kW -> 4000 for a field in W)")
    raise OrderError(f"{path}: '{text}' is not a number; send a bare number{' in ' + unit if unit else ''} with a decimal "
                     "point and no thousands separators")


def _coerce(value: Any, spec: dict, path: str) -> Any:
    """Read numbers delivered as text ("400 V", "3000") in the field's own unit; everything else is left to the
    specification's normaliser."""
    if value is None:
        return None
    if spec.get("properties") and isinstance(value, dict):
        props = {p["id"]: p for p in spec["properties"]}
        return {k: (_coerce(v, props[k], f"{path}.{k}") if k in props else v) for k, v in value.items()}
    typ = spec.get("type", "string")
    if typ == "number":
        if isinstance(value, str):
            if spec.get("continuous_value") and value.strip().lower() == spec["continuous_value"]:
                return spec["continuous_value"]
            if not value.strip():
                return None
            return _parse_number(value, spec, path)
        if isinstance(value, dict) and spec.get("allow_range"):
            return {k: (_coerce(v, {**spec, "allow_range": False}, f"{path}.{k}") if k in ("minimum", "maximum") else v)
                    for k, v in value.items()}
        return value
    if typ in ("array", "table") and isinstance(value, list):
        item = spec.get("items")
        if isinstance(item, dict):
            return [_coerce(v, item, f"{path}[{i}]") for i, v in enumerate(value)]
        return value
    if typ == "object" and isinstance(value, dict) and not spec.get("properties"):
        return value
    if isinstance(value, str) and len(value) > MAX_TEXT:
        raise OrderError(f"{path}: at most {MAX_TEXT} characters")
    return value


def _is_not_known(raw: Any) -> bool:
    if isinstance(raw, str):
        return raw.strip().lower().replace(" ", "_") == NOT_KNOWN_YET
    return isinstance(raw, dict) and raw.get("status") == NOT_KNOWN_YET and set(raw) <= {"status", "value"}


def _explicit(raw: Any) -> bool:
    return isinstance(raw, dict) and "status" in raw and set(raw) <= {"status", "value"}


def _group(answers: dict) -> list[tuple[str, Any]]:
    """``{key: raw}`` -> ``[(field_id, raw)]`` in the caller's order; flat ``field.prop[.sub]`` keys become nested objects
    of their field (later keys win)."""
    out: dict[str, Any] = {}
    for key, raw in answers.items():
        key = str(key).strip()
        if "." not in key:
            if isinstance(out.get(key), dict) and isinstance(raw, dict) and not _explicit(raw):
                out[key] = {**out[key], **raw}
            else:
                out[key] = raw
            continue
        fid, rest = key.split(".", 1)
        node = out.get(fid)
        if not isinstance(node, dict) or _explicit(node):
            node = {}
        out[fid] = node
        parts = rest.split(".")
        for part in parts[:-1]:
            nxt = node.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
            node[part] = nxt
            node = nxt
        node[parts[-1]] = raw
    return list(out.items())


def _merge(old: Any, new: dict) -> dict:
    """Properties of ``new`` over the stored object ``old``; a property sent as null is removed."""
    merged = copy.deepcopy(old) if isinstance(old, dict) else {}
    for key, value in new.items():
        if value is None:
            merged.pop(key, None)
        elif isinstance(value, dict) and isinstance(merged.get(key), dict) and set(value) - {"minimum", "maximum"}:
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _change_for(field_id: str, raw: Any, draft: dict) -> dict:
    """The new stored answer of one field from the agent's raw answer (``OrderError`` for a refused one)."""
    writable = writable_fields()
    if field_id not in writable:
        known = models._field_map(SCHEMA_VERSION)
        if field_id in DERIVED_FIELDS:
            raise OrderError(f"{field_id} is derived from the key operating points; set key_points instead")
        if field_id in known:
            raise OrderError(f"{field_id} is not part of the agent order form (get_spec_form lists the fields); "
                             "MOTRES handles it with you after the simulation")
        raise OrderError(f"unknown field {field_id} (get_spec_form lists the field ids)")
    _, field = writable[field_id]
    if _is_not_known(raw):
        if not field.get("allow_not_known_yet"):
            raise OrderError(f"{field_id} cannot be marked not known yet; give a value or leave it out")
        old = (draft.get("fields") or {}).get(field_id)
        return {"value": old.get("value") if isinstance(old, dict) else None, "status": NOT_KNOWN_YET}
    if raw is None:
        return {"value": None, "status": "missing"}
    if _explicit(raw):
        status = raw.get("status")
        if status not in ("filled", "missing"):
            raise OrderError(f"{field_id}: status must be filled, missing or not_known_yet")
        value = raw.get("value")
    else:
        status, value = "filled", raw
    if status == "missing":
        return {"value": None, "status": "missing"}
    single = _single_property(field)
    if single and not isinstance(value, (dict, list)):
        value = {single: value}               # 8000 for the maximum speed -> {"maximum_rpm": 8000}
    _check_properties(field_id, field, value)
    value = _coerce(value, field, field_id)
    old = (draft.get("fields") or {}).get(field_id)
    if (field.get("properties") and isinstance(value, dict) and isinstance(old, dict)
            and old.get("status") == "filled" and isinstance(old.get("value"), dict)):
        value = _merge(old["value"], value)
    elif field.get("properties") and isinstance(value, dict):
        value = {k: v for k, v in value.items() if v is not None}
    return {"value": value, "status": "filled"}


def _single_property(field: dict) -> str | None:
    """The one required property of an object field: a plain value given for the field goes there."""
    if not field.get("properties"):
        return None
    required = [p["id"] for p in field["properties"] if p.get("required")]
    return required[0] if len(required) == 1 else None


def _check_properties(field_id: str, field: dict, value: Any) -> None:
    if not (field.get("properties") and isinstance(value, dict)):
        return
    for key in value:
        if (field_id, key) in EXCLUDED_PROPERTIES:
            raise OrderError(f"{field_id}.{key} is set in the web app, not through the order form")


def _mission_change(raw: Any, draft: dict) -> tuple[list, str]:
    if _is_not_known(raw):
        return list(draft.get("mission_profile") or []), NOT_KNOWN_YET
    if raw is None:
        return [], "missing"
    if not isinstance(raw, list):
        raise OrderError("mission_profile: a list of phases {phase, torque_Nm, speed_rpm, duration_s, battery_voltage_V} "
                         "or 'not_known_yet'")
    spec = next(f for f in models._field_map(SCHEMA_VERSION)["mission_profile"][0]["fields"] if f["id"] == "mission_profile")
    cols = {c["id"]: c for c in spec.get("columns", [])}
    rows = []
    for i, row in enumerate(raw):
        if not isinstance(row, dict):
            raise OrderError(f"mission_profile[{i}]: an object {{phase, torque_Nm, speed_rpm, duration_s, battery_voltage_V}}")
        extra = set(row) - set(MISSION_COLUMNS) - {"row_id"}
        if extra:
            raise OrderError(f"mission_profile[{i}]: unknown columns {', '.join(sorted(extra))}; use "
                             + ", ".join(MISSION_COLUMNS))
        rows.append({k: (_coerce(v, cols[k], f"mission_profile[{i}].{k}") if k in cols else v) for k, v in row.items()})
    return rows, ("filled" if rows else "missing")


def _check_size(answers: Any) -> dict:
    if answers is None:
        return {}
    if not isinstance(answers, dict):
        raise OrderError("answers must be an object {field_id: answer}")
    if len(answers) > MAX_ANSWERS:
        raise OrderError(f"at most {MAX_ANSWERS} answers per call")
    try:
        size = len(json.dumps(answers, ensure_ascii=False, default=str))
    except (TypeError, ValueError) as exc:
        raise OrderError(f"answers are not plain JSON: {exc}")
    if size > MAX_ANSWERS_BYTES:
        raise OrderError(f"answers too large ({size} bytes; at most {MAX_ANSWERS_BYTES})")
    return answers


def _field_of(path: str) -> str:
    return re.split(r"[.\[]", str(path or ""), maxsplit=1)[0]


def apply_answers(draft: dict, answers: dict) -> tuple[dict, list[str], list[dict]]:
    """``(new_draft, accepted_field_ids, rejected)``: each answer is applied on top of the ones accepted before it and
    kept only if the specification's own normaliser (``models.normalize_draft``) accepts the result."""
    work = copy.deepcopy(draft)
    work["schema_version"] = SCHEMA_VERSION
    accepted, rejected = [], []
    for field_id, raw in _group(answers):
        trial = copy.deepcopy(work)
        try:
            if field_id == "mission_profile":
                rows, status = _mission_change(raw, trial)
                trial["mission_profile"], trial["mission_profile_status"] = rows, status
            else:
                trial.setdefault("fields", {})[field_id] = _change_for(field_id, raw, trial)
            normalized = models.normalize_draft(trial, schema_version=SCHEMA_VERSION)
        except (OrderError, ValueError, TypeError) as exc:
            message = str(exc)
            where = message.split(":", 1)[0].strip() if ":" in message else field_id
            rejected.append({"field": where if _field_of(where) == field_id else field_id,
                             "field_id": field_id, "reason": message})
            continue
        normalized["provenance"] = copy.deepcopy(work.get("provenance") or {})
        work = normalized
        accepted.append(field_id)
    return work, accepted, rejected


# ── needs_input, completeness, answers ───────────────────────────────────────────────────────────────────────────────

def _lookup(field_id: str) -> dict | None:
    hit = models._field_map(SCHEMA_VERSION).get(field_id)
    return hit[1] if hit else None


def _sub_spec(field: dict, rest: str) -> dict | None:
    """The property / column spec a path tail names (``voltage_nominal_V``, ``[0].name``, ``airflow.mode``)."""
    parts = [p for p in re.split(r"\.|\[\d+\]\.?", rest) if p]
    spec = field
    for part in parts:
        pool = spec.get("properties") or (spec.get("items") or {}).get("properties") or spec.get("columns") or []
        alias = {"rpm": "speed_rpm", "voltage_V": "battery_voltage_V"} if spec.get("columns") else {}
        nxt = next((p for p in pool if p["id"] in (part, alias.get(part))), None)
        if nxt is None:
            return None
        spec = nxt
    return spec if spec is not field else None


def _need(error: dict, lang: str) -> dict:
    path = str(error.get("field") or "")
    fid = _field_of(path)
    message = str(error.get("message") or "")
    if fid in DERIVED_FIELDS:            # the S1 point is read from the key points while none are stored
        fid, path = "key_points", "key_points"
        message = "Add the key operating points (name, torque, speed, duration) or mark not known yet"
    field = _lookup(fid) or {}
    label = _text(field.get("label"), lang) or fid
    item = {"field": path, "field_id": fid, "label": label, "reason": message,
            "accepts_not_known_yet": bool(field.get("allow_not_known_yet"))}
    sub = _sub_spec(field, path[len(fid):]) if path != fid else None
    if sub:
        sub_label = _text(sub.get("label"), lang)
        if sub_label:
            item["label"] = f"{label} - {sub_label}"
        if sub.get("unit"):
            item["unit"] = sub["unit"]
        if sub.get("options"):
            item["choices"] = [c["value"] for c in _choices(sub["options"], lang)]
    else:
        if field.get("unit") and not field.get("properties"):
            item["unit"] = field["unit"]
        if field.get("options"):
            item["choices"] = [c["value"] for c in _choices(field["options"], lang)]
    return item


def needs_input(draft: dict, lang: str = "en") -> list[dict]:
    """What still blocks the submission: ``models.validation_errors`` (exactly what ``POST /spec/submit`` refuses with),
    one row per field with a label, the unit and the reason, de-duplicated."""
    out, seen = [], set()
    for error in models.validation_errors(draft):
        item = _need(error, lang)
        key = (item["field"], item["reason"])
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def completeness_view(draft: dict) -> dict:
    c = models.completeness(draft)
    return {"complete": bool(c.get("complete")), "required": c.get("total_required", 0),
            "answered": c.get("answered", 0), "not_known_yet": c.get("unknown", 0),
            "missing": c.get("missing", 0), "invalid": c.get("invalid", 0)}


def answers_view(draft: dict) -> dict:
    """The stored answers of the fields an agent sees (``{field_id: {status, value}}``), plus the mission profile and the
    derived S1 / peak point.  Never the commercial field, the production section or provenance."""
    fields = draft.get("fields") or {}
    out = {}
    for fid in writable_fields():
        answer = fields.get(fid)
        if isinstance(answer, dict) and (answer.get("status") != "missing" or answer.get("value") not in (None, "", [], {})):
            out[fid] = {"status": answer.get("status", "missing"), "value": copy.deepcopy(answer.get("value"))}
    out["mission_profile"] = {"status": draft.get("mission_profile_status", "missing"),
                              "value": [{k: row.get(k) for k in MISSION_COLUMNS if k in row}
                                        for row in draft.get("mission_profile") or [] if isinstance(row, dict)]}
    derived = {fid: copy.deepcopy(fields[fid]) for fid in sorted(DERIVED_FIELDS) if isinstance(fields.get(fid), dict)}
    if derived:
        out["derived"] = derived
    return out


def _wrapper(project_id: str) -> dict:
    return spec_proposals.draft_wrapper(project_id)


def _draft_state(project_id: str, lang: str, *, with_answers: bool = False) -> dict:
    wrapper = _wrapper(project_id)
    draft = wrapper.get("draft") or {}
    version = spec_proposals.schema_version_of(wrapper)
    state = {"revision": int(wrapper.get("revision", 0)), "submitted_version": wrapper.get("version")}
    if version < SCHEMA_VERSION:
        state.update(schema_version=version, needs_input=[], completeness=None,
                     note="this project uses the old specification form; open it in the web app once to migrate it")
        return state
    state["completeness"] = completeness_view(draft)
    state["needs_input"] = needs_input(draft, lang)
    if with_answers:
        state["answers"] = answers_view(draft)
    return state


# ── HTTP refusals of the shared service functions -> agent answers ───────────────────────────────────────────────────

def _detail(exc: HTTPException) -> Any:
    return exc.detail


def _agreement(exc: HTTPException) -> dict:
    detail = _detail(exc) if isinstance(_detail(exc), dict) else {}
    from motor_ai_sim import oauth
    return {"status": "agreement_required", "agreement_version": detail.get("version"),
            "accept_url": oauth.base_url() + "/projects",
            "user_message": ("Before a development can start, your company must accept the AeroStator Platform Services "
                             "and Data Agreement. Please open aerostator.com, sign in and accept it yourself (the AI "
                             "agent cannot accept it for you), then ask me to continue.")}


def _refusal(exc: HTTPException, project_id: str = "") -> OrderError:
    detail = _detail(exc)
    text = detail.get("message") if isinstance(detail, dict) else str(detail)
    if exc.status_code == 404:
        return OrderError(f"project '{project_id}' not found among your projects")
    if exc.status_code == 403:
        return OrderError("only the project owner can change or submit this specification; you can read it and "
                          "write in the project's messages on aerostator.com")
    if exc.status_code == 503:
        return OrderError("the project store is read-only at the moment; try again later")
    return OrderError(text or f"refused ({exc.status_code})")


def _audit(principal_client: str) -> dict:
    return {"via": "mcp", **({"agent": str(principal_client)[:80]} if principal_client else {})}


# ── the tool bodies ──────────────────────────────────────────────────────────────────────────────────────────────────

def _clean_text(value: Any, name: str, limit: int, *, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise OrderError(f"{name} must be text")
    value = " ".join(value.split())
    if required and not value:
        raise OrderError(f"{name} is required")
    if len(value) > limit:
        raise OrderError(f"{name}: at most {limit} characters")
    return value


def _save(user: dict, project_id: str, revision: int, answers: dict, lang: str, client: str) -> dict:
    from motor_ai_sim.development import routes
    wrapper = _wrapper(project_id)
    current = int(wrapper.get("revision", 0))
    if spec_proposals.schema_version_of(wrapper) < SCHEMA_VERSION:
        raise OrderError("this project uses the old specification form; open it in the web app once to migrate it")
    if revision != current:
        return {"status": "conflict", "project_id": project_id, "revision": current, "saved": False,
                "message": ("the specification changed since you read it (now revision %d); your answers were not saved. "
                            "Call get_order_status(project_id) to see the current answers, then update_spec again with "
                            "revision %d." % (current, current))}
    draft, accepted, rejected = apply_answers(wrapper.get("draft") or {}, answers)
    if accepted:
        try:
            saved = routes.save_spec_for(user, project_id, {"draft": draft, "revision": revision,
                                                            "schema_version": SCHEMA_VERSION},
                                         audit_details=_audit(client) | {"fields": accepted[:MAX_ANSWERS]})
        except HTTPException as exc:
            if exc.status_code == 409:
                fresh = int(_wrapper(project_id).get("revision", 0))
                return {"status": "conflict", "project_id": project_id, "revision": fresh, "saved": False,
                        "message": f"the specification changed meanwhile (now revision {fresh}); call update_spec again "
                                   f"with revision {fresh}."}
            if exc.status_code == 422:
                return {"status": "invalid", "project_id": project_id, "revision": current, "saved": False,
                        "rejected": [{"field": _field_of(str(_detail(exc))), "reason": str(_detail(exc))}],
                        "accepted": []}
            raise _refusal(exc, project_id)
        revision = int(saved["revision"])
    state = _draft_state(project_id, lang)
    status = "saved" if accepted else ("invalid" if rejected else "unchanged")
    out = {"status": status, "project_id": project_id, "saved": bool(accepted), "revision": state["revision"],
           "accepted": accepted, "rejected": rejected,
           "recalculations": _recalcs(accepted),
           "completeness": state.get("completeness"), "needs_input": state.get("needs_input", []),
           "ready_to_submit": bool(state.get("completeness") and state["completeness"]["complete"]
                                   and not state.get("needs_input")),
           "link": project_link(project_id)}
    out["next"] = ("submit_spec(project_id, revision) - everything required is answered" if out["ready_to_submit"]
                   else "ask the user for each needs_input field, then update_spec again with the new revision")
    return out


def _recalcs(field_ids: list[str]) -> list[dict]:
    """Which recalculations each accepted change triggers once MOTRES simulates (owner rule 2026-10-26)."""
    rows = []
    known = models._field_map(SCHEMA_VERSION)
    for fid in field_ids:
        section = known[fid][0]["id"] if fid in known else ""
        rc = spec_proposals.recalc_for(fid, section)
        if rc.get("needed"):
            rows.append({"field_id": fid, "recalculation": rc["text"]["en"]})
    return rows


def create_order(user: dict, title: Any, customer_org: Any = None, application: Any = None, answers: Any = None,
                 lang: Any = "en", client: str = "") -> dict:
    """Open a development project owned by the caller and, optionally, save a first draft from ``answers``."""
    from motor_ai_sim.development import routes
    lang = _lang(lang)
    title = _clean_text(title, "title", MAX_TITLE, required=True)
    customer_org = _clean_text(customer_org, "customer_org", MAX_ORG)
    application = _clean_text(application, "application", MAX_APPLICATION)
    answers = dict(_check_size(answers))
    if application and "1_1" not in answers:
        answers = {"1_1": application, **answers}
    try:
        project = routes.create_project_for(user, {"title": title, "customer_org": customer_org},
                                            audit_details=_audit(client))
    except HTTPException as exc:
        if exc.status_code == 428:
            return _agreement(exc)
        if exc.status_code == 429:
            detail = _detail(exc) if isinstance(_detail(exc), dict) else {}
            return {"status": "project_limit", "limit": detail.get("limit"),
                    "user_message": str(detail.get("message") or "You have reached the limit of open projects."),
                    "next": "get_order_status lists your open projects; continue one of them with update_spec"}
        raise _refusal(exc)
    project_id = project["id"]
    out = {"status": "created", "project_id": project_id, "title": project["title"],
           "customer_org": project.get("customer_org", ""), "link": project_link(project_id)}
    if answers:
        saved = _save(user, project_id, 0, answers, lang, client)
        out.update({k: saved[k] for k in ("revision", "accepted", "rejected", "recalculations", "completeness",
                                          "needs_input", "ready_to_submit", "next") if k in saved})
        out["draft_status"] = saved["status"]
    else:
        state = _draft_state(project_id, lang)
        out.update(revision=state["revision"], accepted=[], rejected=[], completeness=state["completeness"],
                   needs_input=state["needs_input"], ready_to_submit=False,
                   next="ask the user for each needs_input field, then update_spec(project_id, revision, answers)")
    return out


def update_spec(user: dict, project_id: Any, revision: Any, answers: Any, lang: Any = "en", client: str = "") -> dict:
    lang = _lang(lang)
    project = _own_project(user, str(project_id or ""))
    if not access.can_edit_spec(user, project):
        raise OrderError("only the project owner can change this specification; you can read it with get_order_status")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise OrderError("revision must be the non-negative integer the last answer gave")
    answers = _check_size(answers)
    if not answers:
        raise OrderError("answers is empty; give {field_id: answer} (get_spec_form lists the fields)")
    return _save(user, project["id"], revision, answers, lang, client)


def submit_spec(user: dict, project_id: Any, revision: Any, lang: Any = "en", client: str = "") -> dict:
    from motor_ai_sim.development import routes
    lang = _lang(lang)
    project = _own_project(user, str(project_id or ""))
    pid = project["id"]
    if not access.can_edit_spec(user, project):
        raise OrderError("only the project owner can submit this specification")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise OrderError("revision must be the non-negative integer the last answer gave")
    try:
        submitted = routes.submit_spec_for(user, pid, revision, audit_details=_audit(client))
    except HTTPException as exc:
        if exc.status_code == 428:
            return _agreement(exc) | {"project_id": pid}
        if exc.status_code == 409:
            fresh = int(_wrapper(pid).get("revision", 0))
            return {"status": "conflict", "project_id": pid, "revision": fresh, "submitted": False,
                    "message": f"the specification changed since you read it (now revision {fresh}); check "
                               f"get_order_status(project_id) and submit again with revision {fresh}."}
        if exc.status_code == 422:
            state = _draft_state(pid, lang)
            return {"status": "incomplete", "project_id": pid, "revision": state["revision"], "submitted": False,
                    "completeness": state.get("completeness"), "needs_input": state.get("needs_input", []),
                    "next": "ask the user for each needs_input field, update_spec, then submit_spec again"}
        raise _refusal(exc, pid)
    version = submitted["version"]
    return {"status": "submitted", "project_id": pid, "submitted": True, "spec_version": version["version"],
            "submitted_at": version.get("created_at"), "revision": submitted["current"]["revision"],
            "completeness": completeness_view(version["draft"]), "next_steps": NEXT_STEPS,
            "link": project_link(pid),
            "user_message": ("Your specification was submitted to MOTRES (version %d). MOTRES engineers will review it, "
                             "run the full simulation and contact you in the project's messages on aerostator.com."
                             % version["version"])}


def _stage(status: Any) -> tuple[str, str]:
    return STAGES.get(str(status or ""), ("in_progress", "MOTRES is working on the project."))


def _motor_names(project: dict) -> list[dict]:
    from motor_ai_sim.workspace import split_published_label
    out = []
    for link in project.get("linked_motors") or []:
        if not isinstance(link, dict) or not link.get("die") or not link.get("config"):
            continue
        out.append({"die": split_published_label(str(link["die"]))[0], "config": str(link["config"]),
                    "status": str(link.get("status") or "current")})
    return out


def _last_submission(project_id: str) -> dict | None:
    index = store.load_json(project_id, "spec/index.json", {"versions": []})
    rows = [v for v in index.get("versions", []) if isinstance(v, dict)]
    if not rows:
        return None
    last = max(rows, key=lambda v: int(v.get("version", 0)))
    return {"version": last.get("version"), "submitted_at": last.get("created_at"),
            "source_revision": last.get("source_revision")}


def _row(user: dict, project: dict, lang: str, *, detail: bool = False) -> dict:
    pid = project["id"]
    stage, stage_text = _stage(project.get("status"))
    state = _draft_state(pid, lang, with_answers=detail)
    last = _last_submission(pid)
    try:
        unread_count = len(unread.unread_ids(pid, _email(user)))
    except Exception:                                    # noqa: BLE001 - a badge never breaks the status
        unread_count = 0
    row = {"project_id": pid, "title": project.get("title", ""), "customer_org": project.get("customer_org", ""),
           "stage": stage, "stage_text": stage_text, "created_at": project.get("created_at"),
           "updated_at": project.get("updated_at"),
           "role": access.membership(_email(user), project), "can_edit_spec": access.can_edit_spec(user, project),
           "spec": {"draft_revision": state["revision"], "submitted_version": last["version"] if last else None,
                    "submitted_at": last["submitted_at"] if last else None,
                    "changed_since_submission": bool(last) and last.get("source_revision") is not None
                    and state["revision"] > int(last["source_revision"]) + 1},
           "completeness": state.get("completeness"),
           "needs_input_count": len(state.get("needs_input", [])),
           "linked_motors": _motor_names(project), "unread_messages": unread_count,
           "link": project_link(pid)}
    if detail:
        row["needs_input"] = state.get("needs_input", [])
        row["answers"] = state.get("answers", {})
        if state.get("note"):
            row["note"] = state["note"]
    return row


def order_status(user: dict, project_id: Any = None, lang: Any = "en") -> dict:
    """The caller's own projects (``project_id`` given: that one, with its answers and needs_input)."""
    lang = _lang(lang)
    if project_id:
        project = _own_project(user, str(project_id))
        store.append_audit(project["id"], _email(user), "read", "spec_draft", "draft", {"via": "mcp"})
        return {"project": _row(user, project, lang, detail=True)}
    rows = []
    for pid in store.membership_projects(_email(user)):
        project = store.get_project(pid)
        if project and access.membership(_email(user), project) is not None:
            rows.append(_row(user, project, lang))
    rows.sort(key=lambda r: str(r.get("updated_at") or ""), reverse=True)
    return {"projects": rows[:MAX_PROJECTS_LISTED], "count": len(rows),
            "note": ("your own development projects; stage_text says where each stands. "
                     "Open one with get_order_status(project_id) for its answers and needs_input.")}


def all_text(obj: Any) -> Iterable[str]:
    """Every string in a nested answer (tests: the no-money wording check)."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k)
            yield from all_text(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from all_text(v)


__all__ = ["OrderError", "spec_form", "create_order", "update_spec", "submit_spec", "order_status", "user_for",
           "needs_input", "apply_answers", "writable_fields", "NEXT_STEPS"]
